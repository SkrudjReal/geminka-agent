"""Validated runtime configuration for Geminka."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")

MODEL_ALIASES = {
    "flash": "google-antigravity/gemini-3.7-flash",
    "gemini": "google-antigravity/gemini-3.7-flash",
    "sonnet": "google-antigravity/claude-sonnet-4-5",
    "opus": "google-antigravity/claude-opus-4-6",
}


class ConfigurationError(ValueError):
    """Raised when runtime configuration is unsafe or malformed."""


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _parse_int(
    value: str,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _parse_allowed_users(raw: str) -> tuple[int, ...]:
    if not raw.strip():
        return ()
    users: list[int] = []
    for item in raw.split(","):
        value = item.strip()
        if not value.isdigit() or int(value) <= 0:
            raise ConfigurationError(
                "TELEGRAM_ALLOWED_USERS must contain positive numeric IDs separated by commas"
            )
        user_id = int(value)
        if user_id not in users:
            users.append(user_id)
    return tuple(users)


def normalize_model_name(value: str) -> str:
    model = value.strip()
    return MODEL_ALIASES.get(model.lower(), model)


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    allowed_users: tuple[int, ...]
    allow_all_users: bool
    owner_user_id: int | None
    omp_base_url: str
    omp_api_key: str
    default_model: str
    reasoning_effort: str
    max_output_tokens: int
    api_max_retries: int
    request_timeout_seconds: int
    max_input_chars: int
    max_download_bytes: int
    startup_notification: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if env is None else env
        allowed_users = _parse_allowed_users(source.get("TELEGRAM_ALLOWED_USERS", ""))
        owner_raw = source.get("TELEGRAM_OWNER_ID", "").strip()
        if owner_raw:
            if not owner_raw.isdigit() or int(owner_raw) <= 0:
                raise ConfigurationError("TELEGRAM_OWNER_ID must be a positive numeric ID")
            owner_user_id: int | None = int(owner_raw)
        else:
            owner_user_id = allowed_users[0] if allowed_users else None

        reasoning = source.get("REASONING_EFFORT", "medium").strip().lower()
        if reasoning not in {"low", "medium", "high"}:
            raise ConfigurationError("REASONING_EFFORT must be low, medium, or high")

        base_url = source.get("OMP_BASE_URL", "http://127.0.0.1:4000/v1").strip().rstrip("/")
        parsed_url = urlparse(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ConfigurationError("OMP_BASE_URL must be an absolute http(s) URL")

        return cls(
            bot_token=source.get("TELEGRAM_BOT_TOKEN", "").strip(),
            allowed_users=allowed_users,
            allow_all_users=_parse_bool(
                source.get("TELEGRAM_ALLOW_ALL_USERS", "false"),
                name="TELEGRAM_ALLOW_ALL_USERS",
            ),
            owner_user_id=owner_user_id,
            omp_base_url=base_url,
            omp_api_key=source.get("OMP_API_KEY", "").strip(),
            default_model=normalize_model_name(
                source.get("DEFAULT_MODEL", "google-antigravity/gemini-3.7-flash")
            ),
            reasoning_effort=reasoning,
            max_output_tokens=_parse_int(
                source.get("MAX_OUTPUT_TOKENS", "8192"),
                name="MAX_OUTPUT_TOKENS",
                minimum=256,
                maximum=65536,
            ),
            api_max_retries=_parse_int(
                source.get("API_MAX_RETRIES", "3"),
                name="API_MAX_RETRIES",
                minimum=0,
                maximum=10,
            ),
            request_timeout_seconds=_parse_int(
                source.get("REQUEST_TIMEOUT_SECONDS", "180"),
                name="REQUEST_TIMEOUT_SECONDS",
                minimum=5,
                maximum=600,
            ),
            max_input_chars=_parse_int(
                source.get("MAX_INPUT_CHARS", "24000"),
                name="MAX_INPUT_CHARS",
                minimum=1000,
                maximum=200000,
            ),
            max_download_bytes=_parse_int(
                source.get("MAX_DOWNLOAD_BYTES", "10485760"),
                name="MAX_DOWNLOAD_BYTES",
                minimum=1024,
                maximum=52428800,
            ),
            startup_notification=_parse_bool(
                source.get("STARTUP_NOTIFICATION", "false"),
                name="STARTUP_NOTIFICATION",
            ),
        )

    def validate_startup(self) -> None:
        if not self.bot_token:
            raise ConfigurationError("TELEGRAM_BOT_TOKEN is required")
        if not self.allowed_users and not self.allow_all_users:
            raise ConfigurationError(
                "Access is deny-by-default: set TELEGRAM_ALLOWED_USERS or explicitly set "
                "TELEGRAM_ALLOW_ALL_USERS=true"
            )
        if self.owner_user_id is not None and self.allowed_users:
            if self.owner_user_id not in self.allowed_users:
                raise ConfigurationError("TELEGRAM_OWNER_ID must be present in TELEGRAM_ALLOWED_USERS")

    def is_user_allowed(self, user_id: int) -> bool:
        return self.allow_all_users or user_id in self.allowed_users


settings = Settings.from_env()

DATA_DIR = BASE_DIR / "data"
MEMORIES_DIR = BASE_DIR / "memories"
DOWNLOADS_DIR = BASE_DIR / "downloads"
STATE_DB_FILE = DATA_DIR / "state.db"
STICKERS_FILE = DATA_DIR / "bot_stickers.json"
USER_ASSETS_FILE = DATA_DIR / "user_assets.json"
CUSTOM_EMOJIS_FILE = DATA_DIR / "custom_emojis.json"
EMOTIONAL_STATE_FILE = DATA_DIR / "emotional_state.json"
ADAPTIVE_STATE_FILE = DATA_DIR / "adaptive_profiles.json"
PROMPT_FILE = BASE_DIR / "system_prompt.txt"

SYSTEM_PROMPT_DEFAULT = (
    "Ты — Geminka (Columbina), живая, умная, эмоциональная девушка-ассистент."
)

# Compatibility aliases. New code should prefer ``settings``.
BOT_TOKEN = settings.bot_token
ALLOWED_USERS = settings.allowed_users
ALLOW_ALL_USERS = settings.allow_all_users
OMP_BASE_URL = settings.omp_base_url
DEFAULT_MODEL = settings.default_model
REASONING_EFFORT = settings.reasoning_effort
MAX_OUTPUT_TOKENS = settings.max_output_tokens
API_MAX_RETRIES = settings.api_max_retries


def ensure_runtime_dirs() -> None:
    for directory in (DATA_DIR, MEMORIES_DIR, DOWNLOADS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def get_system_prompt() -> str:
    """Reload the editable system prompt without restarting the process."""
    try:
        prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return SYSTEM_PROMPT_DEFAULT
    return prompt or SYSTEM_PROMPT_DEFAULT
