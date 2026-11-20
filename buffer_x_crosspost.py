#!/usr/bin/env python3
"""Publish ONLY approved short X texts through Buffer, with remote duplicate recovery."""
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import ipaddress
import json
import os
import sys
import time
from urllib.parse import urlsplit
from crosspost import fetch_articles
from buffer_x_check import graphql, connected_x_channel, BufferRateLimitError
from x_editorial_queue import load_queue, matching_approved, delivery_text, mark_queued, save_queue

EXPECTED_NAME = "iipike_ai"
MAX_REMOTE_QUEUE = 8  # Buffer Free has 10 scheduled slots. Keep two slots for manual use.
MAX_PER_RUN = 1
CHANNEL_CACHE_SECONDS = 12 * 3600
RATE_LIMIT_INITIAL_COOLDOWN = 6 * 3600
RATE_LIMIT_MAX_COOLDOWN = 24 * 3600


def retry_seconds(value, now):
    if not value:
        return 0
    try:
        return max(0, int(float(value)))
    except (ValueError, TypeError):
        try:
            when = parsedate_to_datetime(str(value))
            if when.tzinfo is None:
                return 0
            return max(0, int(when.timestamp() - now))
        except (ValueError, TypeError, OverflowError, IndexError):
            return 0


def record_rate_limit(queue, error, now):
    """Durably back off on 429 so hourly Actions do not burn another request."""
    strikes = min(4, max(0, int(queue.get("buffer_rate_limit_strikes", 0))) + 1)
    wait = min(RATE_LIMIT_MAX_COOLDOWN, RATE_LIMIT_INITIAL_COOLDOWN * (2 ** (strikes - 1)))
    wait = max(wait, retry_seconds(error.retry_after, now))
    queue["buffer_rate_limit_strikes"] = strikes
    queue["buffer_cooldown_until"] = int(now + wait)
    queue["buffer_rate_limit_active"] = True
    save_queue(queue)
    print("X BUFFER RATE LIMIT: cooldown until " +
          datetime.fromtimestamp(queue["buffer_cooldown_until"], timezone.utc).isoformat() +
          "; retry only after cooldown; delivery NOT confirmed", file=sys.stderr, flush=True)



def attach_cover_next(queue):
    """First confirmed X post has an image; alternate with text-only thereafter.

    Count confirmed queued editorial records, not hourly runs or incoming news:
    rejected media and stale or unmatched headlines never change the sequence.
    """
    return sum(row["status"] == "queued" and bool(row.get("buffer_post_id")) and bool(row.get("dzen_id"))
               for row in queue["items"]) % 2 == 0


def verified_cover_url(article):
    """Accept only a plausible public HTTPS image URL from the actual Dzen feed."""
    candidate = article.get("image")
    if not isinstance(candidate, str) or not candidate or len(candidate) > 4096:
        return None
    try:
        parts = urlsplit(candidate)
        hostname = parts.hostname
        if parts.scheme != "https" or not hostname or parts.username or parts.password:
            return None
        if hostname == "localhost" or hostname.endswith(".localhost") or hostname.endswith(".local"):
            return None
        try:
            if not ipaddress.ip_address(hostname).is_global:
                return None
        except ValueError:
            pass
    except ValueError:
        return None
    return candidate


def is_image_post(remote):
    assets = remote.get("assets")
    if not isinstance(assets, list):
        raise RuntimeError("Buffer remote assets missing; cannot validate X image alternation")
    return any(isinstance(asset, dict) and
               str(asset.get("mimeType") or "").lower().startswith("image/")
               for asset in assets)


def find_channel():
    matches = [x for x in connected_x_channel()
               if str(x.get("service", "")).lower() == "twitter"
               and EXPECTED_NAME in {
                   str(x.get("name", "")).lstrip("@").casefold(),
                   str(x.get("displayName", "")).lstrip("@").casefold()
               }]
    if len(matches) != 1:
        raise RuntimeError("Buffer X channel @iipike_ai is missing or ambiguous: no posts")
    if matches[0].get("isQueuePaused"):
        raise RuntimeError("Buffer X queue is paused; no posts")
    return matches[0]


def remote_posts(org_id, channel_id, statuses, max_pages=8):
    """Read complete remote pages; fail closed rather than risk duplicate posts."""
    found, after = [], None
    for _ in range(max_pages):
        paging = ", after: " + json.dumps(after) if after is not None else ""
        query = (
            "query { posts(first: 100" + paging + ", input: {organizationId: " +
            json.dumps(org_id) + ", filter: {status: [" + ",".join(statuses) +
            "], channelIds: [" + json.dumps(channel_id) + "]}}) {" +
            " edges { node { id text status assets { id mimeType } } } pageInfo { hasNextPage endCursor } } }"
        )
        result = graphql(query).get("posts")
        if not isinstance(result, dict) or not isinstance(result.get("edges"), list):
            raise RuntimeError("Buffer remote posts lookup incomplete; no X posts sent")
        for edge in result["edges"]:
            node = edge.get("node") if isinstance(edge, dict) else None
            if (not isinstance(node, dict) or not node.get("id")
                    or not isinstance(node.get("text"), str)
                    or not isinstance(node.get("assets"), list)
                    or str(node.get("status", "")).lower() not in {"sent", "scheduled", "sending", "error"}):
                raise RuntimeError("Buffer remote post entry incomplete; no X posts sent")
            found.append(node)
        page = result.get("pageInfo")
        if not isinstance(page, dict) or not isinstance(page.get("hasNextPage"), bool):
            raise RuntimeError("Buffer pagination info missing; no X posts sent")
        if not page["hasNextPage"]:
            return found
        after = page.get("endCursor")
        if not isinstance(after, str) or not after:
            raise RuntimeError("Buffer next page cursor missing; no X posts sent")
    raise RuntimeError("Buffer pagination exceeded safe limit; no X posts sent")


def create_immediate_post(channel_id, text, cover_url=None):
    """Request immediate X delivery via Buffer, using verified GraphQL media assets."""
    asset_input = (", assets: [{image: {url: " + json.dumps(cover_url) + "}}]"
                   if cover_url else "")
    query = (
        "mutation { createPost(input: {text: " + json.dumps(text, ensure_ascii=False) +
        ", channelId: " + json.dumps(channel_id) +
        ", schedulingType: automatic, mode: shareNow" + asset_input + "}) {" +
        " ... on PostActionSuccess { post { id text assets { id mimeType } } }" +
        " ... on MutationError { message } } }"
    )
    result = graphql(query).get("createPost")
    if not isinstance(result, dict):
        raise RuntimeError("Buffer immediate X post not confirmed")
    posted = result.get("post")
    if isinstance(posted, dict) and posted.get("id") and posted.get("text") == text:
        # The returned assets, not just an ID, must confirm requested media.
        # If Buffer succeeded remotely but returned incomplete data, a later
        # remote-post read will recover this post without issuing a duplicate.
        if is_image_post(posted) != bool(cover_url):
            raise RuntimeError("Buffer X post media mismatch; no automatic text-only fallback")
        return str(posted["id"])
    raise RuntimeError("Buffer immediate X post not confirmed; inspect Buffer before retry")


async def main():
    queue = load_queue()
    now = int(time.time())
    if int(queue.get("buffer_cooldown_until", 0)) > now:
        print("X BUFFER RATE LIMIT: paused until " +
              datetime.fromtimestamp(queue["buffer_cooldown_until"], timezone.utc).isoformat() +
              "; no Buffer API calls", flush=True)
        return
    if not any(row["status"] == "approved" for row in queue["items"]):
        print("X: no approved editorial posts; no API writes or historic backfill", flush=True)
        return
    if not os.getenv("BUFFER_API_KEY"):
        raise RuntimeError("BUFFER_API_KEY missing; X paused")
    articles = await fetch_articles()
    matches = matching_approved(articles, queue, int(time.time()))
    if not matches:
        print("X: no current matching approved Dzen headlines; nothing to queue", flush=True)
        return
    cached = queue.get("buffer_channel_cache")
    if (isinstance(cached, dict) and
            isinstance(cached.get("id"), str) and cached.get("id") and
            isinstance(cached.get("org_id"), str) and cached.get("org_id") and
            isinstance(cached.get("verified_at"), int) and
            0 <= now - cached["verified_at"] < CHANNEL_CACHE_SECONDS):
        channel = cached
    else:
        channel = find_channel()
        # The cache is recorded only after an authenticated remote lookup.
        cache_pending = {"id": channel["id"], "org_id": channel["org_id"],
                         "verified_at": now}
    org, ch = channel["org_id"], channel["id"]
    # An immediate post can remain "sending" while the worker delivers it.
    # Include in-flight posts in duplicate recovery before ANY new write.
    # One pagination stream rather than separate pending/sent queries.
    # Includes errors so a failed Buffer post is never blindly recreated.
    all_remote = remote_posts(org, ch, ["scheduled", "sending", "sent", "error"])
    pending = [p for p in all_remote if p["status"].lower() in {"scheduled", "sending"}]
    sent = [p for p in all_remote if p["status"].lower() == "sent"]
    failed = [p for p in all_remote if p["status"].lower() == "error"]
    if "cache_pending" in locals():
        queue["buffer_channel_cache"] = cache_pending
        save_queue(queue)
    # A successful authenticated remote read proves the 429 has cleared,
    # but does NOT prove a particular post reached X.
    if queue.get("buffer_rate_limit_active"):
        queue["buffer_rate_limit_active"] = False
        queue["buffer_rate_limit_strikes"] = 0
        queue["buffer_cooldown_until"] = 0
        queue["buffer_last_api_ok_at"] = int(time.time())
        save_queue(queue)
    remote_by_text = {x["text"].strip(): x for x in pending + sent + failed}
    for article, entry in matches[:MAX_PER_RUN]:
        message = delivery_text(entry, article)
        with_image = attach_cover_next(queue)
        cover = verified_cover_url(article) if with_image else None
        if with_image and not cover:
            print("X: image slot held until Dzen article has an HTTPS cover:",
                  entry["dzen_title"], flush=True)
            continue
        if message in remote_by_text:
            existing = remote_by_text[message]
            if existing["status"].lower() == "error":
                raise RuntimeError("Buffer rejected an identical X post; hold for manual review")
            if is_image_post(existing) != with_image:
                raise RuntimeError(
                    "Existing X post has different image state; hold for editorial review")
            post_id = existing["id"]
            print("X: approved message already found remotely; recovering delivery state", flush=True)
        else:
            if len(pending) >= MAX_REMOTE_QUEUE:
                print("X: Buffer Free queue near limit; delaying editorial entries", flush=True)
                return
            post_id = create_immediate_post(ch, message, cover_url=cover)
            print("X: approved message accepted for immediate Buffer delivery",
                  "with Dzen cover" if with_image else "text only", flush=True)
        mark_queued(entry, article, post_id, with_image=with_image)
        save_queue(queue)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BufferRateLimitError as exc:
        # Persist cooldown via the ordinary crosspost state checkpoint.
        # Never retry a potentially accepted post or blindly resubmit backlog.
        try:
            record_rate_limit(load_queue(), exc, int(time.time()))
        except Exception as save_error:
            print("X COOLDOWN STATE ERROR:", save_error, file=sys.stderr)
        print("X PUBLISH ERROR:", exc, file=sys.stderr)
        sys.exit(1)
    except RuntimeError as exc:
        print("X PUBLISH ERROR:", exc, file=sys.stderr)
        sys.exit(1)
