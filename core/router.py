"""Lightweight tool router for PieCAD.

Filters which CAD tool schemas are sent to the LLM on each ReAct step, based on
the current CAD state. This keeps the context window small by only exposing the
tools that are actually relevant given how many solid objects exist.

CAD-agnostic: operates on generic state objects and tool schemas. A solid is
recognised without hard-coding a specific CAD kernel's TypeIds -- either via an
explicit ``shape_type == "Solid"`` signal, the presence of physical dimensional
properties (e.g. boxes/cylinders), or a generic solid-bearing type keyword.
"""

from typing import Any, Dict, List


class ToolRouter:
    """Selects the active subset of tools given the current CAD state."""

    # Tool name groups (mirrors the logical CAD concerns).
    # 1. Primitives: creators that can bootstrap geometry from an empty state.
    #    `extrude` is grouped here too because a sketch -> extrude is often the
    #    very first way to produce a solid when no primitive was used.
    _PRIMITIVES: List[str] = [
        "box", "cylinder", "sphere", "cone", "sketch", "extrude",
    ]

    # 2. Features/Modifiers: only meaningful once at least one solid exists.
    _FEATURES: List[str] = [
        "shell", "fillet", "chamfer", "hole", "boolean",
        "pattern_linear", "pattern_circular",
        "edit_feature", "delete_feature",
    ]

    # 3. Assembly/Multi-body: only meaningful once at least two solids exist.
    _ASSEMBLY: List[str] = ["mate", "interference_check"]

    # 4. Queries/Export: always available regardless of geometry.
    _QUERIES: List[str] = [
        "get_state", "get_faces", "get_edges",
        "get_mass_properties", "get_bom",
        "export", "export_state_model",
    ]

    # Generic type keywords indicating an object represents/bears a solid body.
    # Used only as a fallback when the state carries neither an explicit
    # ``shape_type`` nor dimensional properties.
    _SOLID_TYPE_KEYWORDS: tuple = (
        "box", "cylinder", "sphere", "cone",
        "cut", "fuse", "union", "common", "fillet",
        "chamfer", "thickness", "shell", "pad", "revolve", "loft",
        "pattern",
    )

    # Dimensional property keys that mark a primitive solid object.
    _DIMENSION_KEYS: tuple = ("Length", "Width", "Height", "Radius")

    def __init__(self) -> None:
        self._primitives = set(self._PRIMITIVES)
        self._features = set(self._FEATURES)
        self._assembly = set(self._ASSEMBLY)
        self._queries = set(self._QUERIES)

    # ------------------------------------------------------------------ #
    # State analysis
    # ------------------------------------------------------------------ #
    def _is_solid_object(self, obj: Any) -> bool:
        """Return True if ``obj`` represents valid physical solid geometry."""
        if not isinstance(obj, dict):
            return False

        # Explicit CAD-agnostic signal.
        if obj.get("shape_type") == "Solid":
            return True

        # A consumed/hidden ghost is not an active solid body to operate on.
        if obj.get("visible") is False:
            return False

        # Primitive solids expose dimensional properties.
        props = obj.get("properties")
        if isinstance(props, dict) and any(
            k in self._DIMENSION_KEYS for k in props
        ):
            return True

        # Fallback: a generic solid-bearing type keyword (e.g. primitive or a
        # feature result such as a boolean/fillet/shell outcome).
        obj_type = str(obj.get("type") or "").lower()
        return any(kw in obj_type for kw in self._SOLID_TYPE_KEYWORDS)

    def _count_solids(self, current_state_objects: Any) -> int:
        """Count solid objects that represent valid physical geometry.

        Handles empty/malformed states gracefully (returns 0). Ignores hidden
        ghost objects and non-geometry entries.
        """
        if not isinstance(current_state_objects, list):
            return 0
        return sum(
            1 for obj in current_state_objects
            if self._is_solid_object(obj)
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def get_active_tools(self, current_state_objects: Any) -> List[str]:
        """Return the sorted list of tool names that should be exposed.

        Gating thresholds:
          * ``primitives`` and ``queries`` tools are ALWAYS returned.
          * ``features``/modifier tools are returned only if >= 1 solid exists.
          * ``assembly`` tools are returned only if >= 2 solids exist.
        """
        solids = self._count_solids(current_state_objects)

        active = set(self._primitives)
        active.update(self._queries)
        if solids >= 1:
            active.update(self._features)
        if solids >= 2:
            active.update(self._assembly)

        return sorted(active)

    def filter_tools(
        self, tool_schemas: List[Dict[str, Any]], current_state_objects: Any
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
