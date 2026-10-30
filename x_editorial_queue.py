#!/usr/bin/env python3
"""Strict, author-approved X texts paired to exact public Dzen article titles."""
import json
import re
from pathlib import Path
from state_checkpoint import atomic_json
from threads_editorial_queue import title_key, verified_public_dzen_url

QUEUE_FILE = Path("x_editorial_queue.json")
STATUSES = {"approved", "queued", "on_hold"}
MAX_TEXT = 280


def validate_queue(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise RuntimeError("X editorial queue schema invalid")
    items = data.get("items")
    if not isinstance(items, list):
        raise RuntimeError("X queue must have items")
    ids, titles = set(), set()
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("Malformed X editorial item")
        ident, title = item.get("id"), item.get("dzen_title")
        text, status = item.get("x_text"), item.get("status")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,72}", ident):
            raise RuntimeError("Invalid X editorial ID")
        if not isinstance(title, str) or not 8 <= len(title) <= 300:
            raise RuntimeError("X editorial item missing exact Dzen title")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
            raise RuntimeError("X text must be nonempty and at most 280 chars")
        if status not in STATUSES:
            raise RuntimeError("Invalid X editorial status")
        if not isinstance(item.get("include_dzen_link", False), bool):
            raise RuntimeError("X link flag must be Boolean")
        key = title_key(title)
        if len(key) < 5 or ident in ids or key in titles:
            raise RuntimeError("Duplicate X title or ID")
        ids.add(ident)
        titles.add(key)
        if status == "queued" and not (item.get("dzen_id") and item.get("buffer_post_id")):
            raise RuntimeError("Queued X post missing delivery evidence")
    return items


def load_queue(path=QUEUE_FILE):
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_queue(data)
    return data


def matching_approved(articles, queue, now, max_age=3*86400):
    approved = [x for x in validate_queue(queue) if x["status"] == "approved"]
    by_title = {}
    for article in articles:
        if not isinstance(article.get("title"), str):
            continue
        by_title.setdefault(title_key(article["title"]), []).append(article)
    result = []
    for item in approved:
        candidates = by_title.get(title_key(item["dzen_title"]), [])
        if len(candidates) != 1:
            continue
        article = candidates[0]
        when = int(article.get("date") or 0)
        if when <= 0 or not 0 <= now - when <= max_age:
            print("X skip older or future article:", item["dzen_title"], flush=True)
            continue
        result.append((article, item))
    return sorted(result, key=lambda pair: (int(pair[0]["date"]), pair[0]["id"]))


def delivery_text(entry, article):
    message = entry["x_text"].strip()
    if entry.get("include_dzen_link"):
        message += "\n\n" + verified_public_dzen_url(article.get("url"))
    if len(message) > MAX_TEXT:
        raise RuntimeError("X approved text plus link would exceed 280 characters")
    return message


def mark_queued(entry, article, buffer_post_id):
    entry.update(status="queued", dzen_id=str(article["id"]),
                 dzen_url=str(article["url"]), buffer_post_id=str(buffer_post_id))


def save_queue(data, path=QUEUE_FILE):
    validate_queue(data)
    atomic_json(path, data)
