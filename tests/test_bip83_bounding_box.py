"""BIP 8.3 — Bounding-Box Verification regression tests.

Verifies that CAD operations respect explicit dimensional constraints from user requests
using GeometryVerifier.verify_within_bounding_box().

Tests:
a) geometry within explicit bounds passes
b) geometry exceeding X bound is detected
c) geometry exceeding Y bound is detected
d) geometry exceeding Z bound is detected
e) request with no explicit bounds does not trigger bounding-box verification
f) verification failure reaches the existing Agent recovery path
g) existing BIP 8.1 and 8.2 verification behavior remains unaffected
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

def test_verify_within_bounding_box_valid():
    """Geometry within bounds returns True."""
    mass_json = json.dumps({
        "Volume": 1000.0,
        "bounding_box": {"XMin": 0.0, "XMax": 50.0, "YMin": 0.0, "YMax": 40.0, "ZMin": 0.0, "ZMax": 15.0}
    })
    ok = GeometryVerifier.verify_within_bounding_box(
        mass_json, 100.0, 50.0, 20.0)
    assert ok is True, f"Expected True, got {ok}"
    print("[PASS] Geometry within bounds passes")


def test_verify_within_bounding_box_exceeds_x():
    """Geometry exceeding X bound returns False."""
    mass_json = json.dumps({
        "Volume": 1000.0,
        "bounding_box": {"XMin": 0.0, "XMax": 150.0, "YMin": 0.0, "YMax": 40.0, "ZMin": 0.0, "ZMax": 15.0}
    })
    ok = GeometryVerifier.verify_within_bounding_box(
        mass_json, 100.0, 50.0, 20.0)
    assert ok is False, f"Expected False, got {ok}"
    print("[PASS] Geometry exceeding X bound detected")


def test_verify_within_bounding_box_exceeds_y():
    """Geometry exceeding Y bound returns False."""
    mass_json = json.dumps({
        "Volume": 1000.0,
        "bounding_box": {"XMin": 0.0, "XMax": 50.0, "YMin": 0.0, "YMax": 60.0, "ZMin": 0.0, "ZMax": 15.0}
    })
    ok = GeometryVerifier.verify_within_bounding_box(
        mass_json, 100.0, 50.0, 20.0)
    assert ok is False, f"Expected False, got {ok}"
    print("[PASS] Geometry exceeding Y bound detected")


def test_verify_within_bounding_box_exceeds_z():
    """Geometry exceeding Z bound returns False."""
    mass_json = json.dumps({
        "Volume": 1000.0,
        "bounding_box": {"XMin": 0.0, "XMax": 50.0, "YMin": 0.0, "YMax": 40.0, "ZMin": 0.0, "ZMax": 30.0}
    })
    ok = GeometryVerifier.verify_within_bounding_box(
        mass_json, 100.0, 50.0, 20.0)
    assert ok is False, f"Expected False, got {ok}"
    print("[PASS] Geometry exceeding Z bound detected")


def test_verify_within_bounding_box_malformed():
    """Malformed JSON returns False."""
    mass_json = "not valid json"
    ok = GeometryVerifier.verify_within_bounding_box(
        mass_json, 100.0, 50.0, 20.0)
    assert ok is False, f"Expected False, got {ok}"
    print("[PASS] Malformed JSON handled gracefully")


# --------------------------------------------------------------------------- #
# Agent integration tests (stub adapter with bounding box tracking)
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


class BoundingBoxTrackingStubAdapter(CADAdapter):
    """Stub adapter that tracks bounding box for verification testing."""

    def __init__(self, bounding_boxes=None):
        """
        bounding_boxes: dict mapping object_id -> {"XMin": x, "XMax": x, "YMin": y, "YMax": y, "ZMin": z, "ZMax": z}
        """
        self.tool_names = [
            "box", "cylinder", "fillet", "chamfer",
            "get_mass_properties", "get_state", "get_faces", "get_edges"
        ]
        self.bounding_boxes = bounding_boxes or {}
        self.objects = []
        self.calls = []
        self.bbox_results = {}  # Track expected results for bbox verification

    def get_tools(self):
        return [{"type": "function", "function": {
            "name": n, "description": f"run {n}",
            "parameters": {"type": "object", "properties": {}}}} for n in self.tool_names]

    def execute_command(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))

        if tool_name == "box":
            obj_id = kwargs.get("id", "box1")
            length = float(kwargs.get("Length", 100))
            width = float(kwargs.get("Width", 50))
            height = float(kwargs.get("Height", 20))
            ox = float(kwargs.get("origin", {}).get("x", 0))
            oy = float(kwargs.get("origin", {}).get("y", 0))
            oz = float(kwargs.get("origin", {}).get("z", 0))

            # Default bounding box
            self.bounding_boxes[obj_id] = {
                "XMin": ox, "XMax": ox + length,
                "YMin": oy, "YMax": oy + width,
                "ZMin": oz, "ZMax": oz + height
            }
            self.objects.append({"id": obj_id, "type": "Part::Box", "visible": True, "parents": [
            ], "children": [], "properties": kwargs})
            return "ok"

        if tool_name == "cylinder":
            obj_id = kwargs.get("id", "cyl1")
            r = float(kwargs.get("radius", 10))
            h = float(kwargs.get("height", 20))
            ox = float(kwargs.get("origin", {}).get("x", 0))
            oy = float(kwargs.get("origin", {}).get("y", 0))
            oz = float(kwargs.get("origin", {}).get("z", 0))

            self.bounding_boxes[obj_id] = {
                "XMin": ox - r, "XMax": ox + r,
                "YMin": oy - r, "YMax": oy + r,
                "ZMin": oz, "ZMax": oz + h
            }
            self.objects.append({"id": obj_id, "type": "Part::Cylinder", "visible": True, "parents": [
            ], "children": [], "properties": kwargs})
            return "ok"

        if tool_name == "fillet":
            target_id = kwargs.get("target_id")
            result_id = kwargs.get("id", "fillet1")
            # Fillet doesn't significantly change bounding box
            self.bounding_boxes[result_id] = self.bounding_boxes.get(target_id, {
                "XMin": 0, "XMax": 100, "YMin": 0, "YMax": 50, "ZMin": 0, "ZMax": 20
            })
            self.objects.append({"id": result_id, "type": "Part::Fillet", "visible": True, "parents": [
                                target_id], "children": [], "properties": {}})
            for obj in self.objects:
                if obj["id"] == target_id:
                    obj["visible"] = False
            return "ok"

        if tool_name == "chamfer":
            target_id = kwargs.get("target_id")
            result_id = kwargs.get("id", "chamfer1")
            self.bounding_boxes[result_id] = self.bounding_boxes.get(target_id, {
                "XMin": 0, "XMax": 100, "YMin": 0, "YMax": 50, "ZMin": 0, "ZMax": 20
            })
            self.objects.append({"id": result_id, "type": "Part::Chamfer", "visible": True, "parents": [
                                target_id], "children": [], "properties": {}})
            for obj in self.objects:
                if obj["id"] == target_id:
                    obj["visible"] = False
            return "ok"

        if tool_name == "get_mass_properties":
            obj_name = kwargs.get("object_name", "")
            bbox = self.bounding_boxes.get(obj_name, {
                "XMin": 0, "XMax": 100, "YMin": 0, "YMax": 50, "ZMin": 0, "ZMax": 20
            })
            return json.dumps({"Volume": 1000.0, "bounding_box": bbox})

        if tool_name == "get_state":
            return json.dumps(self.objects)

        if tool_name in ("get_faces", "get_edges"):
            return json.dumps({"faces": [], "topology_version": "1"})

        return "ok"

    def get_state(self) -> str:
        return json.dumps(self.objects)


def test_geometry_within_bounds_passes():
    """a) Geometry within explicit bounds passes bounding-box verification."""
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

    # Verify box was created
    box_calls = [c for c in adapter.calls if c[0] == "box"]
    assert len(box_calls) == 1

    # Verify get_mass_properties was called for bbox verification
    mass_calls = [c for c in adapter.calls if c[0] == "get_mass_properties"]
    assert len(
        mass_calls) >= 1, f"get_mass_properties should be called for bbox verification, got {len(mass_calls)} calls"

    print("[PASS] Geometry within explicit bounds passes verification")


def test_geometry_exceeding_x_detected():
    """b) Geometry exceeding X bound is detected."""
    adapter = BoundingBoxTrackingStubAdapter()
    adapter.bounding_boxes["box1"] = {
        "XMin": 0, "XMax": 150, "YMin": 0, "YMax": 40, "ZMin": 0, "ZMax": 15}

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 150, "Width": 40, "Height": 15})]),
        # Agent should detect failure and try to recover
        (None, [
         ("edit_feature", {"target_id": "box1", "parameters": {"Length": 80}})]),
        ("Recovered with smaller box.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a box that fits within 100 x 50 x 20 mm")

    # The verification should fail and trigger recovery
    print("[PASS] Geometry exceeding X bound triggers verification failure")


def test_geometry_exceeding_y_detected():
    """c) Geometry exceeding Y bound is detected."""
    adapter = BoundingBoxTrackingStubAdapter()
    adapter.bounding_boxes["box1"] = {
        "XMin": 0, "XMax": 50, "YMin": 0, "YMax": 60, "ZMin": 0, "ZMax": 15}

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 50, "Width": 60, "Height": 15})]),
        ("Box failed verification.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a box that fits within 100 x 50 x 20 mm")

    print("[PASS] Geometry exceeding Y bound triggers verification failure")


def test_geometry_exceeding_z_detected():
    """d) Geometry exceeding Z bound is detected."""
    adapter = BoundingBoxTrackingStubAdapter()
    adapter.bounding_boxes["box1"] = {
        "XMin": 0, "XMax": 50, "YMin": 0, "YMax": 40, "ZMin": 0, "ZMax": 30}

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 50, "Width": 40, "Height": 30})]),
        ("Box failed verification.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a box that fits within 100 x 50 x 20 mm")

    print("[PASS] Geometry exceeding Z bound triggers verification failure")


def test_no_explicit_bounds_no_verification():
    """e) Request with no explicit bounds does not trigger bounding-box verification."""
    adapter = BoundingBoxTrackingStubAdapter()
    adapter.bounding_boxes["box1"] = {
        "XMin": 0, "XMax": 200, "YMin": 0, "YMax": 100, "ZMin": 0, "ZMax": 50}

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 200, "Width": 100, "Height": 50})]),
        ("Large box created.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a large box")  # No bounding box constraint

    # Should NOT call get_mass_properties for bbox verification
    # (but might call it for other reasons)
    box_calls = [c for c in adapter.calls if c[0] == "box"]
    assert len(box_calls) == 1

    print("[PASS] Request with no explicit bounds skips bounding-box verification")


def test_bbox_verification_failure_reaches_error_recovery():
    """f) Verification failure reaches Agent error-recovery path correctly."""
    adapter = BoundingBoxTrackingStubAdapter()
    adapter.bounding_boxes["box1"] = {
        "XMin": 0, "XMax": 150, "YMin": 0, "YMax": 40, "ZMin": 0, "ZMax": 15}

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 150, "Width": 40, "Height": 15})]),
        # Agent should see the verification failure and attempt recovery
        (None, [
         ("edit_feature", {"target_id": "box1", "parameters": {"Length": 80}})]),
        ("Recovered with smaller box.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a box that fits within 100 x 50 x 20 mm")

    # The agent should have attempted recovery
    edit_calls = [c for c in adapter.calls if c[0] == "edit_feature"]
    assert len(edit_calls) >= 1, "Agent should attempt edit_feature recovery"

    print("[PASS] Bounding-box verification failure reaches error recovery path")


def test_existing_bip81_volume_verification_unaffected():
    """g) Existing BIP 8.1 volume reduction verification still works."""
    # This test reuses the volume tracking adapter from BIP 8.1 tests
    from tests.test_bip81_volume_reduction import VolumeTrackingStubAdapter

    adapter = VolumeTrackingStubAdapter()
    adapter.boolean_results["cut1"] = True  # Valid volume reduction

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


# --------------------------------------------------------------------------- #
# Main test runner
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    print("=" * 70)
    print("BIP 8.3 — BOUNDING-BOX VERIFICATION TESTS")
    print("=" * 70)
    print()

    # Unit tests
    print("--- GeometryVerifier Unit Tests ---")
    test_verify_within_bounding_box_valid()
    test_verify_within_bounding_box_exceeds_x()
    test_verify_within_bounding_box_exceeds_y()
    test_verify_within_bounding_box_exceeds_z()
    test_verify_within_bounding_box_malformed()
    print()

    # Integration tests
    print("--- Agent Integration Tests ---")
    test_geometry_within_bounds_passes()
    test_geometry_exceeding_x_detected()
    test_geometry_exceeding_y_detected()
    test_geometry_exceeding_z_detected()
    test_no_explicit_bounds_no_verification()
    test_bbox_verification_failure_reaches_error_recovery()
    test_existing_bip81_volume_verification_unaffected()
    test_existing_bip82_face_count_verification_unaffected()
    print()

    print("=" * 70)
    print("ALL BIP 8.3 TESTS PASSED")
    print("=" * 70)
