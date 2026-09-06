#!/usr/bin/env bash

# Runtime helpers for starting the Antigravity IDE language server.
# This file is sourced by run.sh and intentionally does not enable shell options.

ANTIGRAVITY_BOOT_LOG="${ANTIGRAVITY_BOOT_LOG:-${TMPDIR:-/tmp}/geminka_antigravity_bootstrap.log}"
ANTIGRAVITY_WINDOWS_PID=""
ANTIGRAVITY_NATIVE_PID=""
ANTIGRAVITY_NATIVE_PROCESS_GROUP=false
ANTIGRAVITY_STARTED_BY_RUNNER=false

is_antigravity_server_alive() {
    pgrep -f '[l]anguage_server.*--csrf_token' >/dev/null 2>&1
}

antigravity_platform() {
    if [ -n "${GEMINKA_PLATFORM_OVERRIDE:-}" ]; then
        printf '%s\n' "$GEMINKA_PLATFORM_OVERRIDE"
    elif [ -n "${WSL_DISTRO_NAME:-}" ] \
        || grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
        printf 'wsl\n'
    elif [ "$(uname -s)" = "Linux" ]; then
        printf 'linux\n'
    else
        printf 'unsupported\n'
    fi
}

find_wsl_antigravity_executable() {
    local configured="${ANTIGRAVITY_EXE:-}"
    local launcher=""
    local resolved=""
    local candidate=""

    if [ -n "$configured" ] && [ -f "$configured" ]; then
        printf '%s\n' "$configured"
        return 0
    fi

    launcher=$(command -v antigravity 2>/dev/null || true)
    if [ -n "$launcher" ]; then
        resolved=$(readlink -f "$launcher" 2>/dev/null || true)
        if [ -n "$resolved" ]; then
            candidate="$(dirname "$(dirname "$resolved")")/Antigravity IDE.exe"
            if [ -f "$candidate" ]; then
                printf '%s\n' "$candidate"
                return 0
            fi
        fi
    fi

    return 1
}

find_linux_antigravity_launcher() {
    local configured="${ANTIGRAVITY_EXE:-}"
    local candidate=""

    if [ -n "$configured" ] && [ -x "$configured" ]; then
        printf '%s\n' "$configured"
        return 0
    fi

    for candidate in antigravity antigravity-ide; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done

    for candidate in \
        /opt/Antigravity/antigravity \
        /usr/share/antigravity/antigravity; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done

    return 1
}

windows_antigravity_is_running() {
    command -v tasklist.exe >/dev/null 2>&1 || return 1
    tasklist.exe /FI 'IMAGENAME eq Antigravity IDE.exe' 2>/dev/null \
        | tr -d '\r' \
        | grep -Fq 'Antigravity IDE.exe'
}

linux_antigravity_is_running() {
    pgrep -f '[/](antigravity|Antigravity)( |$)' >/dev/null 2>&1
}

launch_wsl_antigravity() {
    local executable="$1"
    local project_dir="$2"
    local encoded_path=""
    local remote_uri=""
    local windows_executable=""
    local powershell_output=""

    encoded_path=$(python3 -c \
        'import sys; from urllib.parse import quote; print(quote(sys.argv[1], safe="/"))' \
        "$project_dir")
    remote_uri="vscode-remote://wsl+${WSL_DISTRO_NAME}${encoded_path}"
    : > "$ANTIGRAVITY_BOOT_LOG"

    if command -v powershell.exe >/dev/null 2>&1 \
        && command -v wslpath >/dev/null 2>&1; then
        windows_executable=$(wslpath -w "$executable")
        if [[ "$windows_executable" != *"'"* && "$remote_uri" != *"'"* ]]; then
            if powershell_output=$(powershell.exe -NoProfile -NonInteractive -Command \
                "\$process = Start-Process -FilePath '$windows_executable' -ArgumentList @('--new-window','--folder-uri','$remote_uri') -WindowStyle Hidden -PassThru; Write-Output \$process.Id" \
                2>>"$ANTIGRAVITY_BOOT_LOG" | tr -d '\r'); then
                ANTIGRAVITY_WINDOWS_PID=$(printf '%s\n' "$powershell_output" | tail -n 1)
                return 0
            fi
        fi
    fi

    "$executable" --new-window --folder-uri "$remote_uri" \
        >>"$ANTIGRAVITY_BOOT_LOG" 2>&1 &
}

launch_linux_antigravity() {
    local launcher="$1"
    local project_dir="$2"
    local headless="${GEMINKA_ANTIGRAVITY_HEADLESS:-true}"
    local -a command=()

    : > "$ANTIGRAVITY_BOOT_LOG"

    if [ "$headless" = "true" ]; then
        if ! command -v xvfb-run >/dev/null 2>&1; then
            echo "❌ Для скрытого запуска Antigravity на Linux нужен xvfb-run (пакет xvfb)."
            echo "   Либо установи xvfb, либо задай GEMINKA_ANTIGRAVITY_HEADLESS=false."
            return 1
        fi
        command=(xvfb-run -a -s '-screen 0 1280x720x24 -nolisten tcp' \
            "$launcher" --disable-gpu --new-window "$project_dir")
    else
        if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
            echo "❌ Нет графической сессии. Установи xvfb и включи headless-режим."
            return 1
        fi
        command=("$launcher" --new-window "$project_dir")
    fi

    if command -v setsid >/dev/null 2>&1; then
        setsid "${command[@]}" >>"$ANTIGRAVITY_BOOT_LOG" 2>&1 &
        ANTIGRAVITY_NATIVE_PROCESS_GROUP=true
    else
        "${command[@]}" >>"$ANTIGRAVITY_BOOT_LOG" 2>&1 &
    fi
    ANTIGRAVITY_NATIVE_PID=$!
}

ensure_antigravity_server() {
    local project_dir="$1"
    local platform=""
    local executable=""
    local timeout_seconds="${ANTIGRAVITY_START_TIMEOUT_SECONDS:-60}"
    local attempt=0
    local antigravity_was_running=false

    if is_antigravity_server_alive; then
        echo "🟢 Antigravity Language Server уже запущен."
        return 0
    fi

    if [ "${GEMINKA_AUTO_START_ANTIGRAVITY:-true}" != "true" ]; then
        echo "❌ Antigravity Language Server не запущен, а автозапуск отключён."
        return 1
    fi

    if ! [[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]]; then
        timeout_seconds=60
    fi

    platform=$(antigravity_platform)
    case "$platform" in
        wsl)
            if ! executable=$(find_wsl_antigravity_executable); then
                echo "❌ Не найден Antigravity IDE.exe. Укажи путь через ANTIGRAVITY_EXE."
                return 1
            fi
            windows_antigravity_is_running && antigravity_was_running=true
            echo "🟡 Запускаем скрытый WSL-сеанс Antigravity..."
            launch_wsl_antigravity "$executable" "$project_dir"
            if [ "$antigravity_was_running" = false ] \
                && [[ "$ANTIGRAVITY_WINDOWS_PID" =~ ^[0-9]+$ ]]; then
                ANTIGRAVITY_STARTED_BY_RUNNER=true
            fi
            ;;
        linux)
            if ! executable=$(find_linux_antigravity_launcher); then
                echo "❌ Не найден Antigravity IDE для Linux. Установи пакет antigravity"
                echo "   или укажи исполняемый файл через ANTIGRAVITY_EXE."
                return 1
            fi
            linux_antigravity_is_running && antigravity_was_running=true
            echo "🟡 Запускаем Antigravity IDE для Linux в скрытом Xvfb-сеансе..."
            launch_linux_antigravity "$executable" "$project_dir"
            if [ "$antigravity_was_running" = false ] \
                && [[ "$ANTIGRAVITY_NATIVE_PID" =~ ^[0-9]+$ ]]; then
                ANTIGRAVITY_STARTED_BY_RUNNER=true
            fi
            ;;
        *)
            echo "❌ Автозапуск Antigravity поддерживается только в WSL и Linux."
            return 1
            ;;
    esac

    for ((attempt = 1; attempt <= timeout_seconds; attempt++)); do
        if is_antigravity_server_alive; then
            echo "🟢 Antigravity Language Server готов (${attempt} сек.)."
            return 0
        fi
        sleep 1
    done

    echo "❌ Antigravity Language Server не запустился за ${timeout_seconds} сек."
    echo "   Лог запуска: $ANTIGRAVITY_BOOT_LOG"
    return 1
}

stop_antigravity_runtime() {
    if [ "$ANTIGRAVITY_STARTED_BY_RUNNER" != "true" ] \
        || [ "${GEMINKA_STOP_ANTIGRAVITY_ON_EXIT:-true}" != "true" ]; then
        return 0
    fi

    if [[ "$ANTIGRAVITY_WINDOWS_PID" =~ ^[0-9]+$ ]] \
        && command -v powershell.exe >/dev/null 2>&1; then
        powershell.exe -NoProfile -NonInteractive -Command \
            "Stop-Process -Id $ANTIGRAVITY_WINDOWS_PID -ErrorAction SilentlyContinue" \
            >/dev/null 2>&1 || true
    fi

    if [[ "$ANTIGRAVITY_NATIVE_PID" =~ ^[0-9]+$ ]] \
        && kill -0 "$ANTIGRAVITY_NATIVE_PID" 2>/dev/null; then
        if [ "$ANTIGRAVITY_NATIVE_PROCESS_GROUP" = "true" ]; then
            kill -TERM -- "-$ANTIGRAVITY_NATIVE_PID" 2>/dev/null || true
        else
            kill -TERM "$ANTIGRAVITY_NATIVE_PID" 2>/dev/null || true
        fi
        wait "$ANTIGRAVITY_NATIVE_PID" 2>/dev/null || true
    fi
}
