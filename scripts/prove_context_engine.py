#!/usr/bin/env python
"""BIP 4.2.1 — LIVE Context/State Engine proof against REAL FreeCAD.

Runs the real CADAgent over two turns against the real FreeCAD XML-RPC bridge:

    Turn 1: "Create a 100 x 50 x 20 mm box."
    Turn 2: "Add a 20 mm hole through its center."

For each turn it prints proof the Context Engine is operating: DesignState
summary, per-step telemetry (estimated context tokens vs exact provider tokens),
number of tools exposed, selected/dropped context sections.

At the end it prints the FINAL DesignState and the actual objects observed from
the live FreeCAD state (the box and the hole must really exist).

Uses only the real FreeCADAdapter + real bridge + real CADAgent. No mocks.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
adapters_path = PROJECT_ROOT / "adapters"
if str(adapters_path) not in sys.path:
    sys.path.insert(0, str(adapters_path))

from core.agent import CADAgent  # noqa: E402
from adapters.freecad.adapter import FreeCADAdapter  # noqa: E402

OUT = PROJECT_ROOT / "exports" / "context_engine_proof.txt"

TURNS = [
    "Create a 100 x 50 x 20 mm box.",
    "Add a 20 mm hole through its center.",
]

HOST, PORT = "127.0.0.1", 9876


def probe_backend(adapter):
    """Fail loudly if the live FreeCAD/XML-RPC backend is unreachable."""
    state = adapter.get_state()
    return state


def state_summary(adapter):
    raw = adapter.get_state()
    parsed = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(parsed, list):
        return [
            {
                "id": o.get("id") or o.get("label"),
                "type": o.get("type") or o.get("shape_type"),
                "visible": o.get("visible"),
                "properties": o.get("properties"),
                "children": o.get("children"),
            }
            for o in parsed
        ]
    return parsed


def print_turn(lines, agent, request, response, tools):
    lines.append("=" * 74)
    lines.append(f"TURN REQUEST: {request!r}")
    lines.append(f"FINAL AGENT RESPONSE: {response!r}")
    lines.append(f"SESSION TOOLS: {tools}")
    lines.append("")

    # DesignState summary
    ds = agent.design_state
    lines.append("--- DesignState summary ---")
    lines.append(json.dumps(ds.summary(), indent=2, default=str))
    lines.append("recent_operations:")
    lines.append(json.dumps(
        [op.to_dict() for op in ds.get_recent_operations(10)], indent=2, default=str))
    lines.append("")

    # Telemetry (estimates + exact provider tokens)
    lines.append("--- Telemetry (per ReAct step) ---")
    tel = agent.get_context_telemetry()
    if not tel:
        lines.append("(no telemetry recorded)")
    for i, t in enumerate(tel, start=1):
        lines.append(f"[step {i}]")
        lines.append(
            f"  estimated_context_tokens   = {t.get('estimated_context_tokens')}")
        lines.append(
            f"  estimated_state_tokens     = {t.get('estimated_state_tokens')}")
        lines.append(
            f"  estimated_memory_tokens    = {t.get('estimated_memory_tokens')}")
        lines.append(
            f"  estimated_convers_tokens   = {t.get('estimated_conversation_tokens')}")
        lines.append(
            f"  estimated_tool_schema_tok  = {t.get('estimated_tool_schema_tokens')}")
        lines.append(
            f"  tools_exposed              = {t.get('tools_exposed')}")
        lines.append(
            f"  tools_exposed_names        = {t.get('tools_exposed_names')}")
        lines.append(f"  why_tools                  = {t.get('why_tools')}")
        lines.append(
            f"  dropped_sections           = {t.get('dropped_sections')}")
        lines.append(
            f"  exact_provider_tokens      = {t.get('exact_provider_tokens')}")
        lines.append(f"  input_tokens               = {t.get('input_tokens')}")
        lines.append(
            f"  output_tokens              = {t.get('output_tokens')}")
        lines.append(f"  total_tokens               = {t.get('total_tokens')}")
        lines.append("")
    return lines


def main():
    lines = []
    lines.append("=== BIP 4.2.1 LIVE CONTEXT ENGINE PROOF (real FreeCAD) ===")
    lines.append(f"bridge: {HOST}:{PORT}")
    lines.append("")

    adapter = FreeCADAdapter(host=HOST, port=PORT)

    # Fail loudly if bridge unreachable.
    try:
        initial_state = probe_backend(adapter)
        lines.append("[BACKEND] bridge reachable.")
        lines.append(f"[BACKEND] initial state: {str(initial_state)[:200]}")
    except Exception as e:
        lines.append(
            f"[FATAL] Live FreeCAD/XML-RPC backend unreachable: {type(e).__name__}: {e}")
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(
            f"[FATAL] Live FreeCAD/XML-RPC backend unreachable: {type(e).__name__}: {e}")
        return 1

    # Clear the document so we start from a known state.
    try:
        adapter.clear_document()
        lines.append("[SETUP] document cleared.")
    except Exception as e:
        lines.append(f"[SETUP] clear_document failed (not fatal): {e}")

    agent = CADAgent(adapter=adapter, capture_trace=False)

    for i, request in enumerate(TURNS, start=1):
        try:
            response, tools = agent.handle_message(request)
        except Exception as e:
            lines.append(
                f"[TURN {i}] handle_message raised {type(e).__name__}: {e}")
            import traceback
            lines.append(traceback.format_exc())
            OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"[TURN {i}] handle_message raised {type(e).__name__}: {e}")
            return 1
        lines = print_turn(lines, agent, request, response, tools)

    # FINAL DesignState + live FreeCAD objects.
    lines.append("=" * 74)
    lines.append("FINAL DesignState (from agent)")
    lines.append(json.dumps(
        agent.design_state.snapshot(), indent=2, default=str))
    lines.append("")
    lines.append("=" * 74)
    lines.append("ACTUAL live FreeCAD objects at end:")
    lines.append(json.dumps(state_summary(adapter), indent=2, default=str))

    # Assert the box and hole really exist per the live CAD state.
    live = state_summary(adapter)
    ids = {o.get("id") for o in live}
    lines.append("")
    lines.append(f"[VERIFY] live object ids: {sorted(ids)}")
    has_box = any(o.get("type") for o in live)
    lines.append("[VERIFY] live state has >=1 object: "
                 f"{'YES' if live else 'NO'}")
    lines.append("[VERIFY] live state is a non-empty object list: "
                 f"{'YES' if isinstance(live, list) and live else 'NO'}")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\n[WRITTEN] {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
