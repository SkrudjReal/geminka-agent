#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================================="
echo "  🌸 Geminka Agent (Columbina) — Automated 1-Click Deploy 🚀     "
echo "================================================================="

# --- 1. System Dependencies & UV Installer ---
echo "📦 [1/5] Проверка системного окружения..."

if ! command -v python3 >/dev/null 2>&1; then
    echo "⚠️ Python 3 не найден. Установка python3 и venv..."
    if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-venv python3-pip curl
    fi
fi

# Ensure uv is installed
if ! command -v uv >/dev/null 2>&1 && [ ! -f "$HOME/.local/bin/uv" ] && [ ! -f "$HOME/.cargo/bin/uv" ]; then
    echo "⚡ Установка быстрого пакетного менеджера uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
else
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

# --- 2. Install Project Dependencies ---
echo "📦 [2/5] Установка зависимостей Python..."
if command -v uv >/dev/null 2>&1; then
    uv sync
else
    [ ! -d ".venv" ] && python3 -m venv .venv
    # shellcheck source=/dev/null
    source .venv/bin/activate
    pip install -q --upgrade pip
    pip install -q -e .
fi

# --- 3. Build Open-Antigravity Gateway if needed ---
echo "🔧 [3/5] Проверка шлюза Antigravity Connect..."
GATEWAY_DIR="$SCRIPT_DIR/tools/open-antigravity"
GATEWAY_DIST="$GATEWAY_DIR/dist/index.js"

if [ ! -f "$GATEWAY_DIST" ] && [ -d "$GATEWAY_DIR" ]; then
    if command -v npm >/dev/null 2>&1; then
        echo "🔹 Сборка TypeScript шлюза open-antigravity..."
        (cd "$GATEWAY_DIR" && npm install --silent && npm run build --silent) || true
    fi
fi

# --- 4. Environment & Tokens Validation ---
echo "🔑 [4/5] Проверка конфигурации .env..."
ENV_FILE="$SCRIPT_DIR/.env"
ENV_EXAMPLE="$SCRIPT_DIR/.env.example"

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$ENV_EXAMPLE" ]; then
        cp "$ENV_EXAMPLE" "$ENV_FILE"
    else
        touch "$ENV_FILE"
    fi
fi

get_env_val() {
    local key="$1"
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d '=' -f2- | tr -d '\r"' || true
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

TOKEN=$(get_env_val "TELEGRAM_BOT_TOKEN")
if [ -z "$TOKEN" ] || [ "$TOKEN" = "your_telegram_bot_token_here" ] || [ "$TOKEN" = "your_bot_token_here" ]; then
    echo ""
    echo "👉 Введите Telegram Bot Token (получить у @BotFather): "
    read -r INPUT_TOKEN
    INPUT_TOKEN=$(echo "$INPUT_TOKEN" | tr -d '[:space:]')
    if [ -z "$INPUT_TOKEN" ]; then
        echo "❌ Ошибка: Токен бота обязателен!"
        exit 1
    fi
    set_env_val "TELEGRAM_BOT_TOKEN" "$INPUT_TOKEN"
    echo "✅ Токен сохранён в .env"
fi

USERS=$(get_env_val "TELEGRAM_ALLOWED_USERS")
if [ -z "$USERS" ] || [ "$USERS" = "123456789" ]; then
    echo "👉 Введите ваш Telegram User ID (узнать в @userinfobot): "
    read -r INPUT_ID
    INPUT_ID=$(echo "$INPUT_ID" | tr -d '[:space:]')
    if [ -n "$INPUT_ID" ]; then
        set_env_val "TELEGRAM_ALLOWED_USERS" "$INPUT_ID"
        set_env_val "TELEGRAM_OWNER_ID" "$INPUT_ID"
        echo "✅ Telegram ID сохранён в .env"
    fi
fi

# Set sensible defaults
[ -z "$(get_env_val "OMP_BASE_URL")" ] && set_env_val "OMP_BASE_URL" "http://127.0.0.1:4000/v1"
[ -z "$(get_env_val "DEFAULT_MODEL")" ] && set_env_val "DEFAULT_MODEL" "google-antigravity/gemini-3.7-flash"
[ -z "$(get_env_val "REASONING_EFFORT")" ] && set_env_val "REASONING_EFFORT" "medium"
[ -z "$(get_env_val "MAX_OUTPUT_TOKENS")" ] && set_env_val "MAX_OUTPUT_TOKENS" "8192"

# --- 5. Launch Bot ---
echo "🚀 [5/5] Запуск Geminka Agent..."
echo "================================================================="

if command -v uv >/dev/null 2>&1; then
    exec uv run main.py "$@"
else
    # shellcheck source=/dev/null
    source .venv/bin/activate
    exec python3 main.py "$@"
fi
