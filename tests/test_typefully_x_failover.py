"""Offline Typefully failover safety tests. NEVER call Buffer, Typefully or X."""
import asyncio
from copy import deepcopy
from unittest.mock import patch, AsyncMock
import unittest

import typefully_x_failover as t

NOW = 1795260000
ARTICLE = {"id": "dz-1", "url": "https://dzen.ru/a/abc",
           "title": "A full approved Dzen title", "date": NOW-80, "image": ""}
ENTRY = {"id": "valid-article-20261121", "dzen_title": ARTICLE["title"],
         "x_text": "Our approved X message", "status": "approved"}
QUEUE = {"schema_version": 1, "buffer_rate_limit_active": True,
         "buffer_cooldown_until": NOW+86400, "items": [ENTRY]}


class TypefullyFailoverTests(unittest.TestCase):
    def run_flow(self, queue=None, drafts=None, post=None, article=None, enabled="true"):
        q = deepcopy(queue if queue is not None else QUEUE)
        drafts = [] if drafts is None else drafts
        post = post if post is not None else {
            "id": 77, "draft_title": t.marker(ENTRY), "status": "publishing"}
        calls = []
        def api(method, path, body=None):
            calls.append((method, path, body))
            if method == "POST":
                return deepcopy(post)
            return {"status": "published", "id": 77,
                    "x_published_url": "https://x.com/iipike_ai/status/12345",
                    "platforms": {"x": {"enabled": True,
                                        "posts": [{"text": ENTRY["x_text"]}]}}}
        patches = [
            patch.object(t, "load_queue", return_value=q),
            patch.object(t, "save_queue"),
            patch.object(t, "social_set", return_value=2),
            patch.object(t, "list_drafts", return_value=drafts),
            patch.object(t, "api", side_effect=api),
            patch.object(t, "fetch_articles", new_callable=AsyncMock,
                         return_value=[article or ARTICLE]),
            patch.object(t.time, "time", return_value=NOW),
            patch.dict(t.os.environ, {"TYPEFULLY_API_KEY": "fake",
                                      "TYPEFULLY_FAILOVER_ENABLED": enabled}, clear=True)
        ]
        with patches[0], patches[1] as save, patches[2], patches[3], \
                patches[4], patches[5] as fetch, patches[6], patches[7]:
            outcome = asyncio.run(t.main())
        return q, calls, outcome, save, fetch

    def test_disabled_keeps_primary_without_typefully_call(self):
        q, calls, outcome, _, _ = self.run_flow(enabled="false")
        self.assertEqual(outcome, "primary")
        self.assertFalse(calls)

    def test_buffer_healthy_uses_primary(self):
        queue = deepcopy(QUEUE)
        queue["buffer_rate_limit_active"] = False
        _, calls, outcome, _, _ = self.run_flow(queue=queue)
        self.assertEqual(outcome, "primary")
        self.assertFalse(calls)

    def test_create_now_not_equal_to_delivery(self):
        q, calls, outcome, save, _ = self.run_flow()
        self.assertEqual(outcome, "created")
        self.assertEqual(len([c for c in calls if c[0] == "POST"]), 1)
        body = next(c[2] for c in calls if c[0] == "POST")
        self.assertEqual(body["publish_at"], "now")
        self.assertEqual(body["platforms"]["x"]["posts"][0]["text"], ENTRY["x_text"])
        self.assertEqual(q["items"][0]["status"], "approved")
        self.assertEqual(q["items"][0]["typefully_draft_id"], 77)
        self.assertTrue(q["items"][0]["typefully_attempt_unknown"])
        self.assertEqual(save.call_count, 2)

    def test_confirmed_x_url_updates_queue_without_buffer_id(self):
        queue = deepcopy(QUEUE)
        queue["items"][0].update(typefully_draft_id=77, typefully_attempt_at=NOW-3600,
                                 typefully_attempt_unknown=True,
                                 typefully_post_text=ENTRY["x_text"],
                                 typefully_source_dzen_id=ARTICLE["id"],
                                 typefully_source_dzen_url=ARTICLE["url"])
        d = {"id": 77, "draft_title": t.marker(ENTRY), "status": "published",
             "x_post_enabled": True, "created_at": "2026-11-21T00:00:00Z"}
        q, calls, outcome, _, fetch = self.run_flow(queue=queue, drafts=[d])
        self.assertEqual(outcome, "reconciled")
        self.assertEqual(q["items"][0]["status"], "queued")
        self.assertEqual(q["items"][0]["x_delivery_provider"], "typefully")
        self.assertTrue(q["items"][0]["typefully_post_url"].startswith("https://x.com/"))
        self.assertNotIn("buffer_post_id", q["items"][0])
        self.assertFalse(any(c[0] == "POST" for c in calls))
        fetch.assert_not_awaited()

    def test_ambiguous_attempt_never_retries(self):
        queue = deepcopy(QUEUE)
        queue["items"][0].update(typefully_attempt_unknown=True,
                                 typefully_post_text=ENTRY["x_text"],
                                 typefully_source_dzen_id=ARTICLE["id"],
                                 typefully_source_dzen_url=ARTICLE["url"])
        with self.assertRaisesRegex(t.FailoverError, "unlisted"):
            self.run_flow(queue=queue)

    def test_monthly_limit_prevents_write(self):
        drafts = [{"id": i, "x_post_enabled": True, "status": "published",
                   "created_at": "2026-11-21T00:00:00Z"} for i in range(8)]
        _, calls, outcome, _, _ = self.run_flow(drafts=drafts)
        self.assertEqual(outcome, "none")
        self.assertFalse(any(c[0] == "POST" for c in calls))

    def test_no_exact_approved_match_prevents_post(self):
        _, calls, outcome, _, _ = self.run_flow(
            article={**ARTICLE, "title": "Other title"})
        self.assertEqual(outcome, "none")
        self.assertFalse(any(c[0] == "POST" for c in calls))

    def test_only_our_x_url_counts_as_delivered(self):
        self.assertFalse(t.x_published_url(
            {"x_published_url": "https://x.com/stranger/status/12345"}))
        self.assertFalse(t.x_published_url(
            {"x_published_url": "http://x.com/iipike_ai/status/12345"}))
        self.assertTrue(t.x_published_url(
            {"x_published_url": "https://x.com/iipike_ai/status/12345"}))


if __name__ == "__main__":
    unittest.main()
