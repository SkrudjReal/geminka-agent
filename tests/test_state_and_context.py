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
