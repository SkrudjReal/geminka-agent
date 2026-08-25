"""Main entry point for Geminka Telegram Bot application (Antigravity Connect/SSE)."""

import asyncio
import logging
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from app.bot.handlers import router
from app.bot.middlewares import OwnerAuthMiddleware
from app.core import config
from app.core.logger import setup_logging
from app.services.antigravity import AntigravityClient
from app.services.broadcaster import broadcast
from app.services.omp_gateway import start_omp_gateway_task

logger = logging.getLogger("geminka-main")


async def main() -> None:
    # 1. Initialize secure logging
    setup_logging(level=logging.INFO)

    config.settings.validate_startup()
    config.ensure_runtime_dirs()

    # 2. Auto-start built-in Antigravity Connect/SSE Gateway if not running
    omp_manager = None
    omp_task = None
    antigravity_client = AntigravityClient()

    if not await antigravity_client.check_omp_health():
        parsed = urlparse(config.settings.omp_base_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 4000
        logger.info("Antigravity Connect/SSE Gateway is offline. Auto-launching on %s:%d...", host, port)
        try:
            omp_manager, omp_task = await start_omp_gateway_task(host=host, port=port)
            logger.info("Antigravity Connect/SSE Gateway is ONLINE on %s", config.settings.omp_base_url)
        except Exception as e:
            logger.warning("Could not auto-start Antigravity Gateway: %s", e)
    else:
        logger.info("Antigravity Connect/SSE Gateway is ONLINE on %s", config.settings.omp_base_url)

    bot = Bot(token=config.settings.bot_token)
    dp = Dispatcher(antigravity_client=antigravity_client)

    # 3. Outer middleware registration
    auth_mw = OwnerAuthMiddleware()
    dp.message.outer_middleware(auth_mw)
    dp.callback_query.outer_middleware(auth_mw)
    dp.message_reaction.outer_middleware(auth_mw)

    # 4. Include handler routes
    dp.include_router(router)

    logger.info("Starting Geminka Telegram Bot (Antigravity Connect/SSE + Clean Architecture)...")
    await bot.delete_webhook(drop_pending_updates=False)

    # 5. Safe startup notification via dedicated broadcaster service
    targets = set(config.settings.allowed_users)

    startup_text = (
        '<tg-emoji emoji-id="5456184310895748720">✨</tg-emoji> '
        '<b>Я перезагрузилась и применила все обновления!</b> '
        'На связи и готова к общению, любимый! '
        '<tg-emoji emoji-id="5305602448260345544">☺️</tg-emoji> '
        '<tg-emoji emoji-id="6136716054971291812">💖</tg-emoji>'
    )
    if config.settings.startup_notification and targets:
        await broadcast(
            bot=bot,
            users=targets,
            text=startup_text,
            parse_mode=ParseMode.HTML,
        )

    # 6. Start update polling
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if omp_manager:
            await omp_manager.aclose()
        if omp_task:
            omp_task.cancel()
        await antigravity_client.aclose()
        await bot.session.close()


def run() -> None:
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped cleanly.")
    except config.ConfigurationError as exc:
        logger.critical("Configuration error: %s", exc)
        raise SystemExit(2) from None


if __name__ == "__main__":
    run()
