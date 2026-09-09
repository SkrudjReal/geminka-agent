import pytest
from aiogram import types

from app.bot.middlewares import OwnerAuthMiddleware


def _group_message(thread_id: int | None) -> types.Message:
    return types.Message.model_construct(
        chat=types.Chat.model_construct(id=-1004488980222, type="supergroup"),
        message_thread_id=thread_id,
    )


@pytest.mark.asyncio
async def test_middleware_silently_drops_unconfigured_group_message(monkeypatch) -> None:
    middleware = OwnerAuthMiddleware()
    event = _group_message(7)
    called = False

    async def handler(event, data):
        nonlocal called
        called = True
        return "handled"

    monkeypatch.setattr(
        "app.bot.middlewares.topic_manager.is_topic_active",
        lambda chat_id, topic_id: False,
    )

    result = await middleware(handler, event, {})

    assert result is None
    assert not called


@pytest.mark.asyncio
async def test_middleware_passes_only_active_group_topic_for_allowed_user(monkeypatch) -> None:
    middleware = OwnerAuthMiddleware()
    event = _group_message(5)
    allowed_user = types.User.model_construct(id=1224362805, is_bot=False, first_name="Owner")
    unauthorized_user = types.User.model_construct(id=999999999, is_bot=False, first_name="Stranger")

    async def handler(event, data):
        return "handled"

    monkeypatch.setattr(
        "app.bot.middlewares.topic_manager.is_topic_active",
        lambda chat_id, topic_id: chat_id == -1004488980222 and topic_id == 5,
    )
    monkeypatch.setattr(
        "app.core.config.Settings.is_user_allowed",
        lambda self, user_id: user_id == 1224362805,
    )

    # 1. Allowed user -> handled
    result_allowed = await middleware(handler, event, {"event_from_user": allowed_user})
    assert result_allowed == "handled"

    # 2. Unauthorized user -> blocked (None)
    result_unauthorized = await middleware(handler, event, {"event_from_user": unauthorized_user})
    assert result_unauthorized is None