"""No-network tests for safe moving-main state checkpoints and conflicts."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import state_checkpoint as cp


def fixture():
    return {
        "seen_articles.json": ["old-tg"],
        "seen_vk_articles.json": ["old-vk"],
        "seen_instagram_articles.json": ["old-ig"],
        "seen_threads_articles.json": ["old-thread"],
        "instagram_pending_articles.json": [{
            "id": "future-ig", "title": "Queued", "date": 1
        }],
        "instagram_cover_inbox.json": {
            "schema_version": 1, "offset": 42, "channel_id": -123, "covers": {}
        },
        "threads_editorial_queue.json": {
            "schema_version": 1, "items": [{
                "id": "approved-story-001",
                "dzen_title": "A sample approved story headline",
                "threads_text": "Author-written text.",
                "status": "approved",
            }]
        },
        "x_editorial_queue.json": {
            "schema_version": 1, "items": [{
                "id": "approved-x-001",
                "dzen_title": "An X sample approved story headline",
                "x_text": "Approved X text.",
                "status": "approved",
            }]
        },
        "editorial_archive.json": {
            "schema_version": 1, "updated_at_utc": "2026-10-20T00:00:00+00:00",
            "legacy_seen_ids_count": 1, "scope": "public feed",
            "entries": [],
        },
        "meta_token_refresh_state.json": {
            "instagram": {"last_attempt": None, "last_success": "2026-10-19T00:00:00+00:00"},
            "threads": {"last_attempt": None, "last_success": "2026-10-19T00:00:00+00:00"},
        },
    }


def run(*args, cwd):
    p = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=20)
    if p.returncode:
        raise AssertionError(f"{args}: {p.stderr[-600:]}")
    return p.stdout.strip()


class MergeTests(unittest.TestCase):
    def test_committed_production_state_preserves_queue_and_delivery_ids(self):
        # Exercise the same real-data validation that initialize() runs before
        # publishers, without invoking git, saving state or contacting services.
        state = cp.local_states()
        original = copy.deepcopy(state)
        merged = cp.merge_states(state, state, state)
        queue_name = "threads_editorial_queue.json"
        self.assertEqual(
            {row["id"]: row for row in merged[queue_name]["items"]},
            {row["id"]: row for row in original[queue_name]["items"]})
        for name in cp.IDS:
            self.assertTrue(set(original[name]).issubset(merged[name]), name)
        for row in original[queue_name]["items"]:
            if row["status"] == "published":
                self.assertIn(str(row["dzen_id"]), merged["seen_threads_articles.json"])
        self.assertEqual(state, original)

    def test_id_union_and_independent_editorial_changes(self):
        base = fixture()
        local, remote = copy.deepcopy(base), copy.deepcopy(base)
        local["seen_threads_articles.json"].append("new-thread")
        local["threads_editorial_queue.json"]["items"][0].update(
            status="published", dzen_id="new-thread", threads_post_id="thread-id")
        remote["threads_editorial_queue.json"]["items"].append({
            "id": "new-editor-story-02",
            "dzen_title": "A completely separate editor headline",
            "threads_text": "Approved after runner started.", "status": "approved"})
        remote["seen_articles.json"].append("new-editor-tg")
        remote["instagram_cover_inbox.json"]["covers"]["cover-key"] = {
            "title": "Example image", "file": "instagram_covers/abc-5.jpg"}
        local["instagram_cover_inbox.json"]["offset"] = 50
        merged = cp.merge_states(base, local, remote)
        self.assertEqual(merged["seen_articles.json"], ["new-editor-tg", "old-tg"])
        self.assertIn("new-thread", merged["seen_threads_articles.json"])
        self.assertEqual(len(merged["threads_editorial_queue.json"]["items"]), 2)
        self.assertEqual(merged["instagram_cover_inbox.json"]["offset"], 50)
        self.assertIn("cover-key", merged["instagram_cover_inbox.json"]["covers"])
        self.assertEqual(len(merged["x_editorial_queue.json"]["items"]), 1)

    def test_invalid_editorial_lengths_cannot_halt_other_publishers(self):
        base = fixture()
        base["threads_editorial_queue.json"]["items"][0]["threads_text"] = "Т" * 501
        base["x_editorial_queue.json"]["items"][0]["x_text"] = "И" * 281
        # Global checkpoint must still validate schema, preserve every byte of
        # approved editorial content and allow Telegram/VK to proceed.
        merged = cp.merge_states(base, base, base)
        self.assertEqual(merged["threads_editorial_queue.json"], base["threads_editorial_queue.json"])
        self.assertEqual(merged["x_editorial_queue.json"], base["x_editorial_queue.json"])
        from threads_editorial_queue import validate_queue as threads_validate
        from x_editorial_queue import validate_queue as x_validate
        with self.assertRaisesRegex(RuntimeError, "500"):
            threads_validate(base["threads_editorial_queue.json"])
        with self.assertRaisesRegex(RuntimeError, "280"):
            x_validate(base["x_editorial_queue.json"])

    def test_pending_queue_removes_delivered_items(self):
        base = fixture()
        local, remote = copy.deepcopy(base), copy.deepcopy(base)
        local["seen_instagram_articles.json"].append("future-ig")
        self.assertFalse(cp.merge_states(base, local, remote)["instagram_pending_articles.json"])

    def test_same_record_concurrent_edits_are_not_overwritten(self):
        base = fixture()
        local, remote = copy.deepcopy(base), copy.deepcopy(base)
        local["threads_editorial_queue.json"]["items"][0].update(
            status="published", dzen_id="done-thread", threads_post_id="a")
        remote["threads_editorial_queue.json"]["items"][0]["threads_text"] = "Newly edited by human"
        with self.assertRaises(cp.Conflict):
            cp.merge_states(base, local, remote)
        with tempfile.TemporaryDirectory() as tmp:
            saved = cp.conflict_recovery(base, local, remote, Path(tmp), "Concurrent")
            self.assertEqual(saved["threads_editorial_queue.json"]["items"][0]["threads_text"],
                             "Newly edited by human")
            self.assertIn("done-thread", saved["seen_threads_articles.json"])
            self.assertEqual(saved["threads_editorial_queue.json"]["items"][0]["status"], "approved")
            self.assertTrue((Path(tmp) / "conflict.json").exists())

    def test_atomics_leave_full_json_and_no_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "state.json"
            cp.atomic_json(p, {"a": "текст"})
            self.assertEqual(json.loads(p.read_text()), {"a": "текст"})
            self.assertEqual([x.name for x in p.parent.iterdir()], ["state.json"])


class GitIntegrationTests(unittest.TestCase):
    """Uses only temporary LOCAL bare git repositories; cannot post social content."""
    def test_live_editor_commit_preserved_and_ids_merged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare = root / "origin.git"
            runner = root / "runner"
            editor = root / "editor"
            cache = root / "checkpoint-cache"
            run("git", "init", "--bare", str(bare), cwd=root)
            run("git", "init", "-b", "main", str(runner), cwd=root)
            run("git", "config", "user.name", "Fixture", cwd=runner)
            run("git", "config", "user.email", "fixture@example.org", cwd=runner)
            for name, value in fixture().items():
                cp.atomic_json(runner / name, value)
            (runner / "code.py").write_text("version = 'original'\n")
            run("git", "add", "-A", cwd=runner)
            run("git", "commit", "-m", "initial", cwd=runner)
            run("git", "remote", "add", "origin", str(bare), cwd=runner)
            run("git", "push", "-u", "origin", "main", cwd=runner)
            run("git", "clone", "-b", "main", str(bare), str(editor), cwd=root)
            run("git", "config", "user.name", "Editor", cwd=editor)
            run("git", "config", "user.email", "editor@example.org", cwd=editor)

            with patch.object(cp, "ROOT", runner), patch.object(cp, "CACHE", cache):
                cp.initialize()
                value = json.loads((runner / "seen_articles.json").read_text())
                value.append("sent-tg-from-old-checkout")
                cp.atomic_json(runner / "seen_articles.json", value)

                # Concurrent editor updates main AFTER runner initialized.
                (editor / "code.py").write_text("version = 'NEW EDITOR CODE'\n")
                q = json.loads((editor / "threads_editorial_queue.json").read_text())
                q["items"].append({
                    "id": "future-story-002",
                    "dzen_title": "Second approved Dzen article",
                    "threads_text": "Edited by another user.", "status": "approved"})
                cp.atomic_json(editor / "threads_editorial_queue.json", q)
                seen = json.loads((editor / "seen_articles.json").read_text())
                seen.append("seen-remotely")
                cp.atomic_json(editor / "seen_articles.json", seen)
                run("git", "add", ".", cwd=editor)
                run("git", "commit", "-m", "Editor updated main", cwd=editor)
                run("git", "push", "origin", "main", cwd=editor)

                cp.save("test")
                cp.save("repeat")  # A second checkpoint must be harmless.

            run("git", "fetch", "origin", cwd=editor)
            saved = json.loads(run("git", "show", "origin/main:seen_articles.json", cwd=editor))
            self.assertEqual(set(saved), {"old-tg", "sent-tg-from-old-checkout", "seen-remotely"})
            source = run("git", "show", "origin/main:code.py", cwd=editor)
            self.assertIn("NEW EDITOR CODE", source)
            published = json.loads(run("git", "show", "origin/main:threads_editorial_queue.json",
                                       cwd=editor))
            self.assertEqual(len(published["items"]), 2)
            self.assertEqual(json.loads((runner / "seen_articles.json").read_text()), saved)
            self.assertTrue((cache / "recovery" / "test" / "seen_articles.json").exists())


    def test_push_rejected_then_retried_without_losing_local_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bare, runner, editor = (root / x for x in ("origin.git", "runner", "editor"))
            cache = root / "checkpoint-cache"
            run("git", "init", "--bare", str(bare), cwd=root)
            run("git", "init", "-b", "main", str(runner), cwd=root)
            for key, val in (("user.name", "Fixture"), ("user.email", "fixture@example.org")):
                run("git", "config", key, val, cwd=runner)
            for name, data in fixture().items():
                cp.atomic_json(runner / name, data)
            (runner / "app.py").write_text("safe_code = True\n")
            run("git", "add", "-A", cwd=runner)
            run("git", "commit", "-m", "initial", cwd=runner)
            run("git", "remote", "add", "origin", str(bare), cwd=runner)
            run("git", "push", "-u", "origin", "main", cwd=runner)
            run("git", "clone", "-b", "main", str(bare), str(editor), cwd=root)
            for key, val in (("user.name", "Editor"), ("user.email", "editor@example.org")):
                run("git", "config", key, val, cwd=editor)

            with patch.object(cp, "ROOT", runner), patch.object(cp, "CACHE", cache):
                cp.initialize()
                ids = json.loads((runner / "seen_vk_articles.json").read_text())
                ids.append("vk-sent-before-race")
                cp.atomic_json(runner / "seen_vk_articles.json", ids)
                image_name = "0123456789abcdef0123-23.jpg"
                image_path = runner / "instagram_covers" / image_name
                cp.atomic_bytes(image_path, b"\xff\xd8\xffMOCKJPEG")
                inbox = json.loads((runner / "instagram_cover_inbox.json").read_text())
                inbox["offset"] = 43
                inbox["covers"]["new-cover"] = {
                    "title": "New cover", "file": "instagram_covers/" + image_name
                }
                cp.atomic_json(runner / "instagram_cover_inbox.json", inbox)

                # The editor is ready to push in the race window. It moves
                # main EXACTLY when our first push starts, forcing a rejection.
                (editor / "app.py").write_text("safe_code = True\neditor_update = 42\n")
                run("git", "add", ".", cwd=editor)
                run("git", "commit", "-m", "editor commit during race", cwd=editor)
                actual_run = subprocess.run
                injected = [False]

                def raced_run(args, **kwargs):
                    if (not injected[0] and args[:2] == ["git", "push"]
                            and Path(kwargs.get("cwd") or "") == runner):
                        injected[0] = True
                        response = actual_run(["git", "push", "origin", "main"],
                                              cwd=editor, capture_output=True)
                        if response.returncode:
                            raise AssertionError(response.stderr)
                    return actual_run(args, **kwargs)

                with patch.object(cp.subprocess, "run", side_effect=raced_run):
                    cp.save("covers")
                self.assertTrue(injected[0])

            run("git", "fetch", "origin", cwd=editor)
            data = json.loads(run("git", "show", "origin/main:seen_vk_articles.json", cwd=editor))
            self.assertIn("vk-sent-before-race", data)
            self.assertIn("editor_update = 42",
                          run("git", "show", "origin/main:app.py", cwd=editor))
            inbox_remote = json.loads(run("git", "show", "origin/main:instagram_cover_inbox.json",
                                          cwd=editor))
            self.assertEqual(inbox_remote["offset"], 43)
            self.assertIn("new-cover", inbox_remote["covers"])
            image = actual_run(
                ["git", "show", "origin/main:instagram_covers/" + image_name],
                cwd=editor, capture_output=True)
            self.assertEqual(image.stdout, b"\xff\xd8\xffMOCKJPEG")


if __name__ == "__main__":
    unittest.main()
