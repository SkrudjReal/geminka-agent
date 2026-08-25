"""OpenAI-compatible OMP transport with bounded retry semantics."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncGenerator

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
    ) -> None:
        self.base_url = (base_url or config.settings.omp_base_url).rstrip("/")
        self.store = store
        self.contexts = contexts
        self.memories = memories
        self._owns_client = http_client is None
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
            response = await self._client.get(f"{self.base_url}/models", timeout=3.0)
            return response.status_code == 200
        except httpx.HTTPError:
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

    async def generate_stream(
        self,
        user_id: int,
        prompt: str,
        emotional_context: str = "",
        adaptive_context: str = "",
        user_emojis_context: str = "",
    ) -> AsyncGenerator[str, None]:
        """Generate exclusively through the configured OMP trust boundary."""
        async for token in self.stream_omp_chat(
            user_id,
            prompt,
            emotional_context=emotional_context,
            adaptive_context=adaptive_context,
            user_emojis_context=user_emojis_context,
        ):
            yield token
