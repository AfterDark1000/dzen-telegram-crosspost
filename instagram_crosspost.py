#!/usr/bin/env python3
"""Automatically mirror NEW Dzen articles to @iipike.ai as photo + short caption.

Uses the original landscape Dzen cover, not the discontinued square experiment.
State is separate from Telegram/VK and is committed by the GitHub workflow.
"""
import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from crosspost import fetch_articles, short_teaser

TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
STATE = Path("seen_instagram_articles.json")
API = "https://graph.instagram.com/v24.0"
EXPECTED_USERNAME = "iipike.ai"
MAX_PER_RUN = 10


def graph(method, path, values=None):
    values = values or {}
    endpoint = API + "/" + str(path).lstrip("/")
    if method == "GET":
        if values:
            endpoint += "?" + urllib.parse.urlencode(values)
        request = urllib.request.Request(endpoint, headers={
            "Authorization": "Bearer " + TOKEN,
            "Accept": "application/json",
        })
    else:
        request = urllib.request.Request(
            endpoint, data=urllib.parse.urlencode(values).encode("utf-8"),
            headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/json"},
        )
    try:
        with urllib.request.urlopen(request, timeout=50) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read().decode("utf-8")).get("error", {})
        except (ValueError, UnicodeError):
            error = {}
        code = error.get("code", "unknown")
        message = error.get("message", "Unknown error")
        raise RuntimeError(f"Instagram {method} {path}: HTTP {exc.code}, code {code}, {message}") from None
    if "error" in result:
        err = result["error"]
        raise RuntimeError(f"Instagram {method} {path}: {err.get('code')} {err.get('message')}")
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


def save_state(seen):
    STATE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def recently_published_captions(user_id):
    """Avoid duplicates if an API request succeeded but state wasn't pushed."""
    try:
        response = graph("GET", str(user_id) + "/media", {
            "fields": "id,caption",
            "limit": "100",
        })
        return {item.get("caption", "").strip() for item in response.get("data", [])
                if item.get("caption")}
    except Exception as exc:
        print("Could not read recent Instagram posts for duplicate check:", str(exc)[:160], flush=True)
        return set()


def publish_article(user_id, item, caption):
    cover = item.get("image") or ""
    if not cover.startswith("https://"):
        raise RuntimeError("Article has no usable HTTPS cover; not posting incomplete item")

    container = graph("POST", f"{user_id}/media", {"image_url": cover, "caption": caption})
    cid = container.get("id")
    if not cid:
        raise RuntimeError("Instagram did not create a media container")

    status = ""
    for _ in range(20):
        time.sleep(4)
        data = graph("GET", cid, {"fields": "status_code"})
        status = data.get("status_code", "")
        if status in {"FINISHED", "ERROR", "EXPIRED"}:
            break
    if status != "FINISHED":
        raise RuntimeError("Instagram photo was not ready for publication: " + str(status))

    result = graph("POST", f"{user_id}/media_publish", {"creation_id": cid})
    post_id = result.get("id")
    if not post_id:
        raise RuntimeError("Instagram returned no post ID; publication unconfirmed")
    print("Published on Instagram:", item["title"], "post ID:", post_id, flush=True)


async def main():
    if not TOKEN:
        print("Instagram paused: missing INSTAGRAM_ACCESS_TOKEN.", flush=True)
        return
    if not STATE.exists():
        raise RuntimeError("Missing Instagram state file. Refusing to post old archive.")

    seen = set(json.loads(STATE.read_text(encoding="utf-8")))
    profile = graph("GET", "me", {"fields": "id,user_id,username,account_type"})
    if profile.get("username") != EXPECTED_USERNAME:
        raise RuntimeError("Instagram token belongs to the wrong account; no posts were sent")
    user_id = profile.get("user_id") or profile.get("id")
    if not user_id:
        raise RuntimeError("Could not obtain Instagram publishing account ID")

    articles = await fetch_articles()
    pending = sorted((article for article in articles if article["id"] not in seen),
                     key=lambda article: (article["date"], article["id"]))
    print(f"Instagram: {len(articles)} feed articles; {len(pending)} new.", flush=True)
    if not pending:
        return

    recent_captions = recently_published_captions(user_id)
    for item in pending[:MAX_PER_RUN]:
        caption = make_caption(item)
        if caption in recent_captions:
            print("Already on Instagram, recording without duplicate:", item["title"], flush=True)
        else:
            publish_article(user_id, item, caption)
            recent_captions.add(caption)
        seen.add(item["id"])
        save_state(seen)

    if len(pending) > MAX_PER_RUN:
        print(f"{len(pending) - MAX_PER_RUN} articles remain for the next run.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
