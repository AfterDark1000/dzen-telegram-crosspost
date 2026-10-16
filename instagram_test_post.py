#!/usr/bin/env python3
"""One-off Instagram publication test. Does not affect Dzen/TG/VK workflows."""
import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from crosspost import fetch_articles, short_teaser

API = "https://graph.instagram.com/v24.0"
TOKEN = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "").strip()
PUBLISH = os.environ.get("IG_TEST_PUBLISH", "false").lower() == "true"


def graph(method, path, values=None):
    values = values or {}
    if method == "GET":
        url = API + "/" + path + "?" + urllib.parse.urlencode(values)
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + TOKEN})
    else:
        request = urllib.request.Request(
            API + "/" + path,
            data=urllib.parse.urlencode(values).encode("utf-8"),
            headers={"Authorization": "Bearer " + TOKEN},
        )
    try:
        with urllib.request.urlopen(request, timeout=50) as response:
            result = json.load(response)
    except urllib.error.HTTPError as err:
        try:
            payload = json.loads(err.read().decode("utf-8"))
            info = payload.get("error", {})
        except (ValueError, UnicodeError):
            info = {}
        print("Instagram API HTTP:", err.code)
        print("Instagram API error:", info.get("code", "unknown"), info.get("message", "No further details"))
        raise SystemExit(1) from None
    if "error" in result:
        err = result["error"]
        raise RuntimeError("Instagram API: " + str(err.get("code")) + " " + str(err.get("message")))
    return result


async def main():
    if not TOKEN:
        raise SystemExit("Missing INSTAGRAM_ACCESS_TOKEN secret")
    account = graph("GET", "me", {"fields": "id,user_id,username,account_type"})
    if account.get("username") != "iipike.ai":
        raise SystemExit("Token belongs to a different Instagram account; aborting")
    user_id = account.get("user_id") or account.get("id")
    if not user_id:
        raise SystemExit("Could not resolve Instagram account ID")
    print("Connected Instagram: @" + account["username"], flush=True)
    print("Publishing account ID:", user_id, flush=True)

    items = await fetch_articles()
    if not items:
        raise SystemExit("No Dzen article found for the test")
    item = max(items, key=lambda x: (x["date"], x["id"]))
    if not item.get("image", "").startswith("https://"):
        raise SystemExit("Article has no publicly accessible HTTPS cover")
    caption = item["title"].strip()
    teaser = short_teaser(item.get("text"), limit=550)
    if teaser:
        caption += "\n\n" + teaser
    caption += "\n\n#ИИ #ИскусственныйИнтеллект"
    if len(caption) > 2200:
        raise SystemExit("Caption is too long")
    print("Test article:", item["title"], flush=True)
    print("Cover host:", urllib.parse.urlparse(item["image"]).netloc, flush=True)
    print("Test caption length:", len(caption), flush=True)

    public_image_url = os.environ.get("IG_IMAGE_URL_OVERRIDE", "").strip() or item["image"]
    if not public_image_url.startswith("https://"):
        raise SystemExit("Instagram image URL must be publicly reachable over HTTPS")
    if os.environ.get("IG_IMAGE_URL_OVERRIDE"):
        print("Using generated square JPEG from GitHub.", flush=True)

    container = graph("POST", str(user_id) + "/media", {
        "image_url": public_image_url,
        "caption": caption,
    })
    cid = container.get("id")
    if not cid:
        raise SystemExit("Instagram did not create an image container")
    print("Instagram image container created.", flush=True)
    status = ""
    for n in range(12):
        time.sleep(5)
        result = graph("GET", str(cid), {"fields": "status_code"})
        status = result.get("status_code", "")
        print("Processing status:", status, flush=True)
        if status in ("FINISHED", "ERROR", "EXPIRED"):
            break
    if status != "FINISHED":
        raise SystemExit("Instagram image was not ready: " + str(status))
    print("Instagram image accepted and ready.", flush=True)
    if not PUBLISH:
        print("PREVIEW MODE: no Instagram post was published.", flush=True)
        print("To publish one post, run this workflow with 'Publish one test post' checked.", flush=True)
        return

    media = graph("POST", str(user_id) + "/media_publish", {"creation_id": cid})
    post_id = media.get("id")
    if not post_id:
        raise SystemExit("No confirmation from Instagram media_publish")
    print("Instagram test post successfully published. ID:", post_id, flush=True)
    try:
        details = graph("GET", str(post_id), {"fields": "permalink"})
        if details.get("permalink"):
            print("Post link:", details["permalink"], flush=True)
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
