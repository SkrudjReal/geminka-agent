"""Antigravity Connect/SSE Gateway Process Manager.

Supervises the built-in open-antigravity proxy daemon that bridges
local Antigravity Language Server / OAuth credentials to an OpenAI-compatible
SSE endpoint on port 4000.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import httpx

from app.core import config

logger = logging.getLogger("antigravity-connect-gateway")

AVAILABLE_MODELS = [
    "google-antigravity/gemini-3.7-flash",
]


class GatewayProcessManager:
    """Manages the background open-antigravity Node.js process."""

    def __init__(self, process: asyncio.subprocess.Process | None = None) -> None:
        self.process = process
        self.should_exit = False

    def terminate(self) -> None:
        self.should_exit = True
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
            except Exception as e:
                logger.debug("Error terminating gateway process: %s", e)

    async def aclose(self) -> None:
        self.terminate()
        if self.process and self.process.returncode is None:
            try:
                await asyncio.wait_for(self.process.wait(), timeout=3.0)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass


def _find_gateway_script() -> Path | None:
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "tools" / "open-antigravity" / "dist" / "index.js",
        Path("/home/velunae/tools/open-antigravity/dist/index.js"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


async def is_gateway_healthy(host: str = "127.0.0.1", port: int = 4000) -> bool:
    urls = [
        f"http://{host}:{port}/health",
        f"http://{host}:{port}/v1/models",
    ]
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            for url in urls:
                try:
                    res = await client.get(url)
                    if res.status_code == 200:
                        return True
                except Exception:
                    continue
            return False
    except Exception:
        return False


async def start_omp_gateway_task(
    host: str = "127.0.0.1",
    port: int = 4000,
) -> tuple[GatewayProcessManager, asyncio.Task[Any] | None]:
    """Starts open-antigravity daemon and waits for it to become ready."""
    if await is_gateway_healthy(host, port):
        logger.info("Antigravity Connect/SSE Gateway is already running and healthy on %s:%d", host, port)
        return GatewayProcessManager(None), None

    script_path = _find_gateway_script()
    if not script_path:
        raise RuntimeError(
            "open-antigravity gateway script (dist/index.js) not found. "
            "Ensure tools/open-antigravity is built."
        )

    env = os.environ.copy()
    env["PORT"] = str(port)
    env["HOST"] = host

    logger.info("Auto-launching Antigravity Connect/SSE Gateway from %s on port %d...", script_path, port)
    proc = await asyncio.create_subprocess_exec(
        "node",
        str(script_path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    manager = GatewayProcessManager(proc)

    async def _log_stream() -> None:
        if not proc.stdout:
            return
        while not manager.should_exit:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="ignore").strip()
            if text:
                logger.info("[connect-gateway] %s", text)

    log_task = asyncio.create_task(_log_stream())

    # Wait for gateway to become healthy
    for _ in range(40):
        if await is_gateway_healthy(host, port):
            logger.info("Antigravity Connect/SSE Gateway is ONLINE on http://%s:%d/v1", host, port)
            return manager, log_task
        if proc.returncode is not None:
            break
        await asyncio.sleep(0.1)

    if proc.returncode is not None:
        raise RuntimeError(f"Antigravity Connect/SSE gateway process exited with code {proc.returncode}")

    return manager, log_task