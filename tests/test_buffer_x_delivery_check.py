"""Offline delivery audit tests: no Buffer API and no social publishing."""
import unittest
from unittest.mock import patch

import buffer_x_delivery_check as audit


class DeliveryAuditTests(unittest.TestCase):
    def test_reports_real_remote_error_without_resubmission(self):
        queue = {"items": [
            {"id": "story-one", "status": "queued", "buffer_post_id": "b1"},
            {"id": "story-two", "status": "queued", "buffer_post_id": "b2"},
            {"id": "story-three", "status": "approved"},
        ]}
        result = audit.inspect(queue, [
            {"id": "b1", "status": "error"},
            {"id": "b2", "status": "sent"},
        ])
        self.assertEqual(result["buffer_errors"], ["story-one"])
        self.assertEqual(result["buffer_sent"], 1)
        self.assertEqual(result["unverified"], 0)

    def test_unknown_is_not_automatically_an_error(self):
        q = {"items": [{"id": "missing", "status": "queued", "buffer_post_id": "unknown"}]}
        result = audit.inspect(q, [])
        self.assertEqual(result["buffer_errors"], [])
        self.assertEqual(result["unverified"], 1)

    def test_remote_query_is_bounded_and_read_only(self):
        with patch.object(audit, "graphql", return_value={"posts": {
            "edges": [{"node": {"id": "b", "status": "sent", "text": "hello"}}],
            "pageInfo": {"hasNextPage": True}}}) as graph:
            posts, more = audit.recent_remote_posts("org", "channel")
        self.assertEqual(len(posts), 1)
        self.assertTrue(more)
        request = graph.call_args.args[0]
        self.assertIn("first: 100", request)
        self.assertNotIn("createPost", request)
        self.assertIn("status: [sent, error, scheduled, sending]", request)


if __name__ == "__main__":
    unittest.main()
