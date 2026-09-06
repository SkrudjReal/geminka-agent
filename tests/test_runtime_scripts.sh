#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURE_BIN="$PROJECT_DIR/tests/runtime-fixtures/bin"
TEST_TMP=$(mktemp -d)

cleanup_test() {
    stop_antigravity_runtime 2>/dev/null || true
    case "$TEST_TMP" in
        /tmp/*) rm -r "$TEST_TMP" ;;
    esac
}
trap cleanup_test EXIT

export PATH="$FIXTURE_BIN:$PATH"
export GEMINKA_PLATFORM_OVERRIDE=linux
export GEMINKA_ANTIGRAVITY_HEADLESS=true
export GEMINKA_TEST_READY="$TEST_TMP/language-server.ready"
export GEMINKA_TEST_ARGS="$TEST_TMP/xvfb.args"
export ANTIGRAVITY_BOOT_LOG="$TEST_TMP/antigravity.log"
export ANTIGRAVITY_START_TIMEOUT_SECONDS=3
export ANTIGRAVITY_EXE="$FIXTURE_BIN/antigravity"

# shellcheck source=scripts/runtime/antigravity.sh
source "$PROJECT_DIR/scripts/runtime/antigravity.sh"

[ "$(antigravity_platform)" = "linux" ]
ensure_antigravity_server "$PROJECT_DIR"
[ "$ANTIGRAVITY_STARTED_BY_RUNNER" = "true" ]
grep -Fxq -- '--disable-gpu' "$GEMINKA_TEST_ARGS"
grep -Fxq -- '--new-window' "$GEMINKA_TEST_ARGS"
grep -Fxq -- "$PROJECT_DIR" "$GEMINKA_TEST_ARGS"

stop_antigravity_runtime
if kill -0 "$ANTIGRAVITY_NATIVE_PID" 2>/dev/null; then
    echo "Native Antigravity mock was not stopped" >&2
    exit 1
fi

echo "native Linux runtime bootstrap: OK"
