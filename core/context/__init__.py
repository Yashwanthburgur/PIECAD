"""Context Engine for PieCAD.

Provides provider-independent state, memory, conversation, relevance, budgeting
and compilation abstractions that a future Intent/Decision layer can plug into.
"""

from .budget import ContextBudget
from .compiler import CompiledContext, ContextCompiler
from .conversation import ConversationContext, Turn
from .memory import SessionMemory, MemoryEntry
from .plan import (
    ContextPlan,
    ToolSelectionPlan,
    tool_plan_for,
    REASONING_BOOLEAN,
    REASONING_DEFAULT,
    REASONING_EXPORT,
    REASONING_INSPECT,
    REASONING_MODIFY,
    REASONING_VAGUE,
)
from .relevance import RelevanceEngine, RelevanceRule, CONCEPT_RULES
from .state import DesignState, DesignObject, RecentOperation
from .telemetry import ContextTelemetry, estimate_tokens

__all__ = [
    "ContextBudget",
    "CompiledContext",
    "ContextCompiler",
    "ConversationContext",
    "Turn",
    "SessionMemory",
    "MemoryEntry",
    "ContextPlan",
    "ToolSelectionPlan",
    "tool_plan_for",
    "REASONING_BOOLEAN",
    "REASONING_DEFAULT",
    "REASONING_EXPORT",
    "REASONING_INSPECT",
    "REASONING_MODIFY",
    "REASONING_VAGUE",
    "RelevanceEngine",
    "RelevanceRule",
    "CONCEPT_RULES",
    "DesignState",
    "DesignObject",
    "RecentOperation",
    "ContextTelemetry",
    "estimate_tokens",
]
