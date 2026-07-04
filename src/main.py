"""Entry point: refresh the Stream Deck Claude usage dashboard on a timer.

Usage:
    python -m src.main                 # run forever, refresh on a timer
    python -m src.main --cron          # render one frame, leave it on screen (cron)
    python -m src.main --once          # render one frame, then clear the screen
    python -m src.main --preview out.png  # save a composite preview, no device
    python -m src.main --config path/to/config.yaml
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime, timezone

from .config import Config, load_config
from .data.parser import load_usage_records
from .data.stats import build_session_stats, build_weekly_stats


def _compute_dashboard(config: Config):
    from .data.usage_api import get_usage
    from .render.layout import build_dashboard

    lookback = max(config.weekly_window_days + 1, config.session_window_hours / 24 + 1)
    now = datetime.now(timezone.utc)
    records = load_usage_records(config.claude_projects_dir, lookback, now=now)
    session = build_session_stats(records, config, now=now)
    weekly = build_weekly_stats(records, config, now=now)
    api = get_usage(
        enabled=config.usage_api_enabled, poll_seconds=config.usage_api_poll_seconds
    )
    return build_dashboard(session, weekly, config, now=datetime.now(), api=api)


def _save_preview(images, path: str) -> None:
    from PIL import Image

    from .render.layout import COLS, ROWS
    from .render.tiles import KEY_SIZE

    gap = 6
    width = COLS * KEY_SIZE + (COLS + 1) * gap
    height = ROWS * KEY_SIZE + (ROWS + 1) * gap
    canvas = Image.new("RGB", (width, height), (0, 0, 0))
    for index, img in images.items():
        row, col = divmod(index, COLS)
        x = gap + col * (KEY_SIZE + gap)
        y = gap + row * (KEY_SIZE + gap)
        canvas.paste(img, (x, y))
    canvas.save(path)


def run_preview(config: Config, path: str) -> int:
    images = _compute_dashboard(config)
    _save_preview(images, path)
    print(f"Preview saved to {path}")
    return 0


def run_once_cron(config: Config) -> int:
    """Render a single frame and leave it on the deck (for cron/one-shot use).

    Unlike the interactive loop, this does not clear or dim the screen on exit,
    so the deck keeps showing the last render until the next cron invocation.
    """
    from .device.deck import Deck, DeckNotFoundError

    try:
        deck = Deck(brightness=config.brightness).open(reset=False)
    except DeckNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        deck.render(_compute_dashboard(config))
    except Exception as exc:
        print(f"refresh error: {exc}", file=sys.stderr)
        deck.close(clear=False)
        return 1

    deck.close(clear=False)
    return 0


def run_loop(config: Config, once: bool = False) -> int:
    from .device.deck import Deck, DeckNotFoundError
    from .render.layout import build_error_dashboard

    try:
        deck = Deck(brightness=config.brightness).open()
    except DeckNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Connected: {deck.description}")
    running = {"active": True}

    def _stop(signum, frame):
        running["active"] = False

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        while running["active"]:
            start = time.monotonic()
            try:
                images = _compute_dashboard(config)
                deck.render(images)
            except Exception as exc:  # keep the daemon alive on transient errors
                print(f"refresh error: {exc}", file=sys.stderr)
                try:
                    deck.render(build_error_dashboard(str(exc)))
                except Exception:
                    pass

            if once:
                break

            elapsed = time.monotonic() - start
            remaining = max(0.0, config.refresh_seconds - elapsed)
            # Sleep in small slices so Ctrl-C is responsive.
            while remaining > 0 and running["active"]:
                nap = min(0.5, remaining)
                time.sleep(nap)
                remaining -= nap
    finally:
        deck.close()
        print("Stopped, display cleared.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Stream Deck Claude usage dashboard")
    parser.add_argument("--config", help="Path to config file", default=None)
    parser.add_argument(
        "--cron",
        action="store_true",
        help="Render one frame and exit, leaving it on screen (for cron)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Render one frame to the device and exit (clears screen on exit)",
    )
    parser.add_argument(
        "--preview",
        metavar="PATH",
        help="Save a composite preview image instead of using the device",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)

    if args.preview:
        return run_preview(config, args.preview)
    if args.cron:
        return run_once_cron(config)
    return run_loop(config, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
