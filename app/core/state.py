"""Small SQLite state store with per-user isolation and atomic writes."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.core import config


class StateStore:
    """Persist conversation, user overrides, and curated memories."""

    def __init__(self, path: Path = config.STATE_DB_FILE) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    user_id INTEGER PRIMARY KEY,
                    model TEXT,
                    reasoning TEXT,
                    conversation_id TEXT,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_user_id
                    ON conversation_messages(user_id, id);

                CREATE TABLE IF NOT EXISTS user_memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(user_id, category, content)
                );

                CREATE INDEX IF NOT EXISTS idx_memories_user_id
                    ON user_memories(user_id, id);
                """
            )

    def get_preferences(self, user_id: int) -> dict[str, str | None]:
        with self._lock, self._connect() as connection:
            # Ensure column exists for existing DBs
            try:
                connection.execute("ALTER TABLE user_preferences ADD COLUMN conversation_id TEXT")
            except sqlite3.OperationalError:
                pass
            row = connection.execute(
                "SELECT model, reasoning, conversation_id FROM user_preferences WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        if row is None:
            return {"model": None, "reasoning": None, "conversation_id": None}
        return {
            "model": row["model"],
            "reasoning": row["reasoning"],
            "conversation_id": row["conversation_id"] if "conversation_id" in row.keys() else None,
        }

    def get_conversation_id(self, user_id: int) -> str | None:
        prefs = self.get_preferences(user_id)
        return prefs.get("conversation_id")

    def set_conversation_id(self, user_id: int, conversation_id: str | None) -> None:
        with self._lock, self._connect() as connection:
            try:
                connection.execute("ALTER TABLE user_preferences ADD COLUMN conversation_id TEXT")
            except sqlite3.OperationalError:
                pass
            connection.execute(
                "INSERT INTO user_preferences(user_id, updated_at) VALUES(?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET updated_at = excluded.updated_at",
                (user_id, time.time()),
            )
            connection.execute(
                "UPDATE user_preferences SET conversation_id = ?, updated_at = ? WHERE user_id = ?",
                (conversation_id, time.time(), user_id),
            )

    def set_preference(self, user_id: int, field: str, value: str) -> None:
        statements = {
            "model": "UPDATE user_preferences SET model = ?, updated_at = ? WHERE user_id = ?",
            "reasoning": (
                "UPDATE user_preferences SET reasoning = ?, updated_at = ? WHERE user_id = ?"
            ),
            "conversation_id": (
                "UPDATE user_preferences SET conversation_id = ?, updated_at = ? WHERE user_id = ?"
            ),
        }
        if field not in statements:
            raise ValueError(f"Unsupported preference: {field}")
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO user_preferences(user_id, updated_at) VALUES(?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET updated_at = excluded.updated_at",
                (user_id, time.time()),
            )
            connection.execute(
                statements[field],
                (value, time.time(), user_id),
            )

    def clear_preferences(self, user_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,))

    def get_messages(self, user_id: int, *, limit: int = 30) -> list[dict[str, str]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT role, content FROM conversation_messages "
                "WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [
            {"role": row["role"], "content": row["content"]}
            for row in reversed(rows)
        ]

    def add_exchange(
        self,
        user_id: int,
        user_content: str,
        assistant_content: str,
        *,
        retain_messages: int = 200,
    ) -> None:
        now = time.time()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT INTO conversation_messages(user_id, role, content, created_at) "
                "VALUES(?, ?, ?, ?)",
                [
                    (user_id, "user", user_content, now),
                    (user_id, "assistant", assistant_content, now),
                ],
            )
            connection.execute(
                "DELETE FROM conversation_messages WHERE user_id = ? AND id NOT IN ("
                "SELECT id FROM conversation_messages WHERE user_id = ? "
                "ORDER BY id DESC LIMIT ?)",
                (user_id, user_id, retain_messages),
            )
            connection.commit()

    def clear_messages(self, user_id: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM conversation_messages WHERE user_id = ?", (user_id,))

    def list_memories(self, user_id: int) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT id, category, content, created_at FROM user_memories "
                "WHERE user_id = ? ORDER BY id",
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_memory(self, user_id: int, content: str, category: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO user_memories(user_id, category, content, created_at) "
                "VALUES(?, ?, ?, ?)",
                (user_id, category, content, time.time()),
            )
            return cursor.rowcount > 0

    def delete_memory(self, user_id: int, memory_id: int) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM user_memories WHERE user_id = ? AND id = ?",
                (user_id, memory_id),
            )
            return cursor.rowcount > 0


state_store = StateStore()
