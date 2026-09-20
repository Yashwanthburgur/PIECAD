"""ContextBudget — lightweight deterministic context-budget management.

The compiler uses this to keep a compiled context within a target token envelope
without aggressively truncating genuinely useful information. If a section must
be dropped or trimmed, the fact is recorded so telemetry can report what was
omitted (``dropped_sections``).

All token figures here are ESTIMATES (chars/4 heuristic), never exact provider
counts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ContextBudget:
    """Per-call budget describing the token windows per context section.

    Values are the *target cap* (in estimated tokens) for each section. Setting
    a cap to ``None`` means "no explicit cap" (do not drop that section simply
    to satisfy a number).
    """

    maximum_context_tokens: Optional[int] = 20000
    reserved_output_tokens: Optional[int] = 1000
    state_budget: Optional[int] = 3000
    memory_budget: Optional[int] = 800
    tool_budget: Optional[int] = 4000
    history_budget: Optional[int] = 2000

    # Record of sections that had to be dropped/trimmed to stay within budget.
    dropped_sections: List[str] = field(default_factory=list)

    def effective_state_tokens(self) -> int:
        return self.state_budget or 0

    def note_dropped(self, section: str) -> None:
        if section not in self.dropped_sections:
            self.dropped_sections.append(section)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "maximum_context_tokens": self.maximum_context_tokens,
            "reserved_output_tokens": self.reserved_output_tokens,
            "state_budget": self.state_budget,
            "memory_budget": self.memory_budget,
            "tool_budget": self.tool_budget,
            "history_budget": self.history_budget,
        }
