"""Main entry point for Geminka Telegram Bot application using direct agy CLI."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats, BotCommandScopeDefault

from app.bot.handlers import router
from app.bot.middlewares import OwnerAuthMiddleware
from app.core import config
from app.core.instance import acquire_bot_lock
from app.core.logger import setup_logging
from app.services.antigravity import AntigravityClient
from app.services.palace_memory import palace_memory

logger = logging.getLogger("geminka-main")


async def setup_bot_commands(bot: Bot) -> None:
    """Configures visible Telegram bot commands menu for private chats and default scope."""
    commands = [
        BotCommand(command="start", description="👋 Главное меню и знакомство с Geminka"),
        BotCommand(command="model", description="⚡ Выбор AI модели (Gemini 3.8 / Claude 4.6)"),
        BotCommand(command="reasoning", description="🎯 Настройка мышления (Reasoning Effort)"),
        BotCommand(command="mood", description="💖 Настроение, шкала чувств и сброс эмоций"),
        BotCommand(command="memory", description="📖 Долговременная память и сохранённые факты"),
        BotCommand(command="remember", description="💡 Запомнить новый факт о тебе"),
        BotCommand(command="portrait", description="🧠 Мой текущий портрет тебя"),
        BotCommand(command="recall", description="🔎 Поиск по долговременной памяти"),
        BotCommand(command="forget", description="🗑 Удалить мою личную память"),
        BotCommand(command="debug", description="🧪 Временно отключить запись памяти"),
        BotCommand(command="sandbox", description="🛡️ Файловая песочница (только владелец)"),
        BotCommand(command="rp", description="🌸 Справочник интерактивных RP-действий"),
        BotCommand(command="topic", description="⚙️ Настройка чатов топиков (Forum Threads)"),
        BotCommand(command="chats", description="💬 Разрешённые группы (только владелец)"),
        BotCommand(command="prefix", description="🌸 Префиксы групп (только владелец)"),
        BotCommand(command="conv", description="💬 Переключить диалог/сессию по ID"),
        BotCommand(command="new", description="🔄 Начать новый диалог (сбросить контекст)"),
        BotCommand(command="status", description="🌟 Статус шлюза OMP, движка и метрики"),
        BotCommand(command="help", description="❓ Полное руководство и помощь"),
    ]
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        logger.info("Bot commands successfully registered for all private chats.")
    except Exception as e:
        logger.warning("Failed to register bot commands: %s", e)


async def main() -> None:
    # 1. Initialize secure logging
    setup_logging(level=logging.INFO)

    config.settings.validate_startup()
    config.ensure_runtime_dirs()

    # 2. The default transport is the authenticated agy CLI. Legacy OMP is opt-in.
    antigravity_client = AntigravityClient()
    shared_chunks = await asyncio.to_thread(palace_memory.sync_shared_source)
    imported = await asyncio.to_thread(palace_memory.migrate_legacy, config.STATE_DB_FILE, config.settings.owner_user_id)
    logger.info("MemPalace initialized; shared context chunks=%d, migration counts: %s", shared_chunks, imported)

    if not await antigravity_client.check_omp_health():
        logger.warning("agy CLI health check failed; generation will report the direct CLI error.")
    else:
        logger.info("Direct agy CLI transport is authenticated and ready.")

    bot = Bot(
        token=config.settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(antigravity_client=antigravity_client)

    # 3. Outer middleware registration
    auth_mw = OwnerAuthMiddleware()
    dp.message.outer_middleware(auth_mw)
    dp.callback_query.outer_middleware(auth_mw)
    dp.message_reaction.outer_middleware(auth_mw)

    # 4. Include handler routes
    dp.include_router(router)

    logger.info("Starting Geminka Telegram Bot (direct agy CLI + Clean Architecture)...")
    await bot.delete_webhook(drop_pending_updates=True)

    # 5. Register command menu for all private chats
    await setup_bot_commands(bot)

    # 6. Optional startup notification via direct Telegram API call
    targets = set(config.settings.allowed_users)

    startup_text = (
        '<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> '
        '<b>Я перезагрузилась и применила все обновления!</b> '
        'На связи и готова к общению, любимый! '
        '<tg-emoji emoji-id="5305602448260345544">☺️</tg-emoji> '
        '<tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>'
    )
    if config.settings.startup_notification and targets:
        for user_id in targets:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=startup_text,
                    parse_mode=ParseMode.HTML,
                )
            except Exception as exc:
                logger.warning("Failed to send startup notification to %s: %s", user_id, exc)

    # 6. Start update polling
    for user_id in palace_memory.known_users():
        palace_memory.schedule_analysis(
            user_id, lambda system, text, uid=user_id: antigravity_client._memory_completion(uid, system, text),
        )
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await antigravity_client.aclose()
        await bot.session.close()


def run() -> None:
    try:
        config.settings.validate_startup()
        with acquire_bot_lock(config.settings.bot_token):
            asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped cleanly.")
    except config.ConfigurationError as exc:
        logger.critical("Configuration error: %s", exc)
        raise SystemExit(2) from None


if __name__ == "__main__":
    run()
