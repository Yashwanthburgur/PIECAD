#!/usr/bin/env python
"""Focused test for MCP -> OpenAI tool schema translation.

Verifies translate_mcp_to_openai() handles the real MCP SDK ``Tool`` object,
which exposes its input schema under the snake_case attribute ``input_schema``
(a ``dict[str, Any]`` JSON-Schema). Also verifies preservation of nested
properties, required fields, arrays, enums, and defaults.
"""

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
adapters_path = PROJECT_ROOT / "adapters"
if str(adapters_path) not in sys.path:
    sys.path.insert(0, str(adapters_path))

from mcp.types import Tool  # noqa: E402
from adapters.freecad.mcp_translator import (  # noqa: E402
    translate_mcp_to_openai,
    translate_mcp_tools_to_openai,
)


def run_pass(name, cond):
    tag = "[PASS]" if cond else "[FAIL]"
    print(f"   {tag} {name}")
    return cond


def main():
    results = []

    print("=== MCP Tool Translator Test ===\n")

    # 1. Real SDK Tool object with snake_case input_schema
    print("1. Real MCP SDK Tool object (input_schema, snake_case)...")
    input_schema = {
        "type": "object",
        "properties": {
            "radius": {"type": "number", "description": "Radius in mm", "minimum": 0.0},
            "origin": {
                "type": "object",
                "properties": {
                    "x": {"type": "number", "default": 0.0},
                    "y": {"type": "number", "default": 0.0},
                    "z": {"type": "number", "default": 0.0},
                },
            },
            "kind": {"type": "string", "enum": ["simple", "tapped", "counterbore"]},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["radius"],
    }
    tool = Tool(
        name="create_hole",
        description="Create a hole in a solid.",
        input_schema=input_schema,
    )
    translated = translate_mcp_to_openai(tool)
    results.append(run_pass("type is 'function'",
                   translated["type"] == "function"))
    results.append(run_pass("name preserved",
                   translated["function"]["name"] == "create_hole"))
    results.append(run_pass("description preserved",
                   translated["function"]["description"] == "Create a hole in a solid."))
    results.append(run_pass("parameters is dict", isinstance(
        translated["function"]["parameters"], dict)))
    results.append(run_pass("required preserved",
                   translated["function"]["parameters"]["required"] == ["radius"]))
    nested = translated["function"]["parameters"]["properties"]["origin"]["properties"]
    results.append(run_pass("nested properties preserved",
                   nested["x"]["default"] == 0.0))
    results.append(run_pass("enum preserved", translated["function"]["parameters"]["properties"]["kind"]["enum"] == [
                   "simple", "tapped", "counterbore"]))
    results.append(run_pass("array preserved",
                   translated["function"]["parameters"]["properties"]["tags"]["type"] == "array"))

    # 2. Dict-style tool (legacy inputSchema camelCase) still works
    print("\n2. Legacy dict-style tool (inputSchema camelCase)...")
    legacy = {
        "name": "legacy_tool",
        "description": "Legacy tool",
        "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}}},
    }
    legacy_translated = translate_mcp_to_openai(legacy)
    results.append(run_pass("dict tool translated",
                   legacy_translated["function"]["name"] == "legacy_tool"))
    results.append(run_pass("dict inputSchema used",
                   "a" in legacy_translated["function"]["parameters"]["properties"]))

    # 3. List translation
    print("\n3. translate_mcp_tools_to_openai (list)...")
    list_result = translate_mcp_tools_to_openai([tool, legacy])
    results.append(run_pass("list length", len(list_result) == 2))
    results.append(run_pass("both names", list_result[0]["function"]["name"] ==
                   "create_hole" and list_result[1]["function"]["name"] == "legacy_tool"))

    print()
    if all(results):
        print("ALL TRANSLATOR TESTS PASSED")
        return 0
    else:
        print(f"{results.count(False)} TEST(S) FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
