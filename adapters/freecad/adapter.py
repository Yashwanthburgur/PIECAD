"""FreeCAD Adapter. Maps generic PieCAD intent to FreeCAD operations via XML-RPC.

Architecture rule: "Core defines WHAT, Adapter defines HOW."

This adapter owns every FreeCAD-specific detail: it publishes OpenAI-compatible
tool schemas (WHAT the LLM may request) and translates each structured call into
a matching XML-RPC method name on a small, synchronous FreeCAD bridge (HOW it is
actually executed). Core never sees FreeCAD internals.
"""

import json
import ast
import re
import asyncio
from pathlib import Path
from typing import Any, Dict, List

import xmlrpc.client

from core.adapters.interfaces import CADAdapter
from core.contracts.ir import Box, Cylinder, Boolean, DeleteFeature, Hole, Sketch, Extrude, Fillet, Chamfer, LinearPattern, CircularPattern, Shell, Mate, GetMassProperties, GetBOM, ExportModel, EditFeature, InterferenceCheck
from adapters.freecad.client import FreeCADMCPClient
from adapters.freecad.mcp_translator import translate_mcp_to_openai


def _parse_dict_arg(arg, default_val):
    if isinstance(arg, str):
        try:
            return ast.literal_eval(arg)
        except Exception:
            return default_val
    if isinstance(arg, dict):
        return arg
    return default_val


class FreeCADAdapter(CADAdapter):
    """Concrete adapter for FreeCAD, driven through a synchronous XML-RPC bridge."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9876):
        self.url = f"http://{host}:{port}"
        self._proxy = xmlrpc.client.ServerProxy(self.url, allow_none=True)
        # External-tool client (robust MCP server) used for tools that the core
        # XML-RPC bridge does not implement (e.g. sketch constraints).
        self.mcp_client = FreeCADMCPClient()
        # Tool cache to avoid repeated MCP server launches
        self._cached_tools: List[Dict[str, Any]] | None = None

    # ------------------------------------------------------------------ #
    # External MCP tool execution (synchronous wrapper over the async client)
    # ------------------------------------------------------------------ #
    def _run_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Execute a tool through the external MCP server client.

        Connects the client on demand, delegates to ``mcp_client.call_tool``,
        and returns a JSON/serialized string result for the agent.
        """
        import asyncio
        return asyncio.run(self._async_mcp_tool(tool_name, arguments))

    async def _async_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        if self.mcp_client._session is None:
            connected = await self.mcp_client.connect()
            if not connected:
                raise RuntimeError(
                    f"Cannot connect to FreeCAD MCP server for tool '{tool_name}'."
                )
        result = await self.mcp_client.call_tool(tool_name, arguments)
        # CallToolResult exposes `.content` (list of ContentBlock) and `.structuredContent`.
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return json.dumps(structured, default=str)
        content = getattr(result, "content", None)
        if content is not None:
            return json.dumps([c.model_dump() if hasattr(c, "model_dump") else str(c) for c in content], default=str)
        return str(result)

    # ------------------------------------------------------------------ #
    # CADAdapter.get_tools() -> WHAT the agent may request.
    # ------------------------------------------------------------------ #
    def _get_local_tools(self) -> List[Dict[str, Any]]:
        """Return the hardcoded local OpenAI-compatible tool schemas."""
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
                    "description": (
                        "Create a hole by drilling into a face at a point. "
                        "For tapped/threaded holes, set kind='tapped' and provide "
                        "thread_spec using a standard designation such as 'M6x1.0' "
                        "or '1/4-20 UNC'. The tool will use the correct tap drill "
                        "diameter and ignore the numeric diameter for tapped holes."
                    ),
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
            {
                "type": "function",
                "function": {
                    "name": "mate",
                    "description": "Mate two independent bodies. 'concentric' aligns the central axes of two cylinders/holes/circular edges; 'coincident' brings two planar faces into flush contact. Use get_faces or get_edges first to find the reference IDs.",
                    "parameters": Mate.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "shell",
                    "description": "Hollow out a solid body into a thin-walled container/enclosure by removing the specified faces. Use get_faces first to find the face_refs to leave open. Use a negative thickness (e.g. -2.0) to shell inward.",
                    "parameters": Shell.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "pattern_linear",
                    "description": "Create a linear array of copies of an existing object along a direction vector with a fixed step distance. NEVER manually create duplicate objects; use this tool to array them.",
                    "parameters": LinearPattern.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "pattern_circular",
                    "description": "Create a circular array of copies of an existing object around an axis (e.g. a bolt circle). NEVER manually calculate coordinates; use this tool to array the object around the axis.",
                    "parameters": CircularPattern.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_mass_properties",
                    "description": "Get engineering mass properties of a solid body: volume, center of mass, and bounding box. Useful for validating design dimensions and weight distribution.",
                    "parameters": GetMassProperties.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_bom",
                    "description": "Generate a Bill of Materials: a list of all visible, distinct solid parts currently in the assembly document, with each part's name and volume.",
                    "parameters": GetBOM.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "interference_check",
                    "description": "Run pairwise interference (clash) detection across all visible solids in the assembly (or a specified subset). Returns clash details including overlapping volume for each intersecting pair.",
                    "parameters": InterferenceCheck.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "export",
                    "description": "Export the current visible assembly to a STEP or STL file. The file will be saved to the project's exports/ folder. Provide the desired format ('step' or 'stl') and a base filename without extension.",
                    "parameters": ExportModel.model_json_schema(),
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_feature",
                    "description": "Modify the parametric properties of an existing CAD feature (e.g., changing Length, Width, Radius, Height). The CAD kernel will automatically cascade these changes to all downstream dependent features. Provide the target object ID and a dictionary of property names and their new float values.",
                    "parameters": EditFeature.model_json_schema(),
                }
            },
        ]

    def _fetch_mcp_tools(self) -> List[Dict[str, Any]]:
        """Fetch and translate tools from the MCP server."""
        try:
            # Use asyncio.run to bridge sync-to-async
            return asyncio.run(self._async_fetch_mcp_tools())
        except Exception as e:
            print(f"[FreeCADAdapter] Warning: MCP server unreachable - {e}")
            return []

    async def _async_fetch_mcp_tools(self) -> List[Dict[str, Any]]:
        """Async helper to fetch and translate MCP tools."""
        client = FreeCADMCPClient()
        try:
            connected = await client.connect()
            if not connected:
                return []

            mcp_tools = await client.list_tools()
            # Translate each tool to OpenAI format
            translated = [translate_mcp_to_openai(tool) for tool in mcp_tools]
            return translated
        except Exception as e:
            print(f"[FreeCADAdapter] Warning: MCP tool fetch failed - {e}")
            return []
        finally:
            await client.disconnect()

    def _execute_mcp_tool(self, name: str, args: Dict[str, Any]) -> str:
        """Execute a tool through the external MCP server client.

        Uses a synchronous wrapper over the async client and returns the
        serialized MCP response. Disconnects the client cleanly after use.
        On failure returns a JSON failure dict.
        """
        try:
            return asyncio.run(self._async_execute_mcp_tool(name, args))
        except Exception as e:
            import traceback
            traceback.print_exc()
            return json.dumps({"success": False, "error": str(e)})

    async def _async_execute_mcp_tool(self, name: str, args: Dict[str, Any]) -> str:
        """Async helper to execute an MCP tool and normalize its response."""
        client = FreeCADMCPClient()
        try:
            connected = await client.connect()
            if not connected:
                raise RuntimeError(
                    f"Cannot connect to FreeCAD MCP server for tool '{name}'."
                )

            # Standard MCP protocol call
            result = await client.call_tool(name, args)

            # Extract the actual text/JSON result from the MCP response object.
            structured = getattr(result, "structuredContent", None)
            if structured is not None:
                return json.dumps(structured, default=str)

            content = getattr(result, "content", None)
            if content is not None:
                parts = []
                for c in content:
                    if hasattr(c, "text") and c.text is not None:
                        parts.append(c.text)
                    elif hasattr(c, "model_dump"):
                        parts.append(
                            json.dumps(c.model_dump(
                                exclude_none=True), default=str)
                        )
                    else:
                        parts.append(str(c))
                return "\n".join(parts)

            return str(result)
        finally:
            await client.disconnect()

    def get_tools(self) -> List[Dict[str, Any]]:
        """Return merged tool schemas: local tools + MCP tools (deduplicated)."""
        # Return cached result if available
        if self._cached_tools is not None:
            return self._cached_tools

        # Get local tools
        local_tools = self._get_local_tools()

        # Get MCP tools
        mcp_tools = self._fetch_mcp_tools()

        # Merge: local tools take priority (by name)
        local_names = {tool["function"]["name"] for tool in local_tools}
        merged = list(local_tools)
        for mcp_tool in mcp_tools:
            mcp_name = mcp_tool["function"]["name"]
            if mcp_name not in local_names:
                merged.append(mcp_tool)

        # Cache and return
        self._cached_tools = merged
        return merged

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

        # Sanitize list-typed arguments: the LLM occasionally passes JSON
        # stringified arrays (e.g. edge_refs='["edge_1"]') instead of raw
        # Python lists. Parse them before dispatching.
        for list_key in ("edge_refs", "face_refs", "part_ids", "shapes"):
            if list_key in kwargs and isinstance(kwargs[list_key], str):
                stripped = kwargs[list_key].strip()
                if stripped.startswith("["):
                    parsed = None
                    try:
                        parsed = json.loads(stripped)
                    except Exception:
                        try:
                            parsed = ast.literal_eval(stripped)
                        except Exception:
                            parsed = None
                    if isinstance(parsed, list):
                        kwargs[list_key] = parsed

        # ==================================================================== #
        # Dynamic dispatch: determine tool origin before routing.
        # Local tools ALWAYS take priority. MCP tools are validated against
        # the cached tool set. Unknown tools fail fast without launching server.
        # ==================================================================== #

        # Step A: Check if tool_name matches any local tool
        local_tools = self._get_local_tools()
        local_names = {tool["function"]["name"] for tool in local_tools}
        if tool_name in local_names:
            # Will be handled by existing local routing below
            pass
        else:
            # Step B: Ensure cache is populated, then check MCP tools
            if self._cached_tools is None:
                _ = self.get_tools()  # populate cache
            mcp_names = {
                tool["function"]["name"]
                for tool in self._cached_tools
                if tool["function"]["name"] not in local_names
            }
            if tool_name in mcp_names:
                return self._execute_mcp_tool(tool_name, kwargs)

            # Step C: Unknown tool - fail fast without launching server
            return json.dumps(
                {"success": False, "error": f"Unknown tool: {tool_name}"}
            )

        try:
            # Route external tools through the MCP server client instead of the
            # XML-RPC bridge. The 19 core tools continue to use the bridge below.
            if tool_name == "partdesign_sketch_constraint":
                return self._run_mcp_tool(tool_name, kwargs)

            if tool_name == "box":
                # Extract parameters from IR kwargs (required fields guaranteed by schema)
                obj_id = kwargs["id"]
                length = float(kwargs["length"])
                width = float(kwargs["width"])
                height = float(kwargs["height"])
                origin = _parse_dict_arg(kwargs.get("origin"), {
                                         "x": 0.0, "y": 0.0, "z": 0.0})

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
                origin = _parse_dict_arg(kwargs.get("origin"), {
                                         "x": 0.0, "y": 0.0, "z": 0.0})

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
                return json.dumps(faces)

            if tool_name == "hole":
                obj_id = kwargs["id"]
                target_id = kwargs["target_id"]
                origin = _parse_dict_arg(kwargs.get("origin"), {
                                         "x": 0.0, "y": 0.0, "z": 0.0})
                direction = _parse_dict_arg(kwargs.get("direction"), {
                    "x": 0.0, "y": 0.0, "z": -1.0})
                diameter = float(kwargs["diameter"])
                depth = float(kwargs["depth"])
                kind = kwargs.get("kind", "simple")
                thread_spec = kwargs.get("thread_spec")

                return str(
                    self._proxy.hole(
                        str(obj_id),
                        str(target_id),
                        origin,
                        direction,
                        diameter,
                        depth,
                        kind,
                        thread_spec,
                    )
                )

            if tool_name == "shell":
                f_refs = kwargs.get("face_refs", [])
                if isinstance(f_refs, str):
                    import ast
                    try:
                        f_refs = ast.literal_eval(f_refs)
                    except Exception:
                        f_refs = []
                thick = kwargs.get("thickness", -1.0)
                try:
                    thick = float(thick)
                except Exception:
                    thick = -1.0
                shell_id = kwargs.get("id") or "shell_op"
                return str(
                    self._proxy.shell(
                        shell_id,
                        kwargs.get("target_id", ""),
                        f_refs,
                        thick
                    )
                )

            elif tool_name == "mate":
                offset_val = kwargs.get("offset", 0.0)
                try:
                    offset_val = float(offset_val)
                except Exception:
                    offset_val = 0.0

                flip_val = kwargs.get("flip", False)
                if isinstance(flip_val, str):
                    flip_val = flip_val.strip().lower() in ("true", "1", "yes")
                else:
                    flip_val = bool(flip_val)

                # Parameter-synonym aliases: the LLM occasionally uses the
                # boolean tool's naming (tool_id/target_id) or packs both
                # subelement references into a single `references` array.
                if not kwargs.get("moving_target") and kwargs.get("tool_id"):
                    kwargs["moving_target"] = kwargs.pop("tool_id")
                if not kwargs.get("fixed_target") and kwargs.get("target_id"):
                    kwargs["fixed_target"] = kwargs.pop("target_id")

                if not kwargs.get("moving_ref") or not kwargs.get("fixed_ref"):
                    refs = kwargs.get("references")
                    if isinstance(refs, str):
                        try:
                            refs = json.loads(refs)
                        except Exception:
                            try:
                                refs = ast.literal_eval(refs)
                            except Exception:
                                refs = None
                    if isinstance(refs, (list, tuple)):
                        if len(refs) >= 2:
                            if not kwargs.get("moving_ref"):
                                kwargs["moving_ref"] = refs[0]
                            if not kwargs.get("fixed_ref"):
                                kwargs["fixed_ref"] = refs[1]
                        elif len(refs) == 1 and not kwargs.get("moving_ref"):
                            kwargs["moving_ref"] = refs[0]

                return str(
                    self._proxy.mate(
                        kwargs.get("id") or "mate_op",
                        str(kwargs.get("mate_type", "concentric")),
                        str(kwargs.get("moving_target", "")),
                        str(kwargs.get("moving_ref", "")),
                        str(kwargs.get("fixed_target", "")),
                        str(kwargs.get("fixed_ref", "")),
                        offset_val,
                        flip_val
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
                is_solid = kwargs.get("is_solid", True)
                return str(
                    self._proxy.extrude(
                        str(obj_id),
                        str(sketch_id),
                        depth,
                        is_cut,
                        is_solid,
                    )
                )

            if tool_name == "get_edges":
                object_name = kwargs["object_name"]
                edges = self._proxy.get_edges(str(object_name))
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

            if tool_name == "pattern_linear":
                obj_id = kwargs["id"]
                target_id = kwargs["target_id"]
                direction = _parse_dict_arg(kwargs.get("direction"), {
                                            "x": 1.0, "y": 0.0, "z": 0.0})
                distance = float(kwargs["distance"])
                count = int(kwargs["count"])

                return str(
                    self._proxy.pattern_linear(
                        str(obj_id),
                        str(target_id),
                        direction,
                        distance,
                        count,
                    )
                )

            if tool_name == "pattern_circular":
                obj_id = kwargs["id"]
                target_id = kwargs["target_id"]
                axis_origin = _parse_dict_arg(kwargs.get("axis_origin"), {
                                              "x": 0.0, "y": 0.0, "z": 0.0})
                axis_direction = _parse_dict_arg(kwargs.get("axis_direction"), {
                                                 "x": 0.0, "y": 0.0, "z": 1.0})
                angle = float(kwargs["angle"])
                count = int(kwargs["count"])

                return str(
                    self._proxy.pattern_circular(
                        str(obj_id),
                        str(target_id),
                        axis_origin,
                        axis_direction,
                        angle,
                        count,
                    )
                )

            if tool_name == "get_mass_properties":
                return str(
                    self._proxy.get_mass_properties(
                        kwargs.get("id", "mass"),
                        kwargs.get("object_name", ""),
                    )
                )

            if tool_name == "get_bom":
                return str(
                    self._proxy.get_bom(kwargs.get("id", "bom"))
                )

            if tool_name == "interference_check":
                part_ids = kwargs.get("part_ids")
                return str(
                    self._proxy.interference_check(
                        kwargs.get("id", "interference"),
                        part_ids
                    )
                )

            if tool_name == "export":
                exp_id = kwargs.get("id", "export")
                fmt = kwargs.get("format", "step")
                filename = kwargs.get("filename", "export")

                # Sanitize filename: replace spaces and special chars
                safe_name = re.sub(r'[^\w\-]', '_', filename)
                # Ensure correct extension
                ext = ".stl" if fmt.lower() == "stl" else ".step"
                if not safe_name.endswith(ext):
                    safe_name += ext

                # Calculate absolute path to exports/ at project root
                project_root = Path(__file__).resolve().parent.parent.parent
                exports_dir = project_root / "exports"
                exports_dir.mkdir(parents=True, exist_ok=True)
                filepath = str(exports_dir / safe_name)

                return str(
                    self._proxy.export_model(exp_id, fmt, filepath)
                )

            if tool_name == "edit_feature":
                params = kwargs.get("parameters", {})
                if isinstance(params, str):
                    import ast
                    try:
                        params = ast.literal_eval(params)
                    except Exception:
                        params = {}
                return str(
                    self._proxy.edit_feature(
                        kwargs.get("id", "edit"),
                        kwargs.get("target_id", ""),
                        params
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
    # Document Management
    # ------------------------------------------------------------------ #
    def clear_document(self):
        """Clear the FreeCAD document by closing it and creating a new one."""
        return self._proxy.clear_document()

    # ------------------------------------------------------------------ #
    # Backend API methods (not LLM tools)
    # ------------------------------------------------------------------ #
    def export_obj(self, filepath: str) -> str:
        """Exports the current visible CAD state to a .obj file."""
        return str(self._proxy.export_obj(filepath))

    def export_state_model(self, filepath: str, format: str = "glb") -> str:
        """Exports the current visible CAD state to GLB/glTF for the web viewer.

        Uses the bridge's export_current_state, which prefers FreeCAD's native
        glTF/GLB exporter and falls back to a standard .obj when unavailable.
        """
        return str(self._proxy.export_current_state(filepath, format))
