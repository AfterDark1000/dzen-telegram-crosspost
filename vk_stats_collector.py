#!/usr/bin/env python3
"""Read-only daily VK community statistics for the HPII Monday report.

Only aggregated group metrics and public wall counters are saved.
VK publishing secrets are never printed or committed.
"""
import datetime as dt
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

GROUP_ID = 242308928
API_VERSION = "5.199"
ROOT = Path(__file__).resolve().parent
ZONE = ZoneInfo("Asia/Yekaterinburg")
MAX_WALL_POSTS = 300


class VKError(Exception):
    def __init__(self, kind, code=None):
        self.kind = kind
        self.code = code
        super().__init__(kind)


def vk_call(method, token, **params):
    if not token:
        raise VKError("missing_token")
    body = urllib.parse.urlencode({
        **params, "access_token": token, "v": API_VERSION
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.vk.com/method/" + method, data=body
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as reply:
            payload = json.load(reply)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        # Deliberately do not include exception text or request URLs: credentials
        # are supplied in POST data and must not appear in Actions logs.
        raise VKError("transport_or_json_error") from None
    if not isinstance(payload, dict):
        raise VKError("invalid_response")
    if "error" in payload:
        value = payload["error"]
        code = value.get("error_code") if isinstance(value, dict) else None
        raise VKError("api_denied", code if type(code) is int else None)
    if "response" not in payload:
        raise VKError("missing_response")
    return payload["response"]


def unavailable(error):
    result = {"status": "unavailable", "reason": error.kind}
    if error.code is not None:
        result["api_error_code"] = error.code
    return result


def count(value):
    return value if type(value) is int and value >= 0 else None


def nested_count(obj, *path):
    for part in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return count(obj)


def get_members(api, token):
    try:
        response = api(
            "groups.getById", token,
            group_ids=str(GROUP_ID), fields="members_count"
        )
        if isinstance(response, dict):
            rows = response.get("groups", [])
        else:
            rows = response
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            members = count(rows[0].get("members_count"))
            if members is not None:
                return {"status": "ok", "members": members}
        # Some token kinds omit members_count from groups.getById.
        response = api("groups.getMembers", token, group_id=GROUP_ID, count=1)
        members = nested_count(response, "count")
        if members is None:
            raise VKError("missing_member_count")
        return {"status": "ok", "members": members}
    except VKError as error:
        return unavailable(error)


def short_post(row):
    post_id = row.get("id")
    if type(post_id) is not int or post_id <= 0:
        return None
    owner_id = row.get("owner_id")
    if owner_id != -GROUP_ID or row.get("is_deleted") or row.get("is_archived"):
        return None
    stamp = row.get("date")
    if type(stamp) is not int or stamp <= 0:
        return None
    try:
        published = dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None
    first_line = str(row.get("text") or "").strip().splitlines()
    title = (first_line[0] if first_line else "").strip()[:160]
    return {
        "id": post_id,
        "url": "https://vk.com/wall-" + str(GROUP_ID) + "_" + str(post_id),
        "published_at_utc": published,
        "title": title,
        "views": nested_count(row, "views", "count"),
        "likes": nested_count(row, "likes", "count"),
        "comments": nested_count(row, "comments", "count"),
        "reposts": nested_count(row, "reposts", "count"),
    }


def get_wall(api, token):
    try:
        posts = []
        total = None
        for offset in range(0, MAX_WALL_POSTS, 100):
            response = api(
                "wall.get", token, owner_id=-GROUP_ID,
                filter="owner", offset=offset, count=100
            )
            if not isinstance(response, dict):
                raise VKError("invalid_wall_response")
            if total is None:
                total = count(response.get("count"))
            items = response.get("items")
            if not isinstance(items, list):
                raise VKError("invalid_wall_items")
            for row in items:
                if not isinstance(row, dict):
                    continue
                p = short_post(row)
                if p is not None:
                    posts.append(p)
            if not items or len(items) < 100 or (total is not None and offset + 100 >= total):
                break
        if total is None:
            raise VKError("missing_wall_count")
        return {
            "status": "ok",
            "total_posts": total,
            "observed_posts": len(posts),
            "posts": sorted(posts, key=lambda p: p["id"], reverse=True),
            "coverage_limit": MAX_WALL_POSTS,
        }
    except VKError as error:
        return unavailable(error)


def stats_row(day):
    if not isinstance(day, dict):
        return None
    when = day.get("period_from") or day.get("day") or day.get("period")
    if type(when) is int:
        try:
            when = dt.datetime.fromtimestamp(when, dt.timezone.utc).astimezone(ZONE).date().isoformat()
        except (OSError, ValueError, OverflowError):
            return None
    if not isinstance(when, str):
        return None
    if len(when) >= 10 and when[:10].count("-") == 2:
        when = when[:10]
    else:
        return None
    return {
        "date": when,
        "reach_total": nested_count(day, "reach", "reach"),
        "reach_subscribers": nested_count(day, "reach", "reach_subscribers"),
        "reach_viral": nested_count(day, "reach", "reach_viral"),
        "visitors": nested_count(day, "visitors", "visitors"),
        "profile_views": nested_count(day, "visitors", "views"),
        "subscriptions": nested_count(day, "activity", "subscribed"),
        "unsubscriptions": nested_count(day, "activity", "unsubscribed"),
    }


def get_private_stats(api, token, local_day):
    start = local_day - dt.timedelta(days=13)
    try:
        response = api(
            "stats.get", token, group_id=GROUP_ID,
            date_from=start.isoformat(), date_to=local_day.isoformat()
        )
        if not isinstance(response, list):
            raise VKError("invalid_stats_response")
        days = [x for item in response if (x := stats_row(item)) is not None]
        return {"status": "ok", "days": days}
    except VKError as error:
        return unavailable(error)


def collect(api=vk_call, environ=None, now=None):
    env = os.environ if environ is None else environ
    now = now or dt.datetime.now(dt.timezone.utc)
    local_day = now.astimezone(ZONE).date()
    community_token = env.get("VK_ACCESS_TOKEN", "").strip()
    read_token = env.get("VK_READ_ACCESS_TOKEN", "").strip() or community_token
    stats_token = env.get("VK_STATS_ACCESS_TOKEN", "").strip() or community_token
    if not community_token and not read_token and not stats_token:
        raise RuntimeError("No VK API token configured. No snapshot saved.")
    result = {
        "schema_version": 1,
        "date": local_day.isoformat(),
        "checked_at_utc": now.astimezone(dt.timezone.utc).isoformat(),
        "community_id": GROUP_ID,
        "community": get_members(api, community_token or read_token or stats_token),
        "wall": get_wall(api, read_token),
        "statistics": get_private_stats(api, stats_token, local_day),
    }
    if all(result[k]["status"] != "ok" for k in ("community", "wall", "statistics")):
        raise RuntimeError(
            "All VK read methods denied/failed; refusing empty snapshot. "
            + "Public API error codes: " + ", ".join(
                str(result[k].get("api_error_code", "n/a"))
                for k in ("community", "wall", "statistics")
            )
        )
    return result


def write_snapshot(result, root=ROOT):
    path = root / "stats" / "vk" / (result["date"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
    return path


def main():
    result = collect()
    path = write_snapshot(result)
    summary = []
    for key in ("community", "wall", "statistics"):
        entry = result[key]
        status = entry["status"]
        if status == "unavailable":
            status += "(api error " + str(entry.get("api_error_code", "n/a")) + ")"
        summary.append(key + "=" + status)
    print("VK snapshot:", path.as_posix(), "; ".join(summary))
    if result["community"]["status"] == "ok":
        print("Members:", result["community"]["members"])
    if result["wall"]["status"] == "ok":
        print("Wall posts:", result["wall"]["total_posts"],
              "sampled:", result["wall"]["observed_posts"])
    if result["statistics"]["status"] == "ok":
        print("Stats days:", len(result["statistics"]["days"]))


if __name__ == "__main__":
    main()
