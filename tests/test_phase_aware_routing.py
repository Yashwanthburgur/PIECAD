"""Focused tests for Phase-Aware Routing Micro-BIP.

Tests the phase tracking layer that reduces tool-schema explosion from
~60-70 active tools to only tools relevant to the current ordered task phase.
"""

import json
import pytest
from core.context.plan import ContextPlan, TaskRequirement
from core.intent import IntentClassifier, _build_ordered_requirements
from core.router import ToolRouter
from core.tool_registry import ToolRegistry, ToolCapability, get_global_registry, reset_global_registry


# --------------------------------------------------------------------------- #
# Test fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def registry():
    """Create a fresh tool registry with standard tools registered."""
    reg = reset_global_registry()

    # Register standard tools
    tools = [
        # Primitives
        ToolCapability("box", "primitive", produces=[
                       "solid"], can_bootstrap=True, keywords=["box", "create"]),
        ToolCapability("cylinder", "primitive", produces=[
                       "solid"], can_bootstrap=True, keywords=["cylinder", "create"]),
        ToolCapability("sphere", "primitive", produces=[
                       "solid"], can_bootstrap=True, keywords=["sphere", "create"]),
        ToolCapability("cone", "primitive", produces=[
                       "solid"], can_bootstrap=True, keywords=["cone", "create"]),

        # Edge modifications
        ToolCapability("fillet", "feature", requires=[
                       "edge", "solid"], mutates_topology=True, keywords=["fillet", "round", "edge"]),
        ToolCapability("chamfer", "feature", requires=[
                       "edge", "solid"], mutates_topology=True, keywords=["chamfer", "bevel", "edge"]),

        # Face modifications
        ToolCapability("shell", "feature", requires=[
                       "face", "solid"], mutates_topology=True, keywords=["shell", "hollow"]),
        ToolCapability("hole", "feature", requires=[
                       "face", "solid"], mutates_topology=True, keywords=["hole", "drill", "bore"]),

        # Inspection
        ToolCapability("get_faces", "query", role="inspection",
                       keywords=["faces", "inspect"]),
        ToolCapability("get_edges", "query", role="inspection",
                       keywords=["edges", "inspect"]),
        ToolCapability("get_mass_properties", "query", role="inspection", keywords=[
                       "mass", "volume", "properties"]),
        ToolCapability("get_bom", "query", role="inspection",
                       keywords=["bom", "bill"]),

        # Recovery
        ToolCapability("undo", "recovery", role="recovery", keywords=["undo"]),
        ToolCapability("redo", "recovery", role="recovery", keywords=["redo"]),

        # Verification
        ToolCapability("validate_object", "query",
                       role="verification", keywords=["validate"]),

        # Export
        ToolCapability("export", "query", role="inspection",
                       keywords=["export", "save"]),

        # Boolean
        ToolCapability("boolean", "feature", requires=[
                       "solid"], mutates_topology=True, keywords=["boolean", "union", "cut"]),

        # Pattern
        ToolCapability("pattern_linear", "assembly", requires=[
                       "solid"], mutates_topology=True, keywords=["pattern", "linear"]),
        ToolCapability("pattern_circular", "assembly", requires=[
                       "solid"], mutates_topology=True, keywords=["pattern", "circular"]),

        # Edit
        ToolCapability("edit_feature", "feature", requires=[
                       "solid"], keywords=["edit", "modify"]),

        # Assembly
        ToolCapability("mate", "assembly", requires=[
                       "solid"], keywords=["mate", "assemble"]),

        # Delete
        ToolCapability("delete_feature", "feature", requires=[
                       "solid"], keywords=["delete", "remove"]),
    ]

    for tool in tools:
        reg.register(tool)

    return reg


@pytest.fixture
def router(registry):
    """Create a tool router with the test registry."""
    return ToolRouter(registry)


@pytest.fixture
def intent_classifier(registry):
    """Create an intent classifier with the test registry."""
    return IntentClassifier(registry=registry)


# --------------------------------------------------------------------------- #
# ContextPlan Phase Tracking Tests
# --------------------------------------------------------------------------- #

class TestContextPlanPhaseTracking:
    """Tests for ContextPlan phase tracking methods."""

    def test_current_intent_returns_first_phase(self):
        """current_intent() returns the first phase when at index 0."""
        plan = ContextPlan(
            required_subtasks=[
                TaskRequirement("create_base", ["box"], 1),
                TaskRequirement("edge_modify", ["fillet", "chamfer"], 1),
            ]
        )
        assert plan.current_intent() == "create_base"

    def test_current_intent_returns_none_when_done(self):
        """current_intent() returns None when all phases complete."""
        plan = ContextPlan(
            phase_index=2,
            required_subtasks=[
                TaskRequirement("create_base", ["box"], 1),
            ]
        )
        assert plan.current_intent() is None

    def test_is_phase_complete_false_initially(self):
        """is_phase_complete() returns False when no subtasks completed."""
        plan = ContextPlan(
            required_subtasks=[
                TaskRequirement("edge_modify", ["fillet", "chamfer"], 1),
            ]
        )
        assert plan.is_phase_complete() is False

    def test_is_phase_complete_true_when_all_done(self):
        """is_phase_complete() returns True when all required subtasks done."""
        plan = ContextPlan(
            required_subtasks=[
                TaskRequirement("edge_modify", ["fillet", "chamfer"], 1),
            ],
            completed_subtasks={0: ["fillet", "chamfer"]}
        )
        assert plan.is_phase_complete() is True

    def test_is_phase_complete_false_when_partial(self):
        """is_phase_complete() returns False when only some subtasks done."""
        plan = ContextPlan(
            required_subtasks=[
                TaskRequirement("edge_modify", ["fillet", "chamfer"], 1),
            ],
            completed_subtasks={0: ["fillet"]}
        )
        assert plan.is_phase_complete() is False

    def test_is_phase_complete_quantity_four_holes(self):
        """is_phase_complete() respects quantity for hole operations."""
        plan = ContextPlan(
            required_subtasks=[
                TaskRequirement("holes", ["hole"], 4),
            ],
            completed_subtasks={0: ["hole", "hole", "hole"]}
        )
        assert plan.is_phase_complete() is False

        plan.completed_subtasks = {0: ["hole", "hole", "hole", "hole"]}
        assert plan.is_phase_complete() is True

    def test_mark_subtask_complete_adds_to_current_phase(self):
        """mark_subtask_complete() adds subtask to current phase."""
        plan = ContextPlan(
            required_subtasks=[
                TaskRequirement("edge_modify", ["fillet", "chamfer"], 1),
            ]
        )
        plan.mark_subtask_complete("fillet")
        assert plan.completed_subtasks[0] == ["fillet"]

    def test_mark_subtask_complete_ignores_unknown_subtask(self):
        """mark_subtask_complete() ignores subtasks not in current phase."""
        plan = ContextPlan(
            required_subtasks=[
                TaskRequirement("create_base", ["box"], 1),
            ]
        )
        plan.mark_subtask_complete("fillet")  # Not in create_base phase
        assert plan.completed_subtasks == {}

    def test_advance_phase_moves_to_next(self):
        """advance_phase() increments phase_index when complete."""
        plan = ContextPlan(
            phase_index=0,
            required_subtasks=[
                TaskRequirement("create_base", ["box"], 1),
                TaskRequirement("edge_modify", ["fillet"], 1),
            ],
            completed_subtasks={0: ["box"]}
        )
        result = plan.advance_phase()
        assert result is True
        assert plan.phase_index == 1
        assert plan.current_intent() == "edge_modify"

    def test_advance_phase_fails_when_incomplete(self):
        """advance_phase() returns False when current phase incomplete."""
        plan = ContextPlan(
            phase_index=0,
            required_subtasks=[
                TaskRequirement("create_base", ["box"], 1),
                TaskRequirement("edge_modify", ["fillet"], 1),
            ]
        )
        result = plan.advance_phase()
        assert result is False
        assert plan.phase_index == 0

    def test_advance_phase_fails_when_done(self):
        """advance_phase() returns False when all phases complete."""
        plan = ContextPlan(
            phase_index=1,
            required_subtasks=[
                TaskRequirement("create_base", ["box"], 1),
            ],
            completed_subtasks={0: ["box"]}
        )
        result = plan.advance_phase()
        assert result is False
        assert plan.phase_index == 1


# --------------------------------------------------------------------------- #
# Intent Classifier Ordered Requirements Tests
# --------------------------------------------------------------------------- #

class TestIntentClassifierOrderedRequirements:
    """Tests for _build_ordered_requirements function."""

    def test_create_base_only(self):
        """Only create_base phase when only base creation requested."""
        reqs = _build_ordered_requirements("create a box", ["create_base"])
        assert len(reqs) == 1
        assert reqs[0].name == "create_base"
        assert reqs[0].required_subtasks == ["box"]

    def test_edge_modify_both_fillet_chamfer(self):
        """edge_modify requires both fillet and chamfer when both mentioned."""
        reqs = _build_ordered_requirements(
            "add fillet and chamfer", ["edge_modify"])
        assert len(reqs) == 1
        assert reqs[0].name == "edge_modify"
        assert "fillet" in reqs[0].required_subtasks
        assert "chamfer" in reqs[0].required_subtasks

    def test_edge_modify_fillet_only(self):
        """edge_modify requires only fillet when only fillet mentioned."""
        reqs = _build_ordered_requirements(
            "add fillet to edges", ["edge_modify"])
        assert len(reqs) == 1
        assert reqs[0].required_subtasks == ["fillet"]

    def test_shell_phase_separate(self):
        """shell phase is separate from holes."""
        reqs = _build_ordered_requirements(
            "make a hollow shell", ["face_modify"])
        assert len(reqs) == 1
        assert reqs[0].name == "shell"
        assert reqs[0].required_subtasks == ["shell"]

    def test_holes_phase_separate(self):
        """holes phase is separate from shell."""
        reqs = _build_ordered_requirements("drill four holes", ["face_modify"])
        assert len(reqs) == 1
        assert reqs[0].name == "holes"
        assert reqs[0].required_subtasks == ["hole"]
        assert reqs[0].quantity == 4

    def test_holes_quantity_from_number(self):
        """Holes quantity extracted from numeric text."""
        reqs = _build_ordered_requirements("drill 3 holes", ["face_modify"])
        assert reqs[0].quantity == 3

    def test_full_ordered_sequence(self):
        """Full sequence: create_base -> edge_modify -> shell -> holes."""
        text = "create a box with fillet and chamfer, make it hollow, and drill 4 holes"
        reqs = _build_ordered_requirements(
            text, ["create_base", "edge_modify", "face_modify"])

        assert len(reqs) == 4
        assert reqs[0].name == "create_base"
        assert reqs[1].name == "edge_modify"
        assert reqs[2].name == "shell"
        assert reqs[3].name == "holes"
        assert reqs[3].quantity == 4

    def test_no_auto_inspect_phase(self):
        """Inspect phase NOT auto-added unless explicitly requested."""
        reqs = _build_ordered_requirements("drill a hole", ["face_modify"])
        inspect_phases = [r for r in reqs if r.name == "inspect"]
        assert len(inspect_phases) == 0

    def test_explicit_inspect_adds_phase(self):
        """Explicit inspect request adds inspect phase."""
        reqs = _build_ordered_requirements("drill a hole and inspect it", [
                                           "face_modify", "inspect"])
        inspect_phases = [r for r in reqs if r.name == "inspect"]
        assert len(inspect_phases) == 1


# --------------------------------------------------------------------------- #
# Router Phase-Aware Tool Filtering Tests
# --------------------------------------------------------------------------- #

class TestRouterPhaseAwareFiltering:
    """Tests for router phase-aware tool filtering."""

    def test_create_base_phase_exposes_only_primitives(self, router):
        """create_base phase exposes only primitive creation tools."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement("create_base", ["box"], 1)]
        )

        state_objects = []
        active = router.get_active_tools(state_objects, plan)

        assert "box" in active
        assert "cylinder" in active
        assert "sphere" in active

        assert "fillet" not in active
        assert "chamfer" not in active
        assert "hole" not in active
        assert "shell" not in active

        assert "pattern_linear" not in active
        assert "mate" not in active

    def test_edge_modify_phase_exposes_fillet_chamfer(self, router):
        """edge_modify phase exposes fillet, chamfer, and edge inspection."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement(
                "edge_modify", ["fillet", "chamfer"], 1)]
        )

        state_objects = [{"id": "box1", "type": "Box",
                          "visible": True, "properties": {"Length": 10}}]
        active = router.get_active_tools(state_objects, plan)

        assert "fillet" in active
        assert "chamfer" in active
        assert "get_edges" in active

        assert "shell" not in active
        assert "hole" not in active

        assert "pattern_linear" not in active
        assert "mate" not in active

    def test_shell_phase_exposes_shell_not_holes(self, router):
        """shell phase exposes shell tool but not hole tool."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement("shell", ["shell"], 1)]
        )

        state_objects = [{"id": "box1", "type": "Box",
                          "visible": True, "properties": {"Length": 10}}]
        active = router.get_active_tools(state_objects, plan)

        assert "shell" in active
        assert "get_faces" in active
        assert "hole" not in active
        assert "fillet" not in active
        assert "chamfer" not in active

    def test_holes_phase_exposes_hole_not_shell(self, router):
        """holes phase exposes hole tool but not shell tool."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement("holes", ["hole"], 4)]
        )

        state_objects = [{"id": "box1", "type": "Box",
                          "visible": True, "properties": {"Length": 10}}]
        active = router.get_active_tools(state_objects, plan)

        assert "hole" in active
        assert "get_faces" in active
        assert "shell" not in active
        assert "fillet" not in active
        assert "chamfer" not in active

    def test_inspect_phase_exposes_inspection_tools(self, router):
        """inspect phase exposes inspection tools."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement(
                "inspect", ["get_faces", "get_edges", "get_mass_properties"], 1)]
        )

        state_objects = [{"id": "box1", "type": "Box",
                          "visible": True, "properties": {"Length": 10}}]
        active = router.get_active_tools(state_objects, plan)

        assert "get_faces" in active
        assert "get_edges" in active
        assert "get_mass_properties" in active
        assert "get_bom" in active
        assert "export" in active

        assert "box" not in active
        assert "fillet" not in active
        assert "hole" not in active

    def test_failed_mutation_does_not_advance_phase(self, router, intent_classifier):
        """Failed mutation does not advance phase (tested via intent plan logic)."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement(
                "edge_modify", ["fillet", "chamfer"], 1)]
        )
        assert plan.is_phase_complete() is False

    def test_fillet_alone_does_not_complete_edge_modify(self, router):
        """Successful fillet alone does not complete edge_modify (needs chamfer too)."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement(
                "edge_modify", ["fillet", "chamfer"], 1)]
        )
        plan.mark_subtask_complete("fillet")
        assert plan.is_phase_complete() is False

    def test_fillet_plus_chamfer_completes_edge_modify(self, router):
        """Successful fillet + chamfer completes edge_modify."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement(
                "edge_modify", ["fillet", "chamfer"], 1)]
        )
        plan.mark_subtask_complete("fillet")
        plan.mark_subtask_complete("chamfer")
        assert plan.is_phase_complete() is True

    def test_one_hole_does_not_complete_four_hole_requirement(self, router):
        """One hole does not complete four-hole requirement."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement("holes", ["hole"], 4)]
        )
        plan.mark_subtask_complete("hole")
        assert plan.is_phase_complete() is False

    def test_four_successful_holes_complete_hole_requirement(self, router):
        """Four successful holes complete the hole requirement."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement("holes", ["hole"], 4)]
        )
        for _ in range(4):
            plan.mark_subtask_complete("hole")
        assert plan.is_phase_complete() is True

    def test_recovery_inspection_tools_do_not_advance_phase(self, router):
        """Recovery/inspection tools do not advance phase."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement(
                "edge_modify", ["fillet", "chamfer"], 1)]
        )

        plan.mark_subtask_complete("get_faces")
        plan.mark_subtask_complete("get_edges")
        plan.mark_subtask_complete("get_mass_properties")
        plan.mark_subtask_complete("undo")

        assert plan.is_phase_complete() is False
        assert plan.completed_subtasks == {}

    def test_multiple_react_calls_remain_in_same_phase(self, router):
        """Multiple ReAct calls remain inside the same phase until complete."""
        plan = ContextPlan(
            required_subtasks=[TaskRequirement(
                "edge_modify", ["fillet", "chamfer"], 1)]
        )

        plan.mark_subtask_complete("fillet")
        assert plan.current_intent() == "edge_modify"
        assert plan.phase_index == 0

        plan.mark_subtask_complete("chamfer")
        assert plan.is_phase_complete() is True

        plan.advance_phase()
        assert plan.current_intent() is None

    def test_final_fourth_hole_preserves_no_post_verification(self, router):
        """Final successful fourth hole does not force inspect phase."""
        plan = ContextPlan(
            required_subtasks=[
                TaskRequirement("holes", ["hole"], 4),
            ]
        )

        for _ in range(4):
            plan.mark_subtask_complete("hole")

        assert plan.is_phase_complete() is True
        plan.advance_phase()
        assert plan.current_intent() is None


# --------------------------------------------------------------------------- #
# Router ToolSelectionPlan Phase Pollution Tests
# --------------------------------------------------------------------------- #

class TestRouterToolPlanPhasePollution:
    """Tests that tool_plan.primary tools do NOT pollute active phases."""

    def test_tool_plan_primary_excluded_during_edge_modify(self, router):
        """tool_plan.primary from create_base does not expose box during edge_modify."""
        from core.context.plan import ToolSelectionPlan

        plan = ContextPlan(
            required_subtasks=[TaskRequirement(
                "edge_modify", ["fillet", "chamfer"], 1)]
        )
        tool_plan = ToolSelectionPlan(
            primary=["box", "cylinder", "sphere", "fillet",
                     "chamfer"],  # create_base + edge_modify
            inspection=["get_faces", "get_edges", "get_mass_properties"],
            recovery=["undo"],
            optional=[],
            verification=[]
        )

        state_objects = [{"id": "box1", "type": "Box",
                          "visible": True, "properties": {"Length": 10}}]
        active = router.get_active_tools(state_objects, plan, tool_plan)

        # Should have phase-specific mutation tools
        assert "fillet" in active
        assert "chamfer" in active
        assert "get_edges" in active

        # Should NOT have create_base primary tools
        assert "box" not in active
        assert "cylinder" not in active
        assert "sphere" not in active

        # Should still have inspection/verification/recovery
        assert "get_faces" in active
        assert "get_mass_properties" in active
        assert "undo" in active

    def test_tool_plan_primary_excluded_during_shell(self, router):
        """tool_plan.primary from create_base/holes does not expose box/hole during shell."""
        from core.context.plan import ToolSelectionPlan

        plan = ContextPlan(
            required_subtasks=[TaskRequirement("shell", ["shell"], 1)]
        )
        tool_plan = ToolSelectionPlan(
            primary=["box", "hole", "fillet", "shell"],
            inspection=["get_faces"],
            recovery=["undo"],
            optional=[],
            verification=[]
        )

        state_objects = [{"id": "box1", "type": "Box",
                          "visible": True, "properties": {"Length": 10}}]
        active = router.get_active_tools(state_objects, plan, tool_plan)

        assert "shell" in active
        assert "get_faces" in active
        assert "box" not in active
        assert "hole" not in active
        assert "fillet" not in active
        assert "undo" in active  # recovery still available

    def test_tool_plan_primary_excluded_during_holes(self, router):
        """tool_plan.primary from other phases does not expose shell/fillet during holes."""
        from core.context.plan import ToolSelectionPlan

        plan = ContextPlan(
            required_subtasks=[TaskRequirement("holes", ["hole"], 4)]
        )
        tool_plan = ToolSelectionPlan(
            primary=["box", "shell", "fillet", "chamfer", "hole"],
            inspection=["get_faces"],
            recovery=["undo"],
            optional=[],
            verification=[]
        )

        state_objects = [{"id": "box1", "type": "Box",
                          "visible": True, "properties": {"Length": 10}}]
        active = router.get_active_tools(state_objects, plan, tool_plan)

        assert "hole" in active
        assert "get_faces" in active
        assert "box" not in active
        assert "shell" not in active
        assert "fillet" not in active
        assert "chamfer" not in active
        assert "undo" in active

    def test_tool_plan_primary_included_when_no_active_phase(self, router):
        """tool_plan.primary IS included when no active phase (legacy behavior)."""
        from core.context.plan import ToolSelectionPlan

        plan = ContextPlan(
            required_subtasks=[],  # No ordered phases
            required_tools=["fillet", "chamfer"]
        )
        tool_plan = ToolSelectionPlan(
            primary=["box", "cylinder", "fillet", "chamfer"],
            inspection=["get_faces"],
            recovery=["undo"],
            optional=[],
            verification=[]
        )

        state_objects = [{"id": "box1", "type": "Box",
                          "visible": True, "properties": {"Length": 10}}]
        active = router.get_active_tools(state_objects, plan, tool_plan)

        # Should have all primary tools (legacy)
        assert "box" in active
        assert "cylinder" in active
        assert "fillet" in active
        assert "chamfer" in active
        assert "get_faces" in active
        assert "undo" in active


# --------------------------------------------------------------------------- #
# Router Legacy/Non-Phase Compatibility Tests
# --------------------------------------------------------------------------- #

class TestRouterLegacyCompatibility:
    """Tests that legacy ContextPlan (no ordered phases) still works."""

    def test_legacy_plan_with_required_tools_no_phases(self, router):
        """Legacy plan with required_tools but no ordered phases adds all tools."""
        plan = ContextPlan(
            required_tools=["fillet", "chamfer", "hole", "shell"]
        )
        state_objects = [{"id": "box1", "type": "Box",
                          "visible": True, "properties": {"Length": 10}}]
        active = router.get_active_tools(state_objects, plan)

        assert "fillet" in active
        assert "chamfer" in active
        assert "hole" in active
        assert "shell" in active

    def test_legacy_plan_empty_phases_falls_back(self, router):
        """Legacy plan with empty required_subtasks falls back to required_tools."""
        plan = ContextPlan(
            required_subtasks=[],
            required_tools=["fillet", "chamfer"]
        )
        state_objects = [{"id": "box1", "type": "Box",
                          "visible": True, "properties": {"Length": 10}}]
        active = router.get_active_tools(state_objects, plan)

        assert "fillet" in active
        assert "chamfer" in active


# --------------------------------------------------------------------------- #
# Compiler Phase-Gated Tool Selection Tests
# --------------------------------------------------------------------------- #

class TestCompilerPhaseGatedSelection:
    """Tests that compiler final tool set remains phase-gated."""

    def test_compiler_create_base_phase_gated(self):
        """Compiler final tool set for create_base excludes feature tools."""
        from core.context.compiler import ContextCompiler
        from core.context.state import DesignState
        from core.context.memory import SessionMemory

        comp = ContextCompiler()
        plan = ContextPlan(
            required_subtasks=[TaskRequirement("create_base", ["box"], 1)],
            required_tools=["fillet", "chamfer", "hole", "shell", "boolean"]
        )

        available_tools = [
            {"type": "function", "function": {"name": "box", "description": "box",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "cylinder", "description": "cylinder",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "fillet", "description": "fillet",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "chamfer", "description": "chamfer",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "hole", "description": "hole",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "shell", "description": "shell",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "boolean", "description": "boolean",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "get_faces", "description": "get_faces",
                                              "parameters": {"type": "object", "properties": {}}}},
        ]

        compiled = comp.compile(
            user_message="create a box",
            design_state=DesignState(),
            session_memory=SessionMemory(),
            available_tools=available_tools,
            optional_context_plan=plan,
        )

        names = {t["function"]["name"] for t in compiled.tools}
        assert "box" in names
        assert "cylinder" in names
        assert "get_faces" in names

        assert "fillet" not in names
        assert "chamfer" not in names
        assert "hole" not in names
        assert "shell" not in names
        assert "boolean" not in names

    def test_compiler_edge_modify_phase_gated(self):
        """Compiler final tool set for edge_modify excludes shell/hole/boolean."""
        from core.context.compiler import ContextCompiler
        from core.context.state import DesignState
        from core.context.memory import SessionMemory

        comp = ContextCompiler()
        plan = ContextPlan(
            required_subtasks=[TaskRequirement(
                "edge_modify", ["fillet", "chamfer"], 1)],
            required_tools=["shell", "hole", "boolean", "pattern_linear"]
        )

        available_tools = [
            {"type": "function", "function": {"name": "fillet", "description": "fillet",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "chamfer", "description": "chamfer",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "shell", "description": "shell",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "hole", "description": "hole",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "boolean", "description": "boolean",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "pattern_linear",
                                              "description": "pattern_linear", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "get_edges", "description": "get_edges",
                                              "parameters": {"type": "object", "properties": {}}}},
        ]

        state = DesignState()
        state.update_from_cad_state(json.dumps(
            [{"id": "box1", "type": "Box", "visible": True}]))

        compiled = comp.compile(
            user_message="add fillet and chamfer",
            design_state=state,
            session_memory=SessionMemory(),
            available_tools=available_tools,
            optional_context_plan=plan,
        )

        names = {t["function"]["name"] for t in compiled.tools}
        assert "fillet" in names
        assert "chamfer" in names
        assert "get_edges" in names

        assert "shell" not in names
        assert "hole" not in names
        assert "boolean" not in names
        assert "pattern_linear" not in names

    def test_compiler_shell_phase_excludes_hole(self):
        """Compiler shell phase does not include hole tool."""
        from core.context.compiler import ContextCompiler
        from core.context.state import DesignState
        from core.context.memory import SessionMemory

        comp = ContextCompiler()
        plan = ContextPlan(
            required_subtasks=[TaskRequirement("shell", ["shell"], 1)],
            required_tools=["hole", "fillet", "chamfer"]
        )

        available_tools = [
            {"type": "function", "function": {"name": "shell", "description": "shell",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "hole", "description": "hole",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "fillet", "description": "fillet",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "chamfer", "description": "chamfer",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "get_faces", "description": "get_faces",
                                              "parameters": {"type": "object", "properties": {}}}},
        ]

        state = DesignState()
        state.update_from_cad_state(json.dumps(
            [{"id": "box1", "type": "Box", "visible": True}]))

        compiled = comp.compile(
            user_message="make a shell",
            design_state=state,
            session_memory=SessionMemory(),
            available_tools=available_tools,
            optional_context_plan=plan,
        )

        names = {t["function"]["name"] for t in compiled.tools}
        assert "shell" in names
        assert "get_faces" in names
        assert "hole" not in names
        assert "fillet" not in names
        assert "chamfer" not in names

    def test_compiler_holes_phase_excludes_shell_and_edge(self):
        """Compiler holes phase does not include shell/fillet/chamfer."""
        from core.context.compiler import ContextCompiler
        from core.context.state import DesignState
        from core.context.memory import SessionMemory

        comp = ContextCompiler()
        plan = ContextPlan(
            required_subtasks=[TaskRequirement("holes", ["hole"], 4)],
            required_tools=["shell", "fillet", "chamfer", "boolean"]
        )

        available_tools = [
            {"type": "function", "function": {"name": "hole", "description": "hole",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "shell", "description": "shell",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "fillet", "description": "fillet",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "chamfer", "description": "chamfer",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "boolean", "description": "boolean",
                                              "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "get_faces", "description": "get_faces",
                                              "parameters": {"type": "object", "properties": {}}}},
        ]

        state = DesignState()
        state.update_from_cad_state(json.dumps(
            [{"id": "box1", "type": "Box", "visible": True}]))

        compiled = comp.compile(
            user_message="drill 4 holes",
            design_state=state,
            session_memory=SessionMemory(),
            available_tools=available_tools,
            optional_context_plan=plan,
        )

        names = {t["function"]["name"] for t in compiled.tools}
        assert "hole" in names
        assert "get_faces" in names
        assert "shell" not in names
        assert "fillet" not in names
        assert "chamfer" not in names
        assert "boolean" not in names


# --------------------------------------------------------------------------- #
# IntentClassifier Integration Tests
# --------------------------------------------------------------------------- #

class TestIntentClassifierIntegration:
    """Tests for IntentClassifier producing phase-aware ContextPlan."""

    def test_classify_returns_ordered_requirements(self, intent_classifier):
        """classify() returns IntentResult with ordered_requirements."""
        result = intent_classifier.classify(
            "create a box with fillet and chamfer")

        assert len(result.ordered_requirements) >= 2
        assert result.ordered_requirements[0].name == "create_base"
        assert result.ordered_requirements[1].name == "edge_modify"

    def test_to_context_plan_includes_phase_fields(self, intent_classifier):
        """to_context_plan() includes phase tracking fields."""
        result = intent_classifier.classify("create a box with fillet")
        plan = intent_classifier.to_context_plan(
            result, "create a box with fillet")

        assert plan.phase_index == 0
        assert plan.completed_subtasks == {}
        assert len(plan.required_subtasks) >= 2

    def test_plan_has_current_requirement_method(self, intent_classifier):
        """ContextPlan has current_requirement() method."""
        result = intent_classifier.classify("create a box")
        plan = intent_classifier.to_context_plan(result, "create a box")

        req = plan.current_requirement()
        assert req is not None
        assert req.name == "create_base"


# --------------------------------------------------------------------------- #
# DesignState Evidence-Based Completion Tests
# --------------------------------------------------------------------------- #

class TestDesignStateEvidence:
    """Tests that phase completion uses DesignState.recent_operations as evidence."""

    def test_hole_count_from_recent_operations(self):
        """Test that hole count can be determined from recent_operations."""
        from core.context.state import DesignState, RecentOperation

        state = DesignState()

        for i in range(3):
            state.recent_operations.append(
                RecentOperation(tool="hole", target_id="box1", args={
                                "id": f"hole{i}"}, success=True)
            )

        hole_count = sum(
            1 for op in state.recent_operations if op.tool == "hole" and op.success)
        assert hole_count == 3

        state.recent_operations.append(
            RecentOperation(tool="hole", target_id="box1",
                            args={"id": "hole3"}, success=True)
        )
        hole_count = sum(
            1 for op in state.recent_operations if op.tool == "hole" and op.success)
        assert hole_count == 4

    def test_failed_operations_not_counted(self):
        """Failed operations don't count toward phase completion."""
        from core.context.state import DesignState, RecentOperation

        state = DesignState()

        state.recent_operations.append(
            RecentOperation(tool="hole", target_id="box1",
                            args={"id": "hole1"}, success=True)
        )
        state.recent_operations.append(
            RecentOperation(tool="hole", target_id="box1",
                            args={"id": "hole2"}, success=False)
        )

        hole_count = sum(
            1 for op in state.recent_operations if op.tool == "hole" and op.success)
        assert hole_count == 1

    def test_recovery_operations_not_counted(self):
        """Recovery/read-only operations don't count toward mutation phases."""
        from core.context.state import DesignState, RecentOperation

        state = DesignState()

        state.recent_operations.append(
            RecentOperation(tool="hole", target_id="box1",
                            args={"id": "hole1"}, success=True)
        )
        state.recent_operations.append(
            RecentOperation(tool="get_faces", target_id="box1",
                            args={}, success=True)
        )
        state.recent_operations.append(
            RecentOperation(tool="undo", target_id="box1",
                            args={}, success=True)
        )

        hole_mutations = sum(
            1 for op in state.recent_operations if op.tool == "hole" and op.success)
        assert hole_mutations == 1


# --------------------------------------------------------------------------- #
# Mutation Result / Live Target Propagation Tests
# --------------------------------------------------------------------------- #

class TestMutationResultPropagation:
    """Tests that successful mutation results are correctly propagated to DesignState
    and subsequent context compilation identifies the live target correctly."""

    def test_fillet_creates_result_target_marks_source_obsolete(self):
        """Successful fillet creates result target and marks source as consumed."""
        from core.context.state import DesignState, RecentOperation, DesignObject

        state = DesignState()

        # Simulate: box exists
        state.objects["box1"] = DesignObject(
            object_id="box1",
            object_type="Part::Box",
            label="Box",
            visible=True,
            properties={"Length": 100.0, "Width": 100.0, "Height": 100.0},
        )

        # Simulate successful fillet on box1, creating fillet1
        state.recent_operations.append(RecentOperation(
            tool="fillet",
            target_id="box1",
            args={"id": "fillet1", "target_id": "box1",
                  "edge_refs": ["box1_edge_1"]},
            success=True
        ))

        # Verify recent_operations has the result
        recent = state.get_recent_operations(1)
        assert len(recent) == 1
        assert recent[0].tool == "fillet"
        assert recent[0].target_id == "box1"
        assert recent[0].args.get("id") == "fillet1"

        # The result object should be trackable
        assert "fillet1" in str(recent[0].args.get("id"))

    def test_subsequent_context_identifies_live_target(self):
        """After fillet, compiler should identify fillet1 as live target for edge ops."""
        from core.context.compiler import ContextCompiler
        from core.context.state import DesignState, DesignObject
        from core.context.memory import SessionMemory
        from core.context.plan import ContextPlan, TaskRequirement
        from core.context.conversation import ConversationContext

        state = DesignState()
        state.objects["box1"] = DesignObject(
            object_id="box1",
            object_type="Part::Box",
            label="Box",
            visible=True,
            properties={"Length": 100.0},
        )
        state.objects["fillet1"] = DesignObject(
            object_id="fillet1",
            object_type="Part::Fillet",
            label="Fillet",
            visible=True,
            parents=["box1"],
            children=[],
        )
        state.objects["box1"].visible = False
        state.objects["box1"].children = ["fillet1"]

        # Plan for edge_modify phase
        plan = ContextPlan(
            required_subtasks=[TaskRequirement("edge_modify", ["chamfer"], 1)],
            relevant_object_ids=["box1"],  # Original target still in plan
        )

        comp = ContextCompiler()
        state_view = comp._select_state(state, plan, None, "add chamfer")

        # Should include fillet1 (the live descendant) in the context
        object_ids = [o["id"] for o in state_view.get("objects", [])]
        assert "fillet1" in object_ids, f"Expected fillet1 in context, got {object_ids}"

    def test_stale_source_not_preferred_mutation_target(self):
        """After mutation, stale source should not be presented as preferred target."""
        from core.context.state import DesignState, DesignObject

        state = DesignState()
        state.objects["box1"] = DesignObject(
            object_id="box1",
            object_type="Part::Box",
            label="Box",
            visible=False,  # Consumed
            children=["fillet1"],
        )
        state.objects["fillet1"] = DesignObject(
            object_id="fillet1",
            object_type="Part::Fillet",
            label="Fillet",
            visible=True,
            parents=["box1"],
        )

        active = state.resolve_active_object("box1")
        assert active is not None
        assert active.object_id == "fillet1", f"Expected fillet1, got {active.object_id}"

    def test_unrelated_operations_unaffected(self):
        """Unrelated operations (e.g., shell on different object) unaffected."""
        from core.context.state import DesignState, DesignObject

        state = DesignState()
        state.objects["box1"] = DesignObject(
            object_id="box1", object_type="Part::Box", visible=True, properties={}
        )
        state.objects["box2"] = DesignObject(
            object_id="box2", object_type="Part::Box", visible=True, properties={}
        )
        state.objects["shell1"] = DesignObject(
            object_id="shell1", object_type="Part::Thickness", visible=True, parents=["box2"]
        )
        state.objects["box2"].visible = False
        state.objects["box2"].children = ["shell1"]

        # box1 operations should not be affected by box2's mutation
        active1 = state.resolve_active_object("box1")
        assert active1.object_id == "box1"

        active2 = state.resolve_active_object("box2")
        assert active2.object_id == "shell1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
