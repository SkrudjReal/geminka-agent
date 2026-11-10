"""Optional, lazy read-only gateway to a configured Telegram archive MCP."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

logger = logging.getLogger(__name__)
ALLOWED_TOOLS = frozenset({
    "search_messages", "semantic_search", "list_users", "list_chats", "get_message",
    "get_user_history", "get_message_metrics", "archive_status", "get_statistics", "get_activity",
    "get_top_words", "get_top_phrases", "get_top_mentions", "get_top_authors", "get_top_posts",
    "get_reply_partners", "get_message_history", "compare_periods", "get_archive_coverage",
})


@dataclass(frozen=True)
class CollectorConfig:
    url: str
    token: str = field(repr=False)


def collector_config() -> CollectorConfig | None:
    if os.getenv("TG_COLLECTOR_MCP_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    url = os.getenv("TG_COLLECTOR_MCP_URL", "").strip()
    token = os.getenv("TG_COLLECTOR_MCP_TOKEN", "").strip()
    try:
        parsed = urlsplit(url)
        local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if (not token or not parsed.hostname or (parsed.scheme != "https" and not local_http)
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or any(c in token for c in "\r\n")):
            return None
        _ = parsed.port  # Validate a malformed port without exposing credentials in an error.
    except ValueError:
        return None
    return CollectorConfig(url, token)


async def _request(config: CollectorConfig, tool_name: str | None, arguments: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(
        headers={"Authorization": "Bearer " + config.token},
        timeout=httpx.Timeout(30, connect=5), follow_redirects=False,
    ) as http:
        async with streamable_http_client(config.url, http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                discovered = (await session.list_tools()).tools
                tools = {t.name: t for t in discovered if t.name in ALLOWED_TOOLS
                         and t.annotations and t.annotations.readOnlyHint is True}
                if tool_name is None:
                    return {"ok": True, "tool_count": len(tools), "tools": [dict(name=t.name, description=t.description,
                        input_schema=t.inputSchema) for t in tools.values()]}
                if tool_name not in tools:
                    return {"ok": False, "error": "Инструмент отсутствует или не подтверждён как read-only."}
                result = await session.call_tool(tool_name, arguments)
                payload = result.model_dump(mode="json", exclude_none=True)
                if len(json.dumps(payload, ensure_ascii=False).encode()) > 2_000_000:
                    return {"ok": False, "error": "Ответ слишком большой. Уменьши limit или сузь выборку."}
                return {"ok": not result.isError, "result": payload}


def register_collector(mcp: FastMCP, authorized: Callable[[], bool]) -> None:
    # No network at startup. Missing configuration does not affect the existing bridge.
    if collector_config() is None:
        return

    async def invoke(tool_name: str | None, arguments: dict[str, Any]) -> dict[str, Any]:
        if not authorized():
            return {"ok": False, "error": "Архив доступен только владельцу в личном чате."}
        configured = collector_config()
        if configured is None:
            return {"ok": False, "error": "Telegram Collector MCP отключён или не настроен."}
        if tool_name is not None and tool_name not in ALLOWED_TOOLS:
            return {"ok": False, "error": "Разрешены только известные read-only инструменты коллектора."}
        if len(json.dumps(arguments, ensure_ascii=False).encode()) > 64000:
            return {"ok": False, "error": "Аргументы превышают 64 КБ."}
        try:
            return await asyncio.wait_for(_request(configured, tool_name, arguments), timeout=40)
        except Exception as exc:
            # Exception text may contain URLs, headers or archived content; never log it.
            logger.warning("Optional collector MCP unavailable (%s)", type(exc).__name__)
            return {"ok": False, "error": "Telegram Collector MCP недоступен. Обычный чат продолжает работать."}

    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    @mcp.tool(annotations=annotations)
    async def collector_tools() -> dict[str, Any]:
        """Список read-only инструментов Telegram-архива с описаниями и схемами. Необязательный MCP, только владелец."""
        return await invoke(None, {})

    @mcp.tool(annotations=annotations)
    async def collector_call(tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Вызов инструмента Telegram-архива. Сначала узнай схему через collector_tools; ошибки не блокируют обычный чат."""
        return await invoke(tool_name, arguments or {})
