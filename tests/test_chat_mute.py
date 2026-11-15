from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage, SendPhoto

from app.bot import chat_mute
from app.core.state import StateStore


@pytest.mark.asyncio
@pytest.mark.parametrize('status,allowed', [('creator', True), ('administrator', True), ('member', False)])
async def test_admin_only_and_persisted_timeout(monkeypatch, tmp_path, status, allowed):
    now = [1000.0]
    monkeypatch.setattr('app.core.state.time.time', lambda: now[0])
    store = StateStore(tmp_path / 'state.db')
    monkeypatch.setattr(chat_mute, 'state_store', store)
    message = SimpleNamespace(chat=SimpleNamespace(id=-1, type='supergroup'),
                              from_user=SimpleNamespace(id=5), sender_chat=None, answer=AsyncMock())
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status)))
    await chat_mute.mute_columbina(message, bot)
    assert store.is_chat_muted(-1) == allowed
    assert StateStore(tmp_path / 'state.db').is_chat_muted(-1) == allowed
    assert not store.is_chat_muted(-2)
    assert message.answer.await_count == int(allowed)
    now[0] += 1800
    assert not store.is_chat_muted(-1)


@pytest.mark.asyncio
@pytest.mark.parametrize('method', [SendMessage(chat_id=-1, text='test'),
                                  SendPhoto(chat_id='-1', photo='file-id')])
async def test_blocks_inflight_deliveries_but_not_other_chats(monkeypatch, tmp_path, method):
    store = StateStore(tmp_path / 'state.db')
    monkeypatch.setattr(chat_mute, 'state_store', store)
    store.mute_chat(-1)
    send = AsyncMock(return_value=True)
    with pytest.raises(TelegramForbiddenError):
        await chat_mute.block_muted_delivery(send, None, method)
    send.assert_not_awaited()
    assert await chat_mute.block_muted_delivery(send, None, SendMessage(chat_id=-2, text='ok'))


@pytest.mark.asyncio
async def test_anonymous_administrator_and_timer_extension(monkeypatch, tmp_path):
    now = [1000.0]
    monkeypatch.setattr('app.core.state.time.time', lambda: now[0])
    store = StateStore(tmp_path / 'state.db')
    monkeypatch.setattr(chat_mute, 'state_store', store)
    message = SimpleNamespace(chat=SimpleNamespace(id=-1, type='supergroup'),
                              from_user=SimpleNamespace(id=5), sender_chat=SimpleNamespace(id=-1),
                              answer=AsyncMock())
    bot = SimpleNamespace(get_chat_member=AsyncMock())
    await chat_mute.mute_columbina(message, bot)
    now[0] += 1000
    await chat_mute.mute_columbina(message, bot)
    now[0] += 1000
    assert store.is_chat_muted(-1)
    message.answer.assert_awaited_once()
    bot.get_chat_member.assert_not_awaited()
