#!/usr/bin/env python
"""Test script to verify FreeCADAdapter.get_tools() dynamic discovery and caching."""

import asyncio
import sys
import time
from pathlib import Path

# Add project root to path BEFORE importing
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


def main():
    """Test adapter tool discovery and caching."""
    print("[test_adapter_tools] Starting FreeCADAdapter...")

    from adapters.freecad.adapter import FreeCADAdapter

    adapter = FreeCADAdapter()

    print("[test_adapter_tools] First call to get_tools() (should fetch from MCP)...")
    start_time = time.time()
    tools_first = adapter.get_tools()
    first_duration = time.time() - start_time
    print(
        f"[test_adapter_tools] Discovered {len(tools_first)} tools in {first_duration:.2f}s")

    # Print first few tool names
    for tool in tools_first[:5]:
        print(f"  - {tool['function']['name']}")
    if len(tools_first) > 5:
        print(f"  ... and {len(tools_first) - 5} more")

    print("\n[test_adapter_tools] Second call to get_tools() (should use cache)...")
    start_time = time.time()
    tools_second = adapter.get_tools()
    second_duration = time.time() - start_time
    print(
        f"[test_adapter_tools] Returned {len(tools_second)} tools in {second_duration:.4f}s (cached)")

    # Verify cache worked
    if second_duration < 0.1:
        print("[test_adapter_tools] [OK] Cache working - second call was instant")
    else:
        print("[test_adapter_tools] [FAIL] Cache NOT working - second call was slow")

    # Verify same results
    if tools_first == tools_second:
        print("[test_adapter_tools] [OK] Both calls returned identical results")
    else:
        print("[test_adapter_tools] [FAIL] Results differ between calls")

    return 0


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
