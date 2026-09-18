"""Lightweight tool router for PieCAD.

Filters which CAD tool schemas are sent to the LLM on each ReAct step, based on
the current CAD state. This keeps the context window small by only exposing the
tools that are actually relevant given how many solid objects exist.

CAD-agnostic: operates on generic state objects and tool schemas, with zero
FreeCAD-type-specific terminology (the schema/name matching is what the
adapter already broadcasts).
"""

from typing import Any, Dict, List


class ToolRouter:
    """Selects the active subset of tools given the current CAD state."""

    # Tools that should always be available regardless of state.
    _ALWAYS: List[str] = [
        # management / support tools not gated by geometry presence
        "boolean",
        "delete_feature",
        "sketch",
        "extrude",
        "pattern_linear",
        "pattern_circular",
        "get_mass_properties",
        "get_bom",
        "edit_feature",
        "export",
    ]

    # Named groups (mirrors the logical CAD concerns).
    _PRIMITIVES: List[str] = ["box", "cylinder"]
    _QUERY: List[str] = ["get_faces", "get_edges"]
    _TOPOLOGY: List[str] = ["fillet", "chamfer", "hole", "shell"]
    _ASSEMBLY: List[str] = ["mate", "interference_check"]

    def __init__(self) -> None:
        # Consolidated lookup: tool name -> gate key.
        self._tools_by_gate: Dict[str, str] = {}
        for gate, names in {
            "primitives": self._PRIMITIVES,
            "query": self._QUERY,
            "topology": self._TOPOLOGY,
            "assembly": self._ASSEMBLY,
        }.items():
            for name in names:
                self._tools_by_gate[name] = gate

    # ------------------------------------------------------------------ #
    # State analysis
    # ------------------------------------------------------------------ #
    def _count_solids(self, current_state_objects: list) -> int:
        """Count how many solid objects currently exist in the CAD state.

        Accepts the parsed state array (a list of object dicts). Each solid is
        recognised by ``shape_type == "Solid"``. Non-list / malformed states
        are treated as empty.
        """
        if not isinstance(current_state_objects, list):
            return 0
        count = 0
        for obj in current_state_objects:
            if isinstance(obj, dict) and obj.get("shape_type") == "Solid":
                count += 1
        return count

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def get_active_tools(self, current_state_objects: list) -> List[str]:
        """Return the sorted list of tool names that should be exposed.

        Gating rules:
          * ``primitives`` and ``query`` tools are ALWAYS returned.
          * ``topology`` tools are returned only if >= 1 solid exists.
          * ``assembly`` tools are returned only if >= 2 solids exist.
        """
        solids = self._count_solids(current_state_objects)

        active = set(self._ALWAYS)
        active.update(self._PRIMITIVES)
        active.update(self._QUERY)
        if solids >= 1:
            active.update(self._TOPOLOGY)
        if solids >= 2:
            active.update(self._ASSEMBLY)

        return sorted(active)

    def filter_tools(
        self, tool_schemas: List[Dict[str, Any]], current_state_objects: list
    ) -> List[Dict[str, Any]]:
        """Filter the provided tool schemas down to the active subset.

        ``tool_schemas`` is the adapter's OpenAI-style function schema list
        (each entry exposes its name under ``["function"]["name"]`` or
        ``["name"]``). Schemas whose normalized name is not active are dropped.
        """
        active = set(self.get_active_tools(current_state_objects))
        filtered: List[Dict[str, Any]] = []
        for schema in tool_schemas:
            name = self._schema_name(schema)
            if name in active:
                filtered.append(schema)
        return filtered

    @staticmethod
    def _schema_name(schema: Dict[str, Any]) -> str:
        """Extract and normalize a tool name from an OpenAI-style schema."""
        fn = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(fn, dict):
            name = fn.get("name")
        else:
            name = schema.get("name") if isinstance(schema, dict) else None
        return str(name).strip().lower() if name else ""
