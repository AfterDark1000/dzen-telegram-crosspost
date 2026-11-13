"""Offline regressions: a Dzen rich preview must not leak into VK text."""
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from vk_article_body import clean_dzen_embeds, known_related_links

TITLE = "NVIDIA показала ИИ, который продолжает виртуальный мир после движения робота"
DZEN_URL = "https://dzen.ru/a/au8XP4xPLRylmXQ7"
SITE_ID = "cosmos3-nano-interactive-worlds-20261105"
SITE = {"id": SITE_ID, "url": "articles/" + SITE_ID + "/",
        "site_first_dzen_url": DZEN_URL}
ARTICLE_BODY = (
    "Для этого GR00T 2 одновременно прогнозирует действия робота.\n\n"
    "В основе используется семейство Cosmos. Ранее NVIDIA показала Cosmos 3 Nano.\n\n"
    + TITLE + "\nХроники Пикирующего ИИ\n3 дня назад\n\n"
    "Насколько хорошо роботы обучаются?\n\n"
    "Исследователи проверили GR00T 2 на 50 заданиях."
)


class VKArticleBodyTests(unittest.TestCase):
    def test_dzen_related_card_becomes_single_clickable_site_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(json.dumps({"entries": [{
                "title": TITLE, "dzen_url": DZEN_URL,
                "status": "published_observed_in_feed",
            }]}), encoding="utf-8")
            related = known_related_links([SITE], path=path)
        cleaned, count = clean_dzen_embeds(ARTICLE_BODY, related)
        self.assertEqual(count, 1)
        self.assertIn("Читайте также: " + TITLE, cleaned)
        self.assertIn("https://iipike.ru/articles/" + SITE_ID + "/", cleaned)
        self.assertIn("utm_source=vk", cleaned)
        self.assertNotIn("Хроники Пикирующего ИИ\n3 дня назад", cleaned)
        self.assertEqual(cleaned.count(TITLE), 1)
        self.assertIn("Насколько хорошо роботы обучаются?", cleaned)
        self.assertIn("Исследователи проверили GR00T 2 на 50 заданиях.", cleaned)

    def test_unmapped_widget_is_removed_not_replaced_by_fake_url(self):
        cleaned, count = clean_dzen_embeds(ARTICLE_BODY, {})
        self.assertEqual(count, 1)
        self.assertNotIn(TITLE + "\nХроники Пикирующего ИИ\n3 дня назад", cleaned)
        self.assertNotIn("Читайте также:", cleaned)
        self.assertIn("Насколько хорошо", cleaned)

    def test_live_dom_anchor_can_supply_verified_dzen_link(self):
        cleaned, count = clean_dzen_embeds(
            ARTICLE_BODY, {}, dom_links=[
                {"title": TITLE, "url": DZEN_URL + "?from=feed"},
                {"title": TITLE, "url": "https://evil.example.net/a/fake"},
            ])
        self.assertEqual(count, 1)
        self.assertIn(DZEN_URL, cleaned)
        self.assertNotIn("evil.example.net", cleaned)

    def test_prose_and_regular_inline_links_remain_untouched(self):
        body = ("В тексте есть обычная ссылка https://example.org/paper.\n"
                "Хроники Пикирующего ИИ рассказали об этом три дня назад.\n\n"
                "Здесь заканчивается честный абзац.")
        cleaned, count = clean_dzen_embeds(body, {})
        self.assertEqual(count, 0)
        self.assertEqual(cleaned, body)

    def test_different_widget_age_labels(self):
        for age in ("вчера", "15 минут назад", "1 час назад", "2 недели назад",
                    "5 месяцев назад", "только что"):
            with self.subTest(age=age):
                body = TITLE + "\nХроники Пикирующего ИИ\n" + age + "\n\nВывод."
                cleaned, count = clean_dzen_embeds(body, {TITLE.casefold(): DZEN_URL})
                self.assertEqual(count, 1)
                self.assertNotIn(age, cleaned)
                self.assertIn(DZEN_URL, cleaned)

    def test_conflicting_titles_never_get_guessed_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalog.json"
            path.write_text(json.dumps({"entries": [
                {"title": TITLE, "dzen_url": DZEN_URL,
                 "status": "published_observed_in_feed"},
                {"title": TITLE, "dzen_url": "https://dzen.ru/a/DIFFERENT",
                 "status": "published_observed_in_feed"},
            ]}), encoding="utf-8")
            links = known_related_links([], path=path)
        self.assertEqual(links, {})

    def test_missing_catalog_is_safe_and_never_blocks_vk(self):
        links = known_related_links([], path=Path("/no-such-vk-catalog.json"))
        self.assertEqual(links, {})


if __name__ == "__main__":
    unittest.main()
