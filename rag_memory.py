"""Hermes-style RAG Memory Engine for Geminka.

Implements Hermes Agent memory architecture:
- Dual-file structure: USER.md (Profile & Preferences) and MEMORY.md (Knowledge & Notes)
- Atomic sections delimited by '§'
- Hybrid BM25 & TF-IDF semantic retrieval over indexed memory chunks
- Dynamic Top-K injection into prompt
- Strict isolation: manages its own memories locally in geminka-agent/memories/
"""

import json
import logging
import math
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config

logger = logging.getLogger(__name__)

MEMORIES_DIR = config.BASE_DIR / "memories"
MEMORIES_DIR.mkdir(parents=True, exist_ok=True)

USER_MD_FILE = MEMORIES_DIR / "USER.md"
MEMORY_MD_FILE = MEMORIES_DIR / "MEMORY.md"
FACTS_JSON_FILE = MEMORIES_DIR / "facts.json"

USER_CHAR_LIMIT = 2000
MEMORY_CHAR_LIMIT = 3000


def tokenize(text: str) -> List[str]:
    """Tokenizes and normalizes text for BM25 / TF-IDF retrieval."""
    cleaned = re.sub(r"[^\w\sа-яА-ЯёЁa-zA-Z0-9]", " ", text.lower())
    tokens = [t for t in cleaned.split() if len(t) > 1]
    return tokens


class MemoryChunk:
    def __init__(self, content: str, source: str, category: str = "general"):
        self.content = content.strip()
        self.source = source
        self.category = category  # "user_profile" or "project_knowledge"
        self.tokens = tokenize(self.content)
        self.term_freq = Counter(self.tokens)

    def __repr__(self):
        return f"<MemoryChunk category={self.category} len={len(self.content)}>"


class RAGMemoryEngine:
    def __init__(self):
        self.chunks: List[MemoryChunk] = []
        self.doc_freq: Counter = Counter()
        self.total_docs: int = 0
        self.reload_all_memories()

    def reload_all_memories(self) -> None:
        """Reloads and indexes all local memory files."""
        self.chunks.clear()
        self.doc_freq.clear()

        # 1. Load USER.md (User Profile & Preferences)
        if USER_MD_FILE.exists():
            self._load_markdown_file(USER_MD_FILE, category="user_profile")

        # 2. Load MEMORY.md (Project & World Knowledge)
        if MEMORY_MD_FILE.exists():
            self._load_markdown_file(MEMORY_MD_FILE, category="project_knowledge")

        # 3. Load dynamic facts.json
        if FACTS_JSON_FILE.exists():
            try:
                with open(FACTS_JSON_FILE, "r", encoding="utf-8") as f:
                    facts = json.load(f)
                    for item in facts:
                        text = item.get("text", "")
                        cat = item.get("category", "project_knowledge")
                        if text:
                            self.chunks.append(MemoryChunk(text, source="facts.json", category=cat))
            except Exception as e:
                logger.warning(f"Error loading facts.json: {e}")

        # Deduplicate chunks
        seen = set()
        unique_chunks = []
        for c in self.chunks:
            norm = re.sub(r"\s+", " ", c.content.strip().lower())
            if norm and norm not in seen and not norm.startswith("<!--"):
                seen.add(norm)
                unique_chunks.append(c)

        self.chunks = unique_chunks
        self.total_docs = len(self.chunks)

        # Build Document Frequency
        for chunk in self.chunks:
            for term in set(chunk.tokens):
                self.doc_freq[term] += 1

        logger.info(f"Loaded and indexed {len(self.chunks)} RAG memory chunks in Geminka.")

    def _load_markdown_file(self, path: Path, category: str) -> None:
        try:
            raw = path.read_text(encoding="utf-8")
            # Strip comments
            cleaned_raw = re.sub(r"<!--[\s\S]*?-->", "", raw)
            # Hermes convention: sections delimited by '§' or double newlines
            if "§" in cleaned_raw:
                sections = [s.strip() for s in cleaned_raw.split("§") if s.strip()]
            else:
                sections = [s.strip() for s in cleaned_raw.split("\n\n") if s.strip()]

            for sec in sections:
                # Ignore markdown headers alone
                if len(sec) >= 10 and not (sec.startswith("#") and "\n" not in sec):
                    self.chunks.append(MemoryChunk(sec, source=path.name, category=category))
        except Exception as e:
            logger.debug(f"Could not read memory file {path}: {e}")

    def compute_similarity(self, query_tokens: List[str], chunk: MemoryChunk) -> float:
        """Computes BM25/TF-IDF relevance score."""
        if not query_tokens or not chunk.tokens:
            return 0.0

        score = 0.0
        query_counter = Counter(query_tokens)

        for token in query_counter:
            if token in chunk.term_freq:
                tf = chunk.term_freq[token] / max(1, len(chunk.tokens))
                df = self.doc_freq.get(token, 1)
                idf = math.log((self.total_docs + 1) / (df + 0.5)) + 1.0
                score += tf * idf * (1.0 + 0.5 * query_counter[token])

        # Exact phrase bonus
        raw_query = " ".join(query_tokens)
        if raw_query in chunk.content.lower():
            score += 2.5

        return score

    def retrieve(self, query: str, top_k: int = 3, min_score: float = 0.05) -> List[MemoryChunk]:
        """Retrieves top-k relevant memory chunks for query."""
        if not self.chunks:
            self.reload_all_memories()

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scored = []
        for chunk in self.chunks:
            s = self.compute_similarity(query_tokens, chunk)
            if s >= min_score:
                scored.append((s, chunk))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [chunk for score, chunk in scored[:top_k]]

    def format_rag_context(self, query: str, top_k: int = 3) -> str:
        """Formats retrieved memories into a clean prompt context block."""
        retrieved = self.retrieve(query, top_k=top_k)
        if not retrieved:
            # If no direct match, return base user profile chunk if available
            user_pref = [c for c in self.chunks if c.category == "user_profile"][:1]
            if not user_pref:
                return ""
            mem_items = "\n".join(f"• {c.content}" for c in user_pref)
            return f"[Долговременная память и профиль пользователя]:\n{mem_items}\n"

        mem_items = "\n".join(f"• {c.content}" for c in retrieved)
        return f"[Долговременная память RAG (найденные факты и контекст)]:\n{mem_items}\n"

    def add_user_preference(self, text: str) -> None:
        """Appends a new user preference to USER.md."""
        text = text.strip()
        if not text:
            return
        try:
            with open(USER_MD_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n§\n{text}\n")
            self.reload_all_memories()
        except Exception as e:
            logger.warning(f"Failed to append to USER.md: {e}")

    def add_memory_note(self, text: str, category: str = "project_knowledge") -> None:
        """Appends a new knowledge note to MEMORY.md and facts.json."""
        text = text.strip()
        if not text:
            return

        # 1. Append to facts.json
        facts = []
        if FACTS_JSON_FILE.exists():
            try:
                with open(FACTS_JSON_FILE, "r", encoding="utf-8") as f:
                    facts = json.load(f)
            except Exception:
                facts = []

        facts.append({
            "text": text,
            "category": category,
            "timestamp": time.time(),
        })

        with open(FACTS_JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(facts, f, indent=2, ensure_ascii=False)

        # 2. Append to MEMORY.md
        try:
            with open(MEMORY_MD_FILE, "a", encoding="utf-8") as f:
                f.write(f"\n§\n{text}\n")
        except Exception as e:
            logger.warning(f"Failed to append to MEMORY.md: {e}")

        self.reload_all_memories()

    def get_all_memories_list(self) -> List[str]:
        return [c.content for c in self.chunks]


rag_engine = RAGMemoryEngine()
