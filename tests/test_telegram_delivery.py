"""Regression tests: never duplicate a Telegram post after an ambiguous API timeout."""
import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import urllib.error

import crosspost
import telegram_photo


def article():
    return {
        "id": "dzen-myheritage-001",
        "title": "MyHeritage: тест", "text": "Новая статья.",
        "url": "https://dzen.ru/a/test-id",
        "image": "https://example.org/cover.jpg", "date": 7,
    }


def rejection():
    return urllib.error.HTTPError(
        "https://api.telegram.org/redacted/sendPhoto", 400, "Bad Request",
        {}, io.BytesIO(b'{"description":"Bad Request: wrong file identifier"}'),
    )


class TelegramDeliveryTests(unittest.TestCase):
    def test_url_photo_timeout_holds_without_upload_or_text_fallback(self):
        with patch.object(crosspost, "api_call", side_effect=TimeoutError), \
             patch.object(crosspost, "send_photo_upload") as upload:
            with self.assertRaisesRegex(crosspost.TelegramDeliveryUncertain,
                                        "URL photo outcome uncertain"):
                crosspost.send_article(article())
            upload.assert_not_called()

    def test_url_photo_5xx_holds_without_any_fallback(self):
        error = urllib.error.HTTPError(
            "https://api.telegram.org/redacted/sendPhoto", 502, "Gateway",
            {}, io.BytesIO(b'{"ok":false}'))
        with patch.object(crosspost, "api_call", side_effect=error) as api, \
             patch.object(crosspost, "send_photo_upload") as upload:
            with self.assertRaises(crosspost.TelegramDeliveryUncertain):
                crosspost.send_article(article())
            self.assertEqual(api.call_count, 1)
            upload.assert_not_called()

    def test_photo_upload_timeout_does_not_send_text(self):
        with patch.object(crosspost, "api_call", side_effect=rejection()) as api, \
             patch.object(crosspost, "send_photo_upload",
                          side_effect=telegram_photo.TelegramUploadUncertain("timeout")):
            with self.assertRaises(crosspost.TelegramDeliveryUncertain):
                crosspost.send_article(article())
            self.assertEqual(api.call_count, 1)

    def test_definite_photo_rejections_allow_one_text_fallback(self):
        with patch.object(crosspost, "api_call", side_effect=[rejection(), {"ok":True}]) as api, \
             patch.object(crosspost, "send_photo_upload",
                          side_effect=telegram_photo.PhotoPreparationError("image download failed")):
            crosspost.send_article(article())
            self.assertEqual([c.args[0] for c in api.call_args_list],
                             ["sendPhoto", "sendMessage"])

    def test_text_only_timeout_is_uncertain_too(self):
        row = dict(article(), image="")
        with patch.object(crosspost, "api_call", side_effect=TimeoutError):
            with self.assertRaises(crosspost.TelegramDeliveryUncertain):
                crosspost.send_article(row)

    def test_uncertain_id_is_durably_held_on_next_run_not_marked_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev = os.getcwd()
            try:
                os.chdir(tmp)
                seen = Path(tmp, "seen_articles.json")
                uncertain = Path(tmp, "telegram_delivery_uncertain.json")
                seen.write_text("[]\n", encoding="utf-8")
                uncertain.write_text("[]\n", encoding="utf-8")
                with patch.object(crosspost, "STATE", seen), \
                     patch.object(crosspost, "UNCERTAIN", uncertain), \
                     patch.object(crosspost, "fetch_articles", new_callable=AsyncMock,
                                  return_value=[article()]), \
                     patch.object(crosspost, "load_site_catalog", return_value=[]), \
                     patch.object(crosspost, "send_article",
                                  side_effect=crosspost.TelegramDeliveryUncertain("timeout")) as send:
                    with self.assertRaises(crosspost.TelegramDeliveryUncertain):
                        asyncio.run(crosspost.main())
                    send.assert_called_once()
                    self.assertEqual(json.loads(uncertain.read_text()),
                                     ["dzen-myheritage-001"])
                    self.assertEqual(json.loads(seen.read_text()), [])
                with patch.object(crosspost, "STATE", seen), \
                     patch.object(crosspost, "UNCERTAIN", uncertain), \
                     patch.object(crosspost, "fetch_articles", new_callable=AsyncMock,
                                  return_value=[article()]), \
                     patch.object(crosspost, "load_site_catalog", return_value=[]), \
                     patch.object(crosspost, "send_article") as send:
                    asyncio.run(crosspost.main())
                    send.assert_not_called()
                    self.assertEqual(json.loads(seen.read_text()), [])
            finally:
                os.chdir(prev)

    def test_multipart_request_timeout_is_not_explicit_rejection(self):
        with patch.object(telegram_photo, "download_photo", return_value=b"jpg"), \
             patch.object(telegram_photo.urllib.request, "urlopen",
                          side_effect=TimeoutError):
            with self.assertRaises(telegram_photo.TelegramUploadUncertain):
                telegram_photo.send_photo_upload("not-a-real-token", "@test", "Text", "https://example.com")

    def test_photo_download_error_is_safe_before_network_send(self):
        with patch.object(telegram_photo, "download_photo", side_effect=TimeoutError), \
             patch.object(telegram_photo.urllib.request, "urlopen") as network:
            with self.assertRaises(telegram_photo.PhotoPreparationError):
                telegram_photo.send_photo_upload("not-a-real-token", "@test", "Text", "https://example.com")
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
