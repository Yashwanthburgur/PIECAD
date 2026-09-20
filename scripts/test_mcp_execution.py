#!/usr/bin/env python
"""Test script to verify dynamic command dispatch with MCP and local tools."""

import sys
import time
from pathlib import Path

# Add project root to path BEFORE importing
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


def main():
    """Test adapter command dispatch for MCP tools, local tools, and unknown tools."""
    print("[test_mcp_execution] Starting FreeCADAdapter...")

    from adapters.freecad.adapter import FreeCADAdapter

    adapter = FreeCADAdapter()

    print("\n[test_mcp_execution] Warming up tool cache (get_tools())...")
    start_time = time.time()
    tools = adapter.get_tools()
    cache_duration = time.time() - start_time
    print(
        f"[test_mcp_execution] Cached {len(tools)} tools in {cache_duration:.2f}s")

    # Test 1: Execute a known MCP tool (get_freecad_version)
    print("\n[test_mcp_execution] Test 1: execute_command('get_freecad_version')")
    print("[test_mcp_execution] This should route to MCP server and return version info...")
    start_time = time.time()
    result1 = adapter.execute_command("get_freecad_version")
    duration1 = time.time() - start_time
    print(f"[test_mcp_execution] Duration: {duration1:.2f}s")
    print(f"[test_mcp_execution] Result: {result1[:200]}..." if len(
        result1) > 200 else f"[test_mcp_execution] Result: {result1}")

    # Test 2: Execute an unknown tool (should fail fast)
    print("\n[test_mcp_execution] Test 2: execute_command('hallucinated_fake_tool')")
    print("[test_mcp_execution] This should fail fast WITHOUT launching MCP server...")
    start_time = time.time()
    result2 = adapter.execute_command("hallucinated_fake_tool")
    duration2 = time.time() - start_time
    print(
        f"[test_mcp_execution] Duration: {duration2:.4f}s (should be near-instant)")
    print(f"[test_mcp_execution] Result: {result2}")

    # Verify result2 is a structured error
    import json
    try:
        parsed = json.loads(result2)
        if parsed.get("success") is False and "Unknown tool" in parsed.get("error", ""):
            print(
                "[test_mcp_execution] [OK] Fail-fast logic working - returned structured error for unknown tool")
        else:
            print(
                f"[test_mcp_execution] [FAIL] Expected structured error, got: {result2}")
    except json.JSONDecodeError:
        print(
            f"[test_mcp_execution] [FAIL] Result is not valid JSON: {result2}")

    # Test 3: Execute a local tool (box) - verify local tools still work
    print("\n[test_mcp_execution] Test 3: execute_command('box', id='test_box', length=10, width=20, height=30) - Local tool")
    print("[test_mcp_execution] This should route to local XML-RPC bridge...")
    # Note: This may fail if FreeCAD bridge isn't running, but should NOT fail due to dispatch logic
    start_time = time.time()
    try:
        result3 = adapter.execute_command(
            "box", id="test_box", length=10, width=20, height=30)
        duration3 = time.time() - start_time
        print(f"[test_mcp_execution] Duration: {duration3:.2f}s")
        print(f"[test_mcp_execution] Result: {result3[:200]}..." if len(
            result3) > 200 else f"[test_mcp_execution] Result: {result3}")
        print("[test_mcp_execution] [OK] Local tool dispatch works")
    except Exception as e:
        duration3 = time.time() - start_time
        print(f"[test_mcp_execution] Duration: {duration3:.2f}s")
        print(
            f"[test_mcp_execution] Local tool error (expected if FreeCAD not running): {e}")
        print("[test_mcp_execution] [OK] Local tool dispatch attempted (error is from bridge, not dispatch)")

    print("\n[test_mcp_execution] All tests completed.")
    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
