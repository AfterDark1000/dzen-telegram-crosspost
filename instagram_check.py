#!/usr/bin/env python3
"""Read-only Instagram token check for @iipike.ai; never publishes or logs secrets."""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

token = os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip()
if not token:
    raise SystemExit("Missing INSTAGRAM_ACCESS_TOKEN GitHub Actions secret")

url = "https://graph.instagram.com/v24.0/me?" + urllib.parse.urlencode({
    "fields": "id,username,account_type"
})
request = urllib.request.Request(
    url,
    headers={"Authorization": "Bearer " + token, "Accept": "application/json"}
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
except urllib.error.HTTPError as exc:
    try:
        info = json.loads(exc.read().decode("utf-8"))
    except (ValueError, UnicodeError):
        info = {}
    err = info.get("error", {})
    print("Instagram API HTTP status:", exc.code)
    print("Instagram error code:", err.get("code", "unknown"))
    print("Instagram error type:", err.get("type", "unknown"))
    raise SystemExit(1) from None

username = result.get("username")
if username != "iipike.ai":
    print("Connected Instagram account mismatch. Expected @iipike.ai; check Meta permissions.")
    raise SystemExit(1)

print("Instagram API authorization: OK")
print("Username: @" + username)
print("Account ID:", result.get("id", "not returned"))
print("Account type:", result.get("account_type", "not returned"))
print("No content was published. Access token was not printed.")
