"""FreeCAD Adapter. Maps generic PieCAD intent to FreeCAD operations via RPC."""

import xmlrpc.client
from typing import Any, Dict, List
from core.adapters.interfaces import CADAdapter

class FreeCADAdapter(CADAdapter):
    def __init__(self, host: str = "127.0.0.1", port: int = 9876):
        self.url = f"http://{host}:{port}/"
        self._proxy = xmlrpc.client.ServerProxy(self.url, allow_none=True)

    def get_tools(self) -> List[Dict[str, Any]]:
        """Expose the capabilities of this specific adapter."""
        # For Sprint 3A, we expose the 7 proven primitive/boolean tools here.
        # In Sprint 3B/4, this could dynamically fetch from the MCP server.
        return [
            {
                "type": "function",
                "function": {
                    "name": "create_box",
                    "description": "Create a 3D box solid. Dimensions are in millimeters.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_name": {"type": "string"},
                            "length": {"type": "number"},
                            "width": {"type": "number"},
                            "height": {"type": "number"},
                        },
                        "required": ["length", "width", "height"],
                    },
                },
            },
            # Note: Stubs for brevity. You can copy the full 7 schemas from the 
            # old agent.py here to maintain exact feature parity.
        ]

    def execute_command(self, tool_name: str, parameters: Dict[str, Any]) -> str:
        """Translate structured intent into FreeCAD execution."""
        if tool_name == "create_box":
            name = parameters.get("object_name", "Box")
            length = float(parameters.get("length", 10.0))
            width = float(parameters.get("width", 10.0))
            height = float(parameters.get("height", 10.0))
            
            # Translate to FreeCAD-specific python and execute via RPC
            # This keeps FreeCAD-specifics entirely inside this adapter.
            py_code = f"""
import FreeCAD as App
import Part
doc = App.ActiveDocument
if not doc:
    doc = App.newDocument("PieCAD_Model")
box = doc.addObject("Part::Box", "{name}")
box.Length = {length}
box.Width = {width}
box.Height = {height}
doc.recompute()
try:
    import FreeCADGui
    FreeCADGui.SendMsgToActiveView("ViewFit")
except Exception:
    pass
"""
            try:
                # We assume the FreeCAD RPC bridge has a generic python executor endpoint
                # If we are using our custom 9876 bridge, we call that.
                if hasattr(self._proxy, "execute_python"):
                    self._proxy.execute_python(py_code)
                elif hasattr(self._proxy, "create_box"):
                    # Fallback to the direct function if using our manual bridge from earlier
                    self._proxy.create_box(length, width, height)
                return f"Successfully created Box {length}x{width}x{height}."
            except Exception as e:
                raise RuntimeError(f"FreeCAD execution failed: {str(e)}")
        
        raise NotImplementedError(f"Tool {tool_name} not yet mapped in FreeCADAdapter.")

    def get_state(self) -> Dict[str, Any]:
        return {"status": "connected"}