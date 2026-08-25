#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v uv >/dev/null 2>&1; then
    echo "[*] Launching Geminka Telegram Bot via ultra-fast uv runner..."
    exec uv run main.py
elif [ -d "$SCRIPT_DIR/.venv" ]; then
    echo "[*] Launching Geminka Telegram Bot via local virtualenv..."
    exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/main.py"
else
    echo "[*] Launching Geminka Telegram Bot via system python3..."
    exec python3 "$SCRIPT_DIR/main.py"
fi
