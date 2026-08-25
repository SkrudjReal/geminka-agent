"""OpenAI-compatible OMP transport with bounded retry semantics and local Antigravity Language Server fallback."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import subprocess
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx

from app.core import config
from app.core.context import ContextManager, context_manager
from app.core.state import StateStore, state_store
from app.services.rag import RAGMemoryEngine, rag_engine

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = [
    "google-antigravity/gemini-3.7-flash",
    "google-antigravity/gemini-3.6-flash",
    "google-antigravity/claude-sonnet-4-5",
    "google-antigravity/claude-opus-4-6",
]
REASONING_LEVELS = {"low", "medium", "high"}


class GatewayError(RuntimeError):
    """Safe, user-displayable OMP failure."""


class GatewayUnavailable(GatewayError):
    """OMP could not be reached or exhausted all retries."""


class AntigravityClient:
    def __init__(
        self,
        base_url: str | None = None,
        *,
        api_key: str | None = None,
        store: StateStore = state_store,
        contexts: ContextManager = context_manager,
        memories: RAGMemoryEngine = rag_engine,
        http_client: httpx.AsyncClient | None = None,
        ls_bin: Path | None = None,
        brain_dir: Path | None = None,
    ) -> None:
        self.base_url = (base_url or config.settings.omp_base_url).rstrip("/")
        self.store = store
        self.contexts = contexts
        self.memories = memories
        self._owns_client = http_client is None
        self.ls_bin = ls_bin or (config.LINUX_LS_BIN if config.LINUX_LS_BIN.exists() else config.WIN_LS_BIN)
        self.brain_dir = brain_dir or (config.LINUX_BRAIN_DIR if config.LINUX_BRAIN_DIR.exists() else config.WIN_BRAIN_DIR)
        self._cached_env: dict[str, str] | None = None
        self._cached_port: str | None = None
        self._cached_csrf: str | None = None

        headers = {}
        key = config.settings.omp_api_key if api_key is None else api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        self._client = http_client or httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(config.settings.request_timeout_seconds, connect=5.0),
        )
        logger.info("OMP client initialized for %s", self.base_url)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def get_user_model(self, user_id: int) -> str:
        return self.store.get_preferences(user_id)["model"] or config.settings.default_model

    def set_user_model(self, user_id: int, model_name: str) -> None:
        if model_name not in AVAILABLE_MODELS:
            raise ValueError("Unsupported model")
        self.store.set_preference(user_id, "model", model_name)

    def get_user_reasoning(self, user_id: int) -> str:
        return self.store.get_preferences(user_id)["reasoning"] or config.settings.reasoning_effort

    def set_user_reasoning(self, user_id: int, effort: str) -> None:
        if effort not in REASONING_LEVELS:
            raise ValueError("Unsupported reasoning effort")
        self.store.set_preference(user_id, "reasoning", effort)

    def clear_history(self, user_id: int) -> None:
        self.contexts.clear_user_context(user_id)
        self.store.clear_preferences(user_id)

    async def check_omp_health(self) -> bool:
        try:
            response = await self._client.get(f"{self.base_url}/models", timeout=2.0)
            return response.status_code == 200
        except (httpx.HTTPError, OSError):
            return False

    async def list_omp_models(self) -> list[str]:
        try:
            response = await self._client.get(f"{self.base_url}/models", timeout=5.0)
            response.raise_for_status()
            models = [
                item["id"]
                for item in response.json().get("data", [])
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
            return models or AVAILABLE_MODELS
        except (httpx.HTTPError, ValueError, KeyError):
            return AVAILABLE_MODELS

    async def _backoff(self, attempt: int) -> None:
        delay = min(2**attempt, 16) + random.uniform(0, 0.25)
        await asyncio.sleep(delay)

    async def _iter_sse_content(self, response: httpx.Response) -> AsyncGenerator[str, None]:
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                return
            try:
                data = json.loads(raw)
                token = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
            except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
                logger.debug("Ignored malformed OMP SSE event")
                continue
            if isinstance(token, str) and token:
                yield token

    async def stream_omp_chat(
        self,
        user_id: int,
        prompt: str,
        system_prompt: str | None = None,
        emotional_context: str = "",
        adaptive_context: str = "",
        user_emojis_context: str = "",
    ) -> AsyncGenerator[str, None]:
        model = self.get_user_model(user_id)
        messages = self.contexts.build_payload_messages(
            user_id=user_id,
            current_prompt=prompt,
            system_prompt=system_prompt or config.get_system_prompt(),
            emotional_context=emotional_context,
            memory_context=self.memories.format_memory_context(user_id),
            adaptive_context=adaptive_context,
            user_emojis_context=user_emojis_context,
        )
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "max_tokens": config.settings.max_output_tokens,
            "reasoning_effort": self.get_user_reasoning(user_id),
            "stream": True,
        }
        url = f"{self.base_url}/chat/completions"
        reply: list[str] = []

        for attempt in range(config.settings.api_max_retries + 1):
            emitted = False
            retryable_status = False
            try:
                async with self._client.stream("POST", url, json=payload) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode("utf-8", errors="replace")[:500]
                        logger.warning("OMP HTTP %s: %s", response.status_code, body)
                        retryable_status = response.status_code == 429 or response.status_code >= 500
                        if not retryable_status:
                            raise GatewayError(f"OMP отклонил запрос (HTTP {response.status_code}).")
                    else:
                        async for token in self._iter_sse_content(response):
                            emitted = True
                            reply.append(token)
                            yield token
                        full_reply = "".join(reply).strip()
                        if full_reply:
                            self.contexts.add_exchange(user_id, prompt, full_reply)
                        return
            except GatewayError:
                raise
            except httpx.HTTPError as exc:
                if emitted:
                    raise GatewayUnavailable("OMP оборвал поток после начала ответа.") from exc
                logger.warning("OMP transport attempt %s failed: %s", attempt + 1, type(exc).__name__)

            if attempt >= config.settings.api_max_retries:
                break
            if retryable_status or not emitted:
                await self._backoff(attempt + 1)

        raise GatewayUnavailable("OMP Gateway недоступен. Проверьте его запуск и OMP_BASE_URL.")

    # --- Local Antigravity Language Server Fallback Engine ---

    def get_latest_conversation_id(self) -> str | None:
        """Dynamically finds the most recent active conversation in the brain directory."""
        if not self.brain_dir.exists():
            return None
        try:
            valid: list[tuple[str, float]] = []
            for d in self.brain_dir.iterdir():
                if d.is_dir():
                    log = d / ".system_generated" / "logs" / "transcript.jsonl"
                    if log.exists():
                        valid.append((d.name, log.stat().st_mtime))
            if not valid:
                return None
            valid.sort(key=lambda x: x[1], reverse=True)
            return valid[0][0]
        except Exception as e:
            logger.warning("Failed to scan brain dir: %s", e)
            return None

    def discover_language_server(self, force_refresh: bool = False) -> dict[str, str] | None:
        """Dynamically finds the running language_server process, port and CSRF token."""
        if self._cached_env and not force_refresh:
            return self._cached_env

        port = None
        csrf_token = None
        target_pid = None

        proc_path = Path("/proc")
        if proc_path.exists():
            try:
                for pid_dir in proc_path.iterdir():
                    if pid_dir.is_dir() and pid_dir.name.isdigit():
                        cmd_file = pid_dir / "cmdline"
                        if cmd_file.exists():
                            try:
                                with open(cmd_file, "rb") as f:
                                    cmdline = f.read().decode("utf-8", errors="ignore").replace("\x00", " ")
                                if "language_server" in cmdline and "--csrf_token" in cmdline:
                                    m = re.search(r"--csrf_token\s+([a-f0-9\-]+)", cmdline)
                                    if m:
                                        csrf_token = m.group(1)
                                        target_pid = pid_dir.name
                                        break
                            except Exception:
                                pass

                if target_pid and csrf_token:
                    candidate_ports = []
                    try:
                        ss_res = subprocess.run(["ss", "-tlpn", "-p"], capture_output=True, text=True, timeout=2)
                        for line in ss_res.stdout.splitlines():
                            if f"pid={target_pid}," in line or f"pid={target_pid})" in line:
                                m = re.search(r":(\d+)\s+", line)
                                if m:
                                    candidate_ports.append(m.group(1))
                    except Exception:
                        pass

                    sample_convo = self.get_latest_conversation_id() or "5038b367-f7c3-4502-918b-fa8d0a949f77"
                    for cp in candidate_ports:
                        test_env = os.environ.copy()
                        test_env["ANTIGRAVITY_LS_ADDRESS"] = f"http://127.0.0.1:{cp}"
                        test_env["ANTIGRAVITY_CSRF_TOKEN"] = csrf_token
                        try:
                            probe = subprocess.run(
                                [str(self.ls_bin), "agentapi", "get-conversation-metadata", sample_convo],
                                capture_output=True,
                                text=True,
                                env=test_env,
                                timeout=2,
                            )
                            if probe.returncode == 0 and ("conversationMetadata" in probe.stdout or "response" in probe.stdout):
                                port = cp
                                break
                        except Exception:
                            continue
            except Exception as e:
                logger.debug("Linux language_server discovery error: %s", e)

        if port and csrf_token:
            env = os.environ.copy()
            env["ANTIGRAVITY_LS_ADDRESS"] = f"http://127.0.0.1:{port}"
            env["ANTIGRAVITY_CSRF_TOKEN"] = csrf_token
            self._cached_env = env
            self._cached_port = port
            self._cached_csrf = csrf_token
            return env

        return None

    def run_agentapi(self, args: list[str]) -> dict | None:
        env = self.discover_language_server()
        if not env or not self.ls_bin.exists():
            env = self.discover_language_server(force_refresh=True)
            if not env:
                return None

        cmd = [str(self.ls_bin), "agentapi"] + args
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=15)
            raw = res.stdout.strip()
            if raw:
                try:
                    return json.loads(raw)
                except Exception:
                    return {"raw": raw}
            return None
        except Exception as e:
            logger.error("Error running agentapi: %s", e)
            self._cached_env = None
            return None

    def send_local_message(self, conversation_id: str, text: str) -> bool:
        res = self.run_agentapi(["send-message", conversation_id, text])
        return bool(res and not res.get("error"))

    def get_transcript_path(self, conversation_id: str) -> Path:
        return (
            self.brain_dir
            / conversation_id
            / ".system_generated"
            / "logs"
            / "transcript.jsonl"
        )

    def get_file_size(self, conversation_id: str) -> int:
        path = self.get_transcript_path(conversation_id)
        if not path.exists():
            return 0
        try:
            return path.stat().st_size
        except Exception:
            return 0

    async def stream_local_brain(
        self,
        user_id: int,
        prompt: str,
        conversation_id: str | None = None,
        timeout: int = 180,
    ) -> AsyncGenerator[str, None]:
        """Fallback stream tailing from local Antigravity brain transcript.jsonl."""
        convo_id = conversation_id or self.get_latest_conversation_id()
        if not convo_id:
            yield "❌ Локальный движок Antigravity не найден. Запустите Antigravity IDE или OMP Gateway."
            return

        initial_offset = self.get_file_size(convo_id)
        await asyncio.to_thread(self.send_local_message, convo_id, prompt)

        path = self.get_transcript_path(convo_id)
        start_time = time.time()
        current_offset = max(0, initial_offset)

        while time.time() - start_time < timeout:
            if path.exists():
                try:
                    file_size = path.stat().st_size
                    if file_size > current_offset:
                        with open(path, "r", encoding="utf-8", errors="ignore") as f:
                            f.seek(current_offset)
                            new_data = f.read()
                            current_offset = f.tell()

                        lines = [line.strip() for line in new_data.splitlines() if line.strip()]
                        for line in reversed(lines):
                            try:
                                step = json.loads(line)
                                if (
                                    step.get("source") == "MODEL"
                                    and step.get("type") == "PLANNER_RESPONSE"
                                    and step.get("status") == "DONE"
                                    and step.get("content")
                                    and not step.get("tool_calls")
                                ):
                                    full_reply = step.get("content").strip()
                                    self.contexts.add_exchange(user_id, prompt, full_reply)
                                    yield full_reply
                                    return
                            except Exception:
                                pass
                except Exception:
                    pass

            await asyncio.sleep(0.1)

        yield "⚠️ Локальный агент долго отвечает. Попробуйте повторить."

    async def generate_stream(
        self,
        user_id: int,
        prompt: str,
        emotional_context: str = "",
        adaptive_context: str = "",
        user_emojis_context: str = "",
        conversation_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Unified Generator: Prioritizes OMP Gateway SSE, falls back seamlessly to Local Brain."""
        # If custom base_url was given (e.g. in tests) or OMP is alive, use OMP stream
        is_custom_or_test = not self._owns_client or self.base_url != config.settings.omp_base_url
        is_omp_alive = is_custom_or_test or await self.check_omp_health()

        if is_omp_alive:
            try:
                async for token in self.stream_omp_chat(
                    user_id,
                    prompt,
                    emotional_context=emotional_context,
                    adaptive_context=adaptive_context,
                    user_emojis_context=user_emojis_context,
                ):
                    yield token
                return
            except GatewayUnavailable as exc:
                if "после начала ответа" in str(exc) or is_custom_or_test:
                    raise
                logger.info("OMP Gateway unavailable, falling back to Local Antigravity Engine: %s", exc)

        # Fallback to local Language Server
        full_prompt = f"{emotional_context}\n{adaptive_context}\n{user_emojis_context}\n{prompt}".strip()
        async for token in self.stream_local_brain(user_id, full_prompt, conversation_id=conversation_id):
            yield token
