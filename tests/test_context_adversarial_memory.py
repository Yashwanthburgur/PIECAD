"""BIP 4.2.2 — Adversarial: C. Memory Relevance.

Verify that a request only receives relevant memory sections, and that irrelevant
memory does NOT leak into compiled context merely because it exists.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context.memory import (  # noqa: E402
    SessionMemory,
    KIND_FACT,
    KIND_PREFERENCE,
    KIND_DECISION,
    KIND_CONVENTION,
    KIND_CORRECTION,
    KIND_ASSUMPTION,
    KIND_INFERENCE,
)
from core.context.compiler import ContextCompiler  # noqa: E402
from core.context.state import DesignState  # noqa: E402
from core.context.plan import ContextPlan  # noqa: E402

import json  # noqa: E402


def _full_memory():
    mem = SessionMemory()
    mem.set("units", "mm", kind=KIND_PREFERENCE)
    mem.set("preferred_surface_finish", "anodized", kind=KIND_PREFERENCE)
    mem.set("preferred_hole_size", "M6", kind=KIND_CONVENTION)
    mem.set("unrelated_bracket_slot", "3mm slot", kind=KIND_CONVENTION)
    mem.set("bracket_orientation", "vertical", kind=KIND_DECISION)
    mem.set("material_choice", "Al6061", kind=KIND_FACT)
    mem.set("default_hole_was_m8_now_m6", True, kind=KIND_CORRECTION)
    mem.set("thread_assumption", "coarse", kind=KIND_ASSUMPTION)
    mem.set("wall_thickness_inference", 2.0, kind=KIND_INFERENCE)
    return mem


def _state(obj_id="box1"):
    st = DesignState()
    st.update_from_cad_state(json.dumps([{"id": obj_id, "label": obj_id,
                                          "type": "Part::Box", "visible": True,
                                          "parents": [], "children": [],
                                          "properties": {"Length": 100.0}}]))
    return st


def _tools():
    names = ["box", "hole", "get_state", "edit_feature"]
    return [{"type": "function",
             "function": {"name": n, "parameters": {"type": "object",
                                                    "properties": {}}}}
            for n in names]


def test_hole_request_only_receives_hole_related_memory():
    comp = ContextCompiler()
    compiled = comp.compile(
        user_message="Add a mounting hole for M6",
        design_state=_state(), session_memory=_full_memory(),
        available_tools=_tools(),
    )
    mem = compiled.memory
    flat = [item for sub in mem.values() for item in (sub or [])]
    # The requested hole size convention is relevant and must be present.
    assert any("M6" in str(v) for v in flat)
    # Unrelated memory does NOT leak in.
    assert not any("3mm slot" in str(v) for v in flat)
    assert not any("anodized" in str(v) for v in flat)
    assert not any("vertical" in str(v) for v in flat)


def test_irrelevant_memory_does_not_leak_because_it_exists():
    # Regression: a wanted kind (conventions) with an unrelated entry must not
    # include that unrelated entry merely because the kind is required by plan.
    comp = ContextCompiler()
    plan = ContextPlan(required_tools=["hole"], relevant_object_ids=["box1"],
                       required_state_sections=["objects"],
                       required_memory_sections=["conventions"])
    compiled = comp.compile(
        user_message="Add a mounting hole for M6",
        design_state=_state(), session_memory=_full_memory(),
        available_tools=_tools(), optional_context_plan=plan,
    )
    mem = compiled.memory
    flat = [item for sub in mem.values() for item in (sub or [])]
    assert any("M6" in str(v) for v in flat)
    assert not any("3mm slot" in str(v) for v in flat)


def test_correction_memory_surfaces_on_fix_request():
    comp = ContextCompiler()
    compiled = comp.compile(
        user_message="Actually the hole default should be M6, fix it",
        design_state=_state(), session_memory=_full_memory(),
        available_tools=_tools(),
    )
    flat = [item for sub in compiled.memory.values() for item in (sub or [])]
    assert any("M6" in str(v) for v in flat)


def test_surface_finish_preference_not_leaked_for_hole():
    comp = ContextCompiler()
    compiled = comp.compile(
        user_message="Add a through hole diameter 12",
        design_state=_state(), session_memory=_full_memory(),
        available_tools=_tools(),
    )
    flat = [item for sub in compiled.memory.values() for item in (sub or [])]
    # Aesthetic surface-finish preference is irrelevant to a bare hole request.
    assert not any("anodized" in str(v) for v in flat)
    # The diameter default convention is relevant.
    assert any("M6" in str(v) for v in flat)
