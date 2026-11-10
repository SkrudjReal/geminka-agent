import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.context import ContextManager
from app.core.state import StateStore
from app.services.agy_cli import _Session
from app.services.antigravity import AntigravityClient
from app.services.sqlite_memory import MemoryWriteDisabled, SQLiteMemory


def make_memory(tmp_path):
    store = StateStore(tmp_path / "state.db")
    return SQLiteMemory(tmp_path / "memory.db", store=store)


def test_attach_does_not_initialize_and_read_connections_reject_writes(tmp_path):
    import sqlite3

    memory = make_memory(tmp_path)
    memory.archive(1, "Память для поиска", key="read-test")
    state = StateStore(memory.store.path, initialize=False)
    attached = SQLiteMemory(memory.path, store=state, initialize=False, read_only=True)
    assert attached.search(1, "поиска")
    with attached._connect() as db:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("DELETE FROM records")
    with pytest.raises(MemoryWriteDisabled):
        attached.add_memory(1, "запрещённая запись")
    missing = tmp_path / "absent" / "memory.db"
    SQLiteMemory(missing, store=state, initialize=False, read_only=True)
    assert not missing.parent.exists()


def test_mcp_import_never_initializes_schema(tmp_path):
    import os
    import subprocess
    import sys

    script = """
import sqlite3
def reject(*args, **kwargs):
    raise AssertionError('MCP import attempted a database connection')
sqlite3.connect = reject
import app.services.bot_api_mcp
print('no database connections')
"""
    result = subprocess.run([sys.executable, "-c", script],
                            env=dict(os.environ, GEMINKA_MCP_PROCESS="1"),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "no database connections" in result.stdout


def test_archive_fts_bm25_isolation_and_neighbors(tmp_path):
    memory = make_memory(tmp_path)
    memory.archive(1, "Люблю чай с бергамотом", key="1")
    memory.archive(1, "Люблю чай с бергамотом", key="1")
    memory.archive(1, "Какой чай?", role="assistant", key="1:reply")
    memory.archive(2, "Другой человек любит бергамот", key="1")
    assert memory.count(1) == 2
    hits = memory.search(1, 'бергамотом " OR )')
    assert hits and all(hit["user_id"] == 1 for hit in hits)
    assert isinstance(hits[0]["score"], float)
    rows = memory.neighbors(1, hits[0]["id"])
    assert len(rows) == 2
    assert memory.neighbors(2, hits[0]["id"]) == []
    assert memory.search(1, "' -- ()") == []
    assert memory.search(1, "notfound") == []
    assert SQLiteMemory(memory.path, store=memory.store).count(1) == 2


@pytest.mark.asyncio
async def test_profile_updates_deletes_fts_debug_and_forget(tmp_path):
    memory = make_memory(tmp_path)
    assert memory.add_memory(1, "любит кофе")
    assert not memory.add_memory(1, "любит кофе")
    memory.set_portrait(1, "любит лес")
    memory.set_portrait(1, "любит море")
    assert memory.portrait(1) == "любит море"
    assert not memory.search(1, "лес")
    assert "море" in memory.format_memory_context(1)
    row = memory.search(1, "кофе")[0]
    assert not memory.remove(2, row["id"])
    assert memory.remove(1, row["id"])
    assert not memory.search(1, "кофе")
    memory.store.set_debug_mode(1, True)
    for operation in (
        lambda: memory.archive(1, "new"), lambda: memory.add_memory(1, "new"),
        lambda: memory.set_portrait(1, "new"), lambda: memory.remove(1, 1),
    ):
        with pytest.raises(MemoryWriteDisabled):
            operation()
    memory.add_memory(2, "не удалять")
    await memory.forget(1)
    assert memory.count(1) == 0 and not memory.search(1, "море")
    assert memory.count(2) == 1


@pytest.mark.asyncio
async def test_no_automatic_search_or_background_llm_and_archive_retains_failed_input(tmp_path):
    memory = make_memory(tmp_path)
    client = AntigravityClient(
        store=memory.store, contexts=ContextManager(memory.store), memories=memory,
    )
    memory.search = lambda *args: pytest.fail("Search must be on demand")
    seen = []

    async def stream(*args, **kwargs):
        seen.append(kwargs)
        yield "Ответ"

    client._agy = SimpleNamespace(stream=stream, aclose=AsyncMock())
    assert [part async for part in client.generate_stream(1, "Привет", memory_input={"private": True, "text": "Привет", "key": "tg:1"})] == ["Ответ"]
    assert memory.count(1) == 2
    assert "Привет" in seen[0]["messages"][-1]["content"]
    assert not hasattr(memory, "schedule_analysis")
    await client.aclose()


@pytest.mark.asyncio
async def test_mcp_memory_tools_private_auth_and_debug(tmp_path, monkeypatch):
    import json

    from app.core import config
    from app.services import bot_api_mcp

    memory = make_memory(tmp_path)
    monkeypatch.setattr(bot_api_mcp, "memory", memory)
    monkeypatch.setattr(config, "settings", config.Settings.from_env({"TELEGRAM_ALLOWED_USERS": "1,2"}))
    monkeypatch.setenv("GEMINKA_REQUESTER_ID", "1")
    monkeypatch.setenv("GEMINKA_CHAT_ID", "1")
    assert json.loads(await bot_api_mcp.memory_write("add", "люблю чай"))["ok"]
    assert json.loads(await bot_api_mcp.memory_search("чай"))["records"]
    monkeypatch.setenv("GEMINKA_CHAT_ID", "-100123")
    assert not json.loads(await bot_api_mcp.memory_search("чай"))["ok"]
    assert not json.loads(await bot_api_mcp.memory_write("profile", "secret"))["ok"]
    monkeypatch.setenv("GEMINKA_CHAT_ID", "2")
    monkeypatch.setenv("GEMINKA_REQUESTER_ID", "2")
    assert not json.loads(await bot_api_mcp.memory_search("чай"))["records"]
    memory.store.set_debug_mode(2, True)
    assert not json.loads(await bot_api_mcp.memory_write("add", "new"))["ok"]


@pytest.mark.asyncio
async def test_persona_and_profile_are_frozen_until_new_live_session(tmp_path, monkeypatch):
    memory = make_memory(tmp_path)
    memory.set_portrait(1, "INITIAL PROFILE")
    client = AntigravityClient(store=memory.store, contexts=ContextManager(memory.store), memories=memory)
    agy = client._agy
    sent = []

    async def session(user_id, model, effort, context):
        if user_id not in agy._sessions:
            agy._sessions[user_id] = _Session(SimpleNamespace(returncode=None), model, effort, asyncio.Lock())
        return agy._sessions[user_id]

    async def send(session, prompt):
        sent.append(prompt)
        yield "OK"

    monkeypatch.setattr(agy, "_get_session", session)
    monkeypatch.setattr(agy, "_send", send)
    monkeypatch.setattr("app.core.config.get_system_prompt", lambda: "UNIQUE PERSONA")

    async def turn(text):
        return [part async for part in client.generate_stream(1, text)]

    await turn("first")
    memory.set_portrait(1, "UPDATED PROFILE")
    await turn("second")
    assert "UNIQUE PERSONA" in sent[0] and "INITIAL PROFILE" in sent[0]
    assert "UNIQUE PERSONA" not in sent[1] and "PROFILE" not in sent[1]
    agy._sessions.clear()
    await turn("third")
    assert "UNIQUE PERSONA" in sent[2] and "UPDATED PROFILE" in sent[2]
    agy._sessions.clear()
    await client.aclose()
