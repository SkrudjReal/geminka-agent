#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="python3"
if [ -d "$SCRIPT_DIR/.venv" ]; then
    PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
elif [ -f "/home/velunae/.hermes/hermes-agent/venv/bin/python" ]; then
    PYTHON_BIN="/home/velunae/.hermes/hermes-agent/venv/bin/python"
fi

echo "[*] Launching Geminka Telegram Bot via Antigravity engine (aiogram)..."
exec "$PYTHON_BIN" "$SCRIPT_DIR/bot.py"
