#!/usr/bin/env python3
"""Safely checkpoint ONLY crosspost state on a moving main branch.

No git reset, rebase or force-push. A temporary index constructs a new commit
on top of the latest remote main, leaving the running checkout/code intact.
Editor changes to the same record are never overwritten silently. Published
IDs are monotonically merged even when an editorial record conflicts.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
CACHE = Path(os.getenv("RUNNER_TEMP", tempfile.gettempdir())) / "hpi-state"
IDS = ("seen_articles.json", "seen_vk_articles.json",
       "seen_instagram_articles.json", "seen_threads_articles.json")
FILES = IDS + ("instagram_pending_articles.json", "instagram_cover_inbox.json",
               "threads_editorial_queue.json", "x_editorial_queue.json",
               "editorial_archive.json", "meta_token_refresh_state.json")
ABSENT = object()


class Conflict(ValueError):
    pass


def atomic_bytes(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as f:
            tmp = f.name
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)


def atomic_json(path, obj):
    atomic_bytes(path, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def git(*args, data=None, env=None):
    result = subprocess.run(["git", *args], cwd=ROOT, input=data,
                            capture_output=True, env=env, timeout=65)
    if result.returncode:
        raise RuntimeError("git " + args[0] + " failed: " +
                           result.stderr.decode("utf-8", "replace")[:180])
    return result.stdout


def at(ref, path):
    if not git("ls-tree", ref, "--", path):
        return ABSENT
    return git("show", ref + ":" + path)


def choose(base, local, remote, label):
    if local == remote or remote == base:
        return local
    if local == base:
        return remote
    raise Conflict("Conflicting concurrent state: " + label)


def merge_dict(base, local, remote, label):
    try:
        return choose(base, local, remote, label)
    except Conflict:
        if not all(isinstance(x, dict) for x in (base, local, remote)):
            raise
    result = {}
    for k in sorted(set(base) | set(local) | set(remote)):
        v = merge_dict(base.get(k, ABSENT), local.get(k, ABSENT),
                       remote.get(k, ABSENT), label + "/" + str(k))
        if v is not ABSENT:
            result[k] = v
    return result


def rows(values, key):
    if not isinstance(values, list):
        raise ValueError("Expected list of records keyed by " + key)
    result = {}
    for row in values:
        if (not isinstance(row, dict) or not isinstance(row.get(key), str)
                or not row[key] or row[key] in result):
            raise ValueError("Missing or duplicate record " + key)
        result[row[key]] = row
    return result


def merge_rows(base, local, remote, key, label, exclude=()):
    maps = [{k: v for k, v in rows(a, key).items() if k not in exclude}
            for a in (base, local, remote)]
    out = []
    for ident in sorted(set().union(*(set(m) for m in maps))):
        value = choose(*(m.get(ident, ABSENT) for m in maps), label + "/" + ident)
        if value is not ABSENT:
            out.append(value)
    return out


def checked_ids(states, filename):
    lists = [state[filename] for state in states]
    for values in lists:
        if not isinstance(values, list) or not all(
                isinstance(x, str) and bool(x) for x in values):
            raise ValueError("Invalid posted ID list: " + filename)
    return sorted(set().union(*(set(x) for x in lists)))


def merge_states(base, local, remote):
    states = (base, local, remote)
    out = {name: checked_ids(states, name) for name in IDS}

    name = "instagram_pending_articles.json"
    out[name] = sorted(
        merge_rows(*(s[name] for s in states), "id", name,
                   set(out["seen_instagram_articles.json"])),
        key=lambda row: (int(row.get("date") or 0), row["id"]))

    name = "threads_editorial_queue.json"
    out[name] = merge_dict(
        *({key: val for key, val in s[name].items() if key != "items"}
          for s in states), name)
    out[name]["items"] = merge_rows(
        *(s[name]["items"] for s in states), "id", name)

    from threads_editorial_queue import validate_queue
    validate_queue(out[name])
    # The queue may report a successful publish before the ID list is flushed.
    delivered = {str(row["dzen_id"]) for row in out[name]["items"]
                 if row["status"] == "published"}
    out["seen_threads_articles.json"] = sorted(
        set(out["seen_threads_articles.json"]) | delivered)

    name = "x_editorial_queue.json"
    out[name] = merge_dict(
        *({key: val for key, val in s[name].items() if key != "items"}
          for s in states), name)
    out[name]["items"] = merge_rows(
        *(s[name]["items"] for s in states), "id", name)
    from x_editorial_queue import validate_queue as validate_x_queue
    validate_x_queue(out[name])

    name = "instagram_cover_inbox.json"
    out[name] = merge_dict(
        *({key: val for key, val in s[name].items() if key != "offset"}
          for s in states), name)
    offsets = [s[name]["offset"] for s in states]
    if not all(type(x) is int and x >= 0 for x in offsets):
        raise ValueError("Invalid cover inbox offset")
    out[name]["offset"] = max(offsets)

    name = "editorial_archive.json"
    out[name] = merge_dict(
        *({key: val for key, val in s[name].items()
           if key not in ("entries", "updated_at_utc", "legacy_seen_ids_count")}
          for s in states), name)
    out[name]["entries"] = merge_rows(
        *(s[name]["entries"] for s in states), "key", name)
    out[name]["entries"].sort(key=lambda x: (
        x.get("published_at_utc") or x.get("first_recorded_at_utc") or "",
        x["key"]), reverse=True)
    out[name]["updated_at_utc"] = max(
        s[name].get("updated_at_utc", "") for s in states)
    out[name]["legacy_seen_ids_count"] = len(out["seen_articles.json"])

    name = "meta_token_refresh_state.json"
    out[name] = merge_dict(*(s[name] for s in states), name)
    return out


def local_states():
    return {name: json.loads((ROOT / name).read_text(encoding="utf-8"))
            for name in FILES}


def states_at(ref):
    return {name: json.loads(git("show", ref + ":" + name)) for name in FILES}


def initialize():
    data = local_states()
    merge_states(data, data, data)  # Validate existing state before any publication.
    CACHE.mkdir(parents=True, exist_ok=True)
    atomic_bytes(CACHE / "base", git("rev-parse", "HEAD").strip())
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")


def preserve_local(phase, baseline):
    target = CACHE / "recovery" / phase
    target.mkdir(parents=True, exist_ok=True)
    atomic_bytes(target / "base", baseline.encode("utf-8"))
    for name in FILES:
        if (ROOT / name).is_file():
            shutil.copy2(ROOT / name, target / name)
    for image in (ROOT / "instagram_covers").glob("*.jpg"):
        if not re.fullmatch(r"[0-9a-f]{20}-[0-9]+\.jpg", image.name):
            raise ValueError("Unexpected Instagram cover filename")
        dest = target / "instagram_covers" / image.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image, dest)
    return target


def conflict_recovery(base, local, remote, recovery, why):
    """Don't overwrite editorial edits: save *delivery evidence* and flag conflict."""
    merged = json.loads(json.dumps(remote))
    states = (base, local, remote)
    for name in IDS:
        merged[name] = checked_ids(states, name)
    local_delivered = {
        str(row["dzen_id"])
        for row in local["threads_editorial_queue.json"]["items"]
        if row.get("status") == "published" and row.get("dzen_id")}
    merged["seen_threads_articles.json"] = sorted(
        set(merged["seen_threads_articles.json"]) | local_delivered)
    merged["instagram_pending_articles.json"] = [
        row for row in remote["instagram_pending_articles.json"]
        if row["id"] not in merged["seen_instagram_articles.json"]]
    atomic_json(recovery / "conflict.json", {"reason": why,
                                            "requires_manual_reconciliation": True})
    return merged


def save(phase):
    if not re.fullmatch(r"[a-z-]+", phase):
        raise ValueError("Invalid checkpoint phase")
    baseline = (CACHE / "base").read_text(encoding="utf-8").strip()
    recovery = preserve_local(phase, baseline)
    base, local = states_at(baseline), local_states()

    covers = {}
    for image in (ROOT / "instagram_covers").glob("*.jpg"):
        name = image.relative_to(ROOT).as_posix()
        if not re.fullmatch(r"instagram_covers/[0-9a-f]{20}-[0-9]+\.jpg", name):
            raise ValueError("Invalid Instagram cover name")
        data = image.read_bytes()
        prior = at(baseline, name)
        if prior != data:
            covers[name] = (prior, data)

    for attempt in range(5):
        git("fetch", "--no-tags", "origin",
            "+refs/heads/main:refs/remotes/origin/main")
        head = git("rev-parse", "refs/remotes/origin/main").decode().strip()
        atomic_bytes(recovery / "remote", head.encode("utf-8"))
        remote = states_at(head)
        conflict = None

        try:
            merged_covers = {
                name: choose(prior, value, at(head, name), name)
                for name, (prior, value) in covers.items()
            }
            merged = merge_states(base, local, remote)
        except Conflict as e:
            conflict = str(e)
            merged_covers = {}
            merged = conflict_recovery(base, local, remote, recovery, conflict)

        # Stage only state and image blobs on latest main (NOT stale code).
        with tempfile.TemporaryDirectory(prefix="hpi-git-index-") as temp:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / "index"))
            git("read-tree", head, env=env)
            blobs = {
                name: (json.dumps(value, ensure_ascii=False, indent=2) +
                       "\n").encode("utf-8") for name, value in merged.items()
            }
            blobs.update(merged_covers)
            for name, raw in blobs.items():
                sha = git("hash-object", "-w", "--stdin",
                          data=raw).decode().strip()
                git("update-index", "--add", "--cacheinfo",
                    "100644", sha, name, env=env)
            tree = git("write-tree", env=env).decode().strip()

        if tree == git("rev-parse", head + "^{tree}").decode().strip():
            candidate, ok = head, True
        else:
            candidate = git(
                "commit-tree", tree, "-p", head, "-m",
                "Save crosspost state (" + phase + ") [skip ci]"
            ).decode().strip()
            result = subprocess.run(
                ["git", "push", "origin", candidate + ":refs/heads/main"],
                cwd=ROOT, capture_output=True, timeout=65)
            ok = result.returncode == 0

        if ok:
            for name, value in merged.items():
                atomic_json(ROOT / name, value)
            atomic_bytes(CACHE / "base", candidate.encode("utf-8"))
            print("State checkpoint committed:", phase, candidate[:12], flush=True)
            if conflict:
                # Queue conflict: never silently claim the editor's change was published.
                raise Conflict(conflict + "; confirmed delivery IDs saved. "
                               "Restore editorial record from recovery artifact.")
            return

        print("Concurrent main update or push failed; retry checkpoint:",
              attempt + 1, flush=True)
        if attempt < 4:
            time.sleep(attempt + 1)
    raise RuntimeError("Checkpoint push failed after 5 tries; recovery artifact available")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["init"]:
            initialize()
        elif len(sys.argv) == 3 and sys.argv[1] == "save":
            save(sys.argv[2])
        else:
            raise ValueError("Usage: state_checkpoint.py init | save PHASE")
    except Exception as error:
        print("STATE CHECKPOINT ERROR: " + str(error), file=sys.stderr)
        sys.exit(1)
