"""Map computed stats onto the 32 keys of a Stream Deck XL.

Grid: 4 rows x 8 columns (key index = row * 8 + col).

    Row 0 (0-7)   Session summary
    Rows 1-3 (8-31)  Up to 12 model tiles, 2 keys each
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from PIL import Image

from ..config import Config
from ..data.stats import SessionStats, WeeklyStats
from . import tiles

COLS = 8
ROWS = 4
KEY_COUNT = COLS * ROWS
MODEL_SLOTS = 12  # (ROWS-1) * COLS / 2


def _session_percent(session: SessionStats, config: Config) -> float:
    limit = config.session_token_limit or 0
    if limit <= 0:
        return 0.0
    return session.total_tokens / limit


def build_session_row(
    session: SessionStats, config: Config, now: datetime
) -> dict[int, Image.Image]:
    keys: dict[int, Image.Image] = {}

    keys[0] = tiles.title_key("CLAUDE", now.astimezone().strftime("%H:%M"))

    if session.active:
        keys[1] = tiles.countdown_key("RESET IN", session.reset_countdown(), "h:mm")
        keys[2] = tiles.stat_key("SESSION", tiles.format_tokens(session.total_tokens), "tokens")
        keys[3] = tiles.stat_key("COST", tiles.format_cost(session.cost_usd), "session")
        keys[4] = tiles.stat_key(
            "BURN", tiles.format_tokens(int(session.burn_rate_tokens_per_min)), "tok/min"
        )
        keys[5] = tiles.stat_key(
            "PROJ", tiles.format_tokens(session.projected_tokens), "at reset"
        )
        frac = _session_percent(session, config)
        keys[6] = tiles.stat_key(
            "USED",
            f"{min(frac * 100, 999):.0f}%",
            "of cap",
            value_color=tiles.fraction_color(frac),
        )
    else:
        keys[1] = tiles.countdown_key("SESSION", "idle", "no active")
        keys[2] = tiles.stat_key("SESSION", "0", "tokens")
        keys[3] = tiles.stat_key("COST", "$0.00", "session")
        keys[4] = tiles.stat_key("BURN", "0", "tok/min")
        keys[5] = tiles.stat_key("PROJ", "0", "at reset")
        keys[6] = tiles.stat_key("USED", "0%", "of cap")

    keys[7] = tiles.stat_key("UPDATED", now.astimezone().strftime("%H:%M:%S"), "every 30s")
    return keys


def build_model_tiles(
    weekly: WeeklyStats, config: Config
) -> dict[int, Image.Image]:
    keys: dict[int, Image.Image] = {}
    metric = config.limit_metric

    models = weekly.models[:MODEL_SLOTS]
    for slot, model in enumerate(models):
        base = KEY_COUNT - (ROWS - 1) * COLS + slot * 2  # first model key = index 8
        name_index = base
        bar_index = base + 1
        if bar_index >= KEY_COUNT:
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


def build_dashboard(
    session: SessionStats,
    weekly: WeeklyStats,
    config: Config,
    now: Optional[datetime] = None,
) -> dict[int, Image.Image]:
    """Return a full 32-key image map. Unused keys are filled blank."""
    now = now or datetime.now()
    keys: dict[int, Image.Image] = {}
    keys.update(build_session_row(session, config, now))
    keys.update(build_model_tiles(weekly, config))

    for i in range(KEY_COUNT):
        keys.setdefault(i, tiles.blank_key())
    return keys


def build_error_dashboard(message: str) -> dict[int, Image.Image]:
    keys = {i: tiles.blank_key() for i in range(KEY_COUNT)}
    keys[0] = tiles.title_key("ERROR", accent=tiles.DANGER)
    keys[1] = tiles.message_key(message[:10], color=tiles.DANGER)
    return keys
