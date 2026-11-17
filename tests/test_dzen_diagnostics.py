"""The failed Dzen fetch should explain what page arrived, without exposing it."""
import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import crosspost


class DzenPageDiagnosticTests(unittest.TestCase):
    def test_captcha_page_omits_queries_cookies_and_raw_html(self):
        page = """<!doctype html><head><title>  Security check
        &amp; captcha  </title></head><body>hcaptcha Access denied
        <div>verify you are human</div> private-script-token-123</body>"""
        result = crosspost.describe_dzen_failure(
            final_url="https://user:password@dzen.ru/iipike?tab=articles&token=abc#secret",
            http_status=403, source=page)
        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["final_url"], "https://dzen.ru/iipike")
        self.assertEqual(result["page_title"], "Security check & captcha")
        self.assertTrue(result["captcha_hint"])
        self.assertTrue(result["access_denied_hint"])
        self.assertTrue(result["challenge_hint"])
        self.assertFalse(result["feed_marker_present"])
        output = json.dumps(result)
        for secret in ("abc", "password", "private-script-token-123", "tab=articles", "#secret"):
            self.assertNotIn(secret, output)

    def test_normal_page_without_feed_is_not_treated_as_captcha(self):
        page = "<title>Дзен — ХПИИ</title><body>Новости</body>"
        result = crosspost.describe_dzen_failure(
            final_url="https://dzen.ru/iipike", http_status=200, source=page)
        self.assertEqual(result["page_title"], "Дзен — ХПИИ")
        self.assertEqual(result["html_chars"], len(page))
        self.assertFalse(result["feed_marker_present"])
        self.assertFalse(result["captcha_hint"])
        self.assertFalse(result["challenge_hint"])
        self.assertFalse(result["access_denied_hint"])

    def test_missing_or_invalid_url_never_reveals_credentials(self):
        for value in ("", "about:blank", "https://[invalid"):
            result = crosspost.describe_dzen_failure(
                final_url=value, http_status=None, source="")
            self.assertEqual(result["final_url"], "<unavailable>")
            self.assertIsNone(result["http_status"])

    def test_content_is_not_logged_even_with_feed_marker(self):
        page = '<title>Site</title>feedData https://dzen.ru/a/123 private-secret'
        result = crosspost.describe_dzen_failure(
            final_url="https://dzen.ru/iipike?nonce=hidden",
            http_status=200, source=page)
        self.assertTrue(result["feed_marker_present"])
        self.assertTrue(result["article_url_present"])
        self.assertNotIn("private-secret", json.dumps(result))
        self.assertNotIn("hidden", json.dumps(result))


class DzenFetchDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    def browser_fixture(self, html):
        page = MagicMock()
        page.url = "https://dzen.ru/iipike?tab=articles&secret=no-logging"
        page.goto = AsyncMock(return_value=SimpleNamespace(status=200))
        page.content = AsyncMock(return_value=html)
        page.wait_for_timeout = AsyncMock()
        page.close = AsyncMock()
        browser = MagicMock()
        browser.new_page = AsyncMock(return_value=page)
        browser.close = AsyncMock()
        playwright = MagicMock()
        playwright.chromium.launch = AsyncMock(return_value=browser)
        manager = AsyncMock()
        manager.__aenter__.return_value = playwright
        return page, browser, manager

    async def test_three_failures_log_safe_snapshot_without_touching_delivery(self):
        page, browser, manager = self.browser_fixture(
            "<html><title>Подтвердите, что вы человек</title>"
            "<body>hcaptcha secret-in-body</body></html>")
        output = io.StringIO()
        with patch.object(crosspost, "async_playwright", return_value=manager), \
             patch.object(crosspost.asyncio, "sleep", new_callable=AsyncMock), \
             redirect_stdout(output):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                await crosspost.fetch_articles()
        logs = output.getvalue().splitlines()
        diagnosed = [json.loads(x.removeprefix("DZEN_PAGE_DIAG "))
                     for x in logs if x.startswith("DZEN_PAGE_DIAG ")]
        self.assertEqual(len(diagnosed), 3)
        self.assertEqual(page.goto.await_count, 3)
        self.assertEqual(page.close.await_count, 3)
        browser.close.assert_awaited_once()
        self.assertTrue(all(x["captcha_hint"] for x in diagnosed))
        self.assertTrue(all(x["http_status"] == 200 for x in diagnosed))
        self.assertNotIn("secret-in-body", output.getvalue())
        self.assertNotIn("secret=no-logging", output.getvalue())

    async def test_successful_page_has_no_diagnostics(self):
        html = json.dumps({"feedData": {"items": [
            {"type": "article", "id": "a123", "title": "Test article",
             "shareLink": "https://dzen.ru/a/a123", "publicationDate": 42}]}})
        page, browser, manager = self.browser_fixture(html)
        output = io.StringIO()
        with patch.object(crosspost, "async_playwright", return_value=manager), \
             redirect_stdout(output):
            articles = await crosspost.fetch_articles()
        self.assertEqual([x["title"] for x in articles], ["Test article"])
        self.assertNotIn("DZEN_PAGE_DIAG", output.getvalue())
        self.assertEqual(page.goto.await_count, 1)
        browser.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
