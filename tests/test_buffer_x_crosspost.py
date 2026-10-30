"""Offline X/Buffer safety tests. Never contact Buffer, Dzen or X."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock, patch
import unittest

import buffer_x_crosspost as x
import x_editorial_queue as q

ARTICLE = {
    "id": "6ae-example", "title": "Google представила нового ИИ-помощника",
    "date": 1793300000, "url": "https://dzen.ru/a/abc123", "text": "News",
    "image": "https://avatars.mds.yandex.net/get-zen_doc/example/orig",
}
ENTRY = {
    "id": "google-ai-agent-20261030", "dzen_title": ARTICLE["title"],
    "x_text": "Google выпустила ИИ-помощника. Главное — что он умеет на практике.",
    "status": "approved", "include_dzen_link": False,
}
QUEUE = {"schema_version": 1, "items": [ENTRY]}


class QueueValidationTests(unittest.TestCase):
    def test_empty_production_queue_valid(self):
        self.assertEqual(q.validate_queue({"schema_version": 1, "items": []}), [])

    def test_exact_title_and_freshness_required(self):
        self.assertEqual(len(q.matching_approved([ARTICLE], QUEUE, 1793300100)), 1)
        self.assertEqual(q.matching_approved([ARTICLE], QUEUE, 1793800000), [])
        self.assertEqual(q.matching_approved([{**ARTICLE, "title": "A different headline"}],
                                             QUEUE, 1793300100), [])
        self.assertEqual(q.matching_approved([ARTICLE, ARTICLE], QUEUE, 1793300100), [])

    def test_reject_long_or_duplicate_or_unapproved_text(self):
        for override in ({"x_text": "a"*281}, {"x_text": ""}, {"status": "draft"}):
            with self.assertRaises(RuntimeError):
                q.validate_queue({"schema_version": 1, "items": [{**ENTRY, **override}]})
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            q.validate_queue({"schema_version": 1, "items": [
                ENTRY, {**ENTRY, "id": "another-item-001"}]})

    def test_links_never_truncate_or_invent(self):
        self.assertEqual(q.delivery_text(ENTRY, ARTICLE), ENTRY["x_text"])
        with_link = {**ENTRY, "include_dzen_link": True}
        self.assertTrue(q.delivery_text(with_link, ARTICLE).endswith(ARTICLE["url"]))
        with self.assertRaisesRegex(RuntimeError, "280"):
            q.delivery_text({**with_link, "x_text": "а"*270}, ARTICLE)

    def test_queued_requires_id_evidence(self):
        result = copy.deepcopy(ENTRY)
        q.mark_queued(result, ARTICLE, "buffer-100")
        self.assertEqual(result["buffer_post_id"], "buffer-100")
        self.assertEqual(result["status"], "queued")
        self.assertIs(result["with_image"], False)
        q.validate_queue({"schema_version": 1, "items": [result]})
        with self.assertRaisesRegex(RuntimeError, "delivery evidence"):
            q.validate_queue({"schema_version": 1, "items": [{**ENTRY, "status": "queued"}]})


class RemoteTests(unittest.TestCase):
    def test_buffer_post_mutation_requires_matching_confirmation(self):
        with patch.object(x, "graphql", return_value={
            "createPost": {"post": {"id": "buf123", "text": ENTRY["x_text"], "assets": []}}
        }) as client:
            self.assertEqual(x.create_scheduled_post("channel123", ENTRY["x_text"]), "buf123")
            self.assertIn("addToQueue", client.call_args[0][0])
            self.assertNotIn("assets: [", client.call_args[0][0])
        cover = ARTICLE["image"]
        with patch.object(x, "graphql", return_value={
            "createPost": {"post": {"id": "img123", "text": ENTRY["x_text"],
                                    "assets": [{"id": "asset123", "mimeType": "image/jpeg"}]}}
        }) as client:
            self.assertEqual(x.create_scheduled_post("channel123", ENTRY["x_text"],
                                                      cover_url=cover), "img123")
            self.assertIn("assets: [{image: {url:", client.call_args[0][0])
            self.assertIn(cover, client.call_args[0][0])
        with patch.object(x, "graphql", return_value={
            "createPost": {"post": {"id": "wrong", "text": ENTRY["x_text"], "assets": []}}
        }):
            with self.assertRaisesRegex(RuntimeError, "media mismatch"):
                x.create_scheduled_post("channel123", ENTRY["x_text"], cover_url=cover)
        with patch.object(x, "graphql", return_value={"createPost": {"message": "no room"}}):
            with self.assertRaisesRegex(RuntimeError, "not confirmed"):
                x.create_scheduled_post("channel123", ENTRY["x_text"])

    def test_remote_pagination_required_before_any_writes(self):
        with patch.object(x, "graphql", side_effect=[
            {"posts": {"edges": [{"node": {"id": "old", "text": "previous", "assets": []}}],
                       "pageInfo": {"hasNextPage": True, "endCursor": "cursor1"}}},
            {"posts": {"edges": [{"node": {"id": "next", "text": "another", "assets": []}}],
                       "pageInfo": {"hasNextPage": False, "endCursor": None}}},
        ]) as client:
            posts = x.remote_posts("organization", "channel", ["sent"])
            self.assertEqual(len(posts), 2)
            self.assertIn("cursor1", client.call_args[0][0])
            self.assertIn("assets { id mimeType }", client.call_args[0][0])
        with patch.object(x, "graphql", return_value={"posts": {"edges": []}}):
            with self.assertRaisesRegex(RuntimeError, "pagination"):
                x.remote_posts("organization", "channel", ["scheduled"])


class RuntimeTests(unittest.TestCase):
    def setup(self, queue=None, scheduled=None, sent=None):
        base = copy.deepcopy(queue if queue is not None else QUEUE)
        mocks = [
            patch.object(x, "load_queue", return_value=base),
            patch.object(x, "fetch_articles", new_callable=AsyncMock,
                         return_value=[ARTICLE]),
            patch.object(x, "find_channel", return_value={
                "org_id": "organization", "id": "channel", "name": "iipike_ai"}),
            patch.object(x, "remote_posts", side_effect=[
                [] if scheduled is None else scheduled, [] if sent is None else sent]),
            patch.object(x, "save_queue"),
            patch.object(x, "create_scheduled_post", return_value="buffer123"),
            patch.dict("os.environ", {"BUFFER_API_KEY": "dummy"}),
            patch.object(x.time, "time", return_value=1793300100),
        ]
        return base, mocks

    def test_empty_editorial_queue_makes_zero_api_calls(self):
        _, patches = self.setup(queue={"schema_version": 1, "items": []})
        with patches[0], patches[1] as fetch, patches[2] as lookup, patches[3] as posts,              patches[4], patches[5] as send, patches[6], patches[7]:
            asyncio.run(x.main())
            fetch.assert_not_awaited()
            lookup.assert_not_called()
            posts.assert_not_called()
            send.assert_not_called()

    def test_queue_one_approved_message(self):
        data, p = self.setup()
        with p[0], p[1], p[2], p[3], p[4] as save, p[5] as send, p[6], p[7]:
            asyncio.run(x.main())
            send.assert_called_once_with("channel", ENTRY["x_text"],
                                         cover_url=ARTICLE["image"])
            self.assertTrue(data["items"][0]["with_image"])
            self.assertEqual(data["items"][0]["status"], "queued")
            save.assert_called_once()

    def test_remote_duplicate_recovers_without_second_post(self):
        data, p = self.setup(sent=[{"id":"previous-id","text":ENTRY["x_text"],
                                          "assets":[{"id":"asset","mimeType":"image/jpeg"}]}])
        with p[0], p[1], p[2], p[3], p[4] as save, p[5] as send, p[6], p[7]:
            asyncio.run(x.main())
            send.assert_not_called()
            self.assertEqual(data["items"][0]["buffer_post_id"], "previous-id")
            save.assert_called_once()

    def test_dzen_cover_must_be_public_https_and_first_slot_waits(self):
        self.assertTrue(x.attach_cover_next(QUEUE))
        for url in ("", "http://example.com/c.jpg", "https://127.0.0.1/a.jpg",
                    "https://localhost/a.jpg", "https://u:p@example.com/a.jpg"):
            self.assertIsNone(x.verified_cover_url({**ARTICLE, "image": url}))
        self.assertEqual(x.verified_cover_url(ARTICLE), ARTICLE["image"])
        data, p = self.setup()
        with p[0], p[1] as fetch, p[2], p[3], p[4] as save, p[5] as send, p[6], p[7]:
            fetch.return_value = [{**ARTICLE, "image": ""}]
            asyncio.run(x.main())
            send.assert_not_called()
            save.assert_not_called()
            self.assertEqual(data["items"][0]["status"], "approved")

    def test_second_x_post_is_text_only_third_is_image(self):
        previous = {**ENTRY, "id":"another-post-20261030",
                    "dzen_title":"Another unique article already published in X",
                    "status":"queued", "dzen_id":"old-001", "buffer_post_id":"buffer-old",
                    "with_image":True}
        data, p = self.setup(queue={"schema_version":1, "items":[previous, {**ENTRY}]})
        with p[0], p[1], p[2], p[3], p[4] as save, p[5] as send, p[6], p[7]:
            asyncio.run(x.main())
            send.assert_called_once_with("channel", ENTRY["x_text"], cover_url=None)
            self.assertFalse(data["items"][1]["with_image"])
            self.assertEqual(data["items"][1]["status"], "queued")
            save.assert_called_once()
        self.assertTrue(x.attach_cover_next(data))
        self.assertFalse(x.attach_cover_next({"items":[previous]}))

    def test_remote_same_text_without_image_holds_instead_of_duplicating(self):
        data, p = self.setup(sent=[{"id":"remote-text", "text":ENTRY["x_text"], "assets":[]}])
        with p[0], p[1], p[2], p[3], p[4] as save, p[5] as send, p[6], p[7]:
            with self.assertRaisesRegex(RuntimeError, "different image state"):
                asyncio.run(x.main())
            send.assert_not_called()
            save.assert_not_called()
            self.assertEqual(data["items"][0]["status"], "approved")

    def test_capacity_does_not_block_recovering_existing_image(self):
        full = [{"id":str(i), "text":str(i), "assets":[]}
                for i in range(x.MAX_REMOTE_QUEUE)]
        data, p = self.setup(scheduled=full, sent=[
            {"id":"already-created", "text":ENTRY["x_text"],
             "assets":[{"id":"asset-1", "mimeType":"image/png"}]}])
        with p[0], p[1], p[2], p[3], p[4] as save, p[5] as send, p[6], p[7]:
            asyncio.run(x.main())
            send.assert_not_called()
            self.assertEqual(data["items"][0]["buffer_post_id"], "already-created")
            save.assert_called_once()

    def test_buffer_queue_near_limit_pauses(self):
        data, p = self.setup(scheduled=[{"id":str(i),"text":str(i)}
                                       for i in range(x.MAX_REMOTE_QUEUE)])
        with p[0], p[1], p[2], p[3], p[4] as save, p[5] as send, p[6], p[7]:
            asyncio.run(x.main())
            send.assert_not_called()
            save.assert_not_called()
            self.assertEqual(data["items"][0]["status"], "approved")


if __name__ == "__main__":
    unittest.main()
