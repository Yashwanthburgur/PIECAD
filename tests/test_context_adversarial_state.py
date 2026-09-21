"""BIP 4.2.2 — Adversarial: A. State Evolution.

Stress-test DesignState across realistic CAD state transitions. Verifies it stays
the authoritative representation of the latest state without duplicates or stale
objects leaking through.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from core.context.state import DesignState  # noqa: E402


def _obj(obj_id, obj_type, visible=True, parents=None, children=None, props=None):
    return {
        "id": obj_id, "label": obj_id, "type": obj_type, "visible": visible,
        "parents": list(parents or []), "children": list(children or []),
        "properties": dict(props or {}),
    }


def _box(oid="box1", **kw):
    return _obj(oid, "Part::Box", props={"Length": 100, "Width": 50, "Height": 20}, **kw)


def test_empty_state():
    st = DesignState()
    assert len(st.objects) == 0
    assert st.get_recent_operations() == []
    assert st.get_recent_errors() == []


def test_state_evolution_sequence():
    # Empty -> box -> box+cylinder -> box+cylinder+hole
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box()]))
    assert set(st.objects) == {"box1"}

    st.update_from_cad_state(json.dumps([_box(), _obj("cylinder1", "Part::Cylinder",
                                                      props={"Radius": 10, "Height": 40})]))
    assert set(st.objects) == {"box1", "cylinder1"}

    hole = _obj("hole1", "Part::Cut", visible=False, children=["box1"])
    st.update_from_cad_state(json.dumps([_box(), _obj("cylinder1", "Part::Cylinder",
                                                      props={"Radius": 10, "Height": 40}), hole]))
    assert set(st.objects) == {"box1", "cylinder1", "hole1"}


def test_object_removed_from_latest_state():
    st = DesignState()
    st.update_from_cad_state(json.dumps(
        [_box(), _obj("cylinder1", "Part::Cylinder")]))
    assert "cylinder1" in st.objects
    # Remove the cylinder -> latest state no longer has it.
    st.update_from_cad_state(json.dumps([_box()]))
    assert set(st.objects) == {"box1"}


def test_visibility_change():
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box(visible=True)]))
    assert st.objects["box1"].visible is True
    st.update_from_cad_state(json.dumps([_box(visible=False)]))
    assert st.objects["box1"].visible is False
    # No visible child -> ghost resolves to itself (still invisible).
    resolved = st.resolve_active_object("box1")
    assert resolved is not None and resolved.object_id == "box1"


def test_ghost_resolves_to_visible_child():
    st = DesignState()
    hole = _obj("hole1", "Part::Cut", visible=False, children=["box1"])
    st.update_from_cad_state(json.dumps([_box(), hole]))
    resolved = st.resolve_active_object("hole1")
    assert resolved is not None
    assert resolved.object_id == "box1"
    assert resolved.visible is True


def test_parent_child_relationship():
    st = DesignState()
    hole = _obj("hole1", "Part::Cut", children=["box1"])
    st.update_from_cad_state(json.dumps([_box(), hole]))
    assert st.objects["hole1"].children == ["box1"]
    view = st.select_objects(["hole1"], depth=1)
    ids = {o["id"] for o in view["objects"]}
    assert "box1" in ids  # relationship expansion pulls in the target


def test_snapshot_matches_latest_objects():
    st = DesignState()
    st.update_from_cad_state(json.dumps(
        [_box(), _obj("cylinder1", "Part::Cylinder")]))
    snap = st.snapshot()
    assert {o["id"] for o in snap["objects"]} == {"box1", "cylinder1"}


def test_ghost_stale_reference_does_not_crash():
    # selected_entities may reference an object that no longer exists; the state
    # must not crash and must not fabricate it.
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box()]))
    st.set_selection(["ghost_edge", "box1"])
    assert st.resolve_active_object("ghost_edge") is None
    summary = st.summary()
    assert "ghost_edge" not in summary["object_ids"]


def test_repeated_updates_do_not_duplicate_objects():
    st = DesignState()
    state_list = [json.dumps([_box()]), json.dumps(
        [_box()]), json.dumps([_box()])]
    for s in state_list:
        st.update_from_cad_state(s)
    # Upsert semantics: same id, never duplicated.
    assert len([o for o in st.objects if o == "box1"]) == 1
    assert len(st.objects) == 1


def test_update_from_tool_result_keeps_operations_bounded():
    st = DesignState()
    for i in range(50):
        st.update_from_tool_result(
            "hole", "ok", target_id=f"h{i}", success=True)
    assert len(st.get_recent_operations()) <= 20
