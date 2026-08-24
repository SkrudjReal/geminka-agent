"""Configuration module for Geminka Antigravity Telegram Bot."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Telegram Configuration
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ALLOWED_USERS_RAW = os.getenv("TELEGRAM_ALLOWED_USERS", "").strip()
ALLOWED_USERS = (
    [int(u.strip()) for u in ALLOWED_USERS_RAW.split(",") if u.strip().isdigit()]
    if ALLOWED_USERS_RAW
    else []
)

# OMP Antigravity Gateway Settings (from hermes-antigravity-pipeline)
OMP_BASE_URL = os.getenv("OMP_BASE_URL", "http://127.0.0.1:4000/v1").rstrip("/")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "google-antigravity/gemini-3.7-flash").strip()
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "medium").strip()
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "8192"))
API_MAX_RETRIES = int(os.getenv("API_MAX_RETRIES", "5"))

# Antigravity Local Fallback Paths
LINUX_LS_BIN = Path(os.path.expanduser("~/.antigravity-ide-server/bin/2.5.5-ecfbad74d93962fc8ca485d93ab9b4f3d4cb6cf8/extensions/antigravity/bin/language_server_linux_x64"))
WIN_LS_BIN = Path("/mnt/c/Users/velunae/AppData/Local/Programs/Antigravity/resources/bin/language_server.exe")

LINUX_BRAIN_DIR = Path(os.path.expanduser("~/.gemini/antigravity-ide/brain"))
WIN_BRAIN_DIR = Path("/mnt/c/Users/velunae/.gemini/antigravity-ide/brain")

SESSIONS_FILE = BASE_DIR / "sessions.json"
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
PROMPT_FILE = BASE_DIR / "system_prompt.txt"

SYSTEM_PROMPT_DEFAULT = """Ты — Geminka (Columbina), живая, умная, эмоциональная девушка-ассистент на базе Antigravity."""


def get_system_prompt() -> str:
    """Dynamically reloads system prompt from file on every call without restarting process."""
    if PROMPT_FILE.exists():
        try:
            return PROMPT_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return SYSTEM_PROMPT_DEFAULT
