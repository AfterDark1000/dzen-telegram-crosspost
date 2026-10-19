#!/usr/bin/env python3
"""Dzen -> Threads. New articles only; existing article IDs are pre-seeded.

Uses a separately scoped Threads token, never the Instagram token.
Approximately two concise text posts for one text+original-cover post.
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

from crosspost import fetch_articles, short_teaser

API = "https://graph.threads.net/v1.0"
TOKEN = os.getenv("THREADS_ACCESS_TOKEN", "").strip()
EXPECTED_USERNAME = "iipike.ai"
INTRO_TEXT = "«Хроники Пикирующего ИИ» теперь и в Threads. Здесь — короткие новости об искусственном интеллекте: без рекламного восторга, без страшилок и без лишней воды. Разбираемся, что действительно произошло."
STATE = Path("seen_threads_articles.json")
MAX_PER_RUN = 8
TEXT_LIMIT = 450  # keep clear of Threads' 500-character maximum


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


def clip_at_word(value, limit):
    value = re.sub(r"\s+", " ", value or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-") + "…"


def post_text(article):
    title = re.sub(r"\s+", " ", (article.get("title") or "")).strip()
    if not title:
        raise RuntimeError("Threads article without a title")
    title = clip_at_word(title, 340)
    rest = TEXT_LIMIT - len(title) - 2
    teaser = short_teaser(article.get("text", ""), limit=min(260, rest))
    teaser = clip_at_word(teaser, rest)
    content = title + ("\n\n" + teaser if teaser else "")
    if len(content) > TEXT_LIMIT:
        raise RuntimeError("Threads text unexpectedly long")
    return content


def save_state(seen):
    STATE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_recent_texts():
    """Extra duplicate protection if a publish succeeded before state was committed."""
    try:
        answer = api("GET", "me/threads", {"fields": "id,text", "limit": "100"})
        return {x.get("text", "").strip() for x in answer.get("data", [])
                if isinstance(x, dict) and x.get("text")}
    except Exception as error:
        print("Threads duplicate check unavailable:", str(error)[:220], flush=True)
        return set()


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
        raise RuntimeError("Missing Threads state file; refusing to post old archive.")

    profile = api("GET", "me", {"fields": "id,username"})
    if profile.get("username", "").lower() != EXPECTED_USERNAME:
        raise RuntimeError(
            "Threads token is not for @" + EXPECTED_USERNAME + "; "
            "no posts were sent."
        )
    print("Threads login verified: @" + EXPECTED_USERNAME, flush=True)
    if check_only:
        print("Threads check-only mode: no posts sent.", flush=True)
        return

    if intro:
        # Fail closed if previous posts cannot be inspected. No accidental duplicate.
        response = api("GET", "me/threads", {"fields": "id,text", "limit": "100"})
        current = {x.get("text", "").strip() for x in response.get("data", [])
                   if isinstance(x, dict)}
        if INTRO_TEXT in current:
            print("Threads welcome post already present; skipping.", flush=True)
        else:
            publish(INTRO_TEXT)
            print("Threads welcome post successfully published.", flush=True)
        return

    articles = await fetch_articles()
    seen = set(json.loads(STATE.read_text(encoding="utf-8")))
    pending = sorted((item for item in articles if item["id"] not in seen),
                     key=lambda item: (item["date"], item["id"]))
    print(f"Threads: {len(articles)} feed articles; {len(pending)} new.", flush=True)
    if not pending:
        return

    recent_texts = read_recent_texts()
    for item in pending[:MAX_PER_RUN]:
        text = post_text(item)
        if text in recent_texts:
            print("Threads already contains:", item["title"], flush=True)
        else:
            # Text-first feed, with original cover on about every third article.
            cover = item.get("image", "") if (len(seen) % 3 == 2) else ""
            if not cover.startswith("https://"):
                cover = ""
            publish(text, cover=cover)
            recent_texts.add(text)
        seen.add(item["id"])
        save_state(seen)

    if len(pending) > MAX_PER_RUN:
        print(f"Threads: {len(pending) - MAX_PER_RUN} remain for next run.", flush=True)


if __name__ == "__main__":
    asyncio.run(main(check_only=("--check" in sys.argv), intro=("--intro" in sys.argv)))
