#!/usr/bin/env python
"""BIP 4.3.2 — State Integrity & Unknown-State Safety live proof (standalone).

This script directly tests the implemented fixes without requiring a live
FreeCAD bridge, demonstrating:
1. Successful state refresh.
2. get_state() failure after a known-good state does NOT wipe the state.
3. Empty CAD document vs. unavailable CAD state distinction.
4. Failed tool does not fabricate state.
5. Successful recovery updates state correctly.
6. Requested-vs-achieved parameter mismatch remains observable.
"""

from core.agent import CADAgent
from core.adapters.interfaces import CADAdapter
from core.context.state import DesignState
import sys
import json
from pathlib import Path

# Ensure project root is on sys.path FIRST
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# --------------------------------------------------------------------------- #
# Test 1: DesignState unit-level verification
# --------------------------------------------------------------------------- #

def _obj(obj_id, obj_type, visible=True, parents=None, children=None, props=None):
    return {
        "id": obj_id, "label": obj_id, "type": obj_type, "visible": visible,
        "parents": list(parents or []), "children": list(children or []),
        "properties": dict(props or {}),
    }


def _box(oid="box1", **kw):
    return _obj(oid, "Part::Box", props={"Length": 100, "Width": 50, "Height": 20}, **kw)


def test_designstate_unit():
    """Verify DesignState-level fixes directly."""
    print("=== DesignState Unit Tests ===")

    st = DesignState()

    # 1. Successful state refresh
    st.update_from_cad_state(json.dumps([_box()]))
    assert st.state_available is True, "state_available should be True after success"
    assert st.state_stale is False, "state_stale should be False after success"
    assert st.last_successful_sync is not None
    assert "box1" in st.objects
    print("✓ Successful state refresh marks state_available=True, state_stale=False")

    # 2. get_state failure preserves objects
    st.update_from_cad_state("not valid json")
    assert st.state_available is False, "state_available should be False after failure"
    assert st.state_stale is True, "state_stale should be True after failure"
    assert "box1" in st.objects, "Objects must NOT be wiped on failure"
    print("✓ Failed retrieval preserves objects, marks state_unavailable")

    # 3. Empty document vs unavailable
    st2 = DesignState()
    st2.update_from_cad_state("[]")
    assert st2.state_available is True, "Empty list is valid empty document"
    assert st2.state_stale is False
    assert len(st2.objects) == 0
    print("✓ Empty document ('[]') is distinct from unavailable state")

    # 4. mark_state_unavailable
    st3 = DesignState()
    st3.update_from_cad_state(json.dumps([_box()]))
    st3.mark_state_unavailable()
    assert st3.state_available is False
    assert st3.state_stale is True
    assert "box1" in st3.objects
    print("✓ mark_state_unavailable() flags stale without clearing objects")

    # 5. Snapshot includes flags
    snap = st.snapshot()
    assert "state_available" in snap and snap["state_available"] is False
    assert "state_stale" in snap and snap["state_stale"] is True
    assert "last_successful_sync" in snap
    print("✓ snapshot() includes state_available, state_stale, last_successful_sync")

    # 6. Summary includes flags
    summ = st.summary()
    assert summ["state_available"] is False
    assert summ["state_stale"] is True
    print("✓ summary() includes state_available, state_stale")

    # 7. RecentOperation requested_args serialization
    from core.context.state import RecentOperation
    op = RecentOperation(tool="fillet", args={
                         "radius": 5}, requested_args={"radius": 500})
    d = op.to_dict()
    assert d["args"]["radius"] == 5
    assert d["requested_args"]["radius"] == 500
    print("✓ RecentOperation.to_dict() serializes requested_args")

    print("")


# --------------------------------------------------------------------------- #
# Test 2: Agent integration with stub adapter (simulates live behavior)
# --------------------------------------------------------------------------- #

class ScriptedProvider:
    """Returns scripted LLM responses."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.last_usage = None

    def generate_with_tools(self, messages, tools=None):
        from types import SimpleNamespace
        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        content, tool_calls = self.script[idx]
        tcs = None
        if tool_calls is not None:
            tcs = [
                SimpleNamespace(
                    id=f"call_{i}",
                    function=SimpleNamespace(
                        name=nm, arguments=json.dumps(ar)),
                )
                for i, (nm, ar) in enumerate(tool_calls)
            ]
        return SimpleNamespace(content=content, tool_calls=tcs)


class StubAdapter(CADAdapter):
    """Minimal adapter with scriptable behavior."""

    def __init__(self, tool_names=None, behavior=None, state_sequence=None):
        self.tool_names = tool_names or [
            "box", "fillet", "get_edges", "get_state"]
        self.behavior = behavior or {}
        self.objects = []
        self.state_sequence = state_sequence or []
        self.state_index = 0
        self.calls = []

    def get_tools(self):
        return [{"type": "function", "function": {
            "name": n, "description": f"run {n}",
            "parameters": {"type": "object", "properties": {}}}} for n in self.tool_names]

    def execute_command(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))
        spec = self.behavior.get(tool_name, ("ok", "ok"))
        if callable(spec):
            spec = spec(kwargs)
        if isinstance(spec, tuple) and len(spec) >= 1:
            mode = spec[0]
            if mode == "raise":
                raise spec[1]
        if tool_name == "box":
            self.objects.append({"id": "box1", "type": "Part::Box",
                                 "visible": True, "parents": [], "children": [],
                                 "properties": kwargs})
        return "ok"

    def get_state(self):
        if self.state_sequence:
            s = self.state_sequence[self.state_index]
            self.state_index = min(self.state_index + 1,
                                   len(self.state_sequence) - 1)
            return json.dumps(s)
        return json.dumps(self.objects)


def test_failed_tool_no_fabrication():
    """A non-transient failure never creates fake CAD objects."""
    print("=== Agent Integration: Failed Tool Does Not Fabricate State ===")

    adapter = StubAdapter(behavior={"fillet": (
        "raise", RuntimeError("radius too large"))})
    prov = ScriptedProvider([
        (None, [("fillet", {"radius": 500})]),
        ("Fillet failed.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    agent.handle_message("Fillet the edge 500mm")

    assert len(
        agent.design_state.objects) == 0, "No fake geometry from failed call"
    errs = agent.design_state.get_recent_errors()
    assert any("fillet" in e for e in errs), "Failure recorded in recent_errors"
    print("✓ Failed fillet does not fabricate CAD objects")
    print("✓ Failure recorded in recent_errors")
    print("")


def test_recovery_updates_state():
    """Recovery after failure updates DesignState via immediate sync."""
    print("=== Agent Integration: Recovery Updates State ===")

    adapter = StubAdapter(
        tool_names=["box", "fillet", "get_edges", "get_state"],
        behavior={
            "fillet": lambda kwargs: (
                ("raise", RuntimeError("radius too large"))
                if kwargs.get("radius", 0) > 10 else ("ok", "fillet applied")),
            "get_edges": ("ok", '[{"edge_id": "e1"}]'),
        },
        state_sequence=[
            [{"id": "box1", "type": "Part::Box", "visible": True,
              "parents": [], "children": [], "properties": {"Length": 100}}],
            [{"id": "box1", "type": "Part::Box", "visible": True,
              "parents": [], "children": [], "properties": {"Length": 100}}],
            [{"id": "fillet1", "type": "Part::Fillet", "visible": True,
              "parents": ["box1"], "children": [], "properties": {}},
             {"id": "box1", "type": "Part::Box", "visible": False,
              "parents": [], "children": ["fillet1"], "properties": {"Length": 100}}],
        ],
    )
    prov = ScriptedProvider([
        (None, [("fillet", {"radius": 500})]),
        (None, [("fillet", {"radius": 5})]),
        ("Applied a 5mm fillet.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    agent.handle_message("Fillet the edge")

    fillet_calls = [c for c in adapter.calls if c[0] == "fillet"]
    assert len(fillet_calls) == 2, "Both fillet attempts executed"

    errs = agent.design_state.get_recent_errors()
    assert any("fillet" in e and "radius too large" in e for e in errs)

    ops = agent.design_state.get_recent_operations()
    fillet_ok = [op for op in ops if op.tool == "fillet" and op.success]
    assert len(fillet_ok) == 1, "Successful recovery recorded"
    assert fillet_ok[0].args.get("radius") == 5
    print("✓ Recovery fillet succeeds and is recorded")
    print("✓ Original failure preserved in recent_errors")
    print("")


def test_requested_vs_achieved_mismatch():
    """Requested-vs-achieved mismatch is preserved in operation history."""
    print("=== Agent Integration: Requested-vs-Achieved Mismatch ===")

    adapter = StubAdapter(
        tool_names=["box", "fillet", "get_edges", "get_state"],
        behavior={
            "fillet": lambda kwargs: (
                ("raise", RuntimeError("radius too large"))
                if kwargs.get("radius", 0) > 10 else ("ok", "fillet applied")),
            "get_edges": ("ok", '[{"edge_id": "e1"}]'),
        },
        state_sequence=[
            [{"id": "box1", "type": "Part::Box", "visible": True,
              "parents": [], "children": [], "properties": {"Length": 100}}],
            [{"id": "box1", "type": "Part::Box", "visible": True,
              "parents": [], "children": [], "properties": {"Length": 100}}],
            [{"id": "fillet1", "type": "Part::Fillet", "visible": True,
              "parents": ["box1"], "children": [], "properties": {}},
             {"id": "box1", "type": "Part::Box", "visible": False,
              "parents": [], "children": ["fillet1"], "properties": {"Length": 100}}],
        ],
    )
    prov = ScriptedProvider([
        (None, [("fillet", {"radius": 500})]),
        (None, [("fillet", {"radius": 5})]),
        ("Applied a 5mm fillet.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    agent.handle_message("Fillet the edge")

    ops = agent.design_state.get_recent_operations()
    fillet_ops = [op for op in ops if op.tool == "fillet" and op.success]
    assert len(fillet_ops) == 1
    op = fillet_ops[0]
    assert op.args.get("radius") == 5, "Achieved args stored in args"
    assert op.requested_args is not None, "Requested args preserved"
    assert op.requested_args.get("radius") == 500, "Original request preserved"

    # Also verify snapshot includes it
    snap = agent.design_state.snapshot()
    recent_ops = snap["recent_operations"]
    fillet_snap = [o for o in recent_ops if o["tool"]
                   == "fillet" and o["success"]]
    assert fillet_snap[0]["args"]["radius"] == 5
    assert fillet_snap[0]["requested_args"]["radius"] == 500
    print("✓ Requested args (500) preserved alongside achieved args (5)")
    print("✓ Snapshot includes requested_args field")
    print("")


def test_immediate_state_sync():
    """Immediate sync after successful tool execution."""
    print("=== Agent Integration: Immediate State Sync ===")

    adapter = StubAdapter(
        tool_names=["box", "get_state"],
        behavior={"box": ("ok", "created")},
        state_sequence=[
            [{"id": "box1", "type": "Part::Box", "visible": True,
              "parents": [], "children": [], "properties": {"Length": 100, "Width": 50, "Height": 20}}],
        ],
    )
    prov = ScriptedProvider([
        (None, [("box", {"Length": 100, "Width": 50, "Height": 20})]),
        ("Created box.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    agent.handle_message("Create a box")

    assert "box1" in agent.design_state.objects
    assert agent.design_state.objects["box1"].object_type == "Part::Box"
    assert agent.design_state.state_available is True
    print("✓ DesignState refreshed from adapter.get_state() after box tool")
    print("✓ state_available=True after successful sync")
    print("")


def test_get_state_failure_marks_unavailable():
    """Simulated get_state failure marks DesignState unavailable but preserves objects."""
    print("=== Agent Integration: get_state Failure Marks Unavailable ===")

    # Pre-populate with a known state
    adapter = StubAdapter(
        tool_names=["box", "get_state"],
        behavior={"box": ("ok", "created")},
        state_sequence=[
            [{"id": "box1", "type": "Part::Box", "visible": True,
              "parents": [], "children": [], "properties": {"Length": 100}}],
            # Simulate a get_state failure by returning invalid JSON on the next call
            "not valid json",
        ],
    )
    prov = ScriptedProvider([
        (None, [("box", {"Length": 100})]),
        ("Created box.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    agent.handle_message("Create a box")

    # After the box tool, immediate sync runs. The second get_state call
    # (for verification) returns invalid JSON -> should mark unavailable.
    assert agent.design_state.state_available is False
    assert agent.design_state.state_stale is True
    assert "box1" in agent.design_state.objects  # Objects preserved!
    print("✓ Failed get_state() marks state_unavailable but preserves objects")
    print("")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    print("=" * 70)
    print("BIP 4.3.2 — STATE INTEGRITY & UNKNOWN-STATE SAFETY PROOF")
    print("=" * 70)
    print()

    test_designstate_unit()
    test_failed_tool_no_fabrication()
    test_recovery_updates_state()
    test_requested_vs_achieved_mismatch()
    test_immediate_state_sync()
    test_get_state_failure_marks_unavailable()

    print("=" * 70)
    print("ALL TESTS PASSED — State Integrity fixes verified.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
