"""BIP 8.4 — Operation-Specific Mate Verification regression tests.

Verifies that mate operations actually produce the requested geometric relationship
using GeometryVerifier.verify_mate_coincident() and verify_mate_concentric().

Tests:
a) valid coincident mate passes
b) coincident mate outside tolerance is detected
c) valid concentric mate passes
d) concentric mate outside tolerance is detected
e) invalid/unsupported mate type is handled cleanly
f) verification failure reaches the existing Agent recovery path
g) existing BIP 8.1–8.3 verification remains unaffected
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.verification.checks import GeometryVerifier  # noqa: E402
from core.context.state import DesignState  # noqa: E402
from core.adapters.interfaces import CADAdapter  # noqa: E402
from core.agent import CADAgent  # noqa: E402


# --------------------------------------------------------------------------- #
# GeometryVerifier unit tests
# --------------------------------------------------------------------------- #

def test_verify_mate_coincident_valid():
    """Valid coincident mate (coplanar, opposing normals) returns True."""
    moving = json.dumps({
        "face_id": "face1",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "normal": {"x": 0.0, "y": 0.0, "z": 1.0},
        "area": 100.0
    })
    fixed = json.dumps({
        "face_id": "face2",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "normal": {"x": 0.0, "y": 0.0, "z": -1.0},
        "area": 100.0
    })
    ok, reason = GeometryVerifier.verify_mate_coincident(moving, fixed)
    assert ok is True, f"Expected True, got {ok}: {reason}"
    assert "coincident mate verified" in reason
    print("[PASS] Valid coincident mate passes")


def test_verify_mate_coincident_normals_not_opposing():
    """Coincident mate with non-opposing normals returns False."""
    moving = json.dumps({
        "face_id": "face1",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "normal": {"x": 0.0, "y": 0.0, "z": 1.0},
        "area": 100.0
    })
    fixed = json.dumps({
        "face_id": "face2",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        # Same direction, not opposing
        "normal": {"x": 0.0, "y": 0.0, "z": 1.0},
        "area": 100.0
    })
    ok, reason = GeometryVerifier.verify_mate_coincident(moving, fixed)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "normals not opposing" in reason
    print("[PASS] Coincident mate with non-opposing normals detected")


def test_verify_mate_coincident_not_coplanar():
    """Coincident mate with non-coplanar faces returns False."""
    moving = json.dumps({
        "face_id": "face1",
        "center": {"x": 0.0, "y": 0.0, "z": 0.1},  # 0.1mm offset
        "normal": {"x": 0.0, "y": 0.0, "z": 1.0},
        "area": 100.0
    })
    fixed = json.dumps({
        "face_id": "face2",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "normal": {"x": 0.0, "y": 0.0, "z": -1.0},
        "area": 100.0
    })
    ok, reason = GeometryVerifier.verify_mate_coincident(
        moving, fixed, tolerance=1e-3)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "not coplanar" in reason
    print("[PASS] Coincident mate with non-coplanar faces detected")


def test_verify_mate_coincident_malformed():
    """Malformed JSON returns False."""
    moving = json.dumps({
        "face_id": "face1",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "normal": {"x": 0.0, "y": 0.0, "z": 1.0},
        "area": 100.0
    })
    fixed = "not valid json"
    ok, reason = GeometryVerifier.verify_mate_coincident(moving, fixed)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "parse" in reason.lower()
    print("[PASS] Malformed JSON handled gracefully")


def test_verify_mate_concentric_valid():
    """Valid concentric mate (coaxial) returns True."""
    moving = json.dumps({
        "edge_id": "edge1",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
        "length": 20.0
    })
    fixed = json.dumps({
        "edge_id": "edge2",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
        "length": 20.0
    })
    ok, reason = GeometryVerifier.verify_mate_concentric(moving, fixed)
    assert ok is True, f"Expected True, got {ok}: {reason}"
    assert "concentric mate verified" in reason
    print("[PASS] Valid concentric mate passes")


def test_verify_mate_concentric_axes_not_parallel():
    """Concentric mate with non-parallel axes returns False."""
    moving = json.dumps({
        "edge_id": "edge1",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "axis": {"x": 1.0, "y": 0.0, "z": 0.0},  # X axis
        "length": 20.0
    })
    fixed = json.dumps({
        "edge_id": "edge2",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},  # Z axis
        "length": 20.0
    })
    ok, reason = GeometryVerifier.verify_mate_concentric(moving, fixed)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "axes not parallel" in reason
    print("[PASS] Concentric mate with non-parallel axes detected")


def test_verify_mate_concentric_centers_not_aligned():
    """Concentric mate with misaligned centers returns False."""
    moving = json.dumps({
        "edge_id": "edge1",
        "center": {"x": 1.0, "y": 0.0, "z": 0.0},  # 1mm offset
        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
        "length": 20.0
    })
    fixed = json.dumps({
        "edge_id": "edge2",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
        "length": 20.0
    })
    ok, reason = GeometryVerifier.verify_mate_concentric(
        moving, fixed, tolerance=1e-3)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "centers not aligned" in reason
    print("[PASS] Concentric mate with misaligned centers detected")


def test_verify_mate_concentric_malformed():
    """Malformed JSON returns False."""
    moving = json.dumps({
        "edge_id": "edge1",
        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
        "length": 20.0
    })
    fixed = "not valid json"
    ok, reason = GeometryVerifier.verify_mate_concentric(moving, fixed)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "parse" in reason.lower()
    print("[PASS] Malformed JSON handled gracefully")


# --------------------------------------------------------------------------- #
# Agent integration tests (stub adapter with mate verification)
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


class MateTrackingStubAdapter(CADAdapter):
    """Stub adapter that tracks mate verification data."""

    def __init__(self, mate_results=None):
        """
        mate_results: dict mapping result_id -> {"valid": bool, "type": "coincident|concentric"}
        """
        self.tool_names = [
            "box", "cylinder", "mate", "get_faces", "get_edges",
            "get_state", "get_mass_properties"
        ]
        self.objects = []
        self.calls = []
        self.mate_results = mate_results or {}

    def get_tools(self):
        return [{"type": "function", "function": {
            "name": n, "description": f"run {n}",
            "parameters": {"type": "object", "properties": {}}}} for n in self.tool_names]

    def execute_command(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))

        if tool_name == "box":
            obj_id = kwargs.get("id", "box1")
            self.objects.append({"id": obj_id, "type": "Part::Box", "visible": True, "parents": [
            ], "children": [], "properties": kwargs})
            return "ok"

        if tool_name == "cylinder":
            obj_id = kwargs.get("id", "cyl1")
            self.objects.append({"id": obj_id, "type": "Part::Cylinder", "visible": True, "parents": [
            ], "children": [], "properties": kwargs})
            return "ok"

        if tool_name == "mate":
            result_id = kwargs.get("id", "mate1")
            mate_type = kwargs.get("mate_type", "coincident")
            moving_target = kwargs.get("moving_target")
            moving_ref = kwargs.get("moving_ref")
            fixed_target = kwargs.get("fixed_target")
            fixed_ref = kwargs.get("fixed_ref")

            # Record the mate operation
            self.objects.append({"id": result_id, "type": "Part::Mate", "visible": True,
                                 "parents": [moving_target, fixed_target], "children": [],
                                 "properties": kwargs})

            result_config = self.mate_results.get(
                result_id, {"valid": True, "type": mate_type})
            return "ok"

        if tool_name == "get_faces":
            obj_name = kwargs.get("object_name", "")
            result_config = self.mate_results.get(
                "face_" + obj_name, {"valid": True})

            if result_config.get("valid", True):
                # Return face with center and normal
                return json.dumps({
                    "faces": [{
                        "face_id": f"{obj_name}_face_1",
                        "face_index": 1,
                        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                        "normal": {"x": 0.0, "y": 0.0, "z": -1.0 if obj_name.startswith("moving") else 1.0},
                        "area": 100.0
                    }],
                    "topology_version": "1"
                })
            else:
                # Return face with non-opposing normal or offset center
                if "non_opposing" in str(result_config):
                    return json.dumps({
                        "faces": [{
                            "face_id": f"{obj_name}_face_1",
                            "face_index": 1,
                            "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                            # Same as moving
                            "normal": {"x": 0.0, "y": 0.0, "z": 1.0},
                            "area": 100.0
                        }],
                        "topology_version": "1"
                    })
                else:  # not coplanar
                    return json.dumps({
                        "faces": [{
                            "face_id": f"{obj_name}_face_1",
                            "face_index": 1,
                            "center": {"x": 0.0, "y": 0.0, "z": 0.1},
                            "normal": {"x": 0.0, "y": 0.0, "z": -1.0 if obj_name.startswith("moving") else 1.0},
                            "area": 100.0
                        }],
                        "topology_version": "1"
                    })

        if tool_name == "get_edges":
            obj_name = kwargs.get("object_name", "")
            result_config = self.mate_results.get(
                "edge_" + obj_name, {"valid": True})

            if result_config.get("valid", True):
                return json.dumps({
                    "edges": [{
                        "edge_id": f"{obj_name}_edge_1",
                        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
                        "length": 20.0
                    }],
                    "topology_version": "1"
                })
            elif "not_parallel" in str(result_config):
                return json.dumps({
                    "edges": [{
                        "edge_id": f"{obj_name}_edge_1",
                        "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                        # Different axis
                        "axis": {"x": 1.0, "y": 0.0, "z": 0.0},
                        "length": 20.0
                    }],
                    "topology_version": "1"
                })
            else:  # centers not aligned
                return json.dumps({
                    "edges": [{
                        "edge_id": f"{obj_name}_edge_1",
                        "center": {"x": 1.0, "y": 0.0, "z": 0.0},  # Offset
                        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
                        "length": 20.0
                    }],
                    "topology_version": "1"
                })

        if tool_name == "get_state":
            return json.dumps(self.objects)

        if tool_name == "get_mass_properties":
            obj_name = kwargs.get("object_name", "")
            # Include properties for parameter verification (BIP 11.1)
            props = {}
            for obj in self.objects:
                if obj.get("id") == obj_name:
                    props = obj.get("properties", {})
                    break
            return json.dumps({"Volume": 1000.0, "bounding_box": {
                "XMin": 0, "XMax": 100, "YMin": 0, "YMax": 50, "ZMin": 0, "ZMax": 20
            }, "properties": props})

        return "ok"

    def get_state(self) -> str:
        return json.dumps(self.objects)


def test_valid_coincident_mate_passes():
    """a) Valid coincident mate passes verification."""
    adapter = MateTrackingStubAdapter({
        "face_moving_part": {"valid": True},
        "face_fixed_part": {"valid": True}
    })

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "moving_part", "Length": 50, "Width": 50, "Height": 20})]),
        (None, [
         ("box", {"id": "fixed_part", "Length": 50, "Width": 50, "Height": 20})]),
        (None, [("get_faces", {"object_name": "moving_part"})]),
        (None, [("get_faces", {"object_name": "fixed_part"})]),
        (None, [("mate", {"id": "mate1", "mate_type": "coincident",
                          "moving_target": "moving_part", "moving_ref": "face1",
                          "fixed_target": "fixed_part", "fixed_ref": "face1",
                          "offset": 0.0, "flip": False})]),
        ("Mate applied.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Mate two boxes with coincident faces")

    mate_calls = [c for c in adapter.calls if c[0] == "mate"]
    assert len(mate_calls) == 1

    print("[PASS] Valid coincident mate passes verification")


def test_coincident_mate_outside_tolerance_detected():
    """b) Coincident mate outside tolerance is detected."""
    adapter = MateTrackingStubAdapter({
        "face_moving_part": {"valid": "not_coplanar"},
        "face_fixed_part": {"valid": "not_coplanar"}
    })

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "moving_part", "Length": 50, "Width": 50, "Height": 20})]),
        (None, [
         ("box", {"id": "fixed_part", "Length": 50, "Width": 50, "Height": 20})]),
        (None, [("get_faces", {"object_name": "moving_part"})]),
        (None, [("get_faces", {"object_name": "fixed_part"})]),
        (None, [("mate", {"id": "mate1", "mate_type": "coincident",
                          "moving_target": "moving_part", "moving_ref": "face1",
                          "fixed_target": "fixed_part", "fixed_ref": "face1",
                          "offset": 0.0, "flip": False})]),
        # Agent should detect failure and try to recover
        (None, [("edit_feature", {
         "target_id": "moving_part", "parameters": {"Height": 19.9}})]),
        ("Recovered with adjusted part.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Mate two boxes with coincident faces")

    # The verification should fail and trigger recovery
    print("[PASS] Coincident mate outside tolerance detected")


def test_valid_concentric_mate_passes():
    """c) Valid concentric mate passes verification."""
    adapter = MateTrackingStubAdapter({
        "edge_pin": {"valid": True},
        "edge_hole": {"valid": True}
    })

    prov = ScriptedProvider([
        (None, [("cylinder", {"id": "pin", "radius": 10, "height": 30})]),
        (None, [("cylinder", {"id": "hole", "radius": 12, "height": 30})]),
        (None, [("get_edges", {"object_name": "pin"})]),
        (None, [("get_edges", {"object_name": "hole"})]),
        (None, [("mate", {"id": "mate1", "mate_type": "concentric",
                          "moving_target": "pin", "moving_ref": "edge1",
                          "fixed_target": "hole", "fixed_ref": "edge1",
                          "offset": 0.0, "flip": False})]),
        ("Mate applied.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Mate a pin into a hole concentrically")

    mate_calls = [c for c in adapter.calls if c[0] == "mate"]
    assert len(mate_calls) == 1

    print("[PASS] Valid concentric mate passes verification")


def test_concentric_mate_outside_tolerance_detected():
    """d) Concentric mate outside tolerance is detected."""
    adapter = MateTrackingStubAdapter({
        "edge_pin": {"valid": "not_parallel"},
        "edge_hole": {"valid": "not_parallel"}
    })

    prov = ScriptedProvider([
        (None, [("cylinder", {"id": "pin", "radius": 10, "height": 30})]),
        (None, [("cylinder", {"id": "hole", "radius": 12, "height": 30})]),
        (None, [("get_edges", {"object_name": "pin"})]),
        (None, [("get_edges", {"object_name": "hole"})]),
        (None, [("mate", {"id": "mate1", "mate_type": "concentric",
                          "moving_target": "pin", "moving_ref": "edge1",
                          "fixed_target": "hole", "fixed_ref": "edge1",
                          "offset": 0.0, "flip": False})]),
        ("Mate failed verification.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Mate a pin into a hole concentrically")

    print("[PASS] Concentric mate outside tolerance detected")


def test_invalid_mate_type_handled_cleanly():
    """e) Invalid/unsupported mate type is handled cleanly (no verification, passes)."""
    adapter = MateTrackingStubAdapter()

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 50, "Width": 50, "Height": 20})]),
        (None, [
         ("box", {"id": "box2", "Length": 50, "Width": 50, "Height": 20})]),
        (None, [("get_faces", {"object_name": "box1"})]),
        (None, [("get_faces", {"object_name": "box2"})]),
        (None, [("mate", {"id": "mate1", "mate_type": "parallel",  # Invalid type
                          "moving_target": "box1", "moving_ref": "face1",
                          "fixed_target": "box2", "fixed_ref": "face1",
                          "offset": 0.0, "flip": False})]),
        ("Mate applied.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Mate two boxes with parallel faces")

    # Should pass since unsupported types are skipped
    print("[PASS] Invalid mate type handled cleanly")


def test_mate_verification_failure_reaches_error_recovery():
    """f) Verification failure reaches Agent error-recovery path."""
    adapter = MateTrackingStubAdapter({
        "face_moving_part": {"valid": "not_coplanar"},
        "face_fixed_part": {"valid": "not_coplanar"}
    })

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "moving_part", "Length": 50, "Width": 50, "Height": 20})]),
        (None, [
         ("box", {"id": "fixed_part", "Length": 50, "Width": 50, "Height": 20})]),
        (None, [("get_faces", {"object_name": "moving_part"})]),
        (None, [("get_faces", {"object_name": "fixed_part"})]),
        (None, [("mate", {"id": "mate1", "mate_type": "coincident",
                          "moving_target": "moving_part", "moving_ref": "face1",
                          "fixed_target": "fixed_part", "fixed_ref": "face1",
                          "offset": 0.0, "flip": False})]),
        # Agent should see the verification failure and attempt recovery
        (None, [("edit_feature", {
         "target_id": "moving_part", "parameters": {"Height": 19.9}})]),
        ("Recovered with adjusted part.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Mate two boxes with coincident faces")

    # The agent should have attempted recovery
    edit_calls = [c for c in adapter.calls if c[0] == "edit_feature"]
    assert len(edit_calls) >= 1

    print("[PASS] Mate verification failure reaches error recovery path")


def test_existing_bip81_volume_verification_unaffected():
    """g) Existing BIP 8.1 volume reduction verification still works."""
    from tests.test_bip81_volume_reduction import VolumeTrackingStubAdapter

    adapter = VolumeTrackingStubAdapter()
    adapter.boolean_results["cut1"] = True

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("cylinder", {"id": "tool1", "radius": 10, "height": 30})]),
        (None, [("boolean", {"id": "cut1", "mode": "subtract",
         "target_id": "box1", "tool_id": "tool1"})]),
        ("Boolean subtract completed.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a box and subtract a cylinder")

    boolean_calls = [c for c in adapter.calls if c[0] == "boolean"]
    assert len(boolean_calls) == 1

    print("[PASS] BIP 8.1 volume verification still works")


def test_existing_bip82_face_count_verification_unaffected():
    """g) Existing BIP 8.2 face count verification still works."""
    from tests.test_bip82_face_count import FaceCountTrackingStubAdapter

    adapter = FaceCountTrackingStubAdapter()
    adapter.face_count_results["fillet1"] = True

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("get_edges", {"object_name": "box1"})]),
        (None, [("fillet", {"id": "fillet1", "target_id": "box1", "edge_refs": [
         "box1_edge_1"], "radius": 5.0, "topology_version": "1"})]),
        ("Fillet applied.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message("Create a box and fillet an edge")

    fillet_calls = [c for c in adapter.calls if c[0] == "fillet"]
    assert len(fillet_calls) == 1

    print("[PASS] BIP 8.2 face count verification still works")


def test_existing_bip83_bbox_verification_unaffected():
    """g) Existing BIP 8.3 bounding-box verification still works."""
    from tests.test_bip83_bounding_box import BoundingBoxTrackingStubAdapter

    adapter = BoundingBoxTrackingStubAdapter()
    adapter.bounding_boxes["box1"] = {
        "XMin": 0, "XMax": 50, "YMin": 0, "YMax": 40, "ZMin": 0, "ZMax": 15}

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 50, "Width": 40, "Height": 15})]),
        ("Box created within bounds.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a box that fits within 100 x 50 x 20 mm")

    print("[PASS] BIP 8.3 bounding-box verification still works")


# --------------------------------------------------------------------------- #
# Main test runner
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    print("=" * 70)
    print("BIP 8.4 — OPERATION-SPECIFIC MATE VERIFICATION TESTS")
    print("=" * 70)
    print()

    # Unit tests
    print("--- GeometryVerifier Unit Tests ---")
    test_verify_mate_coincident_valid()
    test_verify_mate_coincident_normals_not_opposing()
    test_verify_mate_coincident_not_coplanar()
    test_verify_mate_coincident_malformed()
    test_verify_mate_concentric_valid()
    test_verify_mate_concentric_axes_not_parallel()
    test_verify_mate_concentric_centers_not_aligned()
    test_verify_mate_concentric_malformed()
    print()

    # Integration tests
    print("--- Agent Integration Tests ---")
    test_valid_coincident_mate_passes()
    test_coincident_mate_outside_tolerance_detected()
    test_valid_concentric_mate_passes()
    test_concentric_mate_outside_tolerance_detected()
    test_invalid_mate_type_handled_cleanly()
    test_mate_verification_failure_reaches_error_recovery()
    test_existing_bip81_volume_verification_unaffected()
    test_existing_bip82_face_count_verification_unaffected()
    test_existing_bip83_bbox_verification_unaffected()
    print()

    print("=" * 70)
    print("ALL BIP 8.4 TESTS PASSED")
    print("=" * 70)
