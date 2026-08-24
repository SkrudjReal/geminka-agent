"""Main entry point for Geminka Telegram Bot application."""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from app.bot.handlers import router
from app.bot.helpers import load_sessions, sessions
from app.bot.middlewares import OwnerAuthMiddleware
from app.core import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("geminka-main")


async def main() -> None:
    if not config.BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing! Please configure it in .env")
        sys.exit(1)

    load_sessions()
    bot = Bot(token=config.BOT_TOKEN)
    dp = Dispatcher()

    # Register outer middleware to filter unauthorized users globally before any handler
    auth_mw = OwnerAuthMiddleware()
    dp.message.outer_middleware(auth_mw)
    dp.message_reaction.outer_middleware(auth_mw)

    dp.include_router(router)

    logger.info("Starting Geminka Telegram Bot (OMP Gateway + Streaming + Clean Architecture)...")
    await bot.delete_webhook(drop_pending_updates=False)

    # Startup notification to authorized users
    notify_targets = set(config.ALLOWED_USERS)
    for uid_str in sessions.keys():
        if str(uid_str).isdigit():
            notify_targets.add(int(uid_str))

    for uid in notify_targets:
        try:
            await bot.send_message(
                chat_id=uid,
                text='<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> <b>Я перезагрузилась и применила все обновления!</b> На связи и готова к общению, любимый! <tg-emoji emoji-id="5305602448260345544">☺️</tg-emoji> <tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>',
                parse_mode=ParseMode.HTML,
            )
            logger.info(f"Sent startup notification to user {uid}")
        except Exception as e:
            logger.debug(f"Could not send startup notification to {uid}: {e}")

    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "message_reaction", "message_reaction_count"],
    )


def run() -> None:
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")


if __name__ == "__main__":
    run()
