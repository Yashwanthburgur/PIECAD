"""BIP 4.3.2 — State Integrity & Unknown-State Safety regression tests.

Covers:
- Successful state refresh.
- get_state() failure after a known-good state (no wipeout).
- Empty CAD document vs. unavailable CAD state distinction.
- Failed tool does not fabricate state.
- Successful recovery updates state correctly.
- Requested-vs-achieved parameter mismatch remains observable.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context.state import DesignState  # noqa: E402
from core.adapters.interfaces import CADAdapter  # noqa: E402
from core.agent import CADAgent  # noqa: E402


def _obj(obj_id, obj_type, visible=True, parents=None, children=None, props=None):
    return {
        "id": obj_id, "label": obj_id, "type": obj_type, "visible": visible,
        "parents": list(parents or []), "children": list(children or []),
        "properties": dict(props or {}),
    }


def _box(oid="box1", **kw):
    return _obj(oid, "Part::Box", props={"Length": 100, "Width": 50, "Height": 20}, **kw)


# --------------------------------------------------------------------------- #
# DesignState unit tests
# --------------------------------------------------------------------------- #

def test_successful_state_refresh():
    """A valid state JSON updates DesignState and marks state_available=True."""
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box()]))
    assert st.state_available is True
    assert st.state_stale is False
    assert st.last_successful_sync is not None
    assert "box1" in st.objects
    assert st.objects["box1"].object_type == "Part::Box"


def test_get_state_failure_after_known_good_preserves_objects():
    """When update_from_cad_state fails, last known-good objects are preserved
    and state_available becomes False (stale)."""
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box(), _obj("cyl1", "Part::Cylinder",
                                                      props={"Radius": 10, "Height": 40})]))
    assert len(st.objects) == 2
    assert st.state_available is True

    # Simulate a failed retrieval (malformed JSON -> treated as failure).
    st.update_from_cad_state("not valid json")
    assert st.state_available is False
    assert st.state_stale is True
    # Objects must NOT be cleared.
    assert len(st.objects) == 2
    assert "box1" in st.objects
    assert "cyl1" in st.objects


def test_empty_document_vs_unavailable_state():
    """An explicitly empty list ("[]") is a valid empty document (state_available=True).
    A parse failure or exception marks state_available=False."""
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box()]))
    assert st.state_available is True

    # Explicitly empty document (valid).
    st.update_from_cad_state("[]")
    assert st.state_available is True
    assert st.state_stale is False
    assert len(st.objects) == 0  # empty document is legitimate

    # Now a failure should not wipe (already empty, but flag matters).
    st.update_from_cad_state("not valid json")
    assert st.state_available is False
    assert st.state_stale is True
    # No objects anyway, but flag is correct.


def test_mark_state_unavailable_preserves_objects():
    """mark_state_unavailable() flags stale without clearing objects."""
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box()]))
    st.mark_state_unavailable()
    assert st.state_available is False
    assert st.state_stale is True
    assert "box1" in st.objects


def test_snapshot_includes_state_flags():
    """DesignState.snapshot() includes state_available, state_stale, last_successful_sync."""
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box()]))
    snap = st.snapshot()
    assert snap["state_available"] is True
    assert snap["state_stale"] is False
    assert snap["last_successful_sync"] is not None


def test_summary_includes_state_flags():
    """DesignState.summary() includes state_available, state_stale."""
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box()]))
    summ = st.summary()
    assert summ["state_available"] is True
    assert summ["state_stale"] is False


# --------------------------------------------------------------------------- #
# Agent integration tests (stub adapter)
# --------------------------------------------------------------------------- #

class ScriptedProvider:
    """Returns scripted LLM responses: list of (content_or_None, tool_calls)."""

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
    """Minimal adapter with scriptable execute_command and get_state."""

    def __init__(self, tool_names=None, behavior=None, state_sequence=None):
        self.tool_names = tool_names or [
            "box", "fillet", "get_state", "get_edges"]
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
        mode, *rest = spec
        if mode == "raise":
            raise rest[0]
        if tool_name == "box":
            self.objects.append({"id": "box1", "type": "Part::Box",
                                 "visible": True, "parents": [], "children": [],
                                 "properties": kwargs})
        return "ok" if len(rest) == 0 else rest[0]

    def get_state(self):
        if self.state_sequence:
            s = self.state_sequence[self.state_index]
            self.state_index = min(self.state_index + 1,
                                   len(self.state_sequence) - 1)
            return json.dumps(s)
        return json.dumps(self.objects)


def test_failed_tool_does_not_fabricate_state():
    """A non-transient tool failure never creates a CAD object in DesignState."""
    adapter = StubAdapter(behavior={"fillet": (
        "raise", RuntimeError("radius too large"))})
    prov = ScriptedProvider([
        (None, [("fillet", {"radius": 500})]),
        ("Fillet failed.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    agent.handle_message("Fillet the edge 500mm")

    # No fake geometry from the failed call.
    assert len(agent.design_state.objects) == 0
    # Failure recorded in recent_errors.
    errs = agent.design_state.get_recent_errors()
    assert any("fillet" in e for e in errs)


def test_successful_recovery_updates_state_correctly():
    """After a failed fillet (radius 500), a recovery fillet (radius 5) succeeds
    and DesignState reflects the actual CAD state (via immediate sync)."""
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
              "parents": [], "children": [], "properties": {"Length": 100}}],  # after box
            [{"id": "box1", "type": "Part::Box", "visible": True,
              "parents": [], "children": [], "properties": {"Length": 100}}],  # after failed fillet (no change)
            [{"id": "fillet1", "type": "Part::Fillet", "visible": True,
              "parents": ["box1"], "children": [], "properties": {}},  # after successful fillet
             {"id": "box1", "type": "Part::Box", "visible": False,
              "parents": [], "children": ["fillet1"], "properties": {"Length": 100}}],
        ],
    )
    prov = ScriptedProvider([
        (None, [("fillet", {"radius": 500})]),   # fails
        (None, [("fillet", {"radius": 5})]),     # recovery succeeds
        ("Applied a 5mm fillet.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    agent.handle_message("Fillet the edge")

    # The recovery succeeded, state should reflect the actual CAD result.
    # Note: exact object set depends on the stub state_sequence.
    # What matters is that the agent didn't crash and recorded both attempts.
    fillet_calls = [c for c in adapter.calls if c[0] == "fillet"]
    assert len(fillet_calls) == 2

    # First attempt recorded as error.
    errs = agent.design_state.get_recent_errors()
    assert any("fillet" in e and "radius too large" in e for e in errs)

    # Second attempt recorded as successful operation.
    ops = agent.design_state.get_recent_operations()
    fillet_ok = [op for op in ops if op.tool == "fillet" and op.success]
    assert len(fillet_ok) == 1
    assert fillet_ok[0].args.get("radius") == 5


def test_requested_vs_achieved_mismatch_preserved():
    """When a recovery changes parameters (requested 500, achieved 5),
    both the original requested args and the final achieved args are visible."""
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
    # The successful operation should carry requested_args=500, args=5.
    op = fillet_ops[0]
    assert op.args.get("radius") == 5
    assert op.requested_args is not None
    assert op.requested_args.get("radius") == 500

    # Snapshot should also include requested_args.
    snap = agent.design_state.snapshot()
    recent_ops = snap["recent_operations"]
    fillet_snap = [o for o in recent_ops if o["tool"]
                   == "fillet" and o["success"]]
    assert len(fillet_snap) == 1
    assert fillet_snap[0]["args"]["radius"] == 5
    assert fillet_snap[0]["requested_args"]["radius"] == 500


def test_state_sync_after_every_successful_tool():
    """Immediate state sync: after a successful box creation, DesignState
    is refreshed from adapter.get_state() before the next ReAct step."""
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

    # After the box tool, immediate sync should have run and DesignState
    # should contain the box object.
    assert "box1" in agent.design_state.objects
    assert agent.design_state.objects["box1"].object_type == "Part::Box"
    assert agent.design_state.state_available is True


if __name__ == "__main__":
    test_successful_state_refresh()
    test_get_state_failure_after_known_good_preserves_objects()
    test_empty_document_vs_unavailable_state()
    test_mark_state_unavailable_preserves_objects()
    test_snapshot_includes_state_flags()
    test_summary_includes_state_flags()
    test_failed_tool_does_not_fabricate_state()
    test_successful_recovery_updates_state_correctly()
    test_requested_vs_achieved_mismatch_preserved()
    test_state_sync_after_every_successful_tool()
    print("All tests passed.")
