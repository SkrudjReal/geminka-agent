import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.agy_cli import AgyCliClient, AgyNoResponseError, _Session


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
    session = _Session(SimpleNamespace(returncode=None), "gemini", "low", asyncio.Lock())
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
    session = _Session(SimpleNamespace(returncode=None), "gemini", "low", asyncio.Lock())
    await turn("MEMORY FOUR")
    assert "UPDATED PERSONA" in sent[3] and "OLD HISTORY" in sent[3]


async def test_group_context_cursor_committed_only_after_success_and_reset_with_session(monkeypatch):
    from app.services.agy_cli import AgyCliError

    client = AgyCliClient()
    client._catalog = ["gemini"]
    session = _Session(SimpleNamespace(returncode=None), "gemini", "low", asyncio.Lock())
    seen = []
    fail = [False]

    async def get_session(*args):
        return session

    async def build(*args, state):
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
