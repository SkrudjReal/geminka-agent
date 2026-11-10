"""Coalesce updates for one user/chat/thread without cancelling a live AGY turn."""

import asyncio
from dataclasses import dataclass, field
from typing import Any


class StreamRestart:
    """Discard a superseded preview, reusing the same Telegram message."""


STREAM_RESTART = StreamRestart()


@dataclass
class PendingTurn:
    items: list[Any] = field(default_factory=list)
    prepared: list[Any] = field(default_factory=list)
    accepting: bool = True
    task: asyncio.Task | None = None

    @property
    def revision(self) -> int:
        return len(self.items)


class TurnBatches:
    def __init__(self):
        self.pending: dict[tuple[int, int, int | None], PendingTurn] = {}
        self.tasks = set()

    async def submit(self, key, item, run):
        batch = self.pending.get(key)
        if batch is None or not batch.accepting:
            batch = PendingTurn()
            self.pending[key] = batch

            async def worker():
                try:
                    await run(batch)
                finally:
                    if self.pending.get(key) is batch:
                        self.pending.pop(key, None)

            batch.task = asyncio.create_task(worker())
            self.tasks.add(batch.task)
            batch.task.add_done_callback(self.tasks.discard)
        batch.items.append(item)
        await asyncio.shield(batch.task)

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        self.tasks.clear()


turn_batches = TurnBatches()
