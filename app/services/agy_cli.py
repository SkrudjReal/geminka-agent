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
import shutil
from collections.abc import AsyncGenerator, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class AgyCliError(RuntimeError):
    """The direct ``agy`` process could not complete a turn."""


def cli_model_name(model: str, effort: str) -> str:
    """Map Geminka's provider-prefixed model name to the CLI catalog."""
    name = model.strip().removeprefix("google-antigravity/")
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


class AgyCliClient:
    """Small per-user pool of persistent ``agy`` stream-json processes."""

    def __init__(
        self,
        *,
        command: str | None = None,
        project_dir: Path | None = None,
        timeout_seconds: int = 180,
    ) -> None:
        configured = command or os.getenv("AGY_CLI_PATH", "agy")
        self.command = shutil.which(configured) or configured
        self.project_dir = project_dir or Path(__file__).resolve().parents[2]
        self.timeout_seconds = timeout_seconds
        self._sessions: dict[int, _Session] = {}

    async def aclose(self) -> None:
        sessions = list(self._sessions.values())
        self._sessions.clear()
        await asyncio.gather(*(self._close_session(session) for session in sessions))

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
                self.command,
                "models",
                cwd=self.project_dir,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except (OSError, asyncio.TimeoutError):
            return False
        return process.returncode == 0 and b"gemini-" in stdout

    async def list_models(self) -> list[str]:
        try:
            process = await asyncio.create_subprocess_exec(
                self.command,
                "models",
                cwd=self.project_dir,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=20)
        except (OSError, asyncio.TimeoutError):
            return []

        result: list[str] = []
        for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
            name = raw_line.strip()
            if not name or name.startswith("Available"):
                continue
            if name.startswith("gemini-") and name.rsplit("-", 1)[-1] in {"low", "medium", "high"}:
                name = name.rsplit("-", 1)[0]
            if name not in result:
                result.append(name)
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
    ) -> AsyncGenerator[str, None]:
        """Yield response deltas from one direct CLI turn."""
        cli_model = cli_model_name(model, effort)
        current = dict(messages[-1])
        if turn_context:
            current["content"] = (
                "[CURRENT TURN CONTEXT — replaces previous turn context]\n"
                + turn_context + "\n[CURRENT USER MESSAGE]\n" + current["content"]
            )
        full_messages = [*messages[:-1], current]
        tool_context = tool_context or {}
        if debug:
            async for token in self._stream_once(
                cli_model, effort, format_messages(full_messages), tool_context
            ):
                yield token
            return

        session = await self._get_session(user_id, cli_model, effort, tool_context)
        async with session.lock:
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
            logger.info("agy turn: bootstrap=%s chars=%d", not session.started, len(prompt))
            try:
                async for token in self._send(session, prompt):
                    yield token
                session.started = True
                session.instructions = instructions
            except (AgyCliError, asyncio.TimeoutError):
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
        context_signature = self._tool_context_signature(tool_context)
        current = self._sessions.get(user_id)
        if (
            current
            and current.process.returncode is None
            and (current.model, current.effort) == (model, effort)
            and current.tool_context == context_signature
        ):
            return current
        if current:
            await self._close_session(current)

        process = await asyncio.create_subprocess_exec(
            self.command,
            "--print=",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--model",
            model,
            "--effort",
            effort,
            "--add-dir",
            str(self.project_dir),
            "--disable-slash-commands",
            "--print-timeout",
            f"{self.timeout_seconds}s",
            cwd=self.project_dir,
            env=self._tool_env(tool_context),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        session = _Session(
            process, model, effort, asyncio.Lock(), tool_context=context_signature
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
        process = await asyncio.create_subprocess_exec(
            self.command,
            "--print=",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
            "--model",
            model,
            "--effort",
            effort,
            "--add-dir",
            str(self.project_dir),
            "--disable-slash-commands",
            "--print-timeout",
            f"{self.timeout_seconds}s",
            cwd=self.project_dir,
            env=self._tool_env(tool_context),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        session = _Session(
            process,
            model,
            effort,
            asyncio.Lock(),
            tool_context=self._tool_context_signature(tool_context),
        )
        session.stderr_task = asyncio.create_task(self._drain_stderr(session))
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
            await self._close_session(session)

    @staticmethod
    def _tool_context_signature(
        tool_context: Mapping[str, str] | None,
    ) -> tuple[tuple[str, str], ...]:
        return tuple(
            sorted(
                (str(key), str(value))
                for key, value in (tool_context or {}).items()
                if key in {"requester_id", "chat_id"}
            )
        )

    @classmethod
    def _tool_env(cls, tool_context: Mapping[str, str] | None) -> dict[str, str]:
        env = os.environ.copy()
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
                        yield delta
                continue
            if event.get("event") != "result":
                continue

            result = event.get("result", {})
            if str(result.get("status", "")).casefold() != "success":
                raise self._error_from_text(json.dumps(result, ensure_ascii=False))
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
            session.process.terminate()
            try:
                await asyncio.wait_for(session.process.wait(), timeout=3)
            except asyncio.TimeoutError:
                session.process.kill()
                await session.process.wait()
        if session.stderr_task:
            session.stderr_task.cancel()

    @staticmethod
    def _error_from_text(text: str) -> AgyCliError:
        lowered = text.casefold()
        if "model_capacity_exhausted" in lowered or "no capacity available" in lowered or "code 503" in lowered:
            return AgyCliError("Модель временно недоступна: нет свободной capacity.")
        return AgyCliError(text.strip()[:500] or "agy CLI завершился с ошибкой")
