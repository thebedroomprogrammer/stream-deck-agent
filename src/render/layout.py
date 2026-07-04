"""Map computed stats onto the 32 keys of a Stream Deck XL.

Grid: 4 rows x 8 columns (key index = row * 8 + col).

    Row 0 (0-7)    Session summary
    Rows 1-2 (8-23)  Up to 8 model tiles, 2 keys each
    Row 3 (24-31)  Session usage progress bar (8 segments, 0-100%)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PIL import Image

from ..config import Config
from ..data.stats import SessionStats, WeeklyStats
from ..data.usage_api import ApiUsage
from . import tiles

COLS = 8
ROWS = 4
KEY_COUNT = COLS * ROWS
PROGRESS_ROW = 3  # bottom row reserved for the usage progress bar
MODEL_ROWS = (1, 2)
MODEL_SLOTS = len(MODEL_ROWS) * COLS // 2  # 8 two-key model tiles
PROGRESS_FIRST_KEY = PROGRESS_ROW * COLS  # key 24

GREY = (70, 70, 78)


def _session_percent(session: SessionStats, config: Config) -> float:
    limit = config.session_token_limit or 0
    if limit <= 0:
        return 0.0
    return session.total_tokens / limit


def _fmt_hm(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    return f"{hours:d}:{rem // 60:02d}"


def _session_usage(
    session: SessionStats, config: Config, api: Optional[ApiUsage]
) -> tuple[str, Optional[float], Optional[int]]:
    """Resolve the session usage fraction and reset seconds.

    Returns (mode, fraction, reset_seconds) where mode is:
        "real" - from Claude's usage API
        "est"  - token-based estimate (API disabled or no data source)
        "na"   - API enabled but unavailable (expired token / rate limited)
    fraction is None only when mode == "na".
    """
    if api is not None and api.available and api.five_hour is not None:
        bucket = api.five_hour
        return "real", bucket.fraction, bucket.seconds_to_reset()

    if api is None or not config.usage_api_enabled:
        if session.active:
            return "est", _session_percent(session, config), session.seconds_to_reset
        return "est", 0.0, None

    # API enabled but no data available right now.
    return "na", None, (session.seconds_to_reset if session.active else None)


def build_session_row(
    session: SessionStats,
    config: Config,
    now: datetime,
    api: Optional[ApiUsage] = None,
) -> dict[int, Image.Image]:
    keys: dict[int, Image.Image] = {}

    keys[0] = tiles.title_key("CLAUDE", now.astimezone().strftime("%H:%M"))

    mode, frac, reset_secs = _session_usage(session, config, api)

    # Reset countdown (real reset from the API when available).
    if reset_secs is not None:
        keys[1] = tiles.countdown_key("RESET IN", _fmt_hm(reset_secs), "h:mm")
    elif session.active:
        keys[1] = tiles.countdown_key("RESET IN", session.reset_countdown(), "h:mm")
    else:
        keys[1] = tiles.countdown_key("SESSION", "idle", "no active")

    # Token counts / cost / burn / projection come from the local logs.
    if session.active:
        keys[2] = tiles.stat_key("SESSION", tiles.format_tokens(session.total_tokens), "tokens")
        keys[3] = tiles.stat_key("COST", tiles.format_cost(session.cost_usd), "session")
        keys[4] = tiles.stat_key(
            "BURN", tiles.format_tokens(int(session.burn_rate_tokens_per_min)), "tok/min"
        )
        keys[5] = tiles.stat_key(
            "PROJ", tiles.format_tokens(session.projected_tokens), "at reset"
        )
    else:
        keys[2] = tiles.stat_key("SESSION", "0", "tokens")
        keys[3] = tiles.stat_key("COST", "$0.00", "session")
        keys[4] = tiles.stat_key("BURN", "0", "tok/min")
        keys[5] = tiles.stat_key("PROJ", "0", "at reset")

    # USED tile: real % from the API, an estimate, or n/a.
    if frac is None:
        keys[6] = tiles.stat_key("USED", "n/a", "no token", value_color=tiles.MUTED)
    else:
        subtitle = {"real": "of limit", "est": "est"}.get(mode, "")
        keys[6] = tiles.stat_key(
            "USED",
            f"{min(frac * 100, 999):.0f}%",
            subtitle,
            value_color=tiles.fraction_color(frac),
        )

    keys[7] = tiles.stat_key("UPDATED", now.astimezone().strftime("%H:%M:%S"), "every 30s")
    return keys


def build_model_tiles(
    weekly: WeeklyStats, config: Config
) -> dict[int, Image.Image]:
    keys: dict[int, Image.Image] = {}
    metric = config.limit_metric

    models = weekly.models[:MODEL_SLOTS]
    for slot, model in enumerate(models):
        base = MODEL_ROWS[0] * COLS + slot * 2  # first model key = index 8
        name_index = base
        bar_index = base + 1
        if bar_index >= PROGRESS_FIRST_KEY:
            break

        frac = model.limit_fraction(metric)
        keys[name_index] = tiles.model_name_key(model.display_name, frac, model.color)

        if metric == "cost" and model.config.weekly_cost_limit:
            used_text = tiles.format_cost(model.cost_usd)
            limit_text = tiles.format_cost(model.config.weekly_cost_limit)
        else:
            used_text = tiles.format_tokens(model.total_tokens)
            limit_text = tiles.format_tokens(model.config.weekly_token_limit)
        keys[bar_index] = tiles.model_bar_key(used_text, limit_text, frac, model.color)

    return keys


def build_progress_row(
    session: SessionStats, config: Config, api: Optional[ApiUsage] = None
) -> dict[int, Image.Image]:
    """Render the bottom row as an 8-segment usage bar (0-100%).

    Segments fill left-to-right: unused = green, used = orange (red on
    overflow). The percentage is drawn on the current fill-front segment.
    """
    keys: dict[int, Image.Image] = {}
    _mode, frac, _reset = _session_usage(session, config, api)

    if frac is None:
        # No real data available: neutral grey bar with an n/a marker.
        for i in range(COLS):
            keys[PROGRESS_FIRST_KEY + i] = tiles.progress_segment_key(
                seg_fill=0.0,
                label="n/a" if i == COLS // 2 else "",
                empty_color=GREY,
            )
        return keys

    pct = frac
    overflow = pct > 1.0
    filled_exact = max(0.0, min(pct, 1.0)) * COLS  # keys-worth that are filled

    # Boundary = last segment that has any orange (the "latest orange one").
    boundary = 0
    for i in range(COLS):
        if filled_exact - i > 0:
            boundary = i
    label_text = f"{pct * 100:.0f}%"

    for i in range(COLS):
        seg = max(0.0, min(1.0, filled_exact - i))
        show_label = label_text if i == boundary else ""
        keys[PROGRESS_FIRST_KEY + i] = tiles.progress_segment_key(
            seg_fill=1.0 if overflow else seg,
            label=show_label,
            overflow=overflow,
        )
    return keys


def build_dashboard(
    session: SessionStats,
    weekly: WeeklyStats,
    config: Config,
    now: Optional[datetime] = None,
    api: Optional[ApiUsage] = None,
) -> dict[int, Image.Image]:
    """Return a full 32-key image map. Unused keys are filled blank."""
    now = now or datetime.now()
    keys: dict[int, Image.Image] = {}
    keys.update(build_session_row(session, config, now, api))
    keys.update(build_model_tiles(weekly, config))
    keys.update(build_progress_row(session, config, api))

    for i in range(KEY_COUNT):
        keys.setdefault(i, tiles.blank_key())
    return keys


def build_error_dashboard(message: str) -> dict[int, Image.Image]:
    keys = {i: tiles.blank_key() for i in range(KEY_COUNT)}
    keys[0] = tiles.title_key("ERROR", accent=tiles.DANGER)
    keys[1] = tiles.message_key(message[:10], color=tiles.DANGER)
    return keys
