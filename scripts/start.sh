#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# Ensure common tool directories are in PATH
export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/v24.12.0/bin:$HOME/.nvm/current/bin:$PATH"

# The bot uses the authenticated agy CLI directly. No IDE, Xvfb, or local
# language-server bootstrap is needed here.
UV_PATH="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
exec "$UV_PATH" run main.py
