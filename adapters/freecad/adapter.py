"""FreeCAD Adapter. Manages FreeCAD-specific schemas and execution."""
import xmlrpc.client
from typing import Any, Dict, List
from core.adapters.interfaces import CADAdapter

class FreeCADAdapter(CADAdapter):
    def __init__(self, host: str = "127.0.0.1", port: int = 9876):
        self.url = f"http://{host}:{port}/"
        self._proxy = xmlrpc.client.ServerProxy(self.url, allow_none=True)

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "create_box",
                    "description": "Create a 3D box solid in mm.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "length": {"type": "number", "description": "Length along X in mm"},
                            "width": {"type": "number", "description": "Width along Y in mm"},
                            "height": {"type": "number", "description": "Height along Z in mm"},
                        },
                        "required": ["length", "width", "height"],
                    },
                },
            }
        ]

    def execute_command(self, tool_name: str, parameters: Dict[str, Any]) -> str:
        if tool_name == "create_box":
            l = float(parameters.get("length", 10.0))
            w = float(parameters.get("width", 10.0))
            h = float(parameters.get("height", 10.0))
            return str(self._proxy.create_box(l, w, h))
        raise NotImplementedError(f"Tool {tool_name} not implemented.")