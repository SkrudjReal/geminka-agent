#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================================="
echo "  🌸 Columbina (Geminka Agent) — All-in-One Automated Runner 🕊️ "
echo "================================================================="

# --- 1. Python Environment & UV Detection ---
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ Ошибка: Python 3 не найден в системе. Установите Python 3.10+."
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "🔹 Python version: $PY_VERSION"

# Ensure UV or Python virtual environment
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
    uv sync --quiet || true
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

# --- 2. Build Open-Antigravity Gateway if needed ---
GATEWAY_DIR="$SCRIPT_DIR/tools/open-antigravity"
GATEWAY_DIST="$GATEWAY_DIR/dist/index.js"

if [ ! -f "$GATEWAY_DIST" ] && [ -d "$GATEWAY_DIR" ]; then
    if command -v npm >/dev/null 2>&1; then
        echo "🔹 Компиляция TypeScript шлюза open-antigravity..."
        (cd "$GATEWAY_DIR" && npm install --silent && npm run build --silent) || true
    fi
fi

# --- 3. Interactive Authorization & Configuration Wizard ---
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

# Function to read value from .env
get_env_val() {
    local key="$1"
    grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | cut -d '=' -f2- | tr -d '\r"' || true
}

# Function to set/update value in .env
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

# If token is default placeholder or empty, prompt interactively
if [ -z "$CURRENT_TOKEN" ] || [ "$CURRENT_TOKEN" = "your_telegram_bot_token_here" ] || [ "$CURRENT_TOKEN" = "your_bot_token_here" ]; then
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

# Ensure OMP defaults in .env
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

# --- 4. OMP Gateway Health Check & Auto-Launch ---
OMP_URL=$(get_env_val "OMP_BASE_URL")
[ -z "$OMP_URL" ] && OMP_URL="http://127.0.0.1:4000/v1"

echo ""
echo "🔍 Проверка подключения к OMP Gateway ($OMP_URL)..."

is_omp_alive() {
    local target="$1"
    local base="${target%/}"
    if curl -s --connect-timeout 2 "$base/models" >/dev/null 2>&1 || \
       curl -s --connect-timeout 2 "$base/v1/models" >/dev/null 2>&1 || \
       curl -s --connect-timeout 2 "http://127.0.0.1:4000/v1/models" >/dev/null 2>&1; then
        return 0
    fi
    return 1
}

OMP_PID=""

if is_omp_alive "$OMP_URL"; then
    echo "🟢 OMP Gateway активен и отвечает на запросы!"
else
    echo "🟡 OMP Gateway не отвечает. Пробуем автоматически поднять шлюз..."
    
    if [ -f "$GATEWAY_DIST" ] && command -v node >/dev/null 2>&1; then
        echo "🚀 Запуск Open-Antigravity OMP Gateway на порту 4000..."
        PORT=4000 HOST=127.0.0.1 nohup node "$GATEWAY_DIST" >/tmp/omp_gateway.log 2>&1 &
        OMP_PID=$!
        echo "🔹 PID фонового OMP Gateway: $OMP_PID (логи: /tmp/omp_gateway.log)"
        
        # Wait up to 6 seconds
        for i in {1..12}; do
            if is_omp_alive "$OMP_URL"; then
                echo "🟢 OMP Gateway успешно запущен и готов к работе!"
                break
            fi
            sleep 0.5
        done
    fi

    if ! is_omp_alive "$OMP_URL"; then
        echo "⚠️  Внимание: OMP Gateway на $OMP_URL поднимется автоматически через main.py."
    fi
fi

# --- 5. Launching Geminka ---
echo ""
echo "🚀 Запуск Geminka Telegram Bot (Columbina)..."
echo "================================================================="

cleanup() {
    echo ""
    echo "🛑 Остановка Geminka..."
    if [ -n "$OMP_PID" ]; then
        kill "$OMP_PID" 2>/dev/null || true
    fi
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

if [ "$HAS_UV" = true ]; then
    uv run main.py
elif [ -f "$SCRIPT_DIR/.venv/bin/python" ]; then
    "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/main.py"
else
    python3 "$SCRIPT_DIR/main.py"
fi
