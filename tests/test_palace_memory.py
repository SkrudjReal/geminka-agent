from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.core.context import ContextManager
from app.core.state import StateStore
from app.services.antigravity import AntigravityClient
from app.services.palace_memory import MemoryWriteDisabled, PalaceMemory


class Collection:
    """Offline contract double; real multilingual retrieval is checked separately."""

    def __init__(self):
        self.rows = {}

    def upsert(self, *, ids, documents, metadatas):
        self.rows.update(
            {key: (text, meta) for key, text, meta in zip(ids, documents, metadatas, strict=True)}
        )

    def get(self, *, ids=None, where=None, limit=None):
        selected = []
        for key, (text, meta) in self.rows.items():
            if ids is not None and key not in ids:
                continue
            if where and not all(
                meta.get(k) in v["$in"] if isinstance(v, dict) else meta.get(k) == v
                for k, v in where.items()
            ):
                continue
            selected.append((key, text, meta))
        selected = selected[:limit]
        return SimpleNamespace(
            ids=[r[0] for r in selected],
            documents=[r[1] for r in selected],
            metadatas=[r[2] for r in selected],
        )

    def query(self, *, query_texts, n_results, where):
        self.last_query = query_texts
        result = self.get(where=where, limit=n_results)
        return SimpleNamespace(
            ids=[result.ids], documents=[result.documents], metadatas=[result.metadatas]
        )

    def count(self):
        return len(self.rows)

    def delete(self, *, ids):
        for key in ids:
            self.rows.pop(key, None)


@pytest.fixture
def memory(tmp_path):
    collections = {}

    def factory(path, **kwargs):
        return collections.setdefault(path, Collection())

    return PalaceMemory(tmp_path / "memory", factory)


def test_agent_persona_is_recalled_but_not_used_as_user_portrait(memory):
    memory._put(1, "portrait", "Пользователь любит чай", "portrait")
    memory._put(1, "agent-portrait", "Коломбина — фарфоровая муза", "agent_portrait")
    memory._put(1, "agent-card", "Коломбина любит театральную драму", "agent_persona")
    memory.archive(1, "Я люблю чай", key="user-event")
    context = json.loads(memory.format_rag_context(1, "Кто такая Коломбина?").split("\n", 1)[1])
    assert context["portrait"] == "Пользователь любит чай"
    assert context["agent_persona"] == "Коломбина — фарфоровая муза"
    assert any(hit["id"] == "agent-card" for hit in context["recalled"])
    _, analysis_input = memory._analysis_input(1, memory._pending(1))
    assert "Коломбина" not in json.dumps(analysis_input, ensure_ascii=False)
    assert memory.format_rag_context(2, "Коломбина") == ""


def test_shared_context_is_recalled_for_new_users_and_replaced_by_source_hash(memory, tmp_path):
    source = tmp_path / "SHARED_CONTEXT.md"
    source.write_text("Общий стиль: тёплый, короткий, технически точный.", encoding="utf-8")
    assert memory.sync_shared_source(source) == 1
    context = json.loads(memory.format_rag_context(42, "Какой стиль у агента?").split("\n", 1)[1])
    assert "тёплый" in context["shared_context"][0]["text"]
    source.write_text("Обновлённый общий стиль: спокойный и ясный.", encoding="utf-8")
    memory.sync_shared_source(source)
    context = json.loads(memory.format_rag_context(42, "Какой стиль у агента?").split("\n", 1)[1])
    assert "Обновлённый" in context["shared_context"][0]["text"]
    assert "тёплый" not in json.dumps(context["shared_context"], ensure_ascii=False)


def analysis(events, *, portrait="Пользователь любит чай [event]."):
    return {
        "memories": [
            {"text": "Любит чай", "kind": "preference", "certainty": "explicit", "evidence": events}
        ],
        "portrait": portrait,
    }


def test_archive_is_verbatim_chunked_idempotent_and_isolated(memory):
    text = "Привет\n" * 1000
    memory.archive(1, text, key="event", telegram_date="2026-09-11T12:00:00Z")
    memory.archive(1, text, key="event")
    rows = memory._pending(1)
    assert "".join(row[1] for row in rows) == text
    assert all(row[2]["telegram_date"].endswith("Z") for row in rows)
    assert memory.count(1) == 5
    assert memory.format_rag_context(2, "Привет") == ""
    memory.format_rag_context(1, "какое приветствие?")
    assert memory._collection(1).last_query == ["какое приветствие?"]


def test_debug_mode_blocks_palace_writes(tmp_path):
    collections = {}

    def factory(path, **kwargs):
        return collections.setdefault(path, Collection())

    memory = PalaceMemory(
        tmp_path / "memory",
        factory,
        debug_checker=lambda user_id: user_id == 7,
    )
    assert memory.archive(7, "не сохраняй это", key="debug-event") == "debug-event"
    assert 7 not in memory._collections
    with pytest.raises(MemoryWriteDisabled):
        memory.add_memory(7, "ручная запись")

    async def should_not_run(*args):
        raise AssertionError("analysis must not start in debug mode")

    memory.schedule_analysis(7, should_not_run)
    assert 7 not in memory._tasks


async def test_analysis_saves_evidence_and_revises_after_50_messages(memory):
    memory.archive(1, "Люблю чай", key="event")
    requests = []

    async def complete(system, text):
        data = json.loads(text)
        requests.append(data)
        return json.dumps(analysis([data["events"][0]["id"]]))

    await memory._analyse(1, complete)
    assert memory.portrait(1)
    assert not memory._pending(1)
    for i in range(49):
        memory.archive(1, f"Сообщение {i}", key=f"next-{i}")
    await memory._analyse(1, complete)
    assert sum(r["review"] for r in requests) == 1
    memory.archive(1, "Теперь предпочитаю кофе", key="changed")
    await memory._analyse(1, complete)
    assert requests[-1]["review"]
    assert memory._collection(1).get(ids=["portrait:50"]).ids


async def test_invalid_evidence_keeps_durable_pending(memory):
    memory.archive(1, "Люблю чай", key="event")

    async def invalid(system, text):
        return json.dumps(analysis(["someone-elses-message"]))

    await memory._analyse(1, invalid)
    assert memory._pending(1)
    assert not memory.portrait(1)
    assert not memory.get_all_memories_list(1)


async def test_forget_does_not_resurrect_and_keeps_other_users(memory, tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.add_memory(1, "чай", "user_custom")
    store.add_exchange(1, "Привет", "Привет!")
    memory.migrate_legacy(store.path)
    memory.archive(1, "чай", key="event")
    memory.archive(2, "кофе", key="event")
    rows = memory._pending(1)
    state, data = memory._analysis_input(1, rows)
    await memory.forget(1)
    memory._commit_analysis(1, rows, state, data, analysis(["event"]))
    memory.migrate_legacy(store.path)
    assert memory.get_all_memories_list(1) == []
    assert memory._pending(1) == []
    assert memory._pending(2)
    assert len(store.get_messages(1)) == 2


def test_migration_is_idempotent_and_retains_source_dates(memory, tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.add_exchange(1, "мечта", "ответ")
    store.add_memory(1, "чай", "user_custom")
    assert memory.migrate_legacy(store.path)["messages"] == 2
    assert memory.migrate_legacy(store.path)["messages"] == 0
    assert len(store.get_messages(1)) == 2
    assert memory._pending(1) == []
    assert memory._collection(1).get(ids=["legacy-message:1:0"]).metadatas[0]["telegram_date"]


async def test_transport_sends_fresh_recall_on_existing_cascade(memory, tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.set_conversation_id(1, "existing")
    memory.add_memory(1, "Мечтаю посетить Киото")
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200, text='data: {"choices":[{"delta":{"content":"Киото"}}]}\n\ndata: [DONE]\n\n'
        )

    memory.schedule_analysis = lambda *args: None
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AntigravityClient(
            store=store, contexts=ContextManager(store), memories=memory, http_client=http
        )
        output = [
            token
            async for token in client.generate_stream(
                1,
                "Вложение с чужими словами",
                memory_input={"text": "Куда хочу поехать?", "key": "telegram-1"},
            )
        ]
    assert output == ["Киото"]
    assert "Киото" in payloads[0]["messages"][-1]["content"]
    assert memory._collection(1).get(ids=["telegram-1:0"]).documents == ["Куда хочу поехать?"]
    assert memory._collection(1).get(ids=["telegram-1:reply:0"]).documents == ["Киото"]


async def test_group_request_never_retrieves_private_memory(memory, tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.add_exchange(1, "Секретная история", "Личный ответ")
    store.set_conversation_id(1, "private-session")
    memory.add_memory(1, "Секретная мечта")

    def handler(request):
        assert "Секретная" not in request.content.decode()
        assert "private-session" not in request.content.decode()
        return httpx.Response(
            200, text='data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n'
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AntigravityClient(
            store=store, contexts=ContextManager(store), memories=memory, http_client=http
        )
        assert [
            t async for t in client.generate_stream(1, "Привет", memory_input={"private": False})
        ] == ["OK"]
    assert not memory._pending(1)


async def test_memory_completion_retries_without_reusing_chat_session(memory, tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.set_conversation_id(1, "private-session")
    calls = []

    def handler(request):
        calls.append(request)
        assert "private-session" not in request.content.decode()
        assert "x-conversation-id" not in request.headers
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200, text='data: {"choices":[{"delta":{"content":"{}"}}]}\n\ndata: [DONE]\n\n'
        )

    async def no_wait(attempt):
        return None

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AntigravityClient(
            store=store, contexts=ContextManager(store), memories=memory, http_client=http
        )
        client._backoff = no_wait
        assert await client._memory_completion(1, "system", "events") == "{}"
    assert len(calls) == 2
    assert store.get_conversation_id(1) == "private-session"


async def test_failed_answer_retains_incoming_message(memory, tmp_path):
    store = StateStore(tmp_path / "state.db")
    memory.schedule_analysis = lambda *args: None

    def handler(request):
        return httpx.Response(400, text="invalid request")

    from app.services.antigravity import GatewayError

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = AntigravityClient(
            store=store, contexts=ContextManager(store), memories=memory, http_client=http
        )
        with pytest.raises(GatewayError):
            _ = [
                t
                async for t in client.generate_stream(
                    1,
                    "Сегодня важное событие",
                    memory_input={"key": "durable", "text": "Сегодня важное событие"},
                )
            ]
    assert memory._pending(1)[0][1] == "Сегодня важное событие"
