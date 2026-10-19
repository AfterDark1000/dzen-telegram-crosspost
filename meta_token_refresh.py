#!/usr/bin/env python3
"""Refresh long-lived Instagram/Threads tokens and replace their GitHub Actions secrets.

Runs after the existing crosspost job. Checks state on each run, refreshes
about every 21 days and retries at most once per 24 hours after an error.
Never writes tokens to repository files, stdout, subprocess arguments or logs.
"""
import base64
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

STATE = Path("meta_token_refresh_state.json")
EXPECTED_REPOSITORY = "AfterDark1000/dzen-telegram-crosspost"
REFRESH_AFTER_DAYS = 21
RETRY_AFTER_HOURS = 24
MIN_VALIDITY_SECONDS = 45 * 24 * 3600
UTC = dt.timezone.utc

ACCOUNTS = {
    "instagram": {
        "token_var": "INSTAGRAM_ACCESS_TOKEN",
        "refresh_url": "https://graph.instagram.com/refresh_access_token",
        "grant_type": "ig_refresh_token",
        "profile_url": "https://graph.instagram.com/v24.0/me",
    },
    "threads": {
        "token_var": "THREADS_ACCESS_TOKEN",
        "refresh_url": "https://graph.threads.net/refresh_access_token",
        "grant_type": "th_refresh_token",
        "profile_url": "https://graph.threads.net/v1.0/me",
    },
}
EXPECTED_USERNAME = "iipike.ai"


def request_json(url, *, authorization=None, payload=None, method="GET"):
    headers = {"Accept": "application/json", "User-Agent": "HPI-MetaTokenMaintainer/1.0"}
    if authorization:
        headers["Authorization"] = "Bearer " + authorization
    if payload is not None:
        headers["Content-Type"] = "application/json"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            raw = response.read()
            return (json.loads(raw.decode("utf-8")) if raw else {}), response.status
    except urllib.error.HTTPError as e:
        # NEVER print the request URL: Meta's refresh URL contains the secret token.
        # NEVER echo Meta's error body: it could contain secrets in diagnostic text.
        raise RuntimeError(f"API returned HTTP {e.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("Network request failed") from None


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("Missing or invalid timestamp in refresh state")
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def now_string(value):
    return value.astimezone(UTC).isoformat(timespec="seconds")


def refresh_meta_token(account, old_token):
    cfg = ACCOUNTS[account]
    if not old_token:
        raise RuntimeError(f"Missing {cfg['token_var']} GitHub secret")
    url = cfg["refresh_url"] + "?" + urllib.parse.urlencode({
        "grant_type": cfg["grant_type"],
        "access_token": old_token,
    })
    response, _ = request_json(url)
    candidate = response.get("access_token")
    expiry = response.get("expires_in")
    if not isinstance(candidate, str) or len(candidate) < 20:
        raise RuntimeError("Meta did not return a usable refreshed token")
    if not isinstance(expiry, (int, float)) or expiry < MIN_VALIDITY_SECONDS:
        raise RuntimeError("Meta returned an unexpectedly short expiry")
    # Verify the refreshed token belongs to our target profile before overwriting
    # a working secret. This uses the new token without logging it.
    profile_url = cfg["profile_url"] + "?" + urllib.parse.urlencode({
        "fields": "id,username", "access_token": candidate,
    })
    profile, _ = request_json(profile_url)
    if (profile.get("username") or "").lower() != EXPECTED_USERNAME:
        raise RuntimeError("Refreshed token belongs to the wrong profile")
    if not profile.get("id"):
        raise RuntimeError("Refreshed token has no account ID")
    return candidate, int(expiry)


def github_headers(pat):
    return {"Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + pat,
            "X-GitHub-Api-Version": "2022-11-28"}


def github_call(path, pat, method="GET", payload=None):
    # All paths are source-defined, never formed from external content.
    url = "https://api.github.com/repos/" + EXPECTED_REPOSITORY + path
    headers = github_headers(pat)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=35) as response:
            raw = response.read()
            return (json.loads(raw.decode("utf-8")) if raw else {}), response.status
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub API returned HTTP {exc.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("GitHub API network error") from None


def store_github_secret(name, new_token, pat):
    """Encrypt using GitHub's public key before sending to Actions Secrets API."""
    from nacl.public import PublicKey, SealedBox
    info, _ = github_call("/actions/secrets/public-key", pat)
    public_key = info.get("key")
    key_id = info.get("key_id")
    if not public_key or not key_id:
        raise RuntimeError("GitHub returned no repository encryption key")
    sealed = SealedBox(PublicKey(base64.b64decode(public_key)))
    encrypted = base64.b64encode(sealed.encrypt(new_token.encode("utf-8"))).decode("ascii")
    _, code = github_call("/actions/secrets/" + name, pat, method="PUT", payload={
        "encrypted_value": encrypted,
        "key_id": key_id,
    })
    if code not in (201, 204):
        raise RuntimeError("GitHub did not confirm secret update")


def check_connections():
    pat = os.getenv("GH_META_SECRETS_PAT", "").strip()
    if not pat:
        raise RuntimeError("Missing GH_META_SECRETS_PAT GitHub secret")
    for account, cfg in ACCOUNTS.items():
        token = os.getenv(cfg["token_var"], "").strip()
        if not token:
            raise RuntimeError("Missing " + cfg["token_var"])
        profile_url = cfg["profile_url"] + "?" + urllib.parse.urlencode({
            "fields": "id,username", "access_token": token,
        })
        profile, _ = request_json(profile_url)
        if (profile.get("username") or "").lower() != EXPECTED_USERNAME:
            raise RuntimeError(account + ": unexpected profile")
        # Verify PAT can see repository secrets metadata; it never reads values.
        details, _ = github_call("/actions/secrets/" + cfg["token_var"], pat)
        if details.get("name") != cfg["token_var"]:
            raise RuntimeError("GitHub cannot inspect " + cfg["token_var"])
        print(account + ": Meta token valid; GitHub Secrets access verified.", flush=True)
    # Test actual Secrets:write with a temporary random secret, without touching
    # INSTAGRAM_ACCESS_TOKEN or THREADS_ACCESS_TOKEN. Delete immediately after.
    import secrets
    probe_name = "META_REFRESH_PERMISSION_TEST"
    store_github_secret(probe_name, secrets.token_urlsafe(32), pat)
    try:
        details, _ = github_call("/actions/secrets/" + probe_name, pat)
        if details.get("name") != probe_name:
            raise RuntimeError("Temporary GitHub secret could not be verified")
    finally:
        github_call("/actions/secrets/" + probe_name, pat, method="DELETE")
    print("GitHub Secrets write/delete permissions verified.", flush=True)
    print("Check complete; NO Instagram or Threads token was renewed or overwritten.", flush=True)


def main():
    if os.getenv("GITHUB_REPOSITORY") not in (None, "", EXPECTED_REPOSITORY):
        raise RuntimeError("Unexpected repository: token maintenance refused")
    if "--check" in sys.argv:
        check_connections()
        return

    if not STATE.is_file():
        raise RuntimeError("Refresh state missing; no credentials modified")
    state = json.loads(STATE.read_text(encoding="utf-8"))
    current = dt.datetime.now(UTC)
    pat = os.getenv("GH_META_SECRETS_PAT", "").strip()
    failures = []
    changed = False

    for account, cfg in ACCOUNTS.items():
        row = state.get(account)
        if not isinstance(row, dict):
            raise RuntimeError("Invalid state for " + account)
        if current - parse_time(row.get("last_success")) < dt.timedelta(days=REFRESH_AFTER_DAYS):
            print(account + ": not due.", flush=True)
            continue
        prev_attempt = row.get("last_attempt")
        if prev_attempt and current - parse_time(prev_attempt) < dt.timedelta(hours=RETRY_AFTER_HOURS):
            print(account + ": waiting until next retry window.", flush=True)
            continue
        row["last_attempt"] = now_string(current)
        changed = True
        try:
            if not pat:
                raise RuntimeError("Missing GH_META_SECRETS_PAT")
            token = os.getenv(cfg["token_var"], "").strip()
            new_token, expiry = refresh_meta_token(account, token)
            store_github_secret(cfg["token_var"], new_token, pat)
            row["last_success"] = now_string(current)
            row["last_expires_in_seconds"] = expiry
            print(account + ": token renewed and safely saved in GitHub Secrets.", flush=True)
        except Exception as exc:
            # Only our curated error strings, never tokens/URLs/response bodies.
            print("WARNING: " + account + ": " + str(exc), flush=True)
            failures.append(account)

    if changed:
        STATE.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                         encoding="utf-8")
    if failures:
        raise RuntimeError("Meta token refresh failed for: " + ", ".join(failures) +
                           ". Other publishing jobs are unaffected.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("TOKEN MAINTENANCE ERROR: " + str(exc), flush=True)
        sys.exit(1)
