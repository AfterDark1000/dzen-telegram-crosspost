"""A single editorial diary article stays on Dzen, never reaching crossposters."""
import json
import unittest

from crosspost import extract_items, is_dzen_only_url


TARGET = "https://dzen.ru/a/auL2PIuIqglEYkgF"
OTHER = "https://dzen.ru/a/auL2PIuIqglEYkgG"


def feed(*articles):
    return json.dumps({"feedData": {"items": [
        {"type": "article", "id": str(i), "title": title,
         "shareLink": url, "text": "Article body", "publicationDate": 1793200000}
        for i, (url, title) in enumerate(articles, 1)
    ]}}, ensure_ascii=False)


class DzenOnlyPolicyTests(unittest.TestCase):
    def test_exact_article_url_is_excluded(self):
        self.assertTrue(is_dzen_only_url(TARGET))
        self.assertTrue(is_dzen_only_url(TARGET + "/?from=feed"))
        for url in (OTHER, "https://dzen.ru/a/another",
                    "https://dzen.ru.evil.test/a/auL2PIuIqglEYkgF",
                    "http://dzen.ru/a/auL2PIuIqglEYkgF", ""):
            self.assertFalse(is_dzen_only_url(url))

    def test_target_filtered_but_unrelated_articles_pass(self):
        result = extract_items(feed((TARGET, "Дневник канала"),
                                    (OTHER, "Другая статья")))
        self.assertEqual([x["url"] for x in result], [OTHER])
        self.assertEqual(result[0]["title"], "Другая статья")

    def test_all_excluded_is_empty_feed_not_parse_error(self):
        self.assertEqual(extract_items(feed((TARGET, "Дневник канала"))), [])

    def test_unrelated_diary_with_distinct_url_is_not_blocked(self):
        result = extract_items(feed((OTHER, "Дневник канала")))
        self.assertEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
