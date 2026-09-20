"""ContextPlan & ToolSelectionPlan — provider-independent planner interfaces.

These classes describe *what the next LLM reasoning step requires*. They are the
consumer-side contract for a future Intent/Decision layer:

    Future Intent Classifier
            ↓ produces a ContextPlan
    ContextCompiler

The intent classifier is NOT implemented in this BIP. It can be deterministic,
Jev/System-1, another model, or a hybrid — it just needs to produce a ``ContextPlan``
(or a ``ToolSelectionPlan``) and the rest of the Context Engine consumes it without
any architectural change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Recognised reasoning modes (not exhaustive; free-form strings are allowed).
REASONING_DEFAULT = "default"
REASONING_INSPECT = "inspect"
REASONING_MODIFY = "modify"
REASONING_BOOLEAN = "boolean"
REASONING_EXPORT = "export"
REASONING_VAGUE = "vague"


@dataclass
class ContextPlan:
    """Provider-independent description of the next reasoning step's inputs.

    Fields are all *optional* so the compiler can run with a partially-populated
    plan (e.g. produced only by the deterministic relevance engine). A future
    classifier may fill more of them.
    """

    required_tools: List[str] = field(default_factory=list)
    relevant_object_ids: List[str] = field(default_factory=list)
    required_state_sections: List[str] = field(default_factory=list)
    required_memory_sections: List[str] = field(default_factory=list)
    relevant_history_terms: List[str] = field(default_factory=list)
    reasoning_mode: str = REASONING_DEFAULT
    confidence: Optional[float] = None
    ambiguity: Optional[float] = None
    additional_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "required_tools": list(self.required_tools),
            "relevant_object_ids": list(self.relevant_object_ids),
            "required_state_sections": list(self.required_state_sections),
            "required_memory_sections": list(self.required_memory_sections),
            "relevant_history_terms": list(self.relevant_history_terms),
            "reasoning_mode": self.reasoning_mode,
            "confidence": self.confidence,
            "ambiguity": self.ambiguity,
        }


@dataclass
class ToolSelectionPlan:
    """A tool plan for a single reasoning step.

    Updates the existing ``ToolRouter`` (which is NOT replaced). Exposes
    ``primary``, ``optional``, ``inspection``, ``verification`` and ``recovery``
    tool roles so a future decision layer can supply a rich dependency graph.
    """

    primary: List[str] = field(default_factory=list)
    optional: List[str] = field(default_factory=list)
    inspection: List[str] = field(default_factory=list)
    verification: List[str] = field(default_factory=list)
    recovery: List[str] = field(default_factory=list)

    def all_tools(self) -> List[str]:
        """Union of every role, preserving order and de-duplicating."""
        seen: List[str] = []
        for group in (self.primary, self.optional, self.inspection,
                      self.verification, self.recovery):
            for name in group:
                if name not in seen:
                    seen.append(name)
        return seen

    def to_dict(self) -> Dict[str, List[str]]:
        return {
            "primary": list(self.primary),
            "optional": list(self.optional),
            "inspection": list(self.inspection),
            "verification": list(self.verification),
            "recovery": list(self.recovery),
        }


def tool_plan_for(
    primary_tool: str,
    *,
    inspection: Optional[List[str]] = None,
    verification: Optional[List[str]] = None,
    recovery: Optional[List[str]] = None,
    optional: Optional[List[str]] = None,
) -> ToolSelectionPlan:
    """Convenience constructor building a ToolSelectionPlan from a primary tool."""
    return ToolSelectionPlan(
        primary=[primary_tool],
        inspection=list(inspection or []),
        verification=list(verification or []),
        recovery=list(recovery or []),
        optional=list(optional or []),
    )
