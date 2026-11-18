import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.bot import handlers
from app.bot.batching import TurnBatches
from app.core.context import ContextManager
from app.core.state import StateStore
from app.services.antigravity import AntigravityClient
from app.services.sqlite_memory import SQLiteMemory


@pytest.mark.asyncio
@pytest.mark.parametrize("preview", [True, False])
async def test_three_messages_produce_one_updated_reply_and_no_stale_archive(tmp_path, monkeypatch, preview):
    store = StateStore(tmp_path / "state.db")
    memory = SQLiteMemory(tmp_path / "memory.db", store=store)
    client = AntigravityClient(store=store, contexts=ContextManager(store), memories=memory)
    started, resume = asyncio.Event(), asyncio.Event()
    calls = []

    async def stream(user_id, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            if preview:
                yield "Промежуточный ответ <tg-sticker tag=\"obsolete\"/>"
            started.set()
            await resume.wait()
            yield " устарел"
        else:
            yield "Один итоговый ответ на first, second и third"

    client._agy = SimpleNamespace(stream=stream, aclose=AsyncMock(), _sessions={})
    monkeypatch.setattr(client, "schedule_memory_summary", lambda user_id: None)
    monkeypatch.setattr(handlers, "state_store", store)
    monkeypatch.setattr(handlers, "check_auth", lambda user_id: True)
    batches = TurnBatches()
    monkeypatch.setattr(handlers, "turn_batches", batches)

    @asynccontextmanager
    async def typing(**kwargs):
        yield

    monkeypatch.setattr(handlers.ChatActionSender, "typing", typing)

    async def prepare(message, bot, client):
        return {
            "prompt": message.text, "emotional_context": "", "adaptive_context": "", "user_emojis_context": "",
            "memory_input": {"private": True, "text": message.text, "key": f"tg:1:{message.message_id}", "chat_id": 1},
        }

    monkeypatch.setattr(handlers, "_prepare_message", prepare)
    bot = SimpleNamespace(
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=100)),
        edit_message_text=AsyncMock(), send_sticker=AsyncMock(),
    )

    def message(index, text):
        return SimpleNamespace(
            from_user=SimpleNamespace(id=1, first_name="User"),
            chat=SimpleNamespace(id=1, type="private"), message_thread_id=None,
            message_id=index, text=text, caption=None, voice=None, reply_to_message=None, answer=AsyncMock(),
        )

    messages = [message(1, "first"), message(2, "second"), message(3, "third")]
    tasks = [asyncio.create_task(handlers.handle_any_message(messages[0], bot, client))]
    await asyncio.wait_for(started.wait(), timeout=2)
    tasks += [asyncio.create_task(handlers.handle_any_message(item, bot, client)) for item in messages[1:]]
    await asyncio.sleep(0)
    assert batches.pending[(1, 1, None)].revision == 3
    resume.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)
    assert len(calls) == 2
    assert all(text in calls[-1]["messages"][-1]["content"] for text in ("first", "second", "third"))
    bot.send_message.assert_awaited_once()
    assert "итоговый" in bot.edit_message_text.call_args.kwargs["text"]
    bot.send_sticker.assert_not_called()
    assert len(store.get_messages(1)) == 2
    assert "Промежуточный" not in str(store.get_messages(1))
    with memory._connect() as db:
        rows = db.execute("SELECT role,text FROM records ORDER BY id").fetchall()
    assert [row[0] for row in rows] == ["user", "user", "user", "assistant"]
    assert not batches.pending
    await client.aclose()


@pytest.mark.asyncio
async def test_waiter_cancellation_keeps_batch_and_different_chats_are_isolated():
    batches = TurnBatches()
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    async def run(batch):
        entered.set()
        await release.wait()
        seen.append(list(batch.items))
        batch.accepting = False

    first = asyncio.create_task(batches.submit((1, 1, None), "one", run))
    await entered.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(batches.submit((1, 1, None), "two", run))
    other = asyncio.create_task(batches.submit((1, -100, 7), "separate", run))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(second, other)
    assert ["one", "two"] in seen and ["separate"] in seen
    assert not batches.pending
    await batches.close()


async def test_failed_group_answer_is_addressed_to_its_requester(monkeypatch):
    from app.bot.batching import PendingTurn

    message = SimpleNamespace(from_user=SimpleNamespace(id=2, first_name='User'),
                              chat=SimpleNamespace(id=-1001, type='supergroup'),
                              message_id=41, is_topic_message=False, reply=AsyncMock(), answer=AsyncMock())
    consumer = SimpleNamespace(stream_from_generator=AsyncMock(side_effect=RuntimeError('failed')))
    monkeypatch.setattr(handlers, 'TelegramStreamConsumer', lambda **_: consumer)

    @asynccontextmanager
    async def typing(**kwargs):
        yield

    monkeypatch.setattr(handlers.ChatActionSender, 'typing', typing)
    batch = PendingTurn(items=[(message, 0)])
    await handlers._answer_batch(batch, SimpleNamespace(), SimpleNamespace())
    message.reply.assert_awaited_once()
    message.answer.assert_not_awaited()


async def test_deliberate_silence_completes_batch_without_error_or_fake_style_delivery(tmp_path, monkeypatch):
    from app.bot.batching import PendingTurn

    store = StateStore(tmp_path / 'state.db')
    memory = SQLiteMemory(tmp_path / 'memory.db', store=store)
    client = AntigravityClient(store=store, contexts=ContextManager(store), memories=memory)
    async def stream(*args, **kwargs):
        yield '<tg-silent/>'
    client._agy = SimpleNamespace(stream=stream, aclose=AsyncMock(), _sessions={})
    monkeypatch.setattr(handlers, 'state_store', store)
    monkeypatch.setattr(client, 'schedule_memory_summary', lambda _: None)

    @asynccontextmanager
    async def typing(**kwargs):
        yield
    monkeypatch.setattr(handlers.ChatActionSender, 'typing', typing)
    async def prepare(message, bot, client):
        return {'prompt': 'клумба', 'emotional_context': '', 'adaptive_context': '', 'user_emojis_context': '',
                'memory_input': {'private': True, 'text': 'клумба', 'key': 'tg:42:1', 'chat_id': 42}}
    monkeypatch.setattr(handlers, '_prepare_message', prepare)
    message = SimpleNamespace(from_user=SimpleNamespace(id=42, first_name='User'),
                              chat=SimpleNamespace(id=42, type='private'),
                              message_id=1, is_topic_message=False, reply=AsyncMock(), answer=AsyncMock())
    bot = SimpleNamespace(send_message=AsyncMock(), send_sticker=AsyncMock())
    batch = PendingTurn(items=[(message, 0)])
    await handlers._answer_batch(batch, bot, client)
    assert batch.accepting is False
    bot.send_message.assert_not_awaited()
    bot.send_sticker.assert_not_awaited()
    message.reply.assert_not_awaited()
    message.answer.assert_not_awaited()
    assert store.get_messages(42)[-1]['content'] == '<tg-silent/>'
    with memory._connect() as db:
        assert db.execute('SELECT count(*) FROM style_deliveries').fetchone()[0] == 0
    await client.aclose()
    memory.close()
