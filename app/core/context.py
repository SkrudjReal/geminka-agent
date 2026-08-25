"""Persistent and strictly bounded conversation context assembly."""

from __future__ import annotations

from app.core import config
from app.core.state import StateStore, state_store


class ContextManager:
    """Build model payloads without allowing any user to see another user's state."""

    def __init__(
        self,
        store: StateStore = state_store,
        *,
        max_turns: int = 15,
        max_chars: int | None = None,
        max_message_chars: int = 12_000,
    ) -> None:
        self.store = store
        self.max_turns = max_turns
        self.max_chars = max_chars or config.settings.max_input_chars
        self.max_message_chars = max_message_chars

    def _bounded_history(self, user_id: int, budget: int) -> list[dict[str, str]]:
        remaining = max(0, budget)
        selected: list[dict[str, str]] = []
        marker = " …[truncated]"

        history = self.store.get_messages(user_id, limit=self.max_turns * 2)
        for message in reversed(history):
            if remaining <= 0:
                break
            content = message["content"][: self.max_message_chars]
            if len(content) > remaining:
                if remaining <= len(marker):
                    content = marker[:remaining]
                else:
                    content = content[: remaining - len(marker)].rstrip() + marker
            selected.append({"role": message["role"], "content": content})
            remaining -= len(content)

        return list(reversed(selected))

    def build_payload_messages(
        self,
        user_id: int,
        current_prompt: str,
        system_prompt: str,
        emotional_context: str = "",
        memory_context: str = "",
        adaptive_context: str = "",
        user_emojis_context: str = "",
        runtime_context: str = "",
    ) -> list[dict[str, str]]:
        """Create an OpenAI-compatible payload with bounded user-controlled text."""
        system_parts = [system_prompt.strip()]
        for block in (
            emotional_context,
            adaptive_context,
            user_emojis_context,
            memory_context,
            runtime_context,
        ):
            if block.strip():
                system_parts.append(block.strip())

        current = current_prompt[: self.max_chars]
        messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
        messages.extend(self._bounded_history(user_id, self.max_chars - len(current)))
        messages.append(
            {
                "role": "user",
                "content": current,
            }
        )
        return messages

    def add_exchange(self, user_id: int, user_content: str, assistant_content: str) -> None:
        self.store.add_exchange(
            user_id,
            user_content[: self.max_message_chars],
            assistant_content[: self.max_message_chars],
        )

    def clear_user_context(self, user_id: int) -> None:
        self.store.clear_messages(user_id)


context_manager = ContextManager()
