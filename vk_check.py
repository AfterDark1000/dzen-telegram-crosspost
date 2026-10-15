#!/usr/bin/env python3
"""Check VK community token, without posting or revealing it."""
import json
import os
import urllib.parse
import urllib.request

token = os.environ.get("VK_ACCESS_TOKEN", "")
if not token:
    raise SystemExit("VK_ACCESS_TOKEN secret missing")
data = urllib.parse.urlencode({"access_token": token, "v": "5.199"}).encode()
request = urllib.request.Request("https://api.vk.com/method/groups.getTokenPermissions", data=data)
with urllib.request.urlopen(request, timeout=25) as response:
    result = json.load(response)
if "error" in result:
    error = result["error"]
    print("VK error code:", error.get("error_code"))
    print("VK error message:", error.get("error_msg"))
    raise SystemExit(1)
print("VK token accepted.")
print("Permissions:", result.get("response"))
print("No VK posts were created.")
