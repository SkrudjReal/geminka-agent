"""Owner-only dynamic bridge to aiogram's Telegram Bot API methods."""

from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from typing import Any

from aiogram import Bot

from app.core import config

_METHOD_RE = re.compile(r"^[a-z][a-z0-9_]+$")

# These methods affect polling/authentication and must never be delegated to a model.
_BLOCKED_METHODS = {"close", "delete_webhook", "get_updates", "log_out", "set_webhook"}


class BotApiCallError(ValueError):
    """A model-generated Bot API call was rejected before reaching Telegram."""


def _normalize_json_strings(value: Any) -> Any:
    """Repair one extra JSON escaping layer in model-generated string values."""
    if isinstance(value, str):
        return (
            value
            .replace('\\"', '"')
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
        )
    if isinstance(value, list):
        return [_normalize_json_strings(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_normalize_json_strings(item) for item in value)
    if isinstance(value, Mapping):
        return {key: _normalize_json_strings(item) for key, item in value.items()}
    return value


def _is_owner(user_id: int) -> bool:
    return config.settings.owner_user_id is not None and user_id == config.settings.owner_user_id


async def call_bot_api(
    bot: Bot,
    *,
    method: str,
    params: Mapping[str, Any] | None,
    requester_id: int | None,
    default_chat_id: int | None = None,
) -> Any:
    """Call one public aiogram Bot method for an authenticated owner request."""
    method = method.strip()
    if requester_id is None or not _is_owner(requester_id):
        raise BotApiCallError("Bot API вызовы разрешены только владельцу.")
    if not _METHOD_RE.fullmatch(method) or method in _BLOCKED_METHODS:
        raise BotApiCallError(f"Метод Bot API запрещён: {method!r}")
    if params is not None and not isinstance(params, Mapping):
        raise BotApiCallError("params должен быть JSON-объектом.")

    function = getattr(bot, method, None)
    if function is None or not callable(function) or method.startswith("_"):
        raise BotApiCallError(f"Публичный метод aiogram не найден: {method!r}")

    call_params = _normalize_json_strings(dict(params or {}))
    if default_chat_id is not None and "chat_id" in inspect.signature(function).parameters:
        call_params.setdefault("chat_id", default_chat_id)

    result = function(**call_params)
    if inspect.isawaitable(result):
        return await result
    return result
