"""A7.5 — Evaluation telemetry + reporting layer (standalone).

This module is a *pure reporting/aggregation layer* that sits on top of the
existing evaluation system. It:

  * consumes the per-fixture result dictionaries emitted by
    ``scripts/run_evals.evaluate_fixture`` (and any telemetry-augmented variant
    of that shape),
  * normalizes them into a single, honest :class:`EvalRecord` representation,
  * aggregates them into descriptive (never qualitative) benchmark statistics,
  * structurally compares two evaluation runs, and
  * serializes records/reports/comparisons to JSON-safe structures.

Design constraints (A7.5):
  * It does **NOT** execute CAD, call an LLM, run evaluations, or modify the
    agent. It never imports ``core.agent`` or anything under ``adapters/``.
  * It uses only the Python standard library (no third-party dependencies).
  * Missing telemetry is represented explicitly as ``None`` (unknown /
    unavailable) so it is never conflated with a genuine ``0``.
  * Aggregation produces descriptive statistics only — no rankings, scores,
    winners/losers, recommendations, or qualitative judgments.

The module is deliberately independent of ``scripts/run_evals.py`` so the two
can evolve concurrently without file conflicts. Integration happens later by
feeding ``evaluate_fixture`` outputs into :func:`normalize_eval_result`.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

__all__ = [
    "EvalRecord",
    "normalize_eval_result",
    "aggregate_eval_results",
    "category_breakdown",
    "tool_coverage",
    "compare_eval_runs",
    "record_to_dict",
    "aggregate_to_dict",
    "comparison_to_dict",
    "to_json",
    "build_report",
]


# --------------------------------------------------------------------------- #
# Small, explicit absence-aware helpers.                                       #
# --------------------------------------------------------------------------- #
class _Missing:
    """Sentinel distinguishing "key absent" from "key present, value None"."""

    def __repr__(self): return "<MISSING>"  # noqa: E731


_MISSING = _Missing()

# Paths tried (in order) when reading a metric from a raw result mapping.
_STEPS_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("steps",),
    ("telemetry", "steps"),
)
_TOTAL_TOKENS_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("total_tokens",),
    ("telemetry", "total_tokens"),
    ("token_telemetry", "total_tokens"),
    ("telemetry", "token_telemetry", "total_tokens"),
)
_PROMPT_TOKENS_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("prompt_tokens",),
    ("total_input_tokens",),
    ("token_telemetry", "prompt_tokens"),
    ("token_telemetry", "total_input_tokens"),
    ("telemetry", "token_telemetry", "total_input_tokens"),
    ("telemetry", "token_telemetry", "prompt_tokens"),
)
_COMPLETION_TOKENS_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("completion_tokens",),
    ("total_output_tokens",),
    ("token_telemetry", "completion_tokens"),
    ("token_telemetry", "total_output_tokens"),
    ("telemetry", "token_telemetry", "total_output_tokens"),
    ("telemetry", "token_telemetry", "completion_tokens"),
)
_ROUTER_SAVINGS_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("router_token_savings",),
    ("telemetry", "router_token_savings"),
    ("token_telemetry", "router_token_savings"),
    ("telemetry", "token_telemetry", "router_token_savings"),
)
_CONTEXT_TELEMETRY_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("context_telemetry",),
    ("telemetry", "context_telemetry"),
)
_RPC_TRIPS_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("rpc_trips",),
    ("telemetry", "rpc_trips"),
)
_DURATION_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("duration_seconds",),
    ("telemetry", "duration_seconds"),
)

_TERMINATION_KEYS = ("termination_reason", "termination")
_FAILURE_CATEGORY_KEYS = ("failure_category", "failure")
_EXPECTED_TOOL_KEYS = ("expected_tools", "expected", "expected_tools_called")
_ACTUAL_TOOL_KEYS = ("actual_tools", "got", "called_tools")
_FAILED_ASSERTION_KEYS = ("failed_assertions",)


def _lookup(raw: Mapping[str, Any], path: Tuple[str, ...]) -> Any:
    """Return the value at ``path`` or :data:`_MISSING` if absent at any hop."""
    cur: Any = raw
    for key in path:
        if isinstance(cur, Mapping) and key in cur:
            cur = cur[key]
        else:
            return _MISSING
    return cur


def _first(raw: Mapping[str, Any], paths: Sequence[Tuple[str, ...]]) -> Any:
    """Return the first present value across ``paths`` (may legitimately be None)."""
    for path in paths:
        val = _lookup(raw, path)
        if val is not _MISSING:
            return val
    return None


def _first_key(raw: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """Return the first present value among flat ``keys``."""
    return _first(raw, tuple((k,) for k in keys))


def _as_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) else None


def _as_bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _as_number(value: Any) -> Optional[Union[int, float]]:
    # ``bool`` is a subclass of ``int`` — exclude it so True/False never
    # masquerades as a token/step count.
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _as_list(value: Any) -> Optional[List[Any]]:
    if isinstance(value, (list, tuple)):
        return list(value)
    return None


def _coerce_int(value: Any) -> Optional[int]:
    """Return an ``int`` for integral numeric input, else ``None``.

    Floats that are whole numbers (e.g. ``4.0``) are accepted; ``bool`` is
    rejected so ``True`` never becomes ``1``.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _is_json_safe(value: Any) -> bool:
    """True when ``value`` can be round-tripped through ``json.dumps`` unchanged."""
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# PART 1 — Normalized eval record.                                             #
# --------------------------------------------------------------------------- #
@dataclass
class EvalRecord:
    """A single, normalized evaluation result.

    Every optional telemetry field is ``None`` when it is unknown or
    unavailable, so callers can always distinguish "no data" from a real ``0``.
    """

    fixture: Optional[str] = None
    passed: Optional[bool] = None

    expected_tools: Optional[List[str]] = None
    actual_tools: Optional[List[str]] = None
    expected_sequence: Optional[List[str]] = None

    tools_passed: Optional[bool] = None
    sequence_passed: Optional[bool] = None
    assertions_passed: Optional[bool] = None
    failed_assertions: Optional[List[Any]] = None

    termination_reason: Optional[str] = None
    failure_category: Optional[str] = None

    steps: Optional[int] = None
    total_tokens: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    router_token_savings: Optional[Any] = None
    context_telemetry: Optional[Any] = None
    rpc_trips: Optional[int] = None
    duration_seconds: Optional[float] = None

    # Trace availability is tri-state: None = unknown (key absent),
    # False = explicitly absent/empty, True = present.
    trace_available: Optional[bool] = None
    # The raw trace is retained ONLY when it is already JSON-safe.
    trace: Optional[List[Any]] = None
    trace_serializable: Optional[bool] = None

    run_id: Optional[str] = None
    timestamp: Optional[str] = None


# --------------------------------------------------------------------------- #
# PART 2 — Normalization.                                                      #
# --------------------------------------------------------------------------- #
def normalize_eval_result(raw_result: Mapping[str, Any]) -> EvalRecord:
    """Normalize one raw result dict into an :class:`EvalRecord`.

    Tolerates the structures currently emitted by ``run_evals.evaluate_fixture``
    (success shape *and* the ``agent_execution_error`` shape) as well as any
    telemetry-augmented variant that nests metrics under ``telemetry`` /
    ``token_telemetry``. Missing fields become ``None``; nothing is fabricated.
    """
    if not isinstance(raw_result, Mapping):
        raise TypeError(
            f"normalize_eval_result expects a mapping, got {type(raw_result)!r}")

    # --- identity / headline outcome ---------------------------------- #
    fixture = (
        raw_result.get("name")
        or raw_result.get("fixture")
        or raw_result.get("fixture_name")
        or raw_result.get("id")
    )
    fixture = fixture if isinstance(fixture, str) else None

    passed = _as_bool(raw_result.get("passed"))

    expected_tools = _as_list(_first_key(raw_result, _EXPECTED_TOOL_KEYS))
    actual_tools = _as_list(_first_key(raw_result, _ACTUAL_TOOL_KEYS))
    expected_sequence = _as_list(raw_result.get("expected_sequence"))

    tools_passed = _as_bool(raw_result.get("tools_passed"))
    sequence_passed = _as_bool(raw_result.get("sequence_passed"))
    assertions_passed = _as_bool(raw_result.get("assertions_passed"))

    # failed_assertions: absent -> None (unknown); empty list -> genuine "none".
    fa = _first_key(raw_result, _FAILED_ASSERTION_KEYS)
    failed_assertions = _as_list(fa) if fa is not None else None
    # Support the nested shape emitted for assertion details, if ever present.
    if failed_assertions is None and isinstance(raw_result.get("neutral_assertions"), Mapping):
        na = raw_result["neutral_assertions"]
        if "details" in na:
            details = _as_list(na.get("details")) or []
            failed_assertions = [d for d in details if _detail_failed(d)]

    termination_reason = _as_str(_first_key(raw_result, _TERMINATION_KEYS))
    failure_category = _as_str(_first_key(raw_result, _FAILURE_CATEGORY_KEYS))

    # --- telemetry (may be absent) ------------------------------------ #
    steps = _as_number(_first(raw_result, _STEPS_PATHS))
    total_tokens = _as_number(_first(raw_result, _TOTAL_TOKENS_PATHS))
    prompt_tokens = _as_number(_first(raw_result, _PROMPT_TOKENS_PATHS))
    completion_tokens = _as_number(
        _first(raw_result, _COMPLETION_TOKENS_PATHS))

    router_savings = _first(raw_result, _ROUTER_SAVINGS_PATHS)
    context_telemetry = _first(raw_result, _CONTEXT_TELEMETRY_PATHS)
    rpc_trips = _as_number(_first(raw_result, _RPC_TRIPS_PATHS))
    duration_seconds = _as_number(_first(raw_result, _DURATION_PATHS))

    # --- trace availability (tri-state) ------------------------------- #
    if "trace" in raw_result:
        raw_trace = raw_result.get("trace")
        if raw_trace:
            trace_available: Optional[bool] = True
            trace_serializable: Optional[bool] = _is_json_safe(raw_trace)
            trace = list(raw_trace) if trace_serializable else None
        else:
            trace_available = False
            trace_serializable = None
            trace = None
    else:
        trace_available = None
        trace_serializable = None
        trace = None

    run_id = _as_str(raw_result.get("run_id"))
    timestamp = _as_str(raw_result.get("timestamp")
                        or raw_result.get("created_at"))

    return EvalRecord(
        fixture=fixture,
        passed=passed,
        expected_tools=expected_tools,
        actual_tools=actual_tools,
        expected_sequence=expected_sequence,
        tools_passed=tools_passed,
        sequence_passed=sequence_passed,
        assertions_passed=assertions_passed,
        failed_assertions=failed_assertions,
        termination_reason=termination_reason,
        failure_category=failure_category,
        steps=_coerce_int(steps),
        total_tokens=_coerce_int(total_tokens),
        prompt_tokens=_coerce_int(prompt_tokens),
        completion_tokens=_coerce_int(completion_tokens),
        router_token_savings=router_savings,
        context_telemetry=context_telemetry,
        rpc_trips=_coerce_int(rpc_trips),
        duration_seconds=duration_seconds,
        trace_available=trace_available,
        trace=trace,
        trace_serializable=trace_serializable,
        run_id=run_id,
        timestamp=timestamp,
    )


def _detail_failed(detail: Any) -> bool:
    """Best-effort: an assertion detail tuple ``(name, ok, reason)`` failed?"""
    if isinstance(detail, (list, tuple)) and len(detail) >= 2:
        return detail[1] is False
    return False


def _records(records: Iterable[Union[EvalRecord, Mapping[str, Any]]]) -> List[EvalRecord]:
    """Coerce an iterable of records/raw dicts into :class:`EvalRecord` objects."""
    out: List[EvalRecord] = []
    for item in records:
        if isinstance(item, EvalRecord):
            out.append(item)
        elif isinstance(item, Mapping):
            out.append(normalize_eval_result(item))
        else:
            raise TypeError(
                f"expected EvalRecord or mapping, got {type(item)!r}")
    return out


# --------------------------------------------------------------------------- #
# PART 3 / PART 4 — Aggregation + category breakdown (descriptive only).       #
# --------------------------------------------------------------------------- #
def _numeric_stats(value_iter: Iterable[Optional[Union[int, float]]]) -> Dict[str, Any]:
    """Descriptive stats that ignore unavailable (``None``) values entirely."""
    present: List[Union[int, float]] = []
    for v in value_iter:
        num = _as_number(v)
        if num is not None:
            present.append(num)
    if not present:
        return {
            "available_count": 0,
            "total": None,
            "average": None,
            "minimum": None,
            "maximum": None,
        }
    total = sum(present)
    return {
        "available_count": len(present),
        "total": total,
        "average": total / len(present),
        "minimum": min(present),
        "maximum": max(present),
    }


def tool_coverage(
    records: Iterable[Union[EvalRecord, Mapping[str, Any]]]
) -> Dict[str, Any]:
    """Neutral tool-coverage statistics across records.

    Counts expected vs actual tool occurrences and how many records have a known
    tool-coverage verdict. Never ranks fixtures.
    """
    recs = _records(records)
    expected_counter: Counter = Counter()
    actual_counter: Counter = Counter()
    with_expected = 0
    with_actual = 0
    tools_known = 0
    tools_ok = 0

    for r in recs:
        if r.expected_tools is not None:
            with_expected += 1
            expected_counter.update(str(t) for t in r.expected_tools)
        if r.actual_tools is not None:
            with_actual += 1
            actual_counter.update(str(t) for t in r.actual_tools)
        if r.tools_passed is not None:
            tools_known += 1
            if r.tools_passed:
                tools_ok += 1

    return {
        "records_with_expected_tools": with_expected,
        "records_with_actual_tools": with_actual,
        "records_tool_verdict_known": tools_known,
        "records_tools_passed": tools_ok,
        "tools_pass_percentage": (
            tools_ok / tools_known * 100.0 if tools_known else None
        ),
        "distinct_expected_tools": sorted(expected_counter),
        "distinct_actual_tools": sorted(actual_counter),
        "expected_tool_counts": dict(sorted(expected_counter.items())),
        "actual_tool_counts": dict(sorted(actual_counter.items())),
        "expected_tool_occurrences": int(sum(expected_counter.values())),
        "actual_tool_occurrences": int(sum(actual_counter.values())),
    }


def aggregate_eval_results(
    records: Iterable[Union[EvalRecord, Mapping[str, Any]]]
) -> Dict[str, Any]:
    """Aggregate records into descriptive statistics only.

    Averages/totals are computed *only* over records where the metric is
    available; missing values are never treated as zero.
    """
    recs = _records(records)

    known_pass = [r.passed for r in recs if r.passed is not None]
    passed_count = sum(1 for p in known_pass if p)
    failed_count = sum(1 for p in known_pass if not p)
    unknown_pass_count = len(recs) - len(known_pass)

    failure_counts: Counter = Counter()
    termination_counts: Counter = Counter()
    total_failed_assertions = 0
    records_with_failed_assertions = 0

    for r in recs:
        if r.failure_category is not None:
            failure_counts[r.failure_category] += 1
        if r.termination_reason is not None:
            termination_counts[r.termination_reason] += 1
        if r.failed_assertions is not None:
            total_failed_assertions += len(r.failed_assertions)
            if r.failed_assertions:
                records_with_failed_assertions += 1

    return {
        "fixture_count": len(recs),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "unknown_pass_count": unknown_pass_count,
        "pass_percentage": (
            passed_count / len(known_pass) * 100.0 if known_pass else None
        ),
        "steps": _numeric_stats(r.steps for r in recs),
        "total_tokens": _numeric_stats(r.total_tokens for r in recs),
        "prompt_tokens": _numeric_stats(r.prompt_tokens for r in recs),
        "completion_tokens": _numeric_stats(r.completion_tokens for r in recs),
        "rpc_trips": _numeric_stats(r.rpc_trips for r in recs),
        "duration_seconds": _numeric_stats(r.duration_seconds for r in recs),
        "failure_counts_by_category": dict(sorted(failure_counts.items())),
        "termination_reason_counts": dict(sorted(termination_counts.items())),
        "assertion_failures": {
            "total_failed_assertions": total_failed_assertions,
            "records_with_failed_assertions": records_with_failed_assertions,
        },
        "tool_coverage": tool_coverage(recs),
        # Honesty aid: how many records actually carried each telemetry field.
        "telemetry_availability": {
            "steps": sum(1 for r in recs if r.steps is not None),
            "total_tokens": sum(1 for r in recs if r.total_tokens is not None),
            "prompt_tokens": sum(1 for r in recs if r.prompt_tokens is not None),
            "completion_tokens": sum(
                1 for r in recs if r.completion_tokens is not None),
            "router_token_savings": sum(
                1 for r in recs if r.router_token_savings is not None),
            "context_telemetry": sum(
                1 for r in recs if r.context_telemetry is not None),
            "rpc_trips": sum(1 for r in recs if r.rpc_trips is not None),
            "duration_seconds": sum(
                1 for r in recs if r.duration_seconds is not None),
            "trace": sum(1 for r in recs if r.trace_available is True),
        },
    }


def _record_used_retries(record: EvalRecord) -> Optional[bool]:
    """True when trace shows a retry (attempt > 1); None when undeterminable."""
    if record.trace is None:
        return None
    for entry in record.trace:
        if isinstance(entry, Mapping):
            attempt = _as_number(entry.get("attempt"))
            if attempt is not None and attempt > 1:
                return True
    return False


def _record_used_sequence(record: EvalRecord) -> Optional[bool]:
    """True when a tool sequence was actually exercised (>1 distinct call)."""
    if record.expected_sequence:
        return True
    if record.actual_tools is None:
        return None
    return len(record.actual_tools) > 1


def category_breakdown(
    records: Iterable[Union[EvalRecord, Mapping[str, Any]]]
) -> Dict[str, Any]:
    """Neutral category aggregation (descriptive only, no ranking)."""
    recs = _records(records)

    failure_counts: Counter = Counter()
    termination_counts: Counter = Counter()
    with_assertion_failures = 0
    with_retries = 0
    retry_known = 0
    with_sequences = 0
    sequence_known = 0

    for r in recs:
        if r.failure_category is not None:
            failure_counts[r.failure_category] += 1
        if r.termination_reason is not None:
            termination_counts[r.termination_reason] += 1
        if r.failed_assertions:
            with_assertion_failures += 1

        retried = _record_used_retries(r)
        if retried is not None:
            retry_known += 1
            if retried:
                with_retries += 1

        used_seq = _record_used_sequence(r)
        if used_seq is not None:
            sequence_known += 1
            if used_seq:
                with_sequences += 1

    passed = sum(1 for r in recs if r.passed is True)
    failed = sum(1 for r in recs if r.passed is False)

    return {
        "passed": passed,
        "failed": failed,
        "unknown": len(recs) - passed - failed,
        "failure_categories": dict(sorted(failure_counts.items())),
        "termination_reasons": dict(sorted(termination_counts.items())),
        "fixtures_with_retries": with_retries,
        "retry_verdict_known": retry_known,
        "fixtures_with_sequences": with_sequences,
        "sequence_verdict_known": sequence_known,
        "fixtures_with_assertion_failures": with_assertion_failures,
    }


# --------------------------------------------------------------------------- #
# PART 5 — Reproducibility comparison.                                         #
# --------------------------------------------------------------------------- #
def _diff_values(a: Any, b: Any) -> Dict[str, Any]:
    """Structured diff for one field: identical / changed / unavailable."""
    if a is None and b is None:
        return {"status": "unavailable", "a": None, "b": None}
    if a == b:
        return {"status": "identical", "a": a, "b": b}
    return {"status": "changed", "a": a, "b": b}


_COMPARABLE_METRICS = (
    "steps",
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "rpc_trips",
    "duration_seconds",
    "router_token_savings",
    "context_telemetry",
)

_COMPARABLE_FLAGS = (
    "passed",
    "expected_tools",
    "actual_tools",
    "expected_sequence",
    "tools_passed",
    "sequence_passed",
    "assertions_passed",
    "failed_assertions",
    "termination_reason",
    "failure_category",
)


def compare_eval_runs(
    run_a: Iterable[Union[EvalRecord, Mapping[str, Any]]],
    run_b: Iterable[Union[EvalRecord, Mapping[str, Any]]],
) -> Dict[str, Any]:
    """Structurally compare two runs, keyed by fixture name.

    Returns per-field ``identical`` / ``changed`` / ``unavailable`` verdicts and
    a roll-up of identical vs changed fixtures. Deliberately avoids any vague
    "looks reproducible" judgement — the caller decides.
    """
    recs_a = _records(run_a)
    recs_b = _records(run_b)

    by_a: Dict[str, EvalRecord] = {}
    for r in recs_a:
        by_a.setdefault(r.fixture or f"<unnamed:{len(by_a)}>", r)
    by_b: Dict[str, EvalRecord] = {}
    for r in recs_b:
        by_b.setdefault(r.fixture or f"<unnamed:{len(by_b)}>", r)

    only_in_a = sorted(set(by_a) - set(by_b))
    only_in_b = sorted(set(by_b) - set(by_a))
    shared = sorted(set(by_a) & set(by_b))

    per_fixture: Dict[str, Any] = {}
    identical_fixtures: List[str] = []
    changed_fixtures: List[str] = []

    for name in shared:
        ra, rb = by_a[name], by_b[name]
        entry: Dict[str, Any] = {}

        for flag in _COMPARABLE_FLAGS:
            entry[flag] = _diff_values(getattr(ra, flag), getattr(rb, flag))

        # Tool-level set deltas (order-independent companion to the list diff).
        tools_a = ra.actual_tools
        tools_b = rb.actual_tools
        if tools_a is not None and tools_b is not None:
            entry["actual_tools_added"] = sorted(set(tools_b) - set(tools_a))
            entry["actual_tools_removed"] = sorted(set(tools_a) - set(tools_b))
        else:
            entry["actual_tools_added"] = None
            entry["actual_tools_removed"] = None

        entry["metrics"] = {
            m: _diff_values(getattr(ra, m), getattr(rb, m))
            for m in _COMPARABLE_METRICS
        }
        entry["trace_available"] = _diff_values(
            ra.trace_available, rb.trace_available)

        changed = any(
            v["status"] == "changed"
            for v in entry.values()
            if isinstance(v, Mapping) and "status" in v
        ) or any(
            v["status"] == "changed"
            for v in entry["metrics"].values()
        )
        entry["verdict"] = "changed" if changed else "identical"
        if changed:
            changed_fixtures.append(name)
        else:
            identical_fixtures.append(name)

        per_fixture[name] = entry

    return {
        "fixture_count_a": len(recs_a),
        "fixture_count_b": len(recs_b),
        "fixtures_only_in_a": only_in_a,
        "fixtures_only_in_b": only_in_b,
        "identical_fixtures": identical_fixtures,
        "changed_fixtures": changed_fixtures,
        "fixtures": per_fixture,
    }


# --------------------------------------------------------------------------- #
# PART 6 — Serialization (JSON-safe, stdlib-only).                             #
# --------------------------------------------------------------------------- #
def _json_safe(value: Any) -> Any:
    """Recursively coerce ``value`` into JSON-native structures.

    Uses a round-trip with ``default=str`` so unknown objects cannot raise; the
    result contains only ``None``/bool/int/float/str/list/dict.
    """
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return repr(value)


def record_to_dict(record: EvalRecord) -> Dict[str, Any]:
    """Serialize one :class:`EvalRecord` to a JSON-safe dict (None stays null)."""
    if not isinstance(record, EvalRecord):
        raise TypeError(f"expected EvalRecord, got {type(record)!r}")
    return _json_safe(asdict(record))


def aggregate_to_dict(report: Mapping[str, Any]) -> Dict[str, Any]:
    """Serialize an aggregate report to a JSON-safe dict."""
    return _json_safe(dict(report))


def comparison_to_dict(comparison: Mapping[str, Any]) -> Dict[str, Any]:
    """Serialize a run comparison to a JSON-safe dict."""
    return _json_safe(dict(comparison))


def to_json(obj: Any, indent: Optional[int] = None) -> str:
    """Serialize a record / aggregate / comparison (or nested dict) to JSON text."""
    if isinstance(obj, EvalRecord):
        obj = record_to_dict(obj)
    return json.dumps(_json_safe(obj), indent=indent, sort_keys=True)


# --------------------------------------------------------------------------- #
# Convenience: one-shot report builder.                                        #
# --------------------------------------------------------------------------- #
def build_report(
    raw_results: Iterable[Union[EvalRecord, Mapping[str, Any]]]
) -> Dict[str, Any]:
    """Normalize + aggregate + break down a set of raw results in one shot."""
    recs = _records(raw_results)
    return {
        "records": [record_to_dict(r) for r in recs],
        "aggregate": aggregate_eval_results(recs),
        "categories": category_breakdown(recs),
    }
