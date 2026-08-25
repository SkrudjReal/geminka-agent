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
    assert Settings.from_env({"DEFAULT_MODEL": "flash"}).default_model == "google-antigravity/gemini-3.7-flash"
    assert Settings.from_env({"DEFAULT_MODEL": "flash-3.7"}).default_model == "google-antigravity/gemini-3.7-flash"
    assert Settings.from_env({"DEFAULT_MODEL": "gemini"}).default_model == "google-antigravity/gemini-3.7-flash"
    assert Settings.from_env({"DEFAULT_MODEL": "3.7"}).default_model == "google-antigravity/gemini-3.7-flash"


def test_default_api_max_retries_is_five() -> None:
    settings = Settings.from_env({})
    assert settings.api_max_retries == 5
