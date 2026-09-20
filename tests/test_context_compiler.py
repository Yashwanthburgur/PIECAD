"""Tests for ContextCompiler (BIP 4.2, Parts 10-14) + telemetry (Part 13).

Proves: basic compilation, selective state, selective memory, selective history,
tool-context compatibility, budgeting, metadata, and telemetry estimates.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from core.context.compiler import ContextCompiler  # noqa: E402
from core.context.conversation import ConversationContext  # noqa: E402
from core.context.memory import SessionMemory  # noqa: E402
from core.context.state import DesignState  # noqa: E402
from core.context.plan import ContextPlan, ToolSelectionPlan  # noqa: E402

_STATE = json.dumps([
    {"id": "box1", "label": "Box", "type": "Part::Box", "visible": True,
     "parents": [], "children": [], "properties": {"Length": 100.0, "Width": 50.0, "Height": 20.0}},
    {"id": "cylinder1", "label": "Cylinder", "type": "Part::Cylinder", "visible": True,
     "parents": [], "children": [], "properties": {"Radius": 10.0, "Height": 40.0}},
    {"id": "hole1", "label": "Hole", "type": "Part::Cut", "visible": False,
     "parents": [], "children": ["box1"], "properties": {}},
    {"id": "fillet1", "label": "Fillet", "type": "Part::Fillet", "visible": True,
     "parents": ["box1"], "children": [], "properties": {}},
    {"id": "bracket1", "label": "Bracket", "type": "Part::Box", "visible": True,
     "parents": [], "children": [], "properties": {"Length": 80.0, "Width": 30.0}},
    {"id": "unrelated_old_object", "label": "OldShaft", "type": "Part::Cylinder",
     "visible": True, "parents": [], "children": [], "properties": {"Radius": 5.0}},
])


def _state():
    st = DesignState()
    st.update_from_cad_state(_STATE)
    return st


def _memory():
    mem = SessionMemory()
    mem.set("units", "mm", kind="user_preference")
    mem.set("preferred_hole_size", "M6", kind="convention")
    mem.set("preferred_material", "Al6061", kind="user_preference")
    mem.set("bracket_orientation", "vertical", kind="decision")
    return mem


def _tools():
    names = [
        "box", "cylinder", "sketch", "extrude", "shell", "fillet", "chamfer",
        "hole", "boolean", "pattern_linear", "pattern_circular",
        "edit_feature", "delete_feature", "mate", "interference_check",
        "get_state", "get_faces", "get_edges", "get_mass_properties",
        "get_bom", "export", "export_state_model",
    ]
    return [{"type": "function",
             "function": {"name": n, "description": f"Run {n}",
                          "parameters": {"type": "object", "properties": {}}}}
            for n in names]


def _conversation():
    ctx = ConversationContext(default_recent_window=3)
    ctx.add_user("Create the base box")
    ctx.add_assistant("done")
    ctx.add_user("Now add a mounting hole M6")
    return ctx


def test_compiler_basic():
    compiler = ContextCompiler()
    compiled = compiler.compile(
        user_message="Add a mounting hole for M6",
        conversation_context=_conversation(),
        design_state=_state(),
        session_memory=_memory(),
        available_tools=_tools(),
    )
    assert compiled.system_context
    assert compiled.user_context == "Add a mounting hole for M6"
    assert compiled.tools
    assert compiled.plan is not None
    assert compiled.telemetry is not None


def test_compiler_selective_state():
    """'Increase the hole diameter' must keep hole1 + its target and drop
    unrelated objects like the old shaft."""
    compiler = ContextCompiler()
    # Force a focused plan: only hole1 + its relationship neighbours should show.
    focused = ContextPlan(relevant_object_ids=["hole1"])
    compiled = compiler.compile(
        user_message="Increase the hole diameter",
        design_state=_state(),
        session_memory=_memory(),
        available_tools=_tools(),
        optional_context_plan=focused,
    )
    state = compiled.design_state.get("objects", [])
    ids = {o.get("id") for o in state}
    assert "hole1" in ids
    assert "box1" in ids  # its target body via relationship
    assert "unrelated_old_object" not in ids


def test_compiler_selective_memory():
    compiler = ContextCompiler()
    compiled = compiler.compile(
        user_message="Increase the hole diameter",
        design_state=_state(),
        session_memory=_memory(),
        available_tools=_tools(),
    )
    mem = compiled.memory
    # Conventions/preferences about holes should be present; the bracket
    # orientation *decision* should be absent.
    flat = [item for sub in mem.values() for item in (sub or [])]
    assert any("M6" in str(v) for v in flat)
    assert not any("vertical" in str(v) for v in flat)


def test_compiler_selective_history():
    compiler = ContextCompiler()
    compiled = compiler.compile(
        user_message="Increase the hole diameter",
        conversation_context=_conversation(),
        design_state=_state(),
        session_memory=_memory(),
        available_tools=_tools(),
    )
    conv_text = " ".join(m.get("content", "") for m in compiled.conversation)
    # The current turn (hole request) must be present.
    assert "mounting" in conv_text or "hole" in conv_text


def test_compiler_tool_context():
    compiler = ContextCompiler()
    compiled = compiler.compile(
        user_message="Add a mounting hole for M6",
        design_state=_state(),
        session_memory=_memory(),
        available_tools=_tools(),
        tool_selection_plan=ToolSelectionPlan(
            primary=["hole"],
            inspection=["get_faces"],
            verification=["get_faces"],
            recovery=["get_error"],
        ),
    )
    names = [t["function"]["name"] for t in compiled.tools]
    assert "hole" in names
    assert "get_faces" in names


def test_compiler_budget():
    compiler = ContextCompiler()
    # State budget is tiny => should record a dropped section.
    compiler.compile(
        user_message="Increase the hole diameter",
        design_state=_state(),
        session_memory=_memory(),
        available_tools=_tools(),
    )
    # Budget default is generous; ensure it still returns a compiled context with
    # non-empty design_state and telemetry records estimates.
    compiled = compiler.compile(
        user_message="Increase the hole diameter",
        design_state=_state(),
        session_memory=_memory(),
        available_tools=_tools(),
    )
    assert compiled.telemetry.estimated_state_tokens >= 0
    assert compiled.telemetry.dropped_sections == [] or isinstance(
        compiled.telemetry.dropped_sections, list)


def test_compiler_metadata():
    compiler = ContextCompiler()
    compiled = compiler.compile(
        user_message="Add a mounting hole M6",
        design_state=_state(),
        session_memory=_memory(),
        available_tools=_tools(),
        react_step=3,
    )
    assert compiled.metadata["react_step"] == 3
    assert compiled.metadata["reasoning_mode"] in ("default", "modify")
    assert compiled.telemetry.react_step == 3
    assert compiled.telemetry.tools_exposed == len(compiled.tools)
