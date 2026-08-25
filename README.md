# Geminka Agent

Приватный Telegram-компаньон с потоковыми ответами через OpenAI-compatible OMP Gateway, эмоциональным профилем и изолированной долговременной памятью.

## Архитектура

```text
Telegram updates
      │
      ▼
deny-by-default middleware ──► handlers / per-user lock
                                      │
                         ┌────────────┼────────────┐
                         ▼            ▼            ▼
                    OMP transport  SQLite state  local profile JSON
                         │        (dialogue,      (emotions/assets,
                         ▼         prefs, memory)  atomic writes)
                  /v1/chat/completions
```

Ключевые свойства:

- доступ закрыт по умолчанию; публичный режим включается только явным флагом;
- нет доступа к локальной Antigravity IDE, её conversations или инструментам;
- диалоги, model/reasoning overrides и `/remember` хранятся в SQLite отдельно для каждого user ID;
- память ограничена по размеру и фильтрует prompt injection/невидимый Unicode;
- OMP retry выполняется только до первого полученного токена, поэтому частичный ответ не дублируется;
- длинные ответы делятся на Telegram-сообщения, вложения ограничены по размеру и удаляются после чтения;
- контейнер запускается не от root, образы закреплены digest-ами, OMP используется как readiness dependency.

## Быстрый запуск

Требования: Python 3.10+ и [uv](https://docs.astral.sh/uv/), работающий OMP Gateway с OpenAI-compatible endpoints `/v1/models` и `/v1/chat/completions`.

```bash
cp .env.example .env
uv sync --frozen --all-groups
uv run main.py
```

Минимальная приватная конфигурация:

```env
TELEGRAM_BOT_TOKEN=123456789:token
TELEGRAM_ALLOWED_USERS=123456789
TELEGRAM_OWNER_ID=123456789
TELEGRAM_ALLOW_ALL_USERS=false
OMP_BASE_URL=http://127.0.0.1:4000/v1
OMP_API_KEY=
DEFAULT_MODEL=flash
```

Если allowlist пуст и `TELEGRAM_ALLOW_ALL_USERS` не равен `true`, приложение завершится с ненулевым кодом. Для Docker Gateway на хосте обычно задайте:

```env
OMP_BASE_URL=http://host.docker.internal:4000/v1
```

Запуск контейнера:

```bash
docker compose up -d --build
docker compose logs -f geminka-agent
```

## Команды

- `/model` — выбрать модель;
- `/reasoning` — выбрать `low`, `medium` или `high`;
- `/memory` и `/remember` — посмотреть или добавить личные факты;
- `/new` — очистить диалог и model/reasoning overrides;
- `/mood` — эмоциональный профиль;
- `/status` — доступность OMP и текущие настройки.

## Состояние и миграция

Новый state boundary — `data/state.db` (SQLite WAL). Старые `data/sessions.json` и записи в `memories/facts.json` не удаляются и не импортируются автоматически: `MEMORY.md`/`facts.json` читаются только как общие проектные заметки, а новые пользовательские факты пишутся только в SQLite. Резервируйте `data/state.db*` вместе с JSON-профилями.

## Проверки

```bash
uv run ruff check .
uv run pytest
bash -n run.sh
```

CI выполняет lint и тесты на каждый push и pull request.

## Лицензия

[MIT](LICENSE)
