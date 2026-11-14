import json

import pytest

from app.services import collector_mcp, group_collector
from app.services.collector_mcp import CollectorConfig
from app.services.group_collector import (
    GroupCollectorBroker,
    chat_page,
    scoped_arguments,
    scoped_result,
    search_page,
)
from app.services.topics import TopicManager


def test_group_tool_scopes_and_cross_chat_reply_redaction():
    assert scoped_arguments(-1001, "search", {"query": "тема"}) == {"query": "тема", "chat_id": -1001}
    assert scoped_arguments(-1001, "get_reply_partners", {"user_id": 42}) == {"user_id": 42, "scope": {"chat_id": -1001}}
    for name in ("archive_status", "get_user_history", "list_users", "delete_message"):
        with pytest.raises(ValueError):
            scoped_arguments(-1001, name, {})
    for tool, args in (("search", {"chat_id": -1002}), ("get_activity", {"scope": {"chat_id": -1002}})):
        with pytest.raises(ValueError):
            scoped_arguments(-1001, tool, args)
    data = {"message": {"chat_id": -1001, "text": "visible"},
            "reply": [{"chat_id": -1002, "text": "FOREIGN SECRET"}]}
    result = scoped_result({"ok": True, "result": {"structuredContent": data,
        "content": [{"type": "text", "text": json.dumps(data)}]}}, -1001)
    assert "FOREIGN SECRET" not in json.dumps(result)
    assert result["result"]["structuredContent"]["message"]["text"] == "visible"


@pytest.mark.asyncio
async def test_real_group_broker_binds_capability_to_chat_without_full_token(tmp_path, monkeypatch):
    topics = TopicManager(tmp_path / "topics.json")
    topics.add_chat(-1001, "One", 1)
    topics.add_chat(-1002, "Two", 1)
    monkeypatch.setattr(group_collector, "topic_manager", topics)
    monkeypatch.setattr(group_collector, "collector_config", lambda: CollectorConfig("http://127.0.0.1:1/mcp", "FULL-PRIVATE-TOKEN"))
    calls = []

    async def remote(cfg, tool, arguments):
        assert cfg.token == "FULL-PRIVATE-TOKEN"
        calls.append((tool, arguments))
        if tool is None:
            return {"ok": True, "tools": [{"name": "search"}, {"name": "list_users"}], "tool_count": 2}
        return {"ok": True, "result": {"structuredContent": {"chat_id": arguments.get("chat_id", -1001), "text": "visible"}}}

    monkeypatch.setattr(group_collector, "_request", remote)
    broker = GroupCollectorBroker()
    try:
        one = await broker.credentials(-1001)
        two = await broker.credentials(-1002)
        assert one["group_broker_token"] != two["group_broker_token"]
        assert "FULL-PRIVATE-TOKEN" not in str(one)
        cfg = CollectorConfig(one["group_broker_url"], one["group_broker_token"], group_proxy=True)
        tools = await collector_mcp._request(cfg, None, {})
        assert tools["ok"] and [t["name"] for t in tools["tools"]] == ["search"]
        assert (await collector_mcp._request(cfg, "search", {"query": "тема"}))["ok"]
        assert calls[-1] == ("search", {"query": "тема", "public_only": True})
        assert (await collector_mcp._request(cfg, "search", {"chat_id": -1002}))["ok"]
        assert calls[-1] == ("search", {"chat_id": -1002, "public_only": True})
        count = len(calls)
        assert not (await collector_mcp._request(cfg, "search", {"chat_id": 42}))["ok"]
        assert not (await collector_mcp._request(cfg, "get_user_history", {"user_id": 42}))["ok"]
        wrong = CollectorConfig(cfg.url, "wrong-token", group_proxy=True)
        assert not (await collector_mcp._request(wrong, "search", {"query": "тема"}))["ok"]
        assert len(calls) == count
        topics.remove_chat(-1001)
        assert not (await collector_mcp._request(cfg, "search", {"query": "тема"}))["ok"]
    finally:
        await broker.close()


@pytest.mark.parametrize("tool,args", [
    ("search_messages", {"chat_id": 42}),
    ("get_message", {"chat_id": 42, "message_id": 1}),
    ("list_chats", {"chat_id": 42}),
    ("get_statistics", {"scope": {"chat_id": 42}}),
    ("get_reply_partners", {"user_id": 7, "scope": {"chat_id": 42}}),
    ("compare_periods", {"scope": {"chat_id": 42}}),
    ("search", {"chat_id": "-1001"}),
    ("search", {"chat_id": 0}),
])
def test_public_archive_rejects_private_targets(tool, args):
    with pytest.raises(ValueError):
        scoped_arguments(-1001, tool, args, public_archive=True)


def test_public_archive_groups_channels_and_safe_default():
    assert scoped_arguments(-1001, "list_chats", {"limit": 100}, public_archive=True) == {"limit": 100}
    for tool in ("search", "search_messages", "semantic_search"):
        assert scoped_arguments(-1001, tool, {"query": "кот", "public_only": False}, public_archive=True) == {
            "query": "кот", "public_only": True}
    assert scoped_arguments(-1001, "search", {"chat_id": -2002}, public_archive=True)["chat_id"] == -2002
    assert scoped_arguments(-1001, "get_statistics", {"scope": {"chat_id": -3003}}, public_archive=True) == {
        "scope": {"chat_id": -3003}}
    for tool in ("get_user_history", "list_users", "archive_status"):
        with pytest.raises(ValueError):
            scoped_arguments(-1001, tool, {}, public_archive=True)


def test_public_results_strip_private_chats_and_cross_chat_private_quotes():
    data = {"chats": [{"chat_id": -1001, "type": "supergroup"},
                      {"chat_id": -2002, "type": "channel"},
                      {"chat_id": 42, "type": "private", "title": "PRIVATE TITLE"}],
            "message": {"chat_id": -2002, "text": "visible", "reply_to_chat_id": 42},
            "reply": {"chat_id": 42, "text": "PRIVATE MESSAGE"}}
    result = scoped_result({"ok": True, "result": {"structuredContent": data,
        "content": [{"type": "text", "text": json.dumps(data)}]}}, None)
    assert "PRIVATE TITLE" not in json.dumps(result) and "PRIVATE MESSAGE" not in json.dumps(result)
    payload = result["result"]["structuredContent"]
    assert [row["chat_id"] for row in payload["chats"]] == [-1001, -2002]
    assert payload["message"]["reply_to_chat_id"] is None
    assert payload["message"]["text"] == "visible"


@pytest.mark.parametrize("structured", [True, False])
def test_search_page_preserves_full_texts_and_sources(structured):
    data = {"messages": [{"chat_id": -1001, "message_id": i, "text": "x" * 6000,
                          "raw_json": "unneeded" * 4000} for i in range(50)],
            "warnings": [{"component": "semantic", "error_type": "TimeoutError"}],
            "truncated": True, "next_cursor": {"before_message_id": 1}}
    raw = {"ok": True, "result": {"structuredContent": data} if structured else {
        "content": [{"type": "text", "text": json.dumps(data)}]}}
    out = search_page(raw)["result"]
    assert out["truncated"]
    assert out["warnings"] == data["warnings"] and out["next_cursor"] == data["next_cursor"]
    assert [row["text"] for row in out["messages"]] == [row["text"] for row in data["messages"]]
    assert len(out["messages"]) == 50
    assert "text_truncated" not in json.dumps(out) and "excerpts" not in out
    assert out["omitted_count"] + out["returned_count"] == 50
    assert "raw_json" not in json.dumps(out) and "content" not in out


def test_search_page_empty_private_and_errors():
    raw = {"ok": True, "result": {"structuredContent": {"messages": [
        {"chat_id": 42, "text": "PRIVATE"}, {"chat_id": -1001, "text": "visible"}]}}}
    result = search_page(raw)
    assert "PRIVATE" not in json.dumps(result)
    assert result["result"]["messages"][0]["text"] == "visible"
    assert search_page({"ok": False, "error": "unavailable"}) == {"ok": False, "error": "unavailable"}
    assert search_page({"ok": True, "result": {"structuredContent": {"messages": []}}})["result"]["messages"] == []


def test_chat_list_compacts_real_fastmcp_row_blocks_and_exposes_cursor():
    rows = [{"chat_id": -2002, "type": "channel", "title": "Channel", "username": "channel",
             "description": "unnecessary " * 1000, "linked_chat_id": -999999},
            {"chat_id": -1001, "type": "supergroup", "title": "Group", "username": None}]
    raw = {"ok": True, "result": {"content": [{"type": "text", "text": json.dumps(row)} for row in rows]}}
    result = chat_page(raw, {"limit": 2})
    data = result["result"]["structuredContent"]
    assert data["next_after_chat_id"] == -1001 and data["has_more"]
    assert len(json.dumps(result)) < len(json.dumps(raw)) / 3
    assert "description" not in json.dumps(result) and "linked_chat_id" not in json.dumps(result)
    assert [row["title"] for row in data["chats"]] == ["Channel", "Group"]
    assert "content" not in result["result"]  # No second escaped copy of the full page.


def test_chat_page_filters_private_rows_and_handles_structured_lists():
    raw = {"ok": True, "result": {"structuredContent": {"result": [
        {"chat_id": -1001, "type": "group", "title": "Group"},
        {"chat_id": 42, "type": "private", "title": "PRIVATE"}]}}}
    result = chat_page(raw, {"limit": 50})
    assert "PRIVATE" not in json.dumps(result)
    assert result["result"]["structuredContent"]["count"] == 1
    assert not result["result"]["structuredContent"]["has_more"]
