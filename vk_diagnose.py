#!/usr/bin/env python3
"""Inspect publicly available Dzen article structure and VK wall photo upload permissions.
Never creates VK posts or uploads photos; prints only structural diagnostics.
"""
import asyncio
import json
import os
import urllib.parse
import urllib.request
from playwright.async_api import async_playwright
from crosspost import fetch_articles

async def main():
    items = await fetch_articles()
    item = items[0]
    print("Dzen sample title:", item["title"])
    print("Feed teaser length:", len(item["text"]))
    print("Cover URL available:", bool(item["image"]))
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(locale="ru-RU")
        try:
            await page.goto(item["url"], wait_until="commit", timeout=25000)
            for sec in range(20):
                await page.wait_for_timeout(1000)
                if sec in (4, 10, 19):
                    try:
                        details = await page.evaluate("""() => {
                          let sels=['article','[itemprop="articleBody"]','main','.article__content','.article__body','.article-render','.article-render__block','[data-testid*="article"]'];
                          let x={};
                          for (let s of sels) x[s]=[...document.querySelectorAll(s)].map(e=>({len:(e.innerText||'').length, p:e.querySelectorAll('p').length,head:(e.innerText||'').slice(0,140)})).slice(0,5);
                          x.bodyLength=(document.body?.innerText||'').length;
                          x.pCount=document.querySelectorAll('p').length;
                          x.title=document.title;
                          return x;
                        }""")
                        print("Article DOM after",sec+1,"seconds:",json.dumps(details,ensure_ascii=False))
                    except Exception as e:
                        print("Page still navigating:",type(e).__name__)
        finally:
            await browser.close()

    token = os.getenv("VK_ACCESS_TOKEN")
    if not token:
        print("Missing VK secret, skipping permission probe")
        return
    params={"group_id":"242308928","access_token":token,"v":"5.199"}
    req=urllib.request.Request("https://api.vk.com/method/photos.getWallUploadServer",
        data=urllib.parse.urlencode(params).encode())
    with urllib.request.urlopen(req,timeout=25) as r:
        result=json.load(r)
    if "error" in result:
        e=result["error"]
        print("VK photo upload permission error:",e.get("error_code"),e.get("error_msg"))
    else:
        print("VK wall photo upload server authorized:",bool(result.get("response",{}).get("upload_url")))

asyncio.run(main())
