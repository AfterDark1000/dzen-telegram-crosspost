#!/usr/bin/env python3
"""Dzen -> VK: complete article text only. Cover is added manually in VK."""
import asyncio
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from playwright.async_api import async_playwright, Error as PlaywrightError
from crosspost import fetch_articles

STATE = Path("seen_vk_articles.json")
GROUP_ID = 242308928
VK_API_VERSION = "5.199"


def make_message(title, body):
    title = (title or "").strip()
    body = (body or "").strip()
    if not title or not body:
        raise RuntimeError("Refusing to post an empty title or article")
    # Preserve sections and paragraph breaks while removing excess blank lines.
    body = re.sub(r"\r\n?", "\n", body)
    body = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", body)
    message = title + "\n\n" + body
    if len(message) > 15000:
        raise RuntimeError("Full article exceeds safe VK post length; not truncating")
    return message


async def read_full_article(page, item):
    """Read the actual Dzen article body, never fall back to its feed preview."""
    last_error = None
    for attempt in range(1, 4):
        try:
            await page.goto(item["url"], wait_until="commit", timeout=30000)
            for _ in range(25):
                await page.wait_for_timeout(1000)
                try:
                    content = await page.evaluate("""() => {
                        const root = document.querySelector('[itemprop="articleBody"]');
                        if (!root) return null;
                        return {
                            text: (root.innerText || '').trim(),
                            paragraphCount: root.querySelectorAll('p').length
                        };
                    }""")
                except PlaywrightError:
                    continue
                if not content:
                    continue
                body = content["text"]
                if content["paragraphCount"] < 2 or len(body) < 350:
                    continue
                teaser = re.sub(r"\s+", " ", item.get("text") or "").strip()
                normalized = re.sub(r"\s+", " ", body).strip()
                # The public feed contains a preview, usually ~500 characters.
                # Require evidence the extracted body is substantially longer.
                if teaser and len(normalized) <= len(teaser) + 60:
                    raise RuntimeError("Dzen body may be incomplete: no longer than feed preview")
                return make_message(item["title"], body)
            raise RuntimeError("Full article body not found on page")
        except (PlaywrightError, RuntimeError) as exc:
            last_error = exc
            print(f"VK article read attempt {attempt}/3 failed: {type(exc).__name__}: {str(exc)[:140]}", flush=True)
            if attempt < 3:
                await asyncio.sleep(attempt * 2)
    raise RuntimeError("Skipping VK post: full Dzen article could not be verified") from last_error


def publish_to_vk(item, message, token):
    fields = {
        "owner_id": str(-GROUP_ID),
        "from_group": "1",
        "message": message,
        # A stable ID also protects against retries when the API accepted a
        # request but the GitHub runner lost the response before saving state.
        "guid": str(uuid.uuid5(uuid.NAMESPACE_URL, item["url"])),
        "access_token": token,
        "v": VK_API_VERSION,
    }
    request = urllib.request.Request(
        "https://api.vk.com/method/wall.post",
        data=urllib.parse.urlencode(fields).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(request, timeout=40) as reply:
            payload = json.load(reply)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"VK wall.post HTTP {exc.code}") from exc
    if "error" in payload:
        err = payload["error"]
        raise RuntimeError(f"VK wall.post error {err.get('error_code')}: {err.get('error_msg')}")
    post_id = payload.get("response", {}).get("post_id")
    if not post_id:
        raise RuntimeError("VK did not return post_id; publication unconfirmed")
    print(f"VK full article posted: {item['title']} (wall-{GROUP_ID}_{post_id})", flush=True)


def save_seen(ids):
    STATE.write_text(json.dumps(sorted(ids), ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")


async def main():
    if not STATE.exists():
        raise RuntimeError("VK state file is missing; not backfilling articles")
    token = os.environ.get("VK_ACCESS_TOKEN", "").strip()
    if not token:
        print("VK paused: VK_ACCESS_TOKEN not configured. Telegram/MAX unaffected.", flush=True)
        return
    seen = set(json.loads(STATE.read_text(encoding="utf-8")))
    articles = await fetch_articles()
    pending = sorted((item for item in articles if item["id"] not in seen),
                     key=lambda item: (item["date"], item["id"]))
    print("VK new articles:", len(pending), flush=True)
    if not pending:
        return
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = await browser.new_page(locale="ru-RU", timezone_id="Europe/Moscow")
            for item in pending:
                message = await read_full_article(page, item)
                publish_to_vk(item, message, token)
                seen.add(item["id"])
                save_seen(seen)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
