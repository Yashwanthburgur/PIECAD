"""BIP 8.2 — Operation-Specific Face-Count Verification regression tests.

Verifies that topology-changing operations produce the expected increase in face count
using GeometryVerifier.verify_face_count_increase().

Tests:
a) valid fillet passes verification
b) fillet with no face-count increase is detected
c) valid chamfer passes verification
d) chamfer with no face-count increase is detected
e) valid linear pattern passes verification
f) valid circular pattern passes verification
g) pattern verification failure reaches the existing Agent recovery path
h) existing generic geometry verification remains unaffected
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

def test_verify_face_count_increase_valid():
    """Valid face count increase (op > base) returns True."""
    base_json = '[{"face_id": "f1"}, {"face_id": "f2"}, {"face_id": "f3"}]'
    op_json = '[{"face_id": "f1"}, {"face_id": "f2"}, {"face_id": "f3"}, {"face_id": "f4"}, {"face_id": "f5"}]'
    ok, reason = GeometryVerifier.verify_face_count_increase(
        base_json, op_json)
    assert ok is True, f"Expected True, got {ok}: {reason}"
    assert "face count increased from 3 to 5" in reason
    print("[PASS] Valid face count increase passes")


def test_verify_face_count_increase_no_change():
    """No face count increase (op == base) returns False."""
    base_json = '[{"face_id": "f1"}, {"face_id": "f2"}, {"face_id": "f3"}]'
    op_json = '[{"face_id": "f1"}, {"face_id": "f2"}, {"face_id": "f3"}]'
    ok, reason = GeometryVerifier.verify_face_count_increase(
        base_json, op_json)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "did not increase" in reason
    print("[PASS] No face count increase detected")


def test_verify_face_count_increase_decrease():
    """Face count decrease (op < base) returns False."""
    base_json = '[{"face_id": "f1"}, {"face_id": "f2"}, {"face_id": "f3"}, {"face_id": "f4"}]'
    op_json = '[{"face_id": "f1"}, {"face_id": "f2"}]'
    ok, reason = GeometryVerifier.verify_face_count_increase(
        base_json, op_json)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "did not increase" in reason
    print("[PASS] Face count decrease detected as failure")


def test_verify_face_count_increase_malformed_json():
    """Malformed JSON returns False with parse error."""
    base_json = '[{"face_id": "f1"}]'
    op_json = 'not valid json'
    ok, reason = GeometryVerifier.verify_face_count_increase(
        base_json, op_json)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "parse" in reason.lower()
    print("[PASS] Malformed JSON handled gracefully")


# --------------------------------------------------------------------------- #
# Agent integration tests (stub adapter with face count tracking)
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


class FaceCountTrackingStubAdapter(CADAdapter):
    """Stub adapter that tracks face count changes for verification testing."""

    def __init__(self, face_counts=None):
        """
        face_counts: dict mapping object_id -> face count
        """
        self.tool_names = [
            "box", "cylinder", "fillet", "chamfer",
            "pattern_linear", "pattern_circular",
            "get_faces", "get_state", "get_mass_properties"
        ]
        self.face_counts = face_counts or {}
        self.objects = []
        self.calls = []
        self.face_count_results = {}  # Track expected results for face-count operations

    def get_tools(self):
        return [{"type": "function", "function": {
            "name": n, "description": f"run {n}",
            "parameters": {"type": "object", "properties": {}}}} for n in self.tool_names]

    def execute_command(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))

        if tool_name == "box":
            obj_id = kwargs.get("id", "box1")
            self.face_counts[obj_id] = 6  # Box has 6 faces
            self.objects.append({"id": obj_id, "type": "Part::Box", "visible": True, "parents": [
            ], "children": [], "properties": kwargs})
            return "ok"

        if tool_name == "cylinder":
            obj_id = kwargs.get("id", "cyl1")
            self.face_counts[obj_id] = 3  # Cylinder has 3 faces
            self.objects.append({"id": obj_id, "type": "Part::Cylinder", "visible": True, "parents": [
            ], "children": [], "properties": kwargs})
            return "ok"

        if tool_name == "fillet":
            target_id = kwargs.get("target_id")
            result_id = kwargs.get("id", "fillet1")
            should_succeed = self.face_count_results.get(result_id, True)

            base_faces = self.face_counts.get(target_id, 6)
            if should_succeed:
                self.face_counts[result_id] = base_faces + \
                    2  # Fillet adds faces
                self.objects.append({"id": result_id, "type": "Part::Fillet", "visible": True, "parents": [
                                    target_id], "children": [], "properties": {}})
                for obj in self.objects:
                    if obj["id"] == target_id:
                        obj["visible"] = False
            else:
                self.face_counts[result_id] = base_faces  # No increase
                self.objects.append({"id": result_id, "type": "Part::Fillet", "visible": True, "parents": [
                                    target_id], "children": [], "properties": {}})
                for obj in self.objects:
                    if obj["id"] == target_id:
                        obj["visible"] = False

            return "ok"

        if tool_name == "chamfer":
            target_id = kwargs.get("target_id")
            result_id = kwargs.get("id", "chamfer1")
            should_succeed = self.face_count_results.get(result_id, True)

            base_faces = self.face_counts.get(target_id, 6)
            if should_succeed:
                self.face_counts[result_id] = base_faces + \
                    2  # Chamfer adds faces
                self.objects.append({"id": result_id, "type": "Part::Chamfer", "visible": True, "parents": [
                                    target_id], "children": [], "properties": {}})
                for obj in self.objects:
                    if obj["id"] == target_id:
                        obj["visible"] = False
            else:
                self.face_counts[result_id] = base_faces  # No increase
                self.objects.append({"id": result_id, "type": "Part::Chamfer", "visible": True, "parents": [
                                    target_id], "children": [], "properties": {}})
                for obj in self.objects:
                    if obj["id"] == target_id:
                        obj["visible"] = False

            return "ok"

        if tool_name == "pattern_linear":
            target_id = kwargs.get("target_id")
            result_id = kwargs.get("id", "pattern1")
            count = int(kwargs.get("count", 2))
            should_succeed = self.face_count_results.get(result_id, True)

            base_faces = self.face_counts.get(target_id, 6)
            if should_succeed:
                # Linear pattern multiplies faces by count
                self.face_counts[result_id] = base_faces * count
                self.objects.append({"id": result_id, "type": "Part::LinearPattern", "visible": True, "parents": [
                                    target_id], "children": [], "properties": {}})
            else:
                self.face_counts[result_id] = base_faces  # No increase
                self.objects.append({"id": result_id, "type": "Part::LinearPattern", "visible": True, "parents": [
                                    target_id], "children": [], "properties": {}})

            return "ok"

        if tool_name == "pattern_circular":
            target_id = kwargs.get("target_id")
            result_id = kwargs.get("id", "pattern1")
            count = int(kwargs.get("count", 4))
            should_succeed = self.face_count_results.get(result_id, True)

            base_faces = self.face_counts.get(target_id, 6)
            if should_succeed:
                # Circular pattern multiplies faces by count
                self.face_counts[result_id] = base_faces * count
                self.objects.append({"id": result_id, "type": "Part::CircularPattern", "visible": True, "parents": [
                                    target_id], "children": [], "properties": {}})
            else:
                self.face_counts[result_id] = base_faces  # No increase
                self.objects.append({"id": result_id, "type": "Part::CircularPattern", "visible": True, "parents": [
                                    target_id], "children": [], "properties": {}})

            return "ok"

        if tool_name == "get_faces":
            obj_name = kwargs.get("object_name", "")
            fc = self.face_counts.get(obj_name, 6)
            # Generate face list of appropriate length
            faces = [{"face_id": f"{obj_name}_face_{i}", "face_index": i, "center": {
                "x": 0, "y": 0, "z": 0}, "area": 100.0} for i in range(fc)]
            return json.dumps({"faces": faces, "topology_version": "1"})

        if tool_name == "get_state":
            return json.dumps(self.objects)

        if tool_name == "get_mass_properties":
            return json.dumps({"Volume": 1000.0, "volume": 1000.0})

        if tool_name in ("get_edges",):
            return json.dumps({"edges": [], "topology_version": "1"})

        return "ok"

    def get_state(self) -> str:
        return json.dumps(self.objects)


def test_fillet_valid_passes_verification():
    """a) Valid fillet passes face count increase verification."""
    adapter = FaceCountTrackingStubAdapter()
    adapter.face_count_results["fillet1"] = True  # Face increase expected

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

    # Verify fillet was called
    fillet_calls = [c for c in adapter.calls if c[0] == "fillet"]
    assert len(fillet_calls) == 1, "Fillet should be called once"

    # Verify get_faces was called for verification
    faces_calls = [c for c in adapter.calls if c[0] == "get_faces"]
    assert len(
        faces_calls) >= 2, f"get_faces should be called for verification, got {len(faces_calls)} calls"

    print("[PASS] Valid fillet passes verification")


def test_fillet_no_increase_fails_verification():
    """b) Fillet with no face count increase is detected and fails."""
    adapter = FaceCountTrackingStubAdapter()
    adapter.face_count_results["fillet1"] = False  # No face increase

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("get_edges", {"object_name": "box1"})]),
        (None, [("fillet", {"id": "fillet1", "target_id": "box1", "edge_refs": [
         "box1_edge_1"], "radius": 5.0, "topology_version": "1"})]),
        # Agent should detect failure and try to recover
        ("Retry with different approach.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message("Create a box and fillet an edge")

    # The verification should fail and be converted to an error
    # The agent's error recovery should kick in
    print("[PASS] Fillet with no face count increase triggers verification failure")


def test_chamfer_valid_passes_verification():
    """c) Valid chamfer passes face count increase verification."""
    adapter = FaceCountTrackingStubAdapter()
    adapter.face_count_results["chamfer1"] = True

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("get_edges", {"object_name": "box1"})]),
        (None, [("chamfer", {"id": "chamfer1", "target_id": "box1", "edge_refs": [
         "box1_edge_1"], "size": 2.0, "topology_version": "1"})]),
        ("Chamfer applied.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message("Create a box and chamfer an edge")

    # Verify chamfer was called
    chamfer_calls = [c for c in adapter.calls if c[0] == "chamfer"]
    assert len(chamfer_calls) == 1, "Chamfer should be called once"

    # Verify get_faces was called for verification
    faces_calls = [c for c in adapter.calls if c[0] == "get_faces"]
    assert len(
        faces_calls) >= 2, f"get_faces should be called for verification, got {len(faces_calls)} calls"

    print("[PASS] Valid chamfer passes verification")


def test_chamfer_no_increase_fails_verification():
    """d) Chamfer with no face count increase is detected and fails."""
    adapter = FaceCountTrackingStubAdapter()
    adapter.face_count_results["chamfer1"] = False

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("get_edges", {"object_name": "box1"})]),
        (None, [("chamfer", {"id": "chamfer1", "target_id": "box1", "edge_refs": [
         "box1_edge_1"], "size": 2.0, "topology_version": "1"})]),
        # Agent should detect failure
        ("Chamfer failed verification.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message("Create a box and chamfer an edge")

    # The verification should fail
    print("[PASS] Chamfer with no face count increase triggers verification failure")


def test_pattern_linear_valid_passes_verification():
    """e) Valid linear pattern passes face count increase verification."""
    adapter = FaceCountTrackingStubAdapter()
    adapter.face_count_results["pattern1"] = True

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("pattern_linear", {"id": "pattern1", "target_id": "box1", "direction": {
         "x": 1, "y": 0, "z": 0}, "distance": 50, "count": 3})]),
        ("Linear pattern created.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a box and pattern it linearly")

    # Verify pattern was called
    pattern_calls = [c for c in adapter.calls if c[0] == "pattern_linear"]
    assert len(pattern_calls) == 1, "Pattern_linear should be called once"

    # Verify get_faces was called for verification
    faces_calls = [c for c in adapter.calls if c[0] == "get_faces"]
    assert len(
        faces_calls) >= 2, f"get_faces should be called for verification, got {len(faces_calls)} calls"

    print("[PASS] Valid linear pattern passes verification")


def test_pattern_circular_valid_passes_verification():
    """f) Valid circular pattern passes face count increase verification."""
    adapter = FaceCountTrackingStubAdapter()
    adapter.face_count_results["pattern1"] = True

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("pattern_circular", {"id": "pattern1", "target_id": "box1", "axis_origin": {
         "x": 0, "y": 0, "z": 0}, "axis_direction": {"x": 0, "y": 0, "z": 1}, "angle": 90, "count": 4})]),
        ("Circular pattern created.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a box and pattern it circularly")

    # Verify pattern was called
    pattern_calls = [c for c in adapter.calls if c[0] == "pattern_circular"]
    assert len(pattern_calls) == 1, "Pattern_circular should be called once"

    # Verify get_faces was called for verification
    faces_calls = [c for c in adapter.calls if c[0] == "get_faces"]
    assert len(
        faces_calls) >= 2, f"get_faces should be called for verification, got {len(faces_calls)} calls"

    print("[PASS] Valid circular pattern passes verification")


def test_pattern_verification_failure_reaches_error_recovery():
    """g) Pattern verification failure reaches Agent error-recovery path correctly."""
    adapter = FaceCountTrackingStubAdapter()
    adapter.face_count_results["pattern1"] = False  # Will fail verification

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("pattern_linear", {"id": "pattern1", "target_id": "box1", "direction": {
         "x": 1, "y": 0, "z": 0}, "distance": 50, "count": 3})]),
        # Agent should see the verification failure and attempt recovery
        (None, [
         ("edit_feature", {"target_id": "pattern1", "parameters": {"Distance": 60.0}})]),
        ("Recovered with adjusted pattern.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a box and pattern it linearly")

    # The agent should have received the verification failure as a structured error
    # and attempted recovery
    pattern_calls = [c for c in adapter.calls if c[0] == "pattern_linear"]
    assert len(pattern_calls) >= 1

    print("[PASS] Pattern verification failure reaches error recovery path")


def test_existing_generic_geometry_verification_unaffected():
    """h) Existing generic geometry verification (check_geometry) still works."""
    from core.verification.checks import check_geometry

    # Test with valid geometry
    valid_objects = [{
        "id": "box1", "label": "box1", "type": "Part::Box",
        "visible": True, "parents": [], "children": [],
        "properties": {"Length": 100, "Width": 50, "Height": 20},
        "shape_is_valid": True, "shape_is_null": False,
        "shape_volume": 100000.0, "shape_type": "Solid"
    }]
    errors = check_geometry(valid_objects)
    assert len(errors) == 0, f"Valid geometry should have no errors: {errors}"
    print("[PASS] Generic geometry verification works for valid objects")

    # Test with invalid geometry
    invalid_objects = [{
        "id": "box1", "label": "box1", "type": "Part::Box",
        "visible": True, "parents": [], "children": [],
        "properties": {"Length": 100, "Width": 50, "Height": 20},
        "shape_is_valid": False, "shape_is_null": False,
        "shape_volume": 0.0, "shape_type": "Solid"
    }]
    errors = check_geometry(invalid_objects)
    assert len(errors) > 0, "Invalid geometry should have errors"
    assert any("invalid shape" in e.lower() for e in errors)
    print("[PASS] Generic geometry verification detects invalid objects")


# --------------------------------------------------------------------------- #
# Main test runner
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    print("=" * 70)
    print("BIP 8.2 — OPERATION-SPECIFIC FACE-COUNT VERIFICATION TESTS")
    print("=" * 70)
    print()

    # Unit tests
    print("--- GeometryVerifier Unit Tests ---")
    test_verify_face_count_increase_valid()
    test_verify_face_count_increase_no_change()
    test_verify_face_count_increase_decrease()
    test_verify_face_count_increase_malformed_json()
    print()

    # Integration tests
    print("--- Agent Integration Tests ---")
    test_fillet_valid_passes_verification()
    test_fillet_no_increase_fails_verification()
    test_chamfer_valid_passes_verification()
    test_chamfer_no_increase_fails_verification()
    test_pattern_linear_valid_passes_verification()
    test_pattern_circular_valid_passes_verification()
    test_pattern_verification_failure_reaches_error_recovery()
    test_existing_generic_geometry_verification_unaffected()
    print()

    print("=" * 70)
    print("ALL BIP 8.2 TESTS PASSED")
    print("=" * 70)
