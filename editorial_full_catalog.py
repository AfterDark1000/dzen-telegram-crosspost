#!/usr/bin/env python3
"""Append verified Dzen articles to the persistent HPII internal-linking catalog.

This is NOT a feed scraper: editorial_archive.json already records published
article metadata. Preserve the historical backfill, titles and provenance.
Clips, posts, covers and the excluded Dzen-only diary never enter this catalog.
"""
import base64
import copy
import datetime as dt
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from state_checkpoint import atomic_json

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "editorial_full_catalog.json"
ARCHIVE = ROOT / "editorial_archive.json"
DIARY_ID = "6ae2f63c8b88aa0944624805"
DIARY_PATH = "/a/auL2PIuIqglEYkgF"
ID_PATTERN = re.compile(r"[0-9a-f]{24}\Z")
DYNAMIC_STATS = frozenset({
    "total_cataloged_editorial_publications",
    "fully_titled_from_archived_public_feed",
    "titled_from_historical_github_logs",
    "missing_titles",
})


def canonical_url(article_id, url):
    """Return a canonical URL only for an authentic matching article ID."""
    if not isinstance(article_id, str) or not ID_PATTERN.fullmatch(article_id):
        return None
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    slug = base64.urlsafe_b64encode(bytes.fromhex(article_id)).decode("ascii").rstrip("=")
    if (parsed.scheme != "https" or parsed.netloc != "dzen.ru"
            or parsed.path != "/a/" + slug
            or parsed.username is not None):
        return None
    if article_id == DIARY_ID or parsed.path == DIARY_PATH:
        return None
    return "https://dzen.ru/a/" + slug


def validate_catalog(catalog):
    if not isinstance(catalog, dict) or not isinstance(catalog.get("entries"), list):
        raise ValueError("Invalid full catalog; refusing to overwrite historical records")
    ids, urls = set(), set()
    for row in catalog["entries"]:
        if not isinstance(row, dict):
            raise ValueError("Non-object full catalog entry")
        article_id, url = row.get("dzen_id"), row.get("dzen_url")
        if not canonical_url(article_id, url):
            raise ValueError("Invalid or excluded full catalog link: " + str(article_id))
        if article_id in ids or url in urls:
            raise ValueError("Duplicate full catalog record: " + article_id)
        ids.add(article_id)
        urls.add(url)


def refresh_metadata(catalog):
    """Recompute dynamic counts while retaining the editor's dated baseline."""
    entries = catalog["entries"]
    stats = dict(catalog.get("stats") or {})
    stats.update({
        "total_cataloged_editorial_publications": len(entries),
        "fully_titled_from_archived_public_feed": sum(
            row.get("source") == "editorial_archive.json" and bool(row.get("title"))
            for row in entries
        ),
        "titled_from_historical_github_logs": sum(
            row.get("title_evidence") == "workflow_log_matched_commit"
            for row in entries
        ),
        "missing_titles": sum(not row.get("title") for row in entries),
    })
    catalog["stats"] = stats
    catalog["coverage"] = (
        f"{len(entries)} editorial articles cataloged; "
        f"{stats['missing_titles']} lack verified titles. "
        "Non-article posts, clips and the excluded Dzen-only diary are omitted. "
        "The editor-reported 215 channel publications are a 2026-11-03 baseline, "
        "not a live counter."
    )
    return catalog


def sync_catalog(catalog, archive, now=None):
    validate_catalog(catalog)
    if not isinstance(archive, dict) or not isinstance(archive.get("entries"), list):
        raise ValueError("Invalid published editorial archive")
    result = copy.deepcopy(catalog)
    by_id = {row["dzen_id"]: row for row in result["entries"]}
    changed = False
    added = 0

    for source in archive["entries"]:
        if not isinstance(source, dict) or source.get("status") != "published":
            continue
        article_id, url = source.get("dzen_id"), source.get("dzen_url")
        canonical = canonical_url(article_id, url)
        title = str(source.get("title") or "").strip()
        if canonical is None or not title or len(title) > 350:
            continue
        excerpt = re.sub(r"\s+", " ", str(source.get("excerpt") or "")).strip()[:420]
        published = str(source.get("published_at_utc") or "").strip()

        if article_id in by_id:
            row = by_id[article_id]
            if row["dzen_url"] != canonical:
                raise ValueError("Conflicting Dzen URL for " + article_id)
            # Enrich historical/untitled records, but never silently rewrite a
            # human-edited or previously verified headline.
            if not row.get("title"):
                row["title"] = title
                row["title_evidence"] = "recorded"
                row["source"] = "editorial_archive.json"
                changed = True
            if not row.get("excerpt") and excerpt:
                row["excerpt"] = excerpt
                changed = True
            if not row.get("published_at_utc") and published:
                row["published_at_utc"] = published
                changed = True
            continue

        row = {
            "dzen_id": article_id,
            "title": title,
            "dzen_url": canonical,
            "status": "published_observed_in_feed",
            "source": "editorial_archive.json",
            "title_evidence": "recorded",
            "url_evidence": "recorded_in_editorial_archive",
        }
        if published:
            row["published_at_utc"] = published
        if excerpt:
            row["excerpt"] = excerpt
        result["entries"].append(row)
        by_id[article_id] = row
        changed = True
        added += 1

    if not changed:
        return catalog, 0
    result["entries"].sort(key=lambda row: row["dzen_id"], reverse=True)
    result["generated_date"] = (now or dt.datetime.now(dt.timezone.utc)).date().isoformat()
    refresh_metadata(result)
    validate_catalog(result)
    return result, added


def main():
    if not CATALOG.is_file():
        raise ValueError("Full catalog file missing; refusing to create an empty replacement")
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    archive = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    updated, added = sync_catalog(catalog, archive)
    if updated == catalog:
        print("Full editorial catalog unchanged.", flush=True)
        return
    atomic_json(CATALOG, updated)
    print(f"Full editorial catalog updated: +{added} new articles, "
          f"{len(updated['entries'])} total; "
          f"{updated['stats']['missing_titles']} still without titles.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
        print("FULL CATALOG ERROR: " + str(exc)[:280], file=sys.stderr)
        sys.exit(1)
