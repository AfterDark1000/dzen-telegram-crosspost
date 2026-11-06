"""Offline regression tests for tagged inbound traffic to iipike.ru."""
import json
import unittest
from urllib.error import URLError
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
import site_utm as utm


def entry(story_id="my-verified-story"):
    return {
        "id": story_id,
        "url": "articles/" + story_id + "/",
        "dzen_url": "https://dzen.ru/a/aumvDMRL2iID_kKu",
    }


class SiteAttributionTests(unittest.TestCase):
    def setUp(self):
        self.item = {"url": "https://dzen.ru/a/aumvDMRL2iID_kKu?from=feed"}

    def test_exact_dzen_url_maps_to_first_party_article(self):
        url = utm.tagged_site_url(self.item, [entry()], "vk")
        self.assertEqual(urlsplit(url).path, "/articles/my-verified-story/")
        qs = parse_qs(urlsplit(url).query)
        self.assertEqual(qs, {
            "utm_source": ["vk"], "utm_medium": ["social"],
            "utm_campaign": ["hpii_articles"],
            "utm_content": ["my-verified-story"],
        })

    def test_telegram_and_max_are_not_misattributed(self):
        url = utm.tagged_site_url(self.item, [entry()], "telegram_max")
        self.assertEqual(parse_qs(urlsplit(url).query)["utm_source"], ["telegram_max"])
        self.assertEqual(parse_qs(urlsplit(url).query)["utm_medium"], ["social"])

    def test_site_first_dzen_article_also_resolves(self):
        rec = entry()
        rec.pop("dzen_url")
        rec["site_first_dzen_url"] = "https://dzen.ru/a/aumvDMRL2iID_kKu"
        self.assertIsNotNone(utm.tagged_site_url(self.item, [rec], "vk"))

    def test_wrong_dzen_url_never_guesses_from_title(self):
        self.assertIsNone(utm.tagged_site_url(
            {"url": "https://dzen.ru/a/unrelated", "title": "my-verified-story"},
            [entry()], "vk"
        ))

    def test_duplicate_mapping_fails_closed(self):
        self.assertIsNone(utm.tagged_site_url(
            self.item, [entry("one"), entry("two")], "vk"))

    def test_reject_unsafe_urls_and_site_records(self):
        urls = [
            "http://dzen.ru/a/aumvDMRL2iID_kKu",
            "https://dzen.ru.evil.test/a/aumvDMRL2iID_kKu",
            "https://user@dzen.ru/a/aumvDMRL2iID_kKu",
            "https://dzen.ru/a/../aumvDMRL2iID_kKu",
            "https://dzen.ru/?id=some",
        ]
        for candidate in urls:
            with self.subTest(candidate=candidate):
                self.assertIsNone(utm.tagged_site_url({"url": candidate}, [entry()], "vk"))
        invalid = entry()
        invalid["url"] = "https://evil.test/articles/my-verified-story/"
        self.assertIsNone(utm.tagged_site_url(self.item, [invalid], "vk"))

    def test_no_catalog_no_link(self):
        self.assertIsNone(utm.tagged_site_url(self.item, [], "vk"))

    def test_load_unavailable_site_is_optional(self):
        with patch.object(utm, "urlopen", side_effect=URLError("temporarily offline")):
            self.assertEqual(utm.load_site_catalog(), [])

    def test_home_links_for_manual_distribution(self):
        for source in ("dzen", "threads", "x", "instagram", "youtube", "max"):
            with self.subTest(source=source):
                url = utm.tagged_home_url(source)
                self.assertEqual(parse_qs(urlsplit(url).query)["utm_source"], [source])
                self.assertEqual(urlsplit(url).netloc, "iipike.ru")

    def test_invalid_platform_is_rejected(self):
        with self.assertRaises(ValueError):
            utm.tagged_home_url("http://evil.test")
        with self.assertRaises(ValueError):
            utm.tagged_site_url(self.item, [entry()], "http://evil.test")


if __name__ == "__main__":
    unittest.main()
