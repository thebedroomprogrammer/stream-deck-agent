# Stream Deck Claude Usage Dashboard

A Python daemon that renders a live Claude Code usage dashboard across all 32 keys
of a **Stream Deck XL** (4x8). It refreshes every 30 seconds and shows:

- **Current session** (Claude's rolling 5-hour window): tokens used, estimated cost,
  burn rate, projected usage, and a big **countdown to reset**.
- **Weekly usage of every model** vs configurable limits, drawn as per-model
  percentage bars.

## How it works

Claude Code writes per-message usage to `~/.claude/projects/**/*.jsonl`. This tool
reads those logs directly (only recently-modified files/lines, so it stays fast),
computes the active 5-hour session block and trailing 7-day per-model totals, then
renders one image per key and pushes them to the device.

```
logs (~/.claude/projects) -> parser -> stats (session + weekly) -> layout -> Stream Deck
```

> Note: Anthropic's real weekly *limit* numbers and server-side reset times are not
> stored locally. Only your *usage* is. Limits are therefore read from `config.yaml`
> (set them to match your plan). The session reset countdown is derived from the
> 5-hour activity window.

## Requirements

- A Stream Deck XL (32 keys). Other models will work but the layout targets 32 keys.
- macOS/Linux with `hidapi` installed (needed by the `streamdeck` library).
- Python 3.10+.

## Setup

```bash
# 1. System dependency for USB HID access
brew install hidapi            # macOS
# sudo apt-get install libhidapi-libusb0   # Debian/Ubuntu

# 2. Python environment
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configuration
cp config.example.yaml config.yaml
#   edit config.yaml -> set weekly_token_limit per model to match your plan

# 4. Plug in the Stream Deck XL, then run
python -m src.main
```

Stop with `Ctrl-C`; the screen is cleared on exit.

## Configuration

See `config.example.yaml` for all options. Key ones:

- `refresh_seconds` — dashboard refresh interval (default 30).
- `brightness` — screen brightness 0-100.
- `models` — per-model `display_name`, `weekly_token_limit`, `color`, and optional
  `price` for cost estimation. Entries are matched by case-insensitive substring
  against the model id in the logs.
- `limit_metric` — `tokens` or `cost`, controls what the percentage bars represent.

## Layout (32 keys)

- **Row 0 (keys 0-7)** — session: title/clock, reset countdown, tokens, cost,
  burn rate, projected end usage, session %, status.
- **Rows 1-3 (keys 8-31)** — up to 12 model tiles (2 keys each): name + %, and a
  colored weekly usage bar with tokens vs limit.

## Troubleshooting

- **No Stream Deck found** — ensure it's plugged in and `hidapi` is installed. On
  Linux you may need a udev rule for non-root access.
- **All zeros** — confirm `claude_projects_dir` points at your Claude logs and that
  you've used Claude Code recently.
