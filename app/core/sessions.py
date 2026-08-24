"""Session and State persistence manager for Geminka."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Set

from app.core import config

logger = logging.getLogger("geminka-sessions")


class SessionManager:
    """Encapsulates persistent conversation mapping between Telegram users and Antigravity conversation IDs."""

    def __init__(self, storage_file: Path = config.SESSIONS_FILE):
        self.storage_file = storage_file
        self._sessions: Dict[str, str] = {}
        self.load()

    def load(self) -> None:
        """Loads sessions from JSON storage."""
        if not self.storage_file.exists():
            self._sessions = {}
            return

        try:
            with open(self.storage_file, "r", encoding="utf-8") as f:
                self._sessions = json.load(f)
                logger.info(f"Loaded {len(self._sessions)} active dialogue sessions.")
        except json.JSONDecodeError as e:
            logger.warning(f"Malformed sessions file ({self.storage_file}): {e}. Starting fresh.")
            self._sessions = {}
        except OSError as e:
            logger.error(f"IO error reading sessions file: {e}")
            self._sessions = {}

    def save(self) -> None:
        """Persists sessions to JSON storage."""
        try:
            with open(self.storage_file, "w", encoding="utf-8") as f:
                json.dump(self._sessions, f, indent=2, ensure_ascii=False)
        except OSError as e:
            logger.error(f"IO error writing sessions file ({self.storage_file}): {e}")

    def get(self, user_id: int) -> Optional[str]:
        """Returns active conversation ID for a user."""
        return self._sessions.get(str(user_id))

    def set(self, user_id: int, conversation_id: str) -> None:
        """Assigns and persists conversation ID for a user."""
        self._sessions[str(user_id)] = conversation_id
        self.save()

    def remove(self, user_id: int) -> Optional[str]:
        """Removes and persists session reset for a user."""
        val = self._sessions.pop(str(user_id), None)
        if val is not None:
            self.save()
        return val

    def get_all_user_ids(self) -> List[int]:
        """Returns all registered user IDs as integers."""
        user_ids = []
        for k in self._sessions.keys():
            if str(k).isdigit():
                user_ids.append(int(k))
        return user_ids


session_manager = SessionManager()
