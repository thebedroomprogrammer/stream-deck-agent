"""Fast reader for Claude Code usage logs.

Claude Code writes one JSON object per line to ~/.claude/projects/**/*.jsonl.
We only care about assistant messages that carry a ``message.usage`` block. To
keep the 30s refresh loop snappy we skip files whose mtime is older than the
lookback window and cheaply pre-filter lines before parsing JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

import json


@dataclass
class UsageRecord:
    timestamp: datetime
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_creation_tokens
            + self.cache_read_tokens
        )


def _parse_timestamp(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        # Handles the trailing "Z" used by Claude Code logs.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _iter_recent_files(projects_dir: Path, since: datetime) -> Iterator[Path]:
    if not projects_dir.exists():
        return
    since_ts = since.timestamp()
    for path in projects_dir.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime >= since_ts:
                yield path
        except OSError:
            continue


def _dedupe_key(obj: dict) -> Optional[tuple]:
    message = obj.get("message") or {}
    message_id = message.get("id")
    request_id = obj.get("requestId")
    if message_id or request_id:
        return (message_id, request_id)
    return None


def iter_usage_records(projects_dir: Path, since: datetime) -> Iterator[UsageRecord]:
    """Yield deduped UsageRecords with timestamp >= ``since``."""
    seen: set[tuple] = set()
    for path in _iter_recent_files(projects_dir, since):
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    # Cheap pre-filter: every usage line contains this key.
                    if '"usage"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (ValueError, TypeError):
                        continue

                    message = obj.get("message")
                    if not isinstance(message, dict):
                        continue
                    usage = message.get("usage")
                    if not isinstance(usage, dict):
                        continue

                    ts = _parse_timestamp(obj.get("timestamp", ""))
                    if ts is None or ts < since:
                        continue

                    key = _dedupe_key(obj)
                    if key is not None:
                        if key in seen:
                            continue
                        seen.add(key)

                    yield UsageRecord(
                        timestamp=ts,
                        model=str(message.get("model", "unknown")),
                        input_tokens=int(usage.get("input_tokens", 0) or 0),
                        output_tokens=int(usage.get("output_tokens", 0) or 0),
                        cache_creation_tokens=int(
                            usage.get("cache_creation_input_tokens", 0) or 0
                        ),
                        cache_read_tokens=int(
                            usage.get("cache_read_input_tokens", 0) or 0
                        ),
                    )
        except OSError:
            continue


def load_usage_records(
    projects_dir: Path, lookback_days: float, now: Optional[datetime] = None
) -> list[UsageRecord]:
    """Load and sort recent usage records (oldest first)."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=lookback_days)
    records = list(iter_usage_records(projects_dir, since))
    records.sort(key=lambda r: r.timestamp)
    return records
