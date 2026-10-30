"""Offline tests for uncertain Instagram publishing responses (NO API requests)."""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import instagram_crosspost as ig


class ReconciliationTests(unittest.TestCase):
    TITLE = "ИИ-хакеры залезли к Anthropic всего за 12 часов"

    def test_exact_or_normalized_headline_only(self):
        caption = self.TITLE + "\n\nArticle summary #ИИ"
        self.assertTrue(ig.matches_published_caption(caption, {caption}))
        self.assertTrue(ig.matches_published_caption(
            caption, {self.TITLE + "\n\nDifferent spacing or teaser"}))
        self.assertFalse(ig.matches_published_caption(
            caption, {"ИИ-хакеры залезли к Anthropic\n\nPartial headline"}))
        self.assertFalse(ig.matches_published_caption(
            caption, {"Other post about " + self.TITLE}))

    def run_case(self, first_seen, after_error=None, publish_error=None, held=False, cutoff=1):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, pending, inbox = (root / name for name in (
                "seen_instagram_articles.json", "instagram_pending_articles.json",
                "instagram_cover_inbox.json"))
            item = {
                "id": "source-001", "title": self.TITLE,
                "text": "An article summary.", "url": "https://dzen.ru/a/test",
                "date": 1,
            }
            if held:
                item["instagram_publish_attempted"] = True
            ig.save_json(state, [])
            ig.save_json(pending, [item])
            ig.save_json(inbox, {"covers": {
                ig.normalize_title(self.TITLE): {
                    "title": self.TITLE,
                    "file": "instagram_covers/" + "a" * 20 + "-1.jpg",
                }}})
            after = [first_seen] if after_error is None else [first_seen, after_error]
            with patch.dict("os.environ", {"INSTAGRAM_FEED_ENABLED": "true",
                                        "INSTAGRAM_RESUME_FROM_UNIX": str(cutoff)}), \
                 patch.object(ig, "STATE", state), \
                 patch.object(ig, "PENDING", pending), \
                 patch.object(ig, "INBOX", inbox), \
                 patch.object(ig, "TOKEN", "test-token"), \
                 patch.object(ig, "fetch_articles", new=AsyncMock(return_value=[])), \
                 patch.object(ig, "graph", return_value={
                     "username": ig.EXPECTED_USERNAME, "user_id": "123",
                 }), \
                 patch.object(ig, "recently_published_captions",
                              side_effect=after), \
                 patch.object(ig, "is_cover_public", return_value=True), \
                 patch.object(ig, "publish_article",
                              side_effect=publish_error) as send:
                failure = None
                try:
                    asyncio.run(ig.main())
                except RuntimeError as e:
                    failure = str(e)
            return (json.loads(state.read_text()),
                    json.loads(pending.read_text()), send.call_count, failure)

    def test_already_live_with_changed_body_never_sends_again(self):
        seen, pending, sends, failure = self.run_case(
            {self.TITLE + "\n\nCaption formatted by Instagram"})
        self.assertEqual((seen, pending, sends, failure),
                         (["source-001"], [], 0, None))

    def test_403_then_confirmed_by_remote_media_is_success(self):
        seen, pending, sends, failure = self.run_case(
            set(), {self.TITLE + "\n\nNow visible in Instagram"},
            RuntimeError("Instagram HTTP 403, code 4"))
        self.assertEqual((seen, pending, sends, failure),
                         (["source-001"], [], 1, None))

    def test_403_without_confirmation_holds_instead_of_retry(self):
        seen, pending, sends, failure = self.run_case(
            set(), set(), RuntimeError("Instagram HTTP 403, code 4"))
        self.assertEqual(seen, [])
        self.assertEqual(sends, 1)
        self.assertIn("403", failure)
        self.assertTrue(pending[0]["instagram_publish_attempted"])

    def test_previous_uncertain_attempt_does_not_resend(self):
        seen, pending, sends, failure = self.run_case(set(), held=True)
        self.assertEqual(seen, [])
        self.assertEqual(sends, 0)
        self.assertIsNone(failure)
        self.assertTrue(pending[0]["instagram_publish_attempted"])

    def test_resume_drops_old_pending_instead_of_publishing(self):
        seen, pending, sends, failure = self.run_case(set(), cutoff=100)
        self.assertEqual((seen, pending, sends, failure), ([], [], 0, None))

    def test_pause_does_not_fetch_or_mutate_pending_queue(self):
        with patch.dict("os.environ", {"INSTAGRAM_FEED_ENABLED": "false"}), \
             patch.object(ig, "fetch_articles", new=AsyncMock()) as fetch, \
             patch.object(ig, "graph") as graph:
            asyncio.run(ig.main())
            fetch.assert_not_awaited()
            graph.assert_not_called()

    def test_no_resume_cutoff_refuses_publication(self):
        with patch.dict("os.environ", {"INSTAGRAM_FEED_ENABLED": "true",
                                      "INSTAGRAM_RESUME_FROM_UNIX": "0"}), \
             patch.object(ig, "fetch_articles", new=AsyncMock()) as fetch, \
             patch.object(ig, "graph") as graph:
            with self.assertRaisesRegex(RuntimeError, "resume requires"):
                asyncio.run(ig.main())
            fetch.assert_not_awaited()
            graph.assert_not_called()


if __name__ == "__main__":
    unittest.main()
