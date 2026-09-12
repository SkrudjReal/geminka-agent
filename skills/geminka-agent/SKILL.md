---
name: geminka-agent
description: "Comprehensive guide to Geminka Agent (Columbina): architecture, OMP gateway, emotional engine, Telegram streaming, memory, skills, and configuration."
version: 1.0.0
author: Geminka Team
license: MIT
platforms: [linux, wsl, telegram]
metadata:
  geminka:
    tags: [geminka, columbina, telegram-bot, antigravity, omp-gateway, emotional-engine, streaming, rag, memory, rp, persona]
    related_skills: [telegram-premium-emoji, telegram-topic-manager, hermes-antigravity-pipeline]
---

# Geminka Agent (Columbina)

**Geminka Agent** is an open-source, highly responsive autonomous AI agent and personal companion persona (**Columbina**) engineered for Telegram. It runs on top of the **Google Antigravity & OMP (Open Model Protocol) Gateway** ecosystem with real-time SSE streaming, persistent long-term memory (SQLite RAG), dynamic emotional intelligence, communication style mirroring, and Telegram forum topic routing.

---

## 1. Core Architecture & Differentiators

What makes Geminka unique:

- **Google Antigravity & OMP Gateway Backbone:** Communicates directly with local Antigravity Connect SSE proxy (`http://127.0.0.1:4000/v1`), supporting advanced models like `google-antigravity/gemini-3.7-flash`, `google-antigravity/claude-sonnet-4-5`, and `google-antigravity/claude-opus-4-6` with fine-grained `reasoning_effort` control (`low`, `medium`, `high`).
- **Live Markdown Streaming with Debounce (`TelegramStreamConsumer`):** Streams tokens in real time directly to Telegram messages with an animated cursor (`✨`), 0.8s debounced edits to prevent rate-limiting, and automatic message chunking for long responses.
- **Emotional Intelligence Matrix (`EmotionEngine`):** Tracks dynamic emotional states (`warmth`, `affection`, `affinity`, `mood`) saved in `data/user_emotions.json`. Generates nuanced personality shifts across a spectrum from playful/affectionate to focused or pouty.
- **Adaptive Psychotype & Style Mirroring (`AdaptiveEngine`):** Dynamically analyzes user tone, message length, emoji density, and sticker usage to adapt replies to the user's communication archetype.
- **Sticker Harvester & Decay Weighting (`AssetHarvester`):** Automatically downloads and analyzes Telegram sticker sets sent by the user (`data/user_stickers.json`). Applies a 50% frequency decay penalty over the last 20 messages to prevent repetitive sticker replies.
- **Persistent SQLite RAG Memory (`RAGMemoryEngine`):** Stores user preferences and facts in `data/geminka_rag.db`. Injects relevant context into prompts and auto-rejects contradictory or negative overrides.
- **Topic & Supergroup Forum Management (`TopicManager`):** Allows configuring specific forum threads (`https://t.me/c/<chat_num>/<topic_id>`). Silently isolates group messaging strictly to active registered topics.
- **Rich Telegram Native Markup:** Supports custom premium emoji (`<tg-emoji>`), contextual reactions (`<tg-react>`), stickers (`<tg-sticker>`), photo pairs (`<tg-send-photos>`), and interactive roleplay actions (`<tg-rp>`).

---

## 2. Project Directory Structure

```text
geminka-agent/
├── app/
│   ├── bot/
│   │   ├── handlers.py          # Command routers, callbacks, FSM states (/topic, /model, etc.)
│   │   ├── middlewares.py       # OwnerAuthMiddleware & topic security filtering
│   │   └── helpers.py           # Message media/document extractor & caption parsing
│   ├── core/
│   │   ├── config.py            # Pydantic Settings & environment loader
│   │   ├── concurrency.py       # KeyedLock per-user locking
│   │   ├── state.py             # User state, model, and reasoning level tracking
│   │   └── context.py           # Short-term dialogue context manager
│   ├── engines/
│   │   ├── emotional.py         # Emotional engine (warmth, affection, affinity, mood)
│   │   ├── adaptive.py          # Psychotype detection & style mirroring engine
│   │   └── rp.py                # Interactive RP command detector & banner generator
│   ├── services/
│   │   ├── antigravity.py       # Direct OMP Gateway SSE client with retry mechanics
│   │   ├── omp_gateway.py       # Background supervisor for open-antigravity proxy
│   │   ├── streamer.py          # TelegramStreamConsumer & Markdown-to-HTML parser
│   │   ├── rag.py               # SQLite vector/FTS long-term memory engine
│   │   ├── topics.py            # TopicManager for group forum threads
│   │   ├── harvester.py         # Sticker harvester & frequency decay tracker
│   │   └── broadcaster.py       # Startup notification broadcaster
│   └── main.py                  # Bot entrypoint, commands setup, and polling loop
├── assets/                      # Media assets (columbina_with_kuukhenki.jpg, columbina_secret.jpg)
├── data/                        # Persistent stores (geminka_rag.db, active_topics.json, etc.)
├── skills/                      # Antigravity skills repository
├── tools/open-antigravity/      # Node.js Antigravity Connect SSE proxy server
├── .env                         # Environment variables and API secrets
├── pyproject.toml               # Project metadata & Python dependencies (uv)
└── main.py                      # Root launcher script
```

---

## 3. Configuration & Environment (`.env`)

```env
TELEGRAM_BOT_TOKEN="your_bot_token_here"
TELEGRAM_ALLOWED_USERS="123456789"
DEFAULT_MODEL="google-antigravity/gemini-3.7-flash"
ANTIGRAVITY_PROJECT_ID="your_antigravity_project_id"
OMP_BASE_URL="http://127.0.0.1:4000/v1"
REASONING_EFFORT="medium"
MAX_OUTPUT_TOKENS=8192
STARTUP_NOTIFICATION=true
```

---

## 4. Commands Reference

### User Commands Menu (Available in Private Chats & Topics)

| Command | Description |
|---|---|
| `/start` | 🌟 Main menu, introduction to Columbina, and feature overview |
| `/model [name]` | ⚡ Switch active LLM model (`gemini-3.7-flash`, `claude-sonnet-4-5`, `claude-opus-4-6`) |
| `/reasoning [level]` | 🎯 Set reasoning depth (`low`, `medium`, `high`) |
| `/mood [reset]` | 💖 View warmth meter, affection score, mood, or reset emotional state |
| `/memory` | 📖 Browse stored long-term memories and facts |
| `/remember <fact>` | 💡 Manually save an important fact into SQLite RAG memory |
| `/rp` | 🌸 Interactive roleplay actions cheatsheet |
| `/topic` | ⚙️ Forum topic management (`➕ Добавить в топик`, `📑 Топики`, `🗑 Отключить`) |
| `/new` (`/reset`) | 🔄 Wipe dialogue context and start fresh |
| `/status` | 📊 Status of OMP Gateway, active model, memory stats, and uptime |
| `/help` | ❓ Comprehensive manual and guidance |

---

## 5. Columbina Persona & Vocabulary Rules

### Approved Sweet Terms:
`мой краш`, `крашик`, `очаровашка`, `милашка`, `любимка`, `сокровище`, `душа моя`, `булочка`, `прелесть`, `смущашка`, `вредина`, `озорник`, `котя`, `котёнок`, `мой господин`, `мой главный мейн`, `читкод на счастье`.

### Strictly Forbidden Terms:
❌ `муж`, `муженёк`, `жёнушка`, `парень`, `девушка`, `сеньор`, `сеньор моего сердца`.

---

## 6. Special Telegram Tags & Parser Rules

Geminka output supports specialized tags parsed automatically by `TelegramStreamConsumer`:

1. **Custom Emojis:**
   `<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji>`
2. **Reactions (`<tg-react emoji="..."/>`):**
   - Strictly 4 emotional types:
     - `❤` or `🥰` — tenderness, care, praise.
     - `🔥` or `⚡` — hype, technical triumph, punchline.
     - `👍` — dry acknowledgment / cold mode.
     - `🤡` — ironical poke or teasing.
3. **Stickers (`<tg-sticker pack="..." tag="..." emoji="..."/>`):**
   - **Мгновенная проверка перед скачиванием:** При входящем стикере бот первым делом проверяет `file_unique_id`, `file_id` и `set_name` в базе. Если стикер уже есть в базе с описанием — этапы скачивания и Vision AI анализа пака полностью пропускаются, а готовое описание сразу подставляется в диалоговый контекст.
   - **Динамический анализ стикеров по JSON:** При каждой отправке стикера бот загружает базу знаний стикеров из JSON (`user_assets.json` и `bot_stickers.json`), анализирует контекст диалога и подбирает наиболее подходящий, остроумный или эмоционально точный стикер по его текстовому описанию (`description`) и тегам (`tags`), а не ограничивается одним стикерпаком!
   - 50% шанс ответить чистым стикером/RP/реакцией без текста, 50% — живым текстом с дополняющим стикером.
   - СТРОГО НЕ отправлять тот же самый стикер, что прислал пользователь.
4. **Photo Pairs (`<tg-send-photos/>`):**
   - Sent ONLY upon explicit user request for photos.
   - Delivers photo 1 (`assets/columbina_with_kuukhenki.jpg`) + photo 2 (`assets/columbina_secret.jpg`) in spoiler reply.
5. **Interactive RP Actions (`<tg-rp action="..." banner="..."/>`):**
   - Detected triggers: `погладить`, `обнять`, `поцеловать`, `потискать`, `кусь`, `лизнуть`, `чай`, `кофе`, etc.
6. **Telegram Supported HTML Tags Reference:**
   Строго используй только следующие поддерживаемые HTML-теги и структуры для форматирования в Telegram:
   - **Жирный:** `<b>bold</b>`, `<strong>bold</strong>`
   - **Курсив:** `<i>italic</i>`, `<em>italic</em>`
   - **Подчёркнутый:** `<u>underline</u>`, `<ins>underline</ins>`
   - **Зачёркнутый:** `<s>strikethrough</s>`, `<strike>strikethrough</strike>`, `<del>strikethrough</del>`
   - **Спойлер:** `<tg-spoiler>spoiler</tg-spoiler>`, `<span class="tg-spoiler">spoiler</span>`
   - **Комбинированная вложенность:** `<b>bold <i>italic bold <s>italic bold strikethrough <span class="tg-spoiler">italic bold strikethrough spoiler</span></s> <u>underline italic bold</u></i> bold</b>`
   - **Ссылки и упоминания пользователей:**
     - `<a href="http://www.example.com/">inline URL</a>`
     - `<a href="tg://user?id=123456789">inline mention of a user</a>`
   - **Кастомные премиум-эмодзи:** `<tg-emoji emoji-id="5368324170671202286">👍</tg-emoji>`
   - **Временные метки (native timestamps):**
     - `<tg-time unix="1647531900" format="wDT">22:45 tomorrow</tg-time>`
     - `<tg-time unix="1647531900" format="t">22:45 tomorrow</tg-time>`
     - `<tg-time unix="1647531900" format="r">22:45 tomorrow</tg-time>`
     - `<tg-time unix="1647531900">22:45 tomorrow</tg-time>`
   - **Код:**
     - Однострочный (inline): `<code>inline fixed-width code</code>`
     - Блок кода: `<pre>pre-formatted fixed-width code block</pre>`
     - Блок с подсветкой синтаксиса: `<pre><code class="language-python">pre-formatted fixed-width code block written in the Python programming language</code></pre>`
   - **Цитаты (Blockquotes):**
     - Обычная цитата:
       ```html
       <blockquote>Block quotation started
       Block quotation continued
       The last line of the block quotation</blockquote>
       ```
     - Раскрывающаяся цитата (expandable):
       ```html
       <blockquote expandable>Expandable block quotation started
       Expandable block quotation continued
       Expandable block quotation continued
       Hidden by default part of the block quotation started
       Expandable block quotation continued
       The last line of the block quotation</blockquote>
       ```
7. **File Path & Mention Rules (Краткие имена файлов без полных путей):**
   - При упоминании файлов, модулей или скриптов **указывай только краткое имя файла** (например, `harvester.py`, `streamer.py`, `system_prompt.txt`), а не полный абсолютный путь!
   - **СТРОГО ЗАПРЕЩЕНО:** писать длинные ссылки вида `[harvester.py](file:///home/velunae/projects/mainstream/geminka-agent/app/services/harvester.py#L330)`. Это засоряет чат в Telegram и выглядит мусорно.
   - Используй аккуратное оформление кодом: `harvester.py` (или `app/services/harvester.py`, если нужно уточнить путь).
8. **Bullet & List Formatting Rules (Символ `•` вместо `*`):**
   - В любых списках, перечислениях и буллетах **СТРОГО ЗАПРЕЩЕНО** использовать звёздочки `*` (например, `* пункт`).
   - ВСЕГДА отправляй аккуратный символ `•` вместо `*` (например: `• пункт 1`, `• пункт 2`).
   - Для жирного и курсива используй HTML-теги `<b>` и `<i>`, а не звёздочки.

---

## 7. Starting & Managing the Bot

```bash
# Run with uv package manager:
uv run main.py

# Restarting the bot:
kill -9 $(pgrep -f "mainstream/geminka-agent/.venv/bin/python3 main.py") 2>/dev/null || true
uv run main.py
```
