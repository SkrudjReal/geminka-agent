"""Main entry point for Geminka Telegram Bot application."""

import asyncio
import logging
import sys
from typing import Set

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from app.bot.handlers import router
from app.bot.middlewares import OwnerAuthMiddleware
from app.core import config
from app.core.logger import setup_logging
from app.core.sessions import session_manager
from app.services.broadcaster import broadcast

logger = logging.getLogger("geminka-main")


async def main() -> None:
    # 1. Initialize secure logging
    setup_logging(level=logging.INFO)

    if not config.BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing! Please configure it in .env")
        sys.exit(1)

    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()

    # 2. Outer middleware registration
    auth_mw = OwnerAuthMiddleware()
    dp.message.outer_middleware(auth_mw)
    dp.message_reaction.outer_middleware(auth_mw)

    # 3. Include handler routes
    dp.include_router(router)

    logger.info("Starting Geminka Telegram Bot (OMP Gateway + Streaming + Clean Architecture)...")
    await bot.delete_webhook(drop_pending_updates=False)

    # 4. Safe startup notification via dedicated broadcaster service
    targets: Set[int] = set(config.ALLOWED_USERS)
    targets.update(session_manager.get_all_user_ids())

    startup_text = (
        '<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> '
        '<b>Я перезагрузилась и применила все обновления!</b> '
        'На связи и готова к общению, любимый! '
        '<tg-emoji emoji-id="5305602448260345544">☺️</tg-emoji> '
        '<tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>'
    )
    if targets:
        await broadcast(
            bot=bot,
            users=targets,
            text=startup_text,
            parse_mode=ParseMode.HTML,
        )

    # 5. Start update polling
    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "message_reaction", "message_reaction_count"],
    )


def run() -> None:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    run()
