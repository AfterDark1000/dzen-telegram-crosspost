#!/usr/bin/env python3
"""Receive Instagram-only 4:5 art from a dedicated Telegram channel.

A channel photo/document caption must be the exact Dzen article headline.
This does not publish anything to Instagram. Secrets never enter Git commits.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageOps, UnidentifiedImageError

STATE = Path("instagram_cover_inbox.json")
COVERS_DIR = Path("instagram_covers")
CHANNEL_NAME = "ХПИИ — Обложки Instagram"
MAX_IMAGE_BYTES = 15_000_000
MAX_BATCH = 100


def normalize_title(title):
    """Exact words and punctuation, with harmless whitespace/dash normalization."""
    s = unicodedata.normalize("NFKC", title or "").casefold()
    s = re.sub(r"[‐‑‒–—−]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


def api(token, method, params=None):
    address = "https://api.telegram.org/bot" + token + "/" + method
    payload = urllib.parse.urlencode(params or {}).encode("utf-8")
    request = urllib.request.Request(address, data=payload, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=35) as response:
            result = json.load(response)
        if result.get("ok") is not True:
            raise RuntimeError("Telegram rejected " + method)
        return result["result"]
    except urllib.error.HTTPError as exc:
        # Do not print URL or response body: the request URL contains the token.
        raise RuntimeError("Telegram " + method + " HTTP " + str(exc.code)) from None
    except urllib.error.URLError:
        raise RuntimeError("Telegram connection failed while calling " + method) from None


def receive_bytes(token, telegram_path):
    if not telegram_path or telegram_path.startswith("/") or ".." in Path(telegram_path).parts:
        raise ValueError("Invalid Telegram file path")
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", telegram_path):
        raise ValueError("Unexpected Telegram file path")
    url = "https://api.telegram.org/file/bot" + token + "/" + telegram_path
    try:
        with urllib.request.urlopen(url, timeout=50) as response:
            raw = response.read(MAX_IMAGE_BYTES + 1)
    except (urllib.error.URLError, urllib.error.HTTPError):
        raise RuntimeError("Could not download Telegram image") from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Telegram image too large")
    return raw


def to_jpeg(raw):
    try:
        with Image.open(io.BytesIO(raw)) as src:
            src.load()
            image = ImageOps.exif_transpose(src).convert("RGB")
            width, height = image.size
            if abs(width / height - 0.8) > 0.02:
                raise ValueError("Wrong aspect ratio; use 1080 x 1350 (4:5)")
            if width < 600 or height < 750:
                raise ValueError("Image too small; use 1080 x 1350")
            image = image.resize((1080, 1350), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            image.save(output, "JPEG", quality=93, optimize=True)
            return output.getvalue()
    except (UnidentifiedImageError, OSError):
        raise ValueError("Telegram file is not a readable image") from None


def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Telegram token not configured")
    if not STATE.is_file():
        raise RuntimeError("Missing inbox state file: refusing to overwrite existing updates")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    if not isinstance(state.get("covers"), dict) or not isinstance(state.get("offset"), int):
        raise ValueError("Corrupt inbox state file")
    channel_id = state.get("channel_id")
    offset = state["offset"]
    total = 0
    for _ in range(6):
        updates = api(token, "getUpdates", {
            "offset": str(offset),
            "limit": str(MAX_BATCH),
            "timeout": "0",
            "allowed_updates": json.dumps(["channel_post", "edited_channel_post"])
        })
        if not updates:
            break
        for update in updates:
            uid = update["update_id"]
            message = update.get("channel_post") or update.get("edited_channel_post") or {}
            chat = message.get("chat") or {}
            match = (str(chat.get("id")) == str(channel_id)) if channel_id is not None else (
                normalize_title(chat.get("title", "")) == normalize_title(CHANNEL_NAME)
            )
            if match and chat.get("type") == "channel":
                if channel_id is None:
                    channel_id = chat["id"]
                    state["channel_id"] = channel_id
                    print("Instagram cover inbox channel confirmed.", flush=True)
                caption = (message.get("caption") or "").strip()
                photos = message.get("photo") or []
                document = message.get("document") or {}
                photo = max(photos, key=lambda p: p.get("file_size", 0)) if photos else {}
                if caption and (photo or str(document.get("mime_type", "")).startswith("image/")):
                    try:
                        if len(caption) > 210 or "\n" in caption:
                            raise ValueError("Caption must be only one Dzen headline, max 210 characters")
                        normalized = normalize_title(caption)
                        if len(normalized) < 12:
                            raise ValueError("Too short headline for safe matching")
                        file_id = (document if document.get("file_id") else photo).get("file_id")
                        details = api(token, "getFile", {"file_id": file_id})
                        path = details.get("file_path")
                        jpeg = to_jpeg(receive_bytes(token, path))
                        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
                        filename = f"{digest}-{message['message_id']}.jpg"
                        COVERS_DIR.mkdir(exist_ok=True)
                        (COVERS_DIR / filename).write_bytes(jpeg)
                        state["covers"][normalized] = {
                            "title": caption,
                            "file": "instagram_covers/" + filename,
                            "message_id": message["message_id"],
                        }
                        total += 1
                        print("Saved vertical cover for: " + caption[:110], flush=True)
                    except (RuntimeError, ValueError, KeyError) as error:
                        print("Cover rejected: " + str(error), flush=True)
            offset = max(offset, uid + 1)
            state["offset"] = offset
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
        if len(updates) < MAX_BATCH:
            break
    print(f"Inbox sync: {total} new covers; {len(state['covers'])} stored.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, KeyError) as error:
        print("COVER INBOX ERROR: " + str(error), file=sys.stderr)
        sys.exit(1)
