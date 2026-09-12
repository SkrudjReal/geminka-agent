from __future__ import annotations

from app.core.context import ContextManager
from app.core.state import StateStore


def test_state_is_isolated_by_user(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.add_exchange(1, "one", "reply one")
    store.add_exchange(2, "two", "reply two")

    assert store.get_messages(1) == [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "reply one"},
    ]
    assert all("two" not in item["content"] for item in store.get_messages(1))


def test_history_character_budget_is_strict(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    store.add_exchange(1, "u" * 40, "a" * 40)
    context = ContextManager(store, max_chars=25, max_message_chars=100)

    payload = context.build_payload_messages(1, "current", "system")
    history = payload[1:-1]

    assert sum(len(item["content"]) for item in history) + len(payload[-1]["content"]) <= 25
    assert payload[-1] == {"role": "user", "content": "current"}


def test_preferences_survive_new_store_instance(tmp_path) -> None:
    path = tmp_path / "state.db"
    StateStore(path).set_preference(7, "reasoning", "high")
    assert StateStore(path).get_preferences(7)["reasoning"] == "high"


def test_conversation_id_storage_and_switch(tmp_path) -> None:
    path = tmp_path / "state.db"
    store = StateStore(path)
    assert store.get_conversation_id(42) is None
    store.set_conversation_id(42, "2ccc81af-14a1-422d-91b8-7085fe98c1df")
    assert store.get_conversation_id(42) == "2ccc81af-14a1-422d-91b8-7085fe98c1df"

    store.set_conversation_id(42, None)
    assert store.get_conversation_id(42) is None


def test_debug_mode_persists_and_blocks_context_writes(tmp_path) -> None:
    path = tmp_path / "state.db"
    store = StateStore(path)
    context = ContextManager(store)

    context.add_exchange(7, "before", "reply")
    assert len(store.get_messages(7)) == 2

    store.set_debug_mode(7, True)
    assert store.is_debug_mode(7)
    assert StateStore(path).is_debug_mode(7)
    context.add_exchange(7, "during", "not saved")
    assert [item["content"] for item in store.get_messages(7)] == ["before", "reply"]

    store.set_debug_mode(7, False)
    context.add_exchange(7, "after", "saved")
    assert [item["content"] for item in store.get_messages(7)][-2:] == ["after", "saved"]


def test_import_messages(tmp_path) -> None:
    path = tmp_path / "state.db"
    store = StateStore(path)
    store.add_exchange(1, "old user", "old bot")

    messages_to_import = [
        ("user", "first message"),
        ("assistant", "first reply"),
        ("user", "second message"),
        ("assistant", "second reply"),
    ]
    imported = store.import_messages(1, messages_to_import)
    assert imported == 4

    history = store.get_messages(1)
    assert len(history) == 4
    assert history[0]["content"] == "first message"
    assert history[-1]["content"] == "second reply"
