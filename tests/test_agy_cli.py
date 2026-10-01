import asyncio
from types import SimpleNamespace

import pytest

from app.services.agy_cli import AgyCliClient, _Session


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
