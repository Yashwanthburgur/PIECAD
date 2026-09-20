"""RelevanceEngine — extensible, deterministic concept-detection layer.

This is the TEMPORARY selection mechanism that stands in for a future intent
classifier. It answers "what does this request need?" using lightweight rule
strategies over the user request, the conversation, the design state and the
session memory.

It is explicitly NOT the final classifier. It is structured as a rule/strategy
list so it can be extended, replaced or augmented later. The compiler consumes
a produced ``ContextPlan`` and does not care who produced it.

Relevance operates as::

    relevance = f(user_request, recent_conversation, design_state, session_memory)

not simply::

    relevance = f(user_request)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .memory import SessionMemory
from .plan import (
    ContextPlan,
    REASONING_BOOLEAN,
    REASONING_DEFAULT,
    REASONING_EXPORT,
    REASONING_INSPECT,
    REASONING_MODIFY,
    REASONING_VAGUE,
)
from .state import DesignState


@dataclass
class RelevanceRule:
    """A named concept rule mapping request signals to context requirements."""

    name: str
    keywords: Sequence[str]
    tools: List[str]
    state_sections: List[str]
    memory_sections: List[str]
    reasoning_mode: str = REASONING_DEFAULT
    object_hints: List[str] = field(default_factory=list)
    confidence: Optional[float] = None

    def matches(self, text: str) -> bool:
        text_l = (text or "").lower()
        return any(kw in text_l for kw in self.keywords)


# --------------------------------------------------------------------------- #
# Rule catalogue.
# --------------------------------------------------------------------------- #
_INSPECTION_TOOLS = ["get_faces", "get_edges",
                     "get_mass_properties", "get_bom"]
_VERIFICATION_TOOLS = ["recompute", "get_faces", "get_error"]
_RECOVERY_TOOLS = ["get_error"]
_QUERY_DEFAULT = ["get_state"]

# Concept rules — each maps to a page of the tool plan + required sections.
CONCEPT_RULES: List[RelevanceRule] = [
    RelevanceRule(
        name="hole",
        keywords=("hole", "drill", "bore", "countersink",
                  "mounting hole", "thread", "tapped"),
        tools=["hole", "edit_feature", "get_faces"] + _INSPECTION_TOOLS,
        state_sections=["objects", "selection", "feature_tree"],
        memory_sections=["conventions", "preferences", "corrections"],
        reasoning_mode=REASONING_MODIFY,
        object_hints=["hole", "cylinder", "bracket", "box", "plate"],
        confidence=0.9,
    ),
    RelevanceRule(
        name="fillet",
        keywords=("fillet", "round the", "round off", "round edge", "radius"),
        tools=["fillet", "edit_feature", "get_edges"] + _INSPECTION_TOOLS,
        state_sections=["objects", "selection"],
        memory_sections=["preferences"],
        reasoning_mode=REASONING_MODIFY,
        object_hints=["edge", "box", "plate"],
    ),
    RelevanceRule(
        name="chamfer",
        keywords=("chamfer", "bevel"),
        tools=["chamfer", "edit_feature", "get_edges"] + _INSPECTION_TOOLS,
        state_sections=["objects", "selection"],
        memory_sections=["preferences"],
        reasoning_mode=REASONING_MODIFY,
        object_hints=["edge", "box", "plate"],
    ),
    RelevanceRule(
        name="box",
        keywords=("box", "block", "plate", "enclosure", "base"),
        tools=["box", "edit_feature"] + _INSPECTION_TOOLS,
        state_sections=["objects"],
        memory_sections=["preferences", "conventions"],
        reasoning_mode=REASONING_DEFAULT,
        object_hints=["box", "plate", "base"],
    ),
    RelevanceRule(
        name="cylinder",
        keywords=("cylinder", "cylindrical", "pin", "shaft", "round stock"),
        tools=["cylinder", "edit_feature"] + _INSPECTION_TOOLS,
        state_sections=["objects"],
        memory_sections=["preferences"],
        reasoning_mode=REASONING_DEFAULT,
        object_hints=["cylinder", "pin", "shaft"],
    ),
    RelevanceRule(
        name="sketch",
        keywords=("sketch", "profile", "2d", "draft"),
        tools=["sketch", "extrude", "pad"] + _INSPECTION_TOOLS,
        state_sections=["objects", "selection"],
        memory_sections=["preferences"],
        reasoning_mode=REASONING_DEFAULT,
        object_hints=["sketch"],
    ),
    RelevanceRule(
        name="pad",
        keywords=("pad", "extrude", "protrusion"),
        tools=["pad", "extrude", "edit_feature"] + _INSPECTION_TOOLS,
        state_sections=["objects", "selection"],
        memory_sections=["preferences"],
        reasoning_mode=REASONING_DEFAULT,
        object_hints=["pad", "sketch"],
    ),
    RelevanceRule(
        name="pocket",
        keywords=("pocket", "recess", "depression"),
        tools=["pocket", "boolean", "edit_feature"] + _INSPECTION_TOOLS,
        state_sections=["objects", "selection"],
        memory_sections=["preferences"],
        reasoning_mode=REASONING_MODIFY,
        object_hints=["pocket", "box", "plate"],
    ),
    RelevanceRule(
        name="boolean",
        keywords=("boolean", "union", "subtract", "difference",
                  "cut", "intersect", "remove", "combine"),
        tools=["boolean"] + _INSPECTION_TOOLS,
        state_sections=["objects", "selection", "feature_tree"],
        memory_sections=["preferences"],
        reasoning_mode=REASONING_BOOLEAN,
        object_hints=[],
    ),
    RelevanceRule(
        name="pattern",
        keywords=("pattern", "array", "repeat", "multiples",
                  "linear", "circular", "bolt holes"),
        tools=["pattern_linear", "pattern_circular",
               "hole"] + _INSPECTION_TOOLS,
        state_sections=["objects", "selection"],
        memory_sections=["preferences", "conventions"],
        reasoning_mode=REASONING_DEFAULT,
        object_hints=["hole", "pattern"],
    ),
    RelevanceRule(
        name="inspect",
        keywords=("inspect", "check", "measure", "dimensions", "faces",
                  "edges", "properties", "what is", "volume", "mass"),
        tools=_INSPECTION_TOOLS,
        state_sections=["objects"],
        memory_sections=[],
        reasoning_mode=REASONING_INSPECT,
        object_hints=[],
    ),
    RelevanceRule(
        name="measure",
        keywords=("measure", "distance", "area", "length between", "how far"),
        tools=["get_faces", "get_mass_properties", "measure"],
        state_sections=["objects", "selection"],
        memory_sections=[],
        reasoning_mode=REASONING_INSPECT,
        object_hints=[],
    ),
    RelevanceRule(
        name="export",
        keywords=("export", "save", "download", "stl",
                  "step", "obj", "format", "save as"),
        tools=["export"] + _INSPECTION_TOOLS,
        state_sections=["objects"],
        memory_sections=["preferences"],
        reasoning_mode=REASONING_EXPORT,
        object_hints=[],
    ),
    RelevanceRule(
        name="delete",
        keywords=("delete", "remove", "erase",
                  "get rid of", "undo the last", "discard"),
        tools=["delete_feature", "edit_feature"] + _INSPECTION_TOOLS,
        state_sections=["objects", "feature_tree"],
        memory_sections=["corrections", "decisions"],
        reasoning_mode=REASONING_MODIFY,
        object_hints=[],
    ),
    RelevanceRule(
        name="modify",
        keywords=("change", "resize", "edit", "update", "make bigger", "make smaller",
                  "increase", "decrease", "resize", "width", "length", "height", "diameter"),
        tools=["edit_feature"] + _INSPECTION_TOOLS,
        state_sections=["objects", "selection"],
        memory_sections=["preferences", "corrections"],
        reasoning_mode=REASONING_MODIFY,
        object_hints=[],
    ),
    RelevanceRule(
        name="select",
        keywords=("select", "choose", "pick", "highlight"),
        tools=["get_state"] + _INSPECTION_TOOLS,
        state_sections=["objects", "selection"],
        memory_sections=[],
        reasoning_mode=REASONING_INSPECT,
        object_hints=[],
    ),
    RelevanceRule(
        name="undo",
        keywords=("undo", "revert", "go back", "cancel last"),
        tools=["edit_feature", "delete_feature"] + _INSPECTION_TOOLS,
        state_sections=["objects", "feature_tree"],
        memory_sections=["decisions", "corrections"],
        reasoning_mode=REASONING_MODIFY,
        object_hints=[],
    ),
    RelevanceRule(
        name="redo",
        keywords=("redo", "reapply"),
        tools=["edit_feature"] + _INSPECTION_TOOLS,
        state_sections=["objects"],
        memory_sections=["decisions"],
        reasoning_mode=REASONING_MODIFY,
        object_hints=[],
    ),
]


class RelevanceEngine:
    """Stateless deterministic relevance engine producing a ContextPlan.

    The engine is state-aware: it considers the current selection and object list
    from ``DesignState`` so a request like "make it round" is interpreted against
    what is actually selected.
    """

    def __init__(self, rules: Optional[List[RelevanceRule]] = None) -> None:
        self._rules = list(rules) if rules is not None else list(CONCEPT_RULES)

    def detect(self, text: str) -> List[RelevanceRule]:
        return [rule for rule in self._rules if rule.matches(text)]

    def plan(
        self,
        user_request: str,
        state: Optional[DesignState] = None,
        memory: Optional[SessionMemory] = None,
        conversation: Any = None,
    ) -> ContextPlan:
        """Produce a ContextPlan from the request + current context.

        ``state`` is used to make relevance selection-state-aware: a selected
        sketch -> sketch tools; a selected edge -> fillet/chamfer + inspection.
        """
        matched = self.detect(user_request)

        required_tools: List[str] = []
        state_sections: List[str] = ["objects"]
        memory_sections: List[str] = []
        object_ids: List[str] = []
        hints_used: List[str] = []
        best_confidence: Optional[float] = None
        reasoning_mode = REASONING_DEFAULT

        for rule in matched:
            for t in rule.tools:
                if t not in required_tools:
                    required_tools.append(t)
            for s in rule.state_sections:
                if s not in state_sections:
                    state_sections.append(s)
            for mem in rule.memory_sections:
                if mem not in memory_sections:
                    memory_sections.append(mem)
            for h in rule.object_hints:
                if h not in hints_used:
                    hints_used.append(h)
            if best_confidence is None or (rule.confidence is not None and rule.confidence > best_confidence):
                best_confidence = rule.confidence
            if rule.reasoning_mode != REASONING_DEFAULT:
                reasoning_mode = rule.reasoning_mode

        if not matched:
            reasoning_mode = REASONING_VAGUE
            # A vague request: keep object list + inspection so the agent can
            # probe geometry before acting.
            if "get_state" not in required_tools:
                required_tools.insert(0, "get_state")
            for t in _INSPECTION_TOOLS:
                if t not in required_tools:
                    required_tools.append(t)
            best_confidence = 0.3

        # ---- state-aware adjustments ----------------------------------- #
        if state is not None:
            selection = state.get_selected_entities()
            facts = state.derived_facts or {}
            kinds = facts.get("selection_kind") or []

            if selection and "edge" in (kinds or []):
                for t in ("fillet", "chamfer", "get_edges"):
                    if t not in required_tools:
                        required_tools.insert(0, t)
                if "selection" not in state_sections:
                    state_sections.append("selection")
                # resolve the object behind the selected edge if present
            elif selection and "sketch" in (kinds or []):
                for t in ("sketch", "extrude", "pad", "edit_feature"):
                    if t not in required_tools:
                        required_tools.append(t)
                if "selection" not in state_sections:
                    state_sections.append("selection")

            # Map object hints onto concrete object ids from state.
            for hint in hints_used:
                for oid, obj in state.objects.items():
                    if oid in object_ids:
                        continue
                    hay = f"{oid} {obj.object_type} {obj.label}".lower()
                    if hint in hay:
                        object_ids.append(oid)
                        break

        plan = ContextPlan(
            required_tools=required_tools,
            relevant_object_ids=object_ids,
            required_state_sections=state_sections,
            required_memory_sections=memory_sections,
            reasoning_mode=reasoning_mode,
            confidence=best_confidence,
            ambiguity=None if matched else 0.8,
            additional_context={"matched_rules": [
                r.name for r in matched], "vague": not matched},
        )
        return plan
