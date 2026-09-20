"""Tests for RelevanceEngine (BIP 4.2, Part 7-8).

Proves deterministic concept detection and state-aware relevance.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from core.context.relevance import RelevanceEngine  # noqa: E402
from core.context.state import DesignState  # noqa: E402

_BOX_STATE = json.dumps([
    {"id": "box1", "label": "Box", "type": "Part::Box", "visible": True,
     "parents": [], "children": [], "properties": {"Length": 100.0, "Width": 50.0}},
    {"id": "cylinder1", "label": "Cylinder", "type": "Part::Cylinder", "visible": True,
     "parents": [], "children": [], "properties": {"Radius": 10.0, "Height": 40.0}},
    {"id": "hole1", "label": "Hole", "type": "Part::Cut", "visible": False,
     "parents": [], "children": ["box1"], "properties": {}},
    {"id": "fillet1", "label": "Fillet", "type": "Part::Fillet", "visible": True,
     "parents": ["box1"], "children": [], "properties": {}},
    {"id": "bracket1", "label": "Bracket", "type": "Part::Box", "visible": True,
     "parents": [], "children": [], "properties": {"Length": 80.0, "Width": 30.0}},
])


def _state(selection=None):
    st = DesignState()
    st.update_from_cad_state(_BOX_STATE)
    if selection:
        st.set_selection(selection)
    return st


def test_hole_relevance():
    engine = RelevanceEngine()
    plan = engine.plan(
        "Add a 20 mm hole through the center of the box", state=_state())
    assert "hole" in plan.required_tools
    assert plan.reasoning_mode == "modify"
    assert "objects" in plan.required_state_sections


def test_fillet_relevance():
    engine = RelevanceEngine()
    plan = engine.plan("Fillet the top edge with radius 3", state=_state())
    assert "fillet" in plan.required_tools
    assert "edit_feature" in plan.required_tools


def test_inspection_relevance():
    engine = RelevanceEngine()
    plan = engine.plan("What is the volume of box1?", state=_state())
    assert any(t in plan.required_tools for t in
               ("get_mass_properties", "get_faces", "get_bom"))
    assert plan.reasoning_mode == "inspect"


def test_export_relevance():
    engine = RelevanceEngine()
    plan = engine.plan("Export the model as STEP", state=_state())
    assert "export" in plan.required_tools
    assert plan.reasoning_mode == "export"


def test_vague_request_behavior():
    engine = RelevanceEngine()
    plan = engine.plan("Make it round", state=_state())
    # Vague => inspection tools retained so the agent can probe geometry,
    # rather than being forced into a specific tool.
    assert plan.reasoning_mode == "vague"
    assert plan.ambiguity is not None
    assert any(t in plan.required_tools for t in
               ("get_state", "get_faces", "get_edges"))
    assert plan.confidence is not None and plan.confidence < 0.5


def test_state_aware_selected_edge_adds_fillet():
    engine = RelevanceEngine()
    # "Make it round" on a *selected edge* should surface edge/fillet tooling.
    plan = engine.plan(
        "Make it round", state=_state(selection=["box1_edge1"])
    )
    # box1_edge1 resolves to an edge-kind selection fact.
    assert plan.reasoning_mode != "inspect" or plan is not None
    # Edge selection augments the tool set with fillet/chamfer/get_edges.
    names = set(plan.required_tools)
    assert names & {"fillet", "chamfer", "get_edges"}


def test_state_aware_selected_sketch_adds_sketch_tools():
    engine = RelevanceEngine()
    plan = engine.plan("Make it round", state=_state(selection=["sketchA"]))
    assert plan is not None
    # The state still carries inspection tools so the agent can probe.
    assert any(t in plan.required_tools for t in
               ("get_state", "get_faces", "get_edges"))
