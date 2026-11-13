"""Prepare Dzen full-text articles for VK without leaking embedded card chrome.

Dzen converts some inline links into a rich recommendation widget:
  <article title>
  Хроники Пикирующего ИИ
  3 дня назад
Playwright's articleBody.innerText flattens this widget into meaningless
plain text. VK does not support the Dzen widget, so render a single verified
text link or remove its decorative text if no trustworthy target is known.
Never infer destinations from titles without the verified Dzen article catalog.
"""
import json
from pathlib import Path
import re

from site_utm import normalize_dzen_url, tagged_site_url

AUTHOR = "Хроники Пикирующего ИИ"
AGE = re.compile(
    r"(?:\d+\s+(?:секунд\w*|минут\w*|час\w*|дн\w*|недел\w*|месяц\w*|месяцев|год\w*|лет)\s+назад|"
    r"сегодня|вчера|позавчера|только что)", re.IGNORECASE,
)


def normalized_title(value):
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def known_related_links(site_catalog, path=Path("editorial_full_catalog.json")):
    """Use exact verified Dzen identities and prefer the matching site article.

    Never guess a URL by similar titles. Unavailable local catalog simply
    disables link enrichment; widget chrome still gets stripped.
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return {}
    index = {}
    ambiguous = set()
    for item in entries:
        if not isinstance(item, dict) or item.get("status") != "published_observed_in_feed":
            continue
        title = normalized_title(item.get("title"))
        dzen = normalize_dzen_url(item.get("dzen_url"))
        if not title or not dzen:
            continue
        target = tagged_site_url({"url": dzen}, site_catalog, "vk") or dzen
        if title in index and index[title] != target:
            ambiguous.add(title)
        else:
            index[title] = target
    return {title: link for title, link in index.items() if title not in ambiguous}


def clean_dzen_embeds(text, related_links=None, *, dom_links=()):
    """Strip a Dzen embedded-article card, preserving a verified text link.

    This is narrowly scoped to our channel and the exact title/author/age
    signature. It must not remove ordinary editorial text or inline links.
    """
    normalized = re.sub(r"\r\n?", "\n", text or "")
    lines = normalized.split("\n")
    links = dict(related_links or {})
    for item in dom_links or ():
        if not isinstance(item, dict):
            continue
        dzen = normalize_dzen_url(item.get("url"))
        title = normalized_title(item.get("title"))
        if dzen and title and title not in links:
            links[title] = dzen
    result = []
    i = 0
    removed = 0
    while i < len(lines):
        if (i + 2 < len(lines)
                and normalized_title(lines[i])
                and len(lines[i].strip()) >= 12
                and lines[i + 1].strip() == AUTHOR
                and AGE.fullmatch(lines[i + 2].strip())):
            title = lines[i].strip()
            url = links.get(normalized_title(title))
            if url:
                result.extend(["Читайте также: " + title, url])
            removed += 1
            i += 3
            continue
        result.append(lines[i])
        i += 1
    body = re.sub(r"\n(?:[ \t]*\n){2,}", "\n\n", "\n".join(result)).strip()
    return body, removed
