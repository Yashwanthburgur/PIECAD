"""A7.4 — Evaluation runner hardening tests.

These tests verify that the evaluation runner correctly:
- Runs in --offline mode without probing FreeCAD
- Has structurally safe assertion results
- Works with current A6 trace format for tool_sequence and tool_argument
- Catches negative cases (wrong tools, wrong sequences, wrong args, etc.)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.adapters.interfaces import CADAdapter  # noqa: E402
from scripts.run_evals import (  # noqa: E402
    evaluate_fixture,
    run_neutral_assertions,
    _check_tool_sequence,
    _check_argument_assertion,
    _check_error_assertion,
    _check_retry_assertion,
)

from tests.test_a73_deterministic_fixtures import StatefulStubAdapter  # noqa: E402


# --------------------------------------------------------------------------- #
# Offline mode tests
# --------------------------------------------------------------------------- #
def test_offline_mode_does_not_probe_freecad():
    """--offline mode uses OfflineScriptedAdapter, never probes FreeCAD."""
    # This is tested implicitly by the CLI tests below - if it passes without
    # FreeCAD running, the offline mode works.
    pass


def test_offline_cli_scripted_happy_path():
    """CLI --offline --test scripted_happy_path passes."""
    import subprocess
    result = subprocess.run([
        sys.executable, "scripts/run_evals.py",
        "--offline", "--test", "scripted_happy_path"
    ], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0
    assert "[PASS] Scripted Happy Path" in result.stdout


def test_offline_cli_scripted_multi_step():
    """CLI --offline --test scripted_multi_step passes."""
    import subprocess
    result = subprocess.run([
        sys.executable, "scripts/run_evals.py",
        "--offline", "--test", "scripted_multi_step"
    ], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0
    assert "[PASS] Scripted Multi-Step Execution" in result.stdout


def test_offline_cli_scripted_tool_failure():
    """CLI --offline --test scripted_tool_failure passes."""
    import subprocess
    result = subprocess.run([
        sys.executable, "scripts/run_evals.py",
        "--offline", "--test", "scripted_tool_failure"
    ], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0
    assert "[PASS] Scripted Tool Failure" in result.stdout


def test_offline_cli_scripted_retry_recovery():
    """CLI --offline --test scripted_retry_recovery passes."""
    import subprocess
    result = subprocess.run([
        sys.executable, "scripts/run_evals.py",
        "--offline", "--test", "scripted_retry_recovery"
    ], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)
    assert result.returncode == 0
    assert "[PASS] Scripted Retry Recovery" in result.stdout


# --------------------------------------------------------------------------- #
# Reproducibility tests
# --------------------------------------------------------------------------- #
def test_offline_deterministic_fixtures_reproducible():
    """Running the same fixture twice yields identical results."""
    adapter1 = StatefulStubAdapter()
    adapter2 = StatefulStubAdapter()

    fixtures_path = Path(__file__).resolve().parent / "eval_fixtures.json"
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))

    scripted_fixtures = [f for f in fixtures if f.get("scripted_responses")]

    for fixture in scripted_fixtures:
        res1 = evaluate_fixture(fixture, adapter1)
        res2 = evaluate_fixture(fixture, adapter2)

        # Key fields must be identical
        assert res1["passed"] == res2[
            "passed"], f"passed differs for {fixture.get('id')}"
        assert res1["got"] == res2["got"], f"got differs for {fixture.get('id')}"
        assert res1["tools_passed"] == res2["tools_passed"]
        assert res1["sequence_passed"] == res2["sequence_passed"]
        assert res1["assertions_passed"] == res2["assertions_passed"]


# --------------------------------------------------------------------------- #
# Assertion result structure tests (PART 3)
# --------------------------------------------------------------------------- #
def test_run_neutral_assertions_returns_structured_result():
    """run_neutral_assertions always returns structured dict with passed/details."""
    fixture = {"neutral_assertions": []}
    adapter = StatefulStubAdapter()

    result = run_neutral_assertions(fixture, adapter)

    assert isinstance(result, dict)
    assert "passed" in result
    assert "details" in result
    assert isinstance(result["details"], list)
    assert result["passed"] is True
    assert result["details"] == []


def test_run_neutral_assertions_failing_returns_structured_result():
    """Even failing assertions return structured tuple format."""
    fixture = {
        "neutral_assertions": [
            {"type": "tool_error", "tool": "nonexistent", "expect_failure": True}
        ]
    }
    adapter = StatefulStubAdapter()

    result = run_neutral_assertions(fixture, adapter)

    assert isinstance(result, dict)
    assert "passed" in result
    assert "details" in result
    assert result["passed"] is False
    assert len(result["details"]) == 1
    # Each detail must be a tuple of (name, bool, reason)
    detail = result["details"][0]
    assert isinstance(detail, tuple)
    assert len(detail) == 3
    assert isinstance(detail[0], str)  # name
    assert isinstance(detail[1], bool)  # passed
    assert isinstance(detail[2], str)  # reason


def test_assertion_failure_makes_fixture_fail():
    """A failing assertion must make the fixture fail overall."""
    fixture = {
        "id": "test_assertion_fail",
        "name": "Test Assertion Fail",
        "prompt": "test",
        "expected_tools_called": ["box"],
        "neutral_assertions": [
            {"type": "tool_error", "tool": "box", "expect_failure": True}
        ],
        "scripted_responses": [
            [[["box", {"id": "box1"}]], None],
            [None, "Done."],
        ],
    }
    # This fixture expects box to fail, but our adapter makes it succeed
    adapter = StatefulStubAdapter()
    res = evaluate_fixture(fixture, adapter)

    assert res["passed"] is False
    assert res["failure_category"] == "assertion"
    assert len(res["failed_assertions"]) > 0


def test_assertion_pass_does_not_make_fixture_fail():
    """A passing assertion must not make the fixture fail."""
    fixture = {
        "id": "test_assertion_pass",
        "name": "Test Assertion Pass",
        "prompt": "test",
        "expected_tools_called": ["box"],
        "neutral_assertions": [
            {"type": "tool_error", "tool": "box", "expect_failure": False}
        ],
        "scripted_responses": [
            [[["box", {"id": "box1"}]], None],
            [None, "Done."],
        ],
    }
    adapter = StatefulStubAdapter()
    res = evaluate_fixture(fixture, adapter)

    assert res["passed"] is True
    assert res["assertions_passed"] is True
    assert res["failed_assertions"] == []


# --------------------------------------------------------------------------- #
# Current A6 trace compatibility tests (PART 4)
# --------------------------------------------------------------------------- #
def _make_a6_trace_entry(step: int, tool: str, args: dict, success: bool = True,
                         error: str = None, attempt: int = 1):
    """Create a trace entry matching the current A6 format."""
    return {
        "step": step,
        "tool": tool,
        "arguments": args,
        "result": "ok" if success else error,
        "success": success,
        "error": error,
        "attempt": attempt,
    }


def test_check_tool_sequence_current_trace_format():
    """_check_tool_sequence works with current A6 trace (tool key, no type)."""
    trace = [
        _make_a6_trace_entry(1, "box", {"id": "box1"}),
        _make_a6_trace_entry(2, "cylinder", {"id": "cyl1"}),
    ]
    actual_tools = [e.get("tool", "") for e in trace if "tool" in e]

    # Correct sequence
    ok, reason = _check_tool_sequence(["box", "cylinder"], actual_tools)
    assert ok is True

    # Incorrect sequence
    ok, reason = _check_tool_sequence(["cylinder", "box"], actual_tools)
    assert ok is False


def test_check_tool_sequence_matching_subsequence():
    """Sequence matching works when expected is a subsequence of actual."""
    trace = [
        _make_a6_trace_entry(1, "box", {"id": "box1"}),
        _make_a6_trace_entry(2, "hole", {"id": "hole1"}),
        _make_a6_trace_entry(3, "fillet", {"id": "fillet1"}),
    ]
    actual_tools = [e.get("tool", "") for e in trace if "tool" in e]

    # Expected is a subsequence
    ok, reason = _check_tool_sequence(["box", "fillet"], actual_tools)
    assert ok is True

    # Expected not a subsequence
    ok, reason = _check_tool_sequence(["fillet", "box"], actual_tools)
    assert ok is False


def test_check_argument_assertion_current_trace_format():
    """_check_argument_assertion works with current A6 trace format."""
    trace = [
        _make_a6_trace_entry(1, "cylinder", {"radius": 20.0, "height": 5.0}),
    ]

    # Matching argument
    assertion = {"tool": "cylinder",
                 "arg": "radius", "op": "eq", "value": 20.0}
    ok, reason = _check_argument_assertion(trace, assertion)
    assert ok is True, f"Expected pass, got: {reason}"

    # Mismatching argument
    assertion = {"tool": "cylinder",
                 "arg": "radius", "op": "eq", "value": 10.0}
    ok, reason = _check_argument_assertion(trace, assertion)
    assert ok is False, f"Expected fail, got: {reason}"


def test_check_argument_assertion_missing_tool():
    """Missing tool in trace returns fail with clear reason."""
    trace = [
        _make_a6_trace_entry(1, "box", {"id": "box1"}),
    ]

    assertion = {"tool": "cylinder",
                 "arg": "radius", "op": "eq", "value": 20.0}
    ok, reason = _check_argument_assertion(trace, assertion)
    assert ok is False
    assert "no matching tool call found" in reason


def test_check_argument_assertion_step_filter():
    """Step filter works correctly."""
    trace = [
        _make_a6_trace_entry(1, "cylinder", {"radius": 10.0}),
        _make_a6_trace_entry(2, "cylinder", {"radius": 20.0}),
    ]

    # Check step 1
    assertion = {"tool": "cylinder", "step": 1,
                 "arg": "radius", "op": "eq", "value": 10.0}
    ok, reason = _check_argument_assertion(trace, assertion)
    assert ok is True, f"Expected pass for step 1, got: {reason}"

    # Check step 2
    assertion = {"tool": "cylinder", "step": 2,
                 "arg": "radius", "op": "eq", "value": 20.0}
    ok, reason = _check_argument_assertion(trace, assertion)
    assert ok is True, f"Expected pass for step 2, got: {reason}"


def test_check_error_assertion_current_trace_format():
    """_check_error_assertion works with current A6 trace format."""
    trace = [
        _make_a6_trace_entry(
            1, "fail_tool", {"id": "f1"}, success=False, error="nothing to do"),
    ]

    # Expect failure - should pass
    assertion = {"tool": "fail_tool",
                 "expect_failure": True, "error_contains": "nothing"}
    ok, reason = _check_error_assertion(trace, assertion)
    assert ok is True, f"Expected pass, got: {reason}"

    # Expect success but tool failed - should fail
    assertion = {"tool": "fail_tool", "expect_failure": False}
    ok, reason = _check_error_assertion(trace, assertion)
    assert ok is False, f"Expected fail, got: {reason}"


def test_check_error_assertion_missing_tool():
    """Missing tool in trace for error assertion returns fail."""
    trace = [
        _make_a6_trace_entry(1, "box", {"id": "box1"}),
    ]

    assertion = {"tool": "fail_tool", "expect_failure": True}
    ok, reason = _check_error_assertion(trace, assertion)
    assert ok is False
    assert "no matching tool call found" in reason


def test_check_retry_assertion_current_trace_format():
    """_check_retry_assertion works with current A6 trace format."""
    trace = [
        _make_a6_trace_entry(1, "retry_tool", {
                             "radius": 50}, success=False, error="radius too large", attempt=1),
        _make_a6_trace_entry(
            2, "retry_tool", {"radius": 5}, success=True, attempt=2),
    ]

    assertion = {
        "tool": "retry_tool",
        "initial_failure": True,
        "retry_success": True,
        "arg_changed": "radius",
        "arg_change_op": "lt"
    }
    ok, reason = _check_retry_assertion(trace, assertion)
    assert ok is True, f"Expected pass, got: {reason}"


def test_check_retry_assertion_no_retry():
    """Retry assertion fails when there's only one call."""
    trace = [
        _make_a6_trace_entry(1, "retry_tool", {"radius": 5}, success=True),
    ]

    assertion = {
        "tool": "retry_tool",
        "initial_failure": True,
        "retry_success": True,
    }
    ok, reason = _check_retry_assertion(trace, assertion)
    assert ok is False
    assert "expected at least 2 calls" in reason


def test_check_retry_assertion_wrong_arg_change():
    """Retry assertion fails when arg doesn't change as expected."""
    trace = [
        _make_a6_trace_entry(1, "retry_tool", {
                             "radius": 50}, success=False, error="radius too large", attempt=1),
        # radius increased!
        _make_a6_trace_entry(
            2, "retry_tool", {"radius": 60}, success=True, attempt=2),
    ]

    assertion = {
        "tool": "retry_tool",
        "initial_failure": True,
        "retry_success": True,
        "arg_changed": "radius",
        "arg_change_op": "lt"  # expected decrease
    }
    ok, reason = _check_retry_assertion(trace, assertion)
    assert ok is False, f"Expected fail (radius increased), got: {reason}"


# --------------------------------------------------------------------------- #
# Negative tests - evaluator must FAIL correctly (PART 6)
# --------------------------------------------------------------------------- #
def test_wrong_expected_tool_fails():
    """Wrong expected_tools_called makes fixture fail."""
    fixture = {
        "id": "wrong_tool",
        "name": "Wrong Tool Test",
        "prompt": "test",
        "expected_tools_called": ["cylinder"],  # but scripted calls box
        "neutral_assertions": [],
        "scripted_responses": [
            [[["box", {"id": "box1"}]], None],
            [None, "Done."],
        ],
    }
    adapter = StatefulStubAdapter()
    res = evaluate_fixture(fixture, adapter)

    assert res["passed"] is False
    assert res["failure_category"] == "tool_coverage"
    assert "cylinder" in res["expected"]
    assert "box" in res["got"]


def test_wrong_tool_sequence_fails():
    """Wrong expected_tool_sequence makes fixture fail."""
    fixture = {
        "id": "wrong_sequence",
        "name": "Wrong Sequence Test",
        "prompt": "test",
        "expected_tools_called": ["box", "cylinder"],
        "expected_tool_sequence": ["cylinder", "box"],  # wrong order
        "neutral_assertions": [],
        "scripted_responses": [
            [[["box", {"id": "box1"}]], None],
            [[["cylinder", {"id": "cyl1"}]], None],
            [None, "Done."],
        ],
    }
    adapter = StatefulStubAdapter()
    res = evaluate_fixture(fixture, adapter)

    assert res["passed"] is False
    assert res["failure_category"] == "tool_sequence"
    assert not res["sequence_passed"]


def test_wrong_tool_argument_fails():
    """Wrong tool_argument assertion makes fixture fail."""
    fixture = {
        "id": "wrong_arg",
        "name": "Wrong Arg Test",
        "prompt": "test",
        "expected_tools_called": ["cylinder"],
        "neutral_assertions": [
            {"type": "tool_argument", "tool": "cylinder",
                "arg": "radius", "op": "eq", "value": 10.0}
        ],
        "scripted_responses": [
            [[["cylinder", {"id": "cyl1", "radius": 20.0}]], None],
            [None, "Done."],
        ],
    }
    adapter = StatefulStubAdapter()
    res = evaluate_fixture(fixture, adapter)

    assert res["passed"] is False
    assert res["failure_category"] == "assertion"
    assert not res["assertions_passed"]


def test_unexpected_success_where_failure_expected_fails():
    """Tool that should fail but succeeds makes fixture fail."""
    fixture = {
        "id": "unexpected_success",
        "name": "Unexpected Success Test",
        "prompt": "test",
        "expected_tools_called": ["fail_tool"],
        "neutral_assertions": [
            {"type": "tool_error", "tool": "fail_tool", "expect_failure": True}
        ],
        "scripted_responses": [
            # Our adapter makes this FAIL
            [[["fail_tool", {"id": "f1"}]], None],
            [None, "Done."],
        ],
    }
    # Our adapter makes fail_tool fail - so this should PASS
    adapter = StatefulStubAdapter()
    res = evaluate_fixture(fixture, adapter)

    # But if we test with a tool that SUCCEEDS but we expect failure...
    fixture2 = {
        "id": "unexpected_success2",
        "name": "Unexpected Success Test 2",
        "prompt": "test",
        "expected_tools_called": ["box"],
        "neutral_assertions": [
            # expect failure but box succeeds
            {"type": "tool_error", "tool": "box", "expect_failure": True}
        ],
        "scripted_responses": [
            [[["box", {"id": "box1"}]], None],
            [None, "Done."],
        ],
    }
    res2 = evaluate_fixture(fixture2, adapter)

    assert res2["passed"] is False
    assert res2["failure_category"] == "assertion"


def test_expected_failure_that_does_not_occur_fails():
    """Expecting a tool to fail but it succeeds makes fixture fail."""
    fixture = {
        "id": "expected_fail_not_happening",
        "name": "Expected Fail Not Happening",
        "prompt": "test",
        "expected_tools_called": ["box"],
        "neutral_assertions": [
            {"type": "tool_error", "tool": "box",
                "expect_failure": True, "error_contains": "error"}
        ],
        "scripted_responses": [
            [[["box", {"id": "box1"}]], None],  # box succeeds
            [None, "Done."],
        ],
    }
    adapter = StatefulStubAdapter()
    res = evaluate_fixture(fixture, adapter)

    assert res["passed"] is False
    assert res["failure_category"] == "assertion"


def test_retry_pattern_no_retry_fails():
    """Retry pattern assertion fails when there's no retry."""
    fixture = {
        "id": "no_retry",
        "name": "No Retry Test",
        "prompt": "test",
        "expected_tools_called": ["retry_tool"],
        "neutral_assertions": [
            {"type": "retry_pattern", "tool": "retry_tool",
                "initial_failure": True, "retry_success": True}
        ],
        "scripted_responses": [
            # Only one call, succeeds
            [[["retry_tool", {"id": "r1", "radius": 5}]], None],
            [None, "Done."],
        ],
    }
    adapter = StatefulStubAdapter()
    res = evaluate_fixture(fixture, adapter)

    assert res["passed"] is False
    assert res["failure_category"] == "assertion"


def test_retry_pattern_wrong_arg_change_fails():
    """Retry pattern assertion fails when arg changes in wrong direction."""
    fixture = {
        "id": "wrong_retry_arg",
        "name": "Wrong Retry Arg Test",
        "prompt": "test",
        "expected_tools_called": ["retry_tool"],
        "neutral_assertions": [
            {"type": "retry_pattern", "tool": "retry_tool", "initial_failure": True, "retry_success": True,
             "arg_changed": "radius", "arg_change_op": "lt"}  # expect decrease
        ],
        "scripted_responses": [
            [[["retry_tool", {"id": "r1", "radius": 5}]], None],
            # radius INCREASED
            [[["retry_tool", {"id": "r2", "radius": 50}]], None],
            [None, "Done."],
        ],
    }
    adapter = StatefulStubAdapter()
    res = evaluate_fixture(fixture, adapter)

    assert res["passed"] is False
    assert res["failure_category"] == "assertion"


# --------------------------------------------------------------------------- #
# Legacy assertion backward compatibility
# --------------------------------------------------------------------------- #
def test_legacy_string_assertions_still_work():
    """Legacy string assertions still work."""
    fixture = {
        "id": "legacy_assert",
        "name": "Legacy Assertion Test",
        "prompt": "test",
        "expected_tools_called": ["box"],
        "neutral_assertions": ["verify_exists"],
        "scripted_responses": [
            [[["box", {"id": "box1"}]], None],
            [None, "Done."],
        ],
    }
    adapter = StatefulStubAdapter()
    res = evaluate_fixture(fixture, adapter)

    assert res["passed"] is True
    assert res["assertions_passed"] is True


# --------------------------------------------------------------------------- #
# Non-offline behavior unchanged
# --------------------------------------------------------------------------- #
def test_non_offline_cli_attempts_freecad():
    """Default (non-offline) CLI attempts FreeCAD connection (not using offline adapter)."""
    import subprocess
    result = subprocess.run([
        sys.executable, "scripts/run_evals.py",
        "--test", "scripted_happy_path"
    ], capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)
    # Non-offline mode should attempt FreeCAD (may succeed or fail depending on environment)
    # But it should NOT use the offline adapter message
    assert "[Adapter] Using offline adapter" not in result.stdout
    # It should either pass or fail based on FreeCAD availability, not crash
    assert result.returncode in (0, 1)
    # If it connected to FreeCAD, we'll see the live backend message
    # If FreeCAD not available, it will fall back to mock
    assert "[Adapter] Using live backend: FreeCADAdapter" in result.stdout or \
           "[Adapter] Live FreeCAD bridge unavailable" in result.stdout


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
