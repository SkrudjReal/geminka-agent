"""Sliding Context Window & Compressor for Geminka.

Implements Hermes-style context compression and sliding window:
- Preserves recent turns intact (protect_last_n)
- Prunes older turns while maintaining a rolling recap when exceeding budget
- Generates composite system instruction (Persona + Emotions + RAG Memory + Rolling Context)
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class UserDialogueContext:
    def __init__(self, user_id: int, max_turns: int = 15, max_chars: int = 24000, protect_last_n: int = 8):
        self.user_id = user_id
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.protect_last_n = protect_last_n
        # List of {"role": "user" | "assistant", "content": str}
        self.messages: List[Dict[str, str]] = []
        self.rolling_recap: str = ""

    def add_user_message(self, content: str) -> None:
        self.messages.append({"role": "user", "content": content})
        self._prune_and_compress()

    def add_assistant_message(self, content: str) -> None:
        self.messages.append({"role": "assistant", "content": content})
        self._prune_and_compress()

    def _prune_and_compress(self) -> None:
        """Sliding window with rolling recap compression for evicted turns."""
        max_messages = self.max_turns * 2
        protected_messages = self.protect_last_n * 2

        # 1. Prune by turn count
        if len(self.messages) > max_messages:
            evicted = self.messages[:-protected_messages]
            self.messages = self.messages[-protected_messages:]
            self._update_rolling_recap(evicted)

        # 2. Prune by character budget
        total_chars = sum(len(m["content"]) for m in self.messages)
        while total_chars > self.max_chars and len(self.messages) > protected_messages:
            evicted = [self.messages.pop(0)]
            total_chars -= len(evicted[0]["content"])
            self._update_rolling_recap(evicted)

    def _update_rolling_recap(self, evicted: List[Dict[str, str]]) -> None:
        """Extracts brief keywords/topics from evicted turns for rolling summary."""
        snippets = []
        for m in evicted:
            role = "Пользователь" if m["role"] == "user" else "Коломбина"
            text = m["content"].strip().replace("\n", " ")
            if len(text) > 80:
                text = text[:77] + "..."
            snippets.append(f"{role}: {text}")

        if snippets:
            recap_str = "; ".join(snippets[-4:])
            self.rolling_recap = f"[Предыдущий контекст беседы]: {recap_str}"

    def clear(self) -> None:
        self.messages.clear()
        self.rolling_recap = ""

    def get_sliding_window(self) -> List[Dict[str, str]]:
        return list(self.messages)


class ContextManager:
    def __init__(self, default_max_turns: int = 15):
        self.default_max_turns = default_max_turns
        self._contexts: Dict[str, UserDialogueContext] = {}

    def get_context(self, user_id: int) -> UserDialogueContext:
        key = str(user_id)
        if key not in self._contexts:
            self._contexts[key] = UserDialogueContext(user_id, max_turns=self.default_max_turns)
        return self._contexts[key]

    def clear_user_context(self, user_id: int) -> None:
        key = str(user_id)
        if key in self._contexts:
            self._contexts[key].clear()

    def build_payload_messages(
        self,
        user_id: int,
        current_prompt: str,
        system_prompt: str,
        emotional_context: str = "",
        rag_context: str = "",
        adaptive_context: str = "",
        user_emojis_context: str = "",
    ) -> List[Dict[str, str]]:
        """Constructs full OpenAI messages array: Composite System Prompt + Sliding Window + User Message."""
        ctx = self.get_context(user_id)

        # Composite system prompt blocks
        system_parts = [system_prompt.strip()]
        if emotional_context.strip():
            system_parts.append(emotional_context.strip())
        if adaptive_context.strip():
            system_parts.append(adaptive_context.strip())
        if user_emojis_context.strip():
            system_parts.append(user_emojis_context.strip())
        if rag_context.strip():
            system_parts.append(rag_context.strip())
        if ctx.rolling_recap:
            system_parts.append(ctx.rolling_recap)

        full_system = "\n\n".join(system_parts)

        messages = [{"role": "system", "content": full_system}]
        # Append recent active dialogue turns
        messages.extend(ctx.get_sliding_window())
        # Append current user prompt
        messages.append({"role": "user", "content": current_prompt})

        return messages


context_manager = ContextManager()
