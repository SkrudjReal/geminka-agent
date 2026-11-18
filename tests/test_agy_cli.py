import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.agy_cli import AgyCliClient, AgyNoResponseError, _Session


@pytest.mark.parametrize('outside', [False, True])
async def test_native_image_pixels_are_attached_only_from_current_user_uploads(tmp_path, monkeypatch, outside):
    import base64

    from PIL import Image

    from app.core import config

    monkeypatch.setattr(config, 'DOWNLOADS_DIR', tmp_path)
    directory = tmp_path / 'attachments' / '-1001' / '42'
    directory.mkdir(parents=True)
    path = (tmp_path if outside else directory) / 'first.png'
    Image.new('RGB', (32, 32), 'red').save(path)
    client = AgyCliClient()
    client._catalog = ['gemini']
    session = _Session(SimpleNamespace(returncode=None), 'gemini', 'low', asyncio.Lock(), conversation_id='unchanged')
    async def get_session(*args):
        return session
    sent = []
    async def send(session, prompt):
        sent.append(prompt)
        yield 'Красный кадр'
    monkeypatch.setattr(client, '_get_session', get_session)
    monkeypatch.setattr(client, '_send', send)
    async def turn():
        return [text async for text in client.stream(42, model='gemini', effort='low',
            messages=[{'role':'system','content':'PERSONA'}, {'role':'user','content':'Видео'}],
            tool_context={'requester_id':'42','chat_id':'-1001'}, images=[str(path)])]
    if outside:
        with pytest.raises(ValueError):
            await turn()
        assert sent == []
    else:
        assert await turn() == ['Красный кадр']
        assert sent[0][0]['type'] == 'text'
        assert sent[0][1]['type'] == 'image' and sent[0][1]['mimeType'] == 'image/png'
        assert base64.b64decode(sent[0][1]['data']) == path.read_bytes()
        assert await turn() == ['Красный кадр']
        assert session.conversation_id == 'unchanged'


@pytest.mark.parametrize("streamed", [False, True])
async def test_result_only_response_is_delivered_without_replaying_deltas(streamed):
    reader = asyncio.StreamReader()
    if streamed:
        reader.feed_data((json.dumps({"event": "step_update", "step_update": {
            "step_type": "agent_response", "text_delta": "answer",
        }}) + "\n").encode())
    reader.feed_data((json.dumps({"event": "result", "result": {
        "status": "success", "response": "answer",
    }}) + "\n").encode())
    reader.feed_eof()
    process = SimpleNamespace(stdin=object(), stdout=reader)
    session = _Session(process, "gemini", "high", asyncio.Lock())
    client = AgyCliClient()
    assert [part async for part in client._send(session, None)] == ["answer"]


async def test_success_without_any_response_is_not_silent_or_retryable():
    reader = asyncio.StreamReader()
    reader.feed_data(b'{"event":"result","result":{"status":"success"}}\n')
    session = _Session(SimpleNamespace(stdin=object(), stdout=reader), "gemini", "high", asyncio.Lock())
    with pytest.raises(AgyNoResponseError) as error:
        _ = [part async for part in AgyCliClient()._send(session, None)]
    assert not error.value.retryable


async def test_failed_result_does_not_expose_private_response_in_error():
    from app.services.agy_cli import AgyCliError

    reader = asyncio.StreamReader()
    reader.feed_data((json.dumps({"event": "result", "result": {
        "status": "ERROR", "response": "PRIVATE TRANSCRIPT",
    }}) + "\n").encode())
    session = _Session(SimpleNamespace(stdin=object(), stdout=reader), "gemini", "high", asyncio.Lock())
    with pytest.raises(AgyCliError) as error:
        _ = [part async for part in AgyCliClient()._send(session, None)]
    assert "PRIVATE TRANSCRIPT" not in str(error.value)


@pytest.mark.parametrize('reason', ['{"code":503}', 'UNAVAILABLE (code 503)',
                                   'The service is currently unavailable'])
async def test_failed_capacity_result_keeps_safe_reason(reason):
    from app.services.agy_cli import AgyCliError

    reader = asyncio.StreamReader()
    reader.feed_data((json.dumps({'event': 'result', 'result': {
        'status': 'ERROR', 'response': 'PRIVATE TRANSCRIPT ' + reason}}) + '\n').encode())
    session = _Session(SimpleNamespace(stdin=object(), stdout=reader), 'gemini', 'low', asyncio.Lock())
    with pytest.raises(AgyCliError, match='нет свободной capacity') as error:
        _ = [part async for part in AgyCliClient()._send(session, None)]
    assert 'PRIVATE' not in str(error.value)


@pytest.mark.asyncio
async def test_live_session_bootstrap_updates_and_fresh_memory(monkeypatch):
    client = AgyCliClient()
    client._catalog = ["gemini"]
    session = _Session(SimpleNamespace(returncode=None), "gemini", "low", asyncio.Lock(),
                       conversation_id='same-conversation')
    sent = []

    async def get_session(*args):
        return session

    async def send(session, prompt):
        sent.append(prompt)
        yield "answer"

    monkeypatch.setattr(client, "_get_session", get_session)
    monkeypatch.setattr(client, "_send", send)
    messages = [
        {"role": "system", "content": "PERSONA CATALOG"},
        {"role": "assistant", "content": "OLD HISTORY"},
        {"role": "user", "content": "hello"},
    ]

    async def turn(memory):
        return [part async for part in client.stream(
            1, model="gemini", effort="low", messages=messages, turn_context=memory
        )]

    assert await turn("MEMORY ONE") == ["answer"]
    await turn("MEMORY TWO")
    assert "PERSONA CATALOG" in sent[0] and "OLD HISTORY" in sent[0]
    assert "PERSONA CATALOG" not in sent[1] and "OLD HISTORY" not in sent[1]
    assert "MEMORY TWO" in sent[1] and "MEMORY ONE" not in sent[1]
    messages[0]["content"] = "UPDATED PERSONA"
    await turn("MEMORY THREE")
    assert "UPDATED PERSONA" in sent[2] and "OLD HISTORY" not in sent[2]
    messages[0]['content'] = 'PERSONA CATALOG'
    await turn('BACK TO COMPANION')
    assert 'PERSONA CATALOG' in sent[3] and 'UPDATED PERSONA' not in sent[3]
    assert session.conversation_id == 'same-conversation'
    session = _Session(SimpleNamespace(returncode=None), "gemini", "low", asyncio.Lock())
    await turn("MEMORY FOUR")
    assert "PERSONA CATALOG" in sent[4] and "OLD HISTORY" in sent[4]


async def test_group_context_cursor_committed_only_after_success_and_reset_with_session(monkeypatch):
    from app.services.agy_cli import AgyCliError

    client = AgyCliClient()
    client._catalog = ["gemini"]
    session = _Session(SimpleNamespace(returncode=None), "gemini", "low", asyncio.Lock())
    seen = []
    fail = [False]

    async def get_session(*args):
        return session

    async def build(*args, state, archive_enabled=True):
        seen.append(dict(state))
        state["cursor"] = args[2]
        return "GROUP DELTA"

    async def send(session, prompt):
        assert "GROUP DELTA" in prompt
        if fail[0]:
            raise AgyCliError("failed")
        yield "ok"

    async def close(session):
        pass

    monkeypatch.setattr(client, "_get_session", get_session)
    monkeypatch.setattr(client, "_send", send)
    monkeypatch.setattr(client, "_close_session", close)
    monkeypatch.setattr("app.services.group_context.build_context", build)
    async def turn(mid):
        return [part async for part in client.stream(
            -1, model="gemini", effort="low",
            messages=[{"role": "system", "content": "persona"}, {"role": "user", "content": "hi"}],
            tool_context={"requester_id": "42"},
            group_input={"chat_id": -1001, "message_id": mid},
        )]

    await turn(10)
    assert session.group_context_state == {"cursor": 10}
    fail[0] = True
    with pytest.raises(AgyCliError):
        await turn(20)
    assert session.group_context_state == {"cursor": 10}
    session = _Session(SimpleNamespace(returncode=None), "gemini", "low", asyncio.Lock())
    fail[0] = False
    await turn(30)
    assert seen == [{}, {"cursor": 10}, {}]
