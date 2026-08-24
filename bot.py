#!/usr/bin/env python3
"""Geminka Telegram Bot powered by Google Antigravity & OMP Gateway (aiogram 3.x).

Features:
- Full OMP Gateway integration (OpenAI-compatible /v1/chat/completions with SSE streaming)
- Multi-model routing (Gemini 3.7 Flash, Claude Sonnet 4.5, Claude Opus) with /model command
- Dynamic Emotional Engine (Moods, Energy, Affection, Affinity & Relationship Stages)
- Inbound & Outbound Telegram Reactions (<tg-react emoji="💖"/> and message_reaction events)
- Custom Emoji entity detection & cataloging (get_custom_emoji_stickers)
- Dual Photo delivery with spoiler reply (<tg-send-photos/>)
- Full reply-to-message & attachment context (text, documents, code, photos, stickers)
- Markdown -> Telegram HTML rich formatting with native <tg-emoji> & <tg-sticker> support
- Live streaming token edits with adaptive cursor (Hermes Agent architecture)
- Think/Reasoning tag suppression
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from aiogram import Bot, Dispatcher, F, Router, types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
)
from aiogram.utils.chat_action import ChatActionSender

import config
from adaptive_engine import adaptive_engine
from antigravity_bridge import AVAILABLE_MODELS, AntigravityClient
from asset_harvester import asset_harvester
from emotional_engine import MOOD_DEFINITIONS, emotion_engine
from stream_consumer import TelegramStreamConsumer, md_to_telegram_html

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("geminka-bot")

if not config.BOT_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN is missing! Please configure it in .env")
    sys.exit(1)

# State & Antigravity Client
sessions: Dict[str, str] = {}
client = AntigravityClient()
router = Router()

CUSTOM_EMOJIS_FILE = config.BASE_DIR / "custom_emojis.json"

TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".css", ".scss", ".json",
    ".md", ".txt", ".yaml", ".yml", ".sh", ".bash", ".zsh", ".sql", ".env",
    ".csv", ".xml", ".rs", ".go", ".c", ".cpp", ".h", ".hpp", ".java", ".kt",
    ".php", ".rb", ".lua", ".ini", ".conf", ".toml", ".dockerfile", ".log",
}


def load_sessions() -> None:
    global sessions
    if config.SESSIONS_FILE.exists():
        try:
            with open(config.SESSIONS_FILE, "r", encoding="utf-8") as f:
                sessions = json.load(f)
                logger.info(f"Loaded {len(sessions)} active sessions.")
        except Exception as e:
            logger.warning(f"Failed to load sessions: {e}")


def save_sessions() -> None:
    try:
        with open(config.SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(sessions, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Failed to save sessions: {e}")


async def send_response(message: types.Message, text: str) -> None:
    """Sends text in chunks <= 4000 characters, rendered as clean Telegram HTML."""
    max_len = 4000
    chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)] or [""]
    for chunk in chunks:
        html_chunk = md_to_telegram_html(chunk)
        try:
            await message.answer(html_chunk, parse_mode=ParseMode.HTML)
        except TelegramBadRequest:
            await message.answer(chunk, parse_mode=None)


def check_auth(user_id: int) -> bool:
    return not config.ALLOWED_USERS or user_id in config.ALLOWED_USERS


async def download_telegram_file(bot: Bot, file_id: str, filename: str) -> Path:
    """Downloads a file from Telegram into the downloads directory."""
    file_obj = await bot.get_file(file_id)
    safe_name = filename.replace("/", "_").replace("\\", "_")
    ts = time.strftime("%Y%m%d_%H%M%S")
    dest_path = config.DOWNLOADS_DIR / f"{ts}_{safe_name}"
    await bot.download_file(file_obj.file_path, dest_path)
    return dest_path


def read_text_file_preview(path: Path, max_bytes: int = 65536) -> Optional[str]:
    """Reads a text/code file for prompt embedding."""
    try:
        if path.stat().st_size > max_bytes:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read(max_bytes) + "\n... [файл обрезан из-за размера]"
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None


async def inspect_custom_emojis(message: types.Message, bot: Bot) -> List[Dict]:
    """Inspects message entities for custom_emoji, resolves stickers/packs, and stores them."""
    if not message.entities:
        return []

    discovered = []
    custom_ids = [
        ent.custom_emoji_id
        for ent in message.entities
        if ent.type == "custom_emoji" and ent.custom_emoji_id
    ]

    if not custom_ids:
        return []

    try:
        stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=custom_ids)
        for st in stickers:
            info = {
                "custom_emoji_id": st.custom_emoji_id,
                "emoji": st.emoji,
                "set_name": st.set_name,
                "file_id": st.file_id,
                "is_animated": st.is_animated,
                "is_video": st.is_video,
            }
            discovered.append(info)
            asset_harvester.register_custom_emoji(
                user_id=message.from_user.id,
                custom_emoji_id=st.custom_emoji_id,
                emoji_char=st.emoji or "✨",
                set_name=st.set_name,
            )
    except Exception as e:
        logger.debug(f"Could not fetch custom emoji stickers: {e}")

    if discovered and CUSTOM_EMOJIS_FILE.exists():
        try:
            with open(CUSTOM_EMOJIS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:
            saved = []

        existing_ids = {item["custom_emoji_id"] for item in saved if "custom_emoji_id" in item}
        new_items = [d for d in discovered if d["custom_emoji_id"] not in existing_ids]
        if new_items:
            saved.extend(new_items)
            with open(CUSTOM_EMOJIS_FILE, "w", encoding="utf-8") as f:
                json.dump(saved, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved {len(new_items)} new custom emojis.")

    return discovered


async def extract_message_context(message: types.Message, bot: Bot) -> str:
    """Deep inspection of message context: text, custom emojis, replies, documents, code, photos, stickers."""
    parts = []

    # 1. Custom Emoji inspection
    custom_emojis = await inspect_custom_emojis(message, bot)
    emoji_hint = ""
    if custom_emojis:
        em_list = [f"custom_emoji(id={ce['custom_emoji_id']}, emoji={ce.get('emoji')}, pack={ce.get('set_name')})" for ce in custom_emojis]
        emoji_hint = f"\n[Использованы кастомные эмодзи: {', '.join(em_list)}]"

    # 2. Reply Context Inspection
    reply_context = ""
    if message.reply_to_message:
        orig = message.reply_to_message
        sender_name = orig.from_user.full_name if orig.from_user else "Собеседник"
        orig_text = orig.text or orig.caption or ""

        orig_media_info = ""
        if orig.photo:
            try:
                dest = await download_telegram_file(bot, orig.photo[-1].file_id, "reply_photo.jpg")
                orig_media_info = f"\n[Прикрепленное фото: путь {dest}]"
            except Exception as e:
                orig_media_info = f"\n[Фото (ошибка скачивания: {e})]"
        elif orig.document:
            try:
                dest = await download_telegram_file(bot, orig.document.file_id, orig.document.file_name or "reply_doc")
                preview = ""
                if Path(orig.document.file_name or "").suffix.lower() in TEXT_EXTENSIONS:
                    content = read_text_file_preview(dest)
                    if content:
                        preview = f"\nСодержимое документа:\n```\n{content}\n```"
                orig_media_info = f"\n[Прикрепленный документ: {orig.document.file_name}, путь {dest}]{preview}"
            except Exception as e:
                orig_media_info = f"\n[Документ (ошибка скачивания: {e})]"
        elif orig.sticker:
            st = orig.sticker
            orig_media_info = f"\n[Стикер: emoji={st.emoji}, pack={st.set_name}, file_id={st.file_id}]"

        reply_context = (
            f"[В ответ на сообщение от {sender_name}]:\n"
            f'"""\n{orig_text}{orig_media_info}\n"""\n\n'
        )

    # 3. Main Message Content
    main_text = message.text or message.caption or ""

    # 4. Inbound Media Handling (Photo, Document, Sticker)
    media_info = ""
    if message.photo:
        try:
            dest = await download_telegram_file(bot, message.photo[-1].file_id, "user_photo.jpg")
            media_info = f"\n[Пользователь прикрепил фото: путь {dest}]"
        except Exception as e:
            media_info = f"\n[Пользователь прикрепил фото, ошибка скачивания: {e}]"
    elif message.document:
        doc = message.document
        try:
            dest = await download_telegram_file(bot, doc.file_id, doc.file_name or "user_doc")
            preview = ""
            if Path(doc.file_name or "").suffix.lower() in TEXT_EXTENSIONS:
                content = read_text_file_preview(dest)
                if content:
                    preview = f"\nСодержимое файла {doc.file_name}:\n```\n{content}\n```"
            media_info = f"\n[Пользователь прикрепил файл: {doc.file_name}, путь {dest}]{preview}"
        except Exception as e:
            media_info = f"\n[Пользователь прикрепил файл: {doc.file_name}, ошибка: {e}]"
    elif message.sticker:
        st = message.sticker
        asset_harvester.register_sticker(
            user_id=message.from_user.id,
            file_id=st.file_id,
            emoji=st.emoji or "🌸",
            set_name=st.set_name,
            is_animated=st.is_animated,
            is_video=st.is_video,
        )
        media_info = f"\n[Пользователь отправил стикер: emoji={st.emoji}, pack={st.set_name}, file_id={st.file_id}]"

    full_content = f"{reply_context}{main_text}{emoji_hint}{media_info}".strip()
    return full_content


# --- Telegram Reactions Handler ---
@router.message_reaction()
async def handle_message_reaction(event: types.MessageReactionUpdated, bot: Bot):
    """Handles reactions put by the user on the bot's messages."""
    user_id = event.user.id if event.user else (event.actor_chat.id if event.actor_chat else 0)
    if not check_auth(user_id):
        return

    added = event.new_reaction

    added_repr = []
    for r in added:
        if isinstance(r, ReactionTypeEmoji):
            added_repr.append(f"emoji:{r.emoji}")
            emotion_engine.update_from_reaction(user_id, r.emoji, is_custom=False)
        elif isinstance(r, ReactionTypeCustomEmoji):
            added_repr.append(f"custom_emoji:{r.custom_emoji_id}")
            emotion_engine.update_from_reaction(user_id, r.custom_emoji_id, is_custom=True)

    if added_repr:
        logger.info(f"User {user_id} added reactions: {', '.join(added_repr)} on message {event.message_id}")


# --- Command Handlers ---
@router.message(Command("start"))
async def cmd_start(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    greeting = (
        "👋 **Привет! Я Geminka** — живой автономный AI-ассистент на базе **Google Antigravity & OMP Gateway**.\n\n"
        "⚡ **Возможности:**\n"
        "• Real-time SSE стриминг токенов через OMP Gateway (`/v1/chat/completions`)\n"
        "• Мульти-модельность: переключение между Gemini 3.7 Flash, Claude Sonnet 4.5, Claude Opus (`/model`)\n"
        "• Динамическая эмоциональная система, реакции и отношения\n"
        "• Полная поддержка цитирования (реплаев), файлов/кода, стикеров и кастомных эмодзи\n"
        "• Telegram Premium Emoji и нативные стикеры по эмоциям\n\n"
        "🛠 **Команды:**\n"
        "• `/model` — выбрать модель (Gemini 3.7 / Claude Sonnet / Claude Opus)\n"
        "• `/mood` — моё текущее настроение и статус отношений\n"
        "• `/new` — начать новую сессию / очистить историю\n"
        "• `/status` — информация о текущем подключении и OMP Gateway\n"
        "• `/help` — помощь"
    )
    await send_response(message, greeting)


@router.message(Command("model", "models"))
async def cmd_model(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    args = message.text.split()[1:] if message.text else []
    if args:
        target = args[0].lower()
        if "claude" in target or "sonnet" in target:
            new_model = "google-antigravity/claude-sonnet-4-5"
        elif "opus" in target:
            new_model = "google-antigravity/claude-opus-4-6"
        elif "3.6" in target:
            new_model = "google-antigravity/gemini-3.6-flash"
        else:
            new_model = "google-antigravity/gemini-3.7-flash"

        client.set_user_model(message.from_user.id, new_model)
        msg_html = md_to_telegram_html(
            f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Модель успешно изменена на:**\n`{new_model}`'
        )
        await message.answer(msg_html, parse_mode=ParseMode.HTML)
        return

    current = client.get_user_model(message.from_user.id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚡ Gemini 3.7 Flash (Default)",
                    callback_data="set_model:google-antigravity/gemini-3.7-flash",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎭 Claude Sonnet 4.5",
                    callback_data="set_model:google-antigravity/claude-sonnet-4-5",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🧠 Claude Opus 4.6",
                    callback_data="set_model:google-antigravity/claude-opus-4-6",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚡ Gemini 3.6 Flash",
                    callback_data="set_model:google-antigravity/gemini-3.6-flash",
                )
            ],
        ]
    )

    text = (
        f'<tg-emoji emoji-id="5359450562079242286">🌟</tg-emoji> **Выбор активной модели:**\n\n'
        f"• **Текущая модель:** `{current}`\n"
        f"• **Reasoning Effort:** `{client.get_user_reasoning(message.from_user.id)}`\n"
        f"• **Max Output Tokens:** `{config.MAX_OUTPUT_TOKENS}`\n\n"
        f"Выбери желаемую модель кнопкой ниже или напиши `/model sonnet` / `/model flash`:"
    )
    await message.answer(md_to_telegram_html(text), reply_markup=keyboard, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("set_model:"))
async def process_set_model(callback: types.CallbackQuery):
    model_name = callback.data.split(":", 1)[1]
    client.set_user_model(callback.from_user.id, model_name)
    await callback.answer(f"Модель выбрана: {model_name}")
    try:
        msg_html = md_to_telegram_html(
            f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Активная модель обновлена:**\n`{model_name}`'
        )
        await callback.message.edit_text(msg_html, parse_mode=ParseMode.HTML)
    except Exception:
        pass


@router.message(Command("reasoning", "effort", "thinking"))
async def cmd_reasoning(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    args = message.text.split()[1:] if message.text else []
    if args:
        val = args[0].lower()
        if val in ["low", "низкий", "1"]:
            new_effort = "low"
        elif val in ["high", "высокий", "3"]:
            new_effort = "high"
        else:
            new_effort = "medium"

        client.set_user_reasoning(message.from_user.id, new_effort)
        msg_html = md_to_telegram_html(
            f'<tg-emoji emoji-id="5359450562079242286">🌟</tg-emoji> **Уровень Reasoning Effort установлен на:** `{new_effort}`'
        )
        await message.answer(msg_html, parse_mode=ParseMode.HTML)
        return

    current = client.get_user_reasoning(message.from_user.id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if current == 'low' else ''}⚡ Low (Быстрый)",
                    callback_data="set_reasoning:low",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if current == 'medium' else ''}🎯 Medium (Рекомендуемый / Баланс)",
                    callback_data="set_reasoning:medium",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"{'✅ ' if current == 'high' else ''}🧠 High (Глубокий анализ)",
                    callback_data="set_reasoning:high",
                )
            ],
        ]
    )

    text = (
        f'<tg-emoji emoji-id="5359450562079242286">🌟</tg-emoji> **Настройка Reasoning Effort (Глубина мыслей):**\n\n'
        f"• **Текущий уровень:** `{current}`\n\n"
        f'<tg-emoji emoji-id="5363859217159582224">📖</tg-emoji> **Уровни:**\n'
        f"• `low` — быстрые минимальные размышления\n"
        f"• `medium` — **оптимальный баланс** качества и скорости (рекомендация скилла)\n"
        f"• `high` — глубокий анализ кода и сложных задач\n\n"
        f"Выбери уровень кнопкой ниже или напиши `/reasoning medium` / `/reasoning high`:"
    )
    await message.answer(md_to_telegram_html(text), reply_markup=keyboard, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("set_reasoning:"))
async def process_set_reasoning(callback: types.CallbackQuery):
    effort_val = callback.data.split(":", 1)[1]
    client.set_user_reasoning(callback.from_user.id, effort_val)
    await callback.answer(f"Reasoning установлен: {effort_val}")
    try:
        msg_html = md_to_telegram_html(
            f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Уровень Reasoning Effort обновлен:**\n`{effort_val}`'
        )
        await callback.message.edit_text(msg_html, parse_mode=ParseMode.HTML)
    except Exception:
        pass


@router.message(Command("mood", "emotions", "relationship"))
async def cmd_mood(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    state = emotion_engine.get_state(message.from_user.id)
    mood_ru = MOOD_DEFINITIONS.get(state.mood, (state.mood, "", 70))[1]
    stage = state.get_relationship_stage()
    stage_desc = state.get_stage_description()

    mood_icons = {
        "playful": "😈 Игривое",
        "affectionate": "💖 Нежное / Тёплое",
        "thoughtful": "📖 Задумчивое",
        "cheerful": "✨ Жизнерадостное",
        "focused": "🎯 Собранное (Focus Mode)",
        "pouty": "🥺 Обижулька",
        "tired": "☕ Уставшее",
        "cold": "❄️ Холодное / Дистанция",
    }
    mood_display = mood_icons.get(state.mood, state.mood)

    text = (
        f"🌸 **Эмоциональное состояние Geminka:**\n\n"
        f"• **Настроение:** `{mood_display}`\n"
        f"  _{mood_ru}_\n"
        f"• **Теплота общения:** `{state.warmth}/100` 🔥\n"
        f"• **Энергия:** `{state.energy}/100` ⚡\n"
        f"• **Привязанность:** `{state.affection}/100` 💓\n\n"
        f"🤝 **Наши отношения:**\n"
        f"• **Статус:** `{stage}` (Очки связи: `{state.affinity}`)\n"
        f"• **Вайб:** _{stage_desc}_\n"
        f"• **Всего диалогов:** `{state.total_interactions}`"
    )
    await send_response(message, text)


@router.message(Command("new", "reset"))
async def cmd_new(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    user_key = str(message.from_user.id)
    sessions.pop(user_key, None)
    save_sessions()
    client.clear_history(message.from_user.id)
    await message.answer("✨ Контекст и история сброшены! Начинаем диалог с чистого листа.")


@router.message(Command("memory", "memories"))
async def cmd_memory(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    from rag_memory import rag_engine

    all_mems = rag_engine.get_all_memories_list()
    total = len(all_mems)

    preview_items = []
    for i, mem in enumerate(all_mems[:6], 1):
        clean_item = mem.strip().replace("\n", " ")
        if len(clean_item) > 120:
            clean_item = clean_item[:117] + "..."
        preview_items.append(f"• {clean_item}")

    preview_text = "\n".join(preview_items) or "Пока нет сохранённых записей."

    text = (
        f'<tg-emoji emoji-id="5363859217159582224">📖</tg-emoji> **Долговременная RAG Память:**\n\n'
        f"• **Всего фрагментов памяти:** `{total}`\n"
        f"• **Источники:** `Hermes Memories (USER.md, MEMORY.md) + Local RAG`\n"
        f"• **Sliding Context Window:** `Active (15 turns / 24k chars)`\n\n"
        f"🧠 **Примеры сохранённых фактов:**\n"
        f"{preview_text}\n\n"
        f"💡 Чтобы сохранить новый факт, напиши:\n`/remember Твой факт или заметка`"
    )
    await send_response(message, text)


@router.message(Command("remember"))
async def cmd_remember(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    from rag_memory import rag_engine

    args = message.text.split(maxsplit=1)[1:] if message.text else []
    if not args or not args[0].strip():
        await message.answer(
            "💡 Напиши факт, который нужно запомнить:\n`/remember Я люблю чай с бергамотом`",
            parse_mode=ParseMode.HTML,
        )
        return

    fact_text = args[0].strip()
    rag_engine.add_memory(fact_text, category="user_custom")

    await message.answer(
        f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Запомнила и зафиксировала в RAG памяти:**\n> {fact_text}',
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("status"))
async def cmd_status(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    from rag_memory import rag_engine

    is_omp_alive = await client.check_omp_health()
    current_model = client.get_user_model(message.from_user.id)
    current_reasoning = client.get_user_reasoning(message.from_user.id)
    state = emotion_engine.get_state(message.from_user.id)
    total_memories = len(rag_engine.chunks)

    engine_status = (
        f"🟢 OMP Gateway (`{config.OMP_BASE_URL}`) [Active]"
        if is_omp_alive
        else "🟡 Local Antigravity Engine (Fallback Active)"
    )

    status_text = (
        f'<tg-emoji emoji-id="5359450562079242286">🌟</tg-emoji> **Статус подключения:**\n\n'
        f"• **Движок:** `{engine_status}`\n"
        f"• **Активная модель:** `{current_model}`\n"
        f"• **Reasoning Effort:** `{current_reasoning}`\n"
        f"• **Sliding Context Window:** `15 turns / 24k chars`\n"
        f"• **RAG Память:** `{total_memories} фрагментов (Hermes + Local)`\n"
        f"• **Теплота:** `{state.warmth}/100` | **Отношения:** `{state.get_relationship_stage()}`\n"
        f"• **Авторизация:** `Google Antigravity OAuth (Active)`"
    )
    await send_response(message, status_text)


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    help_text = (
        f'<tg-emoji emoji-id="5363859217159582224">📖</tg-emoji> **Доступные команды:**\n\n'
        "• `/model` — выбор модели (Gemini 3.7 / Claude Sonnet / Claude Opus)\n"
        "• `/reasoning` — настройка глубины размышлений (low, medium, high)\n"
        "• `/memory` — посмотреть долговременную память и факты\n"
        "• `/remember` — добавить новый факт в память\n"
        "• `/mood` — текущее настроение, теплота и статус отношений\n"
        "• `/new` — сбросить контекст диалога\n"
        "• `/status` — статус OMP Gateway, RAG памяти и параметров\n\n"
        "Ставь реакции, отправляй стикеры, файлы или цитируй сообщения реплаем — всё учитывается!"
    )
    await send_response(message, help_text)


import html
from rp_engine import detect_rp_command, get_random_rp_phrase

@router.message()
async def handle_any_message(message: types.Message, bot: Bot):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    raw_user_content = await extract_message_context(message, bot)
    if not raw_user_content:
        return

    # 1. Check for Inbound RP Command
    user_text = message.text or message.caption or ""
    rp_info = detect_rp_command(user_text)
    user_mention = f'<b><a href="tg://user?id={message.from_user.id}">{html.escape(message.from_user.first_name)}</a></b>'

    if rp_info:
        action, extra_text = rp_info
        if message.reply_to_message and message.reply_to_message.from_user:
            orig_user = message.reply_to_message.from_user
            if orig_user.id == bot.id:
                target_mention = "<b>Коломбина</b>"
            else:
                target_mention = f'<b><a href="tg://user?id={orig_user.id}">{html.escape(orig_user.first_name)}</a></b>'
        else:
            target_mention = "<b>Коломбина</b>"

        rp_banner = get_random_rp_phrase(action, user_mention, target_mention)
        if rp_banner:
            await message.reply(rp_banner, parse_mode=ParseMode.HTML)

        # Sweet RP actions boost warmth & affection
        if action in ['погладить', 'обнять', 'поцеловать', 'потискать', 'чай', 'покормить', 'кусь']:
            state = emotion_engine.get_state(message.from_user.id)
            state.affection = min(100, state.affection + 5)
            state.warmth = min(100, state.warmth + 4)
            state.affinity += 3
            state.mood = "affectionate" if state.warmth > 80 else "playful"
            emotion_engine.save_state()

        # If user only sent the RP action (no extra text), Columbina can react and give a short sweet reply
        if not extra_text:
            raw_user_content = f"[{user_mention} выполнил(-а) RP-действие: «{action}» по отношению к Коломбине. Отреагируй на это взаимно, нежно, эмоционально и в характере!]"

    # Realistic sticker response logic (50% pure sticker/RP/reaction, 50% with text)
    import random
    is_pure_sticker = bool(message.sticker and not message.caption)
    if is_pure_sticker:
        allow_text = random.random() < 0.50
        if not allow_text:
            raw_user_content += (
                "\n[РЕАЛИСТИЧНЫЙ ОТВЕТ НА СТИКЕР (50% шанс — БЕЗ ТЕКСТА)]:\n"
                "• Пользователь отправил стикер. Ответь реплаем ТОЛЬКО стикером (<tg-sticker .../>), "
                "RP-действием (<tg-rp action=\"...\"/>) или реакцией (<tg-react emoji=\"...\"/>) БЕЗ КАКОГО-ЛИБО ТЕКСТА!\n"
                "• ПРАВИЛО: НЕ копируй тот же самый стикер! Подбери из сохранённых стикерпаков подходящий по смыслу, дополняющий или остроумный стикер к текущей ситуации."
            )
        else:
            raw_user_content += (
                "\n[РЕАЛИСТИЧНЫЙ ОТВЕТ НА СТИКЕР (50% шанс — С ТЕКСТОМ)]:\n"
                "• Пользователь отправил стикер. Ответь короткой живой репликой вместе с подходящим дополняющим стикером (<tg-sticker .../>) из твоих сохранённых паков (не копируя тот же самый)."
            )

    # Update emotional state
    emotion_engine.update_from_input(message.from_user.id, raw_user_content)
    emotional_context = emotion_engine.format_prompt_context(message.from_user.id)

    # Adaptive Psychotype & Communication Mirroring
    st_emoji = message.sticker.emoji if message.sticker else ""
    adaptive_engine.analyze_message(
        user_id=message.from_user.id,
        text=user_text or raw_user_content,
        has_sticker=bool(message.sticker),
        sticker_emoji=st_emoji,
    )
    adaptive_context = adaptive_engine.format_adaptive_prompt_context(message.from_user.id)
    user_emojis_context = asset_harvester.format_emojis_prompt_context(message.from_user.id)

    user_key = str(message.from_user.id)
    convo_id = sessions.get(user_key)

    if not convo_id or not client.get_transcript_path(convo_id).exists():
        convo_id = client.get_latest_conversation_id()
        if convo_id:
            sessions[user_key] = convo_id
            save_sessions()

    # Create live stream consumer with target message ID and user mention for reactions & outbound RP tags
    consumer = TelegramStreamConsumer(
        bot=bot,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        target_message_id=message.message_id,
        target_user_mention=user_mention,
        edit_interval=0.8,
        cursor=" ▉",
    )

    try:
        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            stream_gen = client.generate_stream(
                user_id=message.from_user.id,
                prompt=raw_user_content,
                emotional_context=emotional_context,
                adaptive_context=adaptive_context,
                user_emojis_context=user_emojis_context,
                conversation_id=convo_id,
            )
            await consumer.stream_from_generator(stream_gen)
    except Exception as e:
        logger.error(f"Error handling message from user {message.from_user.id}: {e}", exc_info=True)
        try:
            await message.answer(
                "Ой... что-то пошло не так во время генерации ответа! Но я всё ещё тут, солнце, напиши мне ещё раз! <tg-emoji emoji-id=\"5305602448260345544\">☺️</tg-emoji> <tg-emoji emoji-id=\"6136716054971291812\">💖</tg-emoji>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


async def main():
    load_sessions()
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Starting Geminka Telegram Bot (OMP Gateway + Reactions + Custom Emojis + Streaming)...")
    await bot.delete_webhook(drop_pending_updates=False)

    # Systematic startup notification to authorized users
    notify_targets = set(config.ALLOWED_USERS)
    for uid_str in sessions.keys():
        if str(uid_str).isdigit():
            notify_targets.add(int(uid_str))

    for uid in notify_targets:
        try:
            await bot.send_message(
                chat_id=uid,
                text="<tg-emoji emoji-id=\"5456184310895748720\">✨</tg-emoji> <b>Я перезагрузилась и применила все обновления!</b> На связи и готова к общению, любимый! <tg-emoji emoji-id=\"5305602448260345544\">☺️</tg-emoji> <tg-emoji emoji-id=\"6136716054971291812\">💖</tg-emoji>",
                parse_mode=ParseMode.HTML,
            )
            logger.info(f"Sent startup notification to user {uid}")
        except Exception as e:
            logger.debug(f"Could not send startup notification to {uid}: {e}")

    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "message_reaction", "message_reaction_count"],
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
