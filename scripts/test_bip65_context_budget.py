#!/usr/bin/env python
"""BIP 6.5 — Context Budget Enforcement Test.

Validates that the ContextCompiler enforces the total context budget by
proportionally trimming sections when the estimated tokens exceed the maximum.
"""

from core.context.telemetry import ContextTelemetry
from core.context.plan import ContextPlan
from core.context.conversation import ConversationContext
from core.context.memory import SessionMemory
from core.context.state import DesignState
from core.context.budget import ContextBudget
from core.context.compiler import ContextCompiler, CompiledContext
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def test_compiler_enforces_total_budget():
    """Test that compile() trims sections when total exceeds maximum_context_tokens."""
    compiler = ContextCompiler()
    compiler.budget = ContextBudget(
        maximum_context_tokens=1000,  # Very small budget to force trimming
        reserved_output_tokens=100,
        state_budget=300,
        memory_budget=200,
        tool_budget=400,
        history_budget=200,
    )

    # Create a large state that will exceed budget
    design_state = DesignState()
    # Add many objects with large properties
    for i in range(50):
        from core.context.state import DesignObject
        obj = DesignObject(
            object_id=f"obj_{i}",
            object_type="Part::Box",
            label=f"Box{i}",
            properties={f"Prop{j}": float(j * 10) for j in range(20)},
        )
        design_state.objects[obj.object_id] = obj

    # Create many tools
    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "x" * 200,  # Large description
                "parameters": {"type": "object", "properties": {f"p{j}": {"type": "number"} for j in range(10)}}
            }
        }
        for i in range(30)
    ]

    # Compile - should trigger budget enforcement
    compiled = compiler.compile(
        user_message="Create a complex assembly with many parts",
        design_state=design_state,
        available_tools=tools,
        react_step=1,
    )

    # Verify total tokens don't exceed budget
    total_tokens = compiled.estimated_total_tokens()
    max_allowed = compiler.budget.maximum_context_tokens - \
        compiler.budget.reserved_output_tokens

    assert total_tokens <= max_allowed, \
        f"Total tokens {total_tokens} exceeds max allowed {max_allowed}"

    # Verify dropped_sections is recorded
    assert "tools_overflow" in compiled.telemetry.dropped_sections or \
           "state_objects_overflow" in compiled.telemetry.dropped_sections or \
           "memory_overflow" in compiled.telemetry.dropped_sections or \
           "history_overflow" in compiled.telemetry.dropped_sections

    print(f"✓ Total tokens: {total_tokens}, Max allowed: {max_allowed}")
    print(f"✓ Dropped sections: {compiled.telemetry.dropped_sections}")
    print("✓ test_compiler_enforces_total_budget passed")


def test_compiler_does_not_trim_when_under_budget():
    """Test that compile() does not trim when under budget."""
    compiler = ContextCompiler()
    compiler.budget = ContextBudget(
        maximum_context_tokens=50000,  # Large budget
        reserved_output_tokens=1000,
        state_budget=3000,
        memory_budget=800,
        tool_budget=4000,
        history_budget=2000,
    )

    # Small state and tools
    design_state = DesignState()
    design_state.objects = {
        "box1": type('obj', (), {
            'to_dict': lambda self, minimal=False: {"id": "box1", "type": "Part::Box"}
        })()
    }

    tools = [
        {
            "type": "function",
            "function": {
                "name": "box",
                "description": "Create a box",
                "parameters": {"type": "object", "properties": {}}
            }
        }
    ]

    compiled = compiler.compile(
        user_message="Create a box",
        design_state=design_state,
        available_tools=tools,
        react_step=1,
    )

    # Should not have any dropped sections
    assert len(compiled.telemetry.dropped_sections) == 0, \
        f"Expected no dropped sections, got {compiled.telemetry.dropped_sections}"

    print("✓ test_compiler_does_not_trim_when_under_budget passed")


def test_trim_tool_list():
    """Test _trim_tool_list helper."""
    compiler = ContextCompiler()

    tools = [
        {
            "type": "function",
            "function": {
                "name": f"tool_{i}",
                "description": "x" * 100,
                "parameters": {"type": "object", "properties": {}}
            }
        }
        for i in range(10)
    ]

    # Trim to force keeping only ~3 tools
    trimmed = compiler._trim_tool_list(tools, trim_tokens=2000)

    assert len(trimmed) < len(tools), "Should have trimmed tools"
    assert len(trimmed) >= 1, "Should keep at least 1 tool"

    print(f"✓ Original tools: {len(tools)}, Trimmed: {len(trimmed)}")
    print("✓ test_trim_tool_list passed")


def test_trim_tool_list_no_trim_needed():
    """Test _trim_tool_list when no trim needed."""
    compiler = ContextCompiler()

    tools = [
        {
            "type": "function",
            "function": {
                "name": "tool_1",
                "description": "Small tool",
                "parameters": {"type": "object", "properties": {}}
            }
        }
    ]

    trimmed = compiler._trim_tool_list(
        tools, trim_tokens=10)  # Very small trim

    assert len(trimmed) == 1

    print("✓ test_trim_tool_list_no_trim_needed passed")


def test_budget_records_dropped_sections():
    """Test that budget correctly records dropped sections."""
    budget = ContextBudget()

    budget.note_dropped("tools_overflow")
    budget.note_dropped("state_objects_overflow")
    budget.note_dropped("tools_overflow")  # Duplicate

    assert "tools_overflow" in budget.dropped_sections
    assert "state_objects_overflow" in budget.dropped_sections
    assert len(budget.dropped_sections) == 2  # No duplicates

    print("✓ test_budget_records_dropped_sections passed")


if __name__ == "__main__":
    test_compiler_enforces_total_budget()
    test_compiler_does_not_trim_when_under_budget()
    test_trim_tool_list()
    test_trim_tool_list_no_trim_needed()
    test_budget_records_dropped_sections()

    print("\n✓ All BIP 6.5 context budget enforcement tests passed!")
