"""Multiplex independent ACP conversations within each filesystem trust boundary."""

import asyncio
import json
import shutil
import time
from types import SimpleNamespace

from app.core import config
from app.services.agy_cli import AgyCliClient, AgyCliError, AgyNoResponseError, _Session


class _Peer:
    def __init__(self, process, timeout):
        self.process = process
        self.timeout = timeout
        self.pending = {}
        self.streams = {}
        self.sequence = 0
        self.session_count = 0
        self.last_used = time.monotonic()
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._discard_stderr())

    async def _write(self, message):
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()

    async def call(self, method, params):
        if self.reader.done():
            raise AgyNoResponseError("Соединение AGY ACP закрыто.")
        self.sequence += 1
        key = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            await self._write(dict(jsonrpc="2.0", id=key, method=method, params=params))
            return await asyncio.wait_for(future, self.timeout)
        except (OSError, asyncio.TimeoutError) as exc:
            raise AgyNoResponseError("Запрос AGY ACP прерван; автоматический повтор отключён.") from exc
        finally:
            self.pending.pop(key, None)

    async def notify(self, method, params):
        await self._write(dict(jsonrpc="2.0", method=method, params=params))

    async def _discard_stderr(self):
        # Provider stderr may contain prompts or credentials; never forward it to logs.
        while await self.process.stderr.readline():
            pass

    async def _read(self):
        try:
            while raw := await self.process.stdout.readline():
                message = json.loads(raw)
                if "method" not in message:
                    future = self.pending.get(message.get("id"))
                    if future and not future.done():
                        if "error" in message:
                            # Never replay an RPC that may already have executed a tool.
                            future.set_exception(AgyNoResponseError("AGY ACP отклонил запрос; детали скрыты."))
                        else:
                            future.set_result(message.get("result", {}))
                elif message["method"] == "session/update":
                    params = message.get("params", {})
                    queue = self.streams.get(params.get("sessionId"))
                    update = params.get("update", {})
                    content = update.get("content", {})
                    if queue is not None and update.get("sessionUpdate") == "agent_message_chunk" and content.get("type") == "text":
                        queue.put_nowait(content.get("text", ""))
                elif "id" in message:
                    if message["method"] == "session/request_permission":
                        options = message.get("params", {}).get("options", [])
                        allow = next((o for o in options if o.get("kind") == "allow_once"), None)
                        result = {"outcome": {"outcome": "selected", "optionId": allow["optionId"]}} if allow else {"outcome": {"outcome": "cancelled"}}
                        await self._write(dict(jsonrpc="2.0", id=message["id"], result=result))
                    else:
                        # Client-side filesystem/terminal requests must not escape bubblewrap.
                        await self._write(dict(jsonrpc="2.0", id=message["id"], error={"code": -32601, "message": "Unsupported client capability"}))
        except (OSError, ValueError):
            pass
        finally:
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(AgyNoResponseError("Соединение AGY ACP прервано; запрос не повторён."))

    async def close(self):
        await AgyCliClient._close_session(None, SimpleNamespace(process=self.process, stderr_task=self.stderr))
        self.reader.cancel()
        await asyncio.gather(self.reader, self.stderr, return_exceptions=True)


class AgyAcpClient(AgyCliClient):
    """Reuse the existing bounded session leases, bootstrap and context cursors."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._peers = {}
        self._cleanup = set()

    def _route(self, context):
        context = context or {}
        chat = context.get("chat_id", "")
        group = int(chat) if chat.startswith("-") and chat[1:].isdigit() else None
        requester = context.get("requester_id")
        read_only = group is not None or (requester is not None and requester != str(config.settings.owner_user_id))
        return group, read_only, requester != "summary", self.sandbox_enabled

    async def _peer(self, context):
        key = self._route(context)
        used = {id(s.peer) for s in [*self._sessions.values(), *self._one_shots.values()] if hasattr(s, "peer")}
        for old_key, old_peer in list(self._peers.items()):
            if old_key != key and id(old_peer) not in used:
                self._peers.pop(old_key, None)
                await old_peer.close()
        peer = self._peers.get(key)
        if peer and peer.process.returncode is None and not peer.reader.done():
            return peer
        if peer:
            await peer.close()
        group, read_only, _, _ = key
        # Tools are attached per session, never inherited from a shared process.
        command = self._sandbox_command(
            ["acp", "--stdio", "--include-default-customizations=false"],
            read_only=read_only, tools_enabled=False, group_id=group,
        )
        env = self._tool_env(context)
        for name in ("GEMINKA_REQUESTER_ID", "GEMINKA_CHAT_ID", "GEMINKA_GROUP_BROKER_URL", "GEMINKA_GROUP_BROKER_TOKEN"):
            env.pop(name, None)
        process = await asyncio.create_subprocess_exec(
            *command, cwd=self.project_dir, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=4 * 1024 * 1024,
        )
        peer = _Peer(process, self.timeout_seconds)
        try:
            await peer.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        except BaseException:
            await peer.close()
            raise
        self._peers[key] = peer
        return peer

    def _servers(self, context):
        if (context or {}).get("requester_id") == "summary":
            return []
        _, read_only, _, _ = self._route(context)
        env = self._tool_env(context)
        values = {k: v for k, v in env.items() if k.startswith("GEMINKA_") or k == "TELEGRAM_OWNER_ID"}
        values.update(GEMINKA_MCP_PROCESS="1", GEMINKA_MEMORY_READ_ONLY="1" if read_only else "0")
        return [{
            "name": "geminka-bot-api", "command": shutil.which("uv") or "uv",
            "args": ["--directory", str(self.project_dir), "run", "--no-sync", "python", "-m", "app.services.bot_api_mcp"],
            "env": [{"name": k, "value": v} for k, v in values.items()],
        }]

    async def _new_session(self, model, effort, context, saved=None):
        peer = await self._peer(context)
        params = {"cwd": str(self.project_dir), "mcpServers": self._servers(context)}
        if saved:
            params["sessionId"] = saved["conversation_id"]
        result = await peer.call("session/load" if saved else "session/new", params)
        peer.session_count += 1
        session_id = result.get("sessionId")
        if not session_id:
            raise AgyCliError("AGY ACP не вернул sessionId")
        session = _Session(peer.process, model, effort, asyncio.Lock(),
                           tool_context=self._tool_context_signature(context),
                           sandbox_enabled=self.sandbox_enabled, conversation_id=session_id)
        session.peer = peer
        try:
            await peer.call("session/set_model", {"sessionId": session_id, "modelId": model})
            await peer.call("session/set_mode", {"sessionId": session_id, "modeId": "plan" if self._route(context)[1] else "accept-edits"})
        except BaseException:
            await self._close_session(session)
            raise
        if saved:
            session.started = True
            session.instructions = saved["instructions"]
            session.group_context_state = saved["group_context_state"]
        return session

    async def _prepare_session(self, user_id, model, effort, tool_context):
        peer = self._peers.get(self._route(tool_context))
        if not peer or peer.session_count < self.max_sessions:
            return True
        current = self._sessions.get(user_id)
        if (current and current.peer is peer and not getattr(current, "closed", False)
                and peer.process.returncode is None and not peer.reader.done()
                and (current.model, current.effort, current.tool_context, current.sandbox_enabled) == (
                    model, effort, self._tool_context_signature(tool_context), self.sandbox_enabled)):
            return True
        sessions = [(uid, s) for uid, s in self._sessions.items() if s.peer is peer]
        if (any(s.borrowers or s.lock.locked() for _, s in sessions)
                or any(s.peer is peer for s in self._one_shots.values())):
            return False
        # ACP 1.5.8 keeps MCP children after session/close. Bound its lifetime cache too.
        for uid, session in sessions:
            await self._park(uid, session)
        self._peers.pop(self._route(tool_context), None)
        await peer.close()
        return True

    async def _get_session_locked(self, user_id, model, effort, tool_context=None):
        signature = self._tool_context_signature(tool_context)
        route = (model, effort, signature, self.sandbox_enabled)
        current = self._sessions.get(user_id)
        if current:
            if current.process.returncode is None and not current.peer.reader.done() and (current.model, current.effort, current.tool_context, current.sandbox_enabled) == route:
                return current
            await self._close_session(current)
        saved = self._parked.get(user_id)
        if saved and (saved["model"], saved["effort"], saved["tool_context"], saved["sandbox_enabled"]) != route:
            saved = None
        session = await self._new_session(model, effort, tool_context, saved)
        self._parked.pop(user_id, None)
        self._sessions[user_id] = session
        return session

    async def _send(self, session, prompt):
        peer, session_id = session.peer, session.conversation_id
        queue = asyncio.Queue()
        peer.streams[session_id] = queue
        task = asyncio.create_task(peer.call("session/prompt", {
            "sessionId": session_id, "prompt": [{"type": "text", "text": prompt}],
        }))
        task.add_done_callback(lambda _: queue.put_nowait(None))
        received = False
        try:
            while True:
                token = await queue.get()
                if token is None:
                    break
                if token:
                    received = True
                    yield token
            result = await task
            if result.get("stopReason") != "end_turn" or not received:
                raise AgyNoResponseError("AGY ACP не завершил текстовый ответ; автоматический повтор отключён.")
        finally:
            peer.streams.pop(session_id, None)
            if not task.done():
                try:
                    await peer.notify("session/cancel", {"sessionId": session_id})
                    await asyncio.wait_for(asyncio.shield(task), 5)
                except (Exception, asyncio.CancelledError):
                    await peer.close()
            await asyncio.gather(task, return_exceptions=True)

    async def _stream_once_unlimited(self, model, effort, prompt, tool_context=None):
        async with self._available:
            if self._closed:
                raise AgyNoResponseError("AGY client is closed")
            while not await self._prepare_session(None, model, effort, tool_context):
                await self._available.wait()
                if self._closed:
                    raise AgyNoResponseError("AGY client is closed")
            session = await self._new_session(model, effort, tool_context)
            self._one_shots[id(session)] = session
        try:
            async for token in self._send(session, prompt):
                yield token
        finally:
            async with self._lifecycle_lock:
                self._one_shots.pop(id(session), None)
                await self._close_session(session)
                await self._prune_peers(all_idle=True)
                self._available.notify_all()

    async def _close_session(self, session):
        if not hasattr(session, "peer"):
            return await super()._close_session(session)
        if getattr(session, "closed", False):
            return
        session.closed = True
        peer = session.peer
        peer.last_used = time.monotonic()
        if peer.process.returncode is None and not peer.reader.done():
            try:
                await asyncio.wait_for(peer.call("session/close", {"sessionId": session.conversation_id}), 5)
            except (AgyCliError, OSError, asyncio.TimeoutError):
                await peer.close()

    async def _prune_peers(self, *, all_idle=False):
        used = {id(s.peer) for s in [*self._sessions.values(), *self._one_shots.values()] if hasattr(s, "peer")}
        for key, peer in list(self._peers.items()):
            if id(peer) not in used and (all_idle or time.monotonic() - peer.last_used >= self.idle_seconds):
                self._peers.pop(key, None)
                await peer.close()

    async def _expire_idle(self):
        await super()._expire_idle()
        await self._prune_peers(all_idle=True)

    async def _close_all(self):
        await super()._close_all()
        await self._prune_peers(all_idle=True)

    def reset_user(self, user_id):
        self._parked.pop(user_id, None)
        session = self._sessions.pop(user_id, None)
        if session:
            task = asyncio.create_task(self._close_session(session))
            self._cleanup.add(task)
            task.add_done_callback(self._cleanup.discard)

    async def aclose(self):
        await super().aclose()
        if self._cleanup:
            await asyncio.gather(*self._cleanup, return_exceptions=True)
