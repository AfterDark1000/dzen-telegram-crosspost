#!/usr/bin/env python3
"""Approved author-written Threads posts, keyed to future Dzen article titles.

ChatGPT writes to this file ONLY after explicit user approval. The crossposter
never generates, paraphrases, shortens or substitutes content if no item matches.
No payment, server, web UI or second Threads publisher is required.
"""
import json
from pathlib import Path
import re
import unicodedata

QUEUE_FILE = Path("threads_editorial_queue.json")
TEXT_LIMIT = 480  # Keep a little margin below Threads' 500-character limit.
ALLOWED_STATUSES = {"approved", "published", "on_hold"}


def title_key(text):
    """Ignore whitespace, punctuation, case and ё/е; never fuzzy-guess."""
    text = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(re.findall(r"[^\W_]+", text, re.UNICODE))


def validate_queue(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise RuntimeError("Threads editorial queue has an unknown version")
    entries = data.get("items")
    if not isinstance(entries, list):
        raise RuntimeError("Threads editorial queue must contain items list")
    keys, ids = set(), set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("Threads editorial entry is malformed")
        identifier = entry.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,72}", identifier):
            raise RuntimeError("Threads editorial entry has invalid ID")
        if identifier in ids:
            raise RuntimeError("Duplicate editorial item ID: " + identifier)
        ids.add(identifier)
        title = entry.get("dzen_title")
        text = entry.get("threads_text")
        status = entry.get("status")
        if not isinstance(title, str) or len(title.strip()) < 8 or len(title) > 300:
            raise RuntimeError("Threads queue needs the exact approved Dzen title")
        if not isinstance(text, str) or not text.strip() or len(text) > TEXT_LIMIT:
            raise RuntimeError("Threads editorial post missing or exceeds 480 characters: " + identifier)
        if status not in ALLOWED_STATUSES:
            raise RuntimeError("Threads editorial item status is invalid: " + identifier)
        key = title_key(title)
        if len(key) < 5 or key in keys:
            raise RuntimeError("Threads queue has duplicate or ambiguous Dzen title: " + title)
        keys.add(key)
        if status == "published" and not entry.get("dzen_id"):
            raise RuntimeError("Published Threads queue entry lacks Dzen ID")
    return entries


def load_queue(path=QUEUE_FILE):
    if not path.exists():
        raise RuntimeError("No approved Threads queue file: no posts sent")
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_queue(data)
    return data


def eligible_matches(articles, queue, seen):
    """Require exactly one unposted article per approved title and vice versa."""
    entries = validate_queue(queue)
    unmatched = [x for x in articles if str(x.get("id")) not in seen]
    by_title = {}
    for article in unmatched:
        name = article.get("title")
        if isinstance(name, str):
            by_title.setdefault(title_key(name), []).append(article)
    matched = []
    for entry in entries:
        if entry["status"] != "approved":
            continue
        candidates = by_title.get(title_key(entry["dzen_title"]), [])
        if len(candidates) == 1:
            article = candidates[0]
            if entry.get("dzen_id") and str(entry["dzen_id"]) != str(article["id"]):
                raise RuntimeError("Editorial queue Dzen ID disagrees with current article")
            matched.append((article, entry))
        elif len(candidates) > 1:
            print("AMBIGUOUS headline in Dzen; Threads post held:", entry["dzen_title"], flush=True)
    # Fail closed if the same Dzen article matched more than one queue entry.
    ids = [str(article["id"]) for article, _ in matched]
    if len(ids) != len(set(ids)):
        raise RuntimeError("One Dzen ID matched multiple editorial queue entries")
    return sorted(matched, key=lambda pair: (int(pair[0].get("date") or 0), str(pair[0]["id"])))


def mark_published(entry, article, threads_post_id):
    entry["status"] = "published"
    entry["dzen_id"] = str(article["id"])
    entry["dzen_url"] = str(article.get("url") or "")
    entry["threads_post_id"] = str(threads_post_id)
    return entry


def save_queue(queue, path=QUEUE_FILE):
    validate_queue(queue)
    path.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
