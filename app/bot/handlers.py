"""Telegram bot command and message handlers for Geminka."""

import html
import logging
import random

from aiogram import Bot, F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
)
from aiogram.utils.chat_action import ChatActionSender

from app.bot.helpers import extract_message_context, send_response
from app.bot.middlewares import check_auth
from app.core import config
from app.core.concurrency import user_locks
from app.engines.adaptive import adaptive_engine
from app.engines.emotional import MOOD_DEFINITIONS, emotion_engine
from app.engines.rp import detect_rp_command, get_random_rp_phrase
from app.services.antigravity import AVAILABLE_MODELS, AntigravityClient
from app.services.harvester import asset_harvester
from app.services.rag import MemoryRejected, rag_engine
from app.services.streamer import TelegramStreamConsumer, md_to_telegram_html

logger = logging.getLogger("geminka-handlers")

router = Router()
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
async def cmd_model(message: types.Message, antigravity_client: AntigravityClient):
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

        antigravity_client.set_user_model(message.from_user.id, new_model)
        msg_html = md_to_telegram_html(
            f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Модель успешно изменена на:**\n`{new_model}`'
        )
        await message.answer(msg_html, parse_mode=ParseMode.HTML)
        return

    current = antigravity_client.get_user_model(message.from_user.id)
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
        f"• **Reasoning Effort:** `{antigravity_client.get_user_reasoning(message.from_user.id)}`\n"
        f"• **Max Output Tokens:** `{config.MAX_OUTPUT_TOKENS}`\n\n"
        f"Выбери желаемую модель кнопкой ниже или напиши `/model sonnet` / `/model flash`:"
    )
    await message.answer(md_to_telegram_html(text), reply_markup=keyboard, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("set_model:"))
async def process_set_model(callback: types.CallbackQuery, antigravity_client: AntigravityClient):
    model_name = callback.data.split(":", 1)[1]
    if model_name not in AVAILABLE_MODELS:
        await callback.answer("Неизвестная модель", show_alert=True)
        return
    antigravity_client.set_user_model(callback.from_user.id, model_name)
    await callback.answer(f"Модель выбрана: {model_name}")
    try:
        msg_html = md_to_telegram_html(
            f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Активная модель обновлена:**\n`{model_name}`'
        )
        await callback.message.edit_text(msg_html, parse_mode=ParseMode.HTML)
    except Exception:
        pass


@router.message(Command("reasoning", "effort", "thinking"))
async def cmd_reasoning(message: types.Message, antigravity_client: AntigravityClient):
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

        antigravity_client.set_user_reasoning(message.from_user.id, new_effort)
        msg_html = md_to_telegram_html(
            f'<tg-emoji emoji-id="5359450562079242286">🌟</tg-emoji> **Уровень Reasoning Effort установлен на:** `{new_effort}`'
        )
        await message.answer(msg_html, parse_mode=ParseMode.HTML)
        return

    current = antigravity_client.get_user_reasoning(message.from_user.id)
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
async def process_set_reasoning(callback: types.CallbackQuery, antigravity_client: AntigravityClient):
    effort_val = callback.data.split(":", 1)[1]
    if effort_val not in {"low", "medium", "high"}:
        await callback.answer("Неизвестный уровень", show_alert=True)
        return
    antigravity_client.set_user_reasoning(callback.from_user.id, effort_val)
    await callback.answer(f"Reasoning установлен: {effort_val}")
    try:
        msg_html = md_to_telegram_html(
            f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Уровень Reasoning Effort обновлен:**\n`{effort_val}`'
        )
        await callback.message.edit_text(msg_html, parse_mode=ParseMode.HTML)
    except Exception:
        pass


@router.message(Command("mood", "emotions", "relationship", "reset_mood", "mood_reset"))
async def cmd_mood(message: types.Message, command: CommandObject | None = None):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    cmd_name = (command.command if command else "").lower()
    args = (command.args or "").strip().lower() if command else ""
    if cmd_name in ["reset_mood", "mood_reset"] or args in ["reset", "clear", "сброс", "дефолт", "default"]:
        emotion_engine.reset_state(message.from_user.id)
        await send_response(
            message,
            '<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Эмоциональное состояние и шкала отношений сброшены к начальным значениям!**'
        )
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
async def cmd_new(message: types.Message, antigravity_client: AntigravityClient):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    antigravity_client.clear_history(message.from_user.id)
    await message.answer("✨ Контекст и история сброшены! Начинаем диалог с чистого листа.")


@router.message(Command("memory", "memories"))
async def cmd_memory(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    all_mems = rag_engine.get_all_memories_list(message.from_user.id)
    total = len(all_mems)

    preview_items = []
    for mem in all_mems[:6]:
        clean_item = mem.strip().replace("\n", " ")
        if len(clean_item) > 120:
            clean_item = clean_item[:117] + "..."
        preview_items.append(f"• {clean_item}")

    preview_text = "\n".join(preview_items) or "Пока нет сохранённых записей."

    text = (
        f'<tg-emoji emoji-id="5363859217159582224">📖</tg-emoji> **Личная долговременная память:**\n\n'
        f"• **Всего фрагментов памяти:** `{total}`\n"
        f"• **Хранилище:** `SQLite, изоляция по Telegram user ID`\n"
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

    args = message.text.split(maxsplit=1)[1:] if message.text else []
    if not args or not args[0].strip():
        await message.answer(
            "💡 Напиши факт, который нужно запомнить:\n`/remember Я люблю чай с бергамотом`",
            parse_mode=ParseMode.HTML,
        )
        return

    fact_text = args[0].strip()
    try:
        added = rag_engine.add_memory(message.from_user.id, fact_text, category="user_custom")
    except MemoryRejected as exc:
        await message.answer(f"⚠️ {html.escape(str(exc))}", parse_mode=ParseMode.HTML)
        return
    if not added:
        await message.answer("Этот факт уже сохранён.")
        return

    await message.answer(
        f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> <b>Запомнила:</b>\n<blockquote>{html.escape(fact_text)}</blockquote>',
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("status"))
async def cmd_status(message: types.Message, antigravity_client: AntigravityClient):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    is_omp_alive = await antigravity_client.check_omp_health()
    current_model = antigravity_client.get_user_model(message.from_user.id)
    current_reasoning = antigravity_client.get_user_reasoning(message.from_user.id)
    state = emotion_engine.get_state(message.from_user.id)
    total_memories = rag_engine.count(message.from_user.id)

    engine_status = (
        f"🟢 OMP Gateway (`{config.OMP_BASE_URL}`) [Active]"
        if is_omp_alive
        else "🔴 OMP Gateway недоступен"
    )

    status_text = (
        f'<tg-emoji emoji-id="5359450562079242286">🌟</tg-emoji> **Статус подключения:**\n\n'
        f"• **Движок:** `{engine_status}`\n"
        f"• **Активная модель:** `{current_model}`\n"
        f"• **Reasoning Effort:** `{current_reasoning}`\n"
        f"• **Sliding Context Window:** `15 turns / 24k chars`\n"
        f"• **Личная память:** `{total_memories} записей в SQLite`\n"
        f"• **Теплота:** `{state.warmth}/100` | **Отношения:** `{state.get_relationship_stage()}`\n"
        f"• **Авторизация:** `Telegram allowlist + OMP API key`"
    )
    await send_response(message, status_text)


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    help_text = (
        '<tg-emoji emoji-id="5363859217159582224">📖</tg-emoji> **Доступные команды:**\n\n'
        "• `/model` — выбор модели (Gemini 3.7 / Claude Sonnet / Claude Opus)\n"
        "• `/reasoning` — настройка глубины размышлений (low, medium, high)\n"
        "• `/memory` — посмотреть долговременную память и факты\n"
        "• `/remember` — добавить новый факт в память\n"
        "• `/mood` — текущее настроение, теплота и статус отношений\n"
        "• `/new` — сбросить контекст диалога\n"
        "• `/status` — статус OMP Gateway, памяти и параметров\n\n"
        "Ставь реакции, отправляй стикеры, файлы или цитируй сообщения реплаем — всё учитывается!"
    )
    await send_response(message, help_text)


@router.message()
async def handle_any_message(
    message: types.Message,
    bot: Bot,
    antigravity_client: AntigravityClient,
):
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

        if not extra_text:
            raw_user_content = f"[{user_mention} выполнил(-а) RP-действие: «{action}» по отношению к Коломбине. Отреагируй на это взаимно, нежно, эмоционально и в характере!]"

    # Realistic sticker response logic (50% pure sticker/RP/reaction, 50% with text)
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

    user_id = message.from_user.id
    # Create live stream consumer
    consumer = TelegramStreamConsumer(
        bot=bot,
        chat_id=message.chat.id,
        user_id=message.from_user.id,
        target_message_id=message.message_id,
        target_user_mention=user_mention,
        edit_interval=0.8,
        cursor=' <tg-emoji emoji-id="5456184310895748720">✨</tg-emoji>',
    )

    try:
        async with user_locks.get(user_id), ChatActionSender.typing(
            bot=bot, chat_id=message.chat.id
        ):
            stream_gen = antigravity_client.generate_stream(
                user_id=message.from_user.id,
                prompt=raw_user_content,
                emotional_context=emotional_context,
                adaptive_context=adaptive_context,
                user_emojis_context=user_emojis_context,
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
