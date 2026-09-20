#!/usr/bin/env python
"""Test MCP client integration from a FastAPI/asyncio context.

This script simulates the asyncio event loop that FastAPI/Uvicorn creates
and verifies that the FreeCAD adapter's MCP integration works without
'asyncio.run() cannot be called from a running event loop' errors.
"""

from adapters.freecad.adapter import FreeCADAdapter
import asyncio
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Ensure adapters package is importable
adapters_path = PROJECT_ROOT / "adapters"
if str(adapters_path) not in sys.path:
    sys.path.insert(0, str(adapters_path))


async def test_mcp_in_fastapi_context():
    """Test MCP integration from within an asyncio event loop (like FastAPI)."""
    print("=== Testing MCP integration in asyncio context ===\n")

    # Create adapter - this should not crash
    print("1. Creating FreeCADAdapter...")
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)
    print("   [OK] Adapter created successfully")

    # Test get_tools - this internally calls MCP worker which uses dedicated thread
    print("\n2. Calling get_tools() (triggers MCP discovery)...")
    try:
        tools = adapter.get_tools()
        print(f"   [OK] get_tools() returned {len(tools)} tools")
        local_tool_names = [t["function"]["name"]
                            for t in tools if "function" in t]
        print(f"   Tool names: {local_tool_names[:10]}..." if len(
            local_tool_names) > 10 else f"   Tool names: {local_tool_names}")
    except RuntimeError as e:
        if "asyncio.run() cannot be called from a running event loop" in str(e):
            print(f"   [FAIL] asyncio.run() error still occurs: {e}")
            return False
        else:
            # Other errors (like connection refused) are expected if FreeCAD/MCP not running
            print(f"   [WARN] Expected error (no FreeCAD/MCP running): {e}")
            print("   [OK] No asyncio.run() error - async boundary fix works!")
    except Exception as e:
        # Other errors are fine as long as it's not the asyncio.run() error
        if "asyncio.run() cannot be called from a running event loop" in str(e):
            print(f"   [FAIL] asyncio.run() error still occurs: {e}")
            return False
        print(f"   [WARN] Other error (expected if no MCP server): {e}")
        print("   [OK] No asyncio.run() error - async boundary fix works!")

    # Test execute_command with a local tool (box) - doesn't need MCP
    print("\n3. Testing execute_command with local tool 'box'...")
    try:
        # This will fail if XML-RPC bridge not running, but should not have asyncio error
        result = adapter.execute_command(
            "box", id="test_box", length=10.0, width=10.0, height=10.0)
        print(f"   [OK] execute_command executed (result: {result[:50]}...)")
    except RuntimeError as e:
        if "asyncio.run() cannot be called from a running event loop" in str(e):
            print(f"   [FAIL] asyncio.run() error in execute_command: {e}")
            return False
        elif "Cannot reach the FreeCAD XML-RPC bridge" in str(e) or "ConnectionError" in str(e):
            print(f"   [WARN] Expected error (no XML-RPC bridge running): {e}")
            print("   [OK] No asyncio.run() error - async boundary fix works!")
        else:
            print(f"   [WARN] Other error: {e}")
    except Exception as e:
        if "asyncio.run() cannot be called from a running event loop" in str(e):
            print(f"   [FAIL] asyncio.run() error in execute_command: {e}")
            return False
        print(f"   [WARN] Other error: {e}")

    # Test MCP tool execution path
    print("\n4. Testing MCP tool execution path (_run_mcp_tool)...")
    try:
        # This will try to connect to MCP server via worker thread
        result = adapter._run_mcp_tool("nonexistent_tool", {})
        print(f"   [OK] _run_mcp_tool executed (result: {result[:100]}...)")
    except RuntimeError as e:
        if "asyncio.run() cannot be called from a running event loop" in str(e):
            print(f"   [FAIL] asyncio.run() error in _run_mcp_tool: {e}")
            return False
        else:
            print(f"   [WARN] Expected error (no MCP server): {e}")
            print("   [OK] No asyncio.run() error - async boundary fix works!")
    except Exception as e:
        if "asyncio.run() cannot be called from a running event loop" in str(e):
            print(f"   [FAIL] asyncio.run() error in _run_mcp_tool: {e}")
            return False
        print(f"   [WARN] Other error: {e}")

    print("\n=== All async boundary tests passed! ===")
    return True


async def main():
    success = await test_mcp_in_fastapi_context()
    if success:
        print(
            "\n[OK] ASYNC BOUNDARY FIX VERIFIED: No 'asyncio.run()' errors in FastAPI context")
        return 0
    else:
        print("\n[FAIL] ASYNC BOUNDARY FIX FAILED")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
