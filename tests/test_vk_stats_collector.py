"""Offline regression tests for the read-only VK stats collector."""
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

import vk_stats_collector as vk


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 3, 5, 0, tzinfo=UTC)
ENV = {"VK_ACCESS_TOKEN": "not-a-real-token"}


class VKStatsTests(unittest.TestCase):
    def test_members_supports_both_api_response_shapes(self):
        def old_api(method, token, **params):
            return [{"id": vk.GROUP_ID, "members_count": 3}]
        def new_api(method, token, **params):
            return {"groups": [{"id": vk.GROUP_ID, "members_count": 4}]}
        self.assertEqual(vk.get_members(old_api, "fake")["members"], 3)
        self.assertEqual(vk.get_members(new_api, "fake")["members"], 4)

    def test_member_fallback_to_member_count(self):
        calls = []
        def api(method, token, **params):
            calls.append(method)
            return {"groups": [{}]} if method == "groups.getById" else {"count": 2, "items": [999]}
        self.assertEqual(vk.get_members(api, "fake")["members"], 2)
        self.assertEqual(calls, ["groups.getById", "groups.getMembers"])

    def test_collector_survives_denied_wall_and_private_stats(self):
        called = []
        def api(method, token, **params):
            called.append(method)
            if method == "groups.getById":
                return [{"members_count": 1}]
            raise vk.VKError("api_denied", 27)
        result = vk.collect(api=api, environ=ENV, now=NOW)
        self.assertEqual(result["community"]["members"], 1)
        self.assertEqual(result["wall"], {"status": "unavailable", "reason": "api_denied", "api_error_code": 27})
        self.assertEqual(result["statistics"]["status"], "unavailable")
        self.assertIn("stats.get", called)
        self.assertNotIn("not-a-real-token", json.dumps(result))

    def test_wall_counts_and_missing_views_are_not_zeroed(self):
        def api(method, token, **params):
            return {"count": 2, "items": [
                {"id": 10, "owner_id": -vk.GROUP_ID, "date": 1793500000,
                 "text": "Headline\nParagraph", "views": {"count": 7},
                 "likes": {"count": 2}, "comments": {"count": 0},
                 "reposts": {"count": 1}},
                {"id": 11, "owner_id": -vk.GROUP_ID, "date": 1793501000,
                 "text": "Another headline", "likes": {"count": 0}},
            ]}
        result = vk.get_wall(api, "fake")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["observed_posts"], 2)
        self.assertEqual(result["posts"][1]["views"], 7)
        self.assertIsNone(result["posts"][0]["views"])
        self.assertEqual(result["posts"][0]["likes"], 0)
        self.assertEqual(result["posts"][0]["url"], "https://vk.com/wall-242308928_11")

    def test_stats_data_is_aggregated_only(self):
        x = vk.stats_row({
            "period_from": 1793500000,
            "reach": {"reach": 10, "reach_subscribers": 2, "reach_viral": 8},
            "visitors": {"visitors": 3, "views": 5},
            "activity": {"subscribed": 1, "unsubscribed": 0},
        })
        self.assertEqual(x["reach_total"], 10)
        self.assertEqual(x["visitors"], 3)
        self.assertEqual(x["subscriptions"], 1)
        self.assertIsInstance(x["date"], str)

    def test_all_denied_refuses_to_create_fake_daily_measurement(self):
        def api(method, token, **params):
            raise vk.VKError("api_denied", 27)
        with self.assertRaisesRegex(RuntimeError, "All VK read methods denied"):
            vk.collect(api=api, environ=ENV, now=NOW)

    def test_no_token_refuses_without_calling_api(self):
        def api(method, token, **params):
            self.fail("API called without token")
        with self.assertRaisesRegex(RuntimeError, "No VK API token"):
            vk.collect(api=api, environ={}, now=NOW)

    def test_tokens_selected_for_correct_methods(self):
        got = {}
        def api(method, token, **params):
            got[method] = token
            if method == "groups.getById":
                return [{"members_count": 3}]
            if method == "wall.get":
                return {"count": 0, "items": []}
            if method == "stats.get":
                return []
            raise AssertionError(method)
        result = vk.collect(api=api, environ={
            "VK_ACCESS_TOKEN": "write-secret",
            "VK_READ_ACCESS_TOKEN": "read-secret",
            "VK_STATS_ACCESS_TOKEN": "stats-secret",
        }, now=NOW)
        self.assertEqual(got, {
            "groups.getById": "write-secret",
            "wall.get": "read-secret",
            "stats.get": "stats-secret",
        })
        self.assertNotIn("write-secret", json.dumps(result))

    def test_snapshot_path_is_stable_and_can_be_read_for_monday_report(self):
        x = {"date": "2026-11-03", "schema_version": 1, "community_id": vk.GROUP_ID}
        with tempfile.TemporaryDirectory() as dir_name:
            base = Path(dir_name)
            p = vk.write_snapshot(x, root=base)
            self.assertEqual(p.name, "2026-11-03.json")
            self.assertEqual(json.loads(p.read_text())["community_id"], vk.GROUP_ID)
            self.assertEqual(p, vk.write_snapshot(x, root=base))


if __name__ == "__main__":
    unittest.main()
