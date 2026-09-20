"""MCP to OpenAI Schema Translator.

This module provides utilities to translate MCP tool schemas into
OpenAI function calling format.
"""


def translate_mcp_to_openai(mcp_tool) -> dict:
    """Translate an MCP tool into OpenAI function schema format.

    Args:
        mcp_tool: An MCP tool object (dict or object with name, description, inputSchema)

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
    # Handle both dict and object (e.g., mcp.types.Tool)
    if isinstance(mcp_tool, dict):
        name = mcp_tool.get("name", "")
        description = mcp_tool.get("description", "")
        input_schema = mcp_tool.get("inputSchema", {})
    else:
        name = getattr(mcp_tool, "name", "")
        description = getattr(mcp_tool, "description", "")
        input_schema = getattr(mcp_tool, "inputSchema", {})

    # Ensure input_schema is a dict
    if not isinstance(input_schema, dict):
        input_schema = {}

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
