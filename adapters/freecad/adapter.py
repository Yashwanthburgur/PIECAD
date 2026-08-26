"""FreeCAD Adapter implementing the generic CADAdapter interface."""

from typing import Any, Dict, List
import asyncio
from core.adapters.interfaces import CADAdapter
from adapters.freecad.client import FreeCADMCPClient


class FreeCADAdapter(CADAdapter):
    def __init__(self):
        self.client = FreeCADMCPClient()
        self._is_connected = False

    def connect(self) -> bool:
        """Connects to the FreeCAD MCP server synchronously."""
        try:
            self._is_connected = asyncio.run(self.client.connect())
            return self._is_connected
        except Exception as e:
            print(f"[FreeCADAdapter] Failed to initialize connection: {e}")
            return False

    def execute_command(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        """Executes a tool on the FreeCAD MCP bridge."""
        if not self._is_connected:
            raise RuntimeError("FreeCAD Adapter is not connected.")
        return asyncio.run(self.client.call_tool(tool_name, parameters))

    def get_state(self) -> Dict[str, Any]:
        """Queries the active FreeCAD document for current objects and features."""
        if not self._is_connected:
            raise RuntimeError("FreeCAD Adapter is not connected.")
        # Fallback query to list active document objects via MCP
        return self.execute_command("get_active_document", {})