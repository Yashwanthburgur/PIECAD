"""Dynamic capability-based tool router for PieCAD.

Selects the active subset of tools given the current CAD state, task intent,
and tool capabilities. Replaces hard-coded tool name lists with a dynamic
capability-driven model that scales to arbitrary MCP tools.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from core.tool_registry import ToolCapability, ToolRegistry, get_global_registry, infer_capability_from_schema
from core.context.plan import ContextPlan, TaskRequirement


class ToolRouter:
    """Selects the active subset of tools given the current CAD state and task context.

    The router uses a capability registry to make routing decisions based on:
    - Current CAD state (what solids/sketches/edges exist)
    - Task intent (from ContextPlan)
    - Tool capabilities (what tools produce/require)
    - Tool availability/health
    """

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        self.registry = registry or get_global_registry()
        # name -> {available, last_error, consecutive_failures}
        self._tool_health: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------ #
    # Tool health / availability tracking
    # ------------------------------------------------------------------ #
    def record_tool_result(self, name: str, success: bool, error: Optional[str] = None) -> None:
        """Record a tool execution result for health tracking."""
        if name not in self._tool_health:
            self._tool_health[name] = {
                "available": True, "consecutive_failures": 0, "last_error": None}

        health = self._tool_health[name]
        if success:
            health["consecutive_failures"] = 0
            health["available"] = True
            health["last_error"] = None
        else:
            health["consecutive_failures"] += 1
            health["last_error"] = error
            # Mark unavailable after 3 consecutive failures
            if health["consecutive_failures"] >= 3:
                health["available"] = False

    def is_tool_available(self, name: str) -> bool:
        """Check if a tool is currently available."""
        health = self._tool_health.get(name)
        if health is None:
            return True  # Unknown tools assumed available
        return health["available"]

    def get_unavailable_tools(self) -> Set[str]:
        """Get set of currently unavailable tool names."""
        return {name for name, health in self._tool_health.items() if not health["available"]}

    def reset_tool_health(self, name: str) -> None:
        """Reset health tracking for a tool (e.g., after recovery)."""
        if name in self._tool_health:
            self._tool_health[name] = {
                "available": True, "consecutive_failures": 0, "last_error": None}

    # ------------------------------------------------------------------ #
    # State analysis
    # ------------------------------------------------------------------ #
    def _is_solid_object(self, obj: Any) -> bool:
        """Return True if ``obj`` represents valid physical solid geometry."""
        if not isinstance(obj, dict):
            return False

        if obj.get("shape_type") == "Solid":
            return True

        if obj.get("visible") is False:
            return False

        props = obj.get("properties")
        if isinstance(props, dict) and any(
            k in ("Length", "Width", "Height", "Radius") for k in props
        ):
            return True

        obj_type = str(obj.get("type") or "").lower()
        solid_keywords = (
            "box", "cylinder", "sphere", "cone",
            "cut", "fuse", "union", "common", "fillet",
            "chamfer", "thickness", "shell", "pad", "revolve", "loft",
            "pattern",
        )
        return any(kw in obj_type for kw in solid_keywords)

    def _count_solids(self, current_state_objects: Any) -> int:
        if not isinstance(current_state_objects, list):
            return 0
        return sum(
            1 for obj in current_state_objects
            if self._is_solid_object(obj)
        )

    def _has_sketch(self, current_state_objects: Any) -> bool:
        if not isinstance(current_state_objects, list):
            return False
        for obj in current_state_objects:
            if isinstance(obj, dict):
                obj_type = str(obj.get("type") or "").lower()
                if "sketch" in obj_type and obj.get("visible") is not False:
                    return True
        return False

    def _has_edges(self, current_state_objects: Any) -> bool:
        # Edges are available if any solid exists
        return self._count_solids(current_state_objects) > 0

    def _has_faces(self, current_state_objects: Any) -> bool:
        # Faces are available if any solid exists
        return self._count_solids(current_state_objects) > 0

    # ------------------------------------------------------------------ #
    # Phase-aware tool selection
    # ------------------------------------------------------------------ #
    def _get_phase_gated_tools(self, plan: Optional[ContextPlan]) -> Set[str]:
        """Tools allowed based on the current task phase.

        This is the core phase-aware routing logic. It restricts tools
        to only those relevant for the current phase.
        """
        if plan is None:
            return set()

        current_req = plan.current_requirement()
        if current_req is None:
            return set()

        phase_name = current_req.name
        required_subtasks = current_req.required_subtasks

        # Map phase names to allowed tool categories and specific tools
        phase_tool_map = {
            "create_base": {
                "categories": ["primitive"],
                "specific_tools": ["box", "cylinder", "sphere", "cone", "torus", "wedge", "helix", "prism", "sketch", "extrude"],
            },
            "edge_modify": {
                "categories": [],
                "specific_tools": ["fillet", "chamfer", "get_edges"],
            },
            "shell": {
                "categories": [],
                "specific_tools": ["shell", "get_faces"],
            },
            "holes": {
                "categories": [],
                "specific_tools": ["hole", "get_faces"],
            },
            "inspect": {
                "categories": ["query"],
                "specific_tools": ["get_faces", "get_edges", "get_mass_properties", "get_bom", "export"],
            },
            "sketch_extrude": {
                "categories": ["sketch"],
                "specific_tools": ["sketch", "extrude", "pad", "pocket", "revolve", "loft", "sweep"],
            },
            "boolean_ops": {
                "categories": [],
                "specific_tools": ["boolean"],
            },
            "pattern_ops": {
                "categories": ["assembly"],
                "specific_tools": ["pattern_linear", "pattern_circular"],
            },
            "edit_feature": {
                "categories": [],
                "specific_tools": ["edit_feature"],
            },
            "assembly": {
                "categories": ["assembly"],
                "specific_tools": ["mate", "get_faces", "get_edges"],
            },
            "delete_undo": {
                "categories": [],
                "specific_tools": ["delete_feature", "undo"],
            },
            "export_ops": {
                "categories": ["query"],
                "specific_tools": ["export"],
            },
        }

        config = phase_tool_map.get(phase_name, {})
        allowed = set()

        # Add tools from allowed categories
        for cat in config.get("categories", []):
            allowed.update(self.registry.get_by_category(cat))

        # Add specific tools for this phase
        for tool in config.get("specific_tools", []):
            if tool in self.registry.all_names():
                allowed.add(tool)

        # Always include inspection, verification, recovery tools
        # These are safety/utility tools needed in every phase
        # Note: bootstrap tools (primitives) are NOT included in all phases
        # They are only available in create_base phase or when state allows
        allowed.update(self.registry.get_by_role("inspection"))
        allowed.update(self.registry.get_by_role("verification"))
        allowed.update(self.registry.get_by_role("recovery"))

        return allowed

    # ------------------------------------------------------------------ #
    # Capability-based gating
    # ------------------------------------------------------------------ #
    def _get_state_gated_tools(self, current_state_objects: Any) -> Set[str]:
        """Tools allowed based purely on CAD state."""
        solids = self._count_solids(current_state_objects)
        has_sketch = self._has_sketch(current_state_objects)
        has_edges = self._has_edges(current_state_objects)
        has_faces = self._has_faces(current_state_objects)

        active = set()

        # Always available: bootstrap tools + queries + recovery
        active.update(self.registry.bootstrap_tools())
        active.update(self.registry.get_by_role("inspection"))
        active.update(self.registry.get_by_role("verification"))
        active.update(self.registry.get_by_role("recovery"))

        # State-dependent gates
        if solids >= 1:
            # Tools that require a solid
            active.update(self.registry.get_by_requires("solid"))
            # Features category
            active.update(self.registry.get_by_category("feature"))
        if solids >= 2:
            active.update(self.registry.get_by_category("assembly"))
        if has_sketch:
            active.update(self.registry.get_by_requires("sketch"))
            active.update(self.registry.get_by_category("sketch"))
        if has_edges:
            active.update(self.registry.get_by_requires("edge"))
        if has_faces:
            active.update(self.registry.get_by_requires("face"))

        # Filter to only available tools
        unavailable = self.get_unavailable_tools()
        active -= unavailable

        return active

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def get_active_tools(
        self,
        current_state_objects: Any,
        plan: Optional[Any] = None,  # ContextPlan
        tool_plan: Optional[Any] = None,  # ToolSelectionPlan
    ) -> List[str]:
        """Return the sorted list of tool names that should be exposed.

        Considers:
        - CAD state (solids, sketches, edges, faces)
        - ContextPlan (required_tools, relevant_object_ids, phase)
        - ToolSelectionPlan (primary, optional, inspection, verification, recovery)
        - Tool health/availability
        """
        # Determine if we have an active ordered phase
        current_req = None
        if plan is not None and hasattr(plan, "current_requirement"):
            current_req = plan.current_requirement()

        if current_req is not None:
            # ACTIVE PHASE: Phase-gated tools are authoritative.
            # Start with phase-gated tools, then apply minimal state validation.
            active = self._get_phase_gated_tools(plan)

            # Minimal state validation: remove tools that require unavailable state
            # (e.g., edge tools when no edges exist, face tools when no faces exist)
            has_edges = self._has_edges(current_state_objects)
            has_faces = self._has_faces(current_state_objects)
            has_sketch = self._has_sketch(current_state_objects)

            if not has_edges:
                # Remove tools that require edges
                edge_tools = self.registry.get_by_requires("edge")
                active -= edge_tools
            if not has_faces:
                # Remove tools that require faces
                face_tools = self.registry.get_by_requires("face")
                active -= face_tools
            if not has_sketch:
                # Remove tools that require sketches
                sketch_tools = self.registry.get_by_requires("sketch")
                active -= sketch_tools

            # ToolSelectionPlan: only inspection/verification/recovery during active phase
            if tool_plan is not None:
                for t in tool_plan.inspection + tool_plan.verification + tool_plan.recovery:
                    if self.is_tool_available(t):
                        active.add(t)

        else:
            # NO ACTIVE PHASE (legacy): use state-gated + plan-required + tool_plan.all_tools
            active = self._get_state_gated_tools(current_state_objects)

            if plan is not None and hasattr(plan, "required_tools"):
                for t in plan.required_tools:
                    if self.is_tool_available(t):
                        active.add(t)

            if tool_plan is not None:
                for t in tool_plan.all_tools():
                    if self.is_tool_available(t):
                        active.add(t)

        return sorted(active)

    def filter_tools(
        self,
        tool_schemas: List[Dict[str, Any]],
        current_state_objects: Any,
        plan: Optional[Any] = None,
        tool_plan: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Filter the provided tool schemas down to the active subset."""
        active = set(self.get_active_tools(
            current_state_objects, plan, tool_plan))
        filtered: List[Dict[str, Any]] = []
        for schema in tool_schemas:
            name = self._schema_name(schema)
            if name in active:
                filtered.append(schema)
        return filtered

    @staticmethod
    def _schema_name(schema: Dict[str, Any]) -> str:
        fn = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(fn, dict):
            name = fn.get("name")
        else:
            name = schema.get("name") if isinstance(schema, dict) else None
        return str(name).strip().lower() if name else ""

    def sync_registry(self, tool_schemas: List[Dict[str, Any]]) -> None:
        """Synchronize the capability registry with the current tool schemas.

        Infers capabilities for any new tools not yet registered.
        """
        for schema in tool_schemas:
            name = self._schema_name(schema)
            if name and name not in self.registry.all_names():
                cap = infer_capability_from_schema(schema)
                self.registry.register(cap)

    def get_tool_health(self, name: str) -> Dict[str, Any]:
        """Get health info for a tool."""
        return self._tool_health.get(name, {"available": True, "consecutive_failures": 0, "last_error": None})
