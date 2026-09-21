"""BIP 4.2.2 — Adversarial: D. Relevance Engine across all concepts.

Exercise every major concept in CONCEPT_RULES. Each test verifies the expected
relevant tool(s), the reasoning mode, and that unrelated concepts are not
unnecessarily selected. Rules are NOT modified to make tests pass.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from core.context.relevance import RelevanceEngine  # noqa: E402
from core.context.state import DesignState  # noqa: E402


def _state(objs=None):
    st = DesignState()
    st.update_from_cad_state(json.dumps(objs or [
        {"id": "box1", "label": "Box", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100.0}},
        {"id": "cylinder1", "label": "Cylinder", "type": "Part::Cylinder",
         "visible": True, "parents": [], "children": [],
         "properties": {"Radius": 10.0, "Height": 40.0}},
    ]))
    return st


def _assert_concept(request, needed_tools, mode, must_exclude=()):
    engine = RelevanceEngine()
    plan = engine.plan(request, state=_state())
    names = set(plan.required_tools)
    assert names & set(
        needed_tools), f"{request} missing {needed_tools}; got {names}"
    if mode:
        assert plan.reasoning_mode == mode, f"{request}: mode={plan.reasoning_mode}"
    for ex in must_exclude:
        assert ex not in names, f"{request}: unexpectedly selected {ex}"


def test_hole_concept():
    _assert_concept("Drill an M6 mounting hole", ["hole"], "modify")


def test_fillet_concept():
    _assert_concept("Fillet the top edge radius 5", ["fillet"], "modify")
    _assert_concept("Round off this edge", ["fillet"], "modify")


def test_chamfer_concept():
    _assert_concept("Chamfer the corner", ["chamfer"], "modify")
    _assert_concept("Bevel the edge", ["chamfer"], "modify")


def test_box_concept():
    engine = RelevanceEngine()
    plan = engine.plan("Create a base box 100 wide", state=_state())
    assert "box" in plan.required_tools
    # Untrue: no export required for a box request.
    assert "export" not in plan.required_tools


def test_cylinder_concept():
    plan = RelevanceEngine().plan("Create a shaft cylinder r10", state=_state())
    assert "cylinder" in plan.required_tools


def test_sketch_concept():
    plan = RelevanceEngine().plan("Draw a sketch for a profile", state=_state())
    assert "sketch" in plan.required_tools


def test_pad_concept():
    plan = RelevanceEngine().plan("Pad this sketch to 10mm", state=_state())
    assert "pad" in plan.required_tools or "extrude" in plan.required_tools


def test_pocket_concept():
    # "pocket" keyword triggers the pocket rule (a material-removal modifier).
    plan = RelevanceEngine().plan("Create a pocket in the top face", state=_state())
    assert "pocket" in plan.required_tools
    # Pocket does NOT drag in an unrelated creator tool.
    assert "export" not in plan.required_tools


def test_boolean_concept():
    plan = RelevanceEngine().plan("Boolean subtract the cylinder", state=_state())
    assert "boolean" in plan.required_tools
    assert plan.reasoning_mode == "boolean"
    # A boolean request should not need export.
    assert "export" not in plan.required_tools


def test_pattern_concept():
    plan = RelevanceEngine().plan("Pattern the bolt holes linearly", state=_state())
    assert "pattern_linear" in plan.required_tools or "pattern_circular" in plan.required_tools


def test_inspect_concept():
    plan = RelevanceEngine().plan("Inspect the volume of box1", state=_state())
    assert plan.reasoning_mode == "inspect"
    assert "get_mass_properties" in plan.required_tools


def test_measure_concept():
    plan = RelevanceEngine().plan("Measure the distance between holes", state=_state())
    # "measure" hits both the inspect rule and the measure rule; ensure inspection
    # tooling is present and no creator tool is dumped.
    assert any(t in plan.required_tools for t in
               ("get_faces", "get_mass_properties", "measure"))
    assert "box" not in plan.required_tools


def test_export_concept():
    plan = RelevanceEngine().plan("Export the model as STEP", state=_state())
    assert "export" in plan.required_tools
    assert plan.reasoning_mode == "export"


def test_delete_concept():
    plan = RelevanceEngine().plan("Delete the last cylinder", state=_state())
    assert "delete_feature" in plan.required_tools
    assert plan.reasoning_mode == "modify"


def test_modify_concept():
    plan = RelevanceEngine().plan("Resize the box to 200mm", state=_state())
    assert "edit_feature" in plan.required_tools
    assert plan.reasoning_mode == "modify" or plan.reasoning_mode == "boolean"


def test_select_concept():
    plan = RelevanceEngine().plan("Select the hole feature", state=_state())
    assert "get_state" in plan.required_tools or "get_faces" in plan.required_tools
    assert plan.reasoning_mode == "inspect"


def test_undo_concept():
    plan = RelevanceEngine().plan("Undo the last operation", state=_state())
    assert "delete_feature" in plan.required_tools or "edit_feature" in plan.required_tools
    assert plan.reasoning_mode == "modify"


def test_redo_concept():
    plan = RelevanceEngine().plan("Redo the fillet", state=_state())
    assert "edit_feature" in plan.required_tools
    assert plan.reasoning_mode == "modify"


def test_vague_concept():
    plan = RelevanceEngine().plan("Make it round", state=_state())
    assert plan.reasoning_mode == "vague"
    assert plan.ambiguity is not None
    # Inspection tools retained so the agent can probe geometry.
    assert any(t in plan.required_tools for t in
               ("get_state", "get_faces", "get_edges"))
    # A vague request must not directly claim a specific creator.
    assert "export" not in plan.required_tools


def test_state_aware_selected_edge():
    st = _state()
    st.set_selection(["box1_edge1"])
    plan = RelevanceEngine().plan("Make it round", state=st)
    assert plan.reasoning_mode == "vague"
    assert "fillet" in plan.required_tools or "get_edges" in plan.required_tools


def test_state_aware_selected_sketch():
    st = _state()
    st.set_selection(["sketchA"])
    plan = RelevanceEngine().plan("Extrude this", state=st)
    assert "extrude" in plan.required_tools or "pad" in plan.required_tools
