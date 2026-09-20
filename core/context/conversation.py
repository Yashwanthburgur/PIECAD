"""ConversationContext — bounded, relevant conversation-history abstraction.

The Context Engine must *not* blind-concatenate the entire chat history. This
module maintains a bounded turn window and exposes deterministic relevance
selection so that:

- the current user request is ALWAYS retained;
- recent turns within a bounded window are retained;
- older turns are only retained when they reference concepts relevant to the
  current request;
- irrelevant historical turns are excluded from compiled context.

No semantic/vector retrieval is built here; the interface is kept explicit so a
future intent layer can substitute richer selection without an architectural
change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Concept keywords used to decide whether an *older* turn is relevant to the
# current request. Kept deliberately small and deterministic.
_CONCEPT_SIGNALS = (
    "hole", "drill", "bore", "fillet", "round", "chamfer", "bevel", "box",
    "cylinder", "sketch", "pad", "extrude", "pocket", "boolean", "cut",
    "union", "pattern", "material", "units", "mm", "radius", "diameter",
    "export", "stl", "step", "thread", "m6", "m8", "m3", "mounting", "hole",
)


@dataclass
class Turn:
    """A single conversation turn (role + content)."""

    # 'user' | 'assistant' (tool scratchpad is not conversation history)
    role: str
    content: str
    ts: float = 0.0

    def to_message(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class ConversationContext:
    """Holds the raw session turns and produces relevant subsets."""

    default_recent_window: int = 4
    _turns: List[Turn] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    # ingestion
    # ------------------------------------------------------------------ #
    def add_user(self, message: str) -> "ConversationContext":
        self._turns.append(Turn(role="user", content=(message or "").strip()))
        return self

    def add_assistant(self, message: str) -> "ConversationContext":
        self._turns.append(
            Turn(role="assistant", content=(message or "").strip()))
        return self

    def append(self, role: str, content: str) -> "ConversationContext":
        if role == "user":
            return self.add_user(content)
        if role == "assistant":
            return self.add_assistant(content)
        return self

    def clear(self) -> "ConversationContext":
        self._turns.clear()
        return self

    def __len__(self) -> int:
        return len(self._turns)

    # ------------------------------------------------------------------ #
    # relevance
    # ------------------------------------------------------------------ #
    def current_turn(self) -> Optional[Turn]:
        """Return the most recent turn (the current user request)."""
        return self._turns[-1] if self._turns else None

    def recent_turns(self, n: Optional[int] = None) -> List[Turn]:
        """Return the most recent ``n`` turns (bounded window)."""
        n = n if n is not None else self.default_recent_window
        return list(self._turns[-n:]) if self._turns else []

    def relevant_history(self, current_text: str) -> List[Turn]:
        """Select history relevant to ``current_text``.

        Always includes the current turn and the bounded recent window. Older
        turns are included only if they share a concept keyword with the request.
        """
        text_l = (current_text or "").lower()
        signals = set()
        for sig in _CONCEPT_SIGNALS:
            if sig in text_l:
                signals.add(sig)

        recent = self.recent_turns(self.default_recent_window)
        recent_ids = {id(t) for t in recent}

        selected: List[Turn] = list(recent)

        # Older turns: include only concept-relevant ones (bounded to avoid a dump).
        for turn in self._turns[:-self.default_recent_window] if len(self._turns) > self.default_recent_window else []:
            if id(turn) in recent_ids:
                continue
            hay = (turn.content or "").lower()
            if any(sig in hay for sig in signals):
                selected.append(turn)

        return selected[-12:]  # hard cap to avoid unbounded growth

    def to_messages(self, current_text: str) -> List[Dict[str, str]]:
        """Compile selected history into OpenAI-style message dicts."""
        selected = self.relevant_history(current_text)
        return [t.to_message() for t in selected]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "turns": [t.to_message() for t in self._turns],
            "count": len(self._turns),
            "default_recent_window": self.default_recent_window,
        }
