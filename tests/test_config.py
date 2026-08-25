from __future__ import annotations

import pytest

from app.core.config import ConfigurationError, Settings


def test_access_is_deny_by_default() -> None:
    settings = Settings.from_env({"TELEGRAM_BOT_TOKEN": "token"})
    with pytest.raises(ConfigurationError, match="deny-by-default"):
        settings.validate_startup()


def test_public_access_requires_explicit_opt_in() -> None:
    settings = Settings.from_env(
        {"TELEGRAM_BOT_TOKEN": "token", "TELEGRAM_ALLOW_ALL_USERS": "true"}
    )
    settings.validate_startup()
    assert settings.is_user_allowed(99)


def test_invalid_allowlist_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="positive numeric IDs"):
        Settings.from_env({"TELEGRAM_ALLOWED_USERS": "12, attacker"})


def test_model_alias_is_normalized() -> None:
    settings = Settings.from_env({"DEFAULT_MODEL": "opus"})
    assert settings.default_model == "google-antigravity/claude-opus-4-6"
