"""Safe and resilient message broadcasting service for Geminka.

Inspired by standard Aiogram 3 broadcaster patterns (Latand template):
- Granular exception handling (TelegramForbiddenError, TelegramNotFound, TelegramRetryAfter, TelegramAPIError)
- Flood control & backoff via retry_after
- Controlled rate-limiting (asyncio.sleep)
"""

import asyncio
import logging
from typing import Iterable, Optional, Union

from aiogram import Bot, exceptions
from aiogram.enums import ParseMode
from aiogram.types import (
    ForceReply,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

logger = logging.getLogger("geminka-broadcaster")


async def send_message(
    bot: Bot,
    user_id: Union[int, str],
    text: str,
    parse_mode: Optional[str] = ParseMode.HTML,
    disable_notification: bool = False,
    reply_markup: Optional[Union[InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, ForceReply]] = None,
    retry_flood_once: bool = True,
) -> bool:
    """Safely sends a message to a specific user handling all Telegram API exceptions."""
    target_id = int(user_id) if str(user_id).isdigit() else user_id
    try:
        await bot.send_message(
            chat_id=target_id,
            text=text,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
            reply_markup=reply_markup,
        )
    except exceptions.TelegramForbiddenError:
        logger.warning(f"Target [ID:{target_id}]: bot was blocked by the user (TelegramForbiddenError).")
    except exceptions.TelegramNotFound:
        logger.warning(f"Target [ID:{target_id}]: chat not found (TelegramNotFound).")
    except exceptions.TelegramRetryAfter as e:
        logger.warning(f"Target [ID:{target_id}]: Flood limit exceeded. Sleeping for {e.retry_after}s.")
        if not retry_flood_once:
            return False
        await asyncio.sleep(e.retry_after)
        return await send_message(
            bot=bot,
            user_id=user_id,
            text=text,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
            reply_markup=reply_markup,
            retry_flood_once=False,
        )
    except exceptions.TelegramBadRequest as e:
        logger.error(f"Target [ID:{target_id}]: TelegramBadRequest: {e.message}")
    except exceptions.TelegramAPIError as e:
        logger.error(f"Target [ID:{target_id}]: TelegramAPIError: {e.message}")
    except Exception as e:
        logger.error(f"Target [ID:{target_id}]: Unexpected error while sending message: {e}", exc_info=True)
    else:
        logger.debug(f"Target [ID:{target_id}]: Message sent successfully.")
        return True
    return False


async def broadcast(
    bot: Bot,
    users: Iterable[Union[int, str]],
    text: str,
    parse_mode: Optional[str] = ParseMode.HTML,
    disable_notification: bool = False,
    reply_markup: Optional[Union[InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, ForceReply]] = None,
    delay: float = 0.05,
) -> int:
    """Broadcasts a message to a collection of users with rate limiting and metrics."""
    success_count = 0
    unique_users = set(users)

    for user_id in unique_users:
        if await send_message(
            bot=bot,
            user_id=user_id,
            text=text,
            parse_mode=parse_mode,
            disable_notification=disable_notification,
            reply_markup=reply_markup,
        ):
            success_count += 1
        await asyncio.sleep(delay)

    logger.info(f"Broadcast completed: {success_count}/{len(unique_users)} messages sent.")
    return success_count
