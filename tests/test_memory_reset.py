import json
from pathlib import Path

import pytest

from app.core.state import StateStore
from scripts.reset_private_memory import reset


def project(tmp_path):
    (tmp_path / "pyproject.toml").touch()
    (tmp_path / "app/core").mkdir(parents=True)
    (tmp_path / "app/core/state.py").touch()
    store = StateStore(tmp_path / "data/state.db")
    store.add_exchange(1, "old secret", "old reply")
    store.add_memory(1, "old fact", "fact")
    store.set_preference(1, "model", "gemini")
    store.set_conversation_id(1, "old-conversation")
    return store


def test_explicit_wipe_keeps_auth_settings_and_catalogs(tmp_path):
    store = project(tmp_path)
    runtime = tmp_path / "data/agy_sandbox"
    (runtime / "log").mkdir(parents=True)
    (runtime / "log/old.log").write_text("secret")
    (runtime / "cli.log").symlink_to("log/old.log")
    (runtime / "antigravity-oauth-token").write_text("fake-auth")
    (runtime / "settings.json").write_text("{}")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory/old.db").write_text("secret")
    (tmp_path / "data/emotional_state.json").write_text('{"1":{"name":"old"}}')
    (tmp_path / "data/user_assets.json").write_text(json.dumps({"stickers": ["keep"], "user_preferences": {"1": "old"}}))
    reset(tmp_path)
    assert not (tmp_path / "memory").exists()
    assert not (runtime / "log").exists()
    assert not (runtime / "cli.log").is_symlink()
    assert (runtime / "antigravity-oauth-token").read_text() == "fake-auth"
    assert (runtime / "settings.json").exists()
    assert not store.get_messages(1) and not store.list_memories(1)
    assert store.get_conversation_id(1) is None
    assert store.get_preferences(1)["model"] == "gemini"
    assert json.loads((tmp_path / "data/emotional_state.json").read_text()) == {}
    assert json.loads((tmp_path / "data/user_assets.json").read_text())["stickers"] == ["keep"]


def test_wipe_rejects_external_target_before_deleting_anything(tmp_path):
    project(tmp_path)
    (tmp_path / "memory").symlink_to(Path("/tmp"), target_is_directory=True)
    with pytest.raises(ValueError, match="Refusing"):
        reset(tmp_path)
    assert (tmp_path / "memory").is_symlink()
