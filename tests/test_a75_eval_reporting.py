"""A7.5 — Evaluation telemetry + reporting layer tests.

These tests exercise the standalone reporting layer in
``scripts/eval_reporting.py``. They validate *behavior*: honest handling of
missing telemetry, descriptive-only aggregation, structural run comparison and
JSON serialization.

They deliberately reuse the *real* result shape emitted by
``scripts/run_evals.evaluate_fixture`` (Part 8) so the reporting layer is proven
against the actual evaluator rather than an invented schema.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.eval_reporting import (  # noqa: E402
    EvalRecord,
    aggregate_eval_results,
    build_report,
    category_breakdown,
    compare_eval_runs,
    normalize_eval_result,
    record_to_dict,
    tool_coverage,
    to_json,
)


# --------------------------------------------------------------------------- #
# Result-shape builders modelled on the REAL evaluator output.                 #
# --------------------------------------------------------------------------- #
def _complete_raw(**overrides):
    """The full success shape emitted by run_evals.evaluate_fixture."""
    raw = {
        "name": "primitive_creation",
        "passed": True,
        "expected": ["box"],
        "expected_sequence": [],
        "got": ["box"],
        "tools_passed": True,
        "sequence_passed": True,
        "sequence_reason": "",
        "assertions_passed": True,
        "failed_assertions": [],
        "termination_reason": "normal",
        "failure_category": None,
        "trace": [
            {"step": 1, "type": "tool_call", "tool": "box",
             "arguments": {"length": 10}, "success": True, "error": None,
             "attempt": 1},
            {"step": 2, "type": "completion", "reply": "done"},
        ],
    }
    raw.update(overrides)
    return raw


def _failed_raw(**overrides):
    raw = _complete_raw(
        passed=False,
        got=[],
        tools_passed=False,
        assertions_passed=False,
        failed_assertions=[["verify_exists", False, "no solid"]],
        failure_category="tool_coverage",
        termination_reason="tool_execution_error",
    )
    raw.update(overrides)
    return raw


# =========================================================================== #
# PART 1/2 — Normalization                                                     #
# =========================================================================== #
def test_normalize_complete_result():
    rec = normalize_eval_result(_complete_raw())
    assert isinstance(rec, EvalRecord)
    assert rec.fixture == "primitive_creation"
    assert rec.passed is True
    assert rec.expected_tools == ["box"]
    assert rec.actual_tools == ["box"]
    assert rec.tools_passed is True
    assert rec.sequence_passed is True
    assert rec.assertions_passed is True
    assert rec.failed_assertions == []
    assert rec.termination_reason == "normal"
    assert rec.failure_category is None
    assert rec.trace_available is True
    assert rec.trace_serializable is True
    assert rec.trace is not None and len(rec.trace) == 2


def test_normalize_failed_result():
    rec = normalize_eval_result(_failed_raw())
    assert rec.passed is False
    assert rec.tools_passed is False
    assert rec.assertions_passed is False
    assert rec.failure_category == "tool_coverage"
    assert rec.failed_assertions == [["verify_exists", False, "no solid"]]


def test_normalize_missing_telemetry_is_none():
    rec = normalize_eval_result(_complete_raw())
    # No telemetry keys at all in the raw result -> every metric is None,
    # never a fabricated zero.
    assert rec.steps is None
    assert rec.total_tokens is None
    assert rec.prompt_tokens is None
    assert rec.completion_tokens is None
    assert rec.rpc_trips is None
    assert rec.duration_seconds is None
    assert rec.router_token_savings is None
    assert rec.context_telemetry is None


def test_zero_is_not_confused_with_missing():
    zero = normalize_eval_result(_complete_raw(steps=0, total_tokens=0))
    assert zero.steps == 0
    assert zero.total_tokens == 0

    missing = normalize_eval_result(_complete_raw())
    assert missing.steps is None
    assert missing.total_tokens is None
    # Explicit inequality: absent ("unknown") is distinct from a genuine zero.
    assert missing.steps != 0
    assert missing.total_tokens != 0


def test_normalize_agent_execution_error_shape():
    # The minimal dict run_evals returns when handle_message raises.
    raw = {
        "name": "boom",
        "passed": False,
        "expected": ["box"],
        "got": [],
        "failure_category": "agent_execution_error",
        "reason": "ValueError: bad",
    }
    rec = normalize_eval_result(raw)
    assert rec.fixture == "boom"
    assert rec.passed is False
    assert rec.expected_tools == ["box"]
    assert rec.actual_tools == []
    assert rec.failure_category == "agent_execution_error"
    assert rec.steps is None
    assert rec.trace_available is None  # key absent => unknown, not False


def test_normalize_telemetry_nested_shapes():
    raw = _complete_raw(
        telemetry={
            "steps": 4,
            "total_tokens": 250,
            "rpc_trips": 7,
            "duration_seconds": 1.5,
            "token_telemetry": {"total_input_tokens": 200,
                                "total_output_tokens": 50},
        },
    )
    rec = normalize_eval_result(raw)
    assert rec.steps == 4
    assert rec.total_tokens == 250
    assert rec.rpc_trips == 7
    assert rec.duration_seconds == 1.5
    assert rec.prompt_tokens == 200
    assert rec.completion_tokens == 50


def test_normalize_flat_token_telemetry():
    # token_telemetry as a top-level sibling (telemetry-augmented shape).
    raw = _complete_raw(
        steps=2,
        token_telemetry={"total_tokens": 99, "total_input_tokens": 60,
                         "total_output_tokens": 39},
    )
    rec = normalize_eval_result(raw)
    assert rec.total_tokens == 99
    assert rec.prompt_tokens == 60
    assert rec.completion_tokens == 39


def test_normalize_trace_tristate_and_unsafe_object():
    # (a) key absent -> unknown
    assert normalize_eval_result(_complete_raw()).trace_available is True
    raw_no_key = _complete_raw()
    del raw_no_key["trace"]
    assert normalize_eval_result(raw_no_key).trace_available is None

    # (b) explicit empty -> False
    assert normalize_eval_result(
        _complete_raw(trace=[])).trace_available is False

    # (c) unserializable trace is NOT retained but availability is still True
    rec = normalize_eval_result(_complete_raw(trace=[object()]))
    assert rec.trace_available is True
    assert rec.trace_serializable is False
    assert rec.trace is None


def test_normalize_bool_not_treated_as_number():
    rec = normalize_eval_result(_complete_raw(steps=True))
    # ``True`` must not become step count 1.
    assert rec.steps is None


def test_normalize_non_mapping_rejected():
    try:
        # type: ignore[arg-type]
        normalize_eval_result(["not", "a", "mapping"])
    except TypeError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected TypeError for non-mapping input")


# =========================================================================== #
# PART 3 — Aggregation                                                         #
# =========================================================================== #
def test_aggregate_empty_set():
    agg = aggregate_eval_results([])
    assert agg["fixture_count"] == 0
    assert agg["passed_count"] == 0
    assert agg["failed_count"] == 0
    assert agg["pass_percentage"] is None
    assert agg["steps"]["average"] is None
    assert agg["total_tokens"]["total"] is None


def test_aggregate_counts_pass_fail():
    recs = [
        normalize_eval_result(_complete_raw(name="a", passed=True)),
        normalize_eval_result(_complete_raw(name="b", passed=False)),
        normalize_eval_result(_complete_raw(name="c", passed=True)),
    ]
    agg = aggregate_eval_results(recs)
    assert agg["fixture_count"] == 3
    assert agg["passed_count"] == 2
    assert agg["failed_count"] == 1
    assert abs(agg["pass_percentage"] - (2 / 3 * 100.0)) < 1e-9


def test_aggregate_all_failed():
    recs = [normalize_eval_result(_failed_raw(name=f"f{i}")) for i in range(4)]
    agg = aggregate_eval_results(recs)
    assert agg["passed_count"] == 0
    assert agg["failed_count"] == 4
    assert agg["pass_percentage"] == 0.0


def test_averages_ignore_unavailable_values():
    # Only 2 of 4 records have tokens: 100 and 300 -> avg 200, total 400.
    recs = [
        normalize_eval_result(_complete_raw(name="a", total_tokens=100)),
        normalize_eval_result(_complete_raw(name="b")),  # missing
        normalize_eval_result(_complete_raw(name="c", total_tokens=300)),
        normalize_eval_result(_complete_raw(name="d")),  # missing
    ]
    agg = aggregate_eval_results(recs)
    tok = agg["total_tokens"]
    assert tok["available_count"] == 2
    assert tok["total"] == 400
    assert tok["average"] == 200.0
    assert agg["telemetry_availability"]["total_tokens"] == 2


def test_mixed_telemetry_does_not_corrupt_average():
    # A missing value (None) must NOT be folded in as 0.
    recs = [
        normalize_eval_result(_complete_raw(name="a", steps=10)),
        normalize_eval_result(_complete_raw(name="b")),  # None
    ]
    agg = aggregate_eval_results(recs)
    assert agg["steps"]["average"] == 10.0  # not 5.0


def test_all_missing_telemetry_yields_no_average():
    recs = [normalize_eval_result(
        _complete_raw(name=f"m{i}")) for i in range(3)]
    agg = aggregate_eval_results(recs)
    for key in ("steps", "total_tokens", "rpc_trips", "duration_seconds"):
        assert agg[key]["average"] is None
        assert agg[key]["total"] is None
        assert agg[key]["available_count"] == 0


def test_failure_category_counts():
    recs = [
        normalize_eval_result(_failed_raw(name="a",
                                          failure_category="tool_coverage")),
        normalize_eval_result(_failed_raw(name="b",
                                          failure_category="tool_coverage")),
        normalize_eval_result(_failed_raw(name="c",
                                          failure_category="assertion")),
        normalize_eval_result(_complete_raw(name="d", passed=True)),
    ]
    agg = aggregate_eval_results(recs)
    assert agg["failure_counts_by_category"] == {
        "tool_coverage": 2, "assertion": 1}


def test_termination_reason_counts():
    recs = [
        normalize_eval_result(_complete_raw(name="a",
                                            termination_reason="normal")),
        normalize_eval_result(_complete_raw(name="b",
                                            termination_reason="max_steps")),
        normalize_eval_result(_complete_raw(name="c",
                                            termination_reason="normal")),
    ]
    agg = aggregate_eval_results(recs)
    assert agg["termination_reason_counts"] == {"normal": 2, "max_steps": 1}


def test_assertion_failure_counts():
    recs = [
        normalize_eval_result(_complete_raw(
            name="a", failed_assertions=[["x", False, "r"]])),
        normalize_eval_result(_complete_raw(
            name="b", failed_assertions=[["y", False, "r"], ["z", False, "r"]])),
        normalize_eval_result(_complete_raw(name="c", failed_assertions=[])),
        normalize_eval_result(_complete_raw(name="d")),  # unknown -> None
    ]
    agg = aggregate_eval_results(recs)
    assert agg["assertion_failures"]["total_failed_assertions"] == 3
    assert agg["assertion_failures"]["records_with_failed_assertions"] == 2


def test_tool_coverage_aggregation():
    recs = [
        normalize_eval_result(_complete_raw(
            name="a", expected=["box", "hole"], got=["box", "hole", "box"],
            tools_passed=True)),
        normalize_eval_result(_complete_raw(
            name="b", expected=["box"], got=[], tools_passed=False)),
    ]
    cov = tool_coverage(recs)
    assert cov["records_with_expected_tools"] == 2
    assert cov["records_tools_passed"] == 1
    assert cov["tools_pass_percentage"] == 50.0
    assert cov["distinct_expected_tools"] == ["box", "hole"]
    assert cov["actual_tool_counts"]["box"] == 2
    assert cov["expected_tool_occurrences"] == 3


def test_aggregate_accepts_raw_dicts():
    agg = aggregate_eval_results([_complete_raw(total_tokens=5)])
    assert agg["fixture_count"] == 1
    assert agg["total_tokens"]["total"] == 5


# =========================================================================== #
# PART 4 — Category breakdown                                                  #
# =========================================================================== #
def test_category_breakdown_counts():
    recs = [
        normalize_eval_result(_complete_raw(name="a", passed=True)),
        normalize_eval_result(_failed_raw(name="b")),
    ]
    cat = category_breakdown(recs)
    assert cat["passed"] == 1
    assert cat["failed"] == 1
    assert cat["unknown"] == 0
    assert cat["failure_categories"] == {"tool_coverage": 1}
    assert cat["fixtures_with_assertion_failures"] == 1


def test_category_breakdown_retry_detection_from_trace():
    trace = [
        {"step": 1, "tool": "retry_tool", "attempt": 1, "success": False},
        {"step": 2, "tool": "retry_tool", "attempt": 2, "success": True},
    ]
    rec_retry = normalize_eval_result(_complete_raw(name="r", trace=trace))
    rec_plain = normalize_eval_result(
        _complete_raw(name="p", trace=[{"step": 1, "attempt": 1}]))
    rec_unknown = normalize_eval_result(_complete_raw(name="u"))
    del rec_unknown.trace  # simulate key-absent trace
    rec_unknown.trace = None

    cat = category_breakdown([rec_retry, rec_plain, rec_unknown])
    assert cat["fixtures_with_retries"] == 1
    assert cat["retry_verdict_known"] == 2  # unknown trace excluded


def test_category_breakdown_sequence_detection():
    rec_seq = normalize_eval_result(
        _complete_raw(name="s", expected_sequence=["box", "cylinder"]))
    rec_multi = normalize_eval_result(
        _complete_raw(name="m", got=["box", "cylinder"], expected_sequence=[]))
    rec_single = normalize_eval_result(
        _complete_raw(name="one", got=["box"], expected_sequence=[]))
    cat = category_breakdown([rec_seq, rec_multi, rec_single])
    assert cat["fixtures_with_sequences"] == 2
    assert cat["sequence_verdict_known"] == 3


# =========================================================================== #
# PART 5 — Reproducibility comparison                                          #
# =========================================================================== #
def test_compare_identical_runs():
    run_a = [normalize_eval_result(_complete_raw(name="a", steps=3)),
             normalize_eval_result(_complete_raw(name="b", steps=5))]
    run_b = [normalize_eval_result(_complete_raw(name="a", steps=3)),
             normalize_eval_result(_complete_raw(name="b", steps=5))]
    cmp = compare_eval_runs(run_a, run_b)
    assert cmp["changed_fixtures"] == []
    assert set(cmp["identical_fixtures"]) == {"a", "b"}
    assert cmp["fixtures"]["a"]["verdict"] == "identical"
    assert cmp["fixtures"]["a"]["passed"]["status"] == "identical"
    assert cmp["fixtures"]["a"]["metrics"]["steps"]["status"] == "identical"


def test_compare_pass_fail_differs():
    run_a = [normalize_eval_result(_complete_raw(name="a", passed=True))]
    run_b = [normalize_eval_result(_failed_raw(name="a"))]
    cmp = compare_eval_runs(run_a, run_b)
    assert cmp["fixtures"]["a"]["passed"] == {
        "status": "changed", "a": True, "b": False}
    assert cmp["changed_fixtures"] == ["a"]


def test_compare_tools_differ():
    run_a = [normalize_eval_result(_complete_raw(name="a", got=["box"]))]
    run_b = [normalize_eval_result(
        _complete_raw(name="a", got=["box", "hole"]))]
    cmp = compare_eval_runs(run_a, run_b)
    entry = cmp["fixtures"]["a"]
    assert entry["actual_tools"]["status"] == "changed"
    assert entry["actual_tools_added"] == ["hole"]
    assert entry["actual_tools_removed"] == []
    assert entry["verdict"] == "changed"


def test_compare_telemetry_differs():
    run_a = [normalize_eval_result(_complete_raw(name="a", total_tokens=100))]
    run_b = [normalize_eval_result(_complete_raw(name="a", total_tokens=120))]
    cmp = compare_eval_runs(run_a, run_b)
    assert cmp["fixtures"]["a"]["metrics"]["total_tokens"] == {
        "status": "changed", "a": 100, "b": 120}
    assert cmp["changed_fixtures"] == ["a"]


def test_compare_telemetry_unavailable_when_both_none():
    run_a = [normalize_eval_result(_complete_raw(name="a"))]
    run_b = [normalize_eval_result(_complete_raw(name="a"))]
    cmp = compare_eval_runs(run_a, run_b)
    # Both missing -> explicitly "unavailable", NOT "identical".
    assert cmp["fixtures"]["a"]["metrics"]["total_tokens"]["status"] == \
        "unavailable"
    # And that unavailable metric must not force a "changed" verdict.
    assert cmp["fixtures"]["a"]["verdict"] == "identical"


def test_compare_fixtures_only_in_one_run():
    run_a = [normalize_eval_result(_complete_raw(name="a")),
             normalize_eval_result(_complete_raw(name="b"))]
    run_b = [normalize_eval_result(_complete_raw(name="a")),
             normalize_eval_result(_complete_raw(name="c"))]
    cmp = compare_eval_runs(run_a, run_b)
    assert cmp["fixtures_only_in_a"] == ["b"]
    assert cmp["fixtures_only_in_b"] == ["c"]
    assert "b" not in cmp["fixtures"]
    assert "c" not in cmp["fixtures"]


def test_compare_termination_difference():
    run_a = [normalize_eval_result(
        _complete_raw(name="a", termination_reason="normal"))]
    run_b = [normalize_eval_result(
        _complete_raw(name="a", termination_reason="max_steps"))]
    cmp = compare_eval_runs(run_a, run_b)
    assert cmp["fixtures"]["a"]["termination_reason"]["status"] == "changed"


# =========================================================================== #
# PART 6 — Serialization                                                       #
# =========================================================================== #
def test_record_serialization_none_stays_null():
    rec = normalize_eval_result(_complete_raw())
    d = record_to_dict(rec)
    assert d["steps"] is None
    assert d["fixture"] == "primitive_creation"
    # JSON round-trip preserves null.
    parsed = json.loads(to_json(rec))
    assert parsed["steps"] is None
    assert parsed["passed"] is True


def test_json_serialization_of_report():
    recs = [
        normalize_eval_result(_complete_raw(name="a", steps=2)),
        normalize_eval_result(_failed_raw(name="b")),
    ]
    report = build_report(recs)
    text = to_json(report)
    parsed = json.loads(text)  # must not raise
    assert parsed["aggregate"]["fixture_count"] == 2
    assert parsed["categories"]["failed"] == 1
    assert len(parsed["records"]) == 2


def test_json_serialization_of_comparison():
    cmp = compare_eval_runs(
        [normalize_eval_result(_complete_raw(name="a", steps=1))],
        [normalize_eval_result(_complete_raw(name="a", steps=2))],
    )
    parsed = json.loads(to_json(cmp))
    assert parsed["fixtures"]["a"]["metrics"]["steps"]["status"] == "changed"


def test_nested_telemetry_remains_representable():
    raw = _complete_raw(
        router_token_savings={"total_savings_estimate": 12,
                              "per_step": [{"step": 1}]},
        context_telemetry=[{"react_step": 1, "tools_exposed": 4}],
    )
    rec = normalize_eval_result(raw)
    d = record_to_dict(rec)
    assert d["router_token_savings"]["total_savings_estimate"] == 12
    assert d["context_telemetry"][0]["tools_exposed"] == 4
    json.loads(to_json(rec))  # serializable


def test_unserializable_object_does_not_break_serialization():
    # An opaque object smuggled into an optional field must not raise.
    rec = normalize_eval_result(_complete_raw())
    rec.router_token_savings = object()
    text = to_json(rec)
    assert "router_token_savings" in text  # stored as a repr string


# =========================================================================== #
# PART 8 — Use the REAL evaluator result shape                                 #
# =========================================================================== #
class _StatefulStubAdapter:
    """Deterministic adapter mirroring the A7.3 stub (visible solid state)."""

    def __init__(self):
        self._calls = {}

    def get_tools(self):
        return [{"type": "function",
                 "function": {"name": "box", "parameters": {"type": "object"}}}]

    def get_state(self):
        return json.dumps([
            {"id": "box1", "label": "box1", "shape_type": "Solid",
             "visible": True}])

    def execute_command(self, name, **kwargs):
        n = self._calls.get(name, 0)
        self._calls[name] = n + 1
        return json.dumps({"success": True, "id": f"{name}1",
                           "volume": 1000.0})


def test_normalizes_real_evaluate_fixture_output():
    """Normalize a genuine evaluate_fixture() result (Part 8 contract)."""
    from scripts.run_evals import evaluate_fixture  # concurrent file, read-only

    fixture = {
        "id": "reporting_real_shape",
        "name": "Reporting Real Shape",
        "prompt": "Create a 10x10x10 mm box.",
        "expected_tools_called": ["box"],
        "neutral_assertions": [
            {"type": "tool_error", "tool": "box", "expect_failure": False}
        ],
        "scripted_responses": [
            [[["box", {"id": "box1", "length": 10, "width": 10,
                       "height": 10}]], None],
            [None, "Created the box."],
        ],
    }
    real = evaluate_fixture(
        fixture, _StatefulStubAdapter())  # type: ignore[arg-type]
    # Sanity: the runner's real keys are present.
    for key in ("name", "passed", "expected", "got", "tools_passed",
                "assertions_passed", "failed_assertions",
                "termination_reason", "failure_category", "trace"):
        assert key in real

    rec = normalize_eval_result(real)
    assert rec.fixture == "Reporting Real Shape"
    assert rec.passed is True
    assert rec.expected_tools == ["box"]
    assert rec.actual_tools is not None
    assert "box" in [t.lower() for t in rec.actual_tools]
    assert rec.trace_available is True
    # Runner does not emit telemetry -> honest None, not zero.
    assert rec.total_tokens is None
    assert rec.steps is None

    # And the whole thing aggregates + serializes.
    report = build_report([real])
    assert json.loads(to_json(report))["aggregate"]["fixture_count"] == 1


if __name__ == "__main__":  # pragma: no cover
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
