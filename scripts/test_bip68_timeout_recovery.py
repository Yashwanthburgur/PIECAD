#!/usr/bin/env python
"""BIP 6.8 — Agent-Level Operation Cancellation / Recovery Test.

Validates that timeout failures are correctly handled as recoverable tool failures
at the agent layer, without corrupting state or falsely declaring success.
"""

from providers.llm.provider import LLMProvider
from core.context.state import DesignState, RecentOperation
from core.adapters.interfaces import CADAdapter
from core.agent import CADAgent
from unittest.mock import Mock, patch, MagicMock
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path BEFORE any imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
adapters_path = PROJECT_ROOT / "adapters"
sys.path.insert(0, str(adapters_path))
providers_path = PROJECT_ROOT / "providers"
sys.path.insert(0, str(providers_path))

# Now import modules

# Core modules

# Providers


class MockAdapter(CADAdapter):
    """Mock adapter for deterministic testing."""

    def __init__(self):
        self._tools = []
        self._state = "[]"
        self._execute_results = {}

    def get_tools(self):
        return self._tools

    def get_state(self):
        return self._state

    def execute_command(self, tool_name: str, **kwargs) -> str:
        # Return pre-configured result or default success
        if tool_name in self._execute_results:
            return self._execute_results[tool_name]
        return f"{tool_name} succeeded"

    def set_tool_result(self, tool_name: str, result: str):
        self._execute_results[tool_name] = result


def test_direct_freecad_timeout():
    """Test A: Direct FreeCAD timeout is surfaced as structured failure."""
    print("Testing A: Direct FreeCAD timeout...")

    adapter = MockAdapter()
    adapter._tools = [
        {
            "type": "function",
            "function": {
                "name": "box",
                "description": "Create a box",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            },
        }
    ]
    adapter._state = '[]'

    # Simulate a FreeCAD timeout (BIP 6.6 returns structured JSON error)
    timeout_result = json.dumps({
        "success": False,
        "error": "XML-RPC call 'create_box' timed out after 120.0s.",
        "error_type": "freecad_timeout",
        "timeout_seconds": 120.0,
    })
    adapter.set_tool_result("box", timeout_result)

    agent = CADAgent(adapter=adapter, capture_trace=True)

    # Mock LLM to call box tool
    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        # First call: LLM requests box tool
        mock_tool_call = Mock()
        mock_tool_call.id = "call_123"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "box"
        mock_tool_call.function.arguments = '{"id": "test_box"}'

        mock_response = Mock()
        mock_response.tool_calls = [mock_tool_call]
        mock_response.content = None

        # Second call: LLM receives error and finishes
        mock_response2 = Mock()
        mock_response2.tool_calls = None
        mock_response2.content = "Operation failed due to timeout."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Create a box")

    # Verify timeout was handled as failure, not success
    assert "timeout" in result.lower() or "failed" in result.lower(
    ), f"Agent should not declare success: {result}"

    # Verify DesignState recorded failure in recent_errors
    recent_errors = agent.design_state.get_recent_errors()
    assert len(
        recent_errors) == 1, f"Expected 1 recent error, got: {recent_errors}"
    assert "box" in recent_errors[0], f"Error should mention box tool: {recent_errors[0]}"
    assert "timed out" in recent_errors[0].lower(
    ), f"Error should mention timed out: {recent_errors[0]}"

    # Verify recent_operations does NOT contain the failed operation
    recent_ops = agent.design_state.get_recent_operations()
    assert len(
        recent_ops) == 0, "Failed operations should not be in recent_operations"

    # Verify state_available not incorrectly updated
    assert agent.design_state.state_available is True  # State retrieval succeeded

    print("[PASS] test_direct_freecad_timeout passed")


def test_mcp_timeout():
    """Test B: MCP timeout reaches agent as recoverable tool failure."""
    print("Testing B: MCP timeout...")

    adapter = MockAdapter()
    adapter._tools = [
        {
            "type": "function",
            "function": {
                "name": "partdesign_sketch_constraint",
                "description": "Add constraint",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            },
        }
    ]
    adapter._state = '[]'

    # Simulate an MCP timeout (BIP 6.7 returns structured JSON error)
    timeout_result = json.dumps({
        "success": False,
        "error": "MCP tool 'partdesign_sketch_constraint' timed out after 120.0s.",
        "error_type": "mcp_timeout",
        "timeout_seconds": 120.0,
    })
    adapter.set_tool_result("partdesign_sketch_constraint", timeout_result)

    agent = CADAgent(adapter=adapter, capture_trace=True)

    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_tool_call = Mock()
        mock_tool_call.id = "call_456"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "partdesign_sketch_constraint"
        mock_tool_call.function.arguments = '{"id": "constraint_1"}'

        mock_response = Mock()
        mock_response.tool_calls = [mock_tool_call]
        mock_response.content = None

        mock_response2 = Mock()
        mock_response2.tool_calls = None
        mock_response2.content = "Constraint operation timed out."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Add a constraint")

    # Verify timeout was handled as failure
    assert "timed out" in result.lower() or "failed" in result.lower(
    ), f"Agent should not declare success: {result}"

    # Verify DesignState recorded failure in recent_errors
    recent_errors = agent.design_state.get_recent_errors()
    assert len(
        recent_errors) == 1, f"Expected 1 recent error, got: {recent_errors}"
    assert "partdesign_sketch_constraint" in recent_errors[0]
    assert "timed out" in recent_errors[0].lower()

    # Verify recent_operations does NOT contain the failed operation
    recent_ops = agent.design_state.get_recent_operations()
    assert len(
        recent_ops) == 0, "Failed operations should not be in recent_operations"

    print("[PASS] test_mcp_timeout passed")


def test_unknown_state_after_timeout():
    """Test C: After timeout where completion unknown, state reflects uncertainty."""
    print("Testing C: Unknown-state behavior...")

    adapter = MockAdapter()
    adapter._tools = [
        {
            "type": "function",
            "function": {
                "name": "box",
                "description": "Create a box",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            },
        }
    ]

    # Simulate state after timeout - we don't know if box was created
    adapter._state = '[]'

    timeout_result = json.dumps({
        "success": False,
        "error": "XML-RPC call 'create_box' timed out after 120.0s.",
        "error_type": "freecad_timeout",
        "timeout_seconds": 120.0,
    })
    adapter.set_tool_result("box", timeout_result)

    agent = CADAgent(adapter=adapter, capture_trace=True)

    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_tool_call = Mock()
        mock_tool_call.id = "call_789"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "box"
        mock_tool_call.function.arguments = '{"id": "test_box"}'

        mock_response = Mock()
        mock_response.tool_calls = [mock_tool_call]
        mock_response.content = None

        mock_response2 = Mock()
        mock_response2.tool_calls = None
        mock_response2.content = "Timeout occurred, state uncertain."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Create a box")

    # The agent should NOT have added the box to DesignState
    assert "test_box" not in agent.design_state.objects, "Timeout should not add object to state"

    # State should still be available (retrieval succeeded)
    assert agent.design_state.state_available is True

    print("[PASS] test_unknown_state_after_timeout passed")


def test_recovery_after_timeout():
    """Test D: Safe state refresh/query can occur after timeout."""
    print("Testing D: Recovery after timeout...")

    adapter = MockAdapter()
    adapter._tools = [
        {
            "type": "function",
            "function": {
                "name": "box",
                "description": "Create a box",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_state",
                "description": "Get CAD state",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    # First box attempt times out
    timeout_result = json.dumps({
        "success": False,
        "error": "XML-RPC call 'create_box' timed out after 120.0s.",
        "error_type": "freecad_timeout",
        "timeout_seconds": 120.0,
    })
    adapter.set_tool_result("box", timeout_result)

    # But state retrieval works
    adapter._state = '[]'

    agent = CADAgent(adapter=adapter, capture_trace=True)

    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        # Step 1: Try to create box (times out)
        mock_tool_call1 = Mock()
        mock_tool_call1.id = "call_1"
        mock_tool_call1.function = Mock()
        mock_tool_call1.function.name = "box"
        mock_tool_call1.function.arguments = '{"id": "test_box"}'

        mock_response1 = Mock()
        mock_response1.tool_calls = [mock_tool_call1]
        mock_response1.content = None

        # Step 2: LLM decides to query state (safe recovery)
        mock_tool_call2 = Mock()
        mock_tool_call2.id = "call_2"
        mock_tool_call2.function = Mock()
        mock_tool_call2.function.name = "get_state"
        mock_tool_call2.function.arguments = '{}'

        mock_response2 = Mock()
        mock_response2.tool_calls = [mock_tool_call2]
        mock_response2.content = None

        # Step 3: LLM finishes
        mock_response3 = Mock()
        mock_response3.tool_calls = None
        mock_response3.content = "State queried after timeout."

        mock_llm.side_effect = [mock_response1, mock_response2, mock_response3]

        result, tools = agent.handle_message("Create a box, then check state")

    # Verify both tools were called
    assert "box" in tools
    assert "get_state" in tools

    # Verify state was refreshed (get_state doesn't modify objects)
    assert agent.design_state.state_available is True

    print("[PASS] test_recovery_after_timeout passed")


def test_no_auto_retry_mutation():
    """Test E: Mutation is not automatically retried after timeout."""
    print("Testing E: No auto-retry of mutation...")

    adapter = MockAdapter()
    adapter._tools = [
        {
            "type": "function",
            "function": {
                "name": "box",
                "description": "Create a box",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            },
        }
    ]
    adapter._state = '[]'

    # First attempt times out
    timeout_result = json.dumps({
        "success": False,
        "error": "XML-RPC call 'create_box' timed out after 120.0s.",
        "error_type": "freecad_timeout",
        "timeout_seconds": 120.0,
    })
    adapter.set_tool_result("box", timeout_result)

    agent = CADAgent(adapter=adapter, capture_trace=True)

    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_tool_call = Mock()
        mock_tool_call.id = "call_1"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "box"
        mock_tool_call.function.arguments = '{"id": "test_box"}'

        mock_response = Mock()
        mock_response.tool_calls = [mock_tool_call]
        mock_response.content = None

        mock_response2 = Mock()
        mock_response2.tool_calls = None
        mock_response2.content = "Box creation timed out."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Create a box")

    # Verify box was only attempted ONCE (no auto-retry)
    # The LLM could choose to retry, but the agent doesn't auto-retry
    box_attempts = [t for t in tools if t == "box"]
    assert len(
        box_attempts) == 1, f"Mutation should not be auto-retried: {tools}"

    print("[PASS] test_no_auto_retry_mutation passed")


def test_regression_ordinary_tool_error():
    """Test F: Existing ordinary tool errors still follow existing recovery path."""
    print("Testing F: Regression - ordinary tool errors...")

    adapter = MockAdapter()
    adapter._tools = [
        {
            "type": "function",
            "function": {
                "name": "fillet",
                "description": "Fillet edge",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "target_id": {"type": "string"}, "edge_refs": {"type": "array"}, "radius": {"type": "number"}}, "required": ["id", "target_id", "edge_refs", "radius"]},
            },
        }
    ]
    adapter._state = '[]'

    # Simulate a regular CAD kernel error (non-timeout) - raise RuntimeError
    def raise_fillet_error(*args, **kwargs):
        raise RuntimeError("BRep_API: command not done - radius too large")
    adapter.execute_command = raise_fillet_error

    agent = CADAgent(adapter=adapter, capture_trace=True)

    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_tool_call = Mock()
        mock_tool_call.id = "call_1"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "fillet"
        mock_tool_call.function.arguments = '{"id": "fillet1", "target_id": "box1", "edge_refs": ["edge1"], "radius": 100}'

        mock_response = Mock()
        mock_response.tool_calls = [mock_tool_call]
        mock_response.content = None

        mock_response2 = Mock()
        mock_response2.tool_calls = None
        mock_response2.content = "Fillet failed, radius too large."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Add large fillet")

    # Verify error was handled as non-transient failure (in recent_errors)
    recent_errors = agent.design_state.get_recent_errors()
    assert len(recent_errors) > 0
    assert "fillet" in recent_errors[0]

    # Verify recent_operations does NOT contain the failed operation
    recent_ops = agent.design_state.get_recent_operations()
    assert len(
        recent_ops) == 0, "Failed operations should not be in recent_operations"

    print("[PASS] test_regression_ordinary_tool_error passed")


def test_timeout_marked_transient_for_retry():
    """Test G: Timeout errors are marked transient so LLM can retry if desired."""
    print("Testing G: Timeout marked transient...")

    adapter = MockAdapter()
    adapter._tools = [
        {
            "type": "function",
            "function": {
                "name": "box",
                "description": "Create a box",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            },
        }
    ]
    adapter._state = '[]'

    timeout_result = json.dumps({
        "success": False,
        "error": "XML-RPC call 'create_box' timed out after 120.0s.",
        "error_type": "freecad_timeout",
        "timeout_seconds": 120.0,
    })
    adapter.set_tool_result("box", timeout_result)

    agent = CADAgent(adapter=adapter, capture_trace=True)

    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_tool_call = Mock()
        mock_tool_call.id = "call_1"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "box"
        mock_tool_call.function.arguments = '{"id": "test_box"}'

        mock_response = Mock()
        mock_response.tool_calls = [mock_tool_call]
        mock_response.content = None

        mock_response2 = Mock()
        mock_response2.tool_calls = None
        mock_response2.content = "Timeout."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Create a box")

    # Check trace for transient flag
    trace = agent.get_trace()
    tool_traces = [t for t in trace if t.get(
        "type") is None and t.get("tool") == "box"]
    assert len(tool_traces) == 1
    trace_entry = tool_traces[0]
    assert trace_entry["success"] is False
    assert trace_entry["transient"] is True, "Timeout should be marked transient"

    print("[PASS] test_timeout_marked_transient_for_retry passed")


if __name__ == "__main__":
    test_direct_freecad_timeout()
    test_mcp_timeout()
    test_unknown_state_after_timeout()
    test_recovery_after_timeout()
    test_no_auto_retry_mutation()
    test_regression_ordinary_tool_error()
    test_timeout_marked_transient_for_retry()

    print("\n[PASS] All BIP 6.8 timeout/recovery tests passed!")
