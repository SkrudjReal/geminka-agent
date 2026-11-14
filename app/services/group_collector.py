"""Trusted localhost broker: group runtimes never receive the full archive token."""

import asyncio
import hmac
import json
import secrets
import socket
from contextlib import contextmanager
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from app.services.collector_mcp import ALLOWED_TOOLS, _request, collector_config
from app.services.topics import topic_manager

GROUP_TOOLS = ALLOWED_TOOLS - {"list_users", "get_user_history", "archive_status"}
DIRECT_CHAT_TOOLS = {"search", "search_messages", "semantic_search", "list_chats",
                     "get_message", "get_message_history", "get_message_metrics"}


class _BrokerServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self):
        # The Telegram dispatcher, not this optional background server, owns signals.
        yield


def scoped_arguments(chat_id: int, tool: str, arguments: dict[str, Any], *, public_archive=False) -> dict[str, Any]:
    if tool not in GROUP_TOOLS:
        raise ValueError("Этот инструмент не доступен в групповой песочнице.")
    result = dict(arguments)
    target = result if tool in DIRECT_CHAT_TOOLS else dict(result.get("scope") or {})
    if public_archive:
        selected = target.get("chat_id")
        if tool in {"search", "search_messages", "semantic_search"}:
            if selected is not None and (type(selected) is not int or selected >= 0):
                raise ValueError("Разрешены только группы и каналы; личные чаты запрещены.")
            result["public_only"] = True
            return result
        if selected is None and tool == "list_chats":
            return result
        selected = chat_id if selected is None else selected
        if type(selected) is not int or selected >= 0:
            raise ValueError("Разрешены только группы и каналы; личные чаты запрещены.")
        target["chat_id"] = selected
        if target is not result:
            result["scope"] = target
        return result
    if target.get("chat_id") not in {None, chat_id}:
        raise ValueError("В группе разрешён поиск только по текущему chat_id.")
    target["chat_id"] = chat_id
    if target is not result:
        result["scope"] = target
    return result


def _redact_other_chats(value: Any, chat_id: int | None) -> Any:
    if isinstance(value, dict):
        candidate = value.get("chat_id")
        if value.get("type") in {"private", "bot"}:
            return None
        if candidate is not None and (
            (chat_id is not None and candidate != chat_id)
            or (chat_id is None and (type(candidate) is not int or candidate >= 0))
        ):
            return None
        return {key: (None if key in {"reply_to_chat_id", "before_chat_id"} and type(item) is int and item >= 0
                      else _redact_other_chats(item, chat_id)) for key, item in value.items()}
    if isinstance(value, list):
        return [clean for item in value if (clean := _redact_other_chats(item, chat_id)) is not None]
    return value


def scoped_result(result: dict, chat_id: int | None) -> dict:
    result = _redact_other_chats(result, chat_id)
    payload = result.get("result", {})
    # MCP may carry the same JSON as structuredContent and text content.
    for block in payload.get("content", []):
        if block.get("type") == "text":
            try:
                block["text"] = json.dumps(_redact_other_chats(json.loads(block["text"]), chat_id), ensure_ascii=False)
            except (ValueError, TypeError):
                block["text"] = "Неструктурированный ответ архива скрыт в групповом режиме."
    return result


def chat_page(result: dict, arguments: dict) -> dict:
    """FastMCP lists arrive as one JSON text block per row, not structuredContent."""
    if not result.get("ok"):
        return result
    payload = result.get("result", {})
    rows = []
    for block in payload.get("content", []):
        if block.get("type") == "text":
            try:
                value = json.loads(block["text"])
                rows.extend(value if isinstance(value, list) else [value])
            except (ValueError, TypeError):
                continue
    if not rows and payload.get("structuredContent") is not None:
        value = payload["structuredContent"]
        rows = value if isinstance(value, list) else value.get("result", value.get("chats", []))
    valid = [row for row in rows if isinstance(row, dict) and type(row.get("chat_id")) is int]
    chats = [{key: row.get(key) for key in ("chat_id", "title", "username", "type")}
             for row in valid if row["chat_id"] < 0 and row.get("type") not in {"private", "bot"}]
    cursor = max((row["chat_id"] for row in valid), default=None)
    more = len(valid) >= arguments.get("limit", 50) and cursor is not None and "chat_id" not in arguments
    return {"ok": True, "result": {"structuredContent": {
        "chats": chats, "count": len(chats), "has_more": more,
        "next_after_chat_id": cursor if more else None,
        "note": "Это сохранённые чаты архива, не подтверждение активного парсинга. "
                "Для обычного вопроса покажи эту страницу и наличие продолжения. "
                "Не обходи весь архив и не запрашивай статистику каждого чата без явной просьбы. "
                "Следующая страница: list_chats(after_chat_id=next_after_chat_id).",
    }}}


def search_page(result: dict) -> dict:
    """Keep full message texts without duplicate JSON or unrelated SQL fields."""
    if not result.get("ok"):
        return result
    payload = result.get("result", {})
    data = payload.get("structuredContent")
    if not isinstance(data, dict):
        for block in payload.get("content", []):
            if block.get("type") == "text":
                try:
                    data = json.loads(block["text"])
                except (ValueError, TypeError):
                    continue
                if isinstance(data, dict) and "messages" in data:
                    break
    if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
        return result
    messages = []
    for row in data["messages"]:
        if (not isinstance(row, dict) or type(row.get("chat_id")) is not int
                or row["chat_id"] >= 0 or row.get("type") in {"private", "bot"}):
            continue
        item = {key: row[key] for key in (
            "archive_id", "chat_id", "message_id", "sent_at", "sender_user_id",
            "chat_title", "chat_username", "url", "matched_by", "similarity", "hybrid_score",
        ) if key in row}
        item["text"] = row.get("text") or ""
        messages.append(item)
    compact = {key: data[key] for key in (
        "mode", "ranking", "candidates", "truncated", "semantic_coverage", "warnings", "next_cursor",
    ) if key in data}
    compact.update(messages=messages, returned_count=len(messages),
                   omitted_count=len(data["messages"]) - len(messages))
    return {"ok": True, "result": compact}


class GroupCollectorBroker:
    def __init__(self, group_memory=None) -> None:
        self.group_memory = group_memory
        self.tokens: dict[int, str] = {}
        self.server = None
        self.task = None
        self.address = ""
        self.lock = asyncio.Lock()
        self.mcp = FastMCP("geminka-group-archive", stateless_http=True, json_response=True)
        annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
        self.mcp.tool(annotations=annotations)(self.collector_tools)
        self.mcp.tool(annotations=annotations)(self.collector_call)

    def _chat(self) -> int:
        request = self.mcp.get_context().request_context.request
        auth = request.headers.get("authorization", "") if request else ""
        for chat_id, token in self.tokens.items():
            if hmac.compare_digest(auth, "Bearer " + token) and topic_manager.is_group_configured(chat_id):
                return chat_id
        raise ValueError("Групповой доступ к архиву не разрешён.")

    async def collector_tools(self) -> dict:
        """Read-only archive tools for groups/channels; private chats are inaccessible."""
        try:
            self._chat()
            cfg = collector_config()
            try:
                data = await _request(cfg, None, {}) if cfg else {"ok": True, "tools": []}
            except Exception:
                data = {"ok": True, "tools": [], "archive_unavailable": True}
            if not data.get("ok") and self.group_memory is not None:
                data = {"ok": True, "tools": [], "archive_unavailable": True}
            data["tools"] = [tool for tool in data.get("tools", []) if tool["name"] in GROUP_TOOLS]
            if self.group_memory is not None:
                data["tools"].append({"name": "group_memory_search",
                    "description": "SQLite FTS5/BM25 и саммари участника только текущей группы, без личной памяти.",
                    "input_schema": {"type": "object", "properties": {
                        "user_id": {"type": "integer", "minimum": 1},
                        "query": {"type": "string", "maxLength": 1000},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 10}},
                        "required": ["user_id"], "additionalProperties": False}})
            data["tool_count"] = len(data["tools"])
            data["scope_policy"] = "search/semantic_search/search_messages без chat_id ищут сразу по всем группам и каналам; public_only принудителен. Не перебирай list_chats для общего поиска. Для конкретного чата передай chat_id < 0; аналитика без scope.chat_id — текущая группа. Лички запрещены."
            return data
        except Exception:
            return {"ok": False, "error": "Групповой архив недоступен."}

    async def collector_call(self, tool_name: str, arguments: dict | None = None) -> dict:
        """Query any archived group/channel; private chat IDs and reply contents are blocked."""
        try:
            chat_id = self._chat()
            if tool_name == "group_memory_search":
                args = arguments or {}
                uid, limit = args.get("user_id"), args.get("limit", 5)
                query = args.get("query", "")
                if (self.group_memory is None or type(uid) is not int or uid <= 0
                        or type(limit) is not int or not 1 <= limit <= 10
                        or not isinstance(query, str) or len(query) > 1000
                        or set(args) - {"user_id", "query", "limit"}):
                    raise ValueError("Недопустимая область групповой памяти.")
                data = await asyncio.to_thread(self.group_memory.recall, chat_id, uid, query, limit)
                return {"ok": True, "result": {"structuredContent": data}}
            arguments = scoped_arguments(chat_id, tool_name, arguments or {}, public_archive=True)
            if len(json.dumps(arguments).encode()) > 64000:
                raise ValueError("Слишком большие аргументы.")
            cfg = collector_config()
            if cfg is None:
                return {"ok": False, "error": "Архив не настроен."}
            result = await _request(cfg, tool_name, arguments)
            if tool_name == "list_chats":
                result = chat_page(result, arguments)
            elif tool_name in {"search", "search_messages", "semantic_search"}:
                result = search_page(result)
            return scoped_result(result, None)
        except Exception:
            return {"ok": False, "error": "Запрос недоступен. Разрешены только группы и каналы, не личные переписки."}

    async def credentials(self, chat_id: int) -> dict[str, str]:
        if ((collector_config() is None and self.group_memory is None)
                or not topic_manager.is_group_configured(chat_id)):
            return {}
        async with self.lock:
            if self.task is not None and self.task.done():
                self.task = None
            if self.task is None:
                sock = socket.socket()
                sock.bind(("127.0.0.1", 0))
                sock.listen()
                self.address = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
                self.server = _BrokerServer(uvicorn.Config(
                    self.mcp.streamable_http_app(), log_level="error", access_log=False,
                ))
                self.task = asyncio.create_task(self.server.serve(sockets=[sock]))
                for _ in range(100):
                    if self.server.started:
                        break
                    if self.task.done():
                        await self.task
                        raise RuntimeError("Не удалось запустить групповой MCP.")
                    await asyncio.sleep(0.02)
                else:
                    await self.close()
                    raise RuntimeError("Групповой MCP не запустился вовремя.")
            token = self.tokens.setdefault(chat_id, secrets.token_urlsafe(32))
            return {"group_broker_url": self.address, "group_broker_token": token}

    async def close(self) -> None:
        if self.task:
            self.server.should_exit = True
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except asyncio.TimeoutError:
                self.task.cancel()
            self.task = None
