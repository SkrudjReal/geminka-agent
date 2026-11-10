"""Compatibility import for the SQLite FTS5/BM25 memory engine."""

from app.core.state import StateStore, state_store
from app.services.sqlite_memory import MemoryRejected, SQLiteMemory, memory


class RAGMemoryEngine(SQLiteMemory):
    def __init__(self, store: StateStore = state_store):
        super().__init__(store.path.parent / "memory.db", store=store)


rag_engine = memory

__all__ = ["MemoryRejected", "RAGMemoryEngine", "rag_engine"]
