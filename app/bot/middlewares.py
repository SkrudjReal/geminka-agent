"""Security & Authorization Middlewares for Geminka."""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, types
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject

from app.core import config
from app.services.topics import topic_manager

logger = logging.getLogger("geminka-auth")


class OwnerAuthMiddleware(BaseMiddleware):
    """Outer middleware to strictly filter all updates: only allowed users can interact."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return

        # 1. Group / Supergroup handling: must be in active topic AND user must be allowed
        if isinstance(event, types.Message) and event.chat.type in ["group", "supergroup"]:
            if not topic_manager.is_topic_active(event.chat.id, event.message_thread_id):
                return  # Silently ignore messages outside registered topics
            if not config.settings.is_user_allowed(user.id):
                logger.warning(
                    "Blocked unauthorized user %s in active topic %s:%s",
                    user.id,
                    event.chat.id,
                    event.message_thread_id,
                )
                return  # Silently ignore unauthorized group members
            return await handler(event, data)

        if isinstance(event, types.CallbackQuery) and event.message and event.message.chat.type in ["group", "supergroup"]:
            thread_id = getattr(event.message, "message_thread_id", None)
            if not topic_manager.is_topic_active(event.message.chat.id, thread_id):
                return
            if not config.settings.is_user_allowed(user.id):
                try:
                    await event.answer("⛔ Доступ ограничен.", show_alert=True)
                except Exception:
                    pass
                return
            return await handler(event, data)

        # 2. Private Chat security check: fail-closed
        if not config.settings.is_user_allowed(user.id):
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
                    await event.answer("⛔ Доступ ограничен.", show_alert=True)
                except Exception:
                    pass
            logger.warning("Blocked unauthorized private access attempt from user %s", user.id)
            return

        return await handler(event, data)


def check_auth(user_id: int) -> bool:
    """Compatibility helper for handlers; authorization remains fail-closed."""
    return config.settings.is_user_allowed(user_id)
