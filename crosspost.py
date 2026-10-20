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

from playwright.async_api import async_playwright, Error as PlaywrightError
from telegram_photo import send_photo_upload

CHANNEL = "https://dzen.ru/iipike?tab=articles"
TELEGRAM_CHAT = "@iipike"
STATE = Path("seen_articles.json")

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
                for item in payload["items"]:
                    if not isinstance(item, dict) or item.get("type") != "article":
                        continue
                    url = item.get("shareLink") or item.get("bulkData", {}).get("url")
                    if not url or not url.startswith("https://dzen.ru/a/"):
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
                if articles:
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

def send_article(item):
    title = html.escape(item["title"])
    desc = html.escape(short_teaser(item["text"]))
    link = html.escape(item["url"], quote=True)
    caption = f"<b>{title}</b>\n\n{desc}\n\n<a href=\"{link}\">Читать в Дзене →</a>" if desc else f"<b>{title}</b>\n\n<a href=\"{link}\">Читать в Дзене →</a>"
    cover = item.get("image") or ""
    if cover and len(caption) <= 1024:
        # Fast path: Telegram downloads the source image itself.
        try:
            api_call("sendPhoto", {"chat_id": TELEGRAM_CHAT, "photo": cover,
                                   "caption": caption, "parse_mode": "HTML"})
            print("Telegram cover sent via URL.", flush=True)
            return
        except urllib.error.HTTPError as error:
            try:
                message = json.loads(error.read().decode("utf-8")).get("description", "")
            except (ValueError, UnicodeError):
                message = ""
            print(f"Telegram URL photo rejected: HTTP {error.code}; {message[:250]}", flush=True)
        except Exception as error:
            print("Telegram URL photo failed:", type(error).__name__, flush=True)

        # Reliable fallback: runner downloads the cover and uploads a JPEG directly.
        try:
            send_photo_upload(os.environ["TELEGRAM_BOT_TOKEN"], TELEGRAM_CHAT, caption, cover)
            print("Telegram cover sent by direct upload.", flush=True)
            return
        except Exception as error:
            print("Telegram direct photo upload failed:", type(error).__name__, flush=True)
            # Keep the news rather than silently dropping the article.
    else:
        print("Telegram photo unavailable:",
              "missing cover URL" if not cover else "caption exceeds 1024 characters",
              flush=True)

    print("Telegram text-only fallback:", item["title"], flush=True)
    api_call("sendMessage", {"chat_id": TELEGRAM_CHAT, "text": caption, "parse_mode": "HTML",
                             "disable_web_page_preview": "false"})

async def main():
    articles = await fetch_articles()
    # Local snapshot for the archive builder later in the SAME workflow.
    # Never commit this temporary file, and never change publication decisions.
    Path("_current_dzen_snapshot.json").write_text(
        json.dumps(articles, ensure_ascii=False), encoding="utf-8")
    print(f"Found {len(articles)} public Dzen articles")
    ids = {item["id"] for item in articles}
    if not STATE.exists():
        STATE.write_text(json.dumps(sorted(ids), ensure_ascii=False, indent=2) + "\n")
        print("FIRST RUN: recorded existing articles, nothing posted.")
        return
    seen = set(json.loads(STATE.read_text()))
    new = sorted((item for item in articles if item["id"] not in seen),
                 key=lambda item: (item["date"], item["id"]))
    print(f"New articles: {len(new)}")
    for item in new:
        send_article(item)
        print("Posted:", item["title"])
        seen.add(item["id"])
        STATE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2) + "\n")
    print("Done")

if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
