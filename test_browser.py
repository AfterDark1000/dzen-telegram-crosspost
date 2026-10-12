#!/usr/bin/env python3
"""Read public Dzen channel in headless Chromium; no account or cookies."""
import asyncio
import re
from urllib.parse import urlsplit
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(locale="ru-RU", timezone_id="Europe/Moscow")
        responses = []
        def on_response(r):
            u = urlsplit(r.url)
            if u.hostname and (u.hostname.endswith("dzen.ru") or u.hostname.endswith("yandex.ru")):
                if len(responses) < 30:
                    responses.append((r.status, u.hostname, u.path))
        page.on("response", on_response)
        try:
            r = await page.goto("https://dzen.ru/iipike?tab=articles", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(7000)
            content = await page.content()
            visible = await page.locator("body").inner_text(timeout=8000)
            final = urlsplit(page.url)
            print("Initial HTTP:", r.status if r else "none")
            print("Final page:", final.hostname, final.path)
            print("HTML bytes:", len(content.encode("utf-8")))
            print("Has feedData:", "feedData" in content)
            print("Has article title:", "Алиса — новое развитие" in content or "Алиса — новое развитие" in visible)
            print("Has article links:", bool(re.search(r'dzen[.]ru/a/|href="/a/', content)))
            print("Page title:", (await page.title())[:100])
            print("First response paths:", responses)
            if final.hostname == "dzen.ru" and ("feedData" in content or "Алиса — новое развитие" in visible or re.search(r'href="/a/', content)):
                print("PASS: public articles visible in headless Chromium")
                return 0
            print("FAIL: articles not accessible in headless Chromium")
            return 1
        finally:
            await browser.close()

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
