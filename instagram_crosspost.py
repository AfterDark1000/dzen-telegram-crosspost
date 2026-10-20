#!/usr/bin/env python3
"""Dzen -> Instagram: queue public articles, publish only when matching 4:5 art exists.

Images arrive via instagram_cover_inbox.py and are committed to a public repository.
Never use Dzen landscape covers. Telegram, VK and Threads are unaffected.
"""
import asyncio
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from crosspost import fetch_articles, short_teaser
from instagram_cover_inbox import normalize_title

TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
STATE = Path("seen_instagram_articles.json")
PENDING = Path("instagram_pending_articles.json")
INBOX = Path("instagram_cover_inbox.json")
API = "https://graph.instagram.com/v24.0"
EXPECTED_USERNAME = "iipike.ai"
COVER_ROOT = "https://raw.githubusercontent.com/AfterDark1000/dzen-telegram-crosspost/main/"
MAX_PER_RUN = 10


def graph(method, path, values=None):
    values = values or {}
    endpoint = API + "/" + str(path).lstrip("/")
    if method == "GET":
        if values:
            endpoint += "?" + urllib.parse.urlencode(values)
        request = urllib.request.Request(endpoint, headers={
            "Authorization": "Bearer " + TOKEN, "Accept": "application/json"})
    else:
        request = urllib.request.Request(
            endpoint, data=urllib.parse.urlencode(values).encode("utf-8"),
            headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=50) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read().decode("utf-8")).get("error", {})
        except (ValueError, UnicodeError):
            error = {}
        raise RuntimeError("Instagram HTTP " + str(exc.code) + ", code " +
                           str(error.get("code", "unknown"))) from None
    except urllib.error.URLError:
        raise RuntimeError("Instagram API network error") from None
    if "error" in result:
        raise RuntimeError("Instagram API error " + str(result["error"].get("code", "unknown")))
    return result


def make_caption(item):
    title = (item.get("title") or "").strip()
    if not title:
        raise RuntimeError("Dzen article has no title")
    teaser = short_teaser(item.get("text") or "", limit=550)
    caption = title + ("\n\n" + teaser if teaser else "") + "\n\n#ИИ #ИскусственныйИнтеллект"
    if len(caption) > 2200:
        raise RuntimeError("Instagram caption longer than 2200 characters")
    return caption


def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cover_url(title, covers):
    normalized = normalize_title(title)
    matched = covers.get(normalized)
    if not isinstance(matched, dict):
        return None
    filename = matched.get("file", "")
    if not re.fullmatch(r"instagram_covers/[0-9a-f]{20}-[0-9]+\.jpg", filename):
        raise RuntimeError("Invalid registered Instagram cover filename")
    if normalize_title(matched.get("title")) != normalized:
        raise RuntimeError("Instagram cover title does not match registry key")
    return COVER_ROOT + filename


def is_cover_public(url):
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "HPI-Instagram-Cover/1.0"})
        with urllib.request.urlopen(request, timeout=12) as response:
            if response.status != 200 or "image" not in response.headers.get("Content-Type", ""):
                return False
            return response.read(3) == b"\xff\xd8\xff"
    except (urllib.error.URLError, urllib.error.HTTPError):
        return False


def recently_published_captions(user_id):
    """Recover from success followed by a failed GitHub state commit."""
    try:
        response = graph("GET", str(user_id) + "/media", {
            "fields": "id,caption", "limit": "100"})
        return {row["caption"].strip() for row in response.get("data", []) if row.get("caption")}
    except RuntimeError:
        print("Instagram recent-post lookup failed; proceeding with local state.", flush=True)
        return set()


def publish_article(user_id, item, caption, vertical_cover):
    if not vertical_cover or not vertical_cover.startswith(COVER_ROOT):
        raise RuntimeError("No matching trusted vertical image: will not publish")
    container = graph("POST", f"{user_id}/media", {
        "image_url": vertical_cover, "caption": caption})
    cid = container.get("id")
    if not cid:
        raise RuntimeError("Instagram did not create media container")
    status = ""
    for _ in range(20):
        time.sleep(4)
        data = graph("GET", cid, {"fields": "status_code"})
        status = data.get("status_code", "")
        if status in {"FINISHED", "ERROR", "EXPIRED"}:
            break
    if status != "FINISHED":
        raise RuntimeError("Instagram photo not ready: " + str(status))
    result = graph("POST", f"{user_id}/media_publish", {"creation_id": cid})
    post_id = result.get("id")
    if not post_id:
        raise RuntimeError("Instagram did not confirm publication")
    print("Instagram published vertical cover:", item["title"], "ID:", post_id, flush=True)


async def main():
    if not STATE.exists() or not PENDING.exists() or not INBOX.exists():
        raise RuntimeError("Instagram state files missing; publishing paused")
    seen = set(json.loads(STATE.read_text(encoding="utf-8")))
    rows = json.loads(PENDING.read_text(encoding="utf-8"))
    inbox = json.loads(INBOX.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not isinstance(inbox.get("covers"), dict):
        raise RuntimeError("Instagram queue or cover registry invalid")
    covers = inbox["covers"]
    pending = {str(row["id"]): row for row in rows if str(row.get("id", "")) not in seen}
    articles = await fetch_articles()
    for item in articles:
        if item["id"] not in seen:
            pending[item["id"]] = {
                "id": str(item["id"]), "title": str(item.get("title") or ""),
                "text": short_teaser(item.get("text") or "", limit=550),
                "url": str(item.get("url") or ""), "date": int(item.get("date") or 0),
            }
    ordered = sorted(pending.values(), key=lambda row: (int(row.get("date") or 0), row["id"]))
    print("Instagram: " + str(len(ordered)) + " published Dzen news awaiting covers; " +
          str(len(covers)) + " matching-cover entries.", flush=True)
    if not TOKEN or not ordered:
        save_json(PENDING, ordered)
        if not TOKEN:
            print("Instagram access token not configured; no posts.", flush=True)
        return

    # Avoid ambiguous attribution if a headline is shared by multiple distinct news items.
    title_count = {}
    for item in ordered:
        key = normalize_title(item.get("title", ""))
        title_count[key] = title_count.get(key, 0) + 1

    ready = []
    for item in ordered:
        key = normalize_title(item.get("title", ""))
        if title_count[key] != 1:
            print("Duplicate pending headline; matching paused:", item["title"][:110], flush=True)
            continue
        image = cover_url(item["title"], covers)
        if image:
            ready.append((item, image))
    if not ready:
        print("No matching vertical images; nothing sent to Instagram.", flush=True)
        save_json(PENDING, ordered)
        return

    profile = graph("GET", "me", {"fields": "id,user_id,username,account_type"})
    if profile.get("username") != EXPECTED_USERNAME:
        raise RuntimeError("Instagram token is for a different account")
    user_id = profile.get("user_id") or profile.get("id")
    if not user_id:
        raise RuntimeError("Cannot obtain Instagram publishing ID")
    recent_captions = recently_published_captions(user_id)
    done = 0
    for item, vertical_cover in ready:
        if done >= MAX_PER_RUN:
            break
        if not is_cover_public(vertical_cover):
            print("Cover not yet available on public GitHub CDN:", item["title"][:110], flush=True)
            continue
        caption = make_caption(item)
        if caption in recent_captions:
            print("Already on Instagram; recording without duplicate:", item["title"][:110], flush=True)
        else:
            publish_article(user_id, item, caption, vertical_cover)
            recent_captions.add(caption)
        done += 1
        seen.add(item["id"])
        pending.pop(item["id"], None)
        save_json(STATE, sorted(seen))
        save_json(PENDING, sorted(pending.values(), key=lambda x: (x["date"], x["id"])))
    save_json(PENDING, sorted(pending.values(), key=lambda x: (x["date"], x["id"])))
    print(f"Instagram: {done} published or reconciled; {len(pending)} still waiting.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
