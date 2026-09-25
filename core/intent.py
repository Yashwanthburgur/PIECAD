"""IntentClassifier — fast, deterministic+LLM intent analysis for CAD operations.

Produces a ContextPlan + ToolSelectionPlan that the ContextCompiler + ToolRouter
consume to narrow the active tool set before each ReAct step.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from core.context.plan import (
    ContextPlan,
    ToolSelectionPlan,
    REASONING_DEFAULT,
    REASONING_INSPECT,
    REASONING_MODIFY,
    REASONING_BOOLEAN,
    REASONING_EXPORT,
    REASONING_VAGUE,
)
from core.tool_registry import ToolCapability, get_global_registry


# --------------------------------------------------------------------------- #
# Deterministic keyword→capability mapping (fast, no LLM call)
# --------------------------------------------------------------------------- #

_INTENT_KEYWORDS: Dict[str, Dict[str, Any]] = {
    # Primitive creation
    "create_base": {
        "keywords": [
            "create", "make", "build", "add", "box", "cylinder", "sphere", "cone",
            "torus", "wedge", "helix", "prism", "primitive", "base", "block",
            "plate", "disk", "rod", "shaft", "pin"
        ],
        "category": "primitive",
        "reasoning": REASONING_DEFAULT,
        "confidence": 0.85,
    },
    # Sketch + extrude workflow
    "sketch_extrude": {
        "keywords": [
            "sketch", "profile", "2d", "draft", "extrude", "pad", "revolve",
            "protrusion", "loft", "sweep"
        ],
        "category": "sketch",
        "reasoning": REASONING_DEFAULT,
        "confidence": 0.85,
    },
    # Edge modifications (fillet, chamfer)
    "edge_modify": {
        "keywords": [
            "fillet", "round", "chamfer", "bevel", "radius", "edge"
        ],
        "category": "feature",
        "requires": ["edge"],
        "produces": ["solid"],
        "reasoning": REASONING_MODIFY,
        "confidence": 0.9,
    },
    # Face modifications (hole, shell)
    "face_modify": {
        "keywords": [
            "hole", "drill", "bore", "countersink", "thread", "tapped",
            "shell", "hollow", "thin wall", "container"
        ],
        "category": "feature",
        "requires": ["face"],
        "produces": ["solid"],
        "reasoning": REASONING_MODIFY,
        "confidence": 0.9,
    },
    # Boolean operations
    "boolean_ops": {
        "keywords": [
            "boolean", "union", "fuse", "join", "combine", "merge",
            "subtract", "cut", "difference", "remove", "intersect", "common"
        ],
        "category": "feature",
        "requires": ["solid"],
        "produces": ["solid"],
        "reasoning": REASONING_BOOLEAN,
        "confidence": 0.9,
    },
    # Patterning
    "pattern_ops": {
        "keywords": [
            "pattern", "array", "repeat", "linear pattern", "circular pattern",
            "polar pattern", "bolt pattern", "multiples", "copies"
        ],
        "category": "assembly",
        "requires": ["solid"],
        "produces": ["pattern"],
        "reasoning": REASONING_DEFAULT,
        "confidence": 0.85,
    },
    # Inspection/measurement
    "inspect": {
        "keywords": [
            "inspect", "check", "measure", "dimension", "volume", "mass",
            "properties", "faces", "edges", "what is", "show", "list",
            "center of mass", "bounding box", "bbox"
        ],
        "category": "query",
        "reasoning": REASONING_INSPECT,
        "confidence": 0.8,
    },
    # Export
    "export_ops": {
        "keywords": [
            "export", "save", "download", "stl", "step", "obj", "iges",
            "3mf", "format", "save as", "file"
        ],
        "category": "query",
        "reasoning": REASONING_EXPORT,
        "confidence": 0.9,
    },
    # Edit/modify existing feature
    "edit_feature": {
        "keywords": [
            "resize", "change", "modify", "update", "edit", "bigger", "smaller",
            "increase", "decrease", "width", "length", "height", "diameter",
            "move", "translate", "position"
        ],
        "category": "feature",
        "reasoning": REASONING_MODIFY,
        "confidence": 0.75,
    },
    # Assembly/mating
    "assembly": {
        "keywords": [
            "mate", "assemble", "constraint", "concentric", "coincident",
            "align", "position", "insert", "fasten"
        ],
        "category": "assembly",
        "requires": ["solid"],
        "reasoning": REASONING_DEFAULT,
        "confidence": 0.8,
    },
    # Deletion/undo
    "delete_undo": {
        "keywords": [
            "delete", "remove", "undo", "revert", "discard", "erase", "cancel"
        ],
        "category": "feature",
        "reasoning": REASONING_MODIFY,
        "confidence": 0.8,
    },
}

# Inspection tools that are commonly useful after any operation
_COMMON_INSPECTION = ["get_faces", "get_edges",
                      "get_mass_properties", "get_bom"]

# Recovery tools
_RECOVERY_TOOLS = ["undo", "redo", "get_undo_redo_status",
                   "undo_if_invalid", "safe_execute"]


def _detect_intents(text: str) -> List[str]:
    """Detect all matching intent categories from user text."""
    text_l = (text or "").lower()
    matched = []
    for intent_name, config in _INTENT_KEYWORDS.items():
        if any(kw in text_l for kw in config["keywords"]):
            matched.append(intent_name)
    return matched


def _merge_tool_lists(*lists: List[str]) -> List[str]:
    """Union of lists preserving order, deduplicated."""
    seen: Set[str] = set()
    out: List[str] = []
    for lst in lists:
        for t in lst:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


@dataclass
class IntentResult:
    """Result of intent classification."""
    primary_intent: str
    matched_intents: List[str]
    required_tools: List[str] = field(default_factory=list)
    tool_selection_plan: Optional[ToolSelectionPlan] = None
    reasoning_mode: str = REASONING_DEFAULT
    confidence: float = 0.5
    ambiguity: float = 0.5
    required_state_sections: List[str] = field(
        default_factory=lambda: ["objects"])
    required_memory_sections: List[str] = field(default_factory=list)
    relevant_history_terms: List[str] = field(default_factory=list)


class IntentClassifier:
    """Deterministic+LLM intent classifier for CAD operations.

    Phase 1: Fast deterministic keyword matching.
    Phase 2: (Future) LLM refinement for ambiguous cases.
    """

    def __init__(
        self,
        registry: Optional[Any] = None,  # ToolRegistry
        llm_provider: Optional[Any] = None,  # for future LLM refinement
    ):
        self.registry = registry or get_global_registry()
        self.llm_provider = llm_provider

    def classify(self, user_prompt: str) -> IntentResult:
        """Classify user prompt into intent categories and produce plans."""
        # Phase 1: Deterministic keyword matching
        matched = _detect_intents(user_prompt)

        if not matched:
            # Vague/ambiguous request - default to inspection + primitives
            return IntentResult(
                primary_intent="vague",
                matched_intents=["vague"],
                required_tools=_merge_tool_lists(
                    ["box", "cylinder", "sphere", "cone", "sketch", "extrude"],
                    _COMMON_INSPECTION,
                ),
                tool_selection_plan=ToolSelectionPlan(
                    primary=["box", "cylinder", "sketch", "extrude"],
                    inspection=_COMMON_INSPECTION,
                    recovery=_RECOVERY_TOOLS,
                ),
                reasoning_mode=REASONING_VAGUE,
                confidence=0.3,
                ambiguity=0.8,
            )

        # Collect tools from matched intents
        all_tools: List[str] = []
        all_inspection: List[str] = []
        all_recovery: List[str] = _RECOVERY_TOOLS
        primary_tools: List[str] = []
        reasoning_modes: List[str] = []
        state_sections = {"objects"}
        memory_sections: Set[str] = set()

        for intent in matched:
            config = _INTENT_KEYWORDS[intent]
            reasoning_modes.append(config.get("reasoning", REASONING_DEFAULT))

            # Build primary tools from registry capabilities
            cat = config.get("category")
            if cat:
                cat_tools = list(self.registry.get_by_category(cat))
                primary_tools.extend(cat_tools)

            # Add inspection tools
            all_inspection.extend(_COMMON_INSPECTION)

            # State/memory sections
            if "requires" in config:
                for req in config["requires"]:
                    if req == "solid" or req == "edge" or req == "face":
                        state_sections.add("selection")

        # Deduplicate
        primary_tools = list(dict.fromkeys(primary_tools))
        all_inspection = list(dict.fromkeys(all_inspection))

        # If no primary tools found via category, fall back to keyword match
        if not primary_tools:
            # Use keyword matches against registry
            text_l = user_prompt.lower()
            for name, cap in self.registry.all_capabilities().items():
                if any(kw in text_l for kw in cap.keywords):
                    primary_tools.append(name)

        primary_tools = list(dict.fromkeys(primary_tools))

        # Build tool selection plan
        tool_plan = ToolSelectionPlan(
            primary=primary_tools,
            inspection=all_inspection,
            recovery=all_recovery,
            optional=[],
            verification=[],
        )

        # Pick highest confidence reasoning mode
        best_confidence = max(_INTENT_KEYWORDS[i].get(
            "confidence", 0.5) for i in matched)
        primary_intent = matched[0]

        return IntentResult(
            primary_intent=primary_intent,
            matched_intents=matched,
            required_tools=_merge_tool_lists(
                primary_tools, all_inspection, all_recovery),
            tool_selection_plan=tool_plan,
            reasoning_mode=reasoning_modes[0] if reasoning_modes else REASONING_DEFAULT,
            confidence=best_confidence,
            ambiguity=1.0 - best_confidence,
            required_state_sections=list(state_sections),
            required_memory_sections=list(memory_sections),
        )

    def to_context_plan(self, result: IntentResult, user_prompt: str) -> ContextPlan:
        """Convert IntentResult to ContextPlan for the compiler."""
        return ContextPlan(
            required_tools=result.required_tools,
            relevant_object_ids=result.relevant_history_terms,  # will be filled by state
            required_state_sections=result.required_state_sections,
            required_memory_sections=result.required_memory_sections,
            relevant_history_terms=result.relevant_history_terms,
            reasoning_mode=result.reasoning_mode,
            confidence=result.confidence,
            ambiguity=result.ambiguity,
            additional_context={
                "matched_intents": result.matched_intents,
                "primary_intent": result.primary_intent,
            },
        )
