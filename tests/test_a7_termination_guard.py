"""A7 Termination Guard Tests — deterministic regression guard for Track A termination invariant.

FINAL MUTATION → TERMINATION
must NOT become:
FINAL MUTATION → VERIFICATION → TERMINATION

These tests verify that the evaluation runner produces deterministic results
for the termination guard assertion type, which checks that no verification
tools execute after the final mutating operation.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.run_evals import evaluate_fixture  # noqa: E402
from scripts.eval_reporting import (  # noqa: E402
    build_report,
    normalize_eval_result,
    to_json,
)
from tests.test_a73_deterministic_fixtures import StatefulStubAdapter  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _run_cli(*args, **kwargs) -> subprocess.CompletedProcess:
    """Run the evaluation CLI and return the completed process."""
    script = [
        sys.executable,
        "scripts/run_evals.py",
        "--offline",
        *args,
    ]
    return subprocess.run(
        script,
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parent.parent,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Test 1: VALID FINAL MUTATION TERMINATION — should PASS
# --------------------------------------------------------------------------- #
def test_valid_final_mutation_termination():
    """Valid final mutation termination: final tool is mutation, no verification after."""
    result = _run_cli("--test", "termination_valid_final_mutation")

    assert result.returncode == 0, f"CLI failed: {result.stderr}"

    # Check scoreboard output
    assert "SCOREBOARD: 1/1 PASSING" in result.stdout
    assert "TOTAL FIXTURES: 1" in result.stdout
    assert "PASSED: 1" in result.stdout
    assert "FAILED: 0" in result.stdout
    assert "N/M: 1/1" in result.stdout

    # Check per-fixture result shows PASS with correct failure_category and termination
    assert "[PASS] Termination Valid Final Mutation | failure_category=N/A | termination=normal" in result.stdout

    # Verify aggregate report
    report = _extract_aggregate_report(result.stdout)
    assert report["aggregate"]["fixture_count"] == 1
    assert report["aggregate"]["passed_count"] == 1
    assert report["aggregate"]["failed_count"] == 0
    assert report["aggregate"]["pass_percentage"] == 100.0


# --------------------------------------------------------------------------- #
# Test 2: POST-FINAL VERIFICATION REJECTION — should FAIL
# --------------------------------------------------------------------------- #
def test_post_final_verification_rejection():
    """Post-final verification rejection: verification tool executes after final mutation."""
    result = _run_cli(
        "--test", "termination_post_final_verification_rejection")

    assert result.returncode == 1, f"Expected return code 1 (failure), got {result.returncode}"

    # Check scoreboard output
    assert "SCOREBOARD: 0/1 PASSING" in result.stdout
    assert "TOTAL FIXTURES: 1" in result.stdout
    assert "PASSED: 0" in result.stdout
    assert "FAILED: 1" in result.stdout
    assert "N/M: 0/1" in result.stdout

    # Check per-fixture result shows FAIL with assertion failure_category
    assert "[FAIL] Termination Post-Final Verification Rejection | failure_category=assertion | termination=normal" in result.stdout

    # Check failure reason mentions the verification tool
    assert "verification tool(s) get_mass_properties executed after final mutation (box)" in result.stdout

    # Verify aggregate report
    report = _extract_aggregate_report(result.stdout)
    assert report["aggregate"]["fixture_count"] == 1
    assert report["aggregate"]["passed_count"] == 0
    assert report["aggregate"]["failed_count"] == 1
    assert report["aggregate"]["pass_percentage"] == 0.0
    assert report["aggregate"]["failure_counts_by_category"] == {
        "assertion": 1}


# --------------------------------------------------------------------------- #
# Test 3: INTERMEDIATE VERIFICATION VALID — should PASS
# --------------------------------------------------------------------------- #
def test_intermediate_verification_valid():
    """Intermediate verification valid: verification after intermediate mutation is OK."""
    result = _run_cli("--test", "termination_intermediate_verification_valid")

    assert result.returncode == 0, f"CLI failed: {result.stderr}"

    # Check scoreboard output
    assert "SCOREBOARD: 1/1 PASSING" in result.stdout
    assert "TOTAL FIXTURES: 1" in result.stdout
    assert "PASSED: 1" in result.stdout
    assert "FAILED: 0" in result.stdout
    assert "N/M: 1/1" in result.stdout

    # Check per-fixture result shows PASS
    assert "[PASS] Termination Intermediate Verification Valid | failure_category=N/A | termination=normal" in result.stdout

    # Verify aggregate report
    report = _extract_aggregate_report(result.stdout)
    assert report["aggregate"]["fixture_count"] == 1
    assert report["aggregate"]["passed_count"] == 1
    assert report["aggregate"]["failed_count"] == 0
    assert report["aggregate"]["pass_percentage"] == 100.0


# --------------------------------------------------------------------------- #
# Test 4: ALL THREE TOGETHER — deterministic combined run
# --------------------------------------------------------------------------- #
def test_all_termination_guard_fixtures_together():
    """All three termination guard fixtures run together deterministically."""
    result = _run_cli(
        "--test", "termination_valid_final_mutation",
        "--test", "termination_post_final_verification_rejection",
        "--test", "termination_intermediate_verification_valid",
    )

    assert result.returncode == 1, "Expected one failure (post-final verification)"

    # Check scoreboard output
    assert "SCOREBOARD: 2/3 PASSING" in result.stdout
    assert "TOTAL FIXTURES: 3" in result.stdout
    assert "PASSED: 2" in result.stdout
    assert "FAILED: 1" in result.stdout
    assert "N/M: 2/3" in result.stdout

    # Check individual results
    assert "[PASS] Termination Valid Final Mutation | failure_category=N/A | termination=normal" in result.stdout
    assert "[FAIL] Termination Post-Final Verification Rejection | failure_category=assertion | termination=normal" in result.stdout
    assert "[PASS] Termination Intermediate Verification Valid | failure_category=N/A | termination=normal" in result.stdout

    # Verify aggregate report
    report = _extract_aggregate_report(result.stdout)
    assert report["aggregate"]["fixture_count"] == 3
    assert report["aggregate"]["passed_count"] == 2
    assert report["aggregate"]["failed_count"] == 1
    assert abs(report["aggregate"]["pass_percentage"] -
               66.66666666666666) < 0.01
    assert report["aggregate"]["failure_counts_by_category"] == {
        "assertion": 1}


# --------------------------------------------------------------------------- #
# Test 5: REPEATED IDENTICAL RUNS — deterministic
# --------------------------------------------------------------------------- #
def test_repeated_identical_runs():
    """Two consecutive runs produce identical scoreboards."""
    script_args = [
        "--test", "termination_valid_final_mutation",
        "--test", "termination_post_final_verification_rejection",
        "--test", "termination_intermediate_verification_valid",
    ]

    result1 = _run_cli(*script_args)
    result2 = _run_cli(*script_args)

    assert result1.returncode == 1
    assert result2.returncode == 1

    # Extract scoreboards
    sb1 = _extract_scoreboard(result1.stdout)
    sb2 = _extract_scoreboard(result2.stdout)

    # Scoreboards must be identical
    assert sb1 == sb2, f"Scoreboards differ:\nRun 1:\n{sb1}\n\nRun 2:\n{sb2}"

    # Also verify aggregate reports are identical
    report1 = _extract_aggregate_report(result1.stdout)
    report2 = _extract_aggregate_report(result2.stdout)

    assert report1["aggregate"]["fixture_count"] == report2["aggregate"]["fixture_count"]
    assert report1["aggregate"]["passed_count"] == report2["aggregate"]["passed_count"]
    assert report1["aggregate"]["failed_count"] == report2["aggregate"]["failed_count"]
    assert report1["aggregate"]["pass_percentage"] == report2["aggregate"]["pass_percentage"]


# --------------------------------------------------------------------------- #
# Test 6: JSON SERIALIZATION — valid JSON output
# --------------------------------------------------------------------------- #
def test_json_serialization():
    """Aggregate report and records serialize to valid JSON."""
    result = _run_cli(
        "--test", "termination_valid_final_mutation",
        "--test", "termination_post_final_verification_rejection",
        "--test", "termination_intermediate_verification_valid",
    )

    assert result.returncode == 1

    # Extract and parse aggregate report
    report = _extract_aggregate_report(result.stdout)

    # Verify it's valid JSON by re-serializing
    json_str = json.dumps(report, indent=2)
    parsed = json.loads(json_str)

    assert parsed["aggregate"]["fixture_count"] == 3
    assert len(parsed["records"]) == 3


# --------------------------------------------------------------------------- #
# Test 7: PROGRAMMATIC API — evaluate_fixture directly
# --------------------------------------------------------------------------- #
def test_programmatic_evaluation():
    """Test the programmatic evaluate_fixture API with termination_guard assertions."""
    # Valid final mutation
    fixture_valid = {
        "id": "test_valid",
        "name": "Test Valid",
        "prompt": "Create a box.",
        "expected_tools_called": ["box"],
        "neutral_assertions": [
            {
                "type": "termination_guard",
                "expect_final_mutation": True,
                "expect_verification_after_final_mutation": False
            }
        ],
        "scripted_responses": [
            [[["box", {"id": "box1", "length": 10, "width": 10, "height": 10}]], None],
            [None, "Created a box."]
        ],
    }

    # Post-final verification (should fail)
    fixture_invalid = {
        "id": "test_invalid",
        "name": "Test Invalid",
        "prompt": "Create a box then verify.",
        "expected_tools_called": ["box", "get_mass_properties"],
        "neutral_assertions": [
            {
                "type": "termination_guard",
                "expect_final_mutation": True,
                "expect_verification_after_final_mutation": False
            }
        ],
        "scripted_responses": [
            [[["box", {"id": "box1", "length": 10, "width": 10, "height": 10}]], None],
            [[["get_mass_properties", {"object_name": "box1"}]], None],
            [None, "Box created and verified."]
        ],
    }

    # Intermediate verification (should pass)
    fixture_intermediate = {
        "id": "test_intermediate",
        "name": "Test Intermediate",
        "prompt": "Create a box, verify, then cylinder.",
        "expected_tools_called": ["box", "get_mass_properties", "cylinder"],
        "expected_tool_sequence": ["box", "get_mass_properties", "cylinder"],
        "neutral_assertions": [
            {
                "type": "termination_guard",
                "expect_final_mutation": True,
                "expect_verification_after_final_mutation": False
            },
            {
                "type": "tool_sequence",
                "sequence": ["box", "get_mass_properties", "cylinder"]
            }
        ],
        "scripted_responses": [
            [[["box", {"id": "box1", "length": 10, "width": 10, "height": 10}]], None],
            [[["get_mass_properties", {"object_name": "box1"}]], None],
            [[["cylinder", {"id": "cyl1", "radius": 5, "height": 20}]], None],
            [None, "Created box, verified, then cylinder."]
        ],
    }

    adapter = StatefulStubAdapter()

    res_valid = evaluate_fixture(fixture_valid, adapter)
    res_invalid = evaluate_fixture(fixture_invalid, adapter)
    res_intermediate = evaluate_fixture(fixture_intermediate, adapter)

    # Valid final mutation should pass
    assert res_valid["passed"] is True
    assert res_valid["failure_category"] is None
    assert res_valid["termination_reason"] == "normal"

    # Post-final verification should fail
    assert res_invalid["passed"] is False
    assert res_invalid["failure_category"] == "assertion"
    assert res_invalid["termination_reason"] == "normal"
    assert any("termination_guard" in str(fa)
               for fa in res_invalid["failed_assertions"])
    assert any("get_mass_properties" in str(fa)
               for fa in res_invalid["failed_assertions"])

    # Intermediate verification should pass
    assert res_intermediate["passed"] is True
    assert res_intermediate["failure_category"] is None
    assert res_intermediate["termination_reason"] == "normal"


# --------------------------------------------------------------------------- #
# Test 8: MUTATION TOOLS DEFINITION — verify tool classification
# --------------------------------------------------------------------------- #
def test_mutation_and_verification_tool_classification():
    """Verify that mutation and verification tool sets are correctly defined."""
    # These are defined in _check_termination_guard
    mutation_tools = {
        "box", "cylinder", "boolean", "hole", "fillet", "chamfer",
        "shell", "edit_feature", "pattern_linear", "pattern_circular",
        "delete_feature", "mate", "sketch", "extrude"
    }
    verification_tools = {
        "get_mass_properties", "get_faces", "get_edges", "interference_check"
    }

    # Ensure no overlap
    assert mutation_tools.isdisjoint(verification_tools)

    # Ensure key tools are present
    assert "box" in mutation_tools
    assert "cylinder" in mutation_tools
    assert "fillet" in mutation_tools
    assert "get_mass_properties" in verification_tools
    assert "get_faces" in verification_tools
    assert "get_edges" in verification_tools
    assert "interference_check" in verification_tools


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _extract_scoreboard(stdout: str) -> str:
    """Extract SCOREBOARD section from CLI output."""
    lines = stdout.split("\n")
    in_sb = False
    sb_lines = []
    for line in lines:
        if "SCOREBOARD" in line and "=" * 10 in line:
            in_sb = True
        if in_sb:
            sb_lines.append(line)
        if in_sb and "N/M:" in line:
            break
    return "\n".join(sb_lines)


def _extract_aggregate_report(stdout: str) -> dict:
    """Extract and parse the AGGREGATE REPORT JSON from CLI output."""
    header = "AGGREGATE REPORT (A7.5 reporting layer)"
    header_idx = stdout.find(header)
    if header_idx < 0:
        raise ValueError("AGGREGATE REPORT header not found in output")
    json_start = stdout.find("{", header_idx)
    if json_start < 0:
        raise ValueError("JSON object not found after AGGREGATE REPORT header")
    json_text = stdout[json_start:].strip()
    return json.loads(json_text)


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
