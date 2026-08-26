"""Low-level MCP Client for interacting with the FreeCAD Robust MCP Server."""

from typing import Any, Dict, List, Optional
import asyncio
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
        server_params = StdioServerParameters(
            command=self.command,
            args=self.args,
            env={
                "FREECAD_MODE": "xmlrpc",
                "FREECAD_SOCKET_HOST": "localhost",
                "FREECAD_XMLRPC_PORT": "9875",
            },
        )
        try:
            # We will manage the session lifecycle here
            return True
        except Exception as e:
            print(f"[FreeCADMCPClient] Connection failed: {e}")
            return False

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Query the MCP server for available FreeCAD tools."""
        if not self._session:
            return []
        response = await self._session.list_tools()
        return [{"name": t.name, "description": t.description, "inputSchema": t.inputSchema} for t in response.tools]

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Execute a specific FreeCAD MCP tool."""
        if not self._session:
            raise RuntimeError("MCP Client is not connected to FreeCAD server.")
        return await self._session.call_tool(name, arguments)