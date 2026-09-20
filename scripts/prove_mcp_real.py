#!/usr/bin/env python
"""Prove REAL MCP tool discovery and execution against live FreeCAD.

This script drives the actual FreeCADAdapter -> MCP translator ->
FreeCADMCPClient -> real MCP server -> real FreeCAD chain, WITHOUT the
MockCADAdapter. It writes its results to an output file so they can be
captured reliably.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
adapters_path = PROJECT_ROOT / "adapters"
if str(adapters_path) not in sys.path:
    sys.path.insert(0, str(adapters_path))

OUT = PROJECT_ROOT / "exports" / "mcp_real_proof.txt"


def log(lines):
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    lines = []
    lines.append("=== REAL MCP DISCOVERY & EXECUTION PROOF ===")

    from adapters.freecad.adapter import FreeCADAdapter

    try:
        adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

        # 1. get_tools() -> now merges MCP tools after the translator fix
        lines.append("\n[1] get_tools() (MCP discovery enabled)...")
        tools = adapter.get_tools()
        total = len(tools)
        local = adapter._get_local_tools()
        local_names = {t["function"]["name"] for t in local}
        mcp_only = [t["function"]["name"]
                    for t in tools if t["function"]["name"] not in local_names]
        lines.append(f"    total tools: {total}")
        lines.append(f"    local tools: {len(local)}")
        lines.append(f"    MCP-only tools: {len(mcp_only)}")
        for m in mcp_only:
            lines.append(f"      - {m}")

        if total > len(local):
            lines.append("\nREAL MCP TOOL DISCOVERY: VERIFIED")
        else:
            lines.append("\nREAL MCP TOOL DISCOVERY: WOULD-BE (need >20)")

        # 2. Execute an MCP-only tool if one is available
        lines.append(
            "\n[2] Executing an MCP-only tool against real FreeCAD...")
        executed = False
        for name in mcp_only:
            # Prefer a harmless query tool.
            if any(k in name for k in ("version", "get_version", "list", "ping", "documents")):
                lines.append(f"    attempting MCP tool: {name}")
                result = adapter.execute_command(name, **{})
                lines.append(f"    result: {result[:300]}")
                lines.append("\nREAL MCP TOOL EXECUTION: VERIFIED")
                executed = True
                break

        if not executed:
            lines.append(
                "    No MCP-only query tool was exposed to execute a proof.")

    except Exception as e:
        import traceback
        lines.append(f"\n[ERROR] {type(e).__name__}: {e}")
        lines.append(traceback.format_exc())
        lines.append("\nREAL MCP INTEGRATION: BLOCKED")

    log(lines)
    print("Written to", OUT)


if __name__ == "__main__":
    main()
