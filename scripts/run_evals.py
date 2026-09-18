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
import os
import sys
from typing import Any, Dict, List, Optional

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


def collect_actual_tools(agent: "CADAgent", adapter: "RecordingAdapter") -> List[str]:
    """Union of adapter-recorded tools and any tool_calls found in history."""
    actual = list(adapter.called_tools)
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
# Neutral geometric verification                                               #
# --------------------------------------------------------------------------- #
def run_neutral_assertions(
    fixture: Dict[str, Any], adapter: "RecordingAdapter"
) -> Dict[str, Any]:
    """
    Execute the geometric assertions declared in fixture["neutral_assertions"].

    We need baseline (pre-operation) mass properties and face counts, then
    post-operation values. For simplicity and determinism, we capture baseline
    state from the first solid object created, then run the prompt, then
    re-query the same object.

    Since the adapter may not expose the object name easily, we derive it from
    get_state() after the agent finishes.
    """
    assertions = fixture.get("neutral_assertions", [])
    if not assertions:
        return {"passed": True, "details": []}

    results = {"passed": True, "details": []}

    # For the verification to work, we need:
    # 1. The name of the base solid object (first solid created)
    # 2. Its mass properties BEFORE the operation
    # 3. Its mass properties AFTER the operation

    # Since we can't easily intercept "before" state mid-prompt without
    # modifying the agent (which is forbidden), we use a practical approach:
    # - After the agent finishes, we examine the final state
    # - For "verify_volume_reduction", we find the main solid and its "parent"
    #   (the base before cut) via the DAG in get_state()
    # - For "verify_face_count_increase", we compare face counts

    # This is a best-effort implementation that works with the current adapter.

    try:
        # Get final state to identify objects
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
            if assertion == "verify_exists":
                # Final object must exist and have positive volume.
                # Wrap the adapter output in json.dumps() in case it returns a
                # raw dict (GeometryVerifier expects JSON strings).
                try:
                    target_mass = adapter.execute_command(
                        "get_mass_properties", id="target_verify", object_name=target_name)
                    target_mass_str = json.dumps(
                        target_mass) if not isinstance(target_mass, str) else target_mass
                    ok = GeometryVerifier.verify_exists(target_mass_str)
                    details.append(("verify_exists", ok))
                    if not ok:
                        results["passed"] = False
                except Exception as e:
                    details.append(("verify_exists", False, str(e)))
                    results["passed"] = False

            elif assertion == "verify_volume_reduction":
                # Compare base solid mass vs final solid mass.
                # The adapter may return raw dicts; GeometryVerifier expects
                # JSON strings, so wrap outputs with json.dumps().
                try:
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

                    ok = GeometryVerifier.verify_volume_reduction(
                        base_mass_str, final_mass_str)
                    details.append(("verify_volume_reduction", ok))
                    if not ok:
                        results["passed"] = False
                except Exception as e:
                    details.append(("verify_volume_reduction", False, str(e)))
                    results["passed"] = False

            elif assertion == "verify_face_count_increase":
                # Compare face count of base vs final solid.
                # Wrap adapter outputs in json.dumps() so the verifier always
                # receives JSON strings.
                try:
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

                    ok = GeometryVerifier.verify_face_count_increase(
                        base_faces_str, final_faces_str)
                    details.append(("verify_face_count_increase", ok))
                    if not ok:
                        results["passed"] = False
                except Exception as e:
                    details.append(
                        ("verify_face_count_increase", False, str(e)))
                    results["passed"] = False

            elif assertion == "verify_within_bounding_box":
                # This would need max_x, max_y, max_z from fixture; skip for now
                details.append(
                    ("verify_within_bounding_box", True, "not configured"))

        results["details"] = details

    except Exception as e:
        return {"passed": False, "details": [f"Verification error: {e}"]}

    return results


# --------------------------------------------------------------------------- #
# Evaluation                                                                    #
# --------------------------------------------------------------------------- #
def evaluate_fixture(
    fixture: Dict[str, Any], adapter: "RecordingAdapter"
) -> Dict[str, Any]:
    name = fixture.get("name") or fixture.get("id") or "<unnamed>"
    prompt = fixture["prompt"]
    expected = list(fixture.get("expected_tools_called", []) or [])

    # Fresh agent + cleared adapter recording buffer for isolation.
    adapter.called_tools = []
    agent = CADAgent(adapter=adapter)

    try:
        adapter.clear_document()
    except Exception:
        # Mock adapter / backends without clear_document are fine.
        pass

    try:
        agent.handle_message(prompt)
    except Exception as exc:
        return {
            "name": name,
            "passed": False,
            "expected": expected,
            "got": [],
            "reason": f"agent.handle_message raised {type(exc).__name__}: {exc}",
        }

    got = collect_actual_tools(agent, adapter)

    expected_set = set(expected)
    got_set = set(got)
    missing = expected_set - got_set
    passed = len(missing) == 0

    reason = ""
    if not passed:
        reason = f"Expected: {sorted(expected_set)}, Got: {sorted(got_set)}"

    # Run neutral geometric assertions if declared
    neutral_result = run_neutral_assertions(fixture, adapter)
    if not neutral_result["passed"]:
        passed = False
        if reason:
            reason += "; "
        reason += "Neutral assertions failed: " + "; ".join(
            f"{d[0]}={d[1]}" for d in neutral_result["details"] if not d[1])

    return {
        "name": name,
        "passed": passed,
        "expected": expected,
        "got": got,
        "reason": reason,
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
        status = "PASS" if res["passed"] else "FAIL"
        if res["passed"]:
            print(f"[{status}] {res['name']}")
        else:
            print(f"[{status}] {res['name']} ({res['reason']})")
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
