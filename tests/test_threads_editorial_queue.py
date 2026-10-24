"""Offline guardrails: Threads must send approved text only, no duplicate teasers."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
import threads_editorial_queue as ed
import threads_crosspost as bot

ARTICLE = {"id": "dzen-1001", "title": "OpenAI: модель X — вышла!", "text": "This text must NEVER be reposted as a teaser.",
           "url": "https://dzen.ru/a/test1001", "image": "", "date": 1792500000}
QUEUE = {"schema_version": 1, "items": [
    {"id": "threadtest-1001", "dzen_title": "OpenAI модель X вышла",
     "threads_text": "Готовый авторский текст.\n\nС иронией, а не пересказ Дзена.",
     "status": "approved"}
]}

class EditorialQueueTests(unittest.TestCase):
    def test_normalized_headline_matches_without_fuzzy_guessing(self):
        entries = ed.eligible_matches([ARTICLE], QUEUE, set())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1]["threads_text"], QUEUE["items"][0]["threads_text"])
        self.assertEqual(ed.eligible_matches([ARTICLE], QUEUE, {"dzen-1001"}), [])
        self.assertEqual(ed.eligible_matches([{**ARTICLE, "title":"Совсем другая новость"}], QUEUE, set()), [])

    def test_ambiguity_blocks_posting(self):
        second = {**ARTICLE, "id": "dzen-1002", "date": 1792500100}
        self.assertEqual(ed.eligible_matches([ARTICLE, second], QUEUE, set()), [])
        bad = {"schema_version":1,"items":[QUEUE["items"][0],{**QUEUE["items"][0],"id":"anotherid-1002"}]}
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            ed.validate_queue(bad)

    def test_invalid_or_unapproved_posts_never_match(self):
        waiting = {"schema_version":1,"items":[{**QUEUE["items"][0],"status":"on_hold"}]}
        self.assertEqual(ed.eligible_matches([ARTICLE], waiting, set()), [])
        at_limit = {"schema_version":1,"items":[{**QUEUE["items"][0],"threads_text":"А"*480}]}
        ed.validate_queue(at_limit)
        overlong = {"schema_version":1,"items":[{**QUEUE["items"][0],"threads_text":"А"*481}]}
        with self.assertRaisesRegex(RuntimeError, "480"):
            ed.validate_queue(overlong)
        sent = {"schema_version":1,"items":[{**QUEUE["items"][0],"status":"published", "dzen_id": "dzen-1001"}]}
        self.assertEqual(ed.eligible_matches([ARTICLE], sent, set()), [])
        ed.validate_queue(sent)

    def test_publication_links_to_source_id(self):
        item = {**QUEUE["items"][0]}
        ed.mark_published(item, ARTICLE, "threads-999")
        self.assertEqual(item["status"], "published")
        self.assertEqual(item["dzen_id"], ARTICLE["id"])
        self.assertEqual(item["threads_post_id"], "threads-999")

    def test_selective_dzen_link_only_when_explicitly_approved(self):
        author_text = QUEUE["items"][0]["threads_text"]
        no_link = ed.compose_delivery_text(QUEUE["items"][0], ARTICLE)
        self.assertEqual(no_link, author_text)
        approved = {**QUEUE["items"][0], "include_dzen_link": True}
        message = ed.compose_delivery_text(approved, ARTICLE)
        self.assertEqual(message, author_text + "\n\nПодробнее в Дзене: " + ARTICLE["url"])
        self.assertLessEqual(len(message), 500)

    def test_bad_or_missing_dzen_link_blocks_post(self):
        approved = {**QUEUE["items"][0], "include_dzen_link": True}
        for url in ("https://dzen.ru.evil.com/a/123", "https://dzen.ru/a/123?foo=bar",
                    "http://dzen.ru/a/123", "", "https://dzen.ru/not-public"):
            with self.assertRaisesRegex(RuntimeError, "Dzen article URL"):
                ed.compose_delivery_text(approved, {**ARTICLE, "url": url})

    def test_long_author_post_never_truncated_to_make_room_for_link(self):
        approved = {**QUEUE["items"][0], "include_dzen_link": True,
                    "threads_text": "Т" * 475}
        with self.assertRaisesRegex(RuntimeError, "500 characters"):
            ed.compose_delivery_text(approved, ARTICLE)
        self.assertEqual(len(approved["threads_text"]), 475)
        bad = {"schema_version": 1, "items": [
            {**QUEUE["items"][0], "include_dzen_link": "yes"}]}
        with self.assertRaisesRegex(RuntimeError, "true or false"):
            ed.validate_queue(bad)

    def test_queue_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"queue.json"
            ed.save_queue(QUEUE,path)
            self.assertEqual(ed.load_queue(path)["items"][0]["threads_text"],
                             QUEUE["items"][0]["threads_text"])

    def test_eko_488_character_production_regression(self):
        # Exact offending entry from 452b424, before the 41a1e78 data fix.
        path = Path(__file__).parent / "fixtures" / "threads_eko_488.json"
        queue = json.loads(path.read_text(encoding="utf-8"))
        entry = queue["items"][0]
        original_text = entry["threads_text"]
        self.assertEqual(len(original_text), 488)
        self.assertFalse(entry["include_dzen_link"])
        with self.assertRaisesRegex(RuntimeError, "480 characters: " + entry["id"]):
            ed.validate_queue(queue)
        self.assertEqual(entry["threads_text"], original_text)

    def test_committed_production_queue_roundtrip(self):
        # Synthetic fixtures alone missed the invalid production data in 452b424.
        source = Path(ed.__file__).with_name("threads_editorial_queue.json")
        original = source.read_bytes()
        queue = ed.load_queue(source)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "queue.json"
            ed.save_queue(queue, target)
            # Includes every text, status, Dzen ID and Threads publication ID.
            self.assertEqual(ed.load_queue(target), json.loads(original))
        self.assertEqual(source.read_bytes(), original)

class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_without_approval_no_threads_or_dzen_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"seen.json"
            path.write_text('[]', encoding='utf-8')
            with patch.object(bot, "STATE", path), patch.object(bot, "TOKEN", "dummy"), \
                 patch.object(bot, "load_queue", return_value={"schema_version":1,"items":[]}), \
                 patch.object(bot, "fetch_articles", new_callable=AsyncMock) as fetch, \
                 patch.object(bot, "api") as api:
                await bot.main()
                fetch.assert_not_awaited()
                api.assert_not_called()

    async def test_unmatched_news_never_generates_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"seen.json"
            path.write_text('[]', encoding='utf-8')
            with patch.object(bot, "STATE", path), patch.object(bot, "TOKEN", "dummy"), \
                 patch.object(bot, "load_queue", return_value=QUEUE), \
                 patch.object(bot, "fetch_articles", new_callable=AsyncMock, return_value=[
                     {**ARTICLE,"title":"Something entirely different"}]), \
                 patch.object(bot, "api") as api, patch.object(bot, "publish") as publish:
                await bot.main()
                api.assert_not_called()
                publish.assert_not_called()
                self.assertEqual(json.loads(path.read_text()), [])

    async def test_approved_text_is_sent_unchanged_and_marked(self):
        with tempfile.TemporaryDirectory() as folder:
            seenpath=Path(folder)/"seen.json"
            queuepath=Path(folder)/"queue.json"
            seenpath.write_text('[]',encoding='utf-8')
            ed.save_queue(QUEUE,queuepath)
            # Real queue loader/save but redirect filesystem; fake Threads API.
            def mock_api(method,path,params=None):
                if path=="me":return {"username":"iipike.ai"}
                if path=="me/threads":return {"data":[]}
                raise AssertionError("Unexpected API call")
            with patch.object(bot,"STATE",seenpath),patch.object(bot,"TOKEN","dummy"), \
                 patch.object(bot, "load_queue", side_effect=lambda: ed.load_queue(queuepath)), \
                 patch.object(bot, "save_queue",side_effect=lambda q: ed.save_queue(q,queuepath)), \
                 patch.object(bot,"fetch_articles",new_callable=AsyncMock,return_value=[ARTICLE]), \
                 patch.object(bot,"api",side_effect=mock_api), \
                 patch.object(bot,"publish",return_value="threads-999") as publish:
                await bot.main()
                publish.assert_called_once_with(
                    QUEUE["items"][0]["threads_text"],cover="")
            result=ed.load_queue(queuepath)
            self.assertEqual(result["items"][0]["status"],"published")
            self.assertEqual(json.loads(seenpath.read_text()),["dzen-1001"])

    async def test_linked_author_text_posts_once_with_real_dzen_url(self):
        with tempfile.TemporaryDirectory() as folder:
            seenpath = Path(folder) / "seen.json"
            queuepath = Path(folder) / "queue.json"
            seenpath.write_text("[]", encoding="utf-8")
            approved = {"schema_version": 1, "items": [
                {**QUEUE["items"][0], "include_dzen_link": True}]}
            ed.save_queue(approved, queuepath)
            expected = ed.compose_delivery_text(approved["items"][0], ARTICLE)
            def mock_api(method, path, params=None):
                if path == "me":
                    return {"username": "iipike.ai"}
                if path == "me/threads":
                    return {"data": []}
                raise AssertionError("Unexpected API request")
            with patch.object(bot, "STATE", seenpath), patch.object(bot, "TOKEN", "dummy"), \
                 patch.object(bot, "load_queue", side_effect=lambda: ed.load_queue(queuepath)), \
                 patch.object(bot, "save_queue", side_effect=lambda q: ed.save_queue(q, queuepath)), \
                 patch.object(bot, "fetch_articles", new_callable=AsyncMock,
                              return_value=[ARTICLE]), \
                 patch.object(bot, "api", side_effect=mock_api), \
                 patch.object(bot, "publish", return_value="threads-999") as publish:
                await bot.main()
                publish.assert_called_once_with(expected, cover="")
            result = ed.load_queue(queuepath)
            self.assertEqual(result["items"][0]["status"], "published")
            self.assertEqual(result["items"][0]["dzen_id"], ARTICLE["id"])
            self.assertEqual(json.loads(seenpath.read_text()), [ARTICLE["id"]])

    async def test_remote_lookup_failure_blocks_send(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"seen.json"
            path.write_text('[]',encoding='utf-8')
            def fake_api(method,target,params=None):
                if target=="me":return {"username":"iipike.ai"}
                raise RuntimeError("Threads API unavailable")
            with patch.object(bot,"STATE",path),patch.object(bot,"TOKEN","dummy"), \
                 patch.object(bot,"load_queue",return_value=QUEUE), \
                 patch.object(bot,"fetch_articles",new_callable=AsyncMock,return_value=[ARTICLE]), \
                 patch.object(bot,"api",side_effect=fake_api), \
                 patch.object(bot,"publish") as publish:
                with self.assertRaisesRegex(RuntimeError,"unavailable"):
                    await bot.main()
                publish.assert_not_called()

if __name__=="__main__":
    unittest.main()
