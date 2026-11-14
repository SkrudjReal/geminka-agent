import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Chat, Message, User

from app.core.state import StateStore
from app.services import group_collector
from app.services.collector_mcp import CollectorConfig, _request
from app.services.group_collector import GroupCollectorBroker
from app.services.group_memory import GroupMemory, memory_delta
from app.services.sqlite_memory import SQLiteMemory
from app.services.topics import TopicManager


@pytest.fixture
def memory(tmp_path):
    return GroupMemory(AsyncMock(return_value="Обсудили чай [1]"),
                       root=tmp_path / "groups", store=StateStore(tmp_path / "state.db"))


async def test_group_and_user_isolation_private_database_never_searched(memory, tmp_path):
    private = SQLiteMemory(tmp_path / "private.db", store=memory.store)
    private.archive(42, "чай PRIVATE SECRET", key="one")
    for chat, user, text in ((-1001, 42, "чай GROUP ONE"), (-1002, 42, "чай GROUP TWO"),
                             (-1001, 43, "чай OTHER USER")):
        memory.archive(chat, user, text, key="same-key")
    recalled = memory.recall(-1001, 42, "чай")
    assert len(recalled["hits"]) == 1
    assert recalled["hits"][0]["text"] == "чай GROUP ONE"
    assert isinstance(recalled["hits"][0]["score"], float)
    assert memory.recall(-1001, 42, "SECRET")["hits"] == []
    memory.archive(-1001, 42, "duplicate", key="same-key")
    assert memory.database(-1001).count(42) == 1
    assert memory.recall(-3003, 42) == {"summary": "", "hits": []}
    assert not (memory.root / "-3003").exists()
    with pytest.raises(ValueError):
        memory.recall(42, 42, "чай")
    private.close()
    await memory.close()


async def test_background_summary_persists_and_originals_remain_searchable(memory):
    for index in range(8):
        memory.archive(-1001, 42, f"чай событие {index}", key=str(index))
    memory.schedule(-1001, 42)
    worker = memory.workers[-1001]
    worker.tasks[42].cancel()
    await asyncio.gather(worker.tasks[42], return_exceptions=True)
    worker.tasks.clear()
    worker.idle_seconds = 0
    memory.schedule(-1001, 42)
    await worker.tasks[42]
    memory.complete.assert_awaited_once()
    assert memory.complete.call_args.args[:2] == (-1001, 42)
    assert memory.recall(-1001, 42)["summary"] == "Обсудили чай [1]"
    assert memory.recall(-1001, 43)["summary"] == ""
    assert memory.database(-1001).count(42) == 9
    await memory.close()
    reloaded = GroupMemory(memory.complete, root=memory.root, store=memory.store)
    assert reloaded.recall(-1001, 42, "событие")["summary"] == "Обсудили чай [1]"
    assert reloaded.recall(-1001, 42, "событие")["hits"]
    await reloaded.close()


async def test_debug_does_not_create_database_or_start_summary(memory):
    memory.store.set_debug_mode(42, True)
    memory.archive(-1001, 42, "не сохранять", key="one")
    memory.schedule(-1001, 42)
    assert not memory.root.exists() and not memory.workers
    await memory.close()


def test_only_changed_summary_and_new_fts_records_are_sent():
    state = {}
    data = {"summary": "Саммари", "hits": [{"id": 7, "role": "user", "text": "чай", "kind": "dialogue", "score": -1.0}]}
    assert "Саммари" in memory_delta(data, state)
    assert memory_delta(data, state) == ""
    data["summary"] = "Обновлённое саммари"
    result = memory_delta(data, state)
    assert "Обновлённое" in result and '"hits"' not in result
    assert "Саммари" in memory_delta({**data, "summary": "Саммари"}, {})


async def test_middleware_archives_non_triggered_group_messages(memory, tmp_path, monkeypatch):
    from app.bot import middlewares

    topics = TopicManager(tmp_path / "topics.json")
    topics.add_chat(-1001, "Group", 1)
    monkeypatch.setattr(middlewares, "topic_manager", topics)
    monkeypatch.setattr(middlewares, "group_message_allowed", lambda *args: False)
    user = User(id=42, is_bot=False, first_name="User")
    event = Message(message_id=7, date=0, from_user=user, chat=Chat(id=-1001, type="supergroup"), text="люблю чай")
    handler = AsyncMock()
    await middlewares.OwnerAuthMiddleware()(handler, event, {
        "event_from_user": user, "antigravity_client": SimpleNamespace(group_memories=memory)})
    handler.assert_not_awaited()
    assert memory.recall(-1001, 42, "чай")["hits"][0]["text"] == "люблю чай"
    await memory.close()


async def test_scoped_memory_mcp_works_without_external_collector(memory, tmp_path, monkeypatch):
    topics = TopicManager(tmp_path / "topics.json")
    topics.add_chat(-1001, "Group", 1)
    monkeypatch.setattr(group_collector, "topic_manager", topics)
    monkeypatch.setattr(group_collector, "collector_config", lambda: None)
    memory.archive(-1001, 42, "чай VISIBLE", key="one")
    memory.archive(-1002, 42, "чай FOREIGN SECRET", key="one")
    broker = GroupCollectorBroker(group_memory=memory)
    try:
        credentials = await broker.credentials(-1001)
        cfg = CollectorConfig(credentials["group_broker_url"], credentials["group_broker_token"], group_proxy=True)
        tools = await _request(cfg, None, {})
        assert [tool["name"] for tool in tools["tools"]] == ["group_memory_search"]
        result = await _request(cfg, "group_memory_search", {"user_id": 42, "query": "чай"})
        assert "VISIBLE" in str(result) and "FOREIGN SECRET" not in str(result)
        assert not (await _request(cfg, "group_memory_search", {"user_id": 42, "chat_id": -1002}))["ok"]
        assert not (await _request(cfg, "group_memory_search", {"user_id": -1001}))["ok"]
    finally:
        await broker.close()
        await memory.close()
