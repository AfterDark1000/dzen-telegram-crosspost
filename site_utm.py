"""Safe, optional iipike.ru links with per-channel Yandex.Metrika UTM tags.

No redirects, login or state changes. A social publisher uses an exact, verified
Dzen URL mapping from the site's public news.json; ambiguous matches are omitted.
"""
import json
import re
import sys
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

SITE_CATALOG = "https://iipike.ru/news.json"
CAMPAIGN = "hpii_articles"
SOURCES = frozenset({
    "telegram_max", "vk", "dzen", "threads", "x", "youtube", "instagram", "max"
})
ARTICLE = re.compile(r"articles/([a-z0-9]+(?:-[a-z0-9]+)*)/")
DZEN_PATH = re.compile(r"/a/[a-zA-Z0-9_-]+")


def normalize_dzen_url(url):
    if not isinstance(url, str):
        return None
    try:
        p = urlsplit(url)
        if (p.scheme != "https" or p.hostname != "dzen.ru" or p.username
                or p.password or p.port not in (None, 443)
                or not DZEN_PATH.fullmatch(p.path.rstrip("/"))):
            return None
        return "https://dzen.ru" + p.path.rstrip("/")
    except ValueError:
        return None


def load_site_catalog():
    """Read optional public source once per batch; never stop social posting."""
    try:
        req = Request(SITE_CATALOG, headers={"User-Agent": "HPII-Social-Attribution/1.0"})
        with urlopen(req, timeout=8) as response:
            if response.status != 200:
                raise ValueError("Unexpected site catalog response")
            data = json.load(response)
        if not isinstance(data, list) or len(data) > 5000:
            raise ValueError("Unexpected site catalog format")
        return data
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("SITE_UTM_UNAVAILABLE: original social posting continues; " +
              type(error).__name__, file=sys.stderr, flush=True)
        return []


def tagged_site_url(item, catalog, source):
    """Only attribute a precisely matched Dzen article to the same site story."""
    if source not in SOURCES:
        raise ValueError("Unknown social source")
    normalized = normalize_dzen_url(item.get("url")) if isinstance(item, dict) else None
    if not normalized or not isinstance(catalog, list):
        return None
    matches = []
    for entry in catalog:
        if not isinstance(entry, dict):
            continue
        url = entry.get("url")
        if not isinstance(url, str):
            continue
        m = ARTICLE.fullmatch(url)
        if not m or m.group(1) != entry.get("id"):
            continue
        links = (entry.get("dzen_url"), entry.get("site_first_dzen_url"))
        if normalized in (normalize_dzen_url(link) for link in links):
            matches.append(url)
    if len(matches) != 1:
        return None
    params = urlencode({
        "utm_source": source,
        "utm_medium": "social",
        "utm_campaign": CAMPAIGN,
        "utm_content": matches[0].removeprefix("articles/").rstrip("/"),
    })
    return "https://iipike.ru/" + matches[0] + "?" + params


def tagged_home_url(source):
    """Manual distribution links for platform profiles and Dzen articles."""
    if source not in SOURCES:
        raise ValueError("Unknown social source")
    return "https://iipike.ru/?" + urlencode({
        "utm_source": source,
        "utm_medium": "social",
        "utm_campaign": "hpii_profile",
    })
