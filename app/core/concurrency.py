"""Per-user concurrency controls for Telegram update processing."""

from __future__ import annotations

import asyncio
from collections import defaultdict


class UserLockRegistry:
    def __init__(self) -> None:
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    def get(self, user_id: int) -> asyncio.Lock:
        return self._locks[user_id]


user_locks = UserLockRegistry()
