from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.core import config
from app.services.bot_api import BotApiCallError, call_bot_api


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, *, chat_id, text):
        self.calls.append((chat_id, text))
        return SimpleNamespace(message_id=7)

    async def get_me(self):
        return "ok"


@pytest.mark.asyncio
async def test_owner_can_call_public_bot_method(monkeypatch):
    monkeypatch.setattr(config, "settings", replace(config.settings, owner_user_id=42))
    bot = FakeBot()

    result = await call_bot_api(
        bot,
        method="send_message",
        params={"text": "hello"},
        requester_id=42,
        default_chat_id=100,
    )

    assert result.message_id == 7
    assert bot.calls == [(100, "hello")]


@pytest.mark.asyncio
async def test_owner_call_removes_double_json_escaping_from_html_text(monkeypatch):
    monkeypatch.setattr(config, "settings", replace(config.settings, owner_user_id=42))
    bot = FakeBot()

    await call_bot_api(
        bot,
        method="send_message",
        params={"chat_id": 100, "text": '<tg-emoji emoji-id=\\"527\\">🫣</tg-emoji>\\nnext'},
        requester_id=42,
    )

    assert bot.calls == [(100, '<tg-emoji emoji-id="527">🫣</tg-emoji>\nnext')]


@pytest.mark.asyncio
async def test_non_owner_and_polling_methods_are_rejected(monkeypatch):
    monkeypatch.setattr(config, "settings", replace(config.settings, owner_user_id=42))
    bot = FakeBot()

    with pytest.raises(BotApiCallError):
        await call_bot_api(bot, method="send_message", params={}, requester_id=99)
    with pytest.raises(BotApiCallError):
        await call_bot_api(bot, method="get_updates", params={}, requester_id=42)
