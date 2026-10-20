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


if __name__ == "__main__":
    unittest.main()
