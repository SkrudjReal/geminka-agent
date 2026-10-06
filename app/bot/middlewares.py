"""Security & Authorization Middlewares for Geminka."""

import logging
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware, types
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject

from app.core import config
from app.services.topics import topic_manager

logger = logging.getLogger("geminka-auth")

OWNER_COMMANDS = {
    "sandbox", "chats", "prefix", "topic", "topics", "model", "models",
    "reasoning", "effort", "thinking", "debug", "conv", "load", "session", "conversation",
    "new", "reset", "reset_mood", "mood_reset", "mood", "emotions", "relationship",
}


def is_owner(user_id: int) -> bool:
    return config.settings.owner_user_id is not None and user_id == config.settings.owner_user_id


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

        owner = is_owner(user.id) and config.settings.is_user_allowed(user.id)
        if isinstance(event, types.CallbackQuery) and not owner:
            await event.answer("⛔ Настройки может менять только владелец.", show_alert=True)
            return
        if isinstance(event, types.CallbackQuery) and owner:
            return await handler(event, data)
        command = ""
        if isinstance(event, types.Message):
            text = event.text or event.caption or ""
            if text.startswith("/"):
                command = text.split()[0][1:].split("@", 1)[0].lower()
            if command in OWNER_COMMANDS:
                if not owner:
                    await event.answer("⛔ Настройки может менять только владелец.")
                    return
                return await handler(event, data)

        # Registered topics need no prefix; ordinary groups always need a trigger.
        if isinstance(event, types.Message) and event.chat.type in ["group", "supergroup"]:
            active_topic = topic_manager.is_topic_active(event.chat.id, event.message_thread_id)
            if not active_topic and not (
                topic_manager.is_chat_active(event.chat.id)
                and topic_manager.strip_prefix(event.text or event.caption or "") is not None
            ):
                return
            if user.is_bot or event.sender_chat:
                return
            if not config.settings.is_user_allowed(user.id):
                logger.warning(
                    "Blocked unauthorized user %s in active topic %s:%s",
                    user.id,
                    event.chat.id,
                    event.message_thread_id,
                )
                return  # Silently ignore unauthorized group members
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
            logger.warning("Blocked unauthorized private access attempt from user %s", user.id)
            return

        return await handler(event, data)


def check_auth(user_id: int) -> bool:
    """Compatibility helper for handlers; authorization remains fail-closed."""
    return config.settings.is_user_allowed(user_id)
