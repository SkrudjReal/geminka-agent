"""Security & Authorization Middlewares for Geminka."""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, types
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject

from app.core import config

logger = logging.getLogger("geminka-auth")


class OwnerAuthMiddleware(BaseMiddleware):
    """Outer middleware to filter all incoming updates strictly by ALLOWED_USERS."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user or not config.settings.is_user_allowed(user.id):
            if isinstance(event, types.Message):
                try:
                    await event.answer(
                        "⛔ <b>Доступ ограничен.</b> Этот бот работает в приватном режиме только для своего владельца.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
            elif isinstance(event, types.CallbackQuery):
                try:
                    await event.answer("Доступ ограничен.", show_alert=True)
                except Exception:
                    pass
            if user:
                logger.warning("Blocked unauthorized access attempt from user %s", user.id)
            return

        return await handler(event, data)


def check_auth(user_id: int) -> bool:
    """Compatibility helper for handlers; authorization remains fail-closed."""
    return config.settings.is_user_allowed(user_id)
