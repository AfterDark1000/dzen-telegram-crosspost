#!/usr/bin/env python3
"""Final diagnostic: inspect public Dzen redirect chain, no credentials."""
import html
import re
import urllib.error
import urllib.parse
import urllib.request

URLS = [
    "https://dzen.ru/iipike?tab=articles",
    "https://dzen.ru/iipike",
    "https://dzen.ru/iipike?tab=articles&country_code=ru",
    "https://dzen.ru/a/asxXuA4opiCVbhEe",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
}
class TrackRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.hops = []
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source = urllib.parse.urlsplit(req.full_url)
        dest = urllib.parse.urlsplit(newurl)
        self.hops.append((code, source.hostname, source.path, dest.hostname, dest.path))
        if len(self.hops) >= 6:
            raise RuntimeError("Too many redirects")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def inspect(url):
    path = urllib.parse.urlsplit(url).path
    print(f"\n=== REQUEST {path} ===")
    tracker = TrackRedirects()
    opener = urllib.request.build_opener(tracker)
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with opener.open(req, timeout=20) as response:
            data = response.read(1_000_001)
            final = urllib.parse.urlsplit(response.url)
            print("HTTP status:", response.status)
            print("Final location:", final.hostname, final.path)
            print("Response bytes:", len(data))
            text = data.decode("utf-8", "replace")
            normalized = html.unescape(text)
            print("Contains feedData:", "feedData" in normalized)
            print("Contains known title:", "Алиса — новое развитие" in normalized)
            print("Contains article link:", bool(re.search(r"dzen[.]ru/a/", normalized)))
            print("Contains /push:", "/push" in normalized)
            title = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
            print("HTML title:", html.unescape(title.group(1)).strip()[:100] if title else "(none)")
    except Exception as e:
        print("Request failed:", type(e).__name__, str(e)[:160])
    finally:
        if tracker.hops:
            for n, (code, src_host, src_path, dst_host, dst_path) in enumerate(tracker.hops, 1):
                print(f"Redirect {n}: HTTP {code} {src_host}{src_path} -> {dst_host}{dst_path}")
        else:
            print("No HTTP redirects recorded")

if __name__ == "__main__":
    for url in URLS:
        inspect(url)
