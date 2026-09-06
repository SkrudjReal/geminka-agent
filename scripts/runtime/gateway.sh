#!/usr/bin/env bash

# Runtime helpers for building, starting and stopping open-antigravity.
# This file is sourced by run.sh and intentionally does not enable shell options.

OMP_PID=""
OMP_GATEWAY_LOG="${OMP_GATEWAY_LOG:-${TMPDIR:-/tmp}/geminka_omp_gateway.log}"

build_omp_gateway() {
    local gateway_dir="$1"
    local gateway_dist="$2"

    if [ -f "$gateway_dist" ] || [ ! -d "$gateway_dir" ]; then
        return 0
    fi
    if ! command -v npm >/dev/null 2>&1; then
        echo "❌ Gateway не собран, а npm не найден: $gateway_dist"
        return 1
    fi

    echo "🔹 Компиляция TypeScript шлюза open-antigravity..."
    (cd "$gateway_dir" && npm install --silent && npm run build --silent)
}

is_local_omp_url() {
    [[ "$1" =~ ^https?://(127\.0\.0\.1|localhost)(:[0-9]+)?(/|$) ]]
}

omp_endpoint_parts() {
    python3 -c '
import sys
from urllib.parse import urlparse

parsed = urlparse(sys.argv[1])
host = parsed.hostname or "127.0.0.1"
port = parsed.port or (443 if parsed.scheme == "https" else 80)
print(host, port)
' "$1"
}

is_omp_alive() {
    local target="$1"
    local base="${target%/}"
    local gateway_root="${base%/v1}"
    local health_response=""

    if ! health_response=$(curl -fsS --connect-timeout 2 --max-time 3 \
        "$gateway_root/health" 2>/dev/null); then
        return 1
    fi

    python3 -c '
import json
import sys

data = json.loads(sys.argv[1])
ready = (
    data.get("status") == "ok"
    and data.get("servers", 0) > 0
    and data.get("hasApiKey") is True
)
raise SystemExit(0 if ready else 1)
' "$health_response" 2>/dev/null
}

ensure_omp_gateway() {
    local gateway_dist="$1"
    local omp_url="$2"
    local endpoint=""
    local host=""
    local port=""
    local attempt=0

    if is_omp_alive "$omp_url"; then
        echo "🟢 OMP Gateway активен и отвечает на запросы!"
        return 0
    fi

    if ! is_local_omp_url "$omp_url"; then
        echo "❌ Удалённый OMP Gateway не отвечает: $omp_url"
        return 1
    fi
    if [ ! -f "$gateway_dist" ] || ! command -v node >/dev/null 2>&1; then
        echo "❌ Для локального Gateway нужны node и собранный $gateway_dist"
        return 1
    fi

    endpoint=$(omp_endpoint_parts "$omp_url")
    read -r host port <<<"$endpoint"
    : > "$OMP_GATEWAY_LOG"

    echo "🚀 Запуск Open-Antigravity OMP Gateway на ${host}:${port}..."
    PORT="$port" HOST="$host" node "$gateway_dist" >>"$OMP_GATEWAY_LOG" 2>&1 &
    OMP_PID=$!
    echo "🔹 PID Gateway: $OMP_PID (логи: $OMP_GATEWAY_LOG)"

    for ((attempt = 1; attempt <= 30; attempt++)); do
        if is_omp_alive "$omp_url"; then
            echo "🟢 OMP Gateway успешно запущен и готов к работе!"
            return 0
        fi
        sleep 0.5
    done

    echo "❌ OMP Gateway не вышел в готовое состояние."
    echo "   Лог шлюза: $OMP_GATEWAY_LOG"
    return 1
}

stop_omp_gateway() {
    if [ -n "$OMP_PID" ] && kill -0 "$OMP_PID" 2>/dev/null; then
        kill -TERM "$OMP_PID" 2>/dev/null || true
        wait "$OMP_PID" 2>/dev/null || true
    fi
}
