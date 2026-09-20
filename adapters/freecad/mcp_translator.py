"""MCP to OpenAI Schema Translator.

This module provides utilities to translate MCP tool schemas into
OpenAI function calling format.
"""

from typing import Any, Dict, Optional


def _extract_name(mcp_tool) -> str:
    """Extract the tool name from dict or MCP Tool object."""
    if isinstance(mcp_tool, dict):
        return str(mcp_tool.get("name", ""))
    return str(getattr(mcp_tool, "name", ""))


def _extract_description(mcp_tool) -> str:
    """Extract the tool description from dict or MCP Tool object."""
    if isinstance(mcp_tool, dict):
        return str(mcp_tool.get("description", "") or "")
    return str(getattr(mcp_tool, "description", "") or "")


def _extract_input_schema(mcp_tool) -> Dict[str, Any]:
    """Extract the input schema from a dict or MCP Tool object.

    The installed MCP SDK (>=1.25.0) exposes the input schema under the
    snake_case attribute ``input_schema`` (a ``dict[str, Any]``). Older
    revisions/custom clients sometimes use camelCase ``inputSchema`` or expose
    an ``inputSchema`` dict. We probe several possible sources and return the
    first that is a dict.

    Args:
        mcp_tool: An MCP tool (dict or object).

    Returns:
        The input schema dict, or an empty dict if none is available.
    """
    candidates = []

    if isinstance(mcp_tool, dict):
        candidates.extend(
            [
                mcp_tool.get("input_schema"),
                mcp_tool.get("inputSchema"),
                mcp_tool.get("input_schema", {}).get("json_schema"),
                mcp_tool.get("inputSchema", {}).get("json_schema"),
                mcp_tool.get("input_schema", {}).get("schema"),
                mcp_tool.get("inputSchema", {}).get("schema"),
            ]
        )
        # Some adapters also expose the schema on a nested "function" object.
        fn = mcp_tool.get("function")
        if isinstance(fn, dict):
            candidates.extend(
                [
                    fn.get("parameters"),
                    fn.get("input_schema"),
                    fn.get("inputSchema"),
                ]
            )
        # Or as {"schema": {...}} nesting
        if isinstance(mcp_tool.get("input_schema"), dict):
            candidates.append(
                mcp_tool.get("input_schema", {}).get("input_schema"))
        if isinstance(mcp_tool.get("inputSchema"), dict):
            candidates.append(
                mcp_tool.get("inputSchema", {}).get("input_schema"))
    else:
        candidates.extend(
            [
                getattr(mcp_tool, "input_schema", None),
                getattr(mcp_tool, "inputSchema", None),
                getattr(getattr(mcp_tool, "input_schema", None),
                        "json_schema", None),
                getattr(getattr(mcp_tool, "inputSchema", None),
                        "json_schema", None),
                getattr(getattr(mcp_tool, "input_schema", None), "schema", None),
                getattr(getattr(mcp_tool, "inputSchema", None), "schema", None),
            ]
        )

    # A pydantic model dump gives a dict; if candidates are pydantic models,
    # convert via model_dump().
    for cand in candidates:
        if cand is None:
            continue
        # Convert pydantic models / JsonSchemaValue to plain dict
        if not isinstance(cand, dict):
            dump = getattr(cand, "model_dump", None)
            if callable(dump):
                try:
                    cand = dump()
                except Exception:
                    continue
            elif hasattr(cand, "dict"):
                try:
                    cand = cand.dict()
                except Exception:
                    continue
        if isinstance(cand, dict):
            # MCP 2025-06-18 "Tool.input_schema" is a bare JSON-Schema dict.
            # Guard against wrapped {"json_schema": {...}} / {"schema": {...}}.
            if "json_schema" in cand and isinstance(cand.get("json_schema"), dict):
                return cand["json_schema"]
            if "schema" in cand and isinstance(cand.get("schema"), dict):
                return cand["schema"]
            return cand

    return {}


def translate_mcp_to_openai(mcp_tool) -> dict:
    """Translate an MCP tool into OpenAI function schema format.

    Args:
        mcp_tool: An MCP tool object (dict or object with name, description,
            and input schema).

    Returns:
        Dictionary matching OpenAI function schema:
        {
            "type": "function",
            "function": {
                "name": "...",
                "description": "...",
                "parameters": {...}
            }
        }
    """
    name = _extract_name(mcp_tool)
    description = _extract_description(mcp_tool)
    input_schema = _extract_input_schema(mcp_tool)

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": input_schema,
        },
    }


def translate_mcp_tools_to_openai(mcp_tools) -> list[dict]:
    """Translate a list of MCP tools to OpenAI function schemas.

    Args:
        mcp_tools: List of MCP tool objects (dict or mcp.types.Tool)

    Returns:
        List of OpenAI function schema dictionaries.
    """
    return [translate_mcp_to_openai(tool) for tool in mcp_tools]
