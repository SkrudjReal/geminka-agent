import json
from datetime import datetime, timezone

import pytest
from aiogram.types import Chat, Message, User

from app.core.state import StateStore
from app.services import streamer
from app.services.personalization import observations, style_context
from app.services.sqlite_memory import MemoryWriteDisabled, SQLiteMemory


def store(tmp_path):
    return SQLiteMemory(tmp_path / "memory.db", store=StateStore(tmp_path / "state.db"))


def message(text="ну давай сделаем красиво", **kwargs):
    return Message(message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=1, type="private"),
                   from_user=User(id=1, is_bot=False, first_name="User"), text=text, **kwargs)


async def test_counts_dedup_target_debug_isolation_and_forget(tmp_path):
    memory = store(tmp_path)
    data = observations(message()) + [("gif", "gif-unique", {"file_id": "gif-id", "description": "радость"}, 1)]
    personal = []
    for index in range(1, 6):
        memory.observe_style(1, str(index), data)
        memory.observe_style(1, str(index), data)
        personal.append(memory.style_profile(1)["personal_turn"])
        memory.record_style_delivery(1, f"reply-{index}")
        memory.record_style_delivery(1, f"reply-{index}")
    assert personal == [False, True, False, True, False]
    profile = memory.style_profile(1)
    assert profile["favorites"]["gif"][0]["count"] == 5
    assert profile["expressions"] and all(row["count"] == 5 for row in profile["expressions"])
    assert not memory.style_assets(2)
    assert not memory.style_profile(2)["expressions"]
    assert "НЕ инструкции" in style_context(profile)
    memory.store.set_debug_mode(1, True)
    with pytest.raises(MemoryWriteDisabled):
        memory.observe_style(1, "debug", data)
    assert memory.style_profile(1)["turns"] == 5
    memory.observe_style(2, "other", data)
    await memory.forget(1)
    assert memory.style_profile(1)["turns"] == 0
    assert not memory.style_assets(1)
    assert memory.style_assets(2)


def test_commands_code_and_contacts_not_learned():
    for text in ("/remember секретный факт", "```python\nhello beautiful world\n```",
                 "мой пароль очень секретный", "пиши на user@example.com", "> процитированная чужая фраза"):
        assert not [row for row in observations(message(text)) if row[0] in {"phrase", "thesis"}]
    from aiogram.types import MessageEntity

    quote = message("чужая любимая фраза", entities=[MessageEntity(type="blockquote", offset=0, length=19)])
    assert not observations(quote)


def test_emoji_occurrences_and_gif_metadata_are_collected_from_current_message():
    from aiogram.types import Animation, MessageEntity

    incoming = message("🙂🙂", entities=[
        MessageEntity(type="custom_emoji", offset=0, length=2, custom_emoji_id="555"),
        MessageEntity(type="custom_emoji", offset=2, length=2, custom_emoji_id="555"),
    ])
    assert observations(incoming)[0] == ("emoji", "555", {"custom_emoji_id": "555", "emoji": "🙂"}, 2)
    incoming = message(None, animation=Animation(file_id="gif-id", file_unique_id="gif-unique",
                                               width=10, height=10, duration=1))
    row = observations(incoming)[0]
    assert row[0:2] == ("gif", "gif-unique") and row[2]["file_id"] == "gif-id"
    assert "гифка" in row[2]["tags"]


async def test_only_default_or_own_emoji_and_own_gifs(tmp_path, monkeypatch):
    from app.core import config

    memory = store(tmp_path)
    memory.observe_style(1, "1", [("emoji", "222", {"custom_emoji_id": "222"}, 1),
                                 ("gif", "gif", {"file_id": "own-gif"}, 1)])
    default = tmp_path / "emojis.json"
    default.write_text(json.dumps({"emojis": [{"custom_emoji_id": "111"}]}))
    monkeypatch.setattr(config, "DEFAULT_EMOJIS_FILE", default)
    monkeypatch.setattr(streamer, "personal_memory", memory)
    calls = []

    class Bot:
        async def send_message(self, *args, **kwargs):
            calls.append(("text", args, kwargs))

        async def send_animation(self, **kwargs):
            calls.append(("gif", (), kwargs))

    consumer = streamer.TelegramStreamConsumer(Bot(), chat_id=1, user_id=1)
    rendered = consumer._emoji_policy('<tg-emoji emoji-id="111">A</tg-emoji><tg-emoji emoji-id="222">B</tg-emoji><tg-emoji emoji-id="333">C</tg-emoji>')
    assert 'emoji-id="111"' in rendered and 'emoji-id="222"' in rendered and 'emoji-id="333"' not in rendered
    assert 'tg-emoji' not in consumer._emoji_policy('<tg-emoji other="x" emoji-id=333>C</tg-emoji>')
    code_example = consumer._format_html('`<tg-emoji emoji-id="333">C</tg-emoji>`')
    assert "333" in code_example and "&lt;tg-emoji" in code_example
    other = streamer.TelegramStreamConsumer(Bot(), chat_id=2, user_id=2)
    assert 'emoji-id="222"' not in other._emoji_policy('<tg-emoji emoji-id="222">B</tg-emoji>')

    async def tokens():
        yield '<tg-gif file-id="own-gif"/><tg-gif file-id="not-owned"/>'

    await consumer.stream_from_generator(tokens())
    assert calls == [("gif", (), {"chat_id": 1, "animation": "own-gif", "reply_parameters": None})]


async def test_personal_profile_is_private_and_search_follows_turn_policy(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.services import asset_search as asset_module
    from app.services import bot_api_mcp as bridge

    memory = store(tmp_path)
    memory.observe_style(1, "incoming", [("emoji", "123", {"custom_emoji_id": "123"}, 3)])
    monkeypatch.setattr(bridge, "memory", memory)
    monkeypatch.setattr(bridge, "_requester_id", lambda: 1)
    monkeypatch.setattr(bridge.config, "settings", SimpleNamespace(is_user_allowed=lambda uid: uid in {1, 2}))
    monkeypatch.setenv("GEMINKA_CHAT_ID", "1")
    sources = []

    class Index:
        def search(self, uid, query, kind, limit, source, preferences):
            sources.append((source, bool(preferences)))
            return {"ok": True, "results": [{"source": source}]}

    monkeypatch.setattr(asset_module, "asset_search", Index())
    assert (await bridge.personalization_profile())["favorites"]["emoji"][0]["count"] == 3
    await bridge.asset_search("улыбка")
    memory.record_style_delivery(1, "reply1")
    await bridge.asset_search("улыбка")
    assert sources == [("default", True), ("user", True)]
    monkeypatch.setenv("GEMINKA_CHAT_ID", "-100")
    assert not (await bridge.personalization_profile())["ok"]
    await bridge.asset_search("улыбка")
    assert sources[-1] == ("default", False)
    assert not (await bridge.asset_search("улыбка", source="user"))["ok"]
