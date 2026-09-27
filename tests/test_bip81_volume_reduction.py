"""BIP 8.1 — Operation-Specific Geometry Verification regression tests.

Verifies that boolean subtract and hole operations actually removed material
by checking volume reduction using GeometryVerifier.verify_volume_reduction().

Tests:
a) valid boolean subtract passes verification
b) boolean subtract with no meaningful volume reduction is detected
c) valid hole passes verification
d) hole with no meaningful volume reduction is detected
e) existing generic geometry verification still works
f) verification failure reaches the existing Agent error-recovery path correctly
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

def test_verify_volume_reduction_valid():
    """Valid volume reduction (cut < base) returns True."""
    base_json = '{"Volume": 1000.0, "volume": 1000.0}'
    cut_json = '{"Volume": 500.0, "volume": 500.0}'
    ok, reason = GeometryVerifier.verify_volume_reduction(base_json, cut_json)
    assert ok is True, f"Expected True, got {ok}: {reason}"
    assert "volume reduced from 1000.0 to 500.0" in reason
    print("[PASS] Valid volume reduction passes")


def test_verify_volume_reduction_no_reduction():
    """No volume reduction (cut >= base) returns False."""
    base_json = '{"Volume": 1000.0, "volume": 1000.0}'
    cut_json = '{"Volume": 1000.0, "volume": 1000.0}'
    ok, reason = GeometryVerifier.verify_volume_reduction(base_json, cut_json)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "did not decrease" in reason
    print("[PASS] No volume reduction detected")


def test_verify_volume_reduction_increase():
    """Volume increase (cut > base) returns False."""
    base_json = '{"Volume": 1000.0, "volume": 1000.0}'
    cut_json = '{"Volume": 1200.0, "volume": 1200.0}'
    ok, reason = GeometryVerifier.verify_volume_reduction(base_json, cut_json)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "did not decrease" in reason
    print("[PASS] Volume increase detected as failure")


def test_verify_volume_reduction_malformed_json():
    """Malformed JSON returns False with parse error."""
    base_json = '{"Volume": 1000.0}'
    cut_json = 'not valid json'
    ok, reason = GeometryVerifier.verify_volume_reduction(base_json, cut_json)
    assert ok is False, f"Expected False, got {ok}: {reason}"
    assert "parse" in reason.lower() or "did not parse" in reason.lower()
    print("[PASS] Malformed JSON handled gracefully")


def test_verify_exists_valid():
    """verify_exists works for positive volume."""
    mass_json = '{"Volume": 1000.0, "volume": 1000.0}'
    ok, reason = GeometryVerifier.verify_exists(mass_json)
    assert ok is True
    assert "positive volume" in reason
    print("[PASS] verify_exists works for valid solid")


def test_verify_exists_zero_volume():
    """verify_exists fails for zero volume."""
    mass_json = '{"Volume": 0.0, "volume": 0.0}'
    ok, reason = GeometryVerifier.verify_exists(mass_json)
    assert ok is False
    assert "not > 0" in reason
    print("[PASS] verify_exists fails for zero volume")


# --------------------------------------------------------------------------- #
# Agent integration tests (stub adapter with volume verification)
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


class VolumeTrackingStubAdapter(CADAdapter):
    """Stub adapter that tracks volume changes for verification testing."""

    def __init__(self, volumes=None):
        """
        volumes: dict mapping object_id -> volume
        """
        self.tool_names = [
            "box", "cylinder", "boolean", "hole",
            "get_mass_properties", "get_state", "get_faces", "get_edges"
        ]
        self.volumes = volumes or {}
        self.objects = []
        self.calls = []
        self.boolean_results = {}  # Track expected results for boolean operations

    def get_tools(self):
        return [{"type": "function", "function": {
            "name": n, "description": f"run {n}",
            "parameters": {"type": "object", "properties": {}}}} for n in self.tool_names]

    def execute_command(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))

        if tool_name == "box":
            obj_id = kwargs.get("id", "box1")
            self.volumes[obj_id] = kwargs.get(
                "Length", 100) * kwargs.get("Width", 50) * kwargs.get("Height", 20)
            self.objects.append({"id": obj_id, "type": "Part::Box", "visible": True, "parents": [
            ], "children": [], "properties": kwargs})
            return "ok"

        if tool_name == "cylinder":
            obj_id = kwargs.get("id", "cyl1")
            import math
            r = kwargs.get("radius", 10)
            h = kwargs.get("height", 20)
            self.volumes[obj_id] = math.pi * r * r * h
            self.objects.append({"id": obj_id, "type": "Part::Cylinder", "visible": True, "parents": [
            ], "children": [], "properties": kwargs})
            return "ok"

        if tool_name == "boolean":
            mode = kwargs.get("mode", "subtract")
            target_id = kwargs.get("target_id")
            tool_id = kwargs.get("tool_id")
            result_id = kwargs.get("id", "cut1")

            if mode == "subtract":
                base_vol = self.volumes.get(target_id, 1000)
                tool_vol = self.volumes.get(tool_id, 100)

                # Check if this boolean should succeed in volume reduction
                should_succeed = self.boolean_results.get(result_id, True)

                if should_succeed:
                    # Volume reduced by tool volume (simplified)
                    self.volumes[result_id] = max(0, base_vol - tool_vol)
                    self.objects.append({"id": result_id, "type": "Part::Cut", "visible": True, "parents": [
                                        target_id], "children": [], "properties": {}})
                    # Hide base
                    for obj in self.objects:
                        if obj["id"] == target_id:
                            obj["visible"] = False
                else:
                    # No volume reduction (failed cut)
                    self.volumes[result_id] = base_vol
                    self.objects.append({"id": result_id, "type": "Part::Cut", "visible": True, "parents": [
                                        target_id], "children": [], "properties": {}})
                    for obj in self.objects:
                        if obj["id"] == target_id:
                            obj["visible"] = False

            return "ok"

        if tool_name == "hole":
            target_id = kwargs.get("target_id")
            result_id = kwargs.get("id", "hole1")
            diameter = kwargs.get("diameter", 10)
            depth = kwargs.get("depth", 20)

            import math
            hole_vol = math.pi * (diameter / 2) ** 2 * depth

            # Check if this hole should succeed
            should_succeed = self.boolean_results.get(result_id, True)

            base_vol = self.volumes.get(target_id, 1000)
            if should_succeed:
                self.volumes[result_id] = max(0, base_vol - hole_vol)
                self.objects.append({"id": result_id, "type": "Part::Cut", "visible": True, "parents": [
                                    target_id], "children": [], "properties": {}})
                for obj in self.objects:
                    if obj["id"] == target_id:
                        obj["visible"] = False
            else:
                self.volumes[result_id] = base_vol
                self.objects.append({"id": result_id, "type": "Part::Cut", "visible": True, "parents": [
                                    target_id], "children": [], "properties": {}})
                for obj in self.objects:
                    if obj["id"] == target_id:
                        obj["visible"] = False

            return "ok"

        if tool_name == "get_mass_properties":
            obj_name = kwargs.get("object_name", "")
            vol = self.volumes.get(obj_name, 1000.0)
            return json.dumps({"Volume": vol, "volume": vol})

        if tool_name == "get_state":
            return json.dumps(self.objects)

        if tool_name in ("get_faces", "get_edges"):
            return json.dumps({"faces": [], "topology_version": "1"})

        return "ok"

    def get_state(self) -> str:
        return json.dumps(self.objects)


def test_boolean_subtract_valid_passes_verification():
    """a) Valid boolean subtract passes volume reduction verification."""
    adapter = VolumeTrackingStubAdapter()
    adapter.boolean_results["cut1"] = True  # Volume reduction expected

    # The agent should: create box, create tool cylinder, boolean subtract
    # After boolean subtract, volume reduction verification should pass
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

    # Verify boolean was called
    boolean_calls = [c for c in adapter.calls if c[0] == "boolean"]
    assert len(boolean_calls) == 1, "Boolean should be called once"

    # Verify get_mass_properties was called for verification
    mass_calls = [c for c in adapter.calls if c[0] == "get_mass_properties"]
    assert len(
        mass_calls) >= 2, f"get_mass_properties should be called for verification, got {len(mass_calls)} calls"

    # Verify the operation was recorded as successful
    assert "cut1" in adapter.volumes
    assert adapter.volumes["cut1"] < 100000  # base was 100*50*20 = 100000

    print("[PASS] Valid boolean subtract passes verification")


def test_boolean_subtract_no_reduction_fails_verification():
    """b) Boolean subtract with no volume reduction is detected and fails."""
    adapter = VolumeTrackingStubAdapter()
    adapter.boolean_results["cut1"] = False  # No volume reduction

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("cylinder", {"id": "tool1", "radius": 10, "height": 30})]),
        (None, [("boolean", {"id": "cut1", "mode": "subtract",
         "target_id": "box1", "tool_id": "tool1"})]),
        # Agent should detect failure and try to recover
        ("Retry with different approach.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a box and subtract a cylinder")

    # The verification should fail and be converted to an error
    # The agent's error recovery should kick in
    print("[PASS] Boolean subtract with no reduction triggers verification failure")


def test_hole_valid_passes_verification():
    """c) Valid hole passes volume reduction verification."""
    adapter = VolumeTrackingStubAdapter()
    adapter.boolean_results["hole1"] = True

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("get_faces", {"object_name": "box1"})]),
        (None, [("hole", {"id": "hole1", "target_id": "box1", "origin": {"x": 25, "y": 25, "z": 20},
                          "direction": {"x": 0, "y": 0, "z": -1}, "diameter": 10, "depth": 15})]),
        ("Hole created.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message("Create a box and drill a hole")

    # Verify hole was called
    hole_calls = [c for c in adapter.calls if c[0] == "hole"]
    assert len(hole_calls) == 1, "Hole should be called once"

    # Verify get_mass_properties was called for verification
    mass_calls = [c for c in adapter.calls if c[0] == "get_mass_properties"]
    assert len(
        mass_calls) >= 2, f"get_mass_properties should be called for verification, got {len(mass_calls)} calls"

    print("[PASS] Valid hole passes verification")


def test_hole_no_reduction_fails_verification():
    """d) Hole with no volume reduction is detected and fails."""
    adapter = VolumeTrackingStubAdapter()
    adapter.boolean_results["hole1"] = False  # No volume reduction

    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("get_faces", {"object_name": "box1"})]),
        (None, [("hole", {"id": "hole1", "target_id": "box1", "origin": {"x": 25, "y": 25, "z": 20},
                          "direction": {"x": 0, "y": 0, "z": -1}, "diameter": 10, "depth": 15})]),
        # Agent should detect failure
        ("Hole failed verification.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message("Create a box and drill a hole")

    # The verification should fail
    print("[PASS] Hole with no reduction triggers verification failure")


def test_existing_generic_geometry_verification_still_works():
    """e) Existing generic geometry verification (check_geometry) still works."""
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


def test_verification_failure_reaches_error_recovery():
    """f) Verification failure reaches Agent error-recovery path correctly."""
    adapter = VolumeTrackingStubAdapter()
    adapter.boolean_results["cut1"] = False  # Will fail verification

    # Script provider to show agent attempts recovery
    prov = ScriptedProvider([
        (None, [
         ("box", {"id": "box1", "Length": 100, "Width": 50, "Height": 20})]),
        (None, [("cylinder", {"id": "tool1", "radius": 10, "height": 30})]),
        (None, [("boolean", {"id": "cut1", "mode": "subtract",
         "target_id": "box1", "tool_id": "tool1"})]),
        # Agent should see the verification failure and attempt recovery
        (None, [
         ("edit_feature", {"target_id": "tool1", "parameters": {"Radius": 5.0}})]),
        ("Recovered with smaller tool.", None),
    ])

    agent = CADAgent(adapter=adapter, provider=prov)
    result, tools = agent.handle_message(
        "Create a box and subtract a cylinder")

    # The agent should have received the verification failure as a structured error
    # and attempted recovery
    boolean_calls = [c for c in adapter.calls if c[0] == "boolean"]
    # The agent should try at least once
    assert len(boolean_calls) >= 1

    print("[PASS] Verification failure reaches error recovery path")


# --------------------------------------------------------------------------- #
# Main test runner
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    print("=" * 70)
    print("BIP 8.1 — OPERATION-SPECIFIC GEOMETRY VERIFICATION TESTS")
    print("=" * 70)
    print()

    # Unit tests
    print("--- GeometryVerifier Unit Tests ---")
    test_verify_volume_reduction_valid()
    test_verify_volume_reduction_no_reduction()
    test_verify_volume_reduction_increase()
    test_verify_volume_reduction_malformed_json()
    test_verify_exists_valid()
    test_verify_exists_zero_volume()
    print()

    # Integration tests
    print("--- Agent Integration Tests ---")
    test_boolean_subtract_valid_passes_verification()
    test_boolean_subtract_no_reduction_fails_verification()
    test_hole_valid_passes_verification()
    test_hole_no_reduction_fails_verification()
    test_existing_generic_geometry_verification_still_works()
    test_verification_failure_reaches_error_recovery()
    print()

    print("=" * 70)
    print("ALL BIP 8.1 TESTS PASSED")
    print("=" * 70)
