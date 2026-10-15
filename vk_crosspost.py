#!/usr/bin/env python3
"""Send only new Dzen articles to the VK community. Separate delivery state."""
import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from crosspost import fetch_articles, short_teaser

STATE = Path("seen_vk_articles.json")
OWNER = -242308928

def send_vk(item):
    parts = [item["title"].strip()]
    snippet = short_teaser(item.get("text", ""))
    if snippet:
        parts.append(snippet)
    parts.append("Читать полностью: " + item["url"])
    params = {
        "owner_id": str(OWNER),
        "from_group": "1",
        "message": "\n\n".join(parts),
        "access_token": os.environ["VK_ACCESS_TOKEN"],
        "v": "5.199",
    }
    req = urllib.request.Request("https://api.vk.com/method/wall.post",
                                 data=urllib.parse.urlencode(params).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            reply = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError("VK HTTP error: " + str(error.code)) from error
    if "error" in reply:
        info = reply["error"]
        raise RuntimeError("VK API error " + str(info.get("error_code")) +
                           ": " + str(info.get("error_msg", "unknown")))
    post_id = reply.get("response", {}).get("post_id")
    if not post_id:
        raise RuntimeError("VK did not confirm the post")
    print("VK posted:", item["title"], "post_id:", post_id, flush=True)

async def main():
    if not STATE.exists():
        raise RuntimeError("VK state is missing: refusing to backfill posts")
    seen = set(json.loads(STATE.read_text(encoding="utf-8")))
    articles = await fetch_articles()
    new = sorted((item for item in articles if item["id"] not in seen),
                 key=lambda item: (item["date"], item["id"]))
    print("VK new articles:", len(new), flush=True)
    for item in new:
        send_vk(item)
        seen.add(item["id"])
        STATE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    asyncio.run(main())
