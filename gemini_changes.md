# Документация изменений (Gemini / Columbina Agent)

**Дата:** 18 сентября 2026 г.<br>
**Проект:** Columbina (Geminka Agent) — Telegram AI-ассистент на базе Antigravity Connect/SSE<br>
**Статус:** Все изменения протестированы и применены, сервис `geminka.service` активен.

---

## 1. Обзор устранённых проблем

1. **Поиск и запуск Antigravity IDE из WSL (`./run.sh`):**
   - При запуске в WSL скрипт не мог автоматически обнаружить установленный в хостовой Windows исполняемый файл `Antigravity IDE.exe`.
   - Вызовы `powershell.exe` и `tasklist.exe` зависали в WSL из-за открытого дескриптора стандартного ввода (`stdin`).

2. **Зависание стриминга и таймауты OMP Gateway (`Antigravity response timed out`):**
   - Языковой сервер Antigravity по умолчанию запускал диалог в режиме `CONVERSATIONAL_PLANNER_MODE_DEFAULT`, предоставляя моделям (включая Gemini 3.8 Flash) агентские инструменты IDE (`run_command`, `view_file`, `grep_search`).
   - При форматировании ссылок VLESS Reality / HAPP модель пыталась вызвать `run_command` (скрипт замера длины строки `python3 -c ... len(text)`).
   - Языковой сервер переводил шаг в статус ожидания (`status=9` / `WAITING_FOR_USER`), а прокси `open-antigravity` ожидал только текстовый ответ, что приводило к 120-секундному таймауту и ошибке генерации в Telegram.

3. **Переполнение контекста (Context Bloat):**
   - Модуль `harvester.py` помещал описания сотен стикеров целиком в системный промпт (>91 000 символов), создавая избыточную нагрузку на окно контекста.
   - В `palace_memory.py` и `context.py` бюджеты RAG и истории сообщений не учитывали суммарную длину системных инструкций и текущего запроса.

4. **Застревание на устаревших или повреждённых каскадах (Stale Cascades):**
   - Если сервер Antigravity завершал траекторию или сбрасывал каскад, бот продолжал слать запросы с сохранённым `conversation_id`, получая ошибку `trajectory not found` или таймаут.

---

## 2. Детальное описание изменений по компонентам

### 2.1. Скрипты среды и запуска

#### `scripts/runtime/antigravity.sh`
- **Автоматический поиск Windows-версии:**
  Добавлено сканирование путей `/mnt/c/Users/*/AppData/Local/Programs/Antigravity IDE/Antigravity IDE.exe` и `/mnt/c/Program Files/Antigravity IDE/Antigravity IDE.exe` в функции `find_wsl_antigravity_executable()`.
- **Предотвращение зависания WSL:**
  На все вызовы `powershell.exe`, `cmd.exe` и `tasklist.exe` добавлено перенаправление `< /dev/null`, исключающее блокировку процессов на чтении `stdin`.

---

### 2.2. Шлюз `open-antigravity` (Node.js / gRPC-Web)

#### `tools/open-antigravity/src/bridge/grpc.ts`
- **Режим чистой беседы (`CONVERSATIONAL_PLANNER_MODE_READ_ONLY`):**
  В функции `buildCascadeConfig()` при `agenticMode = false` (режим по умолчанию для Telegram-чата) значение `plannerMode` установлено в `'CONVERSATIONAL_PLANNER_MODE_READ_ONLY'`.
- **Отключение автовыполнения команд:**
  Параметр `autoExecutionPolicy` для `runCommand` установлен в `'CASCADE_COMMANDS_AUTO_EXECUTION_OFF'`. Это гарантирует, что модель не получает инструментов исполнения шелл-команд и сразу генерирует текстовый ответ.

#### `tools/open-antigravity/src/converter.ts`
- **Безопасный рабочий каталог:**
  По умолчанию сессии каскадов изолируются в `file:///tmp/antigravity-playground` (или каталоге запроса), исключая случайные попытки модели сканировать рабочее дерево проекта.
- **Прямое извлечение контента:**
  В функции `extractContentAfter()` добавлена поддержка чтения `step.content` и `step.text` наряду со свойствами объекта `step.plannerResponse`.

#### `tools/open-antigravity/src/bridge/statedb.ts`
- **Авторизация через OAuth:**
  Добавлен fallback для извлечения валидных OAuth токенов Google (`ya29...`) из `~/.gemini/antigravity-cli/antigravity-oauth-token` и таблицы `ItemTable` (`antigravityUnifiedStateSync.oauthToken`), если ключ отсутствует в `antigravityAuthStatus`.

---

### 2.3. Системный промпт

#### `system_prompt.md`
- **Добавлен раздел 11 («Критический режим прямого диалога в Telegram»):**
  - Модели строго запрещено вызывать системные функции и инструменты (`run_command`, `view_file`, `grep_search`, `find_by_name`, `bash` и др.).
  - Указано, что контекст Telegram является чисто текстовым, а все справочные данные (память, эмодзи, стикеры) уже присутствуют в промпте.
  - Предписано всегда и без исключений возвращать готовый текст ответа напрямую.

---

### 2.4. Ядро и сервисы Python (`app/`)

#### `app/services/harvester.py`
- **Ограничение контекста стикеров:**
  В функции `format_emojis_prompt_context()` контекст стикеров ограничен: берутся только топ-8 наиболее релевантных паков и до 10 характерных стикеров с краткими описаниями (до 87 символов).
  Размер блока сократился с ~91 КБ до ~5.5 КБ, при этом полный каталог (418+ стикеров) сохранён в локальном JSON для семантического подбора.

#### `app/core/context.py`
- **Динамический расчёт бюджета истории (`history_budget`):**
  В `ContextManager.build_payload_messages()` вычисление лимита истории теперь строго учитывает фактический размер `system_content` и длину текущей реплики пользователя, предотвращая выход за границу `max_input_chars`.

#### `app/services/palace_memory.py`
- **Ограничение RAG-памяти:**
  Верхняя граница JSON-дампа воспоминаний в `format_rag_context()` снижена до 8 000 символов для предотвращения разрастания системного сообщения.

#### `app/services/antigravity.py`
- **Автоматический сброс `conversation_id` при сбоях:**
  В обработчик ошибок `GatewayError` добавлено условие: при возникновении ошибок со словами `"timed out"`, `"timeout"` или `"trajectory"` устаревший `conversation_id` автоматически очищается в `state.db`, после чего запрос повторяется в чистом каскаде без падения сессии.

---

## 3. Результаты тестирования и верификации

1. **Сквозной тест стриминга (`AntigravityClient.generate_stream`):**
   - Протестирован проблемный запрос с длинным списком VLESS Reality (TCP, Vision, gRPC, xhttp, uTLS) и ссылкой маршрутизации HAPP.
   - **Результат:** 79 чанков, 3932 символа, время ответа — моментальный старт стриминга без ожидания и таймаутов.
   - Оформление: корректные теги `<blockquote>`, `<code>`, нативные премиум-эмодзи `<tg-emoji>` и персональный стиль Коломбины.

2. **Статус системного сервиса:**
   - `geminka.service` активен (`active (running)`).
   - OMP Gateway на порту 4000 подключён к Antigravity Language Server (порт 45487).
   - Polling бота `@columbina_ai_bot` успешно запущен.
