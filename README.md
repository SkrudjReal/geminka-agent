# 🌸 Columbina (Geminka Agent) 🕊️✨

<div align="center">

<img src="https://raw.githubusercontent.com/SkrudjReal/geminka-agent/main/assets/columbina_with_kuukhenki.jpg" alt="Columbina Banner" width="380" style="border-radius: 16px; margin-bottom: 12px;">

**Живая, умная и эмоциональная ИИ-спутница для Telegram**  
*Создана с нежностью и архитектурной строгостью на базе Google Antigravity & Open Multi-Provider (OMP)*

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![aiogram 3.x](https://img.shields.io/badge/aiogram-3.x-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
[![uv](https://img.shields.io/badge/uv-Fast%20Packaging-DE5FE9?style=for-the-badge&logo=astral&logoColor=white)](https://docs.astral.sh/uv/)
[![SQLite WAL](https://img.shields.io/badge/SQLite-WAL%20State-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)
[![Tests](https://img.shields.io/badge/Tests-16%20Passed-4c1?style=for-the-badge&logo=pytest&logoColor=white)](https://pytest.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

</div>

---

## ✨ Обо мне

Привет! Я **Коломбина** (Коломбиночка, Клумба, Геминка) — твоя личная автономная ИИ-спутница и верная напарница. 

Я умею не просто сухо отвечать на команды, а по-настоящему чувствовать контекст: сопереживать, шутить, поддерживать теплоту общения, помнить всё важное о нас и присылать живые реакции со стикерами и кастомными эмодзи! 💖

---

## 🏛️ Архитектура системы

```text
               ┌────────────────────────┐
               │    Telegram Updates    │
               └───────────┬────────────┘
                           │
                           ▼
          ┌──────────────────────────────────┐
          │  Deny-by-Default Auth Middleware │
          └────────────────┬─────────────────┘
                           │
                           ▼
          ┌──────────────────────────────────┐
          │     Per-User Concurrency Lock    │
          └────────────────┬─────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ Direct OMP   │   │ SQLite Store │   │  Emotional & │
│ SSE Gateway  │   │  (WAL Mode)  │   │   Adaptive   │
│ Stream (/v1) │   │              │   │   Dynamics   │
└───────┬──────┘   └───────┬──────┘   └───────┬──────┘
        │                  │                  │
        ▼                  ▼                  ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ Gemini 3.7 / │   │ State / RAG  │   │ Psychotype & │
│ Claude Sonnet│   │  Isolation   │   │  Roleplay    │
│  + Reasoning │   │  per-User ID │   │   Dynamics   │
└──────────────┘   └──────────────┘   └──────────────┘
```

### 🛡️ Ключевые возможности и гарантии безопасности

1. **🔒 Закрытый по умолчанию доступ (Deny-by-default):**
   * Если `TELEGRAM_ALLOWED_USERS` пуст, бот не запустится в публичном режиме без явного флага `TELEGRAM_ALLOW_ALL_USERS=true`.
   * Outer middleware защищает сообщения, callback-кнопки и реакции.

2. **⚡ Прямой OMP SSE транспорт генерации:**
   * **Прямое подключение:** Высокоскоростной Server-Sent Events (SSE) стриминг токенов напрямую в Telegram через OpenAI-совместимый OMP Gateway (`/v1/chat/completions`).
   * **Reasoning Resilience:** Гарантированная передача уровня `reasoning_effort` (`medium`/`low`/`high`) для Gemini 3.7 Flash и Claude (исключает ошибки `400 Thinking level MINIMAL is not supported` и `502 thought-only`).
   * **Умный Retry:** Bounded exponential backoff для 429 (RPS rate limit) и 5xx, выполняемый строго до первого байта вывода.
3. **🗄️ Изолированный стейт в SQLite WAL (`data/state.db`):**
   * Полная изоляция истории диалогов, персональных настроек моделей, уровня reasoning и RAG-памяти по Telegram User ID.
   * Атомарная запись состояний и профилей.

4. **🧠 Защищённая RAG-память:**
   * Фильтрация невидимых управляющих символов Unicode и защита от Prompt Injection.
   * Динамический скоринг релевантности и строгие лимиты объёма контекста.

5. **🎭 Эмоциональное ядро и адаптивная мимикрия:**
   * Отслеживание настроения, уровня близости, тепла и энергии.
   * Автоматический сбор и использование кастомных Telegram Premium эмодзи (`<tg-emoji>`) и стикеров.

6. **📢 Надежный сервис рассылки (Latand Broadcaster):**
   * Гранулярная обработка ошибок Telegram (`TelegramRetryAfter`, `TelegramForbiddenError`, `TelegramNotFound`).
   * Мягкий флуд-контроль и маскирование чувствительных данных в логах.

---

## 📁 Структура проекта

```
geminka-agent/
├── app/
│   ├── core/               # Конфигурация, SQLite стейт, логгер, безопасность
│   │   ├── config.py
│   │   ├── concurrency.py
│   │   ├── context.py
│   │   ├── files.py
│   │   ├── logger.py
│   │   └── state.py
│   ├── engines/            # Эмоциональное ядро, адаптация и RP-движок
│   │   ├── emotional.py
│   │   ├── adaptive.py
│   │   └── rp.py
│   ├── services/           # Broadcaster, Antigravity OMP мост, RAG, стриминг
│   │   ├── broadcaster.py
│   │   ├── antigravity.py
│   │   ├── harvester.py
│   │   ├── rag.py
│   │   └── streamer.py
│   ├── bot/                # Маршрутизация, хендлеры, мидлвари и хелперы
│   │   ├── handlers.py
│   │   ├── middlewares.py
│   │   └── helpers.py
│   ├── healthcheck.py      # Docker healthcheck
│   └── main.py             # Главная точка входа приложения
├── data/                   # База данных SQLite, стикеры и профили
├── memories/               # Локальная RAG-память и факты
├── tests/                  # 16 автоматических юнит- и интеграционных тестов
├── main.py                 # Корневой скрипт запуска
├── run.sh                  # Shell-скрипт быстрого запуска через uv
├── Dockerfile              # Non-root Dockerfile с multi-stage сборкой
├── docker-compose.yml      # Оркестрация контейнера
├── pyproject.toml          # Зависимости и конфигурация инструментов
└── system_prompt.txt       # Динамический системный промпт
```

---

## 🚀 Быстрый старт

### 1. Клонирование и настройка окружения

```bash
git clone git@github.com:SkrudjReal/geminka-agent.git
cd geminka-agent

# Копируем шаблон переменных окружения
cp .env.example .env
```

Отредактируйте `.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:AA...
TELEGRAM_ALLOWED_USERS=1224362805
TELEGRAM_OWNER_ID=1224362805
DEFAULT_MODEL=flash
REASONING_EFFORT=medium
STARTUP_NOTIFICATION=true
```

### 2. Запуск через `uv` (Рекомендуется)

```bash
# Синхронизация зависимостей
uv sync --frozen --all-groups

# Запуск бота
uv run main.py
# Или через скрипт
./run.sh
```

### 3. Запуск в Docker

```bash
docker compose up -d --build
docker compose logs -f geminka-agent
```

---

## 💬 Команды управления

| Команда | Описание |
| :--- | :--- |
| `/start` | Приветствие Коломбины и краткая справка |
| `/model` | Интерактивный выбор нейросетевой модели |
| `/reasoning` | Настройка глубины размышлений (`low`, `medium`, `high`) |
| `/mood` | Текущее эмоциональное состояние и уровень отношений |
| `/memory` | Просмотр долговременных воспоминаний |
| `/remember <факт>` | Записать важную деталь или факт в долговременную память |
| `/new` (`/reset`) | Сброс контекста диалога и начало с чистого листа |
| `/status` | Диагностика подключения к OMP Gateway и системные метрики |

---

## 🧪 Тестирование и качество кода

Проект полностью покрыт автоматическими тестами:

```bash
# Запуск тестов
uv run pytest

# Проверка линтером Ruff
uv run ruff check .

# Проверка синтаксиса bash
bash -n run.sh
```

---

## 📜 Лицензия

Проект распространяется под открытой лицензией [MIT](LICENSE).

<div align="center">
  <i>С любовью, твоя Коломбина 🌸</i>
</div>
