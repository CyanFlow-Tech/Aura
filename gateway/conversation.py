"""Per-session conversational memory.

A `Conversation` is the multi-turn message history that lives for the
duration of one device-side dialog (one `session_id`). Each device upload
runs as a single Turn against the same Conversation, so the LLM sees the
full prior context.

The system prompt is supplied at build time (by `LLM`) rather than stored
inside `Conversation`, so prompt changes don't require touching session
state.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Conversation:
    session_id: str
    history: list[dict[str, str]] = field(default_factory=list)

    def append_user(self, text: str) -> None:
        self.history.append({"role": "user", "content": text})

    def append_assistant(self, text: str) -> None:
        self.history.append({"role": "assistant", "content": text})

    def messages(
        self,
        system_prompt: str,
        extra_system_messages: list[str] | None = None,
    ) -> list[dict[str, str]]:
        """Return the message list to feed into the LLM, with the
        system prompt prepended. Returns a fresh list so the caller is
        free to mutate it.
        """
        system_contents = [system_prompt]
        if extra_system_messages:
            system_contents.extend(content for content in extra_system_messages if content)
        # Some providers (e.g. Hunyuan) require system role to appear only once
        # and strictly at index 0.
        messages = [{"role": "system", "content": "\n\n".join(system_contents)}]
        messages.extend(self.history)
        return messages

    def recent_history(self, limit: int) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        return self.history[-limit:]
