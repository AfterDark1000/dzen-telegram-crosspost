#!/usr/bin/env python3
"""Reliable Telegram photo upload when Telegram cannot fetch a Dzen image URL.

The GitHub runner downloads the original cover and sends its image bytes to
Telegram using multipart/form-data, avoiding Telegram's remote-URL fetch limits.
"""
import io
import json
import uuid
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024
MAX_TELEGRAM_BYTES = 9 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 30_000_000


def download_photo(url):
    if not url.startswith("https://"):
        raise ValueError("Dzen photo URL must use HTTPS")
    request = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "image/jpeg,image/png,image/webp,image/*;q=0.8",
    })
    with urllib.request.urlopen(request, timeout=45) as response:
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if not data or len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError("Dzen photo is empty or unexpectedly large")
    with Image.open(io.BytesIO(data)) as image:
        img = ImageOps.exif_transpose(image)
        img = img.convert("RGB")
        if img.width < 100 or img.height < 100:
            raise ValueError("Dzen image dimensions too small")
        img.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        for quality in (90, 83, 72):
            out.seek(0)
            out.truncate(0)
            img.save(out, format="JPEG", quality=quality, optimize=True)
            if out.tell() < MAX_TELEGRAM_BYTES:
                return out.getvalue()
    raise ValueError("JPEG remains too large for Telegram")


def send_photo_upload(token, chat_id, caption, url):
    photo = download_photo(url)
    boundary = "tgphoto-" + uuid.uuid4().hex
    parts = []
    for name, value in (("chat_id", chat_id), ("caption", caption), ("parse_mode", "HTML")):
        parts.append(("--" + boundary + "\r\n" +
                      'Content-Disposition: form-data; name="' + name + '"\r\n\r\n' +
                      str(value) + "\r\n").encode("utf-8"))
    parts.append(("--" + boundary + "\r\n" +
                  'Content-Disposition: form-data; name="photo"; filename="cover.jpg"\r\n' +
                  "Content-Type: image/jpeg\r\n\r\n").encode("utf-8"))
    parts.append(photo)
    parts.append(("\r\n--" + boundary + "--\r\n").encode("utf-8"))

    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendPhoto",
        data=b"".join(parts),
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=65) as response:
            answer = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            description = json.loads(exc.read().decode("utf-8")).get("description", "")
        except (ValueError, UnicodeError):
            description = ""
        raise RuntimeError(f"Telegram multipart upload HTTP {exc.code}: {description[:250]}") from None
    if not answer.get("ok"):
        raise RuntimeError("Telegram did not confirm multipart photo upload")
    return answer
