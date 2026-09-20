"""Telemetry — lightweight context/token observability for LLM calls.

This module is intended to *integrate* with existing provider telemetry rather
than replace or duplicate a full token-tracking system. No heavy tokenizer
dependency (no tiktoken). Estimates use the lightweight ``chars/4`` heuristic and
are explicitly labelled as ESTIMATES, never as exact provider token counts.

Exact counts (when a provider reports them) are kept under the
``exact_provider_tokens`` key so callers can always tell estimates from truth.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Heuristic: 4 characters ≈ 1 token for typical multilingual/English CAD text.
CHARS_PER_TOKEN = 4.0

# Serialization overhead added to JSON dicts (braces/colons/commas/quotes).
_JSON_OVERHEAD_PER_KEY = 6


def estimate_tokens(text: str) -> int:
    """Estimate the token count of a string using the chars/4 heuristic."""
    if not text:
        return 0
    return max(1, int(len(str(text)) / CHARS_PER_TOKEN))


def estimate_json_tokens(obj: Any) -> int:
    """Estimate the token count of a JSON-serializable object."""
    if isinstance(obj, str):
        return estimate_tokens(obj)
    if isinstance(obj, dict):
        total = sum(estimate_json_tokens(v) for v in obj.values())
        total += len(obj) * _JSON_OVERHEAD_PER_KEY
        return total
    if isinstance(obj, (list, tuple)):
        return sum(estimate_json_tokens(v) for v in obj)
    if obj is None:
        return 0
    return estimate_tokens(str(obj))


@dataclass
class ContextTelemetry:
    """Per-LLM-call context metrics.

    All token figures are ESTIMATES (from the chars/4 heuristic) unless the
    field name says ``exact``. ReAct step, timings and tool exposure are also
    captured here for observability.
    """

    react_step: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    exact_provider_tokens: Optional[Dict[str, Any]] = None

    estimated_context_tokens: int = 0
    estimated_tool_schema_tokens: int = 0
    estimated_state_tokens: int = 0
    estimated_memory_tokens: int = 0
    estimated_conversation_tokens: int = 0
    tools_exposed: int = 0
    tools_exposed_names: List[str] = field(default_factory=list)
    why_tools: List[str] = field(default_factory=list)
    dropped_sections: List[str] = field(default_factory=list)
    compilation_time_ms: Optional[float] = None
    llm_latency_ms: Optional[float] = None
    request_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "react_step": self.react_step,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "exact_provider_tokens": self.exact_provider_tokens,
            "estimated_context_tokens": self.estimated_context_tokens,
            "estimated_tool_schema_tokens": self.estimated_tool_schema_tokens,
            "estimated_state_tokens": self.estimated_state_tokens,
            "estimated_memory_tokens": self.estimated_memory_tokens,
            "estimated_conversation_tokens": self.estimated_conversation_tokens,
            "tools_exposed": self.tools_exposed,
            "tools_exposed_names": list(self.tools_exposed_names),
            "why_tools": list(self.why_tools),
            "dropped_sections": list(self.dropped_sections),
            "compilation_time_ms": self.compilation_time_ms,
            "llm_latency_ms": self.llm_latency_ms,
        }


def estimate_context_breakdown(
    *,
    context_text: str = "",
    tool_schema_text: str = "",
    state_text: str = "",
    memory_text: str = "",
    conversation_text: str = "",
) -> Dict[str, int]:
    """Return a dict of per-section estimated token counts."""
    return {
        "context": estimate_tokens(context_text),
        "tool_schema": estimate_tokens(tool_schema_text),
        "state": estimate_tokens(state_text),
        "memory": estimate_tokens(memory_text),
        "conversation": estimate_tokens(conversation_text),
    }


def monotonic_ms() -> float:
    return time.monotonic() * 1000.0
