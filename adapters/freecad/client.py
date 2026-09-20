"""Low-level MCP Client for interacting with the FreeCAD Robust MCP Server."""

from typing import Any, Dict, List, Optional
import asyncio
import os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class FreeCADMCPClient:
    def __init__(self, command: str = "uv", args: Optional[List[str]] = None):
        self.command = command
        self.args = args or [
            "run",
            "--project",
            "adapters/freecad/mcp_server",
            "freecad-mcp",
        ]
        self._session: Optional[ClientSession] = None
        self._exit_stack = None

    async def connect(self) -> bool:
        """Establish STDIO transport connection to the FreeCAD MCP server."""
        import contextlib

        # Inherit system environment and set correct XML-RPC port (9876)
        env = os.environ.copy()
        env.update({
            "FREECAD_MODE": "xmlrpc",
            "FREECAD_XMLRPC_PORT": "9876",
            "MODE": "xmlrpc",
            "XMLRPC_PORT": "9876",
            "PYTHONUNBUFFERED": "1",
        })

        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env=env,
        )
        try:
            # Manage the transport/session lifecycle on an async exit stack so we
            # can cleanly shut down stdio when disconnecting.
            self._exit_stack = contextlib.AsyncExitStack()
            stdin, stdout = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            session = await self._exit_stack.enter_async_context(ClientSession(
                stdin, stdout
            ))
            await session.initialize()
            self._session = session
            return True
        except Exception as e:
            print(f"[FreeCADMCPClient] Connection failed: {e}")
            await self.disconnect()
            return False

    async def disconnect(self) -> None:
        """Close the STDIO transport and any open client session."""
        if self._exit_stack is not None:
            try:
                await self._exit_stack.aclose()
            except Exception as e:
                print(f"[FreeCADMCPClient] Disconnect error: {e}")
            self._exit_stack = None
        self._session = None

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Query the MCP server for available FreeCAD tools."""
        if not self._session:
            return []
        response = await self._session.list_tools()
        tools = []
        for t in response.tools:
            # The installed MCP SDK exposes input schema as `input_schema`
            # (snake_case, a JSON-Schema dict). Older SDK versions pull from
            # `inputSchema`. Support both.
            input_schema = getattr(t, "input_schema", None)
            if input_schema is None:
                input_schema = getattr(t, "inputSchema", None)
            # Normalize pydantic/dict schema to a plain dict.
            if not isinstance(input_schema, dict):
                dump = getattr(input_schema, "model_dump", None)
                if callable(dump):
                    try:
                        input_schema = dump()
                    except Exception:
                        input_schema = {}
                elif input_schema is not None and hasattr(input_schema, "dict"):
                    try:
                        input_schema = input_schema.dict()
                    except Exception:
                        input_schema = {}
            tools.append({
                "name": t.name,
                "description": t.description,
                "inputSchema": input_schema or {},
            })
        return tools

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Execute a specific FreeCAD MCP tool."""
        if not self._session:
            raise RuntimeError(
                "MCP Client is not connected to FreeCAD server.")
        return await self._session.call_tool(name, arguments)
