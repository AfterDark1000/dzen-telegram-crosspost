"""Offline safety checks for append-only HPII linking catalog."""
import copy
import datetime as dt
import unittest

import editorial_full_catalog as catalog


SCION_ID = "6ad08e827267ec5b95c8c6ca"
SCION_URL = "https://dzen.ru/a/atCOgnJn7FuVyMbK"
NEW_ID = "6ae9717a7cc62d6eeab61a66"
NEW_URL = "https://dzen.ru/a/aulxenzGLW7qthpm"
DIARY_ID = "6ae2f63c8b88aa0944624805"
DIARY_URL = "https://dzen.ru/a/auL2PIuIqglEYkgF"
NOW = dt.datetime(2026, 11, 3, 11, 0, tzinfo=dt.timezone.utc)


def base():
    return {
        "schema_version": 1,
        "generated_date": "2026-11-03",
        "purpose": "Dzen linking",
        "stats": {"total_cataloged_editorial_publications": 1, "missing_titles": 0,
                  "excluded_post_count": 1},
        "entries": [{
            "dzen_id": SCION_ID,
            "title": "ИИ пишет столько кода, что Anthropic пришлось отказаться от Git",
            "dzen_url": SCION_URL,
            "status": "published_observed_in_feed",
            "source": "github_actions_post_logs + seen_articles.json",
            "title_evidence": "workflow_log_matched_commit",
            "url_evidence": "derived_from_12_byte_id",
        }],
    }


def row(article_id, url, title, status="published", text=""):
    return {"dzen_id": article_id, "dzen_url": url, "title": title,
            "status": status, "excerpt": text,
            "published_at_utc": "2026-11-03T10:04:13+00:00"}


class FullCatalogTests(unittest.TestCase):
    def test_add_article_skip_cover_and_diary_preserve_old_history(self):
        original = base()
        archive = {"entries": [
            row(NEW_ID, NEW_URL, "Новая опубликованная статья", text="Первый абзац"),
            row(DIARY_ID, DIARY_URL, "Дневник канала"),
            row("dzen-future", "https://dzen.ru/a/future", "Запланировано",
                status="cover_ready"),
            row(NEW_ID, NEW_URL, "Новая обложка", status="cover_ready"),
        ]}
        result, added = catalog.sync_catalog(original, archive, NOW)
        self.assertEqual(added, 1)
        self.assertEqual(len(original["entries"]), 1, "Do not mutate the original")
        self.assertEqual(len(result["entries"]), 2)
        self.assertEqual(result["stats"]["total_cataloged_editorial_publications"], 2)
        self.assertEqual(result["stats"]["excluded_post_count"], 1)
        self.assertNotIn(DIARY_ID, {r["dzen_id"] for r in result["entries"]})
        self.assertIn("Первый абзац", str(result["entries"]))
        self.assertEqual(result["generated_date"], "2026-11-03")
        catalog.validate_catalog(result)
        second, added_second = catalog.sync_catalog(result, archive, NOW)
        self.assertEqual(added_second, 0)
        self.assertEqual(second, result)

    def test_preserve_log_title_enrich_metadata_without_rewriting(self):
        original = base()
        archive = {"entries": [row(
            SCION_ID, SCION_URL, "Anthropic: revised article headline",
            text="Scion is a new system."
        )]}
        updated, added = catalog.sync_catalog(original, archive, NOW)
        self.assertEqual(added, 0)
        a = updated["entries"][0]
        self.assertEqual(a["title"], original["entries"][0]["title"])
        self.assertEqual(a["title_evidence"], "workflow_log_matched_commit")
        self.assertEqual(a["source"], "github_actions_post_logs + seen_articles.json")
        self.assertEqual(a["excerpt"], "Scion is a new system.")
        self.assertEqual(a["published_at_utc"], "2026-11-03T10:04:13+00:00")

    def test_untitled_history_may_gain_verified_title(self):
        original = base()
        original["entries"][0]["title"] = None
        original["entries"][0]["title_evidence"] = "unavailable"
        a, added = catalog.sync_catalog(
            original, {"entries": [row(SCION_ID, SCION_URL, "Scion: verified")]}, NOW)
        self.assertEqual(added, 0)
        self.assertEqual(a["entries"][0]["title"], "Scion: verified")
        self.assertEqual(a["entries"][0]["title_evidence"], "recorded")
        self.assertEqual(a["entries"][0]["source"], "editorial_archive.json")
        self.assertEqual(a["stats"]["missing_titles"], 0)

    def test_catalog_cannot_lose_history_or_accept_forged_urls(self):
        for article_id, url in (
            (SCION_ID, "http://dzen.ru/a/atCOgnJn7FuVyMbK"),
            (SCION_ID, "https://dzen.ru.evil.com/a/atCOgnJn7FuVyMbK"),
            (SCION_ID, NEW_URL),
            (DIARY_ID, DIARY_URL),
        ):
            invalid = base()
            invalid["entries"][0].update(dzen_id=article_id, dzen_url=url)
            with self.assertRaisesRegex(ValueError, "Invalid or excluded"):
                catalog.validate_catalog(invalid)
        repeated = base()
        repeated["entries"].append(copy.deepcopy(repeated["entries"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            catalog.validate_catalog(repeated)

    def test_invalid_archive_never_replaces_catalog(self):
        with self.assertRaisesRegex(ValueError, "Invalid published"):
            catalog.sync_catalog(base(), {"entries": "broken"}, NOW)
        with self.assertRaisesRegex(ValueError, "Invalid full catalog"):
            catalog.sync_catalog({"entries": "broken"}, {"entries": []}, NOW)

    def test_real_catalog_has_no_diary_or_duplicates(self):
        import json
        existing = json.loads(catalog.CATALOG.read_text(encoding="utf-8"))
        catalog.validate_catalog(existing)
        self.assertFalse(any(row["dzen_id"] == DIARY_ID for row in existing["entries"]))


if __name__ == "__main__":
    unittest.main()
