#!/usr/bin/env python
"""
PieCAD Automated Evaluation Runner (Industrial Benchmarking - Tool-Call Coverage)

Standalone headless test harness.

For each fixture in tests/eval_fixtures.json:
  * Instantiate a FRESH core.agent.CADAgent (CAD-agnostic orchestrator).
  * Drive the agent with fixture["prompt"] via agent.handle_message(...).
  * Capture the tools the agent ACTUALLY invoked (via the adapter's
    execute_command, which is the authoritative runtime source of truth).
    We ALSO inspect agent.history for any assistant messages carrying
    tool_calls / function_call, per the evaluation spec.
  * Compare the captured tool names against fixture["expected_tools_called"]
    (order-independent set coverage).
  * If fixture contains "neutral_assertions", run those geometric verifications
    against the final CAD state (volume reduction, face count increase, etc.).
  * Print a strict per-fixture PASS/FAIL line.
  * Print a final SCOREBOARD summary.

Run with:
    python scripts/run_evals.py

Design constraints (per task):
  * DO NOT modify core/agent.py, core/contracts/ir.py, or anything in adapters/.
    This script only CREATES scripts/run_evals.py and reads from the public
    adapter/agent APIs.
  * CADAgent keeps only user/assistant TEXT in agent.history and discards the
    ReAct scratchpad (where tool_calls live), so the reliable way to learn which
    tools were actually executed is to observe the adapter. We therefore wrap
    the adapter (or use a lightweight in-process mock adapter) to record every
    execute_command call. When a live FreeCAD bridge is available the real
    FreeCADAdapter is used; otherwise a minimal mock adapter keeps the runner
    fully standalone/headless.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Union

# Make the project root importable regardless of CWD.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Public, non-restricted imports only.
from core.agent import CADAgent  # noqa: E402
from core.adapters.interfaces import CADAdapter  # noqa: E402
from core.verification.checks import GeometryVerifier  # noqa: E402


# --------------------------------------------------------------------------- #
# Adapter selection: prefer the real FreeCAD adapter; fall back to a mock so   #
# the harness is runnable headlessly without a live CAD bridge.                #
# --------------------------------------------------------------------------- #
def _build_tool_recording_adapter() -> "RecordingAdapter":
    """Return a RecordingAdapter wrapping the best available backend.

    Uses the real FreeCADAdapter when a bridge is reachable (so the agent's
    tool calls actually execute against CAD and we record real invocations).
    Falls back to an in-process mock adapter otherwise, keeping the runner
    fully standalone.
    """
    try:
        import importlib

        active = os.getenv("ACTIVE_CAD_ADAPTER", "freecad")
        factory = {"freecad": "adapters.freecad.adapter.FreeCADAdapter"}
        module_path, class_name = factory[active].rsplit(".", 1)
        mod = importlib.import_module(module_path)
        AdapterCls = getattr(mod, class_name)
        backend = AdapterCls(port=int(os.getenv("CAD_BRIDGE_PORT", "9876")))
        # Probe connectivity so we don't silently run against a dead bridge.
        backend.get_state()
        print(f"[Adapter] Using live backend: {AdapterCls.__name__}")
        return RecordingAdapter(backend)
    except Exception as exc:  # pragma: no cover - environment dependent
        print(
            f"[Adapter] Live FreeCAD bridge unavailable ({exc!r}); "
            "using in-process mock adapter for tool-call coverage."
        )
        return RecordingAdapter(MockCADAdapter())


class RecordingAdapter(CADAdapter):
    """Decorator adapter that records every tool actually executed.

    This is NOT part of the adapters/ package on disk - it is an in-memory
    wrapper created only at runtime by this script, so it does not violate the
    'do not modify adapters/' rule.
    """

    def __init__(self, backend: CADAdapter):
        self._backend = backend
        self.called_tools: List[str] = []
        self._agent_trace: List[Dict[str, Any]] = []

    # CADAdapter interface ------------------------------------------------ #
    def get_tools(self) -> List[Dict[str, Any]]:
        return self._backend.get_tools()

    def execute_command(self, tool_name: str, **kwargs) -> str:
        # Authoritative record of a tool the agent actually invoked.
        self.called_tools.append(tool_name)
        return self._backend.execute_command(tool_name, **kwargs)

    def get_state(self) -> str:
        return self._backend.get_state()

    def clear_document(self) -> None:
        clr = getattr(self._backend, "clear_document", None)
        if callable(clr):
            clr()


class MockCADAdapter(CADAdapter):
    """Minimal in-process adapter so the runner is standalone/headless.

    It does not perform real CAD work; it simply echoes deterministic results
    so the agent's ReAct loop can progress and we can capture which tools the
    agent *intended* to call. Used only when no live CAD bridge is available.
    """

    def get_tools(self) -> List[Dict[str, Any]]:
        # Return a permissive no-op tool set so the agent can call anything.
        return []

    def execute_command(self, tool_name: str, **kwargs) -> str:
        return json.dumps({"ok": True, "tool": tool_name, "echo": kwargs})

    def get_state(self) -> str:
        return json.dumps([])


# --------------------------------------------------------------------------- #
# Fixture loading                                                              #
# --------------------------------------------------------------------------- #
def load_fixtures(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# Tool-call extraction                                                         #
# --------------------------------------------------------------------------- #
def extract_tools_from_history(history: List[Dict[str, Any]]) -> List[str]:
    """Inspect agent.history for assistant messages carrying tool/function calls.

    Per the evaluation spec we look at messages where role == "assistant" and a
    tool_calls or function_call was made. CADAgent mostly keeps text in history,
    but we honor the spec by scanning for any such structure (e.g. OpenAI-style
    tool_calls or legacy function_call).
    """
    tools: List[str] = []
    for msg in history:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue

        # OpenAI-style tool_calls list.
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            name = fn.get("name")
            if name:
                tools.append(name)

        # Legacy single function_call.
        fc = msg.get("function_call")
        if isinstance(fc, dict) and fc.get("name"):
            tools.append(fc["name"])
    return tools


def collect_actual_tools(
    called_tools: List[str], agent: "CADAgent"
) -> List[str]:
    """Merge the agent's session tools with any tool_calls found in history.

    ``called_tools`` is the authoritative list returned by
    ``agent.handle_message`` (the deprecated ``adapter.called_tools`` is no
    longer read).
    """
    actual = list(called_tools)
    actual.extend(extract_tools_from_history(
        getattr(agent, "history", []) or []))
    # De-duplicate while preserving first-seen order.
    seen = set()
    ordered: List[str] = []
    for t in actual:
        if t not in seen:
            seen.add(t)
            ordered.append(t)
    return ordered


# --------------------------------------------------------------------------- #
# Evaluation                                                                    #
# --------------------------------------------------------------------------- #
# Tolerance for floating-point geometric comparisons
GEOMETRY_TOLERANCE = 1e-6


def _match_tool_name(pattern: str, actual: str) -> bool:
    """Case-insensitive substring match for tool names."""
    return pattern.strip().lower() in actual.strip().lower()


def _check_tool_sequence(expected_seq: List[str], actual_seq: List[str]) -> tuple[bool, str]:
    """Check if actual tool sequence matches expected sequence (ordered).

    Returns (passed, reason).
    """
    if not expected_seq:
        return True, "no expected sequence specified"

    if len(actual_seq) < len(expected_seq):
        return False, f"actual sequence shorter than expected: got {len(actual_seq)}, expected at least {len(expected_seq)}"

    # Check if expected sequence appears as a subsequence in actual
    exp_idx = 0
    for actual_tool in actual_seq:
        if exp_idx < len(expected_seq) and _match_tool_name(expected_seq[exp_idx], actual_tool):
            exp_idx += 1

    if exp_idx == len(expected_seq):
        return True, "sequence matched"
    else:
        return False, f"expected sequence not found in actual: missing {expected_seq[exp_idx:]}"


def _check_argument_assertion(
    trace: List[Dict[str, Any]],
    assertion: Dict[str, Any]
) -> tuple[bool, str]:
    """Check a single argument assertion against the trace.

    Assertion format:
    {
        "tool": "fillet",           # tool name pattern (substring match)
        "step": 3,                  # optional: specific step number (1-indexed)
        "arg": "radius",            # argument name
        "op": "eq",                 # operator: eq, ne, lt, le, gt, ge, changed
        "value": 2.5,               # expected value (for eq, ne, lt, le, gt, ge)
        "from_step": 2,             # for "changed": compare to this step's arg value
        "tolerance": 1e-6           # optional tolerance for numeric comparison
    }

    Returns (passed, reason).
    """
    tool_pattern = assertion.get("tool", "")
    arg_name = assertion.get("arg", "")
    op = assertion.get("op", "eq")
    step_filter = assertion.get("step")
    tolerance = assertion.get("tolerance", GEOMETRY_TOLERANCE)

    # Find matching trace entries
    matches = []
    for entry in trace:
        if entry.get("type") != "tool_call":
            continue
        if not _match_tool_name(tool_pattern, entry.get("tool", "")):
            continue
        if step_filter is not None and entry.get("step") != step_filter:
            continue
        matches.append(entry)

    if not matches:
        return False, f"no matching tool call found for tool='{tool_pattern}', arg='{arg_name}'"

    # For "changed" operator, compare two different steps
    if op == "changed":
        from_step = assertion.get("from_step")
        if from_step is None:
            return False, "'changed' operator requires 'from_step'"

        # Find the from_step entry
        from_entry = None
        for entry in trace:
            if entry.get("type") == "tool_call" and entry.get("step") == from_step:
                if _match_tool_name(tool_pattern, entry.get("tool", "")):
                    from_entry = entry
                    break

        if not from_entry:
            return False, f"no matching tool call at from_step={from_step}"

        # Compare argument values across all matching entries
        from_val = from_entry.get("arguments", {}).get(arg_name)
        if from_val is None:
            return False, f"argument '{arg_name}' not found in from_step {from_step}"

        for entry in matches:
            val = entry.get("arguments", {}).get(arg_name)
            if val is None:
                continue
            if isinstance(val, (int, float)) and isinstance(from_val, (int, float)):
                if abs(val - from_val) > tolerance:
                    return True, f"argument '{arg_name}' changed from {from_val} to {val}"
            elif val != from_val:
                return True, f"argument '{arg_name}' changed from {from_val} to {val}"

        return False, f"argument '{arg_name}' did not change from step {from_step}"

    # For other operators, check the last matching entry (most recent)
    entry = matches[-1]
    actual_val = entry.get("arguments", {}).get(arg_name)

    if actual_val is None:
        return False, f"argument '{arg_name}' not found in tool call"

    expected_val = assertion.get("value")

    if op in ("eq", "ne"):
        if isinstance(actual_val, (int, float)) and isinstance(expected_val, (int, float)):
            is_equal = abs(actual_val - expected_val) <= tolerance
        else:
            is_equal = actual_val == expected_val

        if op == "eq":
            if is_equal:
                return True, f"argument '{arg_name}' == {expected_val}"
            return False, f"argument '{arg_name}' = {actual_val}, expected {expected_val}"
        else:  # ne
            if not is_equal:
                return True, f"argument '{arg_name}' != {expected_val}"
            return False, f"argument '{arg_name}' == {expected_val} (expected not equal)"

    elif op in ("lt", "le", "gt", "ge"):
        if not isinstance(actual_val, (int, float)) or not isinstance(expected_val, (int, float)):
            return False, f"comparison operator '{op}' requires numeric values"

        if op == "lt" and actual_val < expected_val:
            return True, f"argument '{arg_name}' ({actual_val}) < {expected_val}"
        elif op == "le" and actual_val <= expected_val + tolerance:
            return True, f"argument '{arg_name}' ({actual_val}) <= {expected_val}"
        elif op == "gt" and actual_val > expected_val:
            return True, f"argument '{arg_name}' ({actual_val}) > {expected_val}"
        elif op == "ge" and actual_val >= expected_val - tolerance:
            return True, f"argument '{arg_name}' ({actual_val}) >= {expected_val}"

        return False, f"argument '{arg_name}' ({actual_val}) {op} {expected_val} is false"

    return False, f"unknown operator: {op}"


def _check_error_assertion(
    trace: List[Dict[str, Any]],
    assertion: Dict[str, Any]
) -> tuple[bool, str]:
    """Check an error/rejection assertion against the trace.

    Assertion format:
    {
        "tool": "fillet",           # tool name pattern (substring match)
        "step": 3,                  # optional: specific step number
        "expect_failure": true,     # expect this tool to fail
        "error_contains": "radius"  # optional: error message should contain this
    }

    Returns (passed, reason).
    """
    tool_pattern = assertion.get("tool", "")
    step_filter = assertion.get("step")
    expect_failure = assertion.get("expect_failure", True)
    error_contains = assertion.get("error_contains")

    matches = []
    for entry in trace:
        # Support both agent trace format (has "tool" key) and legacy format (has "type": "tool_call")
        if "tool" not in entry and entry.get("type") != "tool_call":
            continue
        if not _match_tool_name(tool_pattern, entry.get("tool", "")):
            continue
        if step_filter is not None and entry.get("step") != step_filter:
            continue
        matches.append(entry)

    if not matches:
        return False, f"no matching tool call found for tool='{tool_pattern}'"

    entry = matches[-1]
    success = entry.get("success", True)
    error = entry.get("error", "")

    if expect_failure:
        if not success:
            if error_contains and error_contains.lower() not in (error or "").lower():
                return False, f"tool failed but error '{error}' does not contain '{error_contains}'"
            return True, f"tool failed as expected: {error}"
        else:
            return False, f"expected tool to fail but it succeeded"
    else:
        if success:
            return True, f"tool succeeded as expected"
        else:
            return False, f"expected tool to succeed but it failed: {error}"


def _check_retry_assertion(
    trace: List[Dict[str, Any]],
    assertion: Dict[str, Any]
) -> tuple[bool, str]:
    """Check a retry/self-healing assertion against the trace.

    Assertion format:
    {
        "tool": "fillet",           # tool name pattern
        "initial_failure": true,    # first attempt should fail
        "retry_success": true,      # subsequent attempt should succeed
        "arg_changed": "radius",    # argument that should change on retry
        "arg_change_op": "lt"       # how it should change (lt, gt, changed)
    }

    Returns (passed, reason).
    """
    tool_pattern = assertion.get("tool", "")
    initial_failure = assertion.get("initial_failure", True)
    retry_success = assertion.get("retry_success", True)
    arg_changed = assertion.get("arg_changed")
    arg_change_op = assertion.get("arg_change_op", "changed")

    # Find all matching tool calls in order
    matches = []
    for entry in trace:
        # Support both agent trace format (has "tool" key) and legacy format (has "type": "tool_call")
        if "tool" not in entry and entry.get("type") != "tool_call":
            continue
        if not _match_tool_name(tool_pattern, entry.get("tool", "")):
            continue
        matches.append(entry)

    if len(matches) < 2:
        return False, f"expected at least 2 calls to '{tool_pattern}', found {len(matches)}"

    first = matches[0]
    second = matches[1]

    # Check initial failure
    if initial_failure and first.get("success", True):
        return False, f"first call to '{tool_pattern}' succeeded but expected failure"

    # Check retry success
    if retry_success and not second.get("success", False):
        return False, f"retry call to '{tool_pattern}' failed but expected success"

    # Check argument change
    if arg_changed:
        first_val = first.get("arguments", {}).get(arg_changed)
        second_val = second.get("arguments", {}).get(arg_changed)

        if first_val is None or second_val is None:
            return False, f"argument '{arg_changed}' not found in both calls"

        if arg_change_op == "changed":
            if first_val == second_val:
                return False, f"argument '{arg_changed}' did not change: {first_val} -> {second_val}"
        elif arg_change_op == "lt":
            if not (isinstance(second_val, (int, float)) and isinstance(first_val, (int, float)) and second_val < first_val):
                return False, f"argument '{arg_changed}' did not decrease: {first_val} -> {second_val}"
        elif arg_change_op == "gt":
            if not (isinstance(second_val, (int, float)) and isinstance(first_val, (int, float)) and second_val > first_val):
                return False, f"argument '{arg_changed}' did not increase: {first_val} -> {second_val}"

        return True, f"retry pattern verified: {first_val} -> {second_val}"

    return True, "retry pattern verified"


def _check_geometry_assertion(
    fixture: Dict[str, Any],
    adapter: "RecordingAdapter",
    assertion: Dict[str, Any]
) -> tuple[bool, str]:
    """Check an exact geometric assertion against the final CAD state.

    Assertion format:
    {
        "type": "volume",           # volume, face_count, edge_count, bbox_x, bbox_y, bbox_z
        "op": "eq",                 # eq, approx, lt, le, gt, ge
        "value": 1000.0,            # expected value
        "tolerance": 1e-3,          # for approx
        "object": "last"            # "last", "first", or object name
    }

    Returns (passed, reason).
    """
    import json

    gtype = assertion.get("type", "")
    op = assertion.get("op", "approx")
    expected_val = assertion.get("value")
    tolerance = assertion.get("tolerance", GEOMETRY_TOLERANCE)
    obj_selector = assertion.get("object", "last")

    try:
        state_json = adapter.get_state()
        state = json.loads(state_json)
    except Exception as e:
        return False, f"failed to get CAD state: {e}"

    solids = [obj for obj in state if obj.get("shape_type") == "Solid"]
    visible_solids = [obj for obj in solids if obj.get("visible") is True]

    if not solids:
        return False, "no solid objects in state"

    if obj_selector == "first":
        target = solids[0]
    elif obj_selector == "last":
        target = visible_solids[-1] if visible_solids else solids[-1]
    else:
        # Find by name/id
        target = None
        for obj in solids:
            if obj.get("id") == obj_selector or obj.get("label") == obj_selector:
                target = obj
                break
        if target is None:
            return False, f"object '{obj_selector}' not found"

    target_name = target.get("id") or target.get("label") or ""

    if gtype in ("volume", "mass_properties"):
        mass_result = adapter.execute_command(
            "get_mass_properties", id="verify", object_name=target_name)
        mass_str = json.dumps(mass_result) if not isinstance(
            mass_result, str) else mass_result
        try:
            mass_data = json.loads(mass_str)
            actual_val = float(mass_data.get(
                "volume", mass_data.get("Volume", 0)))
        except Exception as e:
            return False, f"failed to parse mass properties: {e}"

    elif gtype == "face_count":
        faces_result = adapter.execute_command(
            "get_faces", id="verify", object_name=target_name)
        faces_str = json.dumps(faces_result) if not isinstance(
            faces_result, str) else faces_result
        try:
            faces_data = json.loads(faces_str)
            actual_val = len(faces_data) if isinstance(faces_data, list) else 0
        except Exception as e:
            return False, f"failed to parse faces: {e}"

    elif gtype == "edge_count":
        # Try to get edges
        edges_result = adapter.execute_command(
            "get_edges", id="verify", object_name=target_name)
        edges_str = json.dumps(edges_result) if not isinstance(
            edges_result, str) else edges_result
        try:
            edges_data = json.loads(edges_str)
            actual_val = len(edges_data) if isinstance(edges_data, list) else 0
        except Exception as e:
            return False, f"failed to parse edges: {e}"

    elif gtype in ("bbox_x", "bbox_y", "bbox_z"):
        mass_result = adapter.execute_command(
            "get_mass_properties", id="verify", object_name=target_name)
        mass_str = json.dumps(mass_result) if not isinstance(
            mass_result, str) else mass_result
        try:
            mass_data = json.loads(mass_str)
            bbox = mass_data.get("bounding_box", {})
            axis = gtype[-1].upper()  # x, y, z -> X, Y, Z
            min_val = float(bbox.get(f"{axis}Min", 0))
            max_val = float(bbox.get(f"{axis}Max", 0))
            actual_val = abs(max_val - min_val)
        except Exception as e:
            return False, f"failed to parse bounding box: {e}"

    else:
        return False, f"unknown geometry type: {gtype}"

    # Compare with tolerance
    if expected_val is None:
        return False, f"expected value is required for operator '{op}'"

    if op == "eq":
        if abs(actual_val - expected_val) <= tolerance:
            return True, f"{gtype} == {expected_val} (got {actual_val})"
        return False, f"{gtype} = {actual_val}, expected {expected_val} (±{tolerance})"

    elif op == "approx":
        if abs(actual_val - expected_val) <= tolerance:
            return True, f"{gtype} ≈ {expected_val} (got {actual_val})"
        return False, f"{gtype} = {actual_val}, expected ≈ {expected_val} (±{tolerance})"

    elif op == "lt":
        if actual_val < expected_val:
            return True, f"{gtype} ({actual_val}) < {expected_val}"
        return False, f"{gtype} ({actual_val}) >= {expected_val}"

    elif op == "le":
        if actual_val <= expected_val + tolerance:
            return True, f"{gtype} ({actual_val}) <= {expected_val}"
        return False, f"{gtype} ({actual_val}) > {expected_val}"

    elif op == "gt":
        if actual_val > expected_val:
            return True, f"{gtype} ({actual_val}) > {expected_val}"
        return False, f"{gtype} ({actual_val}) <= {expected_val}"

    elif op == "ge":
        if actual_val >= expected_val - tolerance:
            return True, f"{gtype} ({actual_val}) >= {expected_val}"
        return False, f"{gtype} ({actual_val}) < {expected_val}"

    return False, f"unknown operator: {op}"


def run_neutral_assertions(
    fixture: Dict[str, Any], adapter: "RecordingAdapter"
) -> Dict[str, Any]:
    """
    Execute the geometric assertions declared in fixture["neutral_assertions"].

    Supports both legacy string assertions and new structured assertions.
    Legacy: "verify_exists", "verify_volume_reduction", "verify_face_count_increase", "verify_within_bounding_box"
    New: structured dict assertions for exact geometry, tool sequences, arguments, errors, retries.
    """
    assertions = fixture.get("neutral_assertions", [])
    if not assertions:
        return {"passed": True, "details": []}

    results = {"passed": True, "details": []}

    try:
        # Get final state to identify objects (for legacy assertions)
        state_json = adapter.get_state()
        state = json.loads(state_json)

        # Neutral object selection (CAD-system-agnostic, no FreeCAD TypeIds):
        # - final_object: LAST object in the state array that is visible AND a Solid.
        # - base_object: FIRST object in the state array that is a Solid.
        solids = [obj for obj in state if obj.get("shape_type") == "Solid"]
        visible_solids = [obj for obj in solids if obj.get("visible") is True]
        final_obj = visible_solids[-1] if visible_solids else None
        base_obj = solids[0] if solids else None

        if not final_obj:
            return {"passed": False, "details": ["No visible solid object found in final state"]}

        target_name = final_obj.get("id") or final_obj.get("label") or ""
        base_name = ""
        if base_obj:
            base_name = base_obj.get("id") or base_obj.get("label") or ""

        if not target_name:
            return {"passed": False, "details": ["Could not determine target object name"]}

        details = []

        for assertion in assertions:
            try:
                # Legacy string assertions
                if isinstance(assertion, str):
                    if assertion == "verify_exists":
                        target_mass = adapter.execute_command(
                            "get_mass_properties", id="target_verify", object_name=target_name)
                        target_mass_str = json.dumps(
                            target_mass) if not isinstance(target_mass, str) else target_mass
                        ok, reason = GeometryVerifier.verify_exists(
                            target_mass_str)
                        details.append(("verify_exists", ok, reason))
                        if not ok:
                            results["passed"] = False

                    elif assertion == "verify_volume_reduction":
                        base_mass = None
                        if base_name and base_name != target_name:
                            base_mass = adapter.execute_command(
                                "get_mass_properties", id="base_verify", object_name=base_name)

                        final_mass = adapter.execute_command(
                            "get_mass_properties", id="target_verify", object_name=target_name)

                        base_mass_str = json.dumps(
                            base_mass) if not isinstance(base_mass, str) else base_mass
                        final_mass_str = json.dumps(
                            final_mass) if not isinstance(final_mass, str) else final_mass

                        ok, reason = GeometryVerifier.verify_volume_reduction(
                            base_mass_str, final_mass_str)
                        details.append(("verify_volume_reduction", ok, reason))
                        if not ok:
                            results["passed"] = False

                    elif assertion == "verify_face_count_increase":
                        base_faces = None
                        if base_name and base_name != target_name:
                            base_faces = adapter.execute_command(
                                "get_faces", id="base_faces", object_name=base_name)

                        final_faces = adapter.execute_command(
                            "get_faces", id="target_faces", object_name=target_name)

                        base_faces_str = json.dumps(
                            base_faces) if not isinstance(base_faces, str) else base_faces
                        final_faces_str = json.dumps(
                            final_faces) if not isinstance(final_faces, str) else final_faces

                        ok, reason = GeometryVerifier.verify_face_count_increase(
                            base_faces_str, final_faces_str)
                        details.append(
                            ("verify_face_count_increase", ok, reason))
                        if not ok:
                            results["passed"] = False

                    elif assertion == "verify_within_bounding_box":
                        details.append(
                            ("verify_within_bounding_box", True, "not configured"))

                    else:
                        details.append(
                            (assertion, False, f"unknown legacy assertion: {assertion}"))
                        results["passed"] = False

                # New structured assertions
                elif isinstance(assertion, dict):
                    atype = assertion.get("type", "")

                    if atype == "tool_sequence":
                        # Requires trace from agent
                        trace = getattr(adapter, "_agent_trace", [])
                        expected_seq = assertion.get("sequence", [])
                        ok, reason = _check_tool_sequence(expected_seq, [e.get(
                            "tool", "") for e in trace if e.get("type") == "tool_call"])
                        details.append(("tool_sequence", ok, reason))
                        if not ok:
                            results["passed"] = False

                    elif atype == "tool_argument":
                        trace = getattr(adapter, "_agent_trace", [])
                        ok, reason = _check_argument_assertion(
                            trace, assertion)
                        details.append(("tool_argument", ok, reason))
                        if not ok:
                            results["passed"] = False

                    elif atype == "tool_error":
                        trace = getattr(adapter, "_agent_trace", [])
                        ok, reason = _check_error_assertion(trace, assertion)
                        details.append(("tool_error", ok, reason))
                        if not ok:
                            results["passed"] = False

                    elif atype == "retry_pattern":
                        trace = getattr(adapter, "_agent_trace", [])
                        ok, reason = _check_retry_assertion(trace, assertion)
                        details.append(("retry_pattern", ok, reason))
                        if not ok:
                            results["passed"] = False

                    elif atype in ("volume", "face_count", "edge_count", "bbox_x", "bbox_y", "bbox_z", "mass_properties"):
                        ok, reason = _check_geometry_assertion(
                            fixture, adapter, assertion)
                        details.append((atype, ok, reason))
                        if not ok:
                            results["passed"] = False

                    else:
                        details.append(
                            (f"unknown_assertion:{atype}", False, f"unknown assertion type: {atype}"))
                        results["passed"] = False

                else:
                    details.append(
                        (str(assertion), False, "assertion must be string or dict"))
                    results["passed"] = False

            except Exception as e:
                detail = (str(assertion), False,
                          f"{e}\n{traceback.format_exc()}")
                details.append(detail)
                results["passed"] = False

        results["details"] = details

    except Exception as e:
        return {
            "passed": False,
            "details": [f"Verification error: {e}\n{traceback.format_exc()}"],
        }

    return results


def evaluate_fixture(
    fixture: Dict[str, Any], adapter: "RecordingAdapter"
) -> Dict[str, Any]:
    name = fixture.get("name") or fixture.get("id") or "<unnamed>"
    prompt = fixture["prompt"]
    expected = list(fixture.get("expected_tools_called", []) or [])
    expected_sequence = list(fixture.get("expected_tool_sequence", []) or [])

    # Fresh agent for isolation with trace capture enabled.
    agent = CADAgent(adapter=adapter, capture_trace=True)

    try:
        adapter.clear_document()
    except Exception:
        # Mock adapter / backends without clear_document are fine.
        pass

    try:
        # handle_message returns (response_text, session_tools); unpack the
        # tools the agent actually invoked across the whole session.
        final_response, called_tools = agent.handle_message(prompt)
    except Exception as exc:
        return {
            "name": name,
            "passed": False,
            "expected": expected,
            "got": [],
            "failure_category": "agent_execution_error",
            "reason": f"agent.handle_message raised {type(exc).__name__}: {exc}",
        }

    got = collect_actual_tools(called_tools, agent)

    # Capture trace from agent for structured assertions
    trace = agent.get_trace()
    # Store trace on adapter for access by assertion functions
    adapter._agent_trace = trace

    # --- STEP 1: success criteria ----------------------------------- #
    # 1. tools_passed: every expected tool must have been called (set coverage).
    expected_lower = {str(t).strip().lower() for t in expected if t}
    got_lower = {str(t).strip().lower() for t in got if t}
    missing = {e for e in expected_lower
               if not any(e in g for g in got_lower)}
    tools_passed = len(missing) == 0

    # 2. sequence_passed: if expected_tool_sequence is specified, verify order.
    sequence_passed = True
    sequence_reason = ""
    if expected_sequence:
        sequence_passed, sequence_reason = _check_tool_sequence(
            expected_sequence, got)

    # 3. assertions_passed: no failed neutral assertions (legacy + structured).
    neutral_result = run_neutral_assertions(fixture, adapter)
    failed_assertions = [
        d for d in neutral_result["details"] if not d[1]]
    assertions_passed = len(failed_assertions) == 0

    # 4. Determine termination reason from trace
    termination_reason = "normal"
    if trace:
        last_entry = trace[-1]
        if last_entry.get("type") == "max_steps_exhausted":
            termination_reason = "max_steps"
        elif last_entry.get("type") == "completion":
            termination_reason = "normal"
        elif last_entry.get("success") is False:
            termination_reason = "tool_execution_error"

    # 5. Overall pass: all criteria must pass
    passed = tools_passed and sequence_passed and assertions_passed

    # Determine failure category
    failure_category = None
    if not passed:
        if not tools_passed:
            failure_category = "tool_coverage"
        elif not sequence_passed:
            failure_category = "tool_sequence"
        elif not assertions_passed:
            failure_category = "assertion"
        elif termination_reason == "max_steps":
            failure_category = "max_steps_exhausted"
        elif termination_reason == "tool_execution_error":
            failure_category = "tool_execution_error"
        else:
            failure_category = "unknown"

    return {
        "name": name,
        "passed": passed,
        "expected": expected,
        "expected_sequence": expected_sequence,
        "got": got,
        "tools_passed": tools_passed,
        "sequence_passed": sequence_passed,
        "sequence_reason": sequence_reason,
        "assertions_passed": assertions_passed,
        "failed_assertions": failed_assertions,
        "termination_reason": termination_reason,
        "failure_category": failure_category,
        "trace": trace if trace else None,
    }


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #
def _parse_args() -> "argparse.Namespace":
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="PieCAD Automated Evaluation Runner"
    )
    parser.add_argument(
        "--test",
        type=str,
        default=None,
        metavar="NAME",
        help="Run ONLY the fixture whose id or name matches NAME "
             "(e.g. --test primitive_creation). Skips all other fixtures.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    fixtures_path = os.path.join(PROJECT_ROOT, "tests", "eval_fixtures.json")
    fixtures = load_fixtures(fixtures_path)

    # Single-fixture mode: filter to ONLY the requested fixture.
    if args.test:
        wanted = args.test.strip()
        filtered = [
            f for f in fixtures
            if wanted in (f.get("id"), f.get("name"))
        ]
        if not filtered:
            print(f"[ERROR] No fixture found matching '{wanted}'. "
                  f"Available: {[f.get('id') or f.get('name') for f in fixtures]}")
            return 1
        print(f"[--test] Running ONLY fixture matching '{wanted}' "
              f"({len(filtered)} fixture).")
        fixtures = filtered

    print(f"Loaded {len(fixtures)} fixtures from {fixtures_path}\n")

    # One shared recording adapter (re-wrapped per fixture for isolation).
    adapter = _build_tool_recording_adapter()

    results: List[Dict[str, Any]] = []
    for fixture in fixtures:
        # Only evaluate fixtures that declare expected_tools_called.
        if "expected_tools_called" not in fixture:
            print(
                f"[SKIP] {fixture.get('name', fixture.get('id'))} "
                "(no expected_tools_called field)"
            )
            continue

        res = evaluate_fixture(fixture, adapter)

        # STEP 2: dynamic failure logging - print EXACTLY what failed.
        tools_passed = res.get("tools_passed", res["passed"])
        assertions_passed = res.get("assertions_passed", res["passed"])
        failed_assertions = res.get("failed_assertions", [])

        if res["passed"]:
            print(f"[PASS] {res['name']}")
        else:
            reasons = []
            if not tools_passed:
                reasons.append(
                    f"Missing expected tools. Expected: {res['expected']}, "
                    f"Called: {res['got']}")
            if not assertions_passed:
                reasons.append(
                    f"Neutral assertions failed: {failed_assertions}")
            print(f"[FAIL] {res['name']} ({' | '.join(reasons)})")
        results.append(res)

    passing = sum(1 for r in results if r["passed"])
    total = len(results)

    print("\n" + "=" * 60)
    print("SCOREBOARD")
    print("=" * 60)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print(f"  [{status}] {r['name']}")
    print("-" * 60)
    print(f"SCOREBOARD: {passing}/{total} PASSING")

    return 0 if passing == total else 1


if __name__ == "__main__":
    sys.exit(main())
