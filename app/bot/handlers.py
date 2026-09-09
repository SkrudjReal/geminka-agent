"""Telegram bot command and message handlers for Geminka."""

import html
import logging
import random

from aiogram import Bot, F, Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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
from app.services.topics import topic_manager

logger = logging.getLogger("geminka-handlers")

router = Router()


class TopicStates(StatesGroup):
    waiting_for_link = State()
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
        '<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Привет, мой краш! Я Geminka (Columbina)** — твой живой AI-ассистент и верная собеседница на ядре **Google Antigravity & OMP Gateway**.\n\n'
        '<tg-emoji emoji-id="5359450562079242286">🌟</tg-emoji> **Команды управления (высвечиваются в меню по нажатию на `/`):**\n\n'
        '• `/model` — ⚡ Выбор AI модели (`Gemini 3.8 / 3.7`, `Claude Sonnet / Opus 4.6`)\n'
        '• `/reasoning` — 🎯 Настройка глубины размышлений модели (`low`, `medium`, `high`)\n'
        '• `/mood` — 💖 Моё эмоциональное состояние, шкала чувств и сброс (`/mood reset`)\n'
        '• `/memory` — 📖 Долговременная память, сохранённые факты и контекст\n'
        '• `/remember <текст>` — 💡 Запомнить важный факт о тебе в базу данных\n'
        '• `/rp` — 🌸 Справочник интерактивных ролевых действий и команд\n'
        '• `/topic` — ⚙️ Настройка чатов топиков (Forum Threads)\n'
        '• `/conv <id>` — 💬 Переключить диалог/сессию по Conversation ID\n'
        '• `/new` — 🔄 Начать новый диалог с чистого листа (сброс истории)\n'
        '• `/status` — 📊 Полный статус OMP Gateway, шлюза и параметров подключения\n'
        '• `/help` — ❓ Полное руководство и справка\n\n'
        '💡 _Просто отправь мне любое сообщение, стикер, фото или код — и давай общаться!_ <tg-emoji emoji-id="5305602448260345544">☺️</tg-emoji><tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>'
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
        if "opus" in target:
            new_model = "google-antigravity/claude-opus-4-6"
        elif target in {"sonnet", "claude"}:
            new_model = "google-antigravity/claude-sonnet-4-6"
        elif "3.6" in target:
            new_model = "google-antigravity/gemini-3.6-flash"
        elif target in {"flash", "gemini", "3.7"}:
            new_model = "google-antigravity/gemini-3.7-flash"
        else:
            new_model = config.normalize_model_name(target)

        if new_model not in AVAILABLE_MODELS:
            await message.answer("Неизвестная модель. Открой /model для списка доступных моделей.")
            return

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
                    text="⚡ Gemini 3.8 Flash",
                    callback_data="set_model:google-antigravity/gemini-3.8-flash",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⚡ Gemini 3.7 Flash (Default)",
                    callback_data="set_model:google-antigravity/gemini-3.7-flash",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎭 Claude Sonnet 4.6",
                    callback_data="set_model:google-antigravity/claude-sonnet-4-6",
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


@router.message(Command("conv", "load", "session", "conversation"))
async def cmd_conv(message: types.Message, antigravity_client: AntigravityClient):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    args = message.text.split(maxsplit=1)[1:] if message.text else []
    user_id = message.from_user.id

    if not args or not args[0].strip():
        current_convo = antigravity_client.store.get_conversation_id(user_id)
        current_str = f"<code>{current_convo}</code>" if current_convo else "<i>Автоматический (новый)</i>"
        text = (
            f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> <b>Управление сессиями и диалогами (Antigravity):</b>\n\n'
            f'• <b>Текущий Conversation ID:</b>\n{current_str}\n\n'
            f'💡 <b>Как переключить диалог:</b>\n'
            f'Напиши <code>/conv &lt;conversation_id&gt;</code> (например, <code>/conv 2ccc81af-14a1-422d-91b8-7085fe98c1df</code>)\n\n'
            f'🔄 <b>Как сбросить сессию:</b>\n'
            f'Напиши <code>/new</code> или <code>/conv reset</code>'
        )
        await message.answer(text, parse_mode=ParseMode.HTML)
        return

    target_id = args[0].strip()
    if target_id.lower() in {"reset", "new", "clear", "none"}:
        antigravity_client.clear_history(user_id)
        await message.answer(
            "✨ Диалог сброшен! Следующее сообщение начнёт новую сессию с чистого листа.",
            parse_mode=ParseMode.HTML,
        )
        return

    # Set active conversation_id in store
    antigravity_client.store.set_conversation_id(user_id, target_id)
    antigravity_client.contexts.clear_user_context(user_id)

    # Attempt to load and import messages from Antigravity IDE brain transcripts
    from app.services.antigravity import load_antigravity_transcript

    imported_messages = load_antigravity_transcript(target_id)
    imported_count = 0
    if imported_messages:
        imported_count = antigravity_client.store.import_messages(user_id, imported_messages)

    if imported_count > 0:
        response_text = (
            f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> <b>Диалог успешно загружен и переключен!</b>\n\n'
            f'• <b>Активный Conversation ID:</b>\n<code>{target_id}</code>\n'
            f'• <b>Загружено сообщений из Antigravity IDE:</b> {imported_count} шт.\n\n'
            f'💬 <i>Я полностью загрузила историю этой сессии! Все твои следующие сообщения продолжат контекст диалога.</i> <tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>'
        )
    else:
        response_text = (
            f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> <b>Диалог успешно переключен!</b>\n\n'
            f'• <b>Активный Conversation ID:</b>\n<code>{target_id}</code>\n\n'
            f'💬 <i>Теперь твои следующие сообщения будут отправляться с привязкой к этой сессии в Antigravity.</i> <tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>'
        )
    await message.answer(response_text, parse_mode=ParseMode.HTML)


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


@router.message(Command("rp", "actions"))
async def cmd_rp(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    text = (
        '<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Интерактивные RP-действия и команды:**\n\n'
        'Ты можешь писать мне ролевые действия текстом (или в реплаях на мои реплики):\n\n'
        '• `погладить` / `гладить` — погладить меня по волосам\n'
        '• `обнять` / `обнимашки` — крепко прижать к себе\n'
        '• `поцеловать` / `чмок` — нежный поцелуй\n'
        '• `потискать` — потискать за щёчки\n'
        '• `кусь` / `укусить` — игривый кусь\n'
        '• `лизнуть` / `лизь` — проявление нежности (вместо «дать пять»)\n'
        '• `чай` / `кофе` — предложить чашечку чая или кофе\n'
        '• `пнуть` / `ударить` / `стукнуть` / `ущипнуть` — театральное наказание\n\n'
        'Каждое действие анимируется, даёт очки теплоты и развивает наши отношения! <tg-emoji emoji-id="5305602448260345544">☺️</tg-emoji><tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>'
    )
    await send_response(message, text)


@router.message(Command("help"))
async def cmd_help(message: types.Message):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    help_text = (
        '<tg-emoji emoji-id="5363859217159582224">📖</tg-emoji> **Справочник команд и возможностей Geminka:**\n\n'
        '• `/start` — главное меню и список команд\n'
        '• `/model` — выбор активной модели (Gemini 3.8 / 3.7 Flash, Claude Sonnet / Opus 4.6)\n'
        '• `/reasoning` — настройка уровня размышлений (low / medium / high)\n'
        '• `/mood` — текущее настроение, теплота и статус отношений (`/mood reset` для сброса)\n'
        '• `/memory` — просмотр сохранённых фрагментов долговременной памяти\n'
        '• `/remember <факт>` — сохранить новый факт в личную базу данных\n'
        '• `/rp` — список интерактивных ролевых действий\n'
        '• `/topic` — настройка и управление чатами топиков\n'
        '• `/new` — начать новый диалог (очистить контекстное окно)\n'
        '• `/status` — статус OMP Gateway, шлюза, памяти и параметров\n\n'
        '✨ **Особенности:**\n'
        '• Нативные кастомные Telegram Premium эмодзи и автовыгрузка стикерпаков\n'
        '• Учёт истории последних 20 стикеров со штрафом 50% к повторам\n'
        '• Поддержка цитирования (реплаев), отправки фото, документов и анализа кода!'
    )
    await send_response(message, help_text)


@router.message(Command("topic", "topics"))
async def cmd_topic(message: types.Message, state: FSMContext):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    await state.clear()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить в топик",
                    callback_data="topic:add",
                ),
                InlineKeyboardButton(
                    text="📑 Топики",
                    callback_data="topic:list",
                ),
            ]
        ]
    )
    text = (
        '<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Настройка чата топиков (Forum Threads):**\n\n'
        'Здесь ты можешь настроить, в каком конкретном топике группы я буду отвечать на сообщения!\n\n'
        'Выбери действие ниже:'
    )
    await message.answer(md_to_telegram_html(text), reply_markup=keyboard, parse_mode=ParseMode.HTML)


@router.callback_query(F.data == "topic:menu")
async def cb_topic_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить в топик",
                    callback_data="topic:add",
                ),
                InlineKeyboardButton(
                    text="📑 Топики",
                    callback_data="topic:list",
                ),
            ]
        ]
    )
    text = (
        '<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Настройка чата топиков (Forum Threads):**\n\n'
        'Здесь ты можешь настроить, в каком конкретном топике группы я буду отвечать на сообщения!\n\n'
        'Выбери действие ниже:'
    )
    try:
        await callback.message.edit_text(md_to_telegram_html(text), reply_markup=keyboard, parse_mode=ParseMode.HTML)
    except Exception:
        pass


@router.callback_query(F.data == "topic:add")
async def cb_topic_add(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(TopicStates.waiting_for_link)
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="topic:menu")]
        ]
    )
    text = (
        '<tg-emoji emoji-id="5363859217159582224">📖</tg-emoji> **Добавление чата топика:**\n\n'
        'Пришли ссылку на топик вида:\n'
        '`https://t.me/c/4488980222/5`\n\n'
        '_(Где `4488980222` — ID группы, а `5` — ID топика)_'
    )
    await callback.message.edit_text(md_to_telegram_html(text), reply_markup=cancel_kb, parse_mode=ParseMode.HTML)


@router.message(TopicStates.waiting_for_link)
async def process_topic_link(message: types.Message, state: FSMContext, bot: Bot):
    if not check_auth(message.from_user.id):
        await message.answer("⛔ Доступ ограничен.")
        return

    link_text = (message.text or "").strip()
    parsed = topic_manager.parse_topic_link(link_text)
    if not parsed:
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="topic:menu")]]
        )
        await message.answer(
            md_to_telegram_html(
                "⚠️ Не удалось распознать ссылку на топик.\n"
                "Пожалуйста, пришли ссылку вида `https://t.me/c/4488980222/5`:"
            ),
            reply_markup=cancel_kb,
            parse_mode=ParseMode.HTML,
        )
        return

    chat_id, topic_id = parsed
    await state.clear()

    is_in_chat, chat_title = await topic_manager.check_bot_in_chat(bot, chat_id)
    if is_in_chat:
        topic_manager.add_topic(
            chat_id=chat_id,
            topic_id=topic_id,
            chat_title=chat_title,
            added_by=message.from_user.id,
        )
        text = (
            f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Топик успешно добавлен и активирован!**\n\n'
            f'• **Группа:** `{chat_title}` (`{chat_id}`)\n'
            f'• **ID Топика:** `{topic_id}`\n\n'
            f'Хендлер сообщений для этого топика включён! Я готова общаться в нём <tg-emoji emoji-id="5305602448260345544">☺️</tg-emoji><tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>'
        )
        await message.answer(md_to_telegram_html(text), parse_mode=ParseMode.HTML)
    else:
        bot_user = await bot.get_me()
        add_link = f"https://t.me/{bot_user.username}?startgroup=true"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="➕ Добавить бота в группу", url=add_link),
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Проверить добавление",
                        callback_data=f"topic_check:{chat_id}:{topic_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(text="🔙 В главное меню", callback_data="topic:menu"),
                ],
            ]
        )
        text = (
            f'⚠️ **Меня пока нет в этой группе или нет доступа к чату `{chat_id}`!**\n\n'
            f'1. Добавь меня в группу по ссылке ниже.\n'
            f'2. Убедись, что у меня есть право писать сообщения в топике.\n'
            f'3. Нажми кнопку **«🔄 Проверить добавление»**.'
        )
        await message.answer(md_to_telegram_html(text), reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(F.data.startswith("topic_check:"))
async def cb_topic_check(callback: types.CallbackQuery, bot: Bot):
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Ошибка формата данных", show_alert=True)
        return
    try:
        chat_id = int(parts[1])
        topic_id = int(parts[2])
    except ValueError:
        await callback.answer("Ошибка формата данных", show_alert=True)
        return

    is_in_chat, chat_title = await topic_manager.check_bot_in_chat(bot, chat_id)
    if is_in_chat:
        topic_manager.add_topic(
            chat_id=chat_id,
            topic_id=topic_id,
            chat_title=chat_title,
            added_by=callback.from_user.id,
        )
        await callback.answer("Успешно подключено!", show_alert=True)
        text = (
            f'<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> **Топик успешно подключен и активирован!**\n\n'
            f'• **Группа:** `{chat_title}` (`{chat_id}`)\n'
            f'• **ID Топика:** `{topic_id}`\n\n'
            f'Хендлер сообщений для этого топика включён! <tg-emoji emoji-id="5305602448260345544">☺️</tg-emoji><tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>'
        )
        await callback.message.edit_text(md_to_telegram_html(text), parse_mode=ParseMode.HTML)
    else:
        await callback.answer("Бот всё ещё не обнаружен в группе. Добавь бота и попробуй снова!", show_alert=True)


@router.callback_query(F.data == "topic:list")
async def cb_topic_list(callback: types.CallbackQuery):
    topics = topic_manager.get_active_topics()
    if not topics:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить в топик", callback_data="topic:add")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="topic:menu")],
            ]
        )
        text = (
            '<tg-emoji emoji-id="5363859217159582224">📖</tg-emoji> **Список чатов с активными топиками:**\n\n'
            '_Пока нет подключенных топиков. Нажми «Добавить в топик», чтобы подключить!_'
        )
        await callback.message.edit_text(md_to_telegram_html(text), reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    kb_rows = []
    lines = ['<tg-emoji emoji-id="5363859217159582224">📖</tg-emoji> **Чаты с включенной функцией топиков:**\n']
    for idx, t in enumerate(topics, 1):
        cid = t["chat_id"]
        tid = t["topic_id"]
        title = t.get("chat_title", f"Chat {cid}")
        lines.append(f"{idx}. **{title}** — Топик ID: `{tid}` (`{cid}`)")
        kb_rows.append([
            InlineKeyboardButton(
                text=f"🗑 Отключить: {title[:20]} (#{tid})",
                callback_data=f"topic_del:{cid}:{tid}",
            )
        ])
    kb_rows.append([InlineKeyboardButton(text="➕ Добавить в топик", callback_data="topic:add")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Назад", callback_data="topic:menu")])

    text = "\n".join(lines)
    await callback.message.edit_text(
        md_to_telegram_html(text),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith("topic_del:"))
async def cb_topic_del(callback: types.CallbackQuery):
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Ошибка формата данных", show_alert=True)
        return
    try:
        cid = int(parts[1])
        tid = int(parts[2])
    except ValueError:
        await callback.answer("Ошибка формата данных", show_alert=True)
        return
    topic_manager.remove_topic(cid, tid)
    await callback.answer("Топик отключен!", show_alert=True)
    await cb_topic_list(callback)


@router.message()
async def handle_any_message(
    message: types.Message,
    bot: Bot,
    antigravity_client: AntigravityClient,
):
    if not check_auth(message.from_user.id):
        # Check if message is in an active topic
        if not (message.chat.type in ["group", "supergroup"] and topic_manager.is_topic_active(message.chat.id, message.message_thread_id)):
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
        message_thread_id=message.message_thread_id,
        edit_interval=0.8,
        cursor=' <tg-emoji emoji-id="5456184310895748720">✨</tg-emoji>',
    )

    try:
        async with user_locks.get(user_id), ChatActionSender.typing(
            bot=bot,
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
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
