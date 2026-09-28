"""BIP 11.1 — Parameter-Level CAD Verification Tests.

Tests verify that requested operation parameters match actual CAD results.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.verification.checks import (  # noqa: E402
    ParameterVerifier,
    VerificationResult,
    DIMENSIONAL_TOLERANCE,
    ANGULAR_TOLERANCE,
    POSITION_TOLERANCE,
    VOLUME_RELATIVE_TOLERANCE,
    COUNT_TOLERANCE,
    DEPTH_TOLERANCE,
    THREAD_TOLERANCE,
    _float_equal,
    _float_equal_rel,
    _int_equal,
)
from core.adapters.interfaces import CADAdapter  # noqa: E402


class MockAdapter(CADAdapter):
    """Mock adapter that returns predefined responses for get_mass_properties."""

    def __init__(self, mass_properties_map=None):
        self.mass_properties_map = mass_properties_map or {}
        self.calls = []

    def get_tools(self):
        return []

    def get_state(self):
        return "[]"

    def execute_command(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))
        if tool_name == "get_mass_properties":
            obj_name = kwargs.get("object_name")
            if obj_name in self.mass_properties_map:
                return self.mass_properties_map[obj_name]
            return json.dumps({"status": "success", "volume": 1000.0, "properties": {}})
        return "ok"


def test_box_parameter_verification_pass():
    """Box with matching dimensions should PASS."""
    print("Testing box parameter verification - PASS...")

    requested = {"length": 100.0, "width": 50.0, "height": 25.0}
    actual_props = {"Length": 100.0, "Width": 50.0, "Height": 25.0}

    result, reason = ParameterVerifier.verify_box_parameters(
        requested, actual_props)
    assert result == VerificationResult.PASS, f"Expected PASS, got {result}: {reason}"
    print("  [PASS] Box with matching dimensions")


def test_box_parameter_verification_fail():
    """Box with mismatched dimensions should FAIL."""
    print("Testing box parameter verification - FAIL...")

    requested = {"length": 100.0, "width": 50.0, "height": 25.0}
    actual_props = {"Length": 100.0, "Width": 50.0,
                    "Height": 30.0}  # height mismatch

    result, reason = ParameterVerifier.verify_box_parameters(
        requested, actual_props)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    assert "height" in reason
    print("  [PASS] Box with mismatched height fails")


def test_box_parameter_verification_unknown():
    """Box with missing live property should be UNKNOWN."""
    print("Testing box parameter verification - UNKNOWN...")

    requested = {"length": 100.0, "width": 50.0, "height": 25.0}
    actual_props = {"Length": 100.0, "Width": 50.0}  # Height missing

    result, reason = ParameterVerifier.verify_box_parameters(
        requested, actual_props)
    assert result == VerificationResult.UNKNOWN, f"Expected UNKNOWN, got {result}: {reason}"
    print("  [PASS] Box with missing property is UNKNOWN")


def test_cylinder_parameter_verification_pass():
    """Cylinder with matching dimensions should PASS."""
    print("Testing cylinder parameter verification - PASS...")

    requested = {"radius": 10.0, "height": 40.0}
    actual_props = {"Radius": 10.0, "Height": 40.0}

    result, reason = ParameterVerifier.verify_cylinder_parameters(
        requested, actual_props)
    assert result == VerificationResult.PASS, f"Expected PASS, got {result}: {reason}"
    print("  [PASS] Cylinder with matching dimensions")


def test_cylinder_parameter_verification_fail():
    """Cylinder with mismatched radius should FAIL."""
    print("Testing cylinder parameter verification - FAIL...")

    requested = {"radius": 10.0, "height": 40.0}
    actual_props = {"Radius": 12.0, "Height": 40.0}  # radius mismatch

    result, reason = ParameterVerifier.verify_cylinder_parameters(
        requested, actual_props)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    assert "radius" in reason
    print("  [PASS] Cylinder with mismatched radius fails")


def test_hole_parameter_verification_pass():
    """Hole with matching drill properties should PASS."""
    print("Testing hole parameter verification - PASS...")

    requested = {"diameter": 20.0, "depth": 30.0, "kind": "simple"}
    hole_mass = json.dumps({"status": "success", "volume": 500.0})
    drill_props = {"Radius": 10.0, "Height": 32.0}  # depth + 2mm over-drill

    result, reason = ParameterVerifier.verify_hole_parameters(
        requested, hole_mass, drill_props)
    assert result == VerificationResult.PASS, f"Expected PASS, got {result}: {reason}"
    print("  [PASS] Hole with matching drill properties")


def test_hole_parameter_verification_fail_diameter():
    """Hole with mismatched diameter should FAIL."""
    print("Testing hole parameter verification - FAIL diameter...")

    requested = {"diameter": 20.0, "depth": 30.0, "kind": "simple"}
    hole_mass = json.dumps({"status": "success", "volume": 500.0})
    # radius 8 -> diameter 16, not 20
    drill_props = {"Radius": 8.0, "Height": 32.0}

    result, reason = ParameterVerifier.verify_hole_parameters(
        requested, hole_mass, drill_props)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    assert "diameter" in reason
    print("  [PASS] Hole with mismatched diameter fails")


def test_hole_parameter_verification_fail_depth():
    """Hole with mismatched depth should FAIL."""
    print("Testing hole parameter verification - FAIL depth...")

    requested = {"diameter": 20.0, "depth": 30.0, "kind": "simple"}
    hole_mass = json.dumps({"status": "success", "volume": 500.0})
    # height 40 -> depth 38 (with +2), not 30
    drill_props = {"Radius": 10.0, "Height": 40.0}

    result, reason = ParameterVerifier.verify_hole_parameters(
        requested, hole_mass, drill_props)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    assert "depth" in reason
    print("  [PASS] Hole with mismatched depth fails")


def test_hole_parameter_verification_pass():
    """Hole with matching drill properties should PASS."""
    print("Testing hole parameter verification - PASS...")

    requested = {"diameter": 20.0, "depth": 30.0, "kind": "simple"}
    hole_mass = json.dumps({"status": "success", "volume": 500.0})
    drill_props = {"Radius": 10.0, "Height": 32.0}  # depth + 2mm over-drill

    result, reason = ParameterVerifier.verify_hole_parameters(
        requested, hole_mass, drill_props)
    assert result == VerificationResult.PASS, f"Expected PASS, got {result}: {reason}"
    print("  [PASS] Hole with matching drill properties")


def test_hole_parameter_verification_fail_diameter():
    """Hole with mismatched diameter should FAIL."""
    print("Testing hole parameter verification - FAIL diameter...")

    requested = {"diameter": 20.0, "depth": 30.0, "kind": "simple"}
    hole_mass = json.dumps({"status": "success", "volume": 500.0})
    # radius 8 -> diameter 16, not 20
    drill_props = {"Radius": 8.0, "Height": 32.0}

    result, reason = ParameterVerifier.verify_hole_parameters(
        requested, hole_mass, drill_props)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    assert "diameter" in reason
    print("  [PASS] Hole with mismatched diameter fails")


def test_hole_parameter_verification_fail_depth():
    """Hole with mismatched depth should FAIL."""
    print("Testing hole parameter verification - FAIL depth...")

    requested = {"diameter": 20.0, "depth": 30.0, "kind": "simple"}
    hole_mass = json.dumps({"status": "success", "volume": 500.0})
    # height 40 -> depth 38 (with +2mm over-drill), not 30
    drill_props = {"Radius": 10.0, "Height": 40.0}

    result, reason = ParameterVerifier.verify_hole_parameters(
        requested, hole_mass, drill_props)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    assert "depth" in reason
    print("  [PASS] Hole with mismatched depth fails")


def test_hole_parameter_verification_unknown():
    """Hole with missing drill properties should be UNKNOWN."""
    print("Testing hole parameter verification - UNKNOWN...")

    requested = {"diameter": 20.0, "depth": 30.0, "kind": "simple"}
    hole_mass = json.dumps({"status": "success", "volume": 500.0})
    drill_props = None  # No drill properties available

    result, reason = ParameterVerifier.verify_hole_parameters(
        requested, hole_mass, drill_props)
    assert result == VerificationResult.UNKNOWN, f"Expected UNKNOWN, got {result}: {reason}"
    print("  [PASS] Hole with missing drill properties is UNKNOWN")


def test_fillet_parameter_verification_pass():
    """Fillet with matching radius should PASS (BIP 11.4)."""
    print("Testing fillet parameter verification - PASS...")

    requested = {"radius": 5.0}
    fillet_props = {"FilletRadius": 5.0}  # Custom property from BIP 11.4

    result, reason = ParameterVerifier.verify_fillet_parameters(
        requested, fillet_props)
    assert result == VerificationResult.PASS, f"Expected PASS, got {result}: {reason}"
    print("  [PASS] Fillet with matching radius")


def test_fillet_parameter_verification_fail():
    """Fillet with mismatched radius should FAIL (BIP 11.4)."""
    print("Testing fillet parameter verification - FAIL...")

    requested = {"radius": 5.0}
    fillet_props = {"FilletRadius": 2.0}  # Mismatched radius

    result, reason = ParameterVerifier.verify_fillet_parameters(
        requested, fillet_props)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    assert "radius" in reason
    print("  [PASS] Fillet with mismatched radius fails")


def test_fillet_parameter_verification_unknown():
    """Fillet without custom FilletRadius property should be UNKNOWN."""
    print("Testing fillet parameter verification - UNKNOWN...")

    requested = {"radius": 5.0}
    fillet_props = {"some": "props"}  # No FilletRadius property

    result, reason = ParameterVerifier.verify_fillet_parameters(
        requested, fillet_props)
    assert result == VerificationResult.UNKNOWN, f"Expected UNKNOWN, got {result}: {reason}"
    print("  [PASS] Fillet without FilletRadius property is UNKNOWN")


def test_chamfer_parameter_verification_pass():
    """Chamfer with matching size should PASS (BIP 11.4)."""
    print("Testing chamfer parameter verification - PASS...")

    requested = {"size": 3.0}
    chamfer_props = {"ChamferSize": 3.0}  # Custom property from BIP 11.4

    result, reason = ParameterVerifier.verify_chamfer_parameters(
        requested, chamfer_props)
    assert result == VerificationResult.PASS, f"Expected PASS, got {result}: {reason}"
    print("  [PASS] Chamfer with matching size")


def test_chamfer_parameter_verification_fail():
    """Chamfer with mismatched size should FAIL (BIP 11.4)."""
    print("Testing chamfer parameter verification - FAIL...")

    requested = {"size": 3.0}
    chamfer_props = {"ChamferSize": 2.0}  # Mismatched size

    result, reason = ParameterVerifier.verify_chamfer_parameters(
        requested, chamfer_props)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    assert "size" in reason
    print("  [PASS] Chamfer with mismatched size fails")


def test_chamfer_parameter_verification_unknown():
    """Chamfer without custom ChamferSize property should be UNKNOWN."""
    print("Testing chamfer parameter verification - UNKNOWN...")

    requested = {"size": 3.0}
    chamfer_props = {"some": "props"}  # No ChamferSize property

    result, reason = ParameterVerifier.verify_chamfer_parameters(
        requested, chamfer_props)
    assert result == VerificationResult.UNKNOWN, f"Expected UNKNOWN, got {result}: {reason}"
    print("  [PASS] Chamfer without ChamferSize property is UNKNOWN")


def test_pattern_parameter_verification_pass():
    """Pattern with matching count should PASS (BIP 11.4)."""
    print("Testing pattern parameter verification - PASS...")

    requested = {"count": 6}
    # Custom property from BIP 11.4
    pattern_props = {"PatternCount": 6, "PatternType": "linear"}

    result, reason = ParameterVerifier.verify_pattern_count(
        requested, pattern_props, expected_type="linear")
    assert result == VerificationResult.PASS, f"Expected PASS, got {result}: {reason}"
    print("  [PASS] Pattern with matching count")


def test_pattern_parameter_verification_fail():
    """Pattern with mismatched count should FAIL (BIP 11.4)."""
    print("Testing pattern parameter verification - FAIL...")

    requested = {"count": 6}
    pattern_props = {"PatternCount": 4,
                     "PatternType": "linear"}  # Mismatched count

    result, reason = ParameterVerifier.verify_pattern_count(
        requested, pattern_props, expected_type="linear")
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    assert "count" in reason
    print("  [PASS] Pattern with mismatched count fails")


def test_pattern_parameter_verification_type_mismatch():
    """Pattern with mismatched PatternType should be UNKNOWN."""
    print("Testing pattern parameter verification - type mismatch...")

    requested = {"count": 6}
    pattern_props = {"PatternCount": 6,
                     "PatternType": "circular"}  # Type mismatch

    result, reason = ParameterVerifier.verify_pattern_count(
        requested, pattern_props, expected_type="linear")
    assert result == VerificationResult.UNKNOWN, f"Expected UNKNOWN, got {result}: {reason}"
    print("  [PASS] Pattern with type mismatch is UNKNOWN")


def test_pattern_parameter_verification_unknown():
    """Pattern without custom PatternCount property should be UNKNOWN."""
    print("Testing pattern parameter verification - UNKNOWN...")

    requested = {"count": 6}
    pattern_props = {"some": "props"}  # No PatternCount property

    result, reason = ParameterVerifier.verify_pattern_count(
        requested, pattern_props, expected_type="linear")
    assert result == VerificationResult.UNKNOWN, f"Expected UNKNOWN, got {result}: {reason}"
    print("  [PASS] Pattern without PatternCount property is UNKNOWN")


def test_pattern_parameter_verification_malformed_count():
    """Pattern with non-numeric count should be FAIL."""
    print("Testing pattern parameter verification - malformed count...")

    requested = {"count": 6}
    pattern_props = {"PatternCount": "six",
                     "PatternType": "linear"}  # Non-numeric count

    result, reason = ParameterVerifier.verify_pattern_count(
        requested, pattern_props, expected_type="linear")
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    print("  [PASS] Pattern with non-numeric count fails")


def test_pattern_parameter_verification_missing_type():
    """Pattern with missing PatternType should be UNKNOWN (missing evidence)."""
    print("Testing pattern parameter verification - missing type...")

    requested = {"count": 6}
    pattern_props = {"PatternCount": 6}  # No PatternType

    result, reason = ParameterVerifier.verify_pattern_count(
        requested, pattern_props, expected_type="linear")
    assert result == VerificationResult.UNKNOWN, f"Expected UNKNOWN, got {result}: {reason}"
    print("  [PASS] Pattern with missing type is UNKNOWN")


def test_fillet_parameter_verification_malformed():
    """Fillet with non-numeric radius should be FAIL."""
    print("Testing fillet parameter verification - malformed radius...")

    requested = {"radius": 5.0}
    fillet_props = {"FilletRadius": "five"}  # Non-numeric radius

    result, reason = ParameterVerifier.verify_fillet_parameters(
        requested, fillet_props)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    print("  [PASS] Fillet with non-numeric radius fails")


def test_chamfer_parameter_verification_malformed():
    """Chamfer with non-numeric size should be FAIL."""
    print("Testing chamfer parameter verification - malformed size...")

    requested = {"size": 3.0}
    chamfer_props = {"ChamferSize": "three"}  # Non-numeric size

    result, reason = ParameterVerifier.verify_chamfer_parameters(
        requested, chamfer_props)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    print("  [PASS] Chamfer with non-numeric size fails")


def test_hole_parameter_verification_malformed_thread():
    """Hole with mismatched thread spec should FAIL."""
    print("Testing hole parameter verification - malformed thread spec...")

    requested = {"diameter": 20.0, "depth": 30.0,
                 "kind": "tapped", "thread_spec": "M6x1.0"}
    hole_mass = json.dumps({"status": "success", "volume": 500.0, "properties": {
                           "ThreadSpec": "M8x1.25"}})  # Mismatched thread
    drill_props = {"Radius": 10.0, "Height": 32.0}

    result, reason = ParameterVerifier.verify_hole_parameters(
        requested, hole_mass, drill_props)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    assert "thread_spec" in reason
    print("  [PASS] Hole with mismatched thread spec fails")


def test_boolean_subtract_verification_pass():
    """Boolean subtract with volume decrease should PASS."""
    print("Testing boolean subtract verification - PASS...")

    requested = {"mode": "subtract"}
    base_mass = json.dumps({"volume": 1000.0})
    result_mass = json.dumps({"volume": 800.0})  # volume decreased

    result, reason = ParameterVerifier.verify_boolean_operation(
        requested, base_mass, result_mass)
    assert result == VerificationResult.PASS, f"Expected PASS, got {result}: {reason}"
    print("  [PASS] Boolean subtract with volume decrease")


def test_boolean_subtract_verification_fail():
    """Boolean subtract without volume decrease should FAIL."""
    print("Testing boolean subtract verification - FAIL...")

    requested = {"mode": "subtract"}
    base_mass = json.dumps({"volume": 1000.0})
    result_mass = json.dumps({"volume": 1000.0})  # no decrease

    result, reason = ParameterVerifier.verify_boolean_operation(
        requested, base_mass, result_mass)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    print("  [PASS] Boolean subtract without volume decrease fails")


def test_boolean_union_verification_pass():
    """Boolean union with volume increase should PASS."""
    print("Testing boolean union verification - PASS...")

    requested = {"mode": "union"}
    base_mass = json.dumps({"volume": 1000.0})
    result_mass = json.dumps({"volume": 1500.0})  # volume increased

    result, reason = ParameterVerifier.verify_boolean_operation(
        requested, base_mass, result_mass)
    assert result == VerificationResult.PASS, f"Expected PASS, got {result}: {reason}"
    print("  [PASS] Boolean union with volume increase")


def test_boolean_union_verification_fail():
    """Boolean union with volume decrease should FAIL."""
    print("Testing boolean union verification - FAIL...")

    requested = {"mode": "union"}
    base_mass = json.dumps({"volume": 1000.0})
    result_mass = json.dumps({"volume": 800.0})  # volume decreased

    result, reason = ParameterVerifier.verify_boolean_operation(
        requested, base_mass, result_mass)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    print("  [PASS] Boolean union with volume decrease fails")


def test_boolean_intersect_verification_pass():
    """Boolean intersect with volume decrease should PASS."""
    print("Testing boolean intersect verification - PASS...")

    requested = {"mode": "intersect"}
    base_mass = json.dumps({"volume": 1000.0})
    result_mass = json.dumps({"volume": 500.0})  # volume decreased

    result, reason = ParameterVerifier.verify_boolean_operation(
        requested, base_mass, result_mass)
    assert result == VerificationResult.PASS, f"Expected PASS, got {result}: {reason}"
    print("  [PASS] Boolean intersect with volume decrease")


def test_boolean_intersect_verification_fail():
    """Boolean intersect without volume decrease should FAIL."""
    print("Testing boolean intersect verification - FAIL...")

    requested = {"mode": "intersect"}
    base_mass = json.dumps({"volume": 1000.0})
    result_mass = json.dumps({"volume": 1000.0})  # no decrease

    result, reason = ParameterVerifier.verify_boolean_operation(
        requested, base_mass, result_mass)
    assert result == VerificationResult.FAIL, f"Expected FAIL, got {result}: {reason}"
    print("  [PASS] Boolean intersect without volume decrease fails")


def test_boolean_unknown_mode():
    """Boolean with unknown mode should be UNKNOWN."""
    print("Testing boolean unknown mode - UNKNOWN...")

    requested = {"mode": "foobar"}
    base_mass = json.dumps({"volume": 1000.0})
    result_mass = json.dumps({"volume": 800.0})

    result, reason = ParameterVerifier.verify_boolean_operation(
        requested, base_mass, result_mass)
    assert result == VerificationResult.UNKNOWN, f"Expected UNKNOWN, got {result}: {reason}"
    print("  [PASS] Boolean unknown mode is UNKNOWN")


def test_tolerance_constants():
    """Verify tolerance constants are defined and reasonable."""
    print("Testing tolerance constants...")

    assert DIMENSIONAL_TOLERANCE == 1e-3
    assert ANGULAR_TOLERANCE == 1e-3
    assert POSITION_TOLERANCE == 1e-3
    assert VOLUME_RELATIVE_TOLERANCE == 1e-6
    assert COUNT_TOLERANCE == 0
    assert DEPTH_TOLERANCE == 1e-3
    assert THREAD_TOLERANCE == 1e-3
    print("  [PASS] Tolerance constants defined correctly")


def test_float_equal():
    """Test _float_equal helper."""
    print("Testing _float_equal...")

    assert _float_equal(10.0, 10.0005) is True  # within 1e-3
    assert _float_equal(10.0, 10.0015) is False  # exceeds 1e-3
    assert _float_equal(10.0, 10.001, tol=1e-2) is True  # larger tolerance
    print("  [PASS] _float_equal works correctly")


def test_float_equal_rel():
    """Test _float_equal_rel helper."""
    print("Testing _float_equal_rel...")

    # relative diff ~5e-7 < 1e-6
    assert _float_equal_rel(1000.0, 1000.0005) is True
    # relative diff ~2e-6 > 1e-6
    assert _float_equal_rel(1000.0, 1000.002) is False
    assert _float_equal_rel(0.0, 0.0) is True
    print("  [PASS] _float_equal_rel works correctly")


def test_int_equal():
    """Test _int_equal helper."""
    print("Testing _int_equal...")

    assert _int_equal(5, 5) is True
    assert _int_equal(5, 6) is False
    print("  [PASS] _int_equal works correctly")


def test_verification_result_enum():
    """Test VerificationResult enum values."""
    print("Testing VerificationResult enum...")

    assert VerificationResult.PASS.value == "pass"
    assert VerificationResult.FAIL.value == "fail"
    assert VerificationResult.UNKNOWN.value == "unknown"
    print("  [PASS] VerificationResult enum correct")


if __name__ == "__main__":
    print("=" * 70)
    print("BIP 11.1 — PARAMETER-LEVEL CAD VERIFICATION TESTS")
    print("=" * 70)
    print()

    test_tolerance_constants()
    test_float_equal()
    test_float_equal_rel()
    test_int_equal()
    test_verification_result_enum()
    print()

    test_box_parameter_verification_pass()
    test_box_parameter_verification_fail()
    test_box_parameter_verification_unknown()
    test_cylinder_parameter_verification_pass()
    test_cylinder_parameter_verification_fail()
    test_hole_parameter_verification_pass()
    test_hole_parameter_verification_fail_diameter()
    test_hole_parameter_verification_fail_depth()
    test_hole_parameter_verification_unknown()
    test_fillet_parameter_verification_unknown()
    test_chamfer_parameter_verification_unknown()
    test_pattern_parameter_verification_unknown()
    test_boolean_subtract_verification_pass()
    test_boolean_subtract_verification_fail()
    test_boolean_union_verification_pass()
    test_boolean_union_verification_fail()
    test_boolean_intersect_verification_pass()
    test_boolean_intersect_verification_fail()
    test_boolean_unknown_mode()
    print()

    print("=" * 70)
    print("ALL BIP 11.1 PARAMETER VERIFICATION TESTS PASSED")
    print("=" * 70)
