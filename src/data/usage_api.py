"""Real usage from Claude's undocumented /api/oauth/usage endpoint.

This is the same endpoint the Claude Code ``/usage`` slash command uses. It
returns true utilization percentages (0-100) and reset timestamps for the
rolling 5-hour session bucket and the 7-day weekly buckets.

Design constraints (see README):
- READ-ONLY auth: we read the OAuth access token that Claude Code already
  stashed in the macOS Keychain and use it only while it is still valid. We
  never refresh or write it back, so we can't interfere with Claude Code's
  login. When the token is expired we simply report "unavailable".
- The endpoint rate-limits aggressively (~5 calls per token, 429s that persist),
  so results are cached to disk and only refreshed every ``poll_seconds``. This
  cache is shared across processes, which also makes cron invocations safe.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

OAUTH_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
KEYCHAIN_SERVICE = "Claude Code-credentials"
OAUTH_BETA_HEADER = "oauth-2025-04-20"
CACHE_PATH = Path.home() / ".cache" / "stream-deck-agent" / "usage_cache.json"
TOKEN_EXPIRY_BUFFER_MS = 60_000  # treat as expired 60s early


@dataclass
class BucketUsage:
    utilization: float  # percent, 0-100
    resets_at: Optional[datetime] = None

    @property
    def fraction(self) -> float:
        return max(0.0, self.utilization / 100.0)

    def seconds_to_reset(self, now: Optional[datetime] = None) -> Optional[int]:
        if self.resets_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        return max(0, int((self.resets_at - now).total_seconds()))


@dataclass
class ApiUsage:
    available: bool = False
    source: str = "unavailable"  # live | cache | unavailable
    reason: str = ""
    fetched_at: Optional[datetime] = None
    buckets: dict[str, BucketUsage] = field(default_factory=dict)

    @property
    def five_hour(self) -> Optional[BucketUsage]:
        return self.buckets.get("five_hour")

    @property
    def seven_day(self) -> Optional[BucketUsage]:
        return self.buckets.get("seven_day")


def _parse_iso(value) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read_credentials() -> Optional[dict]:
    """Return the parsed Claude Code credentials JSON from the Keychain."""
    try:
        raw = subprocess.check_output(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _keychain_account() -> Optional[str]:
    """Return the account (acct) attribute of the credentials item."""
    try:
        out = subprocess.check_output(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE],
            stderr=subprocess.DEVNULL,
            timeout=5,
            text=True,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('"acct"'):
            # Format: "acct"<blob>="value"
            _, _, value = line.partition("=")
            return value.strip().strip('"') or None
    return None


def _write_credentials(account: str, data: dict) -> bool:
    """Update the Keychain credentials item in place (preserves the item)."""
    try:
        subprocess.run(
            [
                "security", "add-generic-password",
                "-U", "-a", account, "-s", KEYCHAIN_SERVICE,
                "-w", json.dumps(data),
            ],
            check=True,
            capture_output=True,
            timeout=5,
        )
        return True
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return False


def _token_valid(expires_at_ms: Optional[int]) -> bool:
    if not expires_at_ms:
        return False
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    return expires_at_ms > now_ms + TOKEN_EXPIRY_BUFFER_MS


def _refresh_access_token(refresh_token: str) -> Optional[dict]:
    """Exchange a refresh token for a new token set. Returns the raw response."""
    body = json.dumps(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": OAUTH_CLIENT_ID,
        }
    ).encode()
    req = urllib.request.Request(
        OAUTH_TOKEN_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "stream-deck-agent/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        return None
    if not isinstance(data, dict) or "access_token" not in data:
        return None
    return data


def _try_refresh(credentials: dict) -> Optional[str]:
    """Refresh the access token and persist it back to the Keychain.

    Returns the new access token, or None if refresh failed. The rotated
    refresh token is saved atomically so Claude Code stays in sync.
    """
    oauth = credentials.get("claudeAiOauth", credentials)
    refresh_token = oauth.get("refreshToken")
    account = _keychain_account()
    if not refresh_token or not account:
        return None

    resp = _refresh_access_token(refresh_token)
    if resp is None:
        return None

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    oauth["accessToken"] = resp["access_token"]
    oauth["refreshToken"] = resp.get("refresh_token", refresh_token)
    if resp.get("expires_in"):
        oauth["expiresAt"] = now_ms + int(resp["expires_in"]) * 1000
    credentials["claudeAiOauth"] = oauth

    _write_credentials(account, credentials)
    return oauth["accessToken"]


def _http_get_usage(token: str) -> Optional[dict]:
    req = urllib.request.Request(
        OAUTH_USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": OAUTH_BETA_HEADER,
            "User-Agent": "stream-deck-agent/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        return None
    if not isinstance(payload, dict) or payload.get("type") == "error":
        return None
    return payload


def _load_cache() -> Optional[dict]:
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _save_cache(payload: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".tmp")
        record = {"fetched_at": datetime.now(timezone.utc).isoformat(), "data": payload}
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh)
        tmp.replace(CACHE_PATH)
    except OSError:
        pass


def _buckets_from_payload(payload: dict) -> dict[str, BucketUsage]:
    buckets: dict[str, BucketUsage] = {}
    for name, value in payload.items():
        if not isinstance(value, dict) or "utilization" not in value:
            continue
        try:
            util = float(value.get("utilization", 0.0))
        except (TypeError, ValueError):
            continue
        buckets[name] = BucketUsage(
            utilization=util, resets_at=_parse_iso(value.get("resets_at"))
        )
    return buckets


def _from_cache(cache: dict, reason: str = "") -> ApiUsage:
    payload = cache.get("data", {})
    buckets = _buckets_from_payload(payload)
    if not buckets:
        return ApiUsage(reason=reason or "empty cache")
    return ApiUsage(
        available=True,
        source="cache",
        reason=reason,
        fetched_at=_parse_iso(cache.get("fetched_at")),
        buckets=buckets,
    )


def get_usage(
    enabled: bool = True, poll_seconds: int = 180, auto_refresh: bool = False
) -> ApiUsage:
    """Return real usage, refreshing from the API at most every ``poll_seconds``.

    When ``auto_refresh`` is True and the Keychain access token is expired, the
    token is refreshed via its refresh token (and written back to the Keychain).
    """
    if not enabled:
        return ApiUsage(reason="disabled")

    cache = _load_cache()
    now = datetime.now(timezone.utc)

    # Fresh enough cache: serve it without touching the rate-limited endpoint.
    if cache:
        fetched = _parse_iso(cache.get("fetched_at"))
        if fetched and (now - fetched).total_seconds() < poll_seconds:
            return _from_cache(cache)

    # Cache is stale (or missing): try to (re)fetch.
    credentials = _read_credentials()
    oauth = credentials.get("claudeAiOauth", credentials) if credentials else None
    token = oauth.get("accessToken") if oauth else None
    expires_at = oauth.get("expiresAt") if oauth else None

    if not token or not _token_valid(expires_at):
        token = _try_refresh(credentials) if (auto_refresh and credentials) else None
        if not token:
            if cache:
                return _from_cache(cache, reason="token expired; showing cached")
            return ApiUsage(reason="token expired or unavailable")

    payload = _http_get_usage(token)
    if payload is None:
        if cache:
            return _from_cache(cache, reason="fetch failed; showing cached")
        return ApiUsage(reason="fetch failed (rate limited?)")

    _save_cache(payload)
    buckets = _buckets_from_payload(payload)
    if not buckets:
        return ApiUsage(reason="empty response")
    return ApiUsage(available=True, source="live", fetched_at=now, buckets=buckets)
