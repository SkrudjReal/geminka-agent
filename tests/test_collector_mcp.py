from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations

from app.core import config
from app.services import bot_api_mcp, collector_mcp


class Registry:
    def __init__(self):
        self.tools = {}

    def tool(self, **_kwargs):
        def register(fn):
            self.tools[fn.__name__] = fn
            return fn
        return register


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("TG_COLLECTOR_MCP_ENABLED", "true")
    monkeypatch.setenv("TG_COLLECTOR_MCP_URL", "https://collector.example/mcp")
    monkeypatch.setenv("TG_COLLECTOR_MCP_TOKEN", "private-test-token")


def test_disabled_or_incomplete_configuration_does_not_require_remote(monkeypatch):
    monkeypatch.delenv("TG_COLLECTOR_MCP_ENABLED", raising=False)
    registry = Registry()
    collector_mcp.register_collector(registry, lambda: True)
    assert registry.tools == {}
    monkeypatch.setenv("TG_COLLECTOR_MCP_ENABLED", "true")
    monkeypatch.setenv("TG_COLLECTOR_MCP_TOKEN", "")
    collector_mcp.register_collector(registry, lambda: True)
    assert registry.tools == {}


@pytest.mark.parametrize("url", ["http://public.example/mcp", "https://u:secret@host/mcp",
    "https://host/mcp?token=bad", "https://host:invalid/mcp", "not-a-url"])
def test_invalid_optional_url_is_disabled(enabled, monkeypatch, url):
    monkeypatch.setenv("TG_COLLECTOR_MCP_URL", url)
    assert collector_mcp.collector_config() is None


@pytest.mark.asyncio
async def test_lazy_gateway_denies_unapproved_calls_before_network(enabled, monkeypatch):
    async def unexpected(*_args):
        pytest.fail("Network must not be called")
    monkeypatch.setattr(collector_mcp, "_request", unexpected)
    registry = Registry()
    collector_mcp.register_collector(registry, lambda: False)
    assert set(registry.tools) == {"collector_tools", "collector_call"}
    assert not (await registry.tools["collector_tools"]())["ok"]
    collector_mcp.register_collector(registry, lambda: True)
    assert not (await registry.tools["collector_call"]("delete_message"))["ok"]
    assert not (await registry.tools["collector_call"]("search_messages", {"query": "x" * 64001}))["ok"]


@pytest.mark.asyncio
async def test_remote_down_is_a_redacted_optional_error(enabled, monkeypatch, caplog):
    async def broken(*_args):
        raise RuntimeError("Authorization: Bearer private-test-token")
    monkeypatch.setattr(collector_mcp, "_request", broken)
    registry = Registry()
    collector_mcp.register_collector(registry, lambda: True)
    result = await registry.tools["collector_tools"]()
    assert result["ok"] is False
    assert "Обычный чат" in result["error"]
    assert "private-test-token" not in caplog.text + str(result)
    assert "private-test-token" not in repr(collector_mcp.collector_config())


@pytest.mark.asyncio
async def test_read_only_discovery_and_result_with_authenticated_transport(enabled, monkeypatch):
    calls = []

    @asynccontextmanager
    async def transport(url, *, http_client):
        assert url == "https://collector.example/mcp"
        assert http_client.headers["Authorization"] == "Bearer private-test-token"
        assert not http_client.follow_redirects
        yield None, None, None

    class Session:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[
                Tool(name="search_messages", description="Search", inputSchema={"type": "object"},
                     annotations=ToolAnnotations(readOnlyHint=True)),
                Tool(name="get_user_history", inputSchema={}, annotations=ToolAnnotations(readOnlyHint=False)),
                Tool(name="delete_message", inputSchema={}, annotations=ToolAnnotations(readOnlyHint=True)),
            ])

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return CallToolResult(content=[TextContent(type="text", text='{"messages":[]}')])

    monkeypatch.setattr(collector_mcp, "streamable_http_client", transport)
    monkeypatch.setattr(collector_mcp, "ClientSession", Session)
    cfg = collector_mcp.collector_config()
    found = await collector_mcp._request(cfg, None, {})
    assert found["tool_count"] == 1
    assert [t["name"] for t in found["tools"]] == ["search_messages"]
    result = await collector_mcp._request(cfg, "search_messages", {"limit": 1})
    assert result["ok"]
    assert calls == [("search_messages", {"limit": 1})]
    assert not (await collector_mcp._request(cfg, "get_user_history", {}))["ok"]
    assert len(calls) == 1


@pytest.mark.parametrize("requester,chat,allowed", [("42", "42", True), ("42", "-100123", False),
    ("43", "43", False), ("summary", "42", False), ("", "42", False)])
def test_owner_private_scope_is_enforced(monkeypatch, requester, chat, allowed):
    monkeypatch.setattr(config, "settings", config.Settings.from_env({"TELEGRAM_OWNER_ID": "42"}))
    monkeypatch.setenv("GEMINKA_REQUESTER_ID", requester)
    monkeypatch.setenv("GEMINKA_CHAT_ID", chat)
    assert bot_api_mcp._collector_access() is allowed
