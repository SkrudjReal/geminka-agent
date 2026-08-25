from __future__ import annotations

import httpx
import pytest

from app.core.context import ContextManager
from app.core.state import StateStore
from app.services.antigravity import AntigravityClient, GatewayUnavailable
from app.services.rag import RAGMemoryEngine


async def test_retry_before_output_does_not_duplicate(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, text="temporary")
        body = 'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    store = StateStore(tmp_path / "state.db")
    memory = RAGMemoryEngine(store)
    memory.project_memory = ""
    memory.owner_profile = ""
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AntigravityClient(
        "http://gateway.test/v1",
        store=store,
        contexts=ContextManager(store),
        memories=memory,
        http_client=http_client,
    )

    async def no_wait(attempt: int) -> None:
        return None

    client._backoff = no_wait
    output = [token async for token in client.generate_stream(1, "hello")]
    await http_client.aclose()

    assert output == ["OK"]
    assert calls == 2
    assert store.get_messages(1)[-1]["content"] == "OK"


async def test_stream_is_not_retried_after_first_token(tmp_path) -> None:
    calls = 0

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            raise httpx.ReadError("connection dropped")

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=BrokenStream())

    store = StateStore(tmp_path / "state.db")
    memory = RAGMemoryEngine(store)
    memory.project_memory = ""
    memory.owner_profile = ""
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AntigravityClient(
        "http://gateway.test/v1",
        store=store,
        contexts=ContextManager(store),
        memories=memory,
        http_client=http_client,
    )

    received: list[str] = []
    with pytest.raises(GatewayUnavailable, match="после начала ответа"):
        async for token in client.generate_stream(1, "hello"):
            received.append(token)
    await http_client.aclose()

    assert received == ["partial"]
    assert calls == 1
    assert store.get_messages(1) == []


async def test_reasoning_effort_is_injected_in_payload(tmp_path) -> None:
    captured_payload = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_payload
        import json
        captured_payload = json.loads(request.content.decode("utf-8"))
        body = 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    store = StateStore(tmp_path / "state.db")
    memory = RAGMemoryEngine(store)
    memory.project_memory = ""
    memory.owner_profile = ""
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AntigravityClient(
        "http://gateway.test/v1",
        store=store,
        contexts=ContextManager(store),
        memories=memory,
        http_client=http_client,
    )

    client.set_user_model(1, "google-antigravity/gemini-3.7-flash")
    client.set_user_reasoning(1, "medium")

    tokens = [t async for t in client.generate_stream(1, "hello")]
    await http_client.aclose()

    assert tokens == ["Hi"]
    assert captured_payload is not None
    assert captured_payload["model"] == "google-antigravity/gemini-3.7-flash"
    assert captured_payload["reasoning_effort"] == "medium"
    assert captured_payload["stream"] is True
    assert captured_payload["max_tokens"] >= 8192


async def test_rate_limit_429_retries_with_backoff(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 2:
            return httpx.Response(
                429,
                text='{"error":{"message":"Resource has been exhausted (e.g. check quota)."}}'
            )
        body = 'data: {"choices":[{"delta":{"content":"Success after rate limit"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    store = StateStore(tmp_path / "state.db")
    memory = RAGMemoryEngine(store)
    memory.project_memory = ""
    memory.owner_profile = ""
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AntigravityClient(
        "http://gateway.test/v1",
        store=store,
        contexts=ContextManager(store),
        memories=memory,
        http_client=http_client,
    )

    async def no_wait(attempt: int) -> None:
        return None

    client._backoff = no_wait
    tokens = [t async for t in client.generate_stream(1, "hello")]
    await http_client.aclose()

    assert tokens == ["Success after rate limit"]
    assert calls == 3


async def test_502_thought_only_retries(tmp_path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(502, text="thought-only response without final output")
        body = 'data: {"choices":[{"delta":{"content":"Answer"}}]}\n\ndata: [DONE]\n\n'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    store = StateStore(tmp_path / "state.db")
    memory = RAGMemoryEngine(store)
    memory.project_memory = ""
    memory.owner_profile = ""
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AntigravityClient(
        "http://gateway.test/v1",
        store=store,
        contexts=ContextManager(store),
        memories=memory,
        http_client=http_client,
    )

    async def no_wait(attempt: int) -> None:
        return None

    client._backoff = no_wait
    tokens = [t async for t in client.generate_stream(1, "hello")]
    await http_client.aclose()

    assert tokens == ["Answer"]
    assert calls == 2


async def test_check_health_and_list_models(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [{"id": "google-antigravity/gemini-3.7-flash"}, {"id": "claude-opus"}]}
            )
        return httpx.Response(404)

    store = StateStore(tmp_path / "state.db")
    memory = RAGMemoryEngine(store)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AntigravityClient(
        "http://gateway.test/v1",
        store=store,
        contexts=ContextManager(store),
        memories=memory,
        http_client=http_client,
    )

    assert await client.check_omp_health() is True
    models = await client.list_omp_models()
    assert "google-antigravity/gemini-3.7-flash" in models
    assert "claude-opus" in models
    await http_client.aclose()
