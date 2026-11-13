import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.filters import CommandObject
from aiogram.types import ReactionTypeEmoji

from app.bot import handlers
from app.core.state import state_store
from app.engines import emotional
from app.engines.emotional import EmotionalEngine
from app.services.streamer import TelegramStreamConsumer, strip_delivery_tags


def test_insult_overrides_praise_and_technical_keywords(tmp_path) -> None:
    engine = EmotionalEngine(state_file=tmp_path / "emotional_state.json")

    state = engine.update_from_input(1, "спасибо, ты дура")
    assert state.mood == "cold"
    assert state.warmth < 60

    engine2 = EmotionalEngine(state_file=tmp_path / "emotional_state2.json")
    state2 = engine2.update_from_input(2, "исправь код, ты дура")
    assert state2.mood == "cold"
    assert state2.warmth < 60


def test_teasing_triggers_pouty_mood(tmp_path) -> None:
    engine = EmotionalEngine(state_file=tmp_path / "emotional_state.json")
    state = engine.update_from_input(1, "бебебе")
    assert state.mood == "pouty"
    assert state.warmth < 96


def test_apply_model_mood_adjusts_state_and_warmth(tmp_path) -> None:
    engine = EmotionalEngine(state_file=tmp_path / "emotional_state.json")

    cold = engine.apply_model_mood(1, state_name="cold", delta=-60)
    assert cold is not None
    assert cold.mood == "cold"
    assert cold.warmth <= 25
    cold_warmth = cold.warmth

    pouty = engine.apply_model_mood(1, state_name="pouty", delta=15)
    assert pouty is not None
    assert pouty.mood == "pouty"
    assert pouty.warmth > cold_warmth

    assert engine.apply_model_mood(1, state_name="unknown") is None


def test_strip_delivery_tags_removes_tg_mood() -> None:
    assert strip_delivery_tags('ок. <tg-mood state="cold" delta="-60"/>') == "ок."


class _SentMessage:
    message_id = 1


class _FakeBot:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def send_message(self, *args, **kwargs):
        self.calls.append(kwargs)
        return _SentMessage()


@pytest.mark.asyncio
async def test_stream_applies_tg_mood_outside_code_and_respects_debug(tmp_path, monkeypatch) -> None:
    engine = EmotionalEngine(state_file=tmp_path / "emotional_state.json")
    monkeypatch.setattr(emotional, "emotion_engine", engine)
    debug_flag = {"on": False}
    monkeypatch.setattr(state_store, "is_debug_mode", lambda _uid: debug_flag["on"])

    bot = _FakeBot()
    consumer = TelegramStreamConsumer(bot=bot, chat_id=42, user_id=42)

    async def cold_tokens():
        yield 'ок. <tg-mood state="cold" delta="-80"/>'

    final_text = await consumer.stream_from_generator(cold_tokens())
    assert final_text == "ок."
    assert engine.get_state(42).mood == "cold"
    assert engine.get_state(42).warmth <= 25

    debug_flag["on"] = True
    consumer_debug = TelegramStreamConsumer(bot=bot, chat_id=42, user_id=42)

    async def warm_tokens():
        yield 'ладно <tg-mood state="affectionate" delta="80"/>'

    await consumer_debug.stream_from_generator(warm_tokens())
    assert engine.get_state(42).mood == "cold"

    debug_flag["on"] = False
    consumer_code = TelegramStreamConsumer(bot=bot, chat_id=42, user_id=42)

    async def code_tokens():
        yield 'пример: `<tg-mood state="affectionate" delta="80"/>`'

    await consumer_code.stream_from_generator(code_tokens())
    assert engine.get_state(42).mood == "cold"


def test_personal_mood_survives_reload_without_changing_another_user(tmp_path):
    path = tmp_path / "emotional_state.json"
    engine = EmotionalEngine(state_file=path)
    other_before = asdict(engine.get_state(43))
    engine.update_from_input(42, "ты дура")
    engine.apply_model_mood(42, state_name="cold", delta=-80)
    engine.update_from_reaction(42, "👎")
    assert asdict(engine.get_state(43)) == other_before
    assert engine.get_state(42) is not engine.get_state(43)
    assert "user_id=42" in engine.format_prompt_context(42)
    assert "user_id=43" in engine.format_prompt_context(43)
    saved = EmotionalEngine(state_file=path)
    assert asdict(saved.get_state(42)) == asdict(engine.get_state(42))
    assert asdict(saved.get_state(43)) == other_before
    before_reset = asdict(saved.get_state(42))
    saved.reset_state(43)
    assert asdict(saved.get_state(42)) == before_reset


def test_default_template_is_copied_and_stored_user_ids_are_canonical(tmp_path):
    path = tmp_path / "emotional_state.json"
    path.write_text(json.dumps({
        "default": {"user_id": 0, "mood": "playful", "warmth": 80},
        "42": {"user_id": 43, "mood": "cold", "warmth": 20},
        "-100123": {"user_id": -100123, "mood": "cold"},
    }))
    engine = EmotionalEngine(state_file=path)
    assert engine.get_state(42).user_id == 42
    assert engine.get_state(43).mood == "playful"
    engine.apply_model_mood(43, state_name="cold", delta=-50)
    assert engine.get_state(44).warmth == 80
    assert engine.states["default"].warmth == 80
    assert "-100123" not in engine.states
    for invalid in (0, -100123, "42", True):
        with pytest.raises(ValueError, match="user_id"):
            engine.get_state(invalid)


@pytest.mark.asyncio
async def test_group_model_tag_uses_sender_user_id_not_chat_id(tmp_path, monkeypatch):
    engine = EmotionalEngine(state_file=tmp_path / "emotional_state.json")
    monkeypatch.setattr(emotional, "emotion_engine", engine)
    monkeypatch.setattr(state_store, "is_debug_mode", lambda _uid: False)
    before = asdict(engine.get_state(43))

    async def tokens():
        yield 'ок. <tg-mood state="cold" delta="-80" user_id="43"/>'

    await TelegramStreamConsumer(bot=_FakeBot(), chat_id=-100123, user_id=42).stream_from_generator(tokens())
    assert engine.get_state(42).mood == "cold"
    assert asdict(engine.get_state(43)) == before
    assert "-100123" not in engine.states
    cold_before = asdict(engine.get_state(42))

    async def other_tokens():
        yield 'привет! <tg-mood state="affectionate" warmth="94"/>'

    await TelegramStreamConsumer(bot=_FakeBot(), chat_id=-100123, user_id=43).stream_from_generator(other_tokens())
    assert engine.get_state(43).mood == "affectionate"
    assert asdict(engine.get_state(42)) == cold_before


@pytest.mark.asyncio
async def test_mood_commands_and_reactions_are_personal(tmp_path, monkeypatch):
    engine = EmotionalEngine(state_file=tmp_path / "emotional_state.json")
    monkeypatch.setattr(handlers, "emotion_engine", engine)
    monkeypatch.setattr(handlers, "check_auth", lambda _uid: True)
    monkeypatch.setattr(state_store, "is_debug_mode", lambda _uid: False)
    output = AsyncMock()
    monkeypatch.setattr(handlers, "send_response", output)
    engine.apply_model_mood(42, state_name="cold", delta=-80)
    engine.apply_model_mood(43, state_name="cheerful", warmth=77)
    message = SimpleNamespace(from_user=SimpleNamespace(id=43), answer=AsyncMock())
    await handlers.cmd_mood(message, CommandObject(command="mood"))
    assert "77/100" in output.call_args.args[1]
    before = asdict(engine.get_state(42))
    await handlers.cmd_mood(message, CommandObject(command="mood", args="reset"))
    assert asdict(engine.get_state(42)) == before
    event = SimpleNamespace(user=SimpleNamespace(id=43), new_reaction=[ReactionTypeEmoji(emoji="👎")], message_id=1)
    await handlers.handle_message_reaction(event, _FakeBot())
    assert asdict(engine.get_state(42)) == before
    count = len(engine.states)
    event.user = None
    event.actor_chat = SimpleNamespace(id=-100123)
    await handlers.handle_message_reaction(event, _FakeBot())
    assert len(engine.states) == count
