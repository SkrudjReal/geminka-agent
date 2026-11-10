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


@pytest.mark.asyncio
async def test_live_session_bootstrap_updates_and_fresh_memory(monkeypatch):
    client = AgyCliClient()
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
