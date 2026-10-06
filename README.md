# 🌸 Columbina (Geminka Agent) 🕊️✨

<div align="center">

<img src="https://raw.githubusercontent.com/SkrudjReal/geminka-agent/main/assets/columbina_with_kuukhenki.jpg" alt="Columbina Banner" width="380" style="border-radius: 16px; margin-bottom: 12px;">

**Живая, умная и эмоциональная ИИ-спутница для Telegram**<br>
*Создана с нежностью и архитектурной строгостью на базе прямого authenticated `agy` CLI*

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![aiogram 3.x](https://img.shields.io/badge/aiogram-3.x-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
[![uv](https://img.shields.io/badge/uv-Fast%20Packaging-DE5FE9?style=for-the-badge&logo=astral&logoColor=white)](https://docs.astral.sh/uv/)
[![SQLite WAL](https://img.shields.io/badge/SQLite-WAL%20State-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)
[![Tests](https://img.shields.io/badge/Tests-64%20Passed-4c1?style=for-the-badge&logo=pytest&logoColor=white)](https://pytest.org)
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
│ Direct agy   │   │ SQLite Store │   │  MemPalace   │
│ CLI stream-  │   │  (WAL Mode)  │   │  Vector &    │
│ json session │   │              │   │  User Memory │
└───────┬──────┘   └───────┬──────┘   └───────┬──────┘
        │                  │                  │
        ▼                  ▼                  ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ Gemini 3.8 / │   │ Settings &   │   │ Archive,     │
│ Claude 4.6   │   │ Short Context│   │ Facts &      │
│ + Reasoning  │   │ per User ID  │   │ Portraits    │
└──────────────┘   └──────────────┘   └──────────────┘
```

### 🛡️ Ключевые возможности и гарантии безопасности

1. **🔒 Закрытый по умолчанию доступ (Deny-by-default):**
   * Если `TELEGRAM_ALLOWED_USERS` пуст, бот не запустится в публичном режиме без явного флага `TELEGRAM_ALLOW_ALL_USERS=true`.
   * Outer middleware проверяет доступ для сообщений, callback-кнопок и реакций.
   * По умолчанию `agy` и его локальные инструменты работают в sandbox: файлы хоста доступны read-only, запись разрешена внутри проекта; `/tmp` изолирован. На Linux/WSL требуется Bubblewrap (`sudo apt install bubblewrap`); без него запуск модельного процесса отклоняется. Служебное состояние `agy` хранится в игнорируемой папке `data/agy_sandbox/`. Только владелец в личном чате переключает режим для всего бота через `/sandbox on` / `/sandbox off`.

2. **⚡ Прямой транспорт agy CLI:**
   * **Прямое подключение:** по умолчанию Geminka использует установленный и авторизованный `agy` CLI, поддерживает отдельную живую `stream-json` сессию для каждого пользователя и не требует Antigravity IDE, Xvfb или language server.
   * **Выбор модели:** `/model` переключает Gemini 3.8/3.7/3.6 Flash и Claude Sonnet/Opus 4.6; `/reasoning` задаёт глубину рассуждений.
   * **Legacy-шлюз:** OpenAI-совместимый OMP доступен только при явном `AGY_TRANSPORT=omp`.

3. **🗄️ Изолированный стейт в SQLite WAL (`data/state.db`):**
   * Настройки, выбранная модель, reasoning, короткий контекст и идентификаторы диалога хранятся отдельно для каждого Telegram User ID.
   * SQLite остаётся для оперативного состояния; долговременный архив диалогов и факты находятся в MemPalace.

4. **🧠 Долговременная память MemPalace:**
   * Отдельное хранилище каждого пользователя содержит архив Telegram, извлечённые факты и обновляемый портрет; `/recall` выполняет поиск по смыслу.
   * `/debug` временно отключает новые записи в память, `/forget confirm` удаляет личную память и короткий контекст. Подробности и ограничения — в [документации MemPalace](docs/mempalace.md).

5. **🎭 Эмоциональное ядро, стикеры и медиа:**
   * Эмоциональное состояние и адаптивный стиль общения работают отдельно от пользовательского портрета в MemPalace.
   * Стикеры и Premium Emoji выбираются по описаниям из дефолтных каталогов и пользовательской базы; поддерживаются реакции, RP-действия и потоковая отправка файлов.

6. **📢 Публикация постов в Telegram:**
   * Owner-only MCP-мост к Bot API формирует пост с превью-картинкой, отправляет черновик владельцу, затем отдельный комментарий-reply и пересылает фото в канал.
   * Изображения ищутся в Pinterest и выбираются по контакт-листу; повторное использование URL отслеживается в локальном реестре. Стиль можно менять в [`POST_STYLE.md`](POST_STYLE.md), исходный вариант — [`POST_STYLE_DEFAULT.md`](POST_STYLE_DEFAULT.md).
   * Для вызовов требуется отдельно зарегистрировать `geminka-bot-api` в MCP-настройках `agy` CLI; конфигурация и токены в Git не хранятся.

---

## 📁 Структура проекта

```
geminka-agent/
├── app/
│   ├── core/               # Настройки, SQLite, контекст, файлы, безопасность
│   ├── engines/            # Эмоциональное ядро, адаптация и RP-движок
│   ├── services/           # agy/OMP, MemPalace, Bot API/MCP, стикеры и стриминг
│   ├── bot/                # Хендлеры, middleware и Telegram-команды
│   ├── healthcheck.py      # Docker healthcheck
│   └── main.py             # Инициализация и запуск бота
├── data/
│   ├── default_stickers.json # Описания встроенных стикеров
│   ├── default_emojis.json   # Описания встроенных Premium Emoji
│   └── *.example.json        # Примеры runtime-данных
├── docs/mempalace.md       # Устройство и команды долговременной памяти
├── memories/               # Общий обезличенный контекст проекта
├── scripts/
│   ├── select_post_image.py # Поиск и контакт-лист изображений для поста
│   └── start.sh             # Запуск бота из пользовательского systemd-сервиса
├── tests/                  # Автоматические тесты
├── POST_STYLE.md           # Редактируемый стиль постов
├── POST_STYLE_DEFAULT.md   # Исходный стиль для восстановления
├── main.py                 # Корневая точка входа приложения
├── run.sh                  # Настройка и запуск через uv/systemd --user
├── Dockerfile              # Docker-образ без установки agy CLI
├── docker-compose.yml      # Оркестрация контейнера
├── pyproject.toml          # Зависимости и конфигурация инструментов
└── system_prompt.md        # Основные инструкции и образ Коломбины
```

---

## 🚀 Быстрый старт

### 1. Клонирование и настройка окружения

```bash
git clone https://github.com/SkrudjReal/geminka-agent.git
cd geminka-agent

# Копируем шаблон переменных окружения
cp .env.example .env
```

Укажи в `.env` токен от @BotFather, свой числовой Telegram ID и путь к `agy` CLI:

```env
TELEGRAM_BOT_TOKEN=123456789:AA...
TELEGRAM_ALLOWED_USERS=123456789
TELEGRAM_OWNER_ID=123456789
AGY_TRANSPORT=agy
AGY_CLI_PATH=agy
DEFAULT_MODEL=google-antigravity/gemini-3.8-flash
REASONING_EFFORT=high
```

Перед запуском установи и авторизуй `agy` CLI от того же Linux-пользователя, под которым будет работать бот. Проверить доступность можно командой `agy models`.

### 2. Запуск через `uv` (Рекомендуется)

```bash
# Синхронизация зависимостей
uv sync --frozen

# Запуск бота в текущем терминале
uv run python main.py
# Или настройка пользовательского systemd-сервиса
./run.sh
```

`run.sh` создаёт сервис по имени папки проекта: `geminka-agent.service`, `geminka-agent2.service`. При занятом имени другой папкой добавляется цифра (`geminka-agent1.service`); повторный запуск переиспользует сервис этой папки. Старый `geminka.service` переносится только если указывает на этот проект. Чужие процессы не останавливаются. Скрипт не устанавливает и не авторизует `agy` CLI. Фактическое имя выводится при запуске; подставь его ниже при наличии суффикса:

```bash
systemctl --user status geminka-agent.service
journalctl --user -u geminka-agent.service -f
systemctl --user restart geminka-agent.service
```

Для нескольких копий нужны **разные BotFather-токены** и отдельные `.env`, `data/`, `memory/`, `memories/`, `downloads/`: не связывай runtime-папки симлинками и не копируй чужую личную память. Повторный локальный запуск одного bot ID блокируется, включая ручной запуск. Блокировка не охватывает другой сервер или контейнер с отдельным `/tmp`. Состояние AGY хранится отдельно в `data/agy_sandbox/` даже при `/sandbox off`; Bubblewrap нужен для обоих режимов. Авторизация и квоты одного AGY-аккаунта общие. Legacy OMP — внешний сервер: для независимости нужны отдельные gateway URL/порты.

### 3. Запуск в Docker

```bash
docker compose up -d --build
docker compose logs -f geminka-agent
```

Текущий Docker-образ не устанавливает `agy` CLI. В контейнере используй `AGY_TRANSPORT=omp` с доступным из контейнера OMP endpoint либо самостоятельно настрой `agy` CLI и его авторизацию внутри контейнера.

Legacy OMP не обеспечивает эту файловую изоляцию: с sandbox ON генерация через OMP отклоняется. Для OMP владелец должен явно выполнить `/sandbox off`; изоляцию внешнего сервера настраивают отдельно.

---

## 💬 Команды управления

| Команда | Описание |
| :--- | :--- |
| `/start`, `/help` | Приветствие Коломбины и справка |
| `/model` | Выбор Gemini 3.8/3.7/3.6 Flash или Claude Sonnet/Opus 4.6 (только владелец) |
| `/reasoning` | Настройка глубины рассуждений (`low`, `medium`, `high`; только владелец) |
| `/mood` | Текущее эмоциональное состояние и уровень отношений |
| `/memory` | Просмотр сохранённых фактов и долговременной памяти |
| `/remember <факт>` | Запись факта в MemPalace |
| `/portrait` | Просмотр текущего портрета пользователя |
| `/recall <вопрос>` | Семантический поиск по архиву воспоминаний |
| `/forget confirm` | Удаление личной памяти и короткого контекста |
| `/debug [on\|off\|status]` | Временно отключить новые записи в память |
| `/sandbox [on\|off\|status]` | Переключить файловую песочницу для всего бота (только владелец; по умолчанию ON) |
| `/new` (`/reset`) | Сброс короткого контекста и live-сессии |
| `/conv` | Управление диалогом; импорт Conversation ID доступен в legacy OMP |
| `/topic` (`/topics`) | Подключение форум-топиков без префикса (только владелец) |
| `/chats` | Меню групп: добавить по ID/@username/ссылке, список, отключение (только владелец) |
| `/prefix [список\|reset]` | Префиксы групп через запятую; по умолчанию «коломбина», «клумба» (только владелец) |
| `/rp` | Справочник интерактивных RP-действий |
| `/status` | Диагностика транспорта, памяти и системного состояния |

В группе владелец может написать `/chats add`, а в личке — `/chats add -1001234567890` или воспользоваться кнопкой добавления. Для обращения нужен префикс в начале текста/подписи: `коломбина привет`, `клумба, привет`. Реплай не обходит префикс. `/prefix астра, муза` заменяет список для всех обычных групп, `/prefix reset` возвращает стандартные. Подключённые через `/topic` топики отвечают без префикса, даже если группа также включена через `/chats`. Участники должны проходить `TELEGRAM_ALLOWED_USERS` (либо явно включённый `TELEGRAM_ALLOW_ALL_USERS`); настройки и кнопки доступны только владельцу. В группах используются модель/reasoning владельца и отдельный контекст по пользователю/чату/топику, без извлечения личной памяти MemPalace. У других участников файловая запись AGY заблокирована, даже при `/sandbox off`. Это не запрет чтения файлов/сети: не включай публичный доступ для недоверенных пользователей без дополнительной изоляции. Реестр находится в игнорируемом `data/active_topics.json`.

Для получения обычных сообщений в группе сделай бота администратором либо отключи Privacy Mode через BotFather (`/setprivacy`) и пере-добавь его в группу. Иначе Telegram может не доставлять обращения с текстовым префиксом.

---

## 🧪 Тестирование и качество кода

```bash
# Запуск тестов
uv run pytest

# Проверка линтером Ruff
uv run ruff check .

# Проверка синтаксиса bash
bash -n run.sh scripts/start.sh
```

Локальные тесты не проверяют авторизацию `agy`, доступность Telegram, OMP или Pinterest в конкретном окружении.

---

## 📜 Лицензия

Проект распространяется под открытой лицензией [MIT](LICENSE).

<div align="center">
  <i>С любовью, твоя Коломбина 🌸</i>
</div>

# Память MemPalace

Постоянный архив Telegram, векторный поиск и автоматический портрет пользователя:
[настройка, миграция и команды](docs/mempalace.md).
