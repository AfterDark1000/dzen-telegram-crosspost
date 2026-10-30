#!/usr/bin/env python3
"""Read-only Buffer API connectivity probe. Never creates posts or queues content."""
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.buffer.com"

def graphql(query, variables=None):
    token = os.getenv("BUFFER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("BUFFER_API_KEY missing in GitHub Actions secrets")
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(API, data=payload, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + token,
    }, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Buffer authentication/read failed: HTTP " + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("Buffer API network request failed") from None
    if not isinstance(data, dict) or data.get("errors"):
        errors = data.get("errors", []) if isinstance(data, dict) else []
        messages = [str(e.get("message", ""))[:150] for e in errors if isinstance(e, dict)]
        raise RuntimeError("Buffer GraphQL read error: " + "; ".join(messages[:2]))
    return data.get("data") or {}

def connected_x_channel():
    organizations = graphql("query { account { organizations { id name } } }")
    orgs = ((organizations.get("account") or {}).get("organizations") or [])
    if not orgs:
        raise RuntimeError("Buffer key works but there are no organizations")
    x = []
    for org in orgs:
        org_id = org.get("id")
        if not isinstance(org_id, str) or not org_id:
            continue
        data = graphql(
            "query ($org: String!) { channels(input: {organizationId: $org}) { id name displayName service isQueuePaused } }",
            {"org": org_id},
        )
        for item in data.get("channels") or []:
            if item.get("service") in ("twitter", "x"):
                x.append({"org_id": org_id, **item})
    print("Buffer API read-only authenticated; connected X channels:", len(x))
    for item in x:
        print("X channel:", json.dumps({"id": item.get("id"), "name": item.get("name"),
                                        "displayName": item.get("displayName"),
                                        "service": item.get("service"),
                                        "queuePaused": item.get("isQueuePaused")},
                                       ensure_ascii=False))
    if not x:
        raise RuntimeError("X account not connected to this Buffer organization")
    return x

if __name__ == "__main__":
    try:
        connected_x_channel()
    except RuntimeError as exc:
        print("BUFFER X CHECK ERROR:", exc, file=sys.stderr)
        sys.exit(1)
