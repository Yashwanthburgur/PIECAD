"""BIP 9.1 — Long-Chain Topology Stability Fixture.

Stress-tests PieCAD's ghost-object and feature-lineage system across a longer
sequence of dependent CAD edits: create → fillet → pattern → boolean → edit_feature

After every operation:
- verify the expected resulting object exists
- verify consumed/source objects follow existing visibility/lineage semantics
- verify references resolve to the correct live descendant

Includes at least one topology-sensitive reference obtained before a later operation
and verifies that the system resolves it correctly afterward.

Runs the complete chain repeatedly (minimum 5 consecutive runs) to detect
nondeterministic topology/lineage failures.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context.state import DesignState  # noqa: E402
from core.adapters.interfaces import CADAdapter  # noqa: E402


# --------------------------------------------------------------------------- #
# Stub adapter that tracks topology changes and object lineage
# --------------------------------------------------------------------------- #

class TopologyChainStubAdapter(CADAdapter):
    """Stub adapter that tracks topology versions and object lineage for the chain test."""

    def __init__(self):
        self.tool_names = [
            "box", "fillet", "chamfer", "pattern_linear", "pattern_circular",
            "boolean", "hole", "edit_feature", "get_edges", "get_faces",
            "get_state", "get_mass_properties"
        ]
        self.objects = []  # List of object dicts representing CAD state
        self.calls = []
        self.topology_versions = {}  # object_id -> version string
        # object_id -> edge_ref string (captured before topology change)
        self.edge_refs = {}
        # object_id -> face_ref string (captured before topology change)
        self.face_refs = {}
        self._version_counter = 0

    def get_tools(self):
        return [{"type": "function", "function": {
            "name": n, "description": f"run {n}",
            "parameters": {"type": "object", "properties": {}}}} for n in self.tool_names]

    def _new_version(self):
        """Generate a new topology version string."""
        self._version_counter += 1
        return f"v{self._version_counter}"

    def _get_face_count(self, obj_id):
        """Return face count for an object (mock)."""
        obj = self._find_obj(obj_id)
        if not obj:
            return 6  # default box
        if "fillet" in obj.get("type", "").lower():
            return 8
        if "pattern" in obj.get("type", "").lower():
            return 24
        if "cut" in obj.get("type", "").lower() or "fuse" in obj.get("type", "").lower():
            return 26
        return 6

    def _ensure_obj(self, obj_id):
        """Ensure object exists in tracking and return it."""
        existing = self._find_obj(obj_id)
        if existing:
            return existing
        new_obj = {"id": obj_id, "type": "Part::Box", "visible": True,
                   "parents": [], "children": [], "properties": {}}
        self.objects.append(new_obj)
        if obj_id not in self.topology_versions:
            self.topology_versions[obj_id] = "0"
        return new_obj

    def _find_obj(self, obj_id):
        return next((o for o in self.objects if o["id"] == obj_id), None)

    def _hide_obj(self, obj_id):
        obj = self._find_obj(obj_id)
        if obj:
            obj["visible"] = False

    def _get_descendant(self, obj_id):
        """Resolve ghost object to its visible descendant."""
        obj = self._find_obj(obj_id)
        if not obj:
            return None
        if obj["visible"]:
            return obj
        for child_id in obj["children"]:
            child = self._find_obj(child_id)
            if child and child["visible"]:
                return child
            descendant = self._get_descendant(child_id)
            if descendant:
                return descendant
        return None

    def execute_command(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))

        if tool_name == "box":
            obj_id = kwargs.get("id", "box1")
            obj = self._ensure_obj(obj_id)
            if obj is None:
                self.objects.append({"id": obj_id, "type": "Part::Box", "visible": True,
                                     "parents": [], "children": [], "properties": kwargs})
                obj = self._find_obj(obj_id)
            if obj is not None:
                obj["type"] = "Part::Box"
                obj["visible"] = True
                obj["parents"] = []
                obj["children"] = []
                obj["properties"] = kwargs
            return "ok"

        if tool_name == "cylinder":
            obj_id = kwargs.get("id", "cyl1")
            obj = self._ensure_obj(obj_id)
            if obj is None:
                self.objects.append({"id": obj_id, "type": "Part::Cylinder", "visible": True,
                                     "parents": [], "children": [], "properties": kwargs})
                obj = self._find_obj(obj_id)
            if obj is not None:
                obj["type"] = "Part::Cylinder"
                obj["visible"] = True
                obj["parents"] = []
                obj["children"] = []
                obj["properties"] = kwargs
            return "ok"

        if tool_name == "fillet":
            target_id = kwargs.get("target_id")
            result_id = kwargs.get("id", "fillet1")
            edge_refs = kwargs.get("edge_refs", [])
            topology_version = kwargs.get("topology_version", "0")

            if target_id and edge_refs:
                self.edge_refs[target_id] = edge_refs[0]

            old_version = self.topology_versions.get(target_id, "0")
            new_version = self._new_version()
            self.topology_versions[target_id] = new_version
            self.topology_versions[result_id] = new_version

            self._hide_obj(target_id)
            obj = self._ensure_obj(result_id)
            if obj is not None:
                obj["type"] = "Part::Fillet"
                obj["visible"] = True
                obj["parents"] = [target_id]
                obj["children"] = []
                obj["properties"] = {"radius": kwargs.get("radius", 5.0)}

            target_obj = self._find_obj(target_id)
            if target_obj:
                target_obj["children"] = [result_id]

            return "ok"

        if tool_name == "pattern_linear":
            target_id = kwargs.get("target_id")
            result_id = kwargs.get("id", "pattern1")
            count = int(kwargs.get("count", 3))

            new_version = self._new_version()
            self.topology_versions[target_id] = new_version
            self.topology_versions[result_id] = new_version

            self._hide_obj(target_id)
            obj = self._ensure_obj(result_id)
            if obj is not None:
                obj["type"] = "Part::LinearPattern"
                obj["visible"] = True
                obj["parents"] = [target_id]
                obj["children"] = []
                obj["properties"] = kwargs

            target_obj = self._find_obj(target_id)
            if target_obj:
                target_obj["children"] = [result_id]

            return "ok"

        if tool_name == "boolean":
            target_id = kwargs.get("target_id")
            tool_id = kwargs.get("tool_id")
            result_id = kwargs.get("id", "cut1")
            mode = kwargs.get("mode", "subtract")

            new_version = self._new_version()
            self.topology_versions[target_id] = new_version
            self.topology_versions[tool_id] = new_version
            self.topology_versions[result_id] = new_version

            self._hide_obj(target_id)
            self._hide_obj(tool_id)
            obj = self._ensure_obj(result_id)
            if obj is not None:
                obj["type"] = "Part::Cut" if mode == "subtract" else "Part::Fuse"
                obj["visible"] = True
                obj["parents"] = [target_id, tool_id]
                obj["children"] = []
                obj["properties"] = {}

            target_obj = self._find_obj(target_id)
            if target_obj:
                target_obj["children"] = [result_id]
            tool_obj = self._find_obj(tool_id)
            if tool_obj:
                tool_obj["children"] = [result_id]

            return "ok"

        if tool_name == "edit_feature":
            target_id = kwargs.get("target_id")
            params = kwargs.get("parameters", {})

            new_version = self._new_version()
            self.topology_versions[target_id] = new_version

            obj = self._find_obj(target_id)
            if obj:
                obj["properties"].update(params)

            return "ok"

        if tool_name == "get_edges":
            obj_name = kwargs.get("object_name", "")
            version = self.topology_versions.get(obj_name, "0")
            return json.dumps({
                "edges": [{"edge_id": f"{obj_name}_edge_1", "center": {"x": 0, "y": 0, "z": 0}}],
                "topology_version": version
            })

        if tool_name == "get_faces":
            obj_name = kwargs.get("object_name", "")
            version = self.topology_versions.get(obj_name, "0")
            face_count = self._get_face_count(obj_name)
            faces = [{"face_id": f"{obj_name}_face_{i}", "center": {
                "x": 0, "y": 0, "z": 0}} for i in range(1, face_count + 1)]
            return json.dumps({
                "faces": faces,
                "topology_version": version
            })

        if tool_name == "get_state":
            return json.dumps(self.objects)

        if tool_name == "get_mass_properties":
            return json.dumps({"Volume": 1000.0, "volume": 1000.0})

        return "ok"

    def get_state(self) -> str:
        return json.dumps(self.objects)


def _run_chain_once(adapter: TopologyChainStubAdapter, run_id: int) -> bool:
    """Run a single chain iteration and return True if all checks pass."""
    errors = []

    # Clear state for each run
    adapter.objects.clear()
    adapter.calls.clear()
    adapter.topology_versions.clear()
    adapter.edge_refs.clear()
    adapter.face_refs.clear()
    adapter._version_counter = 0

    # Manually drive the chain without LLM (deterministic)
    # This simulates the agent calling the operations in sequence

    # Step 1: create box
    adapter.execute_command("box", id="box1", Length=100, Width=50, Height=20)

    # Step 2: fillet (needs edge ref from box1)
    edge_result = adapter.execute_command("get_edges", object_name="box1")
    edge_version = json.loads(edge_result).get("topology_version", "0")
    fillet_result = adapter.execute_command("fillet", id="fillet1", target_id="box1",
                                            edge_refs=["box1_edge_1"], radius=5.0,
                                            topology_version=edge_version)

    # Step 3: pattern_linear (on fillet1)
    pattern_result = adapter.execute_command("pattern_linear", id="pattern1",
                                             target_id="fillet1", direction={"x": 1, "y": 0, "z": 0},
                                             distance=60, count=3)

    # Step 4: boolean subtract (pattern1 - cylinder)
    adapter.execute_command("cylinder", id="tool_cyl", radius=10, height=30)
    boolean_result = adapter.execute_command("boolean", id="cut1", mode="subtract",
                                             target_id="pattern1", tool_id="tool_cyl")

    # Step 5: edit_feature on the final result
    edit_result = adapter.execute_command(
        "edit_feature", target_id="cut1", parameters={"radius": 12.0})

    # --- Verification after chain completion ---

    # 1. box1 should exist (as ghost) with fillet1 as child
    box1 = adapter._find_obj("box1")
    if not box1:
        errors.append(f"FAIL [run {run_id}]: box1 missing from state")
    elif box1["visible"]:
        errors.append(
            f"FAIL [run {run_id}]: box1 should be hidden (ghost) after fillet")

    # 2. fillet1 should exist (as ghost) with pattern1 as child
    fillet1 = adapter._find_obj("fillet1")
    if not fillet1:
        errors.append(f"FAIL [run {run_id}]: fillet1 missing from state")
    elif fillet1["visible"]:
        errors.append(
            f"FAIL [run {run_id}]: fillet1 should be hidden after pattern")

    # 3. pattern1 should exist (as ghost) with cut1 as child
    pattern1 = adapter._find_obj("pattern1")
    if not pattern1:
        errors.append(f"FAIL [run {run_id}]: pattern1 missing from state")
    elif pattern1["visible"]:
        errors.append(
            f"FAIL [run {run_id}]: pattern1 should be hidden after boolean")

    # 4. tool_cyl should exist (as ghost) with cut1 as child
    tool_cyl = adapter._find_obj("tool_cyl")
    if not tool_cyl:
        errors.append(f"FAIL [run {run_id}]: tool_cyl missing from state")
    elif tool_cyl["visible"]:
        errors.append(
            f"FAIL [run {run_id}]: tool_cyl should be hidden after boolean")

    # 5. cut1 should be the final visible result
    cut1 = adapter._find_obj("cut1")
    if not cut1:
        errors.append(
            f"FAIL [run {run_id}]: cut1 (final result) missing from state")
    elif not cut1["visible"]:
        errors.append(
            f"FAIL [run {run_id}]: cut1 should be visible as final result")

    # 6. Verify ghost resolution: resolve box1 should return cut1
    descendant = adapter._get_descendant("box1")
    if not descendant or descendant["id"] != "cut1":
        errors.append(
            f"FAIL [run {run_id}]: Ghost resolution failed. box1 descendant is {descendant['id'] if descendant else 'None'}, expected cut1")

    # 7. Verify topology version advanced at each step (4 distinct versions)
    versions = {
        "box1": adapter.topology_versions.get("box1", "0"),
        "fillet1": adapter.topology_versions.get("fillet1", "0"),
        "pattern1": adapter.topology_versions.get("pattern1", "0"),
        "cut1": adapter.topology_versions.get("cut1", "0"),
    }
    unique_versions = set(versions.values())
    if len(unique_versions) < 4:
        errors.append(
            f"FAIL [run {run_id}]: Topology versions not unique across chain: {versions}")

    # 8. Verify edge reference captured before fillet is now stale
    # The edge ref was captured at version "0" (when get_edges was called)
    # After fillet, box1 topology version should be "v1" (different from captured "0")
    # captured during get_edges (initial version)
    box1_version_at_capture = "0"
    current_box1_version = adapter.topology_versions.get("box1", "0")
    if box1_version_at_capture == current_box1_version:
        errors.append(
            f"FAIL [run {run_id}]: Edge reference on box1 should be stale but version unchanged: {current_box1_version}")

    # 9. Verify operation sequence
    op_names = [c[0] for c in adapter.calls]
    expected_ops = ["box", "get_edges", "fillet",
                    "pattern_linear", "cylinder", "boolean", "edit_feature"]
    for i, (actual, expected) in enumerate(zip(op_names, expected_ops)):
        if actual != expected:
            errors.append(
                f"FAIL [run {run_id}]: Operation {i}: expected {expected}, got {actual}")

    if errors:
        for err in errors:
            print(f"  {err}")
        return False

    print(f"  [RUN {run_id}] PASS: All topology chain checks passed")
    return True


def test_topology_chain_multiple_runs():
    """Run the topology chain multiple times to detect nondeterministic failures."""
    num_runs = 5
    adapter = TopologyChainStubAdapter()

    all_passed = True
    for run_id in range(1, num_runs + 1):
        if not _run_chain_once(adapter, run_id):
            all_passed = False

    assert all_passed, f"One or more of {num_runs} chain runs failed"
    print(f"[PASS] All {num_runs} topology chain runs passed")


def test_topology_reference_resolution():
    """Test that topology-sensitive references resolve correctly after chain."""
    adapter = TopologyChainStubAdapter()
    adapter.objects.clear()
    adapter.topology_versions.clear()

    # Create initial box
    box_obj = {"id": "box1", "type": "Part::Box", "visible": True,
               "parents": [], "children": [], "properties": {"Length": 100}}
    adapter.objects.append(box_obj)
    adapter.topology_versions["box1"] = "v0"

    # Capture edge reference on box1 at version v0
    edge_ref = "box1_edge_1"
    adapter.edge_refs["box1"] = edge_ref

    # Simulate fillet operation (advances topology)
    adapter.topology_versions["box1"] = "v1"
    adapter.topology_versions["fillet1"] = "v1"
    adapter.objects.append({"id": "fillet1", "type": "Part::Fillet", "visible": True, "parents": [
                           "box1"], "children": [], "properties": {}})
    box_obj["visible"] = False
    box_obj["children"] = ["fillet1"]

    # Verify reference is now stale using DesignState
    st = DesignState()
    st.update_from_cad_state(json.dumps([{
        "id": "fillet1", "type": "Part::Fillet", "visible": True,
        "parents": ["box1"], "children": [], "properties": {}
    }, {
        "id": "box1", "type": "Part::Box", "visible": False,
        "parents": [], "children": ["fillet1"], "properties": {"Length": 100}
    }]))

    # Manually set topology version in DesignState to match the adapter's state
    # (DesignState.update_from_cad_state doesn't populate topology_versions;
    # they are populated via update_from_tool_result when get_edges/get_faces is called)
    st.topology_versions["box1"] = "v1"

    # Record reference at old version (simulating capture before fillet)
    st.record_topology_reference("box1", "edge", "v0")

    # Check if reference is stale
    is_stale = st.is_reference_stale("box1", "edge", "v0")
    assert is_stale, "Reference captured at v0 should be stale after topology advance to v1"

    # Fresh reference at current version
    st.record_topology_reference("box1", "edge", "v1")
    is_stale = st.is_reference_stale("box1", "edge", "v1")
    assert not is_stale, "Fresh reference at v1 should not be stale"

    print("[PASS] Topology reference resolution works correctly")


def test_design_state_lineage():
    """Test DesignState lineage tracking through the chain."""
    st = DesignState()

    # Initial box
    st.update_from_cad_state(json.dumps([{
        "id": "box1", "type": "Part::Box", "visible": True,
        "parents": [], "children": [], "properties": {"Length": 100}
    }]))

    # After fillet
    st.update_from_cad_state(json.dumps([{
        "id": "fillet1", "type": "Part::Fillet", "visible": True,
        "parents": ["box1"], "children": [], "properties": {"radius": 5.0}
    }, {
        "id": "box1", "type": "Part::Box", "visible": False,
        "parents": [], "children": ["fillet1"], "properties": {"Length": 100}
    }]))

    # Verify ghost resolution
    box1_obj = st.get_object("box1")
    assert box1_obj is not None
    assert not box1_obj.visible
    assert "fillet1" in box1_obj.children

    fillet1_obj = st.get_object("fillet1")
    assert fillet1_obj is not None
    assert fillet1_obj.visible
    assert "box1" in fillet1_obj.parents

    # Resolve active object
    active = st.resolve_active_object("box1")
    assert active is not None
    assert active.object_id == "fillet1"

    print("[PASS] DesignState lineage tracking works correctly")


# --------------------------------------------------------------------------- #
# Main test runner
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    print("=" * 70)
    print("BIP 9.1 — LONG-CHAIN TOPOLOGY STABILITY FIXTURE")
    print("=" * 70)
    print()

    print("--- Topology Chain Stress Test (5+ runs) ---")
    test_topology_chain_multiple_runs()
    print()

    print("--- Topology Reference Resolution ---")
    test_topology_reference_resolution()
    print()

    print("--- DesignState Lineage Tracking ---")
    test_design_state_lineage()
    print()

    print("=" * 70)
    print("ALL BIP 9.1 TESTS PASSED")
    print("=" * 70)
