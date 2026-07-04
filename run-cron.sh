#!/usr/bin/env bash
# Wrapper for running the dashboard from cron (minimal environment).
# Renders a single frame and leaves it on the Stream Deck.
#
# Cron entry examples (see README for the 30s trick):
#   * * * * * /path/to/stream-deck-agent/run-cron.sh >> /tmp/streamdeck.log 2>&1

set -euo pipefail

# Resolve this script's directory so cron can call it from anywhere.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# hidapi installed via Homebrew lives here; cron's PATH is minimal.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export DYLD_LIBRARY_PATH="/opt/homebrew/lib:/usr/local/lib:${DYLD_LIBRARY_PATH:-}"

PYTHON="$SCRIPT_DIR/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="$(command -v python3)"
fi

exec "$PYTHON" -m src.main --cron
