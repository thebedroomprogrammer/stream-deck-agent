"""Aggregate raw usage records into session and weekly statistics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from ..config import Config, ModelConfig, ModelPrice
from .parser import UsageRecord


def _cost_for(record: UsageRecord, price: Optional[ModelPrice]) -> float:
    if price is None:
        return 0.0
    return (
        record.input_tokens * price.input
        + record.output_tokens * price.output
        + record.cache_creation_tokens * price.cache_write
        + record.cache_read_tokens * price.cache_read
    ) / 1_000_000.0


@dataclass
class SessionStats:
    active: bool = False
    start_time: Optional[datetime] = None
    reset_time: Optional[datetime] = None
    seconds_to_reset: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    burn_rate_tokens_per_min: float = 0.0
    projected_tokens: int = 0
    entries: int = 0

    def reset_countdown(self) -> str:
        if not self.active or self.seconds_to_reset <= 0:
            return "--:--"
        hours, rem = divmod(self.seconds_to_reset, 3600)
        minutes = rem // 60
        return f"{hours:d}:{minutes:02d}"


@dataclass
class ModelUsage:
    model_id: str
    config: ModelConfig
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def display_name(self) -> str:
        return self.config.display_name

    @property
    def color(self) -> tuple[int, int, int]:
        return self.config.color

    def limit_fraction(self, metric: str) -> float:
        if metric == "cost" and self.config.weekly_cost_limit:
            return _safe_fraction(self.cost_usd, self.config.weekly_cost_limit)
        limit = self.config.weekly_token_limit or 0
        return _safe_fraction(self.total_tokens, limit)


@dataclass
class WeeklyStats:
    models: list[ModelUsage] = field(default_factory=list)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    window_days: int = 7


def _safe_fraction(value: float, limit: float) -> float:
    if not limit or limit <= 0:
        return 0.0
    return max(0.0, value / limit)


def _floor_to_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def build_session_stats(
    records: list[UsageRecord],
    config: Config,
    now: Optional[datetime] = None,
) -> SessionStats:
    """Compute the active 5-hour session block (ccusage-style).

    A block starts at the top of the hour of its first entry. The block is
    considered active while ``now`` is within ``session_window_hours`` of both
    the block start and the last activity.
    """
    now = now or datetime.now(timezone.utc)
    window = timedelta(hours=config.session_window_hours)
    if not records:
        return SessionStats(active=False)

    # Records are sorted oldest-first. Walk into contiguous blocks: a new block
    # begins when the gap since the last entry exceeds the window.
    block: list[UsageRecord] = []
    block_start_hour: Optional[datetime] = None
    for rec in records:
        if not block:
            block = [rec]
            block_start_hour = _floor_to_hour(rec.timestamp)
            continue
        within_window = rec.timestamp - block_start_hour < window
        gap_ok = rec.timestamp - block[-1].timestamp < window
        if within_window and gap_ok:
            block.append(rec)
        else:
            block = [rec]
            block_start_hour = _floor_to_hour(rec.timestamp)

    if not block or block_start_hour is None:
        return SessionStats(active=False)

    reset_time = block_start_hour + window
    last_activity = block[-1].timestamp
    active = now < reset_time and (now - last_activity) < window
    if not active:
        return SessionStats(active=False, start_time=block_start_hour, reset_time=reset_time)

    total_tokens = sum(r.total_tokens for r in block)
    cost = 0.0
    for r in block:
        cfg = config.resolve_model(r.model)
        cost += _cost_for(r, cfg.price)

    seconds_to_reset = int((reset_time - now).total_seconds())
    elapsed_min = max(1.0, (now - block_start_hour).total_seconds() / 60.0)
    burn = total_tokens / elapsed_min
    projected = int(total_tokens + burn * max(0, seconds_to_reset) / 60.0)

    return SessionStats(
        active=True,
        start_time=block_start_hour,
        reset_time=reset_time,
        seconds_to_reset=max(0, seconds_to_reset),
        total_tokens=total_tokens,
        cost_usd=cost,
        burn_rate_tokens_per_min=burn,
        projected_tokens=projected,
        entries=len(block),
    )


def build_weekly_stats(
    records: list[UsageRecord],
    config: Config,
    now: Optional[datetime] = None,
) -> WeeklyStats:
    """Aggregate per-model usage over the trailing weekly window."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=config.weekly_window_days)

    by_model: dict[str, ModelUsage] = {}
    total_tokens = 0
    total_cost = 0.0

    for rec in records:
        if rec.timestamp < since:
            continue
        # Skip synthetic/error placeholder models that carry no real usage.
        if "synthetic" in (rec.model or "").lower():
            continue
        cfg = config.resolve_model(rec.model)
        # Group by the resolved display name so variants collapse together.
        key = cfg.display_name
        mu = by_model.get(key)
        if mu is None:
            mu = ModelUsage(model_id=rec.model, config=cfg)
            by_model[key] = mu
        mu.input_tokens += rec.input_tokens
        mu.output_tokens += rec.output_tokens
        mu.cache_creation_tokens += rec.cache_creation_tokens
        mu.cache_read_tokens += rec.cache_read_tokens
        mu.total_tokens += rec.total_tokens
        cost = _cost_for(rec, cfg.price)
        mu.cost_usd += cost

        total_tokens += rec.total_tokens
        total_cost += cost

    models = sorted(
        (m for m in by_model.values() if m.total_tokens > 0),
        key=lambda m: m.total_tokens,
        reverse=True,
    )
    return WeeklyStats(
        models=models,
        total_tokens=total_tokens,
        total_cost_usd=total_cost,
        window_days=config.weekly_window_days,
    )
