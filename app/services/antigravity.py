"""Direct OpenAI-compatible OMP transport with bounded retry semantics and SSE streaming."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncGenerator
from typing import Any

import httpx

from app.core import config
from app.core.context import ContextManager, context_manager
from app.core.state import StateStore, state_store
from app.services.rag import RAGMemoryEngine, rag_engine

logger = logging.getLogger(__name__)

AVAILABLE_MODELS = [
    "google-antigravity/gemini-3.7-flash",
]
REASONING_LEVELS = {"low", "medium", "high"}

# Reasoning effort defaults per SKILL.md:
# gemini-3.7-flash and google-antigravity models throw 400 "Thinking level MINIMAL is not supported"
# if reasoning_effort is absent or minimal. Level 'high' may cause 502 thought-only responses.
DEFAULT_REASONING_EFFORT = "medium"


class GatewayError(RuntimeError):
    """Safe, user-displayable OMP failure."""


class GatewayUnavailable(GatewayError):
    """OMP could not be reached or exhausted all retries."""


class AntigravityClient:
    """Direct OMP Gateway Client communicating via OpenAI-compatible SSE streaming."""

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
        logger.info("Direct OMP client initialized for %s", self.base_url)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def get_user_model(self, user_id: int) -> str:
        return self.store.get_preferences(user_id)["model"] or config.settings.default_model

    def set_user_model(self, user_id: int, model_name: str) -> None:
        normalized = config.normalize_model_name(model_name)
        if normalized not in AVAILABLE_MODELS and not normalized.startswith("google-antigravity/"):
            raise ValueError(f"Unsupported model: {model_name}")
        self.store.set_preference(user_id, "model", normalized)

    def get_user_reasoning(self, user_id: int) -> str:
        return (
            self.store.get_preferences(user_id)["reasoning"]
            or config.settings.reasoning_effort
            or DEFAULT_REASONING_EFFORT
        )

    def set_user_reasoning(self, user_id: int, effort: str) -> None:
        normalized = effort.strip().lower()
        if normalized not in REASONING_LEVELS:
            raise ValueError(f"Unsupported reasoning effort: {effort}. Must be one of {REASONING_LEVELS}")
        self.store.set_preference(user_id, "reasoning", normalized)

    def clear_history(self, user_id: int) -> None:
        self.contexts.clear_user_context(user_id)
        self.store.clear_preferences(user_id)
        self.store.set_conversation_id(user_id, None)

    def _get_endpoint(self, path: str) -> str:
        """Helper to construct correct path whether base_url has /v1 or not."""
        clean_path = path.lstrip("/")
        if self.base_url.endswith("/v1"):
            return f"{self.base_url}/{clean_path}"
        return f"{self.base_url}/v1/{clean_path}"

    async def check_omp_health(self) -> bool:
        """Health-check against OMP /models or /health."""
        for url in (self._get_endpoint("models"), f"{self.base_url}/health"):
            try:
                response = await self._client.get(url, timeout=2.0)
                if response.status_code == 200:
                    return True
            except (httpx.HTTPError, OSError):
                continue
        return False

    async def list_omp_models(self) -> list[str]:
        """Fetch available models from OMP."""
        try:
            response = await self._client.get(self._get_endpoint("models"), timeout=5.0)
            response.raise_for_status()
            data = response.json().get("data", [])
            models = [
                item["id"]
                for item in data
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            ]
            return models or AVAILABLE_MODELS
        except (httpx.HTTPError, ValueError, KeyError):
            return AVAILABLE_MODELS

    async def _backoff(self, attempt: int) -> None:
        delay = min(2**attempt, 16) + random.uniform(0, 0.25)
        await asyncio.sleep(delay)

    def _ensure_reasoning_effort(self, model: str, effort: str) -> str:
        if (
            "3.7" in model
            or "claude" in model.lower()
            or "google-antigravity/" in model
        ) and effort not in REASONING_LEVELS:
            effort = DEFAULT_REASONING_EFFORT
        return effort

    async def _iter_sse_content(self, response: httpx.Response) -> AsyncGenerator[tuple[str, str | None], None]:
        """Parses standard SSE stream lines into (token, conversation_id) tuples."""
        async for line in response.aiter_lines():
            line = line.strip()
            if not line or not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                return
            try:
                data = json.loads(raw)
                convo_id = data.get("_conversation_id")
                choices = data.get("choices", [])
                if not choices:
                    if convo_id:
                        yield ("", convo_id)
                    continue
                delta = choices[0].get("delta", {})
                token = delta.get("content", "")
            except (json.JSONDecodeError, AttributeError, IndexError, TypeError):
                logger.debug("Ignored malformed OMP SSE event: %s", raw[:100])
                continue
            if isinstance(token, str) and token:
                yield (token, convo_id)
            elif convo_id:
                yield ("", convo_id)

    async def stream_omp_chat(
        self,
        user_id: int,
        prompt: str,
        system_prompt: str | None = None,
        emotional_context: str = "",
        adaptive_context: str = "",
        user_emojis_context: str = "",
    ) -> AsyncGenerator[str, None]:
        """Direct SSE chat completion stream from OMP Gateway with auto-retry and reasoning handling."""
        model = self.get_user_model(user_id)
        reasoning_effort = self._ensure_reasoning_effort(model, self.get_user_reasoning(user_id))
        convo_id = self.store.get_conversation_id(user_id)

        reasoning_desc = (
            "минимальные краткие рассуждения (Low / Minimal Thinking)"
            if reasoning_effort == "low"
            else "умеренный баланс размышлений (Medium Thinking)"
            if reasoning_effort == "medium"
            else "максимально глубокий анализ (High Thinking)"
        )
        runtime_context = (
            f"[ТЕКУЩЕЕ СОСТОЯНИЕ РЕЖИМА РАССУЖДЕНИЙ / REASONING EFFORT]:\n"
            f"• Модель: Gemini 3.7 Flash\n"
            f"• Активный уровень Reasoning Effort: {reasoning_effort.upper()} ({reasoning_desc})."
        )

        messages = self.contexts.build_payload_messages(
            user_id=user_id,
            current_prompt=prompt,
            system_prompt=system_prompt or config.get_system_prompt(),
            emotional_context=emotional_context,
            memory_context=self.memories.format_memory_context(user_id),
            adaptive_context=adaptive_context,
            user_emojis_context=user_emojis_context,
            runtime_context=runtime_context,
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": config.settings.max_output_tokens,
            "reasoning_effort": reasoning_effort,
            "stream": True,
        }
        if convo_id:
            payload["conversation_id"] = convo_id

        req_headers: dict[str, str] = {}
        if convo_id:
            req_headers["x-conversation-id"] = convo_id

        url = self._get_endpoint("chat/completions")
        reply: list[str] = []
        max_retries = max(config.settings.api_max_retries, 1)

        for attempt in range(max_retries + 1):
            emitted = False
            retryable = False
            try:
                async with self._client.stream("POST", url, json=payload, headers=req_headers) as response:
                    if response.status_code != 200:
                        body_bytes = await response.aread()
                        body_text = body_bytes.decode("utf-8", errors="replace")[:1000]
                        logger.warning("OMP HTTP %s: %s", response.status_code, body_text)

                        # Check for 429 RPS rate limit (Resource exhausted / check quota) per SKILL.md
                        is_rate_limit = (
                            response.status_code == 429
                            or "resource has been exhausted" in body_text.lower()
                            or "check quota" in body_text.lower()
                        )
                        # Check for 502 thought-only response
                        is_thought_only = (
                            response.status_code == 502
                            and "thought-only response" in body_text.lower()
                        )
                        if is_thought_only:
                            logger.warning(
                                "OMP returned thought-only 502 for model %s. Reasoning effort %s may be too high or max_tokens too low.",
                                model,
                                reasoning_effort,
                            )

                        if is_rate_limit or response.status_code >= 500:
                            retryable = True
                        else:
                            raise GatewayError(
                                f"OMP отклонил запрос (HTTP {response.status_code}): {body_text[:200]}"
                            )
                    else:
                        async for token, new_convo_id in self._iter_sse_content(response):
                            if new_convo_id and new_convo_id != convo_id:
                                self.store.set_conversation_id(user_id, new_convo_id)
                                convo_id = new_convo_id
                            if token:
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
                logger.warning(
                    "OMP transport attempt %d/%d failed: %s (%s)",
                    attempt + 1,
                    max_retries + 1,
                    type(exc).__name__,
                    exc,
                )
                retryable = True

            if attempt >= max_retries:
                break
            if retryable and not emitted:
                await self._backoff(attempt + 1)

        raise GatewayUnavailable("OMP Gateway недоступен. Проверьте запуск OMP и OMP_BASE_URL.")

    async def generate_stream(
        self,
        user_id: int,
        prompt: str,
        emotional_context: str = "",
        adaptive_context: str = "",
        user_emojis_context: str = "",
        conversation_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Primary stream generator: delegates directly to OMP SSE chat."""
        del conversation_id  # Unused legacy argument preserved for signature compatibility
        async for token in self.stream_omp_chat(
            user_id=user_id,
            prompt=prompt,
            emotional_context=emotional_context,
            adaptive_context=adaptive_context,
            user_emojis_context=user_emojis_context,
        ):
            yield token


# Backward compatibility aliases
OMPClient = AntigravityClient
