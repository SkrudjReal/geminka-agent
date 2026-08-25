"""Fast native OpenAI-compatible OMP SSE Gateway for Antigravity models.

Exposes:
- GET  /health
- GET  /v1/models
- POST /v1/chat/completions (Full SSE streaming with OpenAI format)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
import uvicorn

from app.core import config

logger = logging.getLogger("antigravity-omp-gateway")

AVAILABLE_MODELS = [
    "google-antigravity/gemini-3.7-flash",
    "google-antigravity/gemini-3.6-flash",
    "google-antigravity/claude-sonnet-4-5",
    "google-antigravity/claude-opus-4-6",
]


class OMPBackend:
    def __init__(self) -> None:
        self.ls_bin = config.LINUX_LS_BIN if config.LINUX_LS_BIN.exists() else config.WIN_LS_BIN
        self.brain_dir = config.LINUX_BRAIN_DIR if config.LINUX_BRAIN_DIR.exists() else config.WIN_BRAIN_DIR
        self._cached_env: dict[str, str] | None = None

    def get_latest_conversation_id(self) -> str | None:
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
        if self._cached_env and not force_refresh:
            return self._cached_env

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
                                env = test_env
                                self._cached_env = env
                                return env
                        except Exception:
                            continue
            except Exception as e:
                logger.debug("Language server discovery error: %s", e)

        return None

    def send_message(self, conversation_id: str, text: str) -> bool:
        env = self.discover_language_server()
        if not env or not self.ls_bin.exists():
            env = self.discover_language_server(force_refresh=True)
            if not env:
                return False

        cmd = [str(self.ls_bin), "agentapi", "send-message", conversation_id, text]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=15)
            return res.returncode == 0
        except Exception as e:
            logger.error("Error sending agentapi message: %s", e)
            self._cached_env = None
            return False

    async def stream_completion(self, prompt: str, timeout: int = 180) -> AsyncGenerator[str, None]:
        convo_id = self.get_latest_conversation_id()
        if not convo_id:
            yield "❌ Antigravity conversation not found."
            return

        log_path = self.brain_dir / convo_id / ".system_generated" / "logs" / "transcript.jsonl"
        initial_offset = log_path.stat().st_size if log_path.exists() else 0

        ok = await asyncio.to_thread(self.send_message, convo_id, prompt)
        if not ok:
            yield "❌ Failed to send request to Antigravity Language Server."
            return

        start_time = time.time()
        current_offset = max(0, initial_offset)

        while time.time() - start_time < timeout:
            if log_path.exists():
                try:
                    file_size = log_path.stat().st_size
                    if file_size > current_offset:
                        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
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
                                    content = step.get("content").strip()
                                    # Chunk output token-by-token for smooth SSE streaming
                                    chunk_size = 4
                                    for i in range(0, len(content), chunk_size):
                                        yield content[i : i + chunk_size]
                                        await asyncio.sleep(0.01)
                                    return
                            except Exception:
                                pass
                except Exception:
                    pass

            await asyncio.sleep(0.1)

        yield "⚠️ Request timed out."


backend = OMPBackend()


async def health_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "antigravity-omp-gateway", "version": "1.0.0"})


async def models_endpoint(request: Request) -> JSONResponse:
    return JSONResponse({
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "google-antigravity",
                "permission": [],
                "root": model_id,
                "parent": None,
            }
            for model_id in AVAILABLE_MODELS
        ],
    })


async def chat_completions_endpoint(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}}, status_code=400)

    model = payload.get("model", "google-antigravity/gemini-3.7-flash")
    messages = payload.get("messages", [])
    stream = payload.get("stream", False)

    # Format messages into cohesive prompt
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.append(f"[System Instruction]:\n{content}")
        elif role == "user":
            parts.append(f"User: {content}")
        elif role == "assistant":
            parts.append(f"Assistant: {content}")

    full_prompt = "\n\n".join(parts).strip()
    cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created_ts = int(time.time())

    if stream:
        async def sse_generator() -> AsyncGenerator[bytes, None]:
            async for chunk in backend.stream_completion(full_prompt):
                event_data = {
                    "id": cmpl_id,
                    "object": "chat.completion.chunk",
                    "created": created_ts,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": chunk},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n".encode("utf-8")

            # Final finish event
            final_data = {
                "id": cmpl_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "stop",
                    }
                ],
            }
            yield f"data: {json.dumps(final_data, ensure_ascii=False)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        # Non-streaming response
        chunks: list[str] = []
        async for chunk in backend.stream_completion(full_prompt):
            chunks.append(chunk)
        full_text = "".join(chunks).strip()

        return JSONResponse({
            "id": cmpl_id,
            "object": "chat.completion",
            "created": created_ts,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": full_text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(full_prompt) // 4,
                "completion_tokens": len(full_text) // 4,
                "total_tokens": (len(full_prompt) + len(full_text)) // 4,
            },
        })


def create_app() -> Starlette:
    routes = [
        Route("/health", health_endpoint, methods=["GET"]),
        Route("/v1/models", models_endpoint, methods=["GET"]),
        Route("/v1/chat/completions", chat_completions_endpoint, methods=["POST"]),
    ]
    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]
    return Starlette(debug=False, routes=routes, middleware=middleware)


app = create_app()


async def start_omp_gateway_task(host: str = "127.0.0.1", port: int = 4000) -> tuple[uvicorn.Server, asyncio.Task]:
    """Starts the OMP Gateway server as an asynchronous background task."""
    uv_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(uv_config)
    task = asyncio.create_task(server.serve())
    for _ in range(30):
        if server.started:
            break
        await asyncio.sleep(0.05)
    return server, task


async def start_omp_gateway(host: str = "127.0.0.1", port: int = 4000) -> None:
    """Runs the OMP Gateway server."""
    uv_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(uv_config)
    logger.info("Starting Antigravity OMP SSE Gateway on http://%s:%d/v1", host, port)
    await server.serve()


if __name__ == "__main__":
    asyncio.run(start_omp_gateway())
