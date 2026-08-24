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
        if not config.ALLOWED_USERS:
            return await handler(event, data)

        user = data.get("event_from_user")
        if user and user.id not in config.ALLOWED_USERS:
            if isinstance(event, types.Message):
                try:
                    await event.answer(
                        "⛔ <b>Доступ ограничен.</b> Этот бот работает в приватном режиме только для своего владельца.",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
            logger.warning(f"Blocked unauthorized access attempt from user {user.id} ({user.full_name})")
            return

        return await handler(event, data)


def check_auth(user_id: int) -> bool:
    return not config.ALLOWED_USERS or user_id in config.ALLOWED_USERS
