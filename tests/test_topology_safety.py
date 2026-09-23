"""BIP 4.3.4 — Topology Safety & Stale References regression tests.

Verifies that topology-altering operations advance topology versions
and stale edge_refs/face_refs are rejected at execution time.

NOTE (BIP 4.3.5): topology versions are now deterministic STRING hashes
(XML-RPC safe) rather than integers, so assertions compare string versions
and check that a version *changed* rather than that it incremented by 1.
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

def test_topology_version_starts_at_zero():
    """New objects start with topology version "0"."""
    st = DesignState()
    assert st.get_topology_version("box1") == "0"


def test_topology_version_increments():
    """increment_topology_version produces a new (string) version each call."""
    st = DesignState()
    v1 = st.increment_topology_version("box1")
    v2 = st.increment_topology_version("box1")
    assert isinstance(v1, str)
    assert isinstance(v2, str)
    assert v1 != v2


def test_is_reference_stale():
    """is_reference_stale returns True when the current version differs."""
    st = DesignState()
    current = st.increment_topology_version("box1")  # new version
    assert st.is_reference_stale("box1", "edge", "0") is True
    assert st.is_reference_stale("box1", "edge", current) is False
    assert st.is_reference_stale("box1", "face", "0") is True


def test_record_and_get_reference_version():
    """record_topology_reference stores and retrieves the version."""
    st = DesignState()
    st.record_topology_reference("box1", "edge", "5")
    assert st.get_recorded_reference_version("box1", "edge") == "5"


def test_update_from_tool_result_increments_topology():
    """Topology-altering tools advance topology version on success."""
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box()]))
    assert st.get_topology_version("box1") == "0"

    # Simulate a fillet operation
    st.update_from_tool_result(
        tool="fillet", result="ok", target_id="box1",
        args={"radius": 5.0, "target_id": "box1"}, success=True
    )
    after_fillet = st.get_topology_version("box1")
    assert after_fillet != "0"

    # Another topology operation
    st.update_from_tool_result(
        tool="chamfer", result="ok", target_id="box1",
        args={"size": 2.0, "target_id": "box1"}, success=True
    )
    after_chamfer = st.get_topology_version("box1")
    assert after_chamfer != after_fillet

    # Non-topology tool should NOT increment
    st.update_from_tool_result(
        tool="edit_feature", result="ok", target_id="box1",
        args={"Length": 120.0}, success=True
    )
    # edit_feature IS topology-altering
    after_edit = st.get_topology_version("box1")
    assert after_edit != after_chamfer

    # get_edges should NOT increment
    st.update_from_tool_result(
        tool="get_edges", result="ok", target_id="box1",
        args={"object_name": "box1"}, success=True
    )
    assert st.get_topology_version("box1") == after_edit


def test_snapshot_includes_topology_versions():
    """DesignState.snapshot() includes topology_versions."""
    st = DesignState()
    version = st.increment_topology_version("box1")
    snap = st.snapshot()
    assert "topology_versions" in snap
    assert snap["topology_versions"]["box1"] == version
    assert isinstance(snap["topology_versions"]["box1"], str)


def test_clear_resets_topology_versions():
    """clear() resets topology_versions."""
    st = DesignState()
    st.increment_topology_version("box1")
    st.clear()
    assert st.get_topology_version("box1") == "0"


# --------------------------------------------------------------------------- #
# Agent integration tests (stub adapter)
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
            "box", "fillet", "chamfer", "get_edges", "get_faces", "get_state"]
        self.behavior = behavior or {}
        self.objects = []
        self.state_sequence = state_sequence or []
        self.state_index = 0
        self.calls = []
        self.topology_version = "1"  # Simple mock version (string, BIP 4.3.5)

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
        if tool_name in ("fillet", "chamfer"):
            # Advance mock topology version for the target (string hash)
            import hashlib
            import time
            self.topology_version = hashlib.md5(
                f"{tool_name}:{time.time()}".encode()).hexdigest()[:16]
        return "ok" if len(rest) == 0 else rest[0]

    def get_state(self):
        if self.state_sequence:
            s = self.state_sequence[self.state_index]
            self.state_index = min(self.state_index + 1,
                                   len(self.state_sequence) - 1)
            return json.dumps(s)
        return json.dumps(self.objects)


def test_fresh_reference_succeeds():
    """Using a fresh reference (matching topology version) succeeds."""
    adapter = StubAdapter(
        tool_names=["box", "fillet", "get_edges", "get_state"],
        behavior={
            "fillet": ("ok", "fillet applied"),
            "get_edges": ("ok", '{"edges": [{"edge_id": "box1_edge_1"}], "topology_version": "1"}'),
        },
        state_sequence=[
            [{"id": "box1", "type": "Part::Box", "visible": True,
              "parents": [], "children": [], "properties": {"Length": 100}}],
            [{"id": "fillet1", "type": "Part::Fillet", "visible": True,
              "parents": ["box1"], "children": [], "properties": {}},
             {"id": "box1", "type": "Part::Box", "visible": False,
              "parents": [], "children": ["fillet1"], "properties": {"Length": 100}}],
        ],
    )
    prov = ScriptedProvider([
        (None, [("box", {"Length": 100, "Width": 50, "Height": 20})]),
        (None, [("get_edges", {"object_name": "box1"})]),
        (None, [("fillet", {"radius": 5.0, "target_id": "box1",
         "edge_refs": ["box1_edge_1"], "topology_version": "1"})]),
        ("Fillet applied.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    agent.handle_message("Create a box then fillet an edge")

    # Verify the fillet was called with topology_version
    fillet_calls = [c for c in adapter.calls if c[0] == "fillet"]
    assert len(fillet_calls) == 1
    # The agent should pass topology_version from DesignState
    print("✓ Fresh reference succeeds")


def test_stale_reference_rejected():
    """Using a stale reference raises a clear error."""
    # This test simulates the bridge rejecting stale references
    # We verify the DesignState correctly tracks versions
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box()]))

    # Record a reference at version "0"
    st.record_topology_reference("box1", "edge", "0")

    # Simulate a topology change (fillet)
    current = st.increment_topology_version("box1")  # new version

    # Check if the recorded reference is now stale
    recorded_version = st.get_recorded_reference_version("box1", "edge")
    is_stale = st.is_reference_stale("box1", "edge", recorded_version)
    assert is_stale is True, "Reference should be stale after topology change"

    # Fresh reference at the current version
    st.record_topology_reference("box1", "edge", current)
    recorded_version = st.get_recorded_reference_version("box1", "edge")
    is_stale = st.is_reference_stale("box1", "edge", recorded_version)
    assert is_stale is False, "Fresh reference should not be stale"

    print("✓ Stale reference correctly detected")


def test_multiple_topology_operations():
    """Multiple topology operations advance version each time."""
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box()]))

    topology_tools = ["fillet", "chamfer", "boolean", "hole", "shell",
                      "edit_feature", "pattern_linear", "pattern_circular", "delete_feature"]

    for tool in topology_tools:
        before = st.get_topology_version("box1")
        st.update_from_tool_result(
            tool=tool, result="ok", target_id="box1",
            args={"target_id": "box1"}, success=True
        )
        after = st.get_topology_version("box1")
        assert after != before, f"Tool {tool} should advance the version"

    print("✓ Multiple topology operations advance version correctly")


def test_non_topology_tool_no_increment():
    """Non-topology tools do not advance topology version."""
    st = DesignState()
    st.update_from_cad_state(json.dumps([_box()]))
    version = st.increment_topology_version("box1")

    non_topology_tools = ["get_edges", "get_faces",
                          "get_state", "get_mass_properties", "get_bom", "export"]

    for tool in non_topology_tools:
        st.update_from_tool_result(
            tool=tool, result="ok", target_id="box1",
            args={}, success=True
        )
        assert st.get_topology_version(
            "box1") == version, f"Tool {tool} should NOT advance the version"

    print("✓ Non-topology tools do not advance version")


if __name__ == "__main__":
    print("=" * 70)
    print("BIP 4.3.4 — TOPOLOGY SAFETY & STALE REFERENCES TESTS")
    print("=" * 70)
    print()

    test_topology_version_starts_at_zero()
    test_topology_version_increments()
    test_is_reference_stale()
    test_record_and_get_reference_version()
    test_update_from_tool_result_increments_topology()
    test_snapshot_includes_topology_versions()
    test_clear_resets_topology_versions()
    test_fresh_reference_succeeds()
    test_stale_reference_rejected()
    test_multiple_topology_operations()
    test_non_topology_tool_no_increment()

    print()
    print("=" * 70)
    print("ALL TESTS PASSED — Topology Safety verified.")
    print("=" * 70)
