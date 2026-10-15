#!/usr/bin/env python3
"""Full Dzen article and cover -> VK. Skip publishing rather than post incomplete material."""
import asyncio
import json
import os
import re
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from playwright.async_api import async_playwright, Error as PlaywrightError
from crosspost import fetch_articles

STATE = Path("seen_vk_articles.json")
GROUP = 242308928

def api(method, values, token):
    data = urllib.parse.urlencode({**values, "access_token": token, "v": "5.199"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://api.vk.com/method/" + method, data=data), timeout=40) as r:
        output = json.load(r)
    if "error" in output:
        err = output["error"]
        raise RuntimeError(f"VK {method}: {err.get('error_code')} {err.get('error_msg')}")
    if "response" not in output:
        raise RuntimeError("VK method has no response: " + method)
    return output["response"]

async def get_full_text(page, item):
    for attempt in range(3):
        try:
            await page.goto(item["url"], wait_until="commit", timeout=30000)
            for _ in range(25):
                await page.wait_for_timeout(1000)
                try:
                    content = await page.evaluate("""() => {
                        let body = document.querySelector('[itemprop="articleBody"]');
                        return body ? {text: (body.innerText || '').trim(), paragraphs: body.querySelectorAll('p').length} : null;
                    }""")
                except PlaywrightError:
                    continue
                if content and content["paragraphs"] >= 2 and len(content["text"]) >= 500:
                    text = content["text"]
                    teaser = re.sub(r"\s+", " ", item.get("text", "")).strip()
                    if teaser and len(re.sub(r"\s+", " ", text)) <= len(teaser) + 80:
                        raise RuntimeError("Page text looks like an excerpt")
                    if len(text) > 14000:
                        raise RuntimeError("Article too long for safe VK post")
                    return text
        except Exception:
            if attempt == 2:
                raise
        await asyncio.sleep(2)
    raise RuntimeError("Could not obtain complete article body")

def get_photo(url, token):
    if not url.startswith("https://"):
        raise RuntimeError("Missing HTTPS cover")
    server = api("photos.getWallUploadServer", {"group_id": GROUP}, token)
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"}), timeout=30) as response:
        content = response.read(12*1024*1024 + 1)
    if not 1000 < len(content) < 12*1024*1024:
        raise RuntimeError("Cover file size invalid")
    if content.startswith(b"\xff\xd8\xff"):
        ext, mime = "jpg", "image/jpeg"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        ext, mime = "png", "image/png"
    else:
        raise RuntimeError("Unsupported cover file type")
    boundary = "----VK" + uuid.uuid4().hex
    data = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="cover.{ext}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
        + content + f'\r\n--{boundary}--\r\n'.encode()
    )
    request = urllib.request.Request(server["upload_url"], data=data, headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(request, timeout=60) as response:
        uploaded = json.load(response)
    photos = api("photos.saveWallPhoto", {
        "group_id": GROUP, "photo": uploaded["photo"], "server": uploaded["server"], "hash": uploaded["hash"]
    }, token)
    if not photos or "id" not in photos[0] or "owner_id" not in photos[0]:
        raise RuntimeError("VK failed to save cover")
    return f'photo{photos[0]["owner_id"]}_{photos[0]["id"]}'

def send(item, body, photo, token):
    result = api("wall.post", {
        "owner_id": -GROUP, "from_group": 1,
        "message": item["title"].strip() + "\n\n" + body,
        "attachments": photo,
        "guid": str(uuid.uuid5(uuid.NAMESPACE_URL, item["url"]))
    }, token)
    if not result.get("post_id"):
        raise RuntimeError("VK post was not confirmed")
    print("VK complete article posted:", item["title"], "post:", result["post_id"], flush=True)

async def main():
    if not STATE.exists():
        raise RuntimeError("VK state not initialized")
    group_token = os.environ["VK_ACCESS_TOKEN"]
    user_token = os.getenv("VK_USER_ACCESS_TOKEN")
    if not user_token:
        print("VK awaiting VK_USER_ACCESS_TOKEN via official VK ID. No partial posts will be sent.", flush=True)
        return
    seen = set(json.loads(STATE.read_text(encoding="utf-8")))
    items = await fetch_articles()
    new = sorted((x for x in items if x["id"] not in seen), key=lambda x: (x["date"],x["id"]))
    print("VK new articles:", len(new), flush=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = await browser.new_page(locale="ru-RU")
            for item in new:
                body = await get_full_text(page, item)
                photo = get_photo(item["image"], user_token)
                send(item, body, photo, group_token)
                seen.add(item["id"])
                STATE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finally:
            await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
