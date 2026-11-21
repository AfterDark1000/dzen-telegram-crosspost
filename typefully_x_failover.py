#!/usr/bin/env python3
"""HPII X failover: Typefully only during a persisted Buffer 429 cooldown.

No automatic retry of an ambiguous publishing attempt. Uses the same local
X queue as Buffer, saved by the existing crosspost state checkpoint.
"""
from datetime import datetime
import asyncio
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from crosspost import fetch_articles
from x_editorial_queue import load_queue, matching_approved, delivery_text, save_queue

API_BASE = "https://api.typefully.com/v2"
EXPECTED_X = "iipike_ai"
MAX_PER_30D = 8  # leave two Free-plan posts for manual use
MAX_PAGES = 10


class FailoverError(RuntimeError):
    pass


def api(method, path, body=None):
    key = os.environ.get("TYPEFULLY_API_KEY", "").strip()
    if not key:
        raise FailoverError("TYPEFULLY_API_KEY is not configured")
    if not path.startswith("/") or path.startswith("//"):
        raise FailoverError("Invalid Typefully API path")
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API_BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise FailoverError("Typefully HTTP " + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise FailoverError("Typefully response unavailable; delivery unconfirmed") from None
    if not isinstance(result, dict):
        raise FailoverError("Typefully response incomplete")
    return result


def social_set():
    raw = os.environ.get("TYPEFULLY_SOCIAL_SET_ID", "").strip()
    if not re.fullmatch(r"[1-9][0-9]*", raw):
        raise FailoverError("TYPEFULLY_SOCIAL_SET_ID is not configured")
    value = api("GET", "/social-sets/" + raw + "/")
    x = (value.get("platforms") or {}).get("x") or {}
    if not isinstance(x, dict) or str(x.get("username", "")).lstrip("@").lower() != EXPECTED_X:
        raise FailoverError("Typefully account is not @iipike_ai; NO PUBLISHING")
    return int(raw)


def list_drafts(sid):
    """All drafts must be inspected; incomplete pagination blocks any write."""
    found = []
    for offset in range(0, 50 * MAX_PAGES, 50):
        data = api("GET", f"/social-sets/{sid}/drafts?limit=50&offset={offset}")
        rows = data.get("results")
        if not isinstance(rows, list) or not isinstance(data.get("count"), int):
            raise FailoverError("Typefully draft list incomplete; no publishing")
        found.extend(rows)
        if len(rows) < 50 and not data.get("next"):
            return found
        if not data.get("next") and len(found) >= data["count"]:
            return found
    raise FailoverError("Typefully draft list exceeds pagination safety cap")


def marker(entry):
    return "HPII X fallback: " + entry["id"]


def draft_matches(sid, draft_id, expected):
    data = api("GET", f"/social-sets/{sid}/drafts/{int(draft_id)}")
    x = (data.get("platforms") or {}).get("x") or {}
    posts = x.get("posts") if isinstance(x, dict) else None
    if not x.get("enabled") or not isinstance(posts, list) or len(posts) != 1:
        raise FailoverError("Typefully draft platform differs; manual reconciliation")
    if posts[0].get("text") != expected:
        raise FailoverError("Typefully draft text differs; manual reconciliation")
    return data


def x_published_url(item):
    url = item.get("x_published_url")
    if not isinstance(url, str):
        return None
    parts = urllib.parse.urlsplit(url)
    if (parts.scheme == "https"
            and parts.netloc.lower() in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}
            and re.fullmatch(r"/iipike_ai/status/[0-9]+/?", parts.path, re.I)
            and not parts.username and not parts.password):
        return url
    return None


def reconcile(queue, entry, article, data):
    """A published X URL establishes delivery; a created draft does not."""
    status = data.get("status")
    draft_id = data.get("id")
    if not isinstance(draft_id, int) or draft_id <= 0:
        raise FailoverError("Typefully draft ID missing")
    entry["typefully_draft_id"] = draft_id
    entry["typefully_state"] = str(status)
    if status == "published" and x_published_url(data):
        entry.update(status="queued", dzen_id=str(article["id"]), dzen_url=str(article["url"]),
                     with_image=False, typefully_post_url=x_published_url(data),
                     x_delivery_provider="typefully", typefully_state="published")
        save_queue(queue)
        print("X TYPEFULLY VERIFIED: published", entry["id"], flush=True)
        return "published"
    if status == "error":
        save_queue(queue)
        raise FailoverError("Typefully draft reports error; HOLD, no automatic resend")
    if status not in {"draft", "publishing", "scheduled", "published"}:
        save_queue(queue)
        raise FailoverError("Unexpected Typefully publish state; HOLD")
    save_queue(queue)
    print("X TYPEFULLY PENDING: draft=" + str(draft_id) + " status=" + str(status))
    return "pending"


def recent_budget(drafts, now):
    """Conservative limit in addition to Typefully's own 10/month restriction."""
    recent = 0
    for d in drafts:
        if not isinstance(d, dict):
            raise FailoverError("Malformed Typefully draft listing")
        if not d.get("x_post_enabled") or d.get("status") not in {"scheduled", "publishing", "published", "error"}:
            continue
        raw = d.get("created_at")
        if not isinstance(raw, str):
            raise FailoverError("Typefully draft date missing; cannot enforce quota")
        try:
            when = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            raise FailoverError("Typefully draft date invalid") from None
        if now - 30*86400 <= when <= now + 300:
            recent += 1
    return recent


async def main():
    queue = load_queue()
    now = int(time.time())
    pending = [e for e in queue["items"] if e["status"] == "approved"
               and (e.get("typefully_draft_id") or e.get("typefully_attempt_unknown"))]
    cooldown = (queue.get("buffer_rate_limit_active") is True
                and int(queue.get("buffer_cooldown_until", 0)) > now)
    if not cooldown and not pending:
        print("X TYPEFULLY: Buffer available; primary route")
        return "primary"
    if os.environ.get("TYPEFULLY_FAILOVER_ENABLED") != "true":
        if pending:
            raise FailoverError("Pending Typefully post while failover disabled; BLOCK BUFFER")
        print("X TYPEFULLY: not enabled; Buffer cooldown preserved")
        return "primary"
    sid = social_set()
    drafts = list_drafts(sid)
    # Reconcile unfinished Typefully posts before any new Buffer or Typefully write.
    if pending:
        if len(pending) > 1:
            raise FailoverError("Multiple pending Typefully posts; manual reconciliation")
        entry = pending[0]
        candidates = [d for d in drafts if d.get("draft_title") == marker(entry)]
        if len(candidates) != 1:
            raise FailoverError("Typefully submission unknown/unlisted; NO RETRY and NO BUFFER")
        article = {"id": entry.get("typefully_source_dzen_id"),
                   "url": entry.get("typefully_source_dzen_url")}
        if not article["id"] or not article["url"] or not isinstance(entry.get("typefully_post_text"), str):
            raise FailoverError("Typefully pending metadata incomplete; HOLD")
        data = draft_matches(sid, candidates[0]["id"], entry["typefully_post_text"])
        outcome = reconcile(queue, entry, article, data)
        return "reconciled" if outcome == "published" else "pending"
    if not cooldown:
        return "primary"
    articles = await fetch_articles()
    matches = matching_approved(articles, queue, now, max_age=3*86400)
    if not matches:
        print("X TYPEFULLY: no fresh approved exact-title Dzen matches")
        return "none"
    submissions = [int(e.get("typefully_attempt_at", 0)) for e in queue["items"]
                   if e.get("typefully_attempt_at")]
    if submissions and now - max(submissions) < 24*3600:
        print("X TYPEFULLY: one post per 24 hours")
        return "none"
    if recent_budget(drafts, now) >= MAX_PER_30D:
        print("X TYPEFULLY: conservative 8-post rolling limit reached")
        return "none"
    article, entry = matches[0]
    message = delivery_text(entry, article)
    if not 1 <= len(message) <= 280:
        raise FailoverError("Approved X text exceeds 280 characters")
    candidates = [d for d in drafts if d.get("draft_title") == marker(entry)]
    if len(candidates) > 1:
        raise FailoverError("Ambiguous Typefully drafts; HOLD")
    if candidates:
        # Recovery of an already-created draft, never a second publication.
        entry.update(typefully_draft_id=candidates[0]["id"], typefully_post_text=message,
                     typefully_source_dzen_id=article["id"], typefully_source_dzen_url=article["url"],
                     typefully_attempt_unknown=True)
        save_queue(queue)
        data = draft_matches(sid, candidates[0]["id"], message)
        reconcile(queue, entry, article, data)
        return "recovered"
    if any(d.get("x_post_enabled") and d.get("preview") == message for d in drafts):
        raise FailoverError("Identical X text already exists in Typefully; manual reconciliation")
    # Durable unknown-attempt marker. If POST times out, DO NOT blindly retry.
    entry.update(typefully_attempt_at=now, typefully_attempt_unknown=True,
                 typefully_post_text=message, typefully_source_dzen_id=article["id"],
                 typefully_source_dzen_url=article["url"])
    save_queue(queue)
    body = {"draft_title": marker(entry), "share": False,
            "platforms": {"x": {"enabled": True, "posts": [{"text": message}]}},
            "publish_at": "now"}
    data = api("POST", f"/social-sets/{sid}/drafts", body)
    if data.get("draft_title") != marker(entry):
        raise FailoverError("Typefully draft identity mismatch; HOLD")
    entry["typefully_draft_id"] = data.get("id")
    reconcile(queue, entry, article, data)
    return "created"


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except FailoverError as exc:
        print("X TYPEFULLY HOLD:", exc, file=sys.stderr, flush=True)
        sys.exit(1)
