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
    "new", "reset", "reset_mood", "mood_reset",
}
PRIVATE_COMMANDS = {"memory", "remember", "recall", "forget", "portrait", "status",
                    "conv", "load", "session", "conversation", "new", "reset", "sandbox", "debug"}


def is_owner(user_id: int) -> bool:
    return config.settings.owner_user_id is not None and user_id == config.settings.owner_user_id


def group_message_allowed(message: types.Message, bot_id: int | None = None) -> bool:
    user = message.from_user
    if not user or (bot_id is not None and user.id == bot_id) or message.sender_chat:
        return False
    original = message.reply_to_message
    reply_to_bot = bool(bot_id and original and original.from_user and original.from_user.id == bot_id)
    return topic_manager.should_answer(
        message.chat.id, message.message_thread_id, message.text or message.caption or "",
        reply_to_bot=reply_to_bot,
    )


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
            if event.chat.type in {"group", "supergroup"}:
                from app.services.group_context import observe

                observe(event)
                observer = data.get("chat_observer")
                if observer:
                    observer.observe(event, addressed=group_message_allowed(event, getattr(data.get("bot"), "id", None)))
                if (event.sticker and event.from_user and not event.sender_chat
                        and topic_manager.is_group_configured(event.chat.id)
                        and not group_message_allowed(event, getattr(data.get("bot"), "id", None))):
                    from app.services.harvester import asset_harvester
                    from app.services.sticker_scanner import sticker_scanner

                    sticker = event.sticker
                    asset_harvester.register_sticker(
                        event.from_user.id, sticker.file_id, sticker.file_unique_id,
                        emoji_char=sticker.emoji, set_name=sticker.set_name,
                        is_animated=sticker.is_animated, is_video=sticker.is_video,
                    )
                    if sticker.set_name and data.get("bot"):
                        sticker_scanner.schedule_scan(data["bot"], sticker.set_name, event.from_user.id)
                client = data.get("antigravity_client")
                if (client and topic_manager.is_group_configured(event.chat.id)
                        and event.from_user and not event.from_user.is_bot and not event.sender_chat
                        and not (event.text or event.caption or "").startswith("/")):
                    import asyncio

                    await asyncio.to_thread(
                        client.group_memories.archive, event.chat.id, event.from_user.id,
                        event.text or event.caption or "[media]", key=f"tg:{event.chat.id}:{event.message_id}",
                        telegram_date=event.date.isoformat(), thread_id=event.message_thread_id,
                    )
                    if not group_message_allowed(event, getattr(data.get("bot"), "id", None)):
                        client.group_memories.cancel(event.chat.id, event.from_user.id)
                        client.group_memories.schedule(event.chat.id, event.from_user.id)
            text = event.text or event.caption or ""
            if text.startswith("/"):
                command = text.split()[0][1:].split("@", 1)[0].lower()
            if event.chat.type in {"group", "supergroup"} and command in PRIVATE_COMMANDS:
                if owner:
                    await event.answer("Эта команда доступна только в личном чате с владельцем.")
                return
            if command in OWNER_COMMANDS:
                if not owner:
                    await event.answer("⛔ Настройки может менять только владелец.")
                    return
                return await handler(event, data)
            if command in {"mood", "emotions", "relationship"}:
                if owner or (event.chat.type in {"group", "supergroup"} and (
                    topic_manager.is_chat_active(event.chat.id)
                    or topic_manager.is_topic_active(event.chat.id, event.message_thread_id)
                )):
                    return await handler(event, data)

        # Registered groups are open to humans; configuration remains owner-only.
        if isinstance(event, types.Message) and event.chat.type in ["group", "supergroup"]:
            bot = data.get("bot")
            if not group_message_allowed(event, getattr(bot, "id", None)):
                return
            return await handler(event, data)

        if isinstance(event, types.MessageReactionUpdated) and event.chat.type in {"group", "supergroup"}:
            bot_id = getattr(data.get("bot"), "id", None)
            if event.user and (bot_id is None or event.user.id != bot_id) and topic_manager.is_group_configured(event.chat.id):
                return await handler(event, data)
            return

        # 2. Private Chat security check: fail-closed
        if not owner:
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
    return is_owner(user_id) and config.settings.is_user_allowed(user_id)
