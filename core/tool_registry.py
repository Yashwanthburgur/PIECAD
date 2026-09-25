"""Unified Tool Capability Registry.

Provides a single source of truth for tool metadata across local and MCP tools.
This replaces hard-coded tool name lists with a capability-driven model that
scales to arbitrary MCP tools.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class ToolCapability:
    """Metadata describing a tool's capabilities and prerequisites."""

    name: str
    # Human-readable category: "primitive", "feature", "assembly", "query", "sketch", "partdesign", "draft", etc.
    category: str
    # What this tool produces or operates on
    # e.g. ["solid"], ["sketch"], ["pattern"]
    produces: List[str] = field(default_factory=list)
    # e.g. ["solid"], ["edge"], ["face"], ["sketch"]
    requires: List[str] = field(default_factory=list)
    # Tool role in a workflow
    role: str = "primary"  # "primary", "optional", "inspection", "verification", "recovery"
    # Keywords for relevance matching
    keywords: List[str] = field(default_factory=list)
    # Whether the tool can bootstrap from empty state
    can_bootstrap: bool = False
    # Whether tool modifies topology (for version tracking)
    mutates_topology: bool = False
    # Source of the tool
    source: str = "local"  # "local", "mcp"
    # MCP server name if from MCP
    mcp_server: Optional[str] = None


class ToolRegistry:
    """Central registry of all known tool capabilities.

    Scans local tool schemas + MCP tool schemas and builds a unified capability
    view. The registry is the single source of truth for routing decisions.
    """

    def __init__(self) -> None:
        self._capabilities: Dict[str, ToolCapability] = {}
        self._category_index: Dict[str, Set[str]] = {}
        self._produces_index: Dict[str, Set[str]] = {}
        self._requires_index: Dict[str, Set[str]] = {}
        self._role_index: Dict[str, Set[str]] = {}

    def register(self, capability: ToolCapability) -> None:
        """Register a tool capability."""
        self._capabilities[capability.name] = capability

        # Update indexes
        self._category_index.setdefault(
            capability.category, set()).add(capability.name)
        for p in capability.produces:
            self._produces_index.setdefault(p, set()).add(capability.name)
        for r in capability.requires:
            self._requires_index.setdefault(r, set()).add(capability.name)
        self._role_index.setdefault(
            capability.role, set()).add(capability.name)

    def get(self, name: str) -> Optional[ToolCapability]:
        """Get capability by tool name."""
        return self._capabilities.get(name)

    def get_by_category(self, category: str) -> Set[str]:
        """Get all tool names in a category."""
        return self._category_index.get(category, set())

    def get_by_produces(self, produces: str) -> Set[str]:
        """Get tools that produce a given capability."""
        return self._produces_index.get(produces, set())

    def get_by_requires(self, requires: str) -> Set[str]:
        """Get tools that require a given capability."""
        return self._requires_index.get(requires, set())

    def get_by_role(self, role: str) -> Set[str]:
        """Get tools with a given role."""
        return self._role_index.get(role, set())

    def all_names(self) -> Set[str]:
        """All registered tool names."""
        return set(self._capabilities.keys())

    def all_capabilities(self) -> Dict[str, ToolCapability]:
        """All capabilities."""
        return dict(self._capabilities)

    def bootstrap_tools(self) -> Set[str]:
        """Tools that can run from empty state."""
        return {name for name, cap in self._capabilities.items() if cap.can_bootstrap}

    def topology_mutating_tools(self) -> Set[str]:
        """Tools that mutate topology."""
        return {name for name, cap in self._capabilities.items() if cap.mutates_topology}

    def match_keywords(self, text: str) -> Set[str]:
        """Find tools whose keywords match the text."""
        text_lower = text.lower()
        matched = set()
        for name, cap in self._capabilities.items():
            if any(kw in text_lower for kw in cap.keywords):
                matched.add(name)
        return matched


# Global registry instance
_global_registry: Optional[ToolRegistry] = None


def get_global_registry() -> ToolRegistry:
    """Get or create the global tool registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = ToolRegistry()
    return _global_registry


def reset_global_registry() -> ToolRegistry:
    """Reset and return the global registry (for testing)."""
    global _global_registry
    _global_registry = ToolRegistry()
    return _global_registry


def infer_capability_from_schema(schema: Dict[str, Any], source: str = "local", mcp_server: Optional[str] = None) -> ToolCapability:
    """Infer a ToolCapability from an OpenAI function schema.

    Uses heuristics based on tool name, description, and parameters to populate
    capability fields. This avoids manual registration for every tool.
    """
    fn = schema.get("function", {}) if isinstance(schema, dict) else {}
    name = str(fn.get("name", "")).strip().lower()
    description = str(fn.get("description", "")).lower()
    parameters = fn.get("parameters", {})
    props = parameters.get("properties", {}) if isinstance(
        parameters, dict) else {}

    # Category inference
    category = "unknown"
    if name in ("box", "cylinder", "sphere", "cone", "torus", "wedge", "helix", "prism", "regular_polygon"):
        category = "primitive"
    elif name in ("sketch", "extrude", "pad", "revolve", "loft", "sweep", "extrude_shape", "revolve_shape", "part_loft", "part_sweep"):
        category = "sketch"
    elif name in ("fillet", "chamfer", "hole", "shell", "draft_feature", "thickness_feature", "boolean", "boolean_operation", "fuse", "cut", "intersect", "edit_feature", "delete_feature", "set_placement", "scale_object", "rotate_object", "copy_object", "mirror_object", "create_line", "create_plane", "create_ellipse", "shell_object", "offset_3d", "slice_shape", "section_shape", "make_compound", "explode_compound", "fuse_all", "common_all", "make_wire", "make_face"):
        category = "feature"
    elif name in ("mate", "interference_check", "pattern_linear", "pattern_circular", "linear_pattern", "polar_pattern", "mirrored_feature"):
        category = "assembly"
    elif name in ("get_state", "get_faces", "get_edges", "get_mass_properties", "get_bom", "get_selection", "set_selection", "clear_selection", "list_objects", "inspect_object", "export", "export_step", "export_stl", "export_obj", "export_iges", "export_3mf", "import_step", "import_stl", "validate_object", "validate_document", "get_console_log", "recompute", "recompute_document", "get_sketch_info", "toggle_construction"):
        category = "query"
    elif name in ("create_sketch", "add_sketch_rectangle", "add_sketch_circle", "add_sketch_line", "add_sketch_arc", "add_sketch_point", "add_sketch_ellipse", "add_sketch_polygon", "add_sketch_slot", "add_sketch_bspline", "add_sketch_constraint", "constrain_horizontal", "constrain_vertical", "constrain_coincident", "constrain_parallel", "constrain_perpendicular", "constrain_tangent", "constrain_equal", "constrain_distance", "constrain_distance_x", "constrain_distance_y", "constrain_radius", "constrain_angle", "constrain_fix", "add_external_geometry", "delete_sketch_geometry", "delete_sketch_constraint"):
        category = "partdesign"
    elif name in ("draft_shapestring", "draft_list_fonts", "draft_shapestring_to_sketch", "draft_shapestring_to_face", "draft_text_on_surface", "draft_extrude_shapestring"):
        category = "draft"
    elif name in ("create_partdesign_body", "pad_sketch", "pocket_sketch", "revolution_sketch", "groove_sketch", "fillet_edges", "chamfer_edges", "create_hole", "create_datum_plane", "create_datum_line", "create_datum_point", "subtractive_loft", "subtractive_pipe"):
        category = "partdesign"
    elif name in ("spreadsheet_create", "spreadsheet_set_cell", "spreadsheet_get_cell", "spreadsheet_set_alias", "spreadsheet_get_aliases", "spreadsheet_clear_cell", "spreadsheet_bind_property", "spreadsheet_get_cell_range", "spreadsheet_import_csv", "spreadsheet_export_csv"):
        category = "spreadsheet"
    elif name in ("list_documents", "get_active_document", "create_document", "open_document", "save_document", "close_document"):
        category = "document"
    elif name in ("list_workbenches", "activate_workbench", "fit_all", "set_object_visibility", "set_display_mode", "set_object_color", "zoom_in", "zoom_out", "set_camera_position", "get_screenshot"):
        category = "view"
    elif name in ("undo", "redo", "get_undo_redo_status", "undo_if_invalid", "safe_execute"):
        category = "recovery"
    elif name in ("list_macros", "run_macro", "create_macro", "read_macro", "delete_macro", "create_macro_from_template"):
        category = "macro"
    elif name in ("execute_python", "get_freecad_version", "get_connection_status", "get_console_output", "get_mcp_server_environment"):
        category = "execution"

    # Produces inference
    produces = []
    if "solid" in description or "body" in description:
        produces.append("solid")
    if "sketch" in description or name.startswith("add_sketch") or name in ("create_sketch", "sketch"):
        produces.append("sketch")
    if "pattern" in name:
        produces.append("pattern")
    if "edge" in description and ("fillet" in name or "chamfer" in name):
        produces.append("fillet_chamfer")

    # Requires inference
    requires = []
    if "target" in props or "target_id" in props or "object_name" in props:
        requires.append("solid")
    if "edge" in description and "ref" in str(props).lower():
        requires.append("edge")
    if "face" in description and "ref" in str(props).lower():
        requires.append("face")
    if "sketch" in props or "sketch_id" in props:
        requires.append("sketch")

    # Role inference
    role = "primary"
    if name.startswith("get_") or name in ("inspect_object", "list_objects", "export", "export_step", "export_stl", "export_obj", "export_iges", "export_3mf"):
        role = "inspection"
    elif name in ("validate_object", "validate_document", "recompute", "recompute_document"):
        role = "verification"
    elif name in ("undo", "redo", "get_undo_redo_status", "undo_if_invalid", "safe_execute"):
        role = "recovery"
    elif name in ("edit_feature", "delete_feature", "set_param", "set_placement", "scale_object", "rotate_object", "copy_object", "mirror_object"):
        role = "optional"

    # Bootstrap inference
    can_bootstrap = name in ("box", "cylinder", "sphere", "cone", "torus", "wedge", "helix", "prism", "regular_polygon", "sketch", "create_sketch",
                             "create_partdesign_body", "create_object", "create_box", "create_cylinder", "create_sphere", "create_cone", "create_torus", "create_wedge", "create_helix")

    # Topology mutation
    mutates = name in ("fillet", "chamfer", "hole", "shell", "boolean", "boolean_operation", "fuse", "cut", "intersect", "pattern_linear", "pattern_circular", "linear_pattern", "polar_pattern", "edit_feature", "delete_feature",
                       "pad_sketch", "pocket_sketch", "revolution_sketch", "groove_sketch", "fillet_edges", "chamfer_edges", "create_hole", "mirrored_feature", "subtractive_loft", "subtractive_pipe", "draft_feature", "thickness_feature")

    # Keywords from description
    keywords = []
    for kw in name.replace("_", " ").split():
        keywords.append(kw)
    # Add some from description
    for word in ["hole", "drill", "fillet", "chamfer", "pattern", "array", "export", "save", "inspect", "measure", "delete", "edit", "modify", "resize", "change", "select", "undo", "redo", "sketch", "extrude", "pad", "pocket", "revolve", "loft", "sweep", "boolean", "union", "subtract", "fuse", "cut", "intersect", "mirror", "draft", "shell", "offset", "slice", "section", "compound", "fuse", "common", "wire", "face", "line", "plane", "ellipse", "prism", "polygon", "helix", "torus", "wedge", "cone", "sphere", "box", "cylinder"]:
        if word in description:
            keywords.append(word)

    return ToolCapability(
        name=name,
        category=category,
        produces=produces,
        requires=requires,
        role=role,
        keywords=keywords,
        can_bootstrap=can_bootstrap,
        mutates_topology=mutates,
        source=source,
        mcp_server=mcp_server,
    )
