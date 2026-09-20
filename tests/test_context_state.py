"""Tests for DesignState (BIP 4.2, Part 1-3).

Proves: creation, incremental update, snapshot, snapshot dict, object lookup,
object relationships, serialization, ghost resolution, and selective views.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from core.context.state import DesignState  # noqa: E402

# Mirrors the real FreeCAD state schema (list of object dicts).
_BOX_HOLE_STATE = [
    {
        "id": "box1",
        "label": "Box",
        "type": "Part::Box",
        "visible": True,
        "parents": [],
        "children": [],
        "properties": {"Length": 100.0, "Width": 50.0, "Height": 20.0},
    },
    {
        "id": "hole1",
        "label": "Hole",
        "type": "Part::Cut",
        "visible": False,  # consumed (ghost) by a downstream feature
        "parents": [],
        "children": ["box1"],
        "properties": {},
    },
]


def test_state_creation():
    state = DesignState()
    assert state.snapshot()["objects"] == []
    assert state.get_object("nope") is None


def test_state_update():
    state = DesignState()
    state.update_from_cad_state(json.dumps(_BOX_HOLE_STATE))
    assert state.get_object("box1") is not None
    assert len(state.objects) == 2


def test_state_snapshot():
    state = DesignState()
    state.update_from_cad_state(json.dumps(_BOX_HOLE_STATE))
    snap = state.snapshot()
    assert set(snap.keys()) >= {
        "active_document", "objects", "selected_entities",
        "recent_operations", "recent_errors", "current_task",
        "current_intent", "derived_facts",
    }
    assert len(snap["objects"]) == 2


def test_object_lookup():
    state = DesignState()
    state.update_from_cad_state(json.dumps(_BOX_HOLE_STATE))
    box = state.get_object("box1")
    assert box is not None
    assert box.object_type == "Part::Box"
    assert box.properties.get("Length") == 100.0
    assert box.is_solid()


def test_state_serialization():
    state = DesignState()
    state.update_from_cad_state(json.dumps(_BOX_HOLE_STATE))
    state.set_selection(["box1"])
    blob = json.dumps(state.snapshot())
    reparsed = json.loads(blob)
    # Round-trip: object ids preserved, selection preserved.
    assert {o["id"] for o in reparsed["objects"]} == {"box1", "hole1"}
    assert reparsed["selected_entities"] == ["box1"]


def test_object_relationships():
    # hole1 lists box1 as a child (its target body).
    state = DesignState()
    state.update_from_cad_state(json.dumps(_BOX_HOLE_STATE))
    hole = state.get_object("hole1")
    assert hole.relationships()["children"] == ["box1"]
    # A box with no links has empty relationships.
    box = state.get_object("box1")
    assert box.relationships() == {}


def test_selective_view_includes_target_and_relationships():
    state = DesignState()
    state.update_from_cad_state(json.dumps(_BOX_HOLE_STATE))
    view = state.select_objects(["hole1"], depth=1)
    ids = {o["id"] for o in view["objects"]}
    # Hole pull-in brings in its related box1.
    assert "hole1" in ids
    assert "box1" in ids


def test_ghost_resolution():
    state = DesignState()
    # hole1 is invisible; its visible child (box1) should resolve.
    state.update_from_cad_state(json.dumps(_BOX_HOLE_STATE))
    resolved = state.resolve_active_object("hole1")
    assert resolved is not None
    assert resolved.object_id == "box1"


def test_run_update_from_tool_result():
    state = DesignState()
    state.update_from_tool_result(
        "hole", '{"success": true}', target_id="hole2",
        args={"target_id": "box1"}, success=True,
    )
    ops = state.get_recent_operations()
    assert len(ops) == 1
    assert ops[0].tool == "hole"
    assert state.get_object("hole2") is not None


def test_recent_errors_recording():
    state = DesignState()
    state.update_from_tool_result(
        "fillet", None, target_id="box1", success=False, error="BRep_API not done"
    )
    errs = state.get_recent_errors()
    assert len(errs) == 1
    assert "fillet" in errs[0]
