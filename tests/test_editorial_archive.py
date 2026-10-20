"""Offline non-destructive HPI archive tests, no Dzen connection needed."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import editorial_archive as archive

NOW = dt.datetime(2026, 10, 20, 11, 0, tzinfo=dt.timezone.utc)

class ArchiveTests(unittest.TestCase):
    def test_published_title_link_and_cover_merge(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            feed=[{"id":"dzen-123", "title":"Новая модель FLUX",
                   "url":"https://dzen.ru/a/6a1234", "date":1792480000,
                   "text":"Проверенный анонс новой модели."}]
            (root/"_current_dzen_snapshot.json").write_text(json.dumps(feed,ensure_ascii=False))
            (root/"seen_articles.json").write_text('["dzen-123","older-id"]')
            (root/"instagram_cover_inbox.json").write_text(json.dumps({"covers":{
                "title1":{"title":"Новая модель FLUX"},
                "title2":{"title":"Другая будущая обложка"}
            }},ensure_ascii=False))
            with patch.object(archive,"ROOT",root),patch.object(archive,"ARCHIVE",root/"editorial_archive.json"),patch.object(archive,"DZEN_SNAPSHOT",root/"_current_dzen_snapshot.json"),patch.object(archive,"SEEN",root/"seen_articles.json"),patch.object(archive,"INBOX",root/"instagram_cover_inbox.json"):
                result=archive.create_archive(NOW)
                published=[e for e in result["entries"] if e["status"]=="published"]
                cover=[e for e in result["entries"] if e["status"]=="cover_ready"]
                self.assertEqual(len(published),1)
                self.assertEqual(len(cover),1)
                self.assertTrue(published[0]["vertical_cover_prepared"])
                self.assertEqual(published[0]["dzen_url"],"https://dzen.ru/a/6a1234")
                self.assertEqual(result["legacy_seen_ids_count"],2)
                (root/"editorial_archive.json").write_text(json.dumps(result,ensure_ascii=False))
                (root/"_current_dzen_snapshot.json").write_text("[]")
                next_run=archive.create_archive(NOW+dt.timedelta(hours=1))
                self.assertEqual(next_run["entries"][0]["key"],result["entries"][0]["key"])
                self.assertEqual(len(next_run["entries"]),2)

    def test_reject_forged_dzen_url(self):
        for value in ("http://dzen.ru/a/123","https://dzen.ru.evil.com/a/123",
                      "https://example.com/a/123","https://dzen.ru/profile"):
            self.assertFalse(archive.is_public_dzen_link(value))

if __name__=="__main__":
    unittest.main()
