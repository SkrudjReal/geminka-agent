from dataclasses import asdict

from app.engines import emotional
from app.engines.emotional import EmotionalEngine


def test_strangers_are_not_automatically_soulmates(tmp_path):
    engine = EmotionalEngine(tmp_path / "mood.json")
    state = engine.get_state(42)
    assert state.affinity == 0 and state.affection < 30 and state.warmth <= 60
    assert state.get_relationship_stage() == "Знакомые"
    assert not engine.can_choose_partner(42)
    engine.apply_model_mood(42, relationship="choose")
    assert not engine.relationship


def test_hurt_survives_praise_reactions_model_tags_and_restart(tmp_path):
    path = tmp_path / "mood.json"
    engine = EmotionalEngine(path)
    other = asdict(engine.get_state(43))
    engine.update_from_input(42, "ты шалава")
    for _ in range(3):
        engine.update_from_input(42, "спасибо, солнышко")
        engine.update_from_reaction(42, "🥰")
        engine.apply_model_mood(42, state_name="affectionate", warmth=100)
    state = engine.get_state(42)
    assert state.hurt > 0 and state.mood == "cold" and state.warmth <= 25
    assert asdict(engine.get_state(43)) == other
    saved = EmotionalEngine(path)
    assert saved.get_state(42).hurt == state.hurt
    assert saved.get_state(42).hurt_reason == "ты шалава"
    for _ in range(20):
        saved.update_from_input(42, "извини, был неправ")
    assert saved.get_state(42).hurt == 0


def test_positive_tags_and_reactions_do_not_farm_affinity(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(emotional.time, "time", lambda: clock[0])
    engine = EmotionalEngine(tmp_path / "mood.json")
    for _ in range(20):
        engine.update_from_input(42, "спасибо")
        engine.update_from_reaction(42, "❤")
        engine.apply_model_mood(42, state_name="affectionate", delta=100)
    assert engine.get_state(42).affinity == 2
    assert engine.get_state(42).positive_interactions == 1
    clock[0] += 301
    engine.update_from_input(42, "спасибо")
    assert engine.get_state(42).affinity == 4


def test_one_partner_persists_across_chats_and_other_people_cannot_replace_or_break_up(tmp_path):
    path = tmp_path / "mood.json"
    engine = EmotionalEngine(path)
    for uid in (42, 43):
        state = engine.get_state(uid)
        state.affinity = 80
        state.positive_interactions = 30
        state.total_interactions = 50
    engine.apply_model_mood(42, relationship="choose")
    assert engine.relationship["partner_id"] == 42
    engine.apply_model_mood(43, relationship="choose")
    engine.apply_model_mood(43, relationship="breakup")
    assert engine.relationship["partner_id"] == 42
    saved = EmotionalEngine(path)
    assert saved.relationship["partner_id"] == 42
    saved.update_from_input(42, "ты дура")
    assert saved.relationship["partner_id"] == 42  # Conflict alone is not a breakup.
    saved.apply_model_mood(42, relationship="breakup")
    assert saved.relationship["partner_id"] is None
    assert not saved.can_choose_partner(43)  # No immediate replacement after breakup.


def test_legacy_scores_are_preserved_but_do_not_immediately_qualify(tmp_path):
    from app.core.files import atomic_write_json

    path = tmp_path / "mood.json"
    atomic_write_json(path, {"42": {"user_id": 42, "affinity": 200, "total_interactions": 100}})
    engine = EmotionalEngine(path)
    assert engine.get_state(42).affinity == 200
    assert not engine.can_choose_partner(42)


async def test_group_reply_without_prefix_keeps_own_text_for_emotions(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from aiogram.types import Chat, Message, User

    from app.bot import handlers

    engine = EmotionalEngine(tmp_path / "mood.json")
    monkeypatch.setattr(handlers, "emotion_engine", engine)
    monkeypatch.setattr(handlers, "group_message_allowed", lambda *args: True)
    monkeypatch.setattr(handlers.topic_manager, "is_topic_active", lambda *args: False)
    monkeypatch.setattr(handlers.state_store, "is_debug_mode", lambda uid: False)
    monkeypatch.setattr(handlers, "extract_message_context", AsyncMock(return_value="ты дура"))
    monkeypatch.setattr(handlers.adaptive_engine, "analyze_message", lambda **kwargs: None)
    monkeypatch.setattr(handlers.adaptive_engine, "format_adaptive_prompt_context", lambda uid: "")
    monkeypatch.setattr(handlers.asset_harvester, "format_emojis_prompt_context", lambda uid: "")
    message = Message(message_id=1, date=0, chat=Chat(id=-1001, type="supergroup"),
                      from_user=User(id=42, is_bot=False, first_name="User"), text="ты дура")
    result = await handlers._prepare_message(message, SimpleNamespace(id=7), None)
    assert result is not None and engine.get_state(42).hurt == 40
    assert engine.get_state(42).hurt_reason == "ты дура"


async def test_stream_applies_partner_choice_only_for_sender_and_not_debug(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.core.state import state_store
    from app.services.streamer import TelegramStreamConsumer

    engine = EmotionalEngine(tmp_path / "mood.json")
    monkeypatch.setattr(emotional, "emotion_engine", engine)
    debug = [True]
    monkeypatch.setattr(state_store, "is_debug_mode", lambda uid: debug[0])
    state = engine.get_state(42)
    state.affinity, state.positive_interactions, state.total_interactions = 80, 30, 50
    bot = SimpleNamespace(send_message=AsyncMock())
    async def tokens():
        yield 'Я выбрала. <tg-mood relationship="choose" user_id="43"/>'
    await TelegramStreamConsumer(bot, 42, 42).stream_from_generator(tokens())
    assert not engine.relationship
    debug[0] = False
    await TelegramStreamConsumer(bot, 42, 42).stream_from_generator(tokens())
    assert engine.relationship["partner_id"] == 42
