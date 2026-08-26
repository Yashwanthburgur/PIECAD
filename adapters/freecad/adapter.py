"""FreeCAD Adapter implementing the generic CADAdapter interface via XML-RPC."""

import xmlrpc.client
from typing import Any, Dict
from core.adapters.interfaces import CADAdapter


class FreeCADAdapter(CADAdapter):
    def __init__(self, host: str = "127.0.0.1", port: int = 9875):
        self.url = f"http://{host}:{port}/"
        self._proxy = xmlrpc.client.ServerProxy(self.url, allow_none=True)
        self._is_connected = True

    def connect(self) -> bool:
        return True

    def execute_command(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        if tool_name == "create_box":
            length = float(parameters.get("length", 10.0))
            width = float(parameters.get("width", 10.0))
            height = float(parameters.get("height", 10.0))

            # Direct python execution through the active FreeCAD XML-RPC bridge
            py_code = f"""
import FreeCAD as App
import Part
doc = App.ActiveDocument
if not doc:
    doc = App.newDocument("PieCAD_Model")
box = doc.addObject("Part::Box", "Box")
box.Length = {length}
box.Width = {width}
box.Height = {height}
doc.recompute()
"""
            try:
                # 1. Try standard MCP/neka execute_python
                if hasattr(self._proxy, "execute_python"):
                    return self._proxy.execute_python(py_code)
                elif hasattr(self._proxy, "execute"):
                    return self._proxy.execute(py_code)
                elif hasattr(self._proxy, "create_box"):
                    return self._proxy.create_box(length, width, height)
                else:
                    # Fallback to direct call
                    return self._proxy.system.listMethods()
            except Exception as e:
                raise RuntimeError(f"FreeCAD bridge call failed: {str(e)}")

        raise NotImplementedError(f"Tool {tool_name} not implemented in FreeCADAdapter.")

    def get_state(self) -> Dict[str, Any]:
        return {"status": "connected"}