"""ContextCompiler — the central component that assembles a selective LLM context
for a single ReAct reasoning step.

This is the heart of BIP 4.2. It turns:

    (user_message, conversation_context, design_state, session_memory,
     available_tools, optional_context_plan)

into a structured, *selective* compiled context containing ONLY the information
chosen for the current reasoning step — never a blind dump of the whole CAD
document, whole memory store, or whole history.

Compilation stages (in order):

    1. determine requirements   (relevance engine -> ContextPlan)
    2. select state             (DesignState.select_objects)
    3. select memory            (SessionMemory.relevant)
    4. select history           (ConversationContext.relevant_history)
    5. select tools             (existing ToolRouter + optional ToolSelectionPlan)
    6. assemble context         (build the structured payload)
    7. estimate size            (chars/4 heuristic telemetry)
    8. return compiled context

The compiler is fully provider-independent and consumes an optional ``ContextPlan``,
which is exactly how a future Intent Classifier plugs in without an architectural
change.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .budget import ContextBudget
from .conversation import ConversationContext
from .memory import SessionMemory
from .plan import ContextPlan, ToolSelectionPlan
from .relevance import RelevanceEngine
from .state import DesignState
from .telemetry import ContextTelemetry, estimate_tokens, monotonic_ms

# Globally recognised tool roles (used by the existing ToolRouter) plus the
# richer inspection/verification/recovery roles from ToolSelectionPlan.
_ALWAYS_TOOLS = {"get_state"}
_QUERY_TOOLS = {
    "get_state", "get_faces", "get_edges", "get_mass_properties",
    "get_bom", "export", "export_state_model",
}
_MODE_PREFIX = (
    "You are PieCAD, a production-grade mechanical engineering AI agent. "
    "You control a live CAD model/document. "
    "Communicate tersely: when finished, reply with at most two sentences "
    "stating exactly what the current visible final object is. "
    "Never invent object names; reference exact ids/dimensions from the "
    "provided state. Do not stop until the whole request is fulfilled. "
)


@dataclass
class CompiledContext:
    """Structured, selective context for a single reasoning step."""

    system_context: str = ""
    user_context: str = ""
    conversation: List[Dict[str, str]] = field(default_factory=list)
    design_state: Dict[str, Any] = field(default_factory=dict)
    memory: Dict[str, Any] = field(default_factory=dict)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    tools_exposed: int = 0
    plan: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    telemetry: Optional[ContextTelemetry] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "system_context": self.system_context,
            "user_context": self.user_context,
            "conversation": self.conversation,
            "design_state": self.design_state,
            "memory": self.memory,
            "tools": self.tools,
            "tools_exposed": self.tools_exposed,
            "plan": self.plan,
            "metadata": self.metadata,
        }

    def estimated_total_tokens(self) -> int:
        """Sum of the estimated tokens across all assembled sections."""
        parts = [
            self.system_context,
            self.user_context,
            json.dumps(self.design_state, default=str),
            json.dumps(self.memory, default=str),
            json.dumps(self.conversation, default=str),
            json.dumps(self.tools, default=str),
        ]
        return sum(estimate_tokens(p) for p in parts)


class ContextCompiler:
    """Provider-independent selective context assembler."""

    def __init__(
        self,
        *,
        relevance_engine: Optional[RelevanceEngine] = None,
        budget: Optional[ContextBudget] = None,
        system_prefix: str = _MODE_PREFIX,
    ) -> None:
        self.relevance = relevance_engine or RelevanceEngine()
        self.budget = budget or ContextBudget()
        self.system_prefix = system_prefix

    # ------------------------------------------------------------------ #
    # Stages
    # ------------------------------------------------------------------ #
    def _determine_requirements(
        self,
        user_message: str,
        design_state: Optional[DesignState],
        session_memory: Optional[SessionMemory],
        conversation: Optional[ConversationContext],
        provided_plan: Optional[ContextPlan],
    ) -> ContextPlan:
        if provided_plan is not None:
            return provided_plan
        return self.relevance.plan(
            user_request=user_message,
            state=design_state,
            memory=session_memory,
            conversation=conversation,
        )

    def _select_state(
        self, design_state: Optional[DesignState], plan: ContextPlan
    ) -> Dict[str, Any]:
        if design_state is None:
            return {"selected": [], "objects": []}

        object_ids = plan.relevant_object_ids
        if not object_ids:
            # No concrete objects selected yet — expose the compact summary so the
            # agent can choose what to inspect, without dumping every object.
            if "objects" in plan.required_state_sections:
                summary = design_state.summary()
                return {"summary": summary}
            return {"selected": [], "objects": []}

        # Pull in the target objects plus one hop of relationship neighbours so
        # the agent sees the object's context (e.g. a hole's target body).
        view = design_state.select_objects(object_ids, depth=1)
        selected = view["selected"]
        # Trim to budget (estimated tokens).
        if self.budget.state_budget is not None:
            trimmed = self._trim_object_list(
                view["objects"], self.budget.state_budget)
            if len(trimmed) < len(view["objects"]):
                self.budget.note_dropped("state_objects_overflow")
            view["objects"] = trimmed
        return view

    def _select_memory(
        self,
        session_memory: Optional[SessionMemory],
        plan: ContextPlan,
        user_message: str,
    ) -> Dict[str, Any]:
        if session_memory is None:
            return {}
        from .memory import resolve_section_kinds, _OBJECT_SIGNALS  # local import

        # Map the plan's requested memory sections (which are plural, e.g.
        # "conventions") onto concrete memory kinds (e.g. "convention").
        wanted_kinds: List[str] = []
        for section in plan.required_memory_sections:
            for kind in resolve_section_kinds(section):
                if kind not in wanted_kinds:
                    wanted_kinds.append(kind)

        if wanted_kinds:
            # Selective pull: compute the relevance layer's choice of memory
            # (which is already filtered per-entry in SessionMemory.relevant),
            # then keep ONLY the sections the plan requires. No fallback that
            # could re-introduce "every entry of a wanted kind" leakage.
            relevant = session_memory.relevant(user_message)
            constrained: Dict[str, Any] = {}
            for kind in wanted_kinds:
                entries = relevant.get(kind)
                if entries:
                    constrained[kind] = list(entries)
            return constrained

        # No plan-specified sections: fall back to free-text relevance.
        relevant = session_memory.relevant(user_message)
        return {k: v for k, v in relevant.items() if v}

    def _select_history(
        self,
        conversation: Optional[ConversationContext],
        user_message: str,
        plan: ContextPlan,
    ) -> List[Dict[str, str]]:
        if conversation is None:
            return []
        if plan.relevant_history_terms:
            query = " ".join(plan.relevant_history_terms)
        else:
            query = user_message
        return conversation.to_messages(query)

    def _select_tools(
        self,
        available_tools: List[Dict[str, Any]],
        design_state: Optional[DesignState],
        plan: ContextPlan,
        tool_plan: Optional[ToolSelectionPlan],
    ) -> List[Dict[str, Any]]:
        from core.router import ToolRouter  # local import avoids circular import
        from core.tool_registry import get_global_registry

        # Use the global registry to ensure consistency with agent's router
        registry = get_global_registry()
        router = ToolRouter(registry)

        state_objects = []
        if design_state is not None:
            state_objects = [o.to_dict(minimal=True)
                             for o in design_state.objects.values()]

        # Use new capability-based router API with plan and tool_plan
        router_names = set(router.get_active_tools(
            state_objects, plan, tool_plan))
        plan_names = set(plan.required_tools)
        explicit = set()
        if tool_plan is not None:
            explicit = set(tool_plan.all_tools())
            plan_names |= explicit

        # Final exposure = intersection of adapter surface with (router-active ∪
        # plan-required ∪ explicit tool-plan names). This preserves the adapter's
        # full capability while narrowing per-call exposure.
        wanted = router_names | plan_names
        chosen: List[Dict[str, Any]] = []
        seen: set = set()
        for schema in available_tools:
            name = self._schema_name(schema)
            if name in wanted and name not in seen:
                chosen.append(schema)
                seen.add(name)
        return chosen

    def _assemble(self, compiled: CompiledContext) -> CompiledContext:
        payload = compiled.to_dict()
        # system_context: prefix + current selection/derived facts, no raw dump.
        system = self.system_prefix
        state_summary = payload["design_state"]
        system += f"\n\nCURRENT CAD STATE:\n{json.dumps(state_summary, default=str)}"
        compiled.system_context = system
        return compiled

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def compile(
        self,
        user_message: str,
        conversation_context: Optional[ConversationContext] = None,
        design_state: Optional[DesignState] = None,
        session_memory: Optional[SessionMemory] = None,
        available_tools: Optional[List[Dict[str, Any]]] = None,
        optional_context_plan: Optional[ContextPlan] = None,
        *,
        tool_selection_plan: Optional[ToolSelectionPlan] = None,
        react_step: Optional[int] = None,
        system_prefix: Optional[str] = None,
    ) -> CompiledContext:
        t0 = monotonic_ms()
        self.budget = ContextBudget()

        # 1. requirements
        plan = self._determine_requirements(
            user_message, design_state, session_memory,
            conversation_context, optional_context_plan,
        )

        # 2. state
        state = self._select_state(design_state, plan)

        # 3. memory
        memory = self._select_memory(session_memory, plan, user_message)

        # 4. history
        history = self._select_history(
            conversation_context, user_message, plan)

        # 5. tools
        tools = self._select_tools(
            available_tools or [], design_state, plan, tool_selection_plan,
        )

        # 6. assemble
        compiled = CompiledContext(
            system_context=self.system_prefix,
            user_context=user_message,
            conversation=history,
            design_state=state,
            memory=memory,
            tools=tools,
            tools_exposed=len(tools),
            plan=plan.to_dict(),
            metadata={
                "react_step": react_step,
                "reasoning_mode": plan.reasoning_mode,
                "confidence": plan.confidence,
                "ambiguity": plan.ambiguity,
                "matched_rules": plan.additional_context.get("matched_rules", []),
            },
        )
        if system_prefix:
            self.system_prefix = system_prefix
        compiled = self._assemble(compiled)

        # 7. size - enforce total context budget
        # If estimated tokens exceed maximum, proportionally trim sections
        # (prioritizing tools > state > memory > history)
        total_est = compiled.estimated_total_tokens()
        max_allowed = (self.budget.maximum_context_tokens or 20000) - \
            (self.budget.reserved_output_tokens or 1000)
        if total_est > max_allowed:
            # Determine how much to trim from each section
            tool_est = estimate_tokens(json.dumps(tools, default=str))
            state_est = estimate_tokens(json.dumps(state, default=str))
            memory_est = estimate_tokens(json.dumps(memory, default=str))
            history_est = estimate_tokens(json.dumps(history, default=str))
            system_est = estimate_tokens(self.system_prefix)
            user_est = estimate_tokens(user_message)

            # Fixed overhead (system + user) cannot be trimmed
            fixed_overhead = system_est + user_est
            trim_target = total_est - max_allowed

            # Trim sections in priority order: history -> memory -> state -> tools
            # (tools are most critical for reasoning)
            sections = [
                ("history", history, history_est, 1.0),
                ("memory", memory, memory_est, 1.0),
                # preserve state more aggressively
                ("state", state, state_est, 0.5),
                ("tools", tools, tool_est, 0.2),   # tools most important
            ]

            for section_name, section_data, section_est, protect_factor in sections:
                if trim_target <= 0:
                    break
                # Amount available to trim from this section
                max_trim = int(section_est * protect_factor)
                if max_trim <= 0:
                    continue
                trim_amount = min(trim_target, max_trim)

                if section_name == "tools":
                    # Trim tools from the end (least priority first)
                    tools = self._trim_tool_list(tools, trim_amount)
                    self.budget.note_dropped("tools_overflow")
                elif section_name == "state":
                    # Trim state objects from the end
                    if "objects" in state:
                        state["objects"] = self._trim_object_list(state["objects"],
                                                                  max(0, section_est - trim_amount))
                    self.budget.note_dropped("state_objects_overflow")
                elif section_name == "memory":
                    # Trim memory by reducing entries
                    for kind in memory:
                        if isinstance(memory[kind], list) and trim_amount > 0:
                            memory[kind] = memory[kind][:max(
                                1, len(memory[kind]) // 2)]
                    self.budget.note_dropped("memory_overflow")
                elif section_name == "history":
                    # Trim history
                    if isinstance(history, list) and trim_amount > 0:
                        history = history[:max(1, len(history) // 2)]
                    self.budget.note_dropped("history_overflow")

                trim_target -= trim_amount

            # Reassemble with trimmed sections
            compiled = CompiledContext(
                system_context=self.system_prefix,
                user_context=user_message,
                conversation=history,
                design_state=state,
                memory=memory,
                tools=tools,
                tools_exposed=len(tools),
                plan=plan.to_dict(),
                metadata={
                    "react_step": react_step,
                    "reasoning_mode": plan.reasoning_mode,
                    "confidence": plan.confidence,
                    "ambiguity": plan.ambiguity,
                    "matched_rules": plan.additional_context.get("matched_rules", []),
                },
            )
            if system_prefix:
                self.system_prefix = system_prefix
            compiled = self._assemble(compiled)

        telemetry = ContextTelemetry(
            react_step=react_step,
            estimated_context_tokens=compiled.estimated_total_tokens(),
            estimated_tool_schema_tokens=estimate_tokens(
                json.dumps(tools, default=str)),
            estimated_state_tokens=estimate_tokens(
                json.dumps(state, default=str)),
            estimated_memory_tokens=estimate_tokens(
                json.dumps(memory, default=str)),
            estimated_conversation_tokens=estimate_tokens(
                json.dumps(history, default=str)),
            tools_exposed=len(tools),
            tools_exposed_names=[self._schema_name(t) for t in tools],
            why_tools=plan.additional_context.get("matched_rules", []),
            dropped_sections=list(self.budget.dropped_sections),
            compilation_time_ms=round(monotonic_ms() - t0, 3),
        )
        compiled.telemetry = telemetry
        # 8. return
        return compiled

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _schema_name(schema: Dict[str, Any]) -> str:
        fn = schema.get("function") if isinstance(schema, dict) else None
        if isinstance(fn, dict):
            name = fn.get("name")
        else:
            name = schema.get("name") if isinstance(schema, dict) else None
        return str(name).strip().lower() if name else ""

    def _trim_object_list(
        self, objects: List[Dict[str, Any]], budget_tokens: int
    ) -> List[Dict[str, Any]]:
        """Trim a list of object dicts down to an estimated token budget.

        This does NOT drop the *selected* objects first — it trims from the end
        (which are the relationship-expansion neighbours), keeping the key target.
        """
        if budget_tokens is None or budget_tokens <= 0:
            return objects
        total = estimate_tokens(json.dumps(objects, default=str))
        if total <= budget_tokens:
            return objects
        kept: List[Dict[str, Any]] = []
        running = 0
        # Preserve the earliest (target) objects, drop the tail expansion.
        for obj in objects:
            cost = estimate_tokens(json.dumps(obj, default=str))
            if running + cost > budget_tokens:
                break
            kept.append(obj)
            running += cost
        return kept

    def _trim_tool_list(
        self, tools: List[Dict[str, Any]], trim_tokens: int
    ) -> List[Dict[str, Any]]:
        """Trim a list of tool schemas by removing the least-critical tools.

        Removes tools from the end of the list (which are typically the
        lower-priority/expansion tools) until the estimated token reduction
        meets trim_tokens.
        """
        if trim_tokens <= 0 or not tools:
            return tools
        total = estimate_tokens(json.dumps(tools, default=str))
        if total <= trim_tokens:
            return tools[:1]  # Keep at least 1 tool
        kept: List[Dict[str, Any]] = []
        running = 0
        # Preserve the earliest (highest priority) tools
        for tool in tools:
            cost = estimate_tokens(json.dumps(tool, default=str))
            if running + cost > total - trim_tokens:
                break
            kept.append(tool)
            running += cost
        return kept if kept else tools[:1]
