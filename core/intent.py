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
    TaskRequirement,
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
        # Phase requirement: single primitive creation
        "phase_subtasks": ["box", "cylinder", "sphere", "cone", "torus", "wedge", "helix", "prism"],
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
        "phase_subtasks": ["sketch", "extrude"],
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
        # Phase requirement: BOTH fillet AND chamfer
        "phase_subtasks": ["fillet", "chamfer"],
    },
    # Face modifications (hole, shell) - split into separate phases
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
        # This will be split into shell and holes phases based on keywords
        "phase_subtasks": ["shell", "hole"],
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
        "phase_subtasks": ["boolean"],
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
        "phase_subtasks": ["pattern_linear", "pattern_circular"],
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
        "phase_subtasks": ["get_faces", "get_edges", "get_mass_properties", "get_bom"],
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
        "phase_subtasks": ["export"],
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
        "phase_subtasks": ["edit_feature"],
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
        "phase_subtasks": ["mate"],
    },
    # Deletion/undo
    "delete_undo": {
        "keywords": [
            "delete", "remove", "undo", "revert", "discard", "erase", "cancel"
        ],
        "category": "feature",
        "reasoning": REASONING_MODIFY,
        "confidence": 0.8,
        "phase_subtasks": ["delete_feature", "undo"],
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


def _build_ordered_requirements(user_prompt: str, matched_intents: List[str]) -> List[TaskRequirement]:
    """Build ordered task requirements from the user prompt and matched intents.

    This creates the phase structure: create_base -> edge_modify -> shell -> holes -> inspect
    """
    text_l = (user_prompt or "").lower()
    requirements: List[TaskRequirement] = []

    # Phase 1: create_base - check for primitive creation keywords
    if "create_base" in matched_intents:
        # Check what primitive is requested
        primitive_tools = ["box", "cylinder", "sphere",
                           "cone", "torus", "wedge", "helix", "prism"]
        req_tool = "box"  # default
        for tool in primitive_tools:
            if tool in text_l:
                req_tool = tool
                break
        requirements.append(TaskRequirement(
            name="create_base",
            required_subtasks=[req_tool],
            quantity=1
        ))

    # Phase 2: edge_modify - check for fillet/chamfer
    if "edge_modify" in matched_intents:
        # Both fillet and chamfer are required if both keywords present
        subtasks = []
        if "fillet" in text_l or "round" in text_l:
            subtasks.append("fillet")
        if "chamfer" in text_l or "bevel" in text_l:
            subtasks.append("chamfer")
        # If only "edge" or "radius" mentioned, default to both
        if not subtasks:
            subtasks = ["fillet", "chamfer"]
        requirements.append(TaskRequirement(
            name="edge_modify",
            required_subtasks=subtasks,
            quantity=1
        ))

    # Phase 3: shell - check for shell keywords specifically
    shell_keywords = ["shell", "hollow", "thin wall", "container"]
    if any(kw in text_l for kw in shell_keywords):
        requirements.append(TaskRequirement(
            name="shell",
            required_subtasks=["shell"],
            quantity=1
        ))

    # Phase 4: holes - check for hole keywords specifically
    hole_keywords = ["hole", "drill", "bore",
                     "countersink", "thread", "tapped"]
    if any(kw in text_l for kw in hole_keywords):
        # Check for quantity (e.g., "four holes")
        quantity = 1
        qty_match = re.search(r'\b(\d+)\s*(hole|holes)\b', text_l)
        if qty_match:
            quantity = int(qty_match.group(1))
        elif "four" in text_l and "hole" in text_l:
            quantity = 4
        requirements.append(TaskRequirement(
            name="holes",
            required_subtasks=["hole"],
            quantity=quantity
        ))

    # Phase 5: inspect - only if explicitly requested (not auto-added)
    # Check for explicit inspection keywords that indicate the user wants verification
    explicit_inspect_keywords = ["inspect", "check", "measure", "dimension", "volume", "mass",
                                 "properties", "what is", "show", "list",
                                 "center of mass", "bounding box", "bbox"]
    explicit_inspect = any(kw in text_l for kw in explicit_inspect_keywords)
    # Only add inspect phase if explicitly requested AND not just a side-effect of other keywords
    # The "inspect" intent is matched by many keywords; we only add the phase if the user
    # clearly wants post-completion verification
    if "inspect" in matched_intents and explicit_inspect:
        requirements.append(TaskRequirement(
            name="inspect",
            required_subtasks=["get_faces", "get_edges",
                               "get_mass_properties", "get_bom"],
            quantity=1
        ))

    # Phase 6: export - if explicitly requested
    export_keywords = ["export", "save", "download", "stl", "step", "obj", "iges",
                       "3mf", "format", "save as"]
    if any(kw in text_l for kw in export_keywords) and "export_ops" in matched_intents:
        requirements.append(TaskRequirement(
            name="export_ops",
            required_subtasks=["export"],
            quantity=1
        ))

    # If no specific phases detected but we have intents, fall back to simple mapping
    if not requirements and matched_intents:
        for intent in matched_intents:
            config = _INTENT_KEYWORDS.get(intent, {})
            subtasks = config.get("phase_subtasks", [])
            if subtasks:
                requirements.append(TaskRequirement(
                    name=intent,
                    required_subtasks=subtasks,
                    quantity=1
                ))

    return requirements


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
    # Phase-aware fields
    ordered_requirements: List[TaskRequirement] = field(default_factory=list)


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
                ordered_requirements=[],
            )

        # Build ordered task requirements
        ordered_requirements = _build_ordered_requirements(
            user_prompt, matched)

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
            ordered_requirements=ordered_requirements,
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
            # Phase-aware fields
            phase_index=0,
            completed_subtasks={},
            required_subtasks=result.ordered_requirements,
        )
