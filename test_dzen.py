#!/usr/bin/env python3
"""Diagnostic only: can a GitHub-hosted runner read public Dzen article metadata?"""
import gzip
import html
import json
import re
import sys
import urllib.error
import urllib.request

URL = "https://dzen.ru/iipike?tab=articles"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
}
EXPECTED = ("Алиса — новое развитие", "Claude отправил 20")
def main():
    try:
        with urllib.request.urlopen(urllib.request.Request(URL, headers=HEADERS), timeout=25) as resp:
            raw = resp.read(4_000_001)
            code = resp.status
            charset = resp.headers.get_content_charset() or "utf-8"
            print(f"HTTP status: {code}")
            print(f"Content type: {resp.headers.get('Content-Type', 'unknown')}")
            print(f"Downloaded: {len(raw)} bytes")
            if len(raw) > 4_000_000:
                print("ERROR: response over 4 MB limit")
                return 1
            body = raw.decode(charset, errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"ERROR fetching page: {type(exc).__name__}: {exc}")
        return 1

    # Different Dzen builds embed the payload either raw, HTML-escaped, or
    # inside a JSON-escaped JavaScript string.
    variants = [body, html.unescape(body)]
    variants += [v.replace('\\u0022', '"').replace('\\\"', '"').replace('\\u003c', '<') for v in variants]
    has_feed = any('feedData' in v for v in variants)
    hits = [phrase for phrase in EXPECTED if any(phrase in v for v in variants)]
    article_urls = sorted(set(re.findall(r'https?://dzen\\?.ru/a/[A-Za-z0-9_-]+', " ".join(variants))))
    print(f"feedData marker found: {has_feed}")
    print(f"Known article title markers: {len(hits)} / {len(EXPECTED)}")
    print(f"Article URL patterns found: {len(article_urls)}")
    for url in article_urls[:5]:
        print(f"  {url.replace(chr(92), '')}")
    if has_feed and (hits or article_urls):
        print("PASS: article data accessible to GitHub-hosted runner.")
        return 0
    print("FAIL: no usable article metadata found in initial response.")
    print("No cookies, credentials, or page contents are printed.")
    return 1

if __name__ == "__main__":
    sys.exit(main())
