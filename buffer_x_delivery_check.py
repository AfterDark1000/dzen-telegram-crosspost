#!/usr/bin/env python3
"""Read-only reconciliation of already accepted Buffer X posts; never publishes."""
import json
import os
import sys
import time

from buffer_x_check import BufferRateLimitError, connected_x_channel, graphql
from x_editorial_queue import load_queue


def recent_remote_posts(org_id, channel_id):
    # One bounded query per audit. A missing record is unknown, not proof of failure.
    query = ("query { posts(first: 100, input: {organizationId: " +
             json.dumps(org_id) + ", filter: {status: [sent, error, scheduled, sending], " +
             "channelIds: [" + json.dumps(channel_id) + "]}}) {" +
             " edges { node { id status text } } pageInfo { hasNextPage } } }")
    data = graphql(query).get("posts")
    if not isinstance(data, dict) or not isinstance(data.get("edges"), list):
        raise RuntimeError("Incomplete Buffer delivery status response")
    posts = []
    for edge in data["edges"]:
        node = edge.get("node") if isinstance(edge, dict) else None
        if not isinstance(node, dict) or not isinstance(node.get("id"), str):
            raise RuntimeError("Malformed Buffer delivery status response")
        status = str(node.get("status", "")).lower()
        if status not in {"sent", "error", "scheduled", "sending"}:
            raise RuntimeError("Unexpected Buffer delivery state")
        posts.append(node)
    return posts, bool((data.get("pageInfo") or {}).get("hasNextPage"))


def inspect(queue, posts):
    remote = {post["id"]: str(post["status"]).lower() for post in posts}
    queued = [p for p in queue.get("items", [])
              if p.get("status") == "queued" and p.get("buffer_post_id")]
    seen = [(p["id"], remote[p["buffer_post_id"]]) for p in queued
            if p["buffer_post_id"] in remote]
    return {
        "buffer_errors": [ident for ident, state in seen if state == "error"],
        "buffer_sent": sum(state == "sent" for _, state in seen),
        "buffer_pending": sum(state in {"scheduled", "sending"} for _, state in seen),
        "unverified": len(queued) - len(seen),
    }


def main():
    queue = load_queue()
    if int(queue.get("buffer_cooldown_until", 0)) > int(time.time()):
        print("X DELIVERY AUDIT: rate-limit cooldown; no Buffer API calls")
        return 0
    if not any(p.get("status") == "queued" and p.get("buffer_post_id")
               for p in queue.get("items", [])):
        print("X DELIVERY AUDIT: no accepted Buffer posts to inspect")
        return 0
    cached = queue.get("buffer_channel_cache")
    if not (isinstance(cached, dict) and cached.get("org_id") and cached.get("id") and
            isinstance(cached.get("verified_at"), int) and
            0 <= time.time() - cached["verified_at"] < 86400):
        channels = [p for p in connected_x_channel()
                    if p.get("name", "").lstrip("@").lower() == "iipike_ai" and
                    p.get("service") in ("twitter", "x") and not p.get("isQueuePaused")]
        if len(channels) != 1:
            raise RuntimeError("No unique active Buffer X channel")
        cached = channels[0]
    posts, more = recent_remote_posts(cached["org_id"], cached["id"])
    result = inspect(queue, posts)
    print("X DELIVERY AUDIT: " + json.dumps({**result, "truncated": more}))
    if result["buffer_errors"]:
        print("X DELIVERY ERROR: Buffer reports rejected post IDs " +
              ", ".join(result["buffer_errors"]), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BufferRateLimitError as err:
        print("X DELIVERY AUDIT LIMITED: " + str(err), file=sys.stderr)
        sys.exit(3)
    except RuntimeError as err:
        print("X DELIVERY AUDIT UNKNOWN: " + str(err), file=sys.stderr)
        sys.exit(4)
