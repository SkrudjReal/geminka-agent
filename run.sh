#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/runtime/antigravity.sh
source "$SCRIPT_DIR/scripts/runtime/antigravity.sh"
# shellcheck source=scripts/runtime/gateway.sh
source "$SCRIPT_DIR/scripts/runtime/gateway.sh"
cd "$SCRIPT_DIR"

echo "================================================================="
echo "  🌸 Columbina (Geminka Agent) — All-in-One Automated Runner 🕊️ "
echo "================================================================="

cleanup() {
    echo ""
    echo "🛑 Остановка Geminka..."
    stop_omp_gateway
    stop_antigravity_runtime
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# --- 1. Python environment ---
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Ошибка: Python 3 не найден в системе. Установи Python 3.10+."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "🔹 Python version: $PY_VERSION"

HAS_UV=false
if command -v uv >/dev/null 2>&1; then
    HAS_UV=true
elif [ -f "$HOME/.local/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$PATH"
    HAS_UV=true
elif [ -f "$HOME/.cargo/bin/uv" ]; then
    export PATH="$HOME/.cargo/bin:$PATH"
    HAS_UV=true
fi

if [ "$HAS_UV" = true ]; then
    echo "🔹 Инициализация окружения через uv..."
    uv sync --quiet
else
    echo "🔹 UV не обнаружен. Настройка стандартного Python venv..."
    if [ ! -d ".venv" ]; then
        python3 -m venv .venv
    fi
    # shellcheck source=/dev/null
    source .venv/bin/activate
    pip install -q --upgrade pip
    pip install -q -e .
fi

# --- 2. Gateway build ---
GATEWAY_DIR="$SCRIPT_DIR/tools/open-antigravity"
GATEWAY_DIST="$GATEWAY_DIR/dist/index.js"
build_omp_gateway "$GATEWAY_DIR" "$GATEWAY_DIST"

# --- 3. Interactive configuration ---
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
        echo "🔹 Создан файл .env из .env.example"
    else
        touch "$ENV_FILE"
    fi
fi

get_env_val() {
    local key="$1"
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null \
        | cut -d '=' -f2- \
        | tr -d '\r"' \
        || true
}

set_env_val() {
    local key="$1"
    local val="$2"
    if grep -qE "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

CURRENT_TOKEN=$(get_env_val "TELEGRAM_BOT_TOKEN")
CURRENT_USERS=$(get_env_val "TELEGRAM_ALLOWED_USERS")

if [ -z "$CURRENT_TOKEN" ] \
    || [ "$CURRENT_TOKEN" = "your_telegram_bot_token_here" ] \
    || [ "$CURRENT_TOKEN" = "your_bot_token_here" ]; then
    echo ""
    echo "🔑 --- Первоначальная настройка авторизации Telegram бота ---"
    read -r -p "👉 Введите Telegram Bot Token (получить в @BotFather): " INPUT_TOKEN
    INPUT_TOKEN=$(echo "$INPUT_TOKEN" | tr -d '[:space:]')

    if [ -z "$INPUT_TOKEN" ]; then
        echo "❌ Ошибка: TELEGRAM_BOT_TOKEN не может быть пустым."
        exit 1
    fi
    set_env_val "TELEGRAM_BOT_TOKEN" "$INPUT_TOKEN"
    echo "✅ Bot Token сохранён в .env"
fi

if [ -z "$CURRENT_USERS" ] || [ "$CURRENT_USERS" = "123456789" ]; then
    echo ""
    echo "👤 --- Настройка доступа (Telegram User ID) ---"
    echo "💡 Свой ID можно узнать через бота @userinfobot в Telegram."
    read -r -p "👉 Введите ваш числовой Telegram ID: " INPUT_ID
    INPUT_ID=$(echo "$INPUT_ID" | tr -d '[:space:]')

    if [ -n "$INPUT_ID" ]; then
        set_env_val "TELEGRAM_ALLOWED_USERS" "$INPUT_ID"
        set_env_val "TELEGRAM_OWNER_ID" "$INPUT_ID"
        echo "✅ Telegram ID $INPUT_ID сохранён в .env"
    fi
fi

if [ -z "$(get_env_val "OMP_BASE_URL")" ]; then
    set_env_val "OMP_BASE_URL" "http://127.0.0.1:4000/v1"
fi
if [ -z "$(get_env_val "DEFAULT_MODEL")" ]; then
    set_env_val "DEFAULT_MODEL" "google-antigravity/gemini-3.7-flash"
fi
if [ -z "$(get_env_val "REASONING_EFFORT")" ]; then
    set_env_val "REASONING_EFFORT" "medium"
fi
if [ -z "$(get_env_val "MAX_OUTPUT_TOKENS")" ]; then
    set_env_val "MAX_OUTPUT_TOKENS" "8192"
fi

# --- 4. Antigravity and OMP Gateway ---
OMP_URL=$(get_env_val "OMP_BASE_URL")
[ -z "$OMP_URL" ] && OMP_URL="http://127.0.0.1:4000/v1"

if is_local_omp_url "$OMP_URL"; then
    ensure_antigravity_server "$SCRIPT_DIR"
fi

echo ""
echo "🔍 Проверка подключения к OMP Gateway ($OMP_URL)..."
ensure_omp_gateway "$GATEWAY_DIST" "$OMP_URL"

# --- 5. Systemd Service Deployment & Launch ---
SERVICE_NAME="geminka.service"
SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="$SYSTEMD_USER_DIR/$SERVICE_NAME"
UV_PATH="$(command -v uv 2>/dev/null || echo "$HOME/.local/bin/uv")"

mkdir -p "$SYSTEMD_USER_DIR"

if [ -f "$SERVICE_FILE" ]; then
    echo ""
    echo "🔹 Сервис systemd $SERVICE_NAME уже существует (пересоздание не требуется)."
else
    echo ""
    echo "⚙️ Регистрация нового systemd сервиса: $SERVICE_FILE..."
    cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Geminka (Columbina) Telegram AI Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$UV_PATH run main.py
Restart=always
RestartSec=3
KillMode=control-group
Environment=PYTHONUNBUFFERED=1
Environment=PATH=$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable "$SERVICE_NAME" --quiet
    echo "✅ Сервис $SERVICE_NAME успешно зарегистрирован и включен в автозапуск!"
fi

# Stop any rogue/manual instance running outside systemd to prevent TelegramConflictError
OLD_PIDS=$(pgrep -f "main.py" 2>/dev/null || true)
if [ -n "$OLD_PIDS" ]; then
    for pid in $OLD_PIDS; do
        if ! systemctl --user status "$SERVICE_NAME" 2>/dev/null | grep -qw "$pid"; then
            echo "🔹 Остановка ручного процесса main.py (PID: $pid) перед запуском сервиса..."
            kill -15 "$pid" 2>/dev/null || true
        fi
    done
    sleep 1
fi

echo "🚀 Запуск systemd сервиса $SERVICE_NAME..."
systemctl --user restart "$SERVICE_NAME"

# Disarm cleanup trap so background processes and gateway remain active
trap - EXIT

sleep 2
if systemctl --user is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo "================================================================="
    echo "  ✅ Сервис systemd $SERVICE_NAME успешно запущен!"
    echo "================================================================="
    echo "  🌸 Бот теперь работает в фоновом автономном режиме 24/7."
    echo ""
    echo "  Полезные команды:"
    echo "    • Статус:      systemctl --user status geminka"
    echo "    • Логи:        journalctl --user -u geminka -f"
    echo "    • Перезапуск:  systemctl --user restart geminka"
    echo "    • Остановка:   systemctl --user stop geminka"
    echo "================================================================="
else
    echo ""
    echo "⚠️ Сервис $SERVICE_NAME не смог запуститься. Последние логи:"
    journalctl --user -u "$SERVICE_NAME" -n 15 --no-pager
    exit 1
fi
