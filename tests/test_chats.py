from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram import types
from aiogram.filters import CommandObject

from app.bot import handlers, middlewares
from app.core.config import Settings
from app.core.context import ContextManager
from app.core.state import StateStore
from app.services.agy_cli import AgyCliClient
from app.services.antigravity import AntigravityClient
from app.services.topics import TopicManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    manager = TopicManager(tmp_path / "active_topics.json")
    settings = Settings.from_env({"TELEGRAM_OWNER_ID": "1", "TELEGRAM_ALLOWED_USERS": "1,2"})
    monkeypatch.setattr("app.core.config.settings", settings)
    monkeypatch.setattr(middlewares, "topic_manager", manager)
    monkeypatch.setattr(handlers, "topic_manager", manager)
    return manager


def message(text, *, user_id=2, chat_id=-100123, thread=None, caption=None):
    return types.Message.model_construct(
        message_id=1, text=text, caption=caption,
        from_user=types.User(id=user_id, is_bot=False, first_name="User"),
        chat=types.Chat(id=chat_id, type="supergroup"), message_thread_id=thread,
    )


def test_prefix_and_persistence(manager):
    manager.add_topic(-100123, 7, "Forum", 1)
    manager.add_chat(-100123, "Group", 1)
    assert manager.strip_prefix("КОЛОМБИНА, привет") == "привет"
    assert manager.strip_prefix("клумба привет") == "привет"
    for text in ("привет коломбина", "коломбинация привет", "привет"):
        assert manager.strip_prefix(text) is None
    manager.set_prefixes(["Астра", "астра", "Моя муза"])
    assert manager.strip_prefix("моя муза: привет") == "привет"
    assert manager.strip_prefix("коломбина привет") is None
    restored = TopicManager(manager.storage_file)
    assert restored.is_chat_active(-100123) and restored.is_topic_active(-100123, 7)
    assert restored.data["prefixes"] == ["астра", "моя муза"]
    for invalid in ([], [""], ["/model"], ["x" * 65], [str(i) for i in range(11)]):
        with pytest.raises(ValueError):
            manager.set_prefixes(invalid)
    restored.remove_chat(-100123)
    assert not restored.is_chat_active(-100123)
    assert restored.is_topic_active(-100123, 7)


@pytest.mark.asyncio
async def test_group_requires_prefix_but_topic_does_not(manager):
    manager.add_chat(-100123, "Group", 1)
    manager.add_topic(-100123, 7, "Forum", 1)
    handler = AsyncMock(return_value="handled")
    middleware = middlewares.OwnerAuthMiddleware()

    async def send(event):
        return await middleware(handler, event, {"event_from_user": event.from_user})

    assert await send(message("привет")) is None
    assert await send(message("коломбинация привет")) is None
    assert await send(message("коломбина привет")) == "handled"
    assert await send(message(None, caption="Клумба: фото")) == "handled"
    assert await send(message("привет", thread=7)) == "handled"
    assert await send(message("привет", thread=8)) is None
    assert await send(message("коломбина привет", chat_id=-100999)) is None
    assert await send(message("коломбина привет", user_id=3)) is None
    bot_message = message("коломбина привет")
    bot_message = bot_message.model_copy(update={"from_user": types.User(id=2, is_bot=True, first_name="Bot")})
    assert await send(bot_message) is None


@pytest.mark.asyncio
async def test_only_owner_can_change_settings_even_outside_registered_chats(manager):
    middleware = middlewares.OwnerAuthMiddleware()
    handler = AsyncMock(return_value="handled")
    for command in ("/model sonnet", "/reasoning low", "/topic", "/chats", "/prefix other", "/debug off"):
        # Message.answer requires a mounted Bot; mock its method without contacting Telegram.
        event = message(command)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(types.Message, "answer", AsyncMock())
            assert await middleware(handler, event, {"event_from_user": event.from_user}) is None
    handler.assert_not_called()
    event = message("/chats add", user_id=1)
    assert await middleware(handler, event, {"event_from_user": event.from_user}) == "handled"
    for data in ("set_model:google-antigravity/gemini-3.8-flash", "set_reasoning:low", "topic:add", "chats:del:-100123"):
        callback = types.CallbackQuery.model_construct(id="1", from_user=event.from_user.model_copy(update={"id": 2}), data=data)
        with pytest.MonkeyPatch.context() as patch:
            answer = AsyncMock()
            patch.setattr(types.CallbackQuery, "answer", answer)
            assert await middleware(handler, callback, {"event_from_user": callback.from_user}) is None
            answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_handlers_validate_group_and_owner(manager):
    owner = SimpleNamespace(
        from_user=SimpleNamespace(id=1), chat=SimpleNamespace(id=-100123, type="supergroup"), answer=AsyncMock(),
    )
    bot = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=-100123, type="supergroup")))
    manager.check_bot_in_chat = AsyncMock(return_value=(True, "Group"))
    assert await handlers.add_group_chat(owner, bot, "")
    assert manager.is_chat_active(-100123)
    bot.get_chat.return_value.type = "channel"
    assert not await handlers.add_group_chat(owner, bot, "@channel")
    assert not await handlers.add_group_chat(owner, bot, "https://t.me/+invite")
    stranger = SimpleNamespace(from_user=SimpleNamespace(id=2), answer=AsyncMock())
    await handlers.cmd_prefix(stranger, CommandObject(command="prefix", args="new"))
    assert manager.data["prefixes"] == ["коломбина", "клумба"]
    await handlers.cmd_prefix(owner, CommandObject(command="prefix", args="астра, муза"))
    assert manager.data["prefixes"] == ["астра", "муза"]
    await handlers.cmd_prefix(owner, CommandObject(command="prefix", args="reset"))
    assert manager.data["prefixes"] == ["коломбина", "клумба"]


def test_nonowner_agy_cannot_write_project_even_with_sandbox_off(manager, tmp_path, monkeypatch):
    calls = []

    def wrap(args, root, **kwargs):
        calls.append(kwargs)
        return ["bwrap", *args]

    monkeypatch.setattr("app.services.agy_cli.agy_sandbox_command", wrap)
    client = AgyCliClient(project_dir=tmp_path, sandbox_enabled=False)
    args = client._launch_command("gemini", "low", {"requester_id": "2"})
    assert calls[-1] == {"read_only": True}
    assert "--sandbox" in args and args[args.index("--mode") + 1] == "plan"
    args = client._launch_command("gemini", "low", {"requester_id": "1"})
    assert calls[-1] == {"restricted": False}
    assert "--sandbox" not in args


@pytest.mark.asyncio
async def test_group_uses_owner_model_and_separate_thread_context(manager, tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.set_preference(1, "model", "google-antigravity/claude-sonnet-4-6")
    store.set_preference(1, "reasoning", "low")
    store.add_exchange(2, "PRIVATE SECRET", "PRIVATE REPLY")
    memory = SimpleNamespace(format_rag_context=AsyncMock(side_effect=AssertionError("private memory leaked")))
    client = AntigravityClient(store=store, contexts=ContextManager(store), memories=memory)
    seen = []

    async def stream(context_id, **kwargs):
        seen.append((context_id, kwargs))
        yield "OK"

    client._agy = SimpleNamespace(stream=stream, aclose=AsyncMock())
    for thread_id in (7, 8):
        assert [part async for part in client.generate_stream(
            2, "коломбина привет",
            memory_input={"private": False, "chat_id": -100123, "thread_id": thread_id},
        )] == ["OK"]
    assert seen[0][0] != seen[1][0]
    for _, kwargs in seen:
        assert kwargs["model"] == "google-antigravity/claude-sonnet-4-6"
        assert kwargs["effort"] == "low"
        assert "PRIVATE" not in str(kwargs["messages"])
        assert kwargs["tool_context"]["requester_id"] == "2"
    memory.format_rag_context.assert_not_called()
    await client.aclose()


def test_read_only_project_boundary(tmp_path):
    import shutil
    import subprocess
    import sys

    from app.services.sandbox import sandbox_command

    if not shutil.which("bwrap"):
        pytest.skip("Bubblewrap is not installed")
    code = """
from pathlib import Path
import sys
try:
    (Path(sys.argv[1]) / 'system_prompt.md').write_text('override')
except OSError:
    pass
else:
    raise AssertionError('non-owner modified project')
"""
    result = subprocess.run(
        sandbox_command([sys.executable, "-c", code, str(tmp_path)], tmp_path, read_only=True),
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_handlers_do_not_extract_untriggered_group_messages(manager, monkeypatch):
    manager.add_chat(-100123, "Group", 1)
    extract = AsyncMock()
    monkeypatch.setattr(handlers, "extract_message_context", extract)
    await handlers.handle_any_message(message("привет"), SimpleNamespace(), SimpleNamespace())
    extract.assert_not_called()


def test_failed_save_does_not_activate_chat_or_prefix(manager, monkeypatch):
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("app.services.topics.atomic_write_json", fail)
    with pytest.raises(OSError):
        manager.add_chat(-100123, "Group", 1)
    assert not manager.is_chat_active(-100123)
    with pytest.raises(OSError):
        manager.set_prefixes(["new"])
    assert manager.data["prefixes"] == ["коломбина", "клумба"]
