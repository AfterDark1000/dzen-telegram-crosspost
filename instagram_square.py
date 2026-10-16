#!/usr/bin/env python3
"""Prepare one Instagram-safe square cover from the latest public Dzen cover.

Keeps the source artwork intact. Text/graphics remain inside Instagram's narrower
portrait profile-grid crop; the rest is a blurred extension of the same cover.
Does not publish anything to Instagram.
"""
import asyncio
import io
import os
import re
import urllib.request
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from crosspost import fetch_articles

CANVAS = 1080
# A 3:4 Instagram profile-grid preview can show only ~810px of a square.
# Keep the original landscape artwork fully within that central region.
SAFE_WIDTH = 790
DESTINATION = Path("instagram_covers")


def download_cover(url):
    if not url.startswith("https://"):
        raise RuntimeError("Cover URL must be HTTPS")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as response:
        if response.status != 200:
            raise RuntimeError("Could not download source cover")
        data = response.read(15 * 1024 * 1024 + 1)
    if not (1000 < len(data) <= 15 * 1024 * 1024):
        raise RuntimeError("Unexpected Dzen cover size")
    return data


def compose(data):
    with Image.open(io.BytesIO(data)) as source:
        source = ImageOps.exif_transpose(source).convert("RGB")

    if min(source.size) < 300:
        raise RuntimeError("Dzen cover too small")
    background = ImageOps.fit(source, (CANVAS, CANVAS), method=Image.Resampling.LANCZOS)
    background = background.filter(ImageFilter.GaussianBlur(radius=45))
    background = ImageEnhance.Brightness(background).enhance(0.37)

    original = ImageOps.contain(source, (SAFE_WIDTH, CANVAS - 100),
                                method=Image.Resampling.LANCZOS)
    # A subtle dark shadow gives the readable source thumbnail a clear edge.
    x = (CANVAS - original.width) // 2
    y = (CANVAS - original.height) // 2
    shadow = Image.new("RGBA", (original.width + 26, original.height + 26), (0, 0, 0, 0))
    shadow.paste((0, 0, 0, 210), (13, 13, 13 + original.width, 13 + original.height))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=16))
    background.paste(shadow.convert("RGB"), (x - 13, y - 8))
    background.paste(original, (x, y))
    return background


async def main():
    items = await fetch_articles()
    if not items:
        raise RuntimeError("No Dzen articles in the channel")
    item = max(items, key=lambda x: (x["date"], x["id"]))
    image = item.get("image", "")
    if not image:
        raise RuntimeError("Latest article has no cover")
    ident = re.sub(r"[^a-zA-Z0-9_-]", "", item["id"])
    if not ident:
        raise RuntimeError("Missing safe article ID")
    DESTINATION.mkdir(exist_ok=True)
    path = DESTINATION / (ident + ".jpg")
    picture = compose(download_cover(image))
    picture.save(path, "JPEG", quality=91, subsampling=0, optimize=True)
    print("Article:", item["title"], flush=True)
    print("Cover prepared:", path, "size:", picture.size, flush=True)
    print("Original cover preserved, center-safe for profile grid.", flush=True)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write("cover_path=" + path.as_posix() + "\n")


if __name__ == "__main__":
    asyncio.run(main())
