"""BIP 4.2.2 — Adversarial: E. Tool Selection.

Verify the compiler does not blindly expose all available CAD tools, that it
unions router-selected and plan-required tools, dedups, and handles empty/unknown
tool inputs gracefully.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from core.context.compiler import ContextCompiler  # noqa: E402
from core.context.state import DesignState  # noqa: E402
from core.context.memory import SessionMemory  # noqa: E402
from core.context.plan import ContextPlan  # noqa: E402


def _state(objs=None):
    st = DesignState()
    st.update_from_cad_state(json.dumps(objs or [
        {"id": "box1", "label": "Box", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100.0}},
    ]))
    return st


def _tools():
    names = [
        "box", "cylinder", "sketch", "extrude", "shell", "fillet", "chamfer",
        "hole", "boolean", "pattern_linear", "pattern_circular",
        "edit_feature", "delete_feature", "mate", "interference_check",
        "get_state", "get_faces", "get_edges", "get_mass_properties",
        "get_bom", "export", "export_state_model",
    ]
    return [{"type": "function",
             "function": {"name": n, "description": f"run {n}",
                          "parameters": {"type": "object", "properties": {}}}}
            for n in names]


def _compile(request="Add a hole", plan=None, tools=None):
    comp = ContextCompiler()
    return comp.compile(
        user_message=request,
        design_state=_state(), session_memory=SessionMemory(),
        available_tools=tools if tools is not None else _tools(),
        optional_context_plan=plan,
    )


def test_not_all_tools_exposed():
    compiled = _compile("Add a mounting hole")
    exposed = [t["function"]["name"] for t in compiled.tools]
    assert "hole" in exposed
    # Not everything is exposed for a hole request.
    assert "mate" not in exposed
    assert "interference_check" not in exposed


def test_router_plus_plan_required_union():
    # Plan requires "export" explicitly; router supplies geometry tools.
    plan = ContextPlan(required_tools=["export"])
    compiled = _compile(plan=plan)
    names = {t["function"]["name"] for t in compiled.tools}
    assert "export" in names  # from plan
    assert "get_state" in names  # from router (query)
    assert "box" in names  # from router (primitive)


def test_duplicate_tools_deduped():
    plan = ContextPlan(required_tools=["export", "export", "get_state"])
    compiled = _compile(plan=plan)
    names = [t["function"]["name"] for t in compiled.tools]
    assert names.count("export") == 1
    assert names.count("get_state") == 1


def test_empty_tool_selection_returns_empty_tools():
    compiled = _compile(request="Add a hole", tools=[])
    assert compiled.tools == []


def test_unknown_tool_names_ignored():
    # A plan requiring a tool not in the adapter surface -> not exposed.
    plan = ContextPlan(required_tools=["nonexistent_tool"])
    compiled = _compile(plan=plan)
    names = {t["function"]["name"] for t in compiled.tools}
    assert "nonexistent_tool" not in names


def test_irrelevant_tools_omitted():
    # With a single solid, the router never exposes assembly/boolean tools; an
    # export request must not pull in unrelated creation/modification tools just
    # because they exist in the adapter surface.
    comp = ContextCompiler()
    compiled = comp.compile(
        user_message="Export as STEP", design_state=_state(),
        session_memory=SessionMemory(), available_tools=_tools(),
    )
    names = {t["function"]["name"] for t in compiled.tools}
    assert "export" in names
    assert "get_state" in names  # query available
    # A single-solid export request does not need assembly tools exposed.
    assert "mate" not in names
    assert "interference_check" not in names


def test_tool_count_reported_matches_exposed():
    compiled = _compile("Add a hole")
    assert compiled.tools_exposed == len(compiled.tools)
    if compiled.telemetry:
        assert compiled.telemetry.tools_exposed == len(compiled.tools)
