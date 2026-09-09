#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SCRIPT_DIR"

# Ensure common tool directories are in PATH
export PATH="$HOME/.local/bin:$HOME/.nvm/versions/node/v24.12.0/bin:$HOME/.nvm/current/bin:$PATH"

# Source runtime helpers
# shellcheck source=scripts/runtime/antigravity.sh
source "$SCRIPT_DIR/scripts/runtime/antigravity.sh"
# shellcheck source=scripts/runtime/gateway.sh
source "$SCRIPT_DIR/scripts/runtime/gateway.sh"

# 1. Gateway build (if needed)
GATEWAY_DIR="$SCRIPT_DIR/tools/open-antigravity"
GATEWAY_DIST="$GATEWAY_DIR/dist/index.js"
build_omp_gateway "$GATEWAY_DIR" "$GATEWAY_DIST"

# 2. Read configuration
ENV_FILE="$SCRIPT_DIR/.env"
get_env_val() {
    local key="$1"
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null \
        | cut -d '=' -f2- \
        | tr -d '\r"' \
        || true
}

OMP_URL=$(get_env_val "OMP_BASE_URL")
[ -z "$OMP_URL" ] && OMP_URL="http://127.0.0.1:4000/v1"

# 3. Ensure Antigravity Server is running
if is_local_omp_url "$OMP_URL"; then
    ensure_antigravity_server "$SCRIPT_DIR"
fi

# 4. Ensure OMP Gateway is running
ensure_omp_gateway "$GATEWAY_DIST" "$OMP_URL"

# 5. Launch Bot
UV_PATH="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"
exec "$UV_PATH" run main.py
