#!/usr/bin/env python
"""Test script to verify MCP client can connect and discover tools."""

import asyncio
import sys
from pathlib import Path

# Add project root to path BEFORE importing
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

print(f"[DEBUG] project_root = {project_root}")
print(f"[DEBUG] sys.path[0] = {sys.path[0]}")
print(f"[DEBUG] adapters exists = {(project_root / 'adapters').exists()}")
print(
    f"[DEBUG] adapters/freecad exists = {(project_root / 'adapters' / 'freecad').exists()}")
print(
    f"[DEBUG] adapters/freecad/client.py exists = {(project_root / 'adapters' / 'freecad' / 'client.py').exists()}")
print(
    f"[DEBUG] adapters/__init__.py exists = {(project_root / 'adapters' / '__init__.py').exists()}")


async def main():
    # Import inside main to ensure sys.path is already set
    from adapters.freecad.client import FreeCADMCPClient

    """Connect to MCP server, fetch tools, and print count."""
    print("[test_mcp_discovery] Starting FreeCADMCPClient...")

    client = FreeCADMCPClient()

    print("[test_mcp_discovery] Connecting to MCP server...")
    connected = await client.connect()

    if not connected:
        print("[test_mcp_discovery] FAILED: Could not connect to MCP server")
        return 1

    print("[test_mcp_discovery] Connected successfully. Fetching tools...")

    try:
        tools = await client.list_tools()
        tool_count = len(tools)
        print(f"[test_mcp_discovery] Discovered {tool_count} MCP tools")

        # Print tool names for verification
        for tool in tools:
            print(f"  - {tool['name']}: {tool['description'][:80]}...")

        await client.disconnect()
        print("[test_mcp_discovery] Disconnected cleanly.")
        return 0

    except Exception as e:
        print(f"[test_mcp_discovery] ERROR during tool discovery: {e}")
        import traceback
        traceback.print_exc()
        await client.disconnect()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
