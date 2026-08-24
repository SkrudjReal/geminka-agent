"""Antigravity OMP Gateway Bridge & Fast SSE Streaming Client.

Implements full pipeline architecture from hermes-antigravity-pipeline skill:
- OpenAI-compatible `/v1/chat/completions` protocol
- Fast real-time Server-Sent Events (SSE) streaming token-by-token
- Multi-model routing (Gemini 3.7 Flash, Claude Sonnet 4.5, Claude Opus)
- Critical reasoning_effort normalization ('medium') avoiding 400 & 502 errors
- Max output tokens expansion (8192) preventing thought-only truncation
- Automatic Rate Limit (429 / RESOURCE_EXHAUSTED) retry handler with exponential backoff
- Persistent multi-turn conversation memory per user
- Seamless fallback to local IDE Language Server if OMP Gateway is offline
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional

import httpx

from app.core import config
from app.core.context import context_manager
from app.services.rag import rag_engine

logger = logging.getLogger(__name__)

# Available models on Antigravity OMP Gateway
AVAILABLE_MODELS = [
    "google-antigravity/gemini-3.7-flash",
    "google-antigravity/gemini-3.6-flash",
    "google-antigravity/claude-sonnet-4-5",
    "google-antigravity/claude-opus-4-6",
]

# User model preferences
_user_models: Dict[str, str] = {}
# User reasoning effort preferences: user_id -> str ("low", "medium", "high")
_user_reasoning: Dict[str, str] = {}
# Multi-turn conversation histories: user_id -> List[Dict[str, str]]
_conversation_histories: Dict[str, List[Dict[str, str]]] = {}


class AntigravityClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        ls_bin: Optional[Path] = None,
        brain_dir: Optional[Path] = None,
    ):
        self.base_url = base_url or config.OMP_BASE_URL
        self.ls_bin = ls_bin or (config.LINUX_LS_BIN if config.LINUX_LS_BIN.exists() else config.WIN_LS_BIN)
        self.brain_dir = brain_dir or (config.LINUX_BRAIN_DIR if config.LINUX_BRAIN_DIR.exists() else config.WIN_BRAIN_DIR)
        self._cached_env: Optional[Dict[str, str]] = None
        self._cached_port: Optional[str] = None
        self._cached_csrf: Optional[str] = None

        logger.info(f"Initialized AntigravityClient | OMP Gateway={self.base_url}")

    def get_user_model(self, user_id: int) -> str:
        """Returns the chosen model for user, defaulting to Gemini 3.7 Flash."""
        return _user_models.get(str(user_id), config.DEFAULT_MODEL)

    def set_user_model(self, user_id: int, model_name: str) -> None:
        """Sets chosen model for user."""
        _user_models[str(user_id)] = model_name

    def get_user_reasoning(self, user_id: int) -> str:
        """Returns the chosen reasoning effort for user, defaulting to 'medium'."""
        return _user_reasoning.get(str(user_id), config.REASONING_EFFORT)

    def set_user_reasoning(self, user_id: int, effort: str) -> None:
        """Sets chosen reasoning effort for user ('low', 'medium', 'high')."""
        _user_reasoning[str(user_id)] = effort

    def clear_history(self, user_id: int) -> None:
        """Clears multi-turn conversation history and context window for user."""
        _conversation_histories.pop(str(user_id), None)
        context_manager.clear_user_context(user_id)

    async def check_omp_health(self) -> bool:
        """Checks if OMP Gateway is alive on configured base_url."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                res = await client.get(f"{config.OMP_BASE_URL}/models")
                return res.status_code == 200
        except Exception:
            return False

    async def list_omp_models(self) -> List[str]:
        """Queries available model IDs from OMP Gateway."""
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                res = await client.get(f"{self.base_url}/models")
                if res.status_code == 200:
                    data = res.json()
                    models = [m.get("id") for m in data.get("data", []) if m.get("id")]
                    if models:
                        return models
        except Exception as e:
            logger.debug(f"Failed to fetch OMP models: {e}")
        return AVAILABLE_MODELS

    async def stream_omp_chat(
        self,
        user_id: int,
        prompt: str,
        system_prompt: Optional[str] = None,
        emotional_context: str = "",
        adaptive_context: str = "",
        user_emojis_context: str = "",
    ) -> AsyncGenerator[str, None]:
        """Streams response tokens from OMP Gateway using OpenAI-compatible SSE with RAG & Sliding Context."""
        model = self.get_user_model(user_id)
        sys_prompt = system_prompt or config.get_system_prompt()

        # 1. Query RAG Engine for relevant long-term memories & user facts
        rag_context = rag_engine.format_rag_context(prompt, top_k=3)

        # 2. Build Sliding Context Window messages payload
        messages = context_manager.build_payload_messages(
            user_id=user_id,
            current_prompt=prompt,
            system_prompt=sys_prompt,
            emotional_context=emotional_context,
            rag_context=rag_context,
            adaptive_context=adaptive_context,
            user_emojis_context=user_emojis_context,
        )

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": config.MAX_OUTPUT_TOKENS,
            "stream": True,
        }

        # Stability rule from hermes-antigravity-pipeline skill:
        # Guarantee reasoning_effort for gemini/antigravity models to prevent 400 & 502 errors
        user_reasoning = self.get_user_reasoning(user_id)
        if "gemini" in model.lower() or "antigravity" in model.lower() or "claude" in model.lower():
            payload["reasoning_effort"] = user_reasoning

        url = f"{self.base_url}/chat/completions"
        assistant_reply_accum = []
        retries = 0

        while retries <= config.API_MAX_RETRIES:
            try:
                async with httpx.AsyncClient(timeout=180.0) as client:
                    async with client.stream("POST", url, json=payload) as response:
                        if response.status_code == 429:
                            # Rate limit error from skill: retry with exponential backoff
                            retries += 1
                            backoff = min(2 ** retries, 16)
                            logger.warning(f"OMP 429 Rate limit hit, retrying in {backoff}s ({retries}/{config.API_MAX_RETRIES})...")
                            await asyncio.sleep(backoff)
                            continue

                        if response.status_code != 200:
                            err_body = await response.aread()
                            logger.error(f"OMP Gateway error {response.status_code}: {err_body.decode('utf-8', errors='ignore')}")
                            yield f"⚠️ Ошибка OMP Gateway ({response.status_code}): {err_body.decode('utf-8', errors='ignore')[:150]}"
                            return

                        async for line in response.aiter_lines():
                            if not line or not line.startswith("data:"):
                                continue

                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                break

                            try:
                                chunk_json = json.loads(data_str)
                                delta = chunk_json.get("choices", [{}])[0].get("delta", {})
                                token = delta.get("content") or ""
                                if token:
                                    assistant_reply_accum.append(token)
                                    yield token
                            except Exception:
                                continue

                # Generation finished successfully -> update Sliding Context Window
                full_reply = "".join(assistant_reply_accum).strip()
                if full_reply:
                    ctx = context_manager.get_context(user_id)
                    ctx.add_user_message(prompt)
                    ctx.add_assistant_message(full_reply)
                return

            except httpx.ConnectError:
                logger.warning("Could not connect to OMP Gateway at " + self.base_url)
                break
            except Exception as e:
                retries += 1
                logger.warning(f"OMP stream exception (retry {retries}): {e}")
                if retries > config.API_MAX_RETRIES:
                    yield f"⚠️ Ошибка соединения с OMP Gateway: {e}"
                    return
                await asyncio.sleep(1.0)

            except httpx.ConnectError:
                logger.warning("Could not connect to OMP Gateway at " + self.base_url)
                break
            except Exception as e:
                retries += 1
                logger.warning(f"OMP stream exception (retry {retries}): {e}")
                if retries > config.API_MAX_RETRIES:
                    yield f"⚠️ Ошибка соединения с OMP Gateway: {e}"
                    return
                await asyncio.sleep(1.0)

    # --- Fallback Local Antigravity Engine Mode ---
    def get_latest_conversation_id(self) -> Optional[str]:
        """Dynamically finds the most recent active conversation in the brain directory."""
        if not self.brain_dir.exists():
            return None
        try:
            dirs = [
                d
                for d in self.brain_dir.iterdir()
                if d.is_dir() and (d / ".system_generated" / "logs" / "transcript.jsonl").exists()
            ]
            if not dirs:
                return None
            dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return dirs[0].name
        except Exception as e:
            logger.warning(f"Failed to scan brain dir: {e}")
            return None

    def discover_language_server(self, force_refresh: bool = False) -> Optional[Dict[str, str]]:
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
                logger.debug(f"Linux language_server discovery error: {e}")

        if port and csrf_token:
            env = os.environ.copy()
            env["ANTIGRAVITY_LS_ADDRESS"] = f"http://127.0.0.1:{port}"
            env["ANTIGRAVITY_CSRF_TOKEN"] = csrf_token
            self._cached_env = env
            self._cached_port = port
            self._cached_csrf = csrf_token
            return env

        return None

    def run_agentapi(self, args: List[str]) -> Optional[dict]:
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
            logger.error(f"Error running agentapi: {e}")
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
        conversation_id: Optional[str],
        prompt: str,
        timeout: int = 180,
    ) -> AsyncGenerator[str, None]:
        """Fallback stream tailing from local Antigravity brain transcript.jsonl."""
        if not conversation_id:
            conversation_id = self.get_latest_conversation_id()
            if not conversation_id:
                yield "❌ Локальный движок Antigravity не найден. Запустите Antigravity IDE или OMP Gateway."
                return

        initial_offset = self.get_file_size(conversation_id)
        await asyncio.to_thread(self.send_local_message, conversation_id, prompt)

        path = self.get_transcript_path(conversation_id)
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
                                    yield step.get("content").strip()
                                    return
                            except Exception:
                                pass
                except Exception:
                    pass

            await asyncio.sleep(0.05)

        yield "⚠️ Локальный агент долго отвечает. Попробуйте повторить."

    async def generate_stream(
        self,
        user_id: int,
        prompt: str,
        emotional_context: str = "",
        adaptive_context: str = "",
        user_emojis_context: str = "",
        conversation_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Unified Generator: Prioritizes OMP Gateway SSE, falls back seamlessly to Local Brain."""
        rag_context = rag_engine.format_rag_context(prompt, top_k=3)
        is_omp_alive = await self.check_omp_health()
        if is_omp_alive:
            async for token in self.stream_omp_chat(
                user_id,
                prompt,
                emotional_context=emotional_context,
                adaptive_context=adaptive_context,
                user_emojis_context=user_emojis_context,
            ):
                yield token
        else:
            full_prompt = f"{emotional_context}\n{adaptive_context}\n{user_emojis_context}\n{rag_context}\n{prompt}".strip()
            async for token in self.stream_local_brain(conversation_id, full_prompt):
                yield token
