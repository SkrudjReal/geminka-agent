import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import group_context as context


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    context._history.clear()
    context._profiles.clear()
    monkeypatch.setattr(context.topic_manager, "is_group_configured", lambda chat: chat == -1001)
    monkeypatch.setattr(context, "collector_config", lambda: None)
    yield
    context._history.clear()
    context._profiles.clear()


def message(mid, thread=None, chat=-1001):
    return SimpleNamespace(
        message_id=mid, chat=SimpleNamespace(id=chat), message_thread_id=thread,
        from_user=SimpleNamespace(id=42, full_name="Alice", username="alice"),
        reply_to_message=None, text=f"text-{mid}", caption=None,
    )


async def test_last_100_before_current_deduplicated_and_topic_local():
    for mid in range(1, 111):
        context.observe(message(mid))
    context.observe(message(109))
    context.observe(message(500, thread=7))
    context.observe(message(501, chat=-2002))
    output = await context.build_context(-1001, None, 110, 42, "2026-11-13T00:00:00Z")
    data = json.loads(output.split("\n", 1)[1])
    assert data["preceding_count"] == 100
    assert [row["message_id"] for row in data["messages"]] == list(range(10, 110))
    assert set(context._history) == {(-1001, None), (-1001, 7)}


async def test_archive_uses_exact_user_chat_topic_and_merges_without_leaks(monkeypatch):
    monkeypatch.setattr(context, "collector_config", lambda: object())
    calls = []

    async def request(cfg, tool, args):
        calls.append(args)
        if tool != "search_messages":
            assert args["scope"]["chat_id"] == -1001 and args["scope"]["topic_id"] == 7
            return {"ok": True, "result": {"structuredContent": {}}}
        assert args["chat_id"] == -1001 and args["topic_id"] == 7
        rows = [dict(chat_id=-1001, topic_id=7, message_id=1, sender_user_id=42, text="old"),
                dict(chat_id=-2002, topic_id=7, message_id=2, sender_user_id=42, text="SECRET"),
                dict(chat_id=-1001, topic_id=8, message_id=3, sender_user_id=42, text="OTHER TOPIC")]
        return {"ok": True, "result": {"content": [{"type": "text", "text": json.dumps({"messages": rows})}]}}

    monkeypatch.setattr(context, "_request", request)
    context.observe(message(4, thread=7))
    output = await context.build_context(-1001, 7, 5, 42, "2026-11-13T00:00:00Z")
    assert "SECRET" not in output and "OTHER TOPIC" not in output
    data = json.loads(output.split("\n", 1)[1])
    assert [row["message_id"] for row in data["messages"]] == [1, 4]
    assert len(data["speaker_archive_context"]["examples"]) == 1
    assert any(args.get("sender_user_id") == 42 for args in calls)
    assert calls[0]["before_message_id"] == 5


async def test_session_delta_and_profile_cache_reset_on_new_session(monkeypatch):
    request = AsyncMock(return_value={"ok": True, "result": {"structuredContent": {"messages": []}}})
    monkeypatch.setattr(context, "collector_config", lambda: object())
    monkeypatch.setattr(context, "_request", request)
    context.observe(message(1))
    state = {}
    async def turn(mid, state):
        raw = await context.build_context(-1001, None, mid, 42, "2026-11-13T00:00:00Z", state=state)
        return json.loads(raw.split("\n", 1)[1])

    first = await turn(2, state)
    assert first["mode"] == "bootstrap" and first["preceding_count"] == 1
    assert request.await_count == 5
    context.observe(message(2))
    context.observe(message(3))
    second = await turn(4, state)
    assert [row["message_id"] for row in second["messages"]] == [3]
    assert "speaker_archive_context" not in second
    assert request.await_count == 6  # Only history, no repeated author analytics.
    fresh = await turn(4, {})
    assert fresh["mode"] == "bootstrap" and fresh["preceding_count"] == 3
    assert "speaker_archive_context" in fresh


async def test_profile_has_deeper_scoped_analytics_and_refreshes(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(context.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(context, "collector_config", lambda: object())
    calls = []
    async def request(cfg, tool, args):
        calls.append((tool, args))
        if tool == "search_messages":
            result = {"messages": []}
        elif tool == "get_reply_partners":
            assert args["user_id"] == 42 and args["scope"]["chat_id"] == -1001
            result = {"partners": [{"user_id": 77, "replies": 18}]}
        else:
            assert args["scope"]["sender_user_id"] == 42
            assert args["max_messages"] == 5000
            result = {"top": [{"term": "пример", "messages": 24}]}
        return {"ok": True, "result": {"structuredContent": result}}
    monkeypatch.setattr(context, "_request", request)
    state = {}
    raw = await context.build_context(-1001, 7, 10, 42, "2026-11-13T00:00:00Z", state=state)
    assert '"replies": 18' in raw and '"messages": 24' in raw
    assert {tool for tool, _ in calls} == {"search_messages", "get_top_words", "get_top_phrases", "get_reply_partners"}
    now[0] += 601
    raw = await context.build_context(-1001, 7, 11, 42, "2026-11-13T00:00:00Z", state=state)
    assert "speaker_archive_context" in raw and len(calls) == 10


async def test_archive_failure_preserves_local_context(monkeypatch):
    monkeypatch.setattr(context, "collector_config", lambda: object())
    monkeypatch.setattr(context, "_request", AsyncMock(side_effect=TimeoutError))
    context.observe(message(1))
    output = await context.build_context(-1001, None, 2, 42, "2026-11-13T00:00:00Z")
    assert "text-1" in output


async def test_middleware_records_non_triggering_messages(monkeypatch):
    from aiogram.types import Chat, Message, User

    from app.bot.middlewares import OwnerAuthMiddleware

    user = User(id=42, is_bot=False, first_name="Alice")
    event = Message(message_id=1, date=0, chat=Chat(id=-1001, type="supergroup"),
                    from_user=user, text="без префикса")
    monkeypatch.setattr("app.bot.middlewares.group_message_allowed", lambda *args: False)
    handler = AsyncMock()
    await OwnerAuthMiddleware()(handler, event, {"event_from_user": user})
    handler.assert_not_awaited()
    assert context._history[(-1001, None)][1]["text"] == "без префикса"
