"""FreeCAD Adapter. Maps generic PieCAD intent to FreeCAD operations via XML-RPC.

Architecture rule: "Core defines WHAT, Adapter defines HOW."

This adapter owns every FreeCAD-specific detail: it publishes OpenAI-compatible
tool schemas (WHAT the LLM may request) and translates each structured call into
a matching XML-RPC method name on a small, synchronous FreeCAD bridge (HOW it is
actually executed). Core never sees FreeCAD internals.
"""

from typing import Any, Dict, List

import xmlrpc.client

from core.adapters.interfaces import CADAdapter


def _num_schema(description: str) -> Dict[str, Any]:
    return {"type": "number", "description": description}


def _str_schema(description: str) -> Dict[str, Any]:
    return {"type": "string", "description": description}


class FreeCADAdapter(CADAdapter):
    """Concrete adapter for FreeCAD, driven through a synchronous XML-RPC bridge."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9876):
        self.url = f"http://{host}:{port}"
        self._proxy = xmlrpc.client.ServerProxy(self.url, allow_none=True)

    # ------------------------------------------------------------------ #
    # CADAdapter.get_tools() -> WHAT the agent may request.
    # ------------------------------------------------------------------ #
    def get_tools(self) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible function schemas for the 7 core operations."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "create_box",
                    "description": "Create a 3D box solid. Dimensions are in millimeters.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "length": _num_schema("Length along X in mm."),
                            "width": _num_schema("Width along Y in mm."),
                            "height": _num_schema("Height along Z in mm."),
                            "object_name": _str_schema("Name for the new box object. Default 'Box'."),
                        },
                        "required": ["length", "width", "height"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "create_cylinder",
                    "description": "Create a 3D cylinder solid. Dimensions are in millimeters.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "radius": _num_schema("Cylinder radius in mm."),
                            "height": _num_schema("Cylinder height in mm."),
                            "object_name": _str_schema("Name for the new cylinder object. Default 'Cylinder'."),
                        },
                        "required": ["radius", "height"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "boolean_cut",
                    "description": "Subtract one solid (tool) from another (base) using a boolean cut.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "base_name": _str_schema("Name of the base solid to cut from."),
                            "tool_name": _str_schema("Name of the tool solid that is subtracted."),
                            "result_name": _str_schema("Name for the resulting cut object. Default 'Cut'."),
                        },
                        "required": ["base_name", "tool_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_param",
                    "description": "Change a single named property of an object, e.g. 'Length', 'Width', 'Height' or 'Radius'.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_name": _str_schema("Name of the object to modify."),
                            "param_name": _str_schema("Property name to change (e.g. 'Length')."),
                            "value": {"type": "number", "description": "New numeric value in mm."},
                        },
                        "required": ["object_name", "param_name", "value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "fillet_edges",
                    "description": "Apply a fillet of the given radius to all valid edges of a solid (falls back to default edges if none are specified). Radius is in millimeters.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_name": _str_schema("Name of the solid to fillet."),
                            "radius": _num_schema("Fillet radius in mm."),
                        },
                        "required": ["object_name", "radius"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "translate",
                    "description": "Translate an object by (x, y, z) coordinates before boolean operations.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_name": _str_schema("Name of the object to translate."),
                            "x": _num_schema("Translation in mm along X axis."),
                            "y": _num_schema("Translation in mm along Y axis."),
                            "z": _num_schema("Translation in mm along Z axis."),
                        },
                        "required": ["object_name", "x", "y", "z"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_object",
                    "description": "Deletes an object from the CAD document. Use this when the user asks to undo, remove, or delete geometry.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_name": _str_schema("Name of the object to delete."),
                        },
                        "required": ["object_name"],
                    },
                },
            },
        ]

    # ------------------------------------------------------------------ #
    # CADAdapter.execute_command() -> HOW the request is executed.
    # ------------------------------------------------------------------ #
    def execute_command(self, tool_name: str, parameters: Dict[str, Any]) -> str:
        """Route a structured tool call to the matching FreeCAD XML-RPC method.

        Each branch extracts and coerces its expected arguments, then calls the
        corresponding method on the bridge proxy. The bridge returns a
        human-readable confirmation string which is passed back to the agent.
        """
        try:
            if tool_name == "create_box":
                return str(
                    self._proxy.create_box(
                        float(parameters["length"]),
                        float(parameters["width"]),
                        float(parameters["height"]),
                        str(parameters.get("object_name", "Box")),
                    )
                )

            if tool_name == "create_cylinder":
                return str(
                    self._proxy.create_cylinder(
                        float(parameters["radius"]),
                        float(parameters["height"]),
                        str(parameters.get("object_name", "Cylinder")),
                    )
                )

            if tool_name == "boolean_cut":
                return str(
                    self._proxy.boolean_cut(
                        str(parameters["base_name"]),
                        str(parameters["tool_name"]),
                        str(parameters.get("result_name", "Cut")),
                    )
                )

            if tool_name == "set_param":
                return str(
                    self._proxy.set_param(
                        str(parameters["object_name"]),
                        str(parameters["param_name"]),
                        float(parameters["value"]),
                    )
                )

            if tool_name == "fillet_edges":
                return str(
                    self._proxy.fillet_edges(
                        str(parameters["object_name"]),
                        float(parameters["radius"]),
                    )
                )

            if tool_name == "translate":
                return str(
                    self._proxy.translate(
                        str(parameters["object_name"]),
                        float(parameters["x"]),
                        float(parameters["y"]),
                        float(parameters["z"]),
                    )
                )

            if tool_name == "delete_object":
                return str(
                    self._proxy.delete_object(
                        str(parameters["object_name"]),
                    )
                )

            raise NotImplementedError(
                f"Tool '{tool_name}' is not supported by the FreeCAD adapter."
            )

        except KeyError as e:
            missing = e.args[0]
            raise RuntimeError(
                f"Missing required parameter '{missing}' for tool '{tool_name}'."
            ) from e
        except xmlrpc.client.ProtocolError as e:
            raise RuntimeError(
                f"FreeCAD XML-RPC error ({e.errcode} {e.errmsg}) while executing '{tool_name}'."
            ) from e
        except (ConnectionError, OSError) as e:
            raise RuntimeError(
                f"Cannot reach the FreeCAD XML-RPC bridge at {self.url}: {e}"
            ) from e
        except Exception as e:
            # Wrap any remaining bridge/agent error with tool context.
            if isinstance(e, (RuntimeError, NotImplementedError)):
                raise
            raise RuntimeError(
                f"Failed to execute '{tool_name}' via FreeCAD bridge: {e}"
            ) from e

    # ------------------------------------------------------------------ #
    # CADAdapter.get_state() -> Return a JSON string representing the current document objects.
    # ------------------------------------------------------------------ #
    def get_state(self) -> str:
        """Return a JSON string representing the current document objects.

        Calls the bridge's get_state method and returns the JSON result.
        Catches connection errors gracefully, returning "[]" if the bridge is not reachable.
        """
        try:
            return str(self._proxy.get_state())
        except (ConnectionError, OSError):
            return "[]"
        except Exception:
            return "[]"
