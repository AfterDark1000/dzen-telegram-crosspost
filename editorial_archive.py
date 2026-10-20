#!/usr/bin/env python3
"""One human- and machine-readable HPI editorial archive.

Only public Dzen feed items are called "published".
An Instagram cover without a Dzen item means "cover_ready", NOT "scheduled".
Neither the old seen-ID list nor a cover proves what its article said.
The archive grows monotonically: disappearing from a feed never deletes history.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
from state_checkpoint import atomic_json
import re
import sys
import unicodedata
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "editorial_archive.json"
DZEN_SNAPSHOT = ROOT / "_current_dzen_snapshot.json"
INBOX = ROOT / "instagram_cover_inbox.json"
SEEN = ROOT / "seen_articles.json"
UTC = dt.timezone.utc


def normalized_title(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def is_public_dzen_link(value):
    if not isinstance(value, str):
        return False
    p = urlparse(value)
    return p.scheme == "https" and p.hostname == "dzen.ru" and p.path.startswith("/a/") and not p.username


def create_archive(now=None):
    now = now or dt.datetime.now(UTC)
    prior = read_json(ARCHIVE, {"schema_version": 1, "entries": []})
    if not isinstance(prior, dict) or not isinstance(prior.get("entries"), list):
        raise ValueError("Invalid existing editorial archive; refusing to overwrite it")

    old = {}
    for item in prior["entries"]:
        if isinstance(item, dict) and item.get("key") and isinstance(item.get("title"), str):
            old[item["key"]] = item.copy()

    snapshot = read_json(DZEN_SNAPSHOT, [])
    if not isinstance(snapshot, list):
        raise ValueError("Invalid Dzen snapshot")
    seen = read_json(SEEN, [])
    if not isinstance(seen, list):
        raise ValueError("Invalid seen IDs")
    index = read_json(INBOX, {"covers": {}})
    covers = index.get("covers", {})
    if not isinstance(covers, dict):
        raise ValueError("Invalid Instagram cover index")

    # Never invent title/URL from a raw seen_articles.json ID.
    for item in snapshot:
        if not isinstance(item, dict) or not is_public_dzen_link(item.get("url")):
            continue
        title = str(item.get("title") or "").strip()
        ident = str(item.get("id") or "").strip()
        if not title or not ident or len(title) > 350 or len(ident) > 150:
            continue
        key = "dzen:" + ident
        existing = old.get(key, {})
        published = ""
        try:
            when = int(item.get("date") or 0)
            if 1600000000 <= when <= int(now.timestamp()) + 86400:
                published = dt.datetime.fromtimestamp(when, UTC).isoformat(timespec="seconds")
        except (OverflowError, TypeError, ValueError, OSError):
            pass
        old[key] = {
            **existing, "key": key, "dzen_id": ident, "title": title,
            "title_normalized": normalized_title(title),
            "status": "published", "dzen_url": item["url"],
            "published_at_utc": published or existing.get("published_at_utc", ""),
            "first_recorded_at_utc": existing.get("first_recorded_at_utc") or now.isoformat(timespec="seconds"),
            "excerpt": re.sub(r"\s+", " ", str(item.get("text") or ""))[:420],
        }

    # Match by headline only; covers are merely signs of preparation.
    published_titles = {x.get("title_normalized") for x in old.values()
                        if x.get("status") == "published"}
    for value in covers.values():
        if not isinstance(value, dict):
            continue
        title = str(value.get("title") or "").strip()
        if not title or len(title) > 350:
            continue
        key_normal = normalized_title(title)
        if not key_normal:
            continue
        if key_normal in published_titles:
            for entry in old.values():
                if entry.get("status") == "published" and entry.get("title_normalized") == key_normal:
                    entry["vertical_cover_prepared"] = True
            continue
        cover_key = "cover:" + hashlib.sha256(key_normal.encode("utf-8")).hexdigest()[:20]
        existing = old.get(cover_key, {})
        old[cover_key] = {
            **existing, "key": cover_key, "title": title,
            "title_normalized": key_normal, "status": "cover_ready",
            "first_recorded_at_utc": existing.get("first_recorded_at_utc") or now.isoformat(timespec="seconds"),
        }
    # Remove cover-only entry if exactly that title later appears in Dzen.
    for key, entry in list(old.items()):
        if (entry.get("status") == "cover_ready" and
                entry.get("title_normalized") in published_titles):
            old.pop(key)

    entries = sorted(old.values(), key=lambda x: (
        x.get("published_at_utc") or x.get("first_recorded_at_utc") or "", x.get("key", "")
    ), reverse=True)
    total = len({str(x) for x in seen if x})
    return {
        "schema_version": 1,
        "updated_at_utc": now.isoformat(timespec="seconds"),
        "scope": "public Dzen feed + prepared Instagram covers; not private Dzen schedule",
        "legacy_seen_ids_count": total,
        "entries": entries,
    }


def main():
    result = create_archive()
    current = read_json(ARCHIVE, {})
    # Avoid committing an unchanged catalog on every hourly run.
    if isinstance(current, dict):
        cmp = dict(current)
        cmp["updated_at_utc"] = result["updated_at_utc"]
        if cmp == result:
            print("Editorial archive unchanged.", flush=True)
            return
    atomic_json(ARCHIVE, result)
    n_published = sum(row["status"] == "published" for row in result["entries"])
    n_cover = sum(row["status"] == "cover_ready" for row in result["entries"])
    print(f"Editorial archive: {n_published} public publications; "
          f"{n_cover} covers without matched Dzen publication; "
          f"{result['legacy_seen_ids_count']} historic seen IDs.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
        print("EDITORIAL ARCHIVE ERROR:", str(exc)[:250], file=sys.stderr)
        sys.exit(1)
