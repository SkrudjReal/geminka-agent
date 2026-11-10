import asyncio

import pytest

from app.core.state import StateStore
from app.services.sqlite_memory import SQLiteMemory
from app.services.summaries import SummaryWorker


def populated(tmp_path):
    store = StateStore(tmp_path / "state.db")
    memory = SQLiteMemory(tmp_path / "memory.db", store=store)
    for index in range(8):
        memory.archive(1, f"Чай, событие {index}", role="user" if index % 2 == 0 else "assistant", key=str(index))
    return memory


def test_summary_is_indexed_but_never_replaces_originals(tmp_path):
    memory = populated(tmp_path)
    source = memory.summary_input(1)
    assert len(source["messages"]) == 8
    assert memory.save_summary(1, source, "Обсудили чай [1]")
    assert memory.summary(1) == "Обсудили чай [1]"
    assert memory.count(1) == 9
    assert memory.search(1, "Обсудили")[0]["kind"] == "summary"
    assert memory.summary_input(1) is None
    assert not memory.save_summary(1, source, "outdated")
    assert memory.summary(2) == ""
    assert "Обсудили" in memory.format_memory_context(1)


@pytest.mark.asyncio
async def test_forget_during_summary_cannot_resurrect_data(tmp_path):
    memory = populated(tmp_path)
    source = memory.summary_input(1)
    await memory.forget(1)
    assert not memory.save_summary(1, source, "old personal information")
    assert memory.count(1) == 0 and memory.summary(1) == ""


@pytest.mark.asyncio
async def test_background_job_is_cancellable_and_keeps_pending_input(tmp_path):
    memory = populated(tmp_path)
    entered = asyncio.Event()

    async def complete(*args):
        entered.set()
        await asyncio.Event().wait()

    worker = SummaryWorker(memory, complete, idle_seconds=0)
    worker.schedule(1)
    await asyncio.wait_for(entered.wait(), timeout=2)
    worker.cancel(1)
    await worker.close()
    assert memory.summary(1) == ""
    assert memory.summary_input(1) is not None
    assert memory.count(1) == 8


@pytest.mark.asyncio
async def test_background_summary_and_debug_guard(tmp_path):
    memory = populated(tmp_path)

    async def complete(user_id, instructions, source):
        assert "событие" in source and "не вызывай инструменты" in instructions
        return "Краткое саммари про чай [1]"

    worker = SummaryWorker(memory, complete, idle_seconds=0)
    worker.schedule(1)
    task = worker.tasks[1]
    await task
    assert memory.summary(1) == "Краткое саммари про чай [1]"
    for index in range(8, 16):
        memory.archive(1, f"ещё событие {index}", key=str(index))
    memory.store.set_debug_mode(1, True)
    assert memory.summary_input(1) is None
    worker.schedule(1)
    await worker.tasks[1]
    assert memory.count(1) == 17
    await worker.close()


@pytest.mark.asyncio
async def test_summary_uses_isolated_same_model_without_touching_live_session(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.core.context import ContextManager
    from app.services.antigravity import AntigravityClient

    memory = populated(tmp_path)
    client = AntigravityClient(store=memory.store, contexts=ContextManager(memory.store), memories=memory)
    live_session = object()
    client._agy = SimpleNamespace(command="agy", _sessions={1: live_session}, aclose=AsyncMock())
    calls = []

    class BackgroundClient:
        def __init__(self, **kwargs):
            assert kwargs["sandbox_enabled"]

        async def stream(self, user_id, **kwargs):
            calls.append(kwargs)
            yield "Саммари"

        async def aclose(self):
            pass

    monkeypatch.setattr("app.services.antigravity.AgyCliClient", BackgroundClient)
    assert await client._summary_completion(1, "instructions", "source") == "Саммари"
    assert calls[0]["model"] == client.get_user_model(1)
    assert calls[0]["effort"] == "low" and calls[0]["debug"]
    assert calls[0]["tool_context"]["requester_id"] == "summary"
    assert client._agy._sessions[1] is live_session
    await client.aclose()
