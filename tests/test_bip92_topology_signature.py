"""BIP 9.2 — Topology Signature Tracking Tests.

Focused deterministic tests for geometric signature capture and validation.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context.state import DesignState  # noqa: E402


# --------------------------------------------------------------------------- #
# Mock adapter that simulates get_faces/get_edges with signatures
# --------------------------------------------------------------------------- #

class MockTopologyAdapter:
    """Mock adapter that returns topology data with geometric signatures."""

    def __init__(self):
        self.calls = []
        self.state = []

    def get_tools(self):
        return [
            {"type": "function", "function": {"name": n, "description": f"run {n}",
                                              "parameters": {"type": "object", "properties": {}}}}
            for n in ["box", "fillet", "get_faces", "get_edges", "get_state"]
        ]

    def execute_command(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))

        if tool_name == "box":
            self.state = [{
                "id": "box1", "type": "Part::Box", "visible": True,
                "parents": [], "children": [], "properties": kwargs
            }]
            return "ok"

        if tool_name == "fillet":
            # Simulate fillet: hide box1, create fillet1
            for obj in self.state:
                if obj["id"] == "box1":
                    obj["visible"] = False
                    obj["children"] = ["fillet1"]
            self.state.append({
                "id": "fillet1", "type": "Part::Fillet", "visible": True,
                "parents": ["box1"], "children": [], "properties": {"radius": kwargs.get("radius", 5.0)}
            })
            return "ok"

        if tool_name == "get_faces":
            obj_name = kwargs.get("object_name", "box1")
            # Return faces with signatures (center, area, normal)
            faces = [
                {
                    "face_id": f"{obj_name}_face_1",
                    "face_index": 1,
                    "center": {"x": 50.0, "y": 0.0, "z": 25.0},
                    "area": 2500.0,
                    "normal": {"x": 1.0, "y": 0.0, "z": 0.0}
                },
                {
                    "face_id": f"{obj_name}_face_2",
                    "face_index": 2,
                    "center": {"x": -50.0, "y": 0.0, "z": 25.0},
                    "area": 2500.0,
                    "normal": {"x": -1.0, "y": 0.0, "z": 0.0}
                },
                {
                    "face_id": f"{obj_name}_face_3",
                    "face_index": 3,
                    "center": {"x": 0.0, "y": 50.0, "z": 25.0},
                    "area": 5000.0,
                    "normal": {"x": 0.0, "y": 1.0, "z": 0.0}
                }
            ]
            return json.dumps({
                "faces": faces,
                "topology_version": "v1"
            })

        if tool_name == "get_edges":
            obj_name = kwargs.get("object_name", "box1")
            edges = [
                {
                    "edge_id": f"{obj_name}_edge_1",
                    "edge_index": 1,
                    "center": {"x": 50.0, "y": 50.0, "z": 25.0},
                    "length": 50.0,
                    "tangent": {"x": 0.0, "y": -1.0, "z": 0.0}
                },
                {
                    "edge_id": f"{obj_name}_edge_2",
                    "edge_index": 2,
                    "center": {"x": 50.0, "y": -50.0, "z": 25.0},
                    "length": 50.0,
                    "tangent": {"x": 0.0, "y": 1.0, "z": 0.0}
                }
            ]
            return json.dumps({
                "edges": edges,
                "topology_version": "v1"
            })

        if tool_name == "get_state":
            return json.dumps(self.state)

        return "ok"

    def get_state(self):
        return json.dumps(self.state)


def test_face_signature_captured():
    """Test a) face signature is captured correctly."""
    print("Testing a) face signature captured...")

    adapter = MockTopologyAdapter()
    st = DesignState()

    # Create box
    adapter.execute_command("box", id="box1", Length=100, Width=100, Height=50)

    # Get faces - this should capture signatures
    faces_result = adapter.execute_command("get_faces", object_name="box1")
    st.update_from_tool_result("get_faces", faces_result, target_id="box1")

    # Check stored signatures
    face1_sig = st.get_stored_signature("box1", "face", "box1_face_1")
    face2_sig = st.get_stored_signature("box1", "face", "box1_face_2")
    face3_sig = st.get_stored_signature("box1", "face", "box1_face_3")

    assert face1_sig is not None, "face1 signature should be stored"
    assert face2_sig is not None, "face2 signature should be stored"
    assert face3_sig is not None, "face3 signature should be stored"

    # Verify signature content
    assert face1_sig["center"] == {"x": 50.0, "y": 0.0, "z": 25.0}
    assert face1_sig["area"] == 2500.0
    assert face1_sig["normal"] == {"x": 1.0, "y": 0.0, "z": 0.0}

    assert face2_sig["center"] == {"x": -50.0, "y": 0.0, "z": 25.0}
    assert face2_sig["area"] == 2500.0
    assert face2_sig["normal"] == {"x": -1.0, "y": 0.0, "z": 0.0}

    assert face3_sig["center"] == {"x": 0.0, "y": 50.0, "z": 25.0}
    assert face3_sig["area"] == 5000.0
    assert face3_sig["normal"] == {"x": 0.0, "y": 1.0, "z": 0.0}

    print("  [PASS] Face signatures captured correctly")


def test_edge_signature_captured():
    """Test b) edge signature is captured correctly."""
    print("Testing b) edge signature captured...")

    adapter = MockTopologyAdapter()
    st = DesignState()

    # Create box
    adapter.execute_command("box", id="box1", Length=100, Width=100, Height=50)

    # Get edges - this should capture signatures
    edges_result = adapter.execute_command("get_edges", object_name="box1")
    st.update_from_tool_result("get_edges", edges_result, target_id="box1")

    # Check stored signatures
    edge1_sig = st.get_stored_signature("box1", "edge", "box1_edge_1")
    edge2_sig = st.get_stored_signature("box1", "edge", "box1_edge_2")

    assert edge1_sig is not None, "edge1 signature should be stored"
    assert edge2_sig is not None, "edge2 signature should be stored"

    # Verify signature content
    assert edge1_sig["center"] == {"x": 50.0, "y": 50.0, "z": 25.0}
    assert edge1_sig["length"] == 50.0
    assert edge1_sig["tangent"] == {"x": 0.0, "y": -1.0, "z": 0.0}

    assert edge2_sig["center"] == {"x": 50.0, "y": -50.0, "z": 25.0}
    assert edge2_sig["length"] == 50.0
    assert edge2_sig["tangent"] == {"x": 0.0, "y": 1.0, "z": 0.0}

    print("  [PASS] Edge signatures captured correctly")


def test_unchanged_reference_passes_validation():
    """Test c) unchanged reference passes signature validation."""
    print("Testing c) unchanged reference passes validation...")

    adapter = MockTopologyAdapter()
    st = DesignState()

    # Create box
    adapter.execute_command("box", id="box1", Length=100, Width=100, Height=50)

    # Get faces to capture signatures
    faces_result = adapter.execute_command("get_faces", object_name="box1")
    st.update_from_tool_result("get_faces", faces_result, target_id="box1")

    # Now validate with the SAME signature (simulating unchanged geometry)
    current_sig = {
        "center": {"x": 50.0, "y": 0.0, "z": 25.0},
        "area": 2500.0,
        "normal": {"x": 1.0, "y": 0.0, "z": 0.0}
    }

    result = st.validate_topology_signature(
        "box1", "face", "box1_face_1", current_sig)
    assert result["match"] is True, f"Expected match, got: {result}"
    assert len(result["mismatch_details"]) == 0

    # Test edge validation too
    edges_result = adapter.execute_command("get_edges", object_name="box1")
    st.update_from_tool_result("get_edges", edges_result, target_id="box1")

    current_edge_sig = {
        "center": {"x": 50.0, "y": 50.0, "z": 25.0},
        "length": 50.0,
        "tangent": {"x": 0.0, "y": -1.0, "z": 0.0}
    }

    result = st.validate_topology_signature(
        "box1", "edge", "box1_edge_1", current_edge_sig)
    assert result["match"] is True, f"Expected match, got: {result}"
    assert len(result["mismatch_details"]) == 0

    print("  [PASS] Unchanged references pass validation")


def test_changed_geometry_produces_mismatch():
    """Test d) changed geometry produces a signature mismatch."""
    print("Testing d) changed geometry produces mismatch...")

    adapter = MockTopologyAdapter()
    st = DesignState()

    # Create box
    adapter.execute_command("box", id="box1", Length=100, Width=100, Height=50)

    # Get faces to capture signatures
    faces_result = adapter.execute_command("get_faces", object_name="box1")
    st.update_from_tool_result("get_faces", faces_result, target_id="box1")

    # Now validate with a DIFFERENT signature (simulating geometry change)
    current_sig = {
        "center": {"x": 50.0, "y": 0.0, "z": 25.0},  # Same center
        "area": 3000.0,  # Different area!
        "normal": {"x": 1.0, "y": 0.0, "z": 0.0}
    }

    result = st.validate_topology_signature(
        "box1", "face", "box1_face_1", current_sig)
    assert result["match"] is False, f"Expected mismatch, got: {result}"
    assert "area: stored=2500.0, current=3000.0" in result["mismatch_details"]

    # Test center mismatch
    current_sig2 = {
        "center": {"x": 60.0, "y": 0.0, "z": 25.0},  # Different center!
        "area": 2500.0,
        "normal": {"x": 1.0, "y": 0.0, "z": 0.0}
    }

    result = st.validate_topology_signature(
        "box1", "face", "box1_face_1", current_sig2)
    assert result["match"] is False, f"Expected mismatch, got: {result}"
    assert any("center.x" in m for m in result["mismatch_details"])

    # Test normal mismatch
    current_sig3 = {
        "center": {"x": 50.0, "y": 0.0, "z": 25.0},
        "area": 2500.0,
        "normal": {"x": 0.0, "y": 0.0, "z": 1.0}  # Different normal!
    }

    result = st.validate_topology_signature(
        "box1", "face", "box1_face_1", current_sig3)
    assert result["match"] is False, f"Expected mismatch, got: {result}"
    assert any("normal" in m for m in result["mismatch_details"])

    # Test edge length mismatch
    edges_result = adapter.execute_command("get_edges", object_name="box1")
    st.update_from_tool_result("get_edges", edges_result, target_id="box1")

    current_edge_sig = {
        "center": {"x": 50.0, "y": 50.0, "z": 25.0},
        "length": 60.0,  # Different length!
        "tangent": {"x": 0.0, "y": -1.0, "z": 0.0}
    }

    result = st.validate_topology_signature(
        "box1", "edge", "box1_edge_1", current_edge_sig)
    assert result["match"] is False, f"Expected mismatch, got: {result}"
    assert "length: stored=50.0, current=60.0" in result["mismatch_details"]

    print("  [PASS] Changed geometry produces signature mismatch")


def test_topology_version_checks_remain_functional():
    """Test e) existing topology-version checks remain functional."""
    print("Testing e) topology version checks remain functional...")

    adapter = MockTopologyAdapter()
    st = DesignState()

    # Create box
    adapter.execute_command("box", id="box1", Length=100, Width=100, Height=50)

    # Get faces to set topology version
    faces_result = adapter.execute_command("get_faces", object_name="box1")
    st.update_from_tool_result("get_faces", faces_result, target_id="box1")

    # Version should be recorded
    assert st.get_topology_version("box1") == "v1"
    assert st.get_recorded_reference_version("box1", "face") == "v1"

    # Reference at v1 should NOT be stale
    assert st.is_reference_stale("box1", "face", "v1") is False

    # Reference at v0 should BE stale (simulating old reference)
    assert st.is_reference_stale("box1", "face", "v0") is True

    # Test edge version
    edges_result = adapter.execute_command("get_edges", object_name="box1")
    st.update_from_tool_result("get_edges", edges_result, target_id="box1")

    assert st.get_recorded_reference_version("box1", "edge") == "v1"
    assert st.is_reference_stale("box1", "edge", "v1") is False
    assert st.is_reference_stale("box1", "edge", "v0") is True

    # Simulate fillet (topology-altering operation)
    # The target_id for topology version increment should be the object being modified (box1)
    adapter.execute_command("fillet", id="fillet1",
                            target_id="box1", radius=5.0)
    st.update_from_tool_result("fillet", "ok", target_id="box1", args={
                               "target_id": "box1", "radius": 5.0})

    # Topology version should be invalidated for box1
    # (increment_topology_version removes it, so it returns "0")
    # The new fillet1 has no version yet until get_edges/get_faces is called
    assert st.get_topology_version("box1") == "0"
    assert st.get_topology_version("fillet1") == "0"

    # Now reference at v1 should be stale (version advanced)
    assert st.is_reference_stale("box1", "face", "v1") is True

    print("  [PASS] Topology version checks remain functional")


def test_ghost_lineage_resolution_unaffected():
    """Test f) existing ghost/lineage resolution remains unaffected."""
    print("Testing f) ghost/lineage resolution unaffected...")

    adapter = MockTopologyAdapter()
    st = DesignState()

    # Create box
    adapter.execute_command("box", id="box1", Length=100, Width=100, Height=50)
    st.update_from_cad_state(adapter.get_state())

    # Verify box1 is visible
    box1 = st.get_object("box1")
    assert box1 is not None
    assert box1.visible is True

    # Apply fillet
    adapter.execute_command("fillet", id="fillet1",
                            target_id="box1", radius=5.0)
    st.update_from_cad_state(adapter.get_state())

    # box1 should now be ghost (hidden)
    box1 = st.get_object("box1")
    assert box1 is not None
    assert box1.visible is False
    assert "fillet1" in box1.children

    # fillet1 should be visible
    fillet1 = st.get_object("fillet1")
    assert fillet1 is not None
    assert fillet1.visible is True
    assert "box1" in fillet1.parents

    # Ghost resolution should work
    active = st.resolve_active_object("box1")
    assert active is not None
    assert active.object_id == "fillet1"

    # Signatures should still be queryable for ghost object
    face1_sig = st.get_stored_signature("box1", "face", "box1_face_1")
    # Signature might not be captured yet if get_faces wasn't called, but the method should exist
    # and not interfere with ghost resolution

    print("  [PASS] Ghost/lineage resolution unaffected")


def test_signature_tolerance():
    """Test that signature validation respects tolerance."""
    print("Testing signature tolerance...")

    adapter = MockTopologyAdapter()
    st = DesignState()

    adapter.execute_command("box", id="box1", Length=100, Width=100, Height=50)
    faces_result = adapter.execute_command("get_faces", object_name="box1")
    st.update_from_tool_result("get_faces", faces_result, target_id="box1")

    # Within tolerance (1e-6 default)
    current_sig = {
        "center": {"x": 50.0000001, "y": 0.0, "z": 25.0},
        "area": 2500.0000001,
        "normal": {"x": 1.0, "y": 0.0, "z": 0.0}
    }

    result = st.validate_topology_signature(
        "box1", "face", "box1_face_1", current_sig, tolerance=1e-6)
    # With default tolerance 1e-6, the differences are at the boundary
    # Should still match due to floating point rounding
    assert result["match"] is True

    # Outside tolerance
    current_sig2 = {
        "center": {"x": 50.001, "y": 0.0, "z": 25.0},  # 0.001 > 1e-6
        "area": 2500.0,
        "normal": {"x": 1.0, "y": 0.0, "z": 0.0}
    }

    result = st.validate_topology_signature(
        "box1", "face", "box1_face_1", current_sig2, tolerance=1e-6)
    assert result["match"] is False
    assert any("center.x" in m for m in result["mismatch_details"])

    print("  [PASS] Signature tolerance works correctly")


def test_no_stored_signature_returns_match():
    """Test that validation returns match when no signature is stored."""
    print("Testing no stored signature returns match...")

    st = DesignState()

    # No signatures stored at all
    result = st.validate_topology_signature("box1", "face", "box1_face_1", {
        "center": {"x": 50.0, "y": 0.0, "z": 25.0},
        "area": 2500.0,
        "normal": {"x": 1.0, "y": 0.0, "z": 0.0}
    })

    assert result["match"] is True
    assert result["stored_signature"] is None
    assert "no_stored_signature" in result["mismatch_details"]

    print("  [PASS] No stored signature returns match")


if __name__ == "__main__":
    print("=" * 70)
    print("BIP 9.2 — TOPOLOGY SIGNATURE TRACKING TESTS")
    print("=" * 70)
    print()

    test_face_signature_captured()
    test_edge_signature_captured()
    test_unchanged_reference_passes_validation()
    test_changed_geometry_produces_mismatch()
    test_topology_version_checks_remain_functional()
    test_ghost_lineage_resolution_unaffected()
    test_signature_tolerance()
    test_no_stored_signature_returns_match()

    print()
    print("=" * 70)
    print("ALL BIP 9.2 TESTS PASSED")
    print("=" * 70)
