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
class TaskRequirement:
    """A single ordered task requirement (phase) with its required subtasks.

    This represents one step in the ordered task plan (e.g., create_base,
    edge_modify, shell, holes, inspect). Each requirement has a list of
    required subtasks (capabilities) that must be completed for the phase
    to be considered done.
    """
    name: str
    required_subtasks: List[str] = field(default_factory=list)
    quantity: int = 1  # For requirements like "four holes"


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

    # Phase-aware routing fields
    phase_index: int = 0
    completed_subtasks: Dict[int, List[str]] = field(default_factory=dict)
    required_subtasks: List[TaskRequirement] = field(default_factory=list)

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
            "phase_index": self.phase_index,
            "completed_subtasks": {k: list(v) for k, v in self.completed_subtasks.items()},
            "required_subtasks": [
                {"name": r.name, "required_subtasks": list(
                    r.required_subtasks), "quantity": r.quantity}
                for r in self.required_subtasks
            ],
        }

    def current_intent(self) -> Optional[str]:
        """Return the name of the current active task requirement (phase)."""
        if self.phase_index < len(self.required_subtasks):
            return self.required_subtasks[self.phase_index].name
        return None

    def current_requirement(self) -> Optional[TaskRequirement]:
        """Return the current TaskRequirement object."""
        if self.phase_index < len(self.required_subtasks):
            return self.required_subtasks[self.phase_index]
        return None

    def is_phase_complete(self) -> bool:
        """Check if the current phase is complete based on completed subtasks."""
        req = self.current_requirement()
        if req is None:
            return True  # No more phases
        completed = self.completed_subtasks.get(self.phase_index, [])
        # Check if all required subtasks are completed (with quantity)
        for subtask in req.required_subtasks:
            # Count how many times this subtask was completed
            count = completed.count(subtask)
            if count < req.quantity:
                return False
        return True

    def mark_subtask_complete(self, subtask: str) -> None:
        """Mark a subtask as completed for the current phase.

        Only marks if the subtask is actually required for the current phase.
        """
        req = self.current_requirement()
        if req is None:
            return
        if subtask in req.required_subtasks:
            if self.phase_index not in self.completed_subtasks:
                self.completed_subtasks[self.phase_index] = []
            self.completed_subtasks[self.phase_index].append(subtask)

    def advance_phase(self) -> bool:
        """Advance to the next phase if current phase is complete.

        Returns True if advanced, False if not (phase not complete or no more phases).
        """
        if self.is_phase_complete():
            if self.phase_index + 1 <= len(self.required_subtasks):
                self.phase_index += 1
                return True
        return False

    def get_active_tool_categories(self) -> List[str]:
        """Get tool categories relevant to the current phase.

        This helps the router filter tools based on the current phase.
        """
        req = self.current_requirement()
        if req is None:
            return []
        # Map requirement names to tool categories
        category_map = {
            "create_base": ["primitive"],
            "edge_modify": ["feature"],
            "shell": ["feature"],
            "holes": ["feature"],
            "inspect": ["query"],
            "sketch_extrude": ["sketch"],
            "boolean_ops": ["feature"],
            "pattern_ops": ["assembly"],
            "edit_feature": ["feature"],
            "assembly": ["assembly"],
            "delete_undo": ["feature"],
            "export_ops": ["query"],
        }
        return category_map.get(req.name, [])

    def get_required_subtasks_for_current_phase(self) -> List[str]:
        """Get the list of required subtasks for the current phase."""
        req = self.current_requirement()
        if req is None:
            return []
        return list(req.required_subtasks)


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
