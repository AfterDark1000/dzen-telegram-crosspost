#!/usr/bin/env python3
"""Check VK document wall upload endpoint; do not upload or post."""
import json
import os
import urllib.parse
import urllib.request

token = os.environ.get("VK_ACCESS_TOKEN")
if not token:
    raise SystemExit("Missing VK_ACCESS_TOKEN secret")
data = urllib.parse.urlencode({"group_id":"242308928","access_token":token,"v":"5.199"}).encode()
request = urllib.request.Request("https://api.vk.com/method/docs.getWallUploadServer", data=data)
with urllib.request.urlopen(request,timeout=30) as response:
    result=json.load(response)
if "error" in result:
    e=result["error"]
    print("VK API error:",e.get("error_code"),e.get("error_msg"))
    raise SystemExit(1)
print("VK document upload endpoint accepted:",bool(result.get("response",{}).get("upload_url")))
print("No URLs or tokens shown. No file uploaded. No VK post created.")
if not result.get("response",{}).get("upload_url"):
    raise SystemExit(1)
