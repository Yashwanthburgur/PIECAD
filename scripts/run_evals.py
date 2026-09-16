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
def main() -> int:
    fixtures_path = os.path.join(PROJECT_ROOT, "tests", "eval_fixtures.json")
    fixtures = load_fixtures(fixtures_path)
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
