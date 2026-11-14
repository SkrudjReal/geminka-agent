"""Direct transport for the authenticated ``agy`` CLI.

The CLI owns authentication and keeps the conversation alive in its
``stream-json`` mode, so Geminka does not need the Antigravity IDE or its
language server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from collections.abc import AsyncGenerator, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from app.core import config
from app.services.sandbox import SandboxError, agy_sandbox_command

logger = logging.getLogger(__name__)


class AgyCliError(RuntimeError):
    """The direct ``agy`` process could not complete a turn."""

    retryable = True


class AgyNoResponseError(AgyCliError):
    """A completed turn may already have executed tools: never replay it blindly."""

    retryable = False


EFFORTS = ("low", "medium", "high", "xhigh", "max")


def model_family(model: str) -> str:
    return re.sub(r"-(?:low|medium|high|xhigh|max)$", "", model)


def model_effort(model: str) -> str | None:
    suffix = model.rsplit("-", 1)[-1]
    return suffix if suffix in EFFORTS else None


def cli_model_name(model: str, effort: str, catalog: list[str] | None = None) -> str:
    """Map Geminka's provider-prefixed model name to the CLI catalog."""
    name = model.strip().removeprefix("google-antigravity/")
    if catalog is not None:
        variants = [item for item in catalog if model_family(item) == model_family(name)]
        if not variants:
            raise AgyCliError("Модель недоступна в AGY. Обнови список через /model.")
        return next((item for item in variants if model_effort(item) == effort),
                    next((item for item in variants if model_effort(item) == "medium"), variants[0]))
    if name.startswith(("gemini-", "claude-")) and re.search(r"-(?:low|medium|high|xhigh)$", name):
        return re.sub(r"-(?:low|medium|high|xhigh)$", f"-{effort}", name)
    if name.startswith("gemini-") and name.endswith("-flash"):
        return f"{name}-{effort}"
    return name


def format_messages(messages: Iterable[dict[str, str]]) -> str:
    """Serialize the bounded application context into one trusted CLI turn."""
    labels = {"system": "SYSTEM CONTEXT", "user": "USER", "assistant": "ASSISTANT"}
    blocks = []
    for message in messages:
        role = labels.get(message.get("role", "user"), message.get("role", "user").upper())
        blocks.append(f"[{role}]\n{message.get('content', '')}")
    return "\n\n".join(blocks)


@dataclass
class _Session:
    process: asyncio.subprocess.Process
    model: str
    effort: str
    lock: asyncio.Lock
    started: bool = False
    instructions: str = ""
    stderr_task: asyncio.Task[None] | None = None
    tool_context: tuple[tuple[str, str], ...] = ()
    sandbox_enabled: bool = True
    group_context_state: dict | None = None


class AgyCliClient:
    """Small per-user pool of persistent ``agy`` stream-json processes."""

    def __init__(
        self,
        *,
        command: str | None = None,
        project_dir: Path | None = None,
        timeout_seconds: int = 180,
        sandbox_enabled: bool = True,
    ) -> None:
        configured = command or os.getenv("AGY_CLI_PATH", "agy")
        self.command = shutil.which(configured) or configured
        self.project_dir = project_dir or Path(__file__).resolve().parents[2]
        self.timeout_seconds = timeout_seconds
        self._sessions: dict[int, _Session] = {}
        self.sandbox_enabled = sandbox_enabled
        self._one_shots: dict[int, _Session] = {}
        self._lifecycle_lock = asyncio.Lock()
        self.model_labels: dict[str, str] = {}
        self._catalog: list[str] = []

    async def aclose(self) -> None:
        async with self._lifecycle_lock:
            await self._close_all()

    async def _close_all(self) -> None:
        sessions = [*self._sessions.values(), *self._one_shots.values()]
        self._sessions.clear()
        self._one_shots.clear()
        await asyncio.gather(*(self._close_session(session) for session in sessions))

    async def set_sandbox_mode(self, enabled: bool) -> None:
        async with self._lifecycle_lock:
            self.sandbox_enabled = enabled
            await self._close_all()

    def _sandbox_command(self, args: list[str], *, read_only: bool = False, tools_enabled: bool = True,
                         group_id: int | None = None) -> list[str]:
        try:
            if not self.sandbox_enabled and not read_only:
                return agy_sandbox_command([self.command, *args], self.project_dir, restricted=False)
            return agy_sandbox_command(
                [self.command, "--sandbox", *args], self.project_dir, read_only=read_only,
                **({"tools_enabled": False} if not tools_enabled else {}),
                **({"group_id": group_id} if group_id is not None else {}),
            )
        except (SandboxError, OSError) as exc:
            raise AgyCliError(str(exc)) from exc

    def _launch_command(
        self, model: str, effort: str, tool_context: Mapping[str, str] | None = None,
    ) -> list[str]:
        requester = (tool_context or {}).get("requester_id")
        chat = (tool_context or {}).get("chat_id", "")
        group_id = int(chat) if chat.startswith("-") and chat[1:].isdigit() else None
        read_only = group_id is not None or (requester is not None and requester != str(config.settings.owner_user_id))
        return self._sandbox_command([
            "--print=", "--input-format", "stream-json", "--output-format", "stream-json",
            "--model", model, "--effort", effort, "--mode", "plan" if read_only else "accept-edits",
            "--add-dir", str(self.project_dir), "--disable-slash-commands",
            "--print-timeout", f"{self.timeout_seconds}s",
        ], read_only=read_only, tools_enabled=requester != "summary", group_id=group_id)

    def reset_user(self, user_id: int) -> None:
        session = self._sessions.pop(user_id, None)
        if session and session.process.returncode is None:
            session.process.terminate()

    async def check_health(self) -> bool:
        """Use the CLI's authenticated model listing as a lightweight health check."""
        if shutil.which(self.command) is None and not Path(self.command).exists():
            return False
        try:
            process = await asyncio.create_subprocess_exec(
                *self._sandbox_command(["models"]),
                cwd=self.project_dir,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except (OSError, asyncio.TimeoutError, AgyCliError):
            return False
        return process.returncode == 0 and b"gemini-" in stdout

    async def list_models(self) -> list[str]:
        self.model_labels = {}
        self._catalog = []
        try:
            process = await asyncio.create_subprocess_exec(
                *self._sandbox_command(["models"]),
                cwd=self.project_dir,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except (OSError, asyncio.TimeoutError, AgyCliError):
            return []

        if process.returncode != 0:
            return []
        result: list[str] = []
        for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
            name, _, label = raw_line.strip().partition("\t")
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._/-]*", name):
                continue
            if name not in result:
                result.append(name)
                self.model_labels[name] = label.strip() or name
        self._catalog = result
        return result

    async def stream(
        self,
        user_id: int,
        *,
        model: str,
        effort: str,
        messages: list[dict[str, str]],
        debug: bool = False,
        turn_context: str = "",
        tool_context: Mapping[str, str] | None = None,
        group_input: dict | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield response deltas from one direct CLI turn."""
        if not self._catalog:
            await self.list_models()
        cli_model = cli_model_name(model, effort, self._catalog)
        effort = model_effort(cli_model) or effort
        current = dict(messages[-1])
        if turn_context:
            current["content"] = (
                "[CURRENT TURN CONTEXT — replaces previous turn context]\n"
                + turn_context + "\n[CURRENT USER MESSAGE]\n" + current["content"]
            )
        full_messages = [*messages[:-1], current]
        tool_context = tool_context or {}
        async def with_group_context(state):
            from app.services.group_context import build_context

            extra = await build_context(
                group_input["chat_id"], group_input.get("thread_id"),
                group_input["message_id"], int(tool_context["requester_id"]),
                group_input.get("date"), state=state,
            )
            from app.services.group_memory import memory_delta

            extra += memory_delta(group_input.get("local_memory", {}), state)
            return {**current, "content": extra + "\n" + current["content"]}

        if debug:
            if group_input:
                full_messages = [*messages[:-1], await with_group_context({})]
            async for token in self._stream_once(
                cli_model, effort, format_messages(full_messages), tool_context
            ):
                yield token
            return

        session = await self._get_session(user_id, cli_model, effort, tool_context)
        async with session.lock:
            next_group_state = dict(session.group_context_state or {})
            if group_input:
                current = await with_group_context(next_group_state)
                full_messages = [*messages[:-1], current]
            instructions = messages[0]["content"]
            if not session.started:
                outgoing = full_messages
            elif session.instructions != instructions:
                outgoing = [
                    {"role": "system", "content": "[UPDATED INSTRUCTIONS — replace previous instructions]\n" + instructions},
                    current,
                ]
            else:
                outgoing = [current]
            prompt = format_messages(outgoing)
            turn_started = time.monotonic()
            first_token_at = None
            logger.info("agy turn: bootstrap=%s chars=%d", not session.started, len(prompt))
            try:
                async for token in self._send(session, prompt):
                    if first_token_at is None:
                        first_token_at = time.monotonic()
                        logger.info("agy first_token_ms=%.0f bootstrap=%s", (first_token_at - turn_started) * 1000, not session.started)
                    yield token
                session.started = True
                session.instructions = instructions
                session.group_context_state = next_group_state
                logger.info("agy completed_ms=%.0f", (time.monotonic() - turn_started) * 1000)
            except asyncio.CancelledError:
                if self._sessions.get(user_id) is session:
                    self._sessions.pop(user_id, None)
                await self._close_session(session)
                raise
            except (AgyCliError, asyncio.TimeoutError):
                if self._sessions.get(user_id) is session:
                    self._sessions.pop(user_id, None)
                await self._close_session(session)
                raise

    async def _get_session(
        self,
        user_id: int,
        model: str,
        effort: str,
        tool_context: Mapping[str, str] | None = None,
    ) -> _Session:
        async with self._lifecycle_lock:
            return await self._get_session_locked(user_id, model, effort, tool_context)

    async def _get_session_locked(
        self, user_id: int, model: str, effort: str,
        tool_context: Mapping[str, str] | None = None,
    ) -> _Session:
        context_signature = self._tool_context_signature(tool_context)
        current = self._sessions.get(user_id)
        if (
            current
            and current.process.returncode is None
            and (current.model, current.effort) == (model, effort)
            and current.tool_context == context_signature
            and current.sandbox_enabled == self.sandbox_enabled
        ):
            return current
        if current:
            await self._close_session(current)

        process = await asyncio.create_subprocess_exec(
            *self._launch_command(model, effort, tool_context),
            cwd=self.project_dir,
            env=self._tool_env(tool_context),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        session = _Session(
            process, model, effort, asyncio.Lock(), tool_context=context_signature,
            sandbox_enabled=self.sandbox_enabled,
        )
        session.stderr_task = asyncio.create_task(self._drain_stderr(session))
        self._sessions[user_id] = session
        return session

    async def _stream_once(
        self,
        model: str,
        effort: str,
        prompt: str,
        tool_context: Mapping[str, str] | None = None,
    ) -> AsyncGenerator[str, None]:
        async with self._lifecycle_lock:
            process = await asyncio.create_subprocess_exec(
                *self._launch_command(model, effort, tool_context),
                cwd=self.project_dir,
                env=self._tool_env(tool_context),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            session = _Session(
                process, model, effort, asyncio.Lock(),
                tool_context=self._tool_context_signature(tool_context),
                sandbox_enabled=self.sandbox_enabled,
            )
            session.stderr_task = asyncio.create_task(self._drain_stderr(session))
            self._one_shots[id(session)] = session
        try:
            if process.stdin is None:
                raise AgyCliError("agy CLI не открыл stdin")
            payload = {
                "event": "user",
                "message": {"role": "user", "content": prompt},
            }
            process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
            await process.stdin.drain()
            process.stdin.close()
            async for token in self._send(session, prompt=None):
                yield token
            await asyncio.wait_for(process.wait(), timeout=10)
        finally:
            self._one_shots.pop(id(session), None)
            await self._close_session(session)

    @staticmethod
    def _tool_context_signature(
        tool_context: Mapping[str, str] | None,
    ) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                (str(key), str(value))
                for key, value in (tool_context or {}).items()
                if key in {"requester_id", "chat_id", "group_broker_url"}
            )
        )

    @classmethod
    def _tool_env(cls, tool_context: Mapping[str, str] | None) -> dict[str, str]:
        env = os.environ.copy()
        chat = (tool_context or {}).get("chat_id", "")
        if chat.startswith("-") and chat[1:].isdigit():
            safe = {"PATH", "LANG", "LC_ALL", "TZ", "TERM", "SSL_CERT_FILE", "SSL_CERT_DIR"}
            env = {key: value for key, value in env.items() if key in safe}
            env["GEMINKA_GROUP_MODE"] = "1"
            if config.settings.owner_user_id is not None:
                env["TELEGRAM_OWNER_ID"] = str(config.settings.owner_user_id)
            for key in ("group_broker_url", "group_broker_token"):
                if value := (tool_context or {}).get(key):
                    env[f"GEMINKA_{key.upper()}"] = value
        for key, value in cls._tool_context_signature(tool_context):
            env[f"GEMINKA_{key.upper()}"] = value
        return env

    async def _send(self, session: _Session, prompt: str | None) -> AsyncGenerator[str, None]:
        process = session.process
        if process.stdin is None or process.stdout is None:
            raise AgyCliError("agy CLI не открыл stream-json канал")
        if prompt is not None:
            payload = {
                "event": "user",
                "message": {"role": "user", "content": prompt},
            }
            process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode())
            await process.stdin.drain()

        received_text = False
        while True:
            raw_line = await asyncio.wait_for(process.stdout.readline(), timeout=self.timeout_seconds + 10)
            if not raw_line:
                raise AgyCliError("agy CLI завершился до ответа")
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except json.JSONDecodeError:
                logger.debug("Ignored malformed agy event: %s", raw_line[:200])
                continue

            if event.get("event") == "step_update":
                update = event.get("step_update", {})
                if update.get("step_type") == "agent_response":
                    delta = update.get("text_delta")
                    if isinstance(delta, str) and delta:
                        received_text = True
                        yield delta
                continue
            if event.get("event") != "result":
                continue

            result = event.get("result", {})
            if str(result.get("status", "")).casefold() != "success":
                error = self._error_from_text(json.dumps(result, ensure_ascii=False))
                # Provider errors may contain an entire private transcript in response.
                if "response" in result:
                    if not str(error).startswith("Модель временно недоступна:"):
                        error.args = ("AGY завершил генерацию со статусом ERROR; текст ответа скрыт из логов.",)
                logger.warning("agy failed_result streamed=%s category=%s", received_text,
                               "capacity" if str(error).startswith("Модель временно недоступна:") else "unknown")
                raise error
            # Some CLI turns return only the final response, without text deltas.
            # Never replay the full response after an already streamed answer.
            response = result.get("response")
            if not received_text and isinstance(response, str) and response:
                yield response
            elif not received_text:
                raise AgyNoResponseError("AGY завершил запрос без текста; автоматический повтор небезопасен.")
            return

    async def _drain_stderr(self, session: _Session) -> None:
        if session.process.stderr is None:
            return
        while True:
            line = await session.process.stderr.readline()
            if not line:
                return
            logger.debug("agy: %s", line.decode("utf-8", errors="replace").rstrip())

    async def _close_session(self, session: _Session) -> None:
        if session.process.returncode is None:
            try:
                session.process.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(session.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                try:
                    session.process.kill()
                except ProcessLookupError:
                    pass
                await session.process.wait()
        if session.stderr_task:
            session.stderr_task.cancel()

    @staticmethod
    def _error_from_text(text: str) -> AgyCliError:
        lowered = text.casefold().replace("\\", "")
        if ("model_capacity_exhausted" in lowered or "no capacity available" in lowered
                or re.search(r'\bcode[\s"\x27:=(]*503\b', lowered)
                or "the service is currently unavailable" in lowered):
            return AgyCliError("Модель временно недоступна: нет свободной capacity.")
        return AgyCliError(text.strip()[:500] or "agy CLI завершился с ошибкой")
