# 🌸 Columbina (Geminka Agent) 🕊️✨

<div align="center">

<img src="https://raw.githubusercontent.com/SkrudjReal/geminka-agent/main/assets/columbina_with_kuukhenki.jpg" alt="Columbina Banner" width="380" style="border-radius: 16px; margin-bottom: 12px;">

**A lively, intelligent, and emotional AI companion for Telegram**<br>
*Built with care and architectural rigor, powered by the authenticated `agy` CLI*

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![aiogram 3.x](https://img.shields.io/badge/aiogram-3.x-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://docs.aiogram.dev/)
[![uv](https://img.shields.io/badge/uv-Fast%20Packaging-DE5FE9?style=for-the-badge&logo=astral&logoColor=white)](https://docs.astral.sh/uv/)
[![SQLite WAL](https://img.shields.io/badge/SQLite-WAL%20State-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)
[![Tests](https://img.shields.io/badge/Tests-64%20Passed-4c1?style=for-the-badge&logo=pytest&logoColor=white)](https://pytest.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

</div>

---

## ✨ About Me

Hello! I'm **Columbina** (Columbina-chan, Klumba, Geminka) — your personal autonomous AI companion and trusted partner.

I don't just give dry, command-like answers: I follow the conversation, empathize, joke, keep things warm, remember what matters to us, and send lively reactions with stickers and custom emoji! 💖

---

## 🏛️ System Architecture

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

### 🛡️ Key Features and Security

1. **🔒 Deny-by-default access:**
   * If `TELEGRAM_ALLOWED_USERS` is empty, the bot will not run in public mode unless `TELEGRAM_ALLOW_ALL_USERS=true` is explicitly set.
   * Outer middleware checks access for messages, callback queries, and reactions.
   * By default, `agy` and its local tools run in a sandbox: host files are read-only, writes are limited to the project, and `/tmp` is isolated. Linux/WSL requires Bubblewrap (`sudo apt install bubblewrap`); model subprocesses fail closed if it is unavailable. AGY runtime state lives in the ignored `data/agy_sandbox/` directory. Only the owner in a private chat can change the bot-wide mode with `/sandbox on` / `/sandbox off`.

2. **⚡ Direct `agy` CLI transport:**
   * **Direct connection:** Geminka uses the installed and authenticated `agy` CLI by default, maintains a live `stream-json` session per user, and does not require the Antigravity IDE, Xvfb, or a language server.
   * **Model selection:** `/model` switches between Gemini 3.8/3.7/3.6 Flash and Claude Sonnet/Opus 4.6; `/reasoning` sets the reasoning effort.
   * **Legacy gateway:** The OpenAI-compatible OMP transport is available only when `AGY_TRANSPORT=omp` is explicitly set.

3. **🗄️ Isolated SQLite WAL state (`data/state.db`):**
   * Settings, selected model, reasoning effort, short context, and conversation IDs are stored separately for each Telegram user ID.
   * SQLite holds operational state; the long-term conversation archive and facts are stored in MemPalace.

4. **🧠 MemPalace long-term memory:**
   * Each user has a separate store containing a Telegram archive, extracted facts, and an evolving portrait; `/recall` performs semantic search.
   * `/debug` temporarily disables new memory writes. `/forget confirm` deletes that user's memory and short context. See the [MemPalace documentation](docs/mempalace.md) for details and limitations.

5. **🎭 Emotional core, stickers, and media:**
   * Emotional state and adaptive conversation style are separate from the user's MemPalace portrait.
   * Stickers and Premium Emoji are selected using descriptions from the built-in catalogs and user database. Reactions, RP actions, and streamed file delivery are also supported.

6. **📢 Telegram channel publishing:**
   * The owner-only MCP bridge to the Bot API prepares a post with a preview image, sends the photo draft to the owner's private chat, then sends a separate reply-comment and forwards the photo to the channel.
   * Images are found on Pinterest and presented in a contact sheet for selection; previously used URLs are tracked in a local registry. Edit the post style in [`POST_STYLE.md`](POST_STYLE.md); restore the original from [`POST_STYLE_DEFAULT.md`](POST_STYLE_DEFAULT.md).
   * The `geminka-bot-api` server must be registered separately in the `agy` CLI's MCP settings. Its configuration and credentials are not stored in Git.

---

## 📁 Project Structure

```
geminka-agent/
├── app/
│   ├── core/               # Configuration, SQLite, context, files, security
│   ├── engines/            # Emotional, adaptive, and RP engines
│   ├── services/           # agy/OMP, MemPalace, Bot API/MCP, stickers, streaming
│   ├── bot/                # Handlers, middleware, and Telegram commands
│   ├── healthcheck.py      # Docker health check
│   └── main.py             # Bot initialization and startup
├── data/
│   ├── default_stickers.json # Descriptions of built-in stickers
│   ├── default_emojis.json   # Descriptions of built-in Premium Emoji
│   └── *.example.json        # Runtime-data examples
├── docs/mempalace.md       # Long-term memory architecture and commands
├── memories/               # Shared, depersonalized project context
├── scripts/
│   ├── select_post_image.py # Search and contact sheets for post images
│   └── start.sh             # Startup script for the user systemd service
├── tests/                  # Automated tests
├── POST_STYLE.md           # Editable post style
├── POST_STYLE_DEFAULT.md   # Original style for restoration
├── main.py                 # Application entry point
├── run.sh                  # Setup and launch via uv/systemd --user
├── Dockerfile              # Docker image without the agy CLI
├── docker-compose.yml      # Container orchestration
├── pyproject.toml          # Dependencies and tool configuration
└── system_prompt.md        # Columbina's core instructions and persona
```

---

## 🚀 Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/SkrudjReal/geminka-agent.git
cd geminka-agent

# Copy the environment template
cp .env.example .env
```

Set your BotFather token, numeric Telegram user ID, and `agy` CLI path in `.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:AA...
TELEGRAM_ALLOWED_USERS=123456789
TELEGRAM_OWNER_ID=123456789
AGY_TRANSPORT=agy
AGY_CLI_PATH=agy
DEFAULT_MODEL=google-antigravity/gemini-3.8-flash
REASONING_EFFORT=high
```

Install and authenticate the `agy` CLI as the same Linux user that will run the bot. Verify it with `agy models`.

### 2. Run with `uv` (Recommended)

```bash
# Sync dependencies
uv sync --frozen

# Run the bot in the current terminal
uv run python main.py
# Or configure the user systemd service
./run.sh
```

`run.sh` can create and restart `geminka.service` through `systemd --user`. It does not install or authenticate the `agy` CLI. To manage the service manually:

```bash
systemctl --user status geminka
journalctl --user -u geminka -f
systemctl --user restart geminka
```

### 3. Run with Docker

```bash
docker compose up -d --build
docker compose logs -f geminka-agent
```

The current Docker image does not install the `agy` CLI. In the container, use `AGY_TRANSPORT=omp` with an OMP endpoint reachable from the container, or configure the `agy` CLI and its authentication inside the container yourself.

Legacy OMP does not provide this filesystem isolation: generation through OMP is rejected while sandbox is ON. The owner must explicitly use `/sandbox off` for OMP; isolation of that external server is configured separately.

---

## 💬 Bot Commands

| Command | Description |
| :--- | :--- |
| `/start`, `/help` | Columbina's greeting and help |
| `/model` | Choose Gemini 3.8/3.7/3.6 Flash or Claude Sonnet/Opus 4.6 |
| `/reasoning` | Set reasoning effort (`low`, `medium`, `high`) |
| `/mood` | Current emotional state and relationship level |
| `/memory` | View saved facts and long-term memory |
| `/remember <fact>` | Save a fact to MemPalace |
| `/portrait` | View the current user portrait |
| `/recall <question>` | Semantic search through the memory archive |
| `/forget confirm` | Delete personal memory and short context |
| `/debug [on\|off\|status]` | Temporarily disable new memory writes |
| `/sandbox [on\|off\|status]` | Toggle the bot-wide filesystem sandbox (owner only; ON by default) |
| `/new` (`/reset`) | Reset short context and the live session |
| `/conv` | Manage a conversation; Conversation ID import is for legacy OMP |
| `/topic` (`/topics`) | Configure forum topics |
| `/rp` | Interactive RP action guide |
| `/status` | Diagnose transport, memory, and system state |

---

## 🧪 Testing and Code Quality

```bash
# Run tests
uv run pytest

# Run Ruff
uv run ruff check .

# Check shell syntax
bash -n run.sh scripts/start.sh
```

Local tests do not verify `agy` authentication or live access to Telegram, OMP, or Pinterest in your environment.

---

## 📜 License

This project is released under the [MIT License](LICENSE).

<div align="center">
  <i>With love, your Columbina 🌸</i>
</div>

# MemPalace Memory

Persistent Telegram archive, vector search, and an automatically updated user portrait:
[setup, migration, and commands](docs/mempalace.md).
