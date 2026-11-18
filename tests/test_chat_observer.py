from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
    for mid in range(1, 20):
        job.observe(message(mid))
        job.observe(message(mid))
    job.observe(message(1, chat=-1002))
    assert job.claim() == []
    job.observe(message(20))
    rows = job.claim()
    assert len(rows) == 20
    assert {r['chat_id'] for r in rows} == {-1001}
    assert job.claim() == []
    job.db.close()


@pytest.mark.asyncio
async def test_reply_and_restart_never_replay(tmp_path):
    job = make_job(tmp_path)
    for mid in range(1, 21):
        job.observe(message(mid))
    job.generate = AsyncMock(return_value='{"message_id":12,"text":"А меня?))"}')
    assert await job.tick()
    assert len(job.generate.call_args.args[0]) == 20
    call = job.bot.send_message.call_args
    assert call.kwargs['reply_parameters'].message_id == 12
    await job.close()


@pytest.mark.asyncio
async def test_selected_reply_receives_same_local_memory(tmp_path, monkeypatch):
    import json

    from app.services.decisions import decisions

    job = make_job(tmp_path)
    job.client = SimpleNamespace(
        group_memories=SimpleNamespace(recall=Mock(return_value={'summary': 'Shared history', 'hits': []})),
        _agy=SimpleNamespace(command='unused'), get_user_model=lambda _: 'test')
    choose = AsyncMock(return_value=12)
    monkeypatch.setattr(decisions, 'observer_target', choose)
    captured = {}

    class Generator:
        def __init__(self, **kwargs):
            pass

        async def stream(self, *args, **kwargs):
            captured.update(kwargs)
            yield '{"message_id":12,"text":"Привет)"}'

        async def aclose(self):
            pass

    monkeypatch.setattr('app.services.chat_observer.AgyCliClient', Generator)
    rows = [{'chat_id': -1001, 'message_id': 12, 'user_id': 42, 'text': 'hello', 'eligible': 1}]
    assert json.loads(await job.generate(rows))['message_id'] == 12
    payload = json.loads(captured['messages'][1]['content'])
    assert payload['local_memory'] == choose.call_args.kwargs['context']
    assert payload['messages'] == rows
    await job.close()
    other = make_job(tmp_path)
    assert other.claim() == []
    await other.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('output', ['NO_REPLY', '{"message_id":999,"text":"Нет"}',
                                   '{"message_id":1,"text":"Нет"}', 'broken'])
async def test_skip_invalid_and_addressed_targets(tmp_path, output):
    job = make_job(tmp_path)
    for mid in range(1, 22):
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
    for mid in range(1, 20):
        job.observe(message(mid))
    assert not await job.tick()
    job.generate.assert_not_awaited()
    job.observe(message(20))
    job.bot.send_message.side_effect = TimeoutError()
    assert await job.tick()
    assert not await job.tick()
    job.bot.send_message.assert_awaited_once()
    await job.close()


def test_topics_are_separate(tmp_path):
    job = make_job(tmp_path)
    for mid in range(1, 21):
        job.observe(message(mid).model_copy(update={'message_thread_id': 1 if mid < 11 else 2}))
    assert job.claim() == []
    job.db.close()


def test_addressed_messages_and_commands_do_not_count(tmp_path):
    job = make_job(tmp_path)
    for mid in range(1, 21):
        job.observe(message(mid), addressed=True)
    job.observe(message(21).model_copy(update={'text': '/mood'}))
    assert job.claim() == []
    for mid in range(22, 42):
        job.observe(message(mid))
    assert [r['message_id'] for r in job.claim()] == list(range(22, 42))
    job.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('decision', [0, None])
async def test_local_memory_reaches_decision_without_starting_generator(tmp_path, monkeypatch, decision):
    from app.services.decisions import decisions

    job = make_job(tmp_path)
    recall = Mock(return_value={'summary': 'Known participant', 'hits': [{'text': 'Earlier conversation'}]})
    job.client = SimpleNamespace(group_memories=SimpleNamespace(recall=recall))
    choose = AsyncMock(return_value=decision)
    monkeypatch.setattr(decisions, 'observer_target', choose)
    rows = [{'chat_id': -1001, 'user_id': uid, 'text': 'hello', 'eligible': 1, 'message_id': i}
            for i, uid in enumerate([42, 43, 42], 1)]
    assert await job.generate(rows) == 'NO_REPLY'
    assert [call.args[:2] for call in recall.call_args_list] == [(-1001, 42), (-1001, 43)]
    assert [r['user_id'] for r in choose.call_args.kwargs['context']] == [42, 43]
    assert choose.call_args.kwargs['context'][0]['summary'] == 'Known participant'
    await job.close()
