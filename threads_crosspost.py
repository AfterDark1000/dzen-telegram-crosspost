#!/usr/bin/env python3
"""Dzen -> Threads. Publish approved editorial texts only, never auto-teasers.

Existing posted Dzen article IDs remain pre-seeded and untouched. Queue
entries are created by ChatGPT only after explicit user approval.
"""
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from crosspost import fetch_articles
from threads_editorial_queue import (
    eligible_matches, load_queue, mark_published, save_queue
)

API = "https://graph.threads.net/v1.0"
TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
EXPECTED_USERNAME = "iipike.ai"
INTRO_TEXT = "«Хроники Пикирующего ИИ» теперь и в Threads. Здесь — короткие новости об искусственном интеллекте: без рекламного восторга, без страшилок и без лишней воды. Разбираемся, что действительно произошло."
STATE = Path("seen_threads_articles.json")
MAX_PER_RUN = 8


def api(method, path, params=None):
    params = params or {}
    url = API + "/" + path.lstrip("/")
    data = None
    if method == "GET":
        if params:
            url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": "Bearer " + TOKEN, "Accept": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            error = json.loads(exc.read().decode("utf-8")).get("error", {})
        except (UnicodeError, ValueError):
            error = {}
        raise RuntimeError(
            f"Threads {method} {path}: HTTP {exc.code}, "
            f"code {error.get('code', 'unknown')}: "
            f"{str(error.get('message', 'request failed'))[:220]}"
        ) from None
    if not isinstance(result, dict) or "error" in result:
        raise RuntimeError(f"Threads {method} {path}: invalid API response")
    return result


def save_state(seen):
    STATE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_recent_texts():
    """Read recent Threads posts BEFORE any new send, or fail closed.

    Used to recover from Threads publish success followed by a GitHub commit
    failure; never silently assume that the account contains no post.
    """
    response = api("GET", "me/threads", {"fields": "id,text", "limit": "100"})
    data = response.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Threads recent-post lookup incomplete; posting paused")
    return {row["text"].strip(): str(row.get("id") or "")
            for row in data if isinstance(row, dict)
            and isinstance(row.get("text"), str) and row.get("text", "").strip()}


def create_container(text, cover=None):
    params = {"media_type": "IMAGE" if cover else "TEXT", "text": text}
    if cover:
        params["image_url"] = cover
        params["alt_text"] = "Обложка новости: " + text.split("\n", 1)[0][:150]
    result = api("POST", "me/threads", params)
    cid = str(result.get("id") or "")
    if not cid:
        raise RuntimeError("Threads created no container ID")
    return cid


def wait_until_ready(cid):
    for _ in range(12):
        result = api("GET", cid, {"fields": "status,error_message"})
        status = result.get("status", "")
        if status == "FINISHED":
            return
        if status == "PUBLISHED":
            raise RuntimeError("Threads container already published; verify before retrying")
        if status in ("ERROR", "EXPIRED"):
            raise RuntimeError(
                "Threads media container " + status + ": " +
                str(result.get("error_message", ""))[:180]
            )
        time.sleep(5)
    raise RuntimeError("Threads container not ready after 60 seconds; no post sent")


def publish(text, cover=None):
    # A remote image can be rejected during container creation.
    # If so, publish the same news as text; do not lose the news.
    if cover:
        try:
            cid = create_container(text, cover)
        except RuntimeError as error:
            print("Threads image container rejected; using text:", str(error)[:180], flush=True)
            cid = create_container(text)
            cover = None
    else:
        cid = create_container(text)
    wait_until_ready(cid)
    answer = api("POST", "me/threads_publish", {"creation_id": cid})
    post_id = str(answer.get("id") or "")
    if not post_id:
        raise RuntimeError("Threads did not confirm publication; check profile before retrying")
    print("Threads published:", post_id, "with cover" if cover else "text only", flush=True)
    return post_id


async def main(check_only=False, intro=False):
    if not TOKEN:
        print("Threads paused: add THREADS_ACCESS_TOKEN to GitHub Actions secrets.", flush=True)
        return
    if not STATE.exists():
        raise RuntimeError("Missing Threads state: refusing to re-post historic Dzen articles.")

    queue = load_queue()
    seen = set(json.loads(STATE.read_text(encoding="utf-8")))
    if not all(isinstance(x, str) for x in seen):
        raise RuntimeError("Corrupt Threads seen-ID state; refusing to post")

    if not check_only and not intro and not any(
            entry["status"] == "approved" for entry in queue["items"]):
        print("Threads: no approved author-written posts; no publishing.", flush=True)
        return

    articles = []
    matches = []
    if not check_only and not intro:
        articles = await fetch_articles()
        matches = eligible_matches(articles, queue, seen)
        print("Threads: "+str(len(articles))+" Dzen feed items; "+
              str(len(matches))+" uniquely matched approved posts.", flush=True)
        if not matches:
            print("Unmatched editorial posts wait for the exact Dzen headline; no auto-teasers.", flush=True)
            return

    profile = api("GET", "me", {"fields": "id,username"})
    if profile.get("username", "").lower() != EXPECTED_USERNAME:
        raise RuntimeError("Threads token is not for @" + EXPECTED_USERNAME + "; no posts sent")
    print("Threads login verified: @" + EXPECTED_USERNAME, flush=True)

    if check_only:
        print("Threads check-only mode: no posts sent.", flush=True)
        return
    if intro:
        # An explicit --intro is separate from regular editorial publishing.
        current = read_recent_texts()
        if INTRO_TEXT in current:
            print("Threads welcome post already present; skipping.", flush=True)
        else:
            publish(INTRO_TEXT)
        return

    # Protection against retry after a previous successful API post whose
    # GitHub state commit failed. If this lookup fails, DO NOT publish.
    recent_texts = read_recent_texts()
    for item, approved in matches[:MAX_PER_RUN]:
        message = approved["threads_text"].strip()
        if message in recent_texts:
            post_id = recent_texts[message]
            if not post_id:
                raise RuntimeError("Threads prior post found without ID; hold for manual review")
            print("Threads already contains approved author post:", item["title"], flush=True)
        else:
            # Preserve the historic alternating text / original-cover pattern.
            cover = item.get("image", "") if len(seen) % 3 == 2 else ""
            if not isinstance(cover, str) or not cover.startswith("https://"):
                cover = ""
            post_id = publish(message, cover=cover)
            recent_texts[message] = post_id
            print("Threads author post published:", item["title"], flush=True)
        mark_published(approved, item, post_id)
        seen.add(str(item["id"]))
        # Both files will be committed together by the GitHub workflow.
        save_queue(queue)
        save_state(seen)

    if len(matches) > MAX_PER_RUN:
        print(f"Threads: {len(matches) - MAX_PER_RUN} approved posts await next run.", flush=True)


if __name__ == "__main__":
    asyncio.run(main(check_only=("--check" in sys.argv), intro=("--intro" in sys.argv)))
