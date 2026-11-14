from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from app.services.chat_observer import ChatObserver


def make_job(tmp_path):
    bot = SimpleNamespace(id=99, send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)))
    topics = SimpleNamespace(is_chat_active=lambda _: True, is_topic_active=lambda *args: False)
    store = SimpleNamespace(is_debug_mode=lambda _: False)
    return ChatObserver(bot, None, path=tmp_path / 'observer.db', topics=topics, store=store)


def message(mid, chat=-1001, uid=42):
    return Message(message_id=mid, date=0, chat=Chat(id=chat, type='supergroup'),
                   from_user=User(id=uid, first_name='Alice', is_bot=False), text=f'text {mid}')


def test_exact_batches_duplicate_and_chat_isolation(tmp_path):
    job = make_job(tmp_path)
    for mid in range(1, 100):
        job.observe(message(mid))
        job.observe(message(mid))
    job.observe(message(1, chat=-1002))
    assert job.claim() == []
    job.observe(message(100))
    rows = job.claim()
    assert len(rows) == 100
    assert {r['chat_id'] for r in rows} == {-1001}
    assert job.claim() == []
    job.db.close()


@pytest.mark.asyncio
async def test_reply_and_restart_never_replay(tmp_path):
    job = make_job(tmp_path)
    for mid in range(1, 101):
        job.observe(message(mid))
    job.generate = AsyncMock(return_value='{"message_id":12,"text":"А меня?))"}')
    assert await job.tick()
    assert len(job.generate.call_args.args[0]) == 100
    call = job.bot.send_message.call_args
    assert call.kwargs['reply_parameters'].message_id == 12
    await job.close()
    other = make_job(tmp_path)
    assert other.claim() == []
    await other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('output', ['NO_REPLY', '{"message_id":999,"text":"Нет"}',
                                   '{"message_id":1,"text":"Нет"}', 'broken'])
async def test_skip_invalid_and_addressed_targets(tmp_path, output):
    job = make_job(tmp_path)
    for mid in range(1, 101):
        job.observe(message(mid), addressed=mid == 1)
    job.generate = AsyncMock(return_value=output)
    await job.tick()
    job.bot.send_message.assert_not_awaited()
    await job.close()


@pytest.mark.asyncio
async def test_debug_self_and_unconfigured_not_stored(tmp_path):
    job = make_job(tmp_path)
    job.observe(message(1, uid=99))
    job.store.is_debug_mode = lambda _: True
    job.observe(message(2))
    job.store.is_debug_mode = lambda _: False
    job.topics.is_chat_active = lambda _: False
    job.observe(message(3))
    assert job.db.execute('SELECT count(*) FROM messages').fetchone()[0] == 0
    await job.close()


@pytest.mark.asyncio
async def test_no_generation_before_threshold_and_failed_send_not_retried(tmp_path):
    job = make_job(tmp_path)
    job.generate = AsyncMock(return_value='{"message_id":1,"text":"А меня?))"}')
    for mid in range(1, 100):
        job.observe(message(mid))
    assert not await job.tick()
    job.generate.assert_not_awaited()
    job.observe(message(100))
    job.bot.send_message.side_effect = TimeoutError()
    assert await job.tick()
    assert not await job.tick()
    job.bot.send_message.assert_awaited_once()
    await job.close()


def test_topics_are_separate(tmp_path):
    job = make_job(tmp_path)
    for mid in range(1, 101):
        job.observe(message(mid).model_copy(update={'message_thread_id': 1 if mid < 51 else 2}))
    assert job.claim() == []
    job.db.close()
