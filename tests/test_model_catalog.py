from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.bot import handlers
from app.core.context import ContextManager
from app.core.state import StateStore
from app.services.agy_cli import AgyCliClient, cli_model_name
from app.services.antigravity import AntigravityClient
from app.services.rag import RAGMemoryEngine


async def test_agy_catalog_parses_ids_labels_and_preserves_variants(monkeypatch):
    client = AgyCliClient()
    monkeypatch.setattr(client, "_sandbox_command", lambda args: ["fake-agy", *args])
    process = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(
        b"Fetching available models...\n"
        b"gemini-4-argon-medium\tGemini 4 Argon (Medium)\n"
        b"gemini-4-argon-high\tGemini 4 Argon (High)\n"
        b"gpt-oss-120b-medium\tGPT-OSS 120B (Medium)\n", b"")))
    monkeypatch.setattr("asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    assert await client.list_models() == ["gemini-4-argon-medium", "gemini-4-argon-high", "gpt-oss-120b-medium"]
    assert client.model_labels["gemini-4-argon-medium"] == "Gemini 4 Argon (Medium)"
    process.returncode = 1
    assert await client.list_models() == []
    assert client.model_labels == {}


@pytest.mark.parametrize(("model", "effort", "expected"), [
    ("google-antigravity/gemini-4-argon-medium", "high", "gemini-4-argon-high"),
    ("google-antigravity/claude-sonnet-5-5-low", "medium", "claude-sonnet-5-5-medium"),
    ("gemini-4-hydrogen-xhigh", "xhigh", "gemini-4-hydrogen-xhigh"),
    ("gemini-pro-orchestrator-g1-pro", "high", "gemini-pro-orchestrator-g1-pro"),
    ("gpt-oss-120b-medium", "high", "gpt-oss-120b-medium"),
])
def test_exact_model_ids_and_reasoning(model, effort, expected):
    assert cli_model_name(model, effort) == expected


async def test_live_catalog_is_used_for_selection_without_static_fallback(tmp_path):
    store = StateStore(tmp_path / "state.db")
    client = AntigravityClient(store=store, contexts=ContextManager(store), memories=RAGMemoryEngine(store))
    client._agy = SimpleNamespace(
        list_models=AsyncMock(return_value=["claude-sonnet-5-5-medium"]),
        model_labels={"claude-sonnet-5-5-medium": "Claude Sonnet 5.5 (Medium)"}, reset_user=Mock(),
    )
    models = await client.list_omp_models()
    assert models == ["google-antigravity/claude-sonnet-5-5"]
    client.set_user_model(42, models[0])
    assert client.get_user_model(42) == models[0]
    assert client.get_user_reasoning(42) == "medium"
    client._agy.list_models.return_value = []
    assert await client.list_omp_models() == []
    with pytest.raises(ValueError):
        client.set_user_model(42, models[0])


async def test_model_menu_and_stale_callbacks(monkeypatch):
    monkeypatch.setattr(handlers, "is_owner", lambda _: True)
    monkeypatch.setattr(handlers, "check_auth", lambda _: True)
    model = "google-antigravity/gemini-4-argon"
    client = SimpleNamespace(
        list_omp_models=AsyncMock(return_value=[model]), model_label=lambda _: "Gemini 4 Argon",
        get_user_model=lambda _: model, get_user_reasoning=lambda _: "medium", set_user_model=Mock(),
    )
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), text="/model", answer=AsyncMock())
    await handlers.cmd_model(message, client)
    buttons = message.answer.call_args.kwargs["reply_markup"].inline_keyboard
    assert len(buttons) == 1
    assert buttons[0][0].text == "✅ Gemini 4 Argon"
    assert len(buttons[0][0].callback_data.encode()) <= 64
    callback = SimpleNamespace(from_user=message.from_user, data="set_model:google-antigravity/claude-opus-4-6",
                               answer=AsyncMock())
    await handlers.process_set_model(callback, client)
    client.set_user_model.assert_not_called()
    assert callback.answer.call_args.kwargs["show_alert"]


async def test_failed_catalog_never_shows_old_menu(monkeypatch):
    monkeypatch.setattr(handlers, "is_owner", lambda _: True)
    monkeypatch.setattr(handlers, "check_auth", lambda _: True)
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), text="/model", answer=AsyncMock())
    await handlers.cmd_model(message, SimpleNamespace(list_omp_models=AsyncMock(return_value=[])))
    assert "reply_markup" not in message.answer.call_args.kwargs
    assert "AGY" in message.answer.call_args.args[0]


async def test_grouping_and_independent_reasoning_selection(tmp_path):
    store = StateStore(tmp_path / "state.db")
    client = AntigravityClient(store=store, contexts=ContextManager(store), memories=RAGMemoryEngine(store))
    raw = ["gemini-4-hydrogen-low", "gemini-4-hydrogen-medium", "gemini-4-hydrogen-high",
           "gemini-4-hydrogen-xhigh", "gpt-oss-120b-medium", "gemini-pro-orchestrator-g1-pro"]
    client._agy = SimpleNamespace(list_models=AsyncMock(return_value=raw),
        model_labels={name: name for name in raw}, reset_user=Mock())
    models = await client.list_omp_models()
    assert len(models) == 3
    store.set_preference(42, "reasoning", "high")
    client.set_user_model(42, models[0])
    assert client.get_user_reasoning(42) == "high"
    assert client.reasoning_levels(42) == ["low", "medium", "high", "xhigh"]
    client.set_user_reasoning(42, "xhigh")
    assert client.get_user_reasoning(42) == "xhigh"
    assert cli_model_name(client.get_user_model(42), client.get_user_reasoning(42), raw) == "gemini-4-hydrogen-xhigh"
    client.set_user_model(42, "gpt-oss-120b")
    assert client.reasoning_levels(42) == ["medium"]
    assert client.get_user_reasoning(42) == "medium"
    with pytest.raises(ValueError):
        client.set_user_reasoning(42, "high")
    client.set_user_model(42, models[-1])
    assert client.reasoning_levels(42) == []


async def test_reasoning_menu_and_invalid_level(monkeypatch):
    monkeypatch.setattr(handlers, "is_owner", lambda _: True)
    monkeypatch.setattr(handlers, "check_auth", lambda _: True)
    client = SimpleNamespace(list_omp_models=AsyncMock(return_value=["hydrogen"]),
        reasoning_levels=lambda _: ["low", "medium", "high", "xhigh"],
        get_user_reasoning=lambda _: "medium", get_user_model=lambda _: "hydrogen",
        model_label=lambda _: "Hydrogen", set_user_reasoning=Mock())
    message = SimpleNamespace(from_user=SimpleNamespace(id=42), text="/resoning", answer=AsyncMock())
    await handlers.cmd_reasoning(message, client)
    buttons = message.answer.call_args.kwargs["reply_markup"].inline_keyboard
    assert [row[0].callback_data for row in buttons] == ["set_reasoning:" + level for level in
                                                        ["low", "medium", "high", "xhigh"]]
    message.text = "/reasoning ultra"
    await handlers.cmd_reasoning(message, client)
    client.set_user_reasoning.assert_not_called()
    message.text = "/resoning xhigh"
    await handlers.cmd_reasoning(message, client)
    client.set_user_reasoning.assert_called_once_with(42, "xhigh")
