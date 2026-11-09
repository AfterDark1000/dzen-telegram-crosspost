#!/usr/bin/env python3
"""Dzen -> Telegram. No login; state is persisted in seen_articles.json."""
import asyncio
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path
from state_checkpoint import atomic_json

from playwright.async_api import async_playwright, Error as PlaywrightError
from telegram_photo import (
    send_photo_upload, PhotoPreparationError, TelegramUploadRejected,
    TelegramUploadUncertain,
)
from site_utm import load_site_catalog, tagged_site_url

CHANNEL = "https://dzen.ru/iipike?tab=articles"
TELEGRAM_CHAT = "@iipike"
STATE = Path("seen_articles.json")
UNCERTAIN = Path("telegram_delivery_uncertain.json")


class TelegramDeliveryUncertain(RuntimeError):
    """Telegram could have delivered a message despite an API/transport failure."""


# Editorial articles that must remain on Dzen only. Every downstream
# publisher (Telegram, VK, Instagram, Threads) uses fetch_articles().
DZEN_ONLY_PATHS = frozenset({
    "/a/auL2PIuIqglEYkgF",  # «Дневник канала»
})


def is_dzen_only_url(url):
    if not isinstance(url, str):
        return False
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname == "dzen.ru"
            and parsed.username is None
            and parsed.path.rstrip("/") in DZEN_ONLY_PATHS)

def extract_items(source):
    # Dzen embeds JSON in page HTML. Prefer the structured feedData payload.
    decoder = json.JSONDecoder()
    for candidate in (source, html.unescape(source), source.replace('&quot;', '"')):
        for match in re.finditer(r'["\\]feedData["\\]\s*:\s*', candidate):
            try:
                payload, _ = decoder.raw_decode(candidate[match.end():])
            except (ValueError, TypeError):
                continue
            if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                articles = []
                excluded = 0
                for item in payload["items"]:
                    if not isinstance(item, dict) or item.get("type") != "article":
                        continue
                    url = item.get("shareLink") or item.get("bulkData", {}).get("url")
                    if not url or not url.startswith("https://dzen.ru/a/"):
                        continue
                    if is_dzen_only_url(url):
                        excluded += 1
                        print("Dzen-only editorial article excluded from crossposting:", url, flush=True)
                        continue
                    ident = str(item.get("id") or url)
                    img = item.get("image") or {}
                    image_url = ""
                    if img.get("urlTemplate") and img.get("namespace"):
                        image_url = img["urlTemplate"].replace("{namespace}", img["namespace"]).replace("{size}", img.get("sizeName", "scale_1200"))
                    articles.append({
                        "id": ident, "title": item.get("title", "Новая статья"),
                        "text": item.get("text", ""), "url": url,
                        "image": image_url, "date": int(item.get("publicationDate") or 0)
                    })
                # A valid feed containing only excluded articles is not a parse error.
                if articles or excluded:
                    return articles
    raise RuntimeError("Structured article feed not found; nothing will be posted.")

async def fetch_articles():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            last_error = None
            for attempt in range(1, 4):
                page = await browser.new_page(locale="ru-RU", timezone_id="Europe/Moscow")
                try:
                    print(f"Dzen fetch attempt {attempt}/3", flush=True)
                    await page.goto(CHANNEL, wait_until="commit", timeout=25000)
                    for _ in range(25):
                        await page.wait_for_timeout(1000)
                        try:
                            return extract_items(await page.content())
                        except (RuntimeError, PlaywrightError):
                            pass
                    raise RuntimeError("Dzen page opened but article feed not found")
                except Exception as error:
                    last_error = error
                    print(f"Attempt {attempt} failed: {type(error).__name__}: {str(error)[:180]}", flush=True)
                    if attempt < 3:
                        await asyncio.sleep(2 * attempt)
                finally:
                    await page.close()
            raise RuntimeError("Unable to obtain Dzen articles after 3 attempts") from last_error
        finally:
            await browser.close()

def api_call(method, params):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    with urllib.request.urlopen(req, timeout=30) as response:
        answer = json.load(response)
    if not answer.get("ok"):
        raise RuntimeError(f"Telegram {method} failed")
    return answer

def short_teaser(raw, limit=230):
    """Use one or two complete opening sentences, never a mid-word cutoff."""
    text = re.sub(r"\s+", " ", raw or "").strip()
    if not text:
        return ""
    # Sentence boundaries, excluding periods within numbers and abbreviations when possible.
    parts = re.split(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z«"(0-9])', text)
    result = []
    for part in parts:
        candidate = " ".join(result + [part])
        if len(candidate) > limit:
            break
        result.append(part)
        if len(result) >= 2:
            break
    if result:
        return " ".join(result)
    # For a very long first sentence, use a clean word boundary with an ellipsis.
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"

def send_article(item, *, site_url=None):
    title = html.escape(item["title"])
    desc = html.escape(short_teaser(item["text"]))
    link = html.escape(item["url"], quote=True)
    caption = f"<b>{title}</b>\n\n{desc}\n\n<a href=\"{link}\">Читать в Дзене →</a>" if desc else f"<b>{title}</b>\n\n<a href=\"{link}\">Читать в Дзене →</a>"
    # Repostbot mirrors this exact message to MAX. The combined source label
    # prevents falsely attributing MAX readers to Telegram.
    if site_url:
        extra = '<a href="' + html.escape(site_url, quote=True) + '">Читать на сайте ХПИИ →</a>\n'
        if len(caption) + len(extra) <= 1024:
            caption = caption.replace('<a href="' + link + '">Читать в Дзене →</a>',
                                      extra + '<a href="' + link + '">Читать в Дзене →</a>')
        else:
            print("SITE_UTM_SKIPPED: Telegram photo caption length safeguard", flush=True)
    cover = item.get("image") or ""
    if cover and len(caption) <= 1024:
        # An explicit 400/413/422 rejection is safe to retry with a different
        # medium. A timeout, HTTP 5xx or transport error is NOT proof that
        # Telegram did not publish the first request.
        try:
            api_call("sendPhoto", {"chat_id": TELEGRAM_CHAT, "photo": cover,
                                   "caption": caption, "parse_mode": "HTML"})
            print("Telegram cover sent via URL.", flush=True)
            return
        except urllib.error.HTTPError as error:
            if error.code not in (400, 413, 422):
                raise TelegramDeliveryUncertain(
                    "Telegram URL photo outcome uncertain: HTTP " + str(error.code)
                ) from error
            print("Telegram URL photo explicitly rejected: HTTP " +
                  str(error.code), flush=True)
        except Exception as error:
            raise TelegramDeliveryUncertain(
                "Telegram URL photo outcome uncertain: " + type(error).__name__
            ) from error

        # This upload fallback is permitted only if the first request was
        # explicitly rejected. Local image preparation errors are safe.
        try:
            send_photo_upload(os.environ["TELEGRAM_BOT_TOKEN"],
                              TELEGRAM_CHAT, caption, cover)
            print("Telegram cover sent by direct upload.", flush=True)
            return
        except (PhotoPreparationError, TelegramUploadRejected) as error:
            print("Telegram photo not sent; text fallback allowed: " +
                  type(error).__name__, flush=True)
        except TelegramUploadUncertain as error:
            raise TelegramDeliveryUncertain(
                "Telegram direct upload outcome uncertain"
            ) from error
        except Exception as error:
            # Unexpected errors inside the photo sender may occur after its
            # network call; never guess that Telegram rejected the request.
            raise TelegramDeliveryUncertain(
                "Telegram direct upload not confirmed: " + type(error).__name__
            ) from error
    else:
        print("Telegram photo unavailable:",
              "missing cover URL" if not cover else "caption exceeds 1024 characters",
              flush=True)

    print("Telegram text-only fallback:", item["title"], flush=True)
    try:
        api_call("sendMessage", {"chat_id": TELEGRAM_CHAT, "text": caption,
                                "parse_mode": "HTML",
                                "disable_web_page_preview": "false"})
    except Exception as error:
        raise TelegramDeliveryUncertain(
            "Telegram text-only send not confirmed: " + type(error).__name__
        ) from error

async def main():
    articles = await fetch_articles()
    # Local snapshot for the archive builder later in the SAME workflow.
    # Never commit this temporary file, and never change publication decisions.
    Path("_current_dzen_snapshot.json").write_text(
        json.dumps(articles, ensure_ascii=False), encoding="utf-8")
    print(f"Found {len(articles)} public Dzen articles")
    ids = {item["id"] for item in articles}
    if not STATE.exists():
        atomic_json(STATE, sorted(ids))
        print("FIRST RUN: recorded existing articles, nothing posted.")
        return
    seen = set(json.loads(STATE.read_text()))
    if not UNCERTAIN.exists():
        raise RuntimeError("Missing Telegram uncertain-delivery registry; sending refused")
    uncertain = set(json.loads(UNCERTAIN.read_text(encoding="utf-8")))
    if not all(isinstance(value, str) and value for value in uncertain):
        raise RuntimeError("Invalid Telegram uncertain-delivery registry; sending refused")
    new = sorted((item for item in articles if item["id"] not in seen),
                 key=lambda item: (item["date"], item["id"]))
    print(f"New articles: {len(new)}")
    catalog = load_site_catalog() if new else []
    for item in new:
        if item["id"] in uncertain:
            print("TELEGRAM_UNCONFIRMED_HELD:", item["id"],
                  "Manual channel verification required; not retrying.", flush=True)
            continue
        try:
            send_article(item, site_url=tagged_site_url(item, catalog, "telegram_max"))
        except TelegramDeliveryUncertain as error:
            uncertain.add(item["id"])
            atomic_json(UNCERTAIN, sorted(uncertain))
            print("TELEGRAM_UNCONFIRMED_HELD:", item["id"], str(error),
                  "No automatic retry; inspect channel before manual reconciliation.",
                  flush=True)
            # The workflow still checkpoints the uncertainty state in its
            # early/final steps and fails visibly rather than claiming delivery.
            raise
        print("Posted:", item["title"])
        seen.add(item["id"])
        atomic_json(STATE, sorted(seen))
    print("Done")

if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
