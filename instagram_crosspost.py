#!/usr/bin/env python3
"""Dzen -> Instagram: queue public articles, publish only when matching 4:5 art exists.

Images arrive via instagram_cover_inbox.py and are committed to a public repository.
Never use Dzen landscape covers. Telegram, VK and Threads are unaffected.
"""
import asyncio
import json
import os
from pathlib import Path
from state_checkpoint import atomic_json
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
                           str(error.get("code", "unknown")) + ", subcode " +
                           str(error.get("error_subcode", "unknown"))) from None
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
    atomic_json(path, obj)


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
    """Fail closed when a prior successful Instagram post cannot be inspected."""
    response = graph("GET", str(user_id) + "/media", {
        "fields": "id,caption", "limit": "100"})
    data = response.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Instagram recent media lookup incomplete; publishing paused")
    return {row["caption"].strip() for row in data
            if isinstance(row, dict) and isinstance(row.get("caption"), str)
            and row["caption"].strip()}


def matches_published_caption(expected, recent_captions):
    """Identify an already delivered article by its full first-line headline.

    Meta can change harmless whitespace in the post body; the pending title
    is unique within the queue and must match the complete remote first line.
    """
    headline = normalize_title(expected.split("\n", 1)[0])
    if not headline:
        return False
    return any(remote == expected or
               normalize_title(remote.split("\n", 1)[0]) == headline
               for remote in recent_captions if isinstance(remote, str))


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
    # Defense in depth for manual invocations or future workflow edits.
    # A pause must NOT refresh the pending queue or contact the Instagram API.
    if os.getenv("INSTAGRAM_FEED_ENABLED", "false").lower() != "true":
        print("Instagram feed PAUSED: no publication and no new pending entries.", flush=True)
        return
    cutoff_text = os.getenv("INSTAGRAM_RESUME_FROM_UNIX", "").strip()
    if not cutoff_text.isdecimal() or int(cutoff_text) <= 0:
        raise RuntimeError("Instagram resume requires an explicit positive UTC UNIX cutoff; backlog held")
    resume_from = int(cutoff_text)
    if not STATE.exists() or not PENDING.exists() or not INBOX.exists():
        raise RuntimeError("Instagram state files missing; publishing paused")
    seen = set(json.loads(STATE.read_text(encoding="utf-8")))
    rows = json.loads(PENDING.read_text(encoding="utf-8"))
    inbox = json.loads(INBOX.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not isinstance(inbox.get("covers"), dict):
        raise RuntimeError("Instagram queue or cover registry invalid")
    covers = inbox["covers"]
    # On a deliberate future restart, drop pre-resumption items (including
    # old pending records). Never release a backlog of stale news.
    pending = {str(row["id"]): row for row in rows
               if str(row.get("id", "")) not in seen
               and int(row.get("date") or 0) >= resume_from}
    articles = await fetch_articles()
    for item in articles:
        if item["id"] not in seen and int(item.get("date") or 0) >= resume_from:
            pending[item["id"]] = {
                "id": str(item["id"]), "title": str(item.get("title") or ""),
                "text": short_teaser(item.get("text") or "", limit=550),
                "url": str(item.get("url") or ""), "date": int(item.get("date") or 0),
            }
    ordered = sorted(pending.values(), key=lambda row: (int(row.get("date") or 0), row["id"]))
    save_json(PENDING, ordered)
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
        caption = make_caption(item)
        if matches_published_caption(caption, recent_captions):
            print("Already on Instagram; recording without duplicate:", item["title"][:110], flush=True)
        elif item.get("instagram_publish_attempted"):
            # An earlier media_publish may have succeeded despite an API error.
            print("Instagram outcome unconfirmed; holding automatic retry:",
                  item["title"][:110], flush=True)
            continue
        else:
            if not is_cover_public(vertical_cover):
                print("Cover not yet available on public GitHub CDN:", item["title"][:110], flush=True)
                continue
            # Save before any network send: checkpointing preserves this flag.
            item["instagram_publish_attempted"] = True
            save_json(PENDING, sorted(
                pending.values(), key=lambda x: (int(x.get("date") or 0), x["id"])))
            try:
                publish_article(user_id, item, caption, vertical_cover)
            except RuntimeError:
                # An API error is not proof that publishing failed.
                try:
                    current = recently_published_captions(user_id)
                except RuntimeError:
                    print("Instagram verification unavailable; retry held:",
                          item["title"][:110], flush=True)
                    raise
                if not matches_published_caption(caption, current):
                    print("Instagram result not confirmed; retry held:",
                          item["title"][:110], flush=True)
                    raise
                print("Instagram delivery confirmed after API error:",
                      item["title"][:110], flush=True)
                recent_captions.update(current)
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
