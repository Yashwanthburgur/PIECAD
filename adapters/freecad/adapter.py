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
from core.contracts.ir import Box, Cylinder, Boolean, DeleteFeature, Hole, Sketch, Extrude, Fillet, Chamfer


class FreeCADAdapter(CADAdapter):
    """Concrete adapter for FreeCAD, driven through a synchronous XML-RPC bridge."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9876):
        self.url = f"http://{host}:{port}"
        self._proxy = xmlrpc.client.ServerProxy(self.url, allow_none=True)

    # ------------------------------------------------------------------ #
    # CADAdapter.get_tools() -> WHAT the agent may request.
    # ------------------------------------------------------------------ #
    def get_tools(self) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible function schemas for the 4 core operations."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "box",
                    "description": "Create a rectangular box solid. Dimensions are in millimeters.",
                    "parameters": Box.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "cylinder",
                    "description": "Create a cylindrical solid. Dimensions are in millimeters.",
                    "parameters": Cylinder.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "boolean",
                    "description": "Perform a boolean operation between two existing objects. Use 'subtract' to drill holes or remove material (Base minus Tool). Use 'union' to join them. Use 'intersect' to keep only the common volume.",
                    "parameters": Boolean.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "delete_feature",
                    "description": "Delete an existing feature/object from the CAD document.",
                    "parameters": DeleteFeature.model_json_schema(),
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_faces",
                    "description": "Query the B-rep faces of an existing object to use as references for sketches or holes. Returns face IDs, areas, and center of mass coordinates.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_name": {"type": "string", "description": "The ID of the object to query"}
                        },
                        "required": ["object_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "hole",
                    "description": "Create a hole by drilling into a face at a point. Automatically calculates depth for through-all if not specified.",
                    "parameters": Hole.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "sketch",
                    "description": "Create a 2D sketch on a face of an existing object. Use get_faces first to find the face_ref.",
                    "parameters": Sketch.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "extrude",
                    "description": "Extrude a sketch to create a solid (pad) or cut through material. Set is_cut=true for holes/cuts.",
                    "parameters": Extrude.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_edges",
                    "description": "Query the B-rep edges of an existing object to use as references for fillets. Returns edge IDs, lengths, and center of mass coordinates.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "object_name": {"type": "string", "description": "The ID of the object to query"}
                        },
                        "required": ["object_name"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "fillet",
                    "description": "Apply a fillet to a specific edge of an object. Use get_edges first to find the edge_ref.",
                    "parameters": Fillet.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "chamfer",
                    "description": "Apply a chamfer to specific edges of an object. Use get_edges first to find the edge_refs.",
                    "parameters": Chamfer.model_json_schema(),
                }
            },
        ]

    # ------------------------------------------------------------------ #
    # CADAdapter.execute_command() -> HOW the request is executed.
    # ------------------------------------------------------------------ #
    def execute_command(self, tool_name: str, **kwargs) -> str:
        """Route a structured tool call to the matching FreeCAD XML-RPC method.

        Each branch extracts and coerces its expected arguments, then calls the
        corresponding method on the bridge proxy. The bridge returns a
        human-readable confirmation string which is passed back to the agent.
        """
        # Sanitize tool name (e.g., 'cylinder.op' -> 'cylinder')
        tool_name = tool_name.split('.')[0]

        # Sanitize kwargs (e.g., {'cylinder.radius': 15} -> {'radius': 15})
        clean_kwargs = {}
        for k, v in kwargs.items():
            clean_key = k.split('.')[-1]
            clean_kwargs[clean_key] = v
        kwargs = clean_kwargs

        try:
            if tool_name == "box":
                # Extract parameters from IR kwargs (required fields guaranteed by schema)
                obj_id = kwargs["id"]
                length = float(kwargs["length"])
                width = float(kwargs["width"])
                height = float(kwargs["height"])
                origin = kwargs.get("origin", {"x": 0, "y": 0, "z": 0})

                # Create the box
                result = self._proxy.create_box(
                    length, width, height, str(obj_id))

                # Apply translation if origin is not (0,0,0)
                ox = float(origin.get("x", 0))
                oy = float(origin.get("y", 0))
                oz = float(origin.get("z", 0))
                if ox != 0 or oy != 0 or oz != 0:
                    self._proxy.translate(str(obj_id), ox, oy, oz)

                return str(result)

            if tool_name == "cylinder":
                # Extract parameters from IR kwargs (required fields guaranteed by schema)
                obj_id = kwargs["id"]
                radius = float(kwargs["radius"])
                height = float(kwargs["height"])
                origin = kwargs.get("origin", {"x": 0, "y": 0, "z": 0})

                # Create the cylinder
                result = self._proxy.create_cylinder(
                    radius, height, str(obj_id))

                # Apply translation if origin is not (0,0,0)
                ox = float(origin.get("x", 0))
                oy = float(origin.get("y", 0))
                oz = float(origin.get("z", 0))
                if ox != 0 or oy != 0 or oz != 0:
                    self._proxy.translate(str(obj_id), ox, oy, oz)

                return str(result)

            if tool_name == "boolean":
                # Extract parameters from IR kwargs (required fields guaranteed by schema)
                result_id = kwargs["id"]
                mode = kwargs["mode"]
                target_id = kwargs["target_id"]
                tool_id = kwargs["tool_id"]

                return str(
                    self._proxy.boolean(
                        str(mode),
                        str(target_id),
                        str(tool_id),
                        str(result_id),
                    )
                )

            if tool_name == "delete_feature":
                target_feature_id = kwargs["target_feature_id"]
                return str(
                    self._proxy.delete_object(str(target_feature_id))
                )

            if tool_name == "get_faces":
                object_name = kwargs["object_name"]
                faces = self._proxy.get_faces(str(object_name))
                import json
                return json.dumps(faces)

            if tool_name == "hole":
                obj_id = kwargs["id"]
                face_ref = kwargs["face_ref"]
                x = float(kwargs["x"])
                y = float(kwargs["y"])
                diameter = float(kwargs["diameter"])
                depth = kwargs.get("depth")
                depth_val = float(depth) if depth is not None else 100.0
                return str(
                    self._proxy.hole(
                        str(obj_id),
                        str(face_ref),
                        x,
                        y,
                        diameter,
                        depth_val,
                    )
                )

            if tool_name == "sketch":
                obj_id = kwargs["id"]
                face_ref = kwargs["face_ref"]
                shapes = kwargs["shapes"]
                return str(
                    self._proxy.sketch(
                        str(obj_id),
                        str(face_ref),
                        shapes,
                    )
                )

            if tool_name == "extrude":
                obj_id = kwargs["id"]
                sketch_id = kwargs["sketch_id"]
                depth = float(kwargs["depth"])
                is_cut = kwargs.get("is_cut", False)
                return str(
                    self._proxy.extrude(
                        str(obj_id),
                        str(sketch_id),
                        depth,
                        is_cut,
                    )
                )

            if tool_name == "get_edges":
                object_name = kwargs["object_name"]
                edges = self._proxy.get_edges(str(object_name))
                import json
                return json.dumps(edges)

            if tool_name == "fillet":
                obj_id = kwargs["id"]
                target_id = kwargs["target_id"]
                edge_refs = kwargs["edge_refs"]
                radius = float(kwargs["radius"])
                return str(
                    self._proxy.fillet(
                        str(obj_id),
                        str(target_id),
                        edge_refs,
                        radius,
                    )
                )

            if tool_name == "chamfer":
                obj_id = kwargs["id"]
                target_id = kwargs["target_id"]
                edge_refs = kwargs["edge_refs"]
                size = float(kwargs["size"])
                return str(
                    self._proxy.chamfer(
                        str(obj_id),
                        str(target_id),
                        edge_refs,
                        size,
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

    # ------------------------------------------------------------------ #
    # Backend API methods (not LLM tools)
    # ------------------------------------------------------------------ #
    def export_obj(self, filepath: str) -> str:
        """Exports the current visible CAD state to a .obj file."""
        return str(self._proxy.export_obj(filepath))
