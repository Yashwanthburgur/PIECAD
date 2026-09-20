#!/usr/bin/env python
"""Real end-to-end LLM-driven CAD execution against live FreeCAD.

Connects CADAgent directly to the real FreeCADAdapter (XML-RPC bridge) and
drives four prompts: box, cylinder, box+hole, and an impossible fillet. All
evidence (trace, tool sequence, errors, retries, final response, state) is
written to an output file so it can be captured reliably.

Uses only the real adapter + real FreeCAD. No MockCADAdapter, no eval harness.
"""

import json
import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
adapters_path = PROJECT_ROOT / "adapters"
if str(adapters_path) not in sys.path:
    sys.path.insert(0, str(adapters_path))

from core.agent import CADAgent  # noqa: E402
from adapters.freecad.adapter import FreeCADAdapter  # noqa: E402

OUT = PROJECT_ROOT / "exports" / "live_agent_proof.txt"

PROMPTS = [
    "Create a 100 x 50 x 20 mm box.",
    "Create a cylinder of radius 10 mm and height 40 mm.",
    "Create a 100 x 50 x 20 mm box and subtract a 20 mm diameter hole through its center.",
    "Create a 10 x 10 x 10 mm box. Fillet its edge with a 100 mm radius.",
]


def probe_backend(adapter):
    """Fail loudly if the live FreeCAD/XML-RPC backend is unreachable."""
    try:
        state = adapter.get_state()
        return True, state
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def get_state_summary(adapter):
    try:
        state = adapter.get_state()
        parsed = json.loads(state) if isinstance(state, str) else state
        if isinstance(parsed, list):
            return [
                {
                    "id": o.get("id") or o.get("label"),
                    "type": o.get("type") or o.get("shape_type"),
                    "props": o.get("properties"),
                }
                for o in parsed
            ]
        return parsed
    except Exception as e:
        return f"<state error: {e}>"


def run_prompt(adapter, prompt, index):
    lines = []
    lines.append("=" * 70)
    lines.append(f"TEST {index}: prompt = {prompt!r}")
    lines.append("=" * 70)

    # Isolate: clear the document before each test.
    try:
        adapter.clear_document()
        lines.append("[SETUP] document cleared")
    except Exception as e:
        lines.append(f"[SETUP] clear_document failed (not fatal): {e}")

    agent = CADAgent(adapter=adapter, capture_trace=True)

    try:
        final_response, called_tools = agent.handle_message(prompt)
        lines.append(f"\n[RESULT] agent completed: True")
        lines.append(f"[RESULT] final_response: {final_response!r}")
        lines.append(f"[RESULT] session_tools: {called_tools}")
    except Exception as e:
        lines.append(
            f"\n[RESULT] agent raised exception: {type(e).__name__}: {e}")
        lines.append(traceback.format_exc())
        final_response = None
        called_tools = []

    # Ordered tool sequence from the trace.
    trace = agent.get_trace() or []
    tool_seq = [
        e.get("tool")
        for e in trace
        if e.get("tool") and "arguments" in e or e.get("type") == "tool_call"
    ]
    lines.append(f"\n[TRACE] ordered tool sequence: {tool_seq}")

    # Tool-by-tool detail with args + errors + retries.
    lines.append("\n[TRACE] per-tool detail:")
    for e in trace:
        if e.get("tool") and "arguments" in e:
            lines.append(
                f"  step={e.get('step')} tool={e.get('tool')} "
                f"args={e.get('arguments')} success={e.get('success')} "
                f"error={e.get('error')}"
            )
        elif e.get("type") == "completion":
            lines.append(
                f"  step={e.get('step')} type=completion reply={e.get('reply')!r}")
        elif e.get("type") == "max_steps_exhausted":
            lines.append(f"  step={e.get('step')} type=max_steps_exhausted")
        elif e.get("type") == "geometry_warning":
            lines.append(
                f"  step={e.get('step')} type=geometry_warning errors={e.get('errors')}")

    errors = [e.get("error") for e in trace if e.get("error")]
    retries = [e for e in trace if e.get("success") is False]
    lines.append(f"\n[ANALYSIS] tool execution errors: {errors}")
    lines.append(
        f"[ANALYSIS] failed-then-? entries (potential retries): {len(retries)}")

    lines.append(f"\n[STATE] final FreeCAD objects:")
    lines.append(json.dumps(get_state_summary(adapter), indent=2))

    return lines


def main():
    lines = []
    lines.append("=== REAL AGENTIC FREEOCAD EXECUTION PROOF ===\n")

    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    ok, probe = probe_backend(adapter)
    if not ok:
        lines.append(
            f"[FATAL] Live FreeCAD/XML-RPC backend unreachable: {probe}")
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"FATAL: {probe}")
        return 1

    lines.append(f"[BACKEND] reachable. initial state: {probe[:200]}")

    for i, prompt in enumerate(PROMPTS, start=1):
        try:
            lines.extend(run_prompt(adapter, prompt, i))
        except Exception as e:
            lines.append(f"\n[TEST {i}] unexpected harness error: {e}")
            lines.append(traceback.format_exc())

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Written to", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
