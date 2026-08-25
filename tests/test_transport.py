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
