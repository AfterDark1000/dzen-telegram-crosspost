#!/usr/bin/env python3
"""Offline safety tests: never contact Meta or GitHub."""
import base64
import datetime as dt
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import meta_token_refresh as m


class TokenRefreshTests(unittest.TestCase):
    def test_refreshed_meta_token_identity_and_expiry(self):
        secret = "ABCDEF-1234567890-long-lived-token"
        fake = [
            ({"access_token": secret, "expires_in": 5184000}, 200),
            ({"username": "iipike.ai", "id": "12345"}, 200),
        ]
        with patch.object(m, "request_json", side_effect=fake) as req:
            refreshed, validity = m.refresh_meta_token("threads", "EXISTING-SECRET")
        self.assertEqual(refreshed, secret)
        self.assertEqual(validity, 5184000)
        self.assertIn("th_refresh_token", req.call_args_list[0].args[0])

    def test_wrong_identity_refused(self):
        response = [
            ({"access_token": "ABCDEFGHIJKLMNOPQRSTUVWXY", "expires_in": 5184000}, 200),
            ({"username": "unexpected", "id": "222"}, 200),
        ]
        with patch.object(m, "request_json", side_effect=response):
            with self.assertRaisesRegex(RuntimeError, "wrong profile"):
                m.refresh_meta_token("instagram", "OLD-SECRET")

    def test_short_expiry_refused(self):
        with patch.object(m, "request_json", return_value=(
            {"access_token": "ABCDEFGHIJKLMNOPQRSTUVWXY", "expires_in": 3600}, 200
        )):
            with self.assertRaisesRegex(RuntimeError, "short expiry"):
                m.refresh_meta_token("threads", "OLD-SECRET")

    def test_github_secret_encrypted_only(self):
        from nacl.public import PrivateKey, SealedBox
        sk = PrivateKey.generate()
        fake_key = base64.b64encode(bytes(sk.public_key)).decode("ascii")
        token = "REAL-NEW-SECRET"
        requests = []
        def api(path, pat, method="GET", payload=None):
            requests.append((path, method, payload))
            if method == "GET":
                return {"key": fake_key, "key_id": "key1"}, 200
            return {}, 204
        with patch.object(m, "github_call", side_effect=api):
            m.store_github_secret("THREADS_ACCESS_TOKEN", token, "FAKE_PAT")
        self.assertEqual(requests[1][1], "PUT")
        self.assertNotIn(token, json.dumps(requests[1][2]))
        ciphertext = base64.b64decode(requests[1][2]["encrypted_value"])
        self.assertEqual(SealedBox(sk).decrypt(ciphertext).decode("utf-8"), token)

    def test_http_error_does_not_leak_token(self):
        secret = "HIGHLY-SENSITIVE-SAMPLE-VALUE"
        def bad(_req, timeout):
            raise urllib.error.HTTPError("https://example.com/?access_token=" + secret,
                                         400, "invalid", {}, None)
        with patch("urllib.request.urlopen", side_effect=bad):
            with self.assertRaises(RuntimeError) as cm:
                m.request_json("https://example.com/?access_token=" + secret)
        self.assertNotIn(secret, str(cm.exception))

    def test_recent_tokens_not_refreshed(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "state.json"
            now = dt.datetime.now(dt.timezone.utc)
            state = {name: {"last_success": m.now_string(now - dt.timedelta(days=2)),
                            "last_attempt": None}
                     for name in m.ACCOUNTS}
            path.write_text(json.dumps(state), encoding="utf-8")
            with patch.object(m, "STATE", path), patch.object(m, "refresh_meta_token",
                                                               side_effect=AssertionError("too early")):
                m.main()
            self.assertEqual(json.loads(path.read_text()), state)

    def test_recent_failed_attempt_throttles_retry(self):
        with tempfile.TemporaryDirectory() as work:
            path = Path(work) / "state.json"
            now = dt.datetime.now(dt.timezone.utc)
            state = {name: {"last_success": m.now_string(now - dt.timedelta(days=29)),
                            "last_attempt": m.now_string(now - dt.timedelta(hours=1))}
                     for name in m.ACCOUNTS}
            path.write_text(json.dumps(state), encoding="utf-8")
            with patch.object(m, "STATE", path), patch.object(m, "refresh_meta_token",
                                                               side_effect=AssertionError("retry too early")):
                m.main()
            self.assertEqual(json.loads(path.read_text()), state)


if __name__ == "__main__":
    unittest.main()
