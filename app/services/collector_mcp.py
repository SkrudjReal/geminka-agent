"""Optional, lazy read-only gateway to a configured Telegram archive MCP."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import time
from collections import OrderedDict
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
ALLOWED_TOOLS = frozenset(
    {
        "search",
        "search_messages",
        "semantic_search",
        "list_users",
        "list_chats",
        "get_message",
        "get_user_history",
        "get_message_metrics",
        "archive_status",
        "get_statistics",
        "get_activity",
        "get_top_words",
        "get_top_phrases",
        "get_top_mentions",
        "get_top_authors",
        "get_top_posts",
        "get_reply_partners",
        "get_message_history",
        "compare_periods",
        "get_archive_coverage",
        "group_memory_search",
    }
)


@dataclass(frozen=True)
class CollectorConfig:
    url: str
    token: str = field(repr=False)
    group_proxy: bool = False
    host_header: str = ""


def collector_config() -> CollectorConfig | None:
    if os.getenv("GEMINKA_GROUP_MODE") == "1":
        url = os.getenv("GEMINKA_GROUP_BROKER_URL", "")
        token = os.getenv("GEMINKA_GROUP_BROKER_TOKEN", "")
        parsed = urlsplit(url)
        if parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and token:
            return CollectorConfig(url, token, group_proxy=True)
        return None
    if os.getenv("TG_COLLECTOR_MCP_ENABLED", "false").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return None
    url = os.getenv("TG_COLLECTOR_MCP_URL", "").strip()
    token = os.getenv("TG_COLLECTOR_MCP_TOKEN", "").strip()
    host_header = os.getenv("TG_COLLECTOR_MCP_HOST", "").strip()
    try:
        parsed = urlsplit(url)
        local_http = parsed.scheme == "http" and parsed.hostname in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        if (
            not token
            or not parsed.hostname
            or (parsed.scheme != "https" and not local_http)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or any(c in token for c in "\r\n")
        ):
            return None
        _ = parsed.port  # Validate a malformed port without exposing credentials in an error.
        if host_header:
            origin = urlsplit("http://" + host_header)
            if (
                not local_http
                or origin.hostname not in {"127.0.0.1", "localhost", "::1"}
                or origin.port is None
                or origin.username
                or origin.password
                or origin.path
                or origin.query
                or origin.fragment
                or any(c in host_header for c in "\r\n")
            ):
                return None
    except ValueError:
        return None
    return CollectorConfig(url, token, host_header=host_header)


class _Connection:
    """One owning task enters/exits MCP's AnyIO contexts; callers only queue work."""

    def __init__(self, config):
        self.config = config
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue(maxsize=64)
        self.cache = OrderedDict()
        self.inflight = {}
        self.task = self.loop.create_task(self.run())

    async def run(self):
        current = None
        tasks = set()
        slots = asyncio.Semaphore(4)
        cfg = self.config
        try:
            async with httpx.AsyncClient(
                headers={
                    "Authorization": "Bearer " + cfg.token,
                    **({"Host": cfg.host_header} if cfg.host_header else {}),
                },
                timeout=httpx.Timeout(30, connect=5),
                follow_redirects=False,
            ) as http:
                async with streamable_http_client(cfg.url, http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = (
                            None
                            if cfg.group_proxy
                            else {
                                t.name: t
                                for t in (await session.list_tools()).tools
                                if t.name in ALLOWED_TOOLS
                                and t.annotations
                                and t.annotations.readOnlyHint is True
                            }
                        )

                        async def serve(item):
                            name, arguments, future = item
                            try:
                                async with slots:
                                    result = await asyncio.wait_for(self.call(session, tools, name, arguments), 35)
                                if not future.done():
                                    future.set_result(result)
                            except Exception:
                                if not future.done():
                                    future.set_exception(RuntimeError("Collector call failed"))

                        try:
                            while True:
                                try:
                                    current = await asyncio.wait_for(self.queue.get(), 60)
                                except TimeoutError:
                                    if not tasks:
                                        break
                                    continue
                                task = asyncio.create_task(serve(current))
                                tasks.add(task)
                                task.add_done_callback(tasks.discard)
                                current = None
                        finally:
                            for task in tasks:
                                task.cancel()
                            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            logger.warning("Collector connection ended; credentials and payload omitted")
        finally:
            for future in self.inflight.values():
                if not future.done():
                    future.set_exception(RuntimeError("Collector disconnected"))
            self.cache.clear()

    async def call(self, session, tools, tool_name, arguments):
        if self.config.group_proxy:
            if tool_name == 'keenable_search':
                result = await session.call_tool('keenable_search', arguments)
                if result.isError:
                    return {'ok': False, 'error': 'Групповой веб-поиск недоступен.'}
                return result.structuredContent or json.loads(result.content[0].text)
            result = await session.call_tool(
                "collector_tools" if tool_name is None else "collector_call",
                {} if tool_name is None else {"tool_name": tool_name, "arguments": arguments},
            )
            return result.structuredContent or json.loads(result.content[0].text)
        if tool_name is None:
            return {
                "ok": True,
                "tool_count": len(tools),
                "tools": [
                    dict(name=t.name, description=t.description, input_schema=t.inputSchema)
                    for t in tools.values()
                ],
            }
        if tool_name not in tools:
            return {
                "ok": False,
                "error": "Инструмент отсутствует или не подтверждён как read-only.",
            }
        result = await session.call_tool(tool_name, arguments)
        payload = result.model_dump(mode="json", exclude_none=True)
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 2_000_000:
            return {"ok": False, "error": "Ответ слишком большой. Уменьши limit или сузь выборку."}
        return {"ok": not result.isError, "result": payload}

    async def request(self, name, arguments):
        key = json.dumps([name, arguments], sort_keys=True, ensure_ascii=False)
        cached = self.cache.get(key)
        if cached and cached[0] > time.monotonic():
            self.cache.move_to_end(key)
            return copy.deepcopy(cached[1])
        future = self.inflight.get(key)
        if future is None:
            if len(self.inflight) >= 64:
                raise RuntimeError("Collector queue full")
            future = self.loop.create_future()
            self.inflight[key] = future

            # A cancelled waiter must not cancel another caller's identical request.
            def complete(done):
                self.inflight.pop(key, None)
                if not done.cancelled() and done.exception() is None:
                    result = done.result()
                    # The local broker must recheck revocable group capabilities each call.
                    if result.get("ok") and not self.config.group_proxy:
                        ttl = 300 if name is None else 30 if name.startswith("get_") else 5
                        size = len(json.dumps(result, ensure_ascii=False).encode())
                        if size <= 1_000_000:
                            self.cache[key] = (time.monotonic() + ttl, result)
                            while len(self.cache) > 16:
                                self.cache.popitem(last=False)

            future.add_done_callback(complete)
            try:
                self.queue.put_nowait((name, copy.deepcopy(arguments), future))
            except asyncio.QueueFull:
                future.set_exception(RuntimeError("Collector queue full"))
        return copy.deepcopy(await asyncio.shield(future))


_connections = {}


async def _request(
    config: CollectorConfig, tool_name: str | None, arguments: dict[str, Any]
) -> dict[str, Any]:
    if config is None or (tool_name is not None and tool_name not in ALLOWED_TOOLS
                          and not (config.group_proxy and tool_name == 'keenable_search')):
        return {"ok": False, "error": "Collector not configured or tool not allowed."}
    loop = asyncio.get_running_loop()
    connection = _connections.get(config)
    if connection is None or connection.loop is not loop or connection.task.done():
        for key, old in list(_connections.items()):
            if old.task.done():
                _connections.pop(key, None)
        if len(_connections) >= 8:
            raise RuntimeError('Collector connection limit reached')
        connection = _connections[config] = _Connection(config)
    started = time.monotonic()
    try:
        return await connection.request(tool_name, arguments)
    finally:
        logger.info(
            "collector tool=%s duration_ms=%.0f",
            tool_name or "discovery",
            (time.monotonic() - started) * 1000,
        )


async def close_collector():
    connections = list(_connections.values())
    _connections.clear()
    for connection in connections:
        connection.task.cancel()
    await asyncio.gather(*(c.task for c in connections), return_exceptions=True)


def register_collector(mcp: FastMCP, authorized: Callable[[], bool]) -> None:
    # No network at startup. Missing configuration does not affect the existing bridge.
    if collector_config() is None:
        return

    async def invoke(tool_name: str | None, arguments: dict[str, Any]) -> dict[str, Any]:
        if not authorized():
            return {
                "ok": False,
                "error": "Архив доступен владельцу в личке или в разрешённой групповой песочнице.",
            }
        configured = collector_config()
        if configured is None:
            return {"ok": False, "error": "Telegram Collector MCP отключён или не настроен."}
        if tool_name is not None and tool_name not in ALLOWED_TOOLS:
            return {
                "ok": False,
                "error": "Разрешены только известные read-only инструменты коллектора.",
            }
        if len(json.dumps(arguments, ensure_ascii=False).encode()) > 64000:
            return {"ok": False, "error": "Аргументы превышают 64 КБ."}
        try:
            return await asyncio.wait_for(_request(configured, tool_name, arguments), timeout=40)
        except Exception as exc:
            # Exception text may contain URLs, headers or archived content; never log it.
            logger.warning("Optional collector MCP unavailable (%s)", type(exc).__name__)
            return {
                "ok": False,
                "error": "Telegram Collector MCP недоступен. Обычный чат продолжает работать.",
            }

    annotations = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )

    @mcp.tool(annotations=annotations)
    async def collector_tools() -> dict[str, Any]:
        """Read-only инструменты архива. В группе доступны группы/каналы, но не личные переписки."""
        return await invoke(None, {})

    @mcp.tool(annotations=annotations)
    async def collector_call(
        tool_name: str, arguments: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Вызов инструмента Telegram-архива. Сначала узнай схему через collector_tools; ошибки не блокируют обычный чат."""
        return await invoke(tool_name, arguments or {})
