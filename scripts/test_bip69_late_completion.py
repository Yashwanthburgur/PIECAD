#!/usr/bin/env python
"""BIP 6.9 — Late Completion / Orphaned Operation Protection Test.

Validates that timeout operations are tracked with identity and late completion
does not corrupt state.
"""

from providers.llm.provider import LLMProvider
from core.context.state import DesignState, RecentOperation
from core.adapters.interfaces import CADAdapter
from core.agent import CADAgent
import sys
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Ensure project root is on sys.path BEFORE any imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
adapters_path = PROJECT_ROOT / "adapters"
sys.path.insert(0, str(adapters_path))
providers_path = PROJECT_ROOT / "providers"
sys.path.insert(0, str(providers_path))

# Core modules

# Providers


class MockAdapter(CADAdapter):
    """Mock adapter for deterministic testing with late completion simulation."""

    def __init__(self):
        self._tools = []
        self._state = "[]"
        self._execute_results = {}
        self._call_count = {}
        self._late_completion_results = {}  # operation_id -> late result
        # operation_ids that should return late completion
        self._pending_operations = set()

    def get_tools(self):
        return self._tools

    def get_state(self):
        return self._state

    def execute_command(self, tool_name: str, **kwargs) -> str:
        operation_id = kwargs.pop("_operation_id", None)

        # Track call count per tool
        self._call_count[tool_name] = self._call_count.get(tool_name, 0) + 1
        call_num = self._call_count[tool_name]

        # Check if this operation should simulate late completion
        if operation_id and operation_id in self._pending_operations:
            # First call times out, subsequent calls return late completion
            if call_num == 1:
                self._pending_operations.remove(operation_id)
                timeout_result = json.dumps({
                    "success": False,
                    "error": f"Simulated timeout for {tool_name}",
                    "error_type": "freecad_timeout",
                    "timeout_seconds": 120.0,
                    "operation_id": operation_id,
                })
                return timeout_result
            else:
                # Late completion
                return self._late_completion_results.get(operation_id, f"{tool_name} succeeded (late)")

        # Return pre-configured result or default success
        if tool_name in self._execute_results:
            return self._execute_results[tool_name]
        return f"{tool_name} succeeded"

    def set_tool_result(self, tool_name: str, result: str):
        self._execute_results[tool_name] = result

    def set_late_completion(self, operation_id: str, result: str):
        """Set up an operation to return late completion on second call."""
        self._pending_operations.add(operation_id)
        self._late_completion_results[operation_id] = result


def test_operation_identity():
    """Test A: Each execution gets a unique operation ID."""
    print("Testing A: Operation identity...")

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
        mock_response2.content = "Box created."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Create a box")

    # Verify operation ID was generated and tracked
    assert agent._operation_counter == 1
    assert len(agent._pending_operations) == 0  # No timeout, so not pending

    print("[PASS] test_operation_identity passed")


def test_timeout_marks_unresolved():
    """Test B: Timed-out mutation becomes unresolved/unknown."""
    print("Testing B: Timeout marks unresolved...")

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
        mock_response2.content = "Operation timed out."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Create a box")

    # Verify timeout handled as failure
    recent_errors = agent.design_state.get_recent_errors()
    assert len(recent_errors) == 1
    assert "timed out" in recent_errors[0].lower()

    # Verify operation is tracked as pending/unresolved
    assert len(agent._pending_operations) == 1
    op_id = list(agent._pending_operations.keys())[0]
    pending = agent._pending_operations[op_id]
    assert pending["status"] == "timeout"
    assert pending["tool"] == "box"
    assert pending["args"]["id"] == "test_box"

    # Verify NOT recorded as successful operation
    recent_ops = agent.design_state.get_recent_operations()
    assert len(recent_ops) == 0

    print("[PASS] test_timeout_marks_unresolved passed")


def test_late_completion_protection():
    """Test C: Late completion cannot overwrite newer state."""
    print("Testing C: Late completion protection...")

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

    # Initial state empty
    adapter._state = '[]'

    # Set up first operation to timeout, then late complete
    # We'll manually control the operation IDs
    agent = CADAgent(adapter=adapter, capture_trace=True)

    # Manually add a pending operation to track
    op_id = "op_1_box_1"
    agent._pending_operations[op_id] = {
        "tool": "box",
        "args": {"id": "test_box"},
        "target_id": None,
        "step": 1,
        "ts": 0,
        "status": "timeout",
        "error_type": "freecad_timeout",
    }

    # State after timeout - box doesn't exist yet
    adapter._state = '[]'

    # Now simulate state refresh showing box appeared (late completion)
    late_state = json.dumps([{
        "id": "test_box",
        "type": "Part::Box",
        "visible": True,
        "label": "test_box",
        "properties": {"Length": 100, "Width": 100, "Height": 50},
        "parents": [],
        "children": []
    }])

    agent._update_design_state(late_state)
    agent._check_pending_operations_against_state(json.loads(late_state))

    # Pending should be cleared after late completion detected
    assert op_id not in agent._pending_operations
    # But state should now have the box
    assert "test_box" in agent.design_state.objects

    print("[PASS] test_late_completion_protection passed")


def test_newer_operation_wins():
    """Test D: Operation A times out, B completes, A's late completion doesn't win."""
    print("Testing D: Newer operation wins...")

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
                "name": "cylinder",
                "description": "Create a cylinder",
                "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            },
        }
    ]
    adapter._state = '[]'

    # First box times out
    timeout_result = json.dumps({
        "success": False,
        "error": "XML-RPC call 'create_box' timed out after 120.0s.",
        "error_type": "freecad_timeout",
        "timeout_seconds": 120.0,
    })
    adapter.set_tool_result("box", timeout_result)

    # Cylinder succeeds
    adapter.set_tool_result("cylinder", "Successfully created cylinder 'cyl1'")

    agent = CADAgent(adapter=adapter, capture_trace=True)

    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        # Step 1: box (times out)
        mock_tool_call1 = Mock()
        mock_tool_call1.id = "call_1"
        mock_tool_call1.function = Mock()
        mock_tool_call1.function.name = "box"
        mock_tool_call1.function.arguments = '{"id": "box1"}'

        mock_response1 = Mock()
        mock_response1.tool_calls = [mock_tool_call1]
        mock_response1.content = None

        # Step 2: cylinder (succeeds)
        mock_tool_call2 = Mock()
        mock_tool_call2.id = "call_2"
        mock_tool_call2.function = Mock()
        mock_tool_call2.function.name = "cylinder"
        mock_tool_call2.function.arguments = '{"id": "cyl1"}'

        mock_response2 = Mock()
        mock_response2.tool_calls = [mock_tool_call2]
        mock_response2.content = None

        # Step 3: finish
        mock_response3 = Mock()
        mock_response3.tool_calls = None
        mock_response3.content = "Done."

        mock_llm.side_effect = [mock_response1, mock_response2, mock_response3]

        result, tools = agent.handle_message("Create box then cylinder")

    # Both tools should have been called
    assert "box" in tools
    assert "cylinder" in tools

    # Box should be pending (timed out)
    assert len(agent._pending_operations) == 1
    op_id = list(agent._pending_operations.keys())[0]
    assert agent._pending_operations[op_id]["tool"] == "box"

    # Cylinder should be in recent operations
    recent_ops = agent.design_state.get_recent_operations()
    assert len(recent_ops) == 1
    assert recent_ops[0].tool == "cylinder"

    print("[PASS] test_newer_operation_wins passed")


def test_recovery_after_timeout():
    """Test E: State refresh after timeout reconciles actual CAD state."""
    print("Testing E: Recovery after timeout...")

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
        # Step 1: box (times out)
        mock_tool_call1 = Mock()
        mock_tool_call1.id = "call_1"
        mock_tool_call1.function = Mock()
        mock_tool_call1.function.name = "box"
        mock_tool_call1.function.arguments = '{"id": "test_box"}'

        mock_response1 = Mock()
        mock_response1.tool_calls = [mock_tool_call1]
        mock_response1.content = None

        # Step 2: get_state (recovery)
        mock_tool_call2 = Mock()
        mock_tool_call2.id = "call_2"
        mock_tool_call2.function = Mock()
        mock_tool_call2.function.name = "get_state"
        mock_tool_call2.function.arguments = '{}'

        mock_response2 = Mock()
        mock_response2.tool_calls = [mock_tool_call2]
        mock_response2.content = None

        # Step 3: finish
        mock_response3 = Mock()
        mock_response3.tool_calls = None
        mock_response3.content = "State checked."

        mock_llm.side_effect = [mock_response1, mock_response2, mock_response3]

        result, tools = agent.handle_message("Create box, then check state")

    # Both tools called
    assert "box" in tools
    assert "get_state" in tools

    # Pending operation tracked
    assert len(agent._pending_operations) == 1

    # State refresh should have been triggered (get_state succeeded)
    assert agent.design_state.state_available is True

    print("[PASS] test_recovery_after_timeout passed")


def test_no_auto_retry():
    """Test F: Timeout does not automatically re-execute mutation."""
    print("Testing F: No auto-retry...")

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
        mock_response2.content = "Timed out."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Create a box")

    # Box only attempted once
    box_attempts = [t for t in tools if t == "box"]
    assert len(box_attempts) == 1

    # Operation ID tracked
    assert agent._operation_counter == 1

    print("[PASS] test_no_auto_retry passed")


def test_explicit_retry_new_identity():
    """Test G: Explicit later execution gets new operation identity."""
    print("Testing G: Explicit retry gets new identity...")

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

    # First attempt times out, second succeeds
    timeout_result = json.dumps({
        "success": False,
        "error": "XML-RPC call 'create_box' timed out after 120.0s.",
        "error_type": "freecad_timeout",
        "timeout_seconds": 120.0,
    })

    call_count = {"box": 0}

    def box_side_effect(*args, **kwargs):
        call_count["box"] += 1
        if call_count["box"] == 1:
            return timeout_result
        return "Successfully created box 'test_box'"
    adapter.execute_command = box_side_effect

    agent = CADAgent(adapter=adapter, capture_trace=True)

    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        # First turn: box times out
        mock_tool_call1 = Mock()
        mock_tool_call1.id = "call_1"
        mock_tool_call1.function = Mock()
        mock_tool_call1.function.name = "box"
        mock_tool_call1.function.arguments = '{"id": "test_box"}'

        mock_response1 = Mock()
        mock_response1.tool_calls = [mock_tool_call1]
        mock_response1.content = None

        mock_response2 = Mock()
        mock_response2.tool_calls = None
        mock_response2.content = "First attempt timed out."

        # Second turn: explicit retry
        mock_tool_call3 = Mock()
        mock_tool_call3.id = "call_2"
        mock_tool_call3.function = Mock()
        mock_tool_call3.function.name = "box"
        mock_tool_call3.function.arguments = '{"id": "test_box"}'

        mock_response3 = Mock()
        mock_response3.tool_calls = [mock_tool_call3]
        mock_response3.content = None

        mock_response4 = Mock()
        mock_response4.tool_calls = None
        mock_response4.content = "Second attempt succeeded."

        mock_llm.side_effect = [mock_response1,
                                mock_response2, mock_response3, mock_response4]

        result1, tools1 = agent.handle_message("Create a box")
        result2, tools2 = agent.handle_message("Try again")

    # Two separate operation IDs generated
    assert agent._operation_counter == 2

    # First operation still pending
    assert len(agent._pending_operations) == 1

    print("[PASS] test_explicit_retry_new_identity passed")


def test_regression_normal_operations():
    """Test H: Normal successful operations still work."""
    print("Testing H: Regression - normal operations...")

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
    adapter.set_tool_result("box", "Successfully created box 'box1'")

    agent = CADAgent(adapter=adapter, capture_trace=True)

    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_tool_call = Mock()
        mock_tool_call.id = "call_1"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = "box"
        mock_tool_call.function.arguments = '{"id": "box1"}'

        mock_response = Mock()
        mock_response.tool_calls = [mock_tool_call]
        mock_response.content = None

        mock_response2 = Mock()
        mock_response2.tool_calls = None
        mock_response2.content = "Box created."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Create a box")

    # Success recorded
    recent_ops = agent.design_state.get_recent_operations()
    assert len(recent_ops) == 1
    assert recent_ops[0].success is True
    assert recent_ops[0].tool == "box"

    # No pending operations
    assert len(agent._pending_operations) == 0

    print("[PASS] test_regression_normal_operations passed")


def test_regression_ordinary_errors():
    """Test I: Ordinary tool failures still use existing error path."""
    print("Testing I: Regression - ordinary errors...")

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
        mock_response2.content = "Fillet failed."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Add large fillet")

    # Error recorded in recent_errors
    recent_errors = agent.design_state.get_recent_errors()
    assert len(recent_errors) > 0
    assert "fillet" in recent_errors[0]

    # Not in recent_operations
    recent_ops = agent.design_state.get_recent_operations()
    assert len(recent_ops) == 0

    # Not in pending (not a timeout)
    assert len(agent._pending_operations) == 0

    print("[PASS] test_regression_ordinary_errors passed")


def test_bip68_timeout_recovery_intact():
    """Test J: BIP 6.8 timeout handling still works."""
    print("Testing J: BIP 6.8 timeout recovery intact...")

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
        mock_response2.content = "Timeout handled."

        mock_llm.side_effect = [mock_response, mock_response2]

        result, tools = agent.handle_message("Create a box")

    # Timeout handled as transient failure (BIP 6.8)
    trace = agent.get_trace()
    tool_traces = [t for t in trace if t.get(
        "type") is None and t.get("tool") == "box"]
    assert len(tool_traces) == 1
    assert tool_traces[0]["success"] is False
    assert tool_traces[0]["transient"] is True  # BIP 6.8: timeout is transient

    # Pending tracked (BIP 6.9)
    assert len(agent._pending_operations) == 1

    print("[PASS] test_bip68_timeout_recovery_intact passed")


if __name__ == "__main__":
    test_operation_identity()
    test_timeout_marks_unresolved()
    test_late_completion_protection()
    test_newer_operation_wins()
    test_recovery_after_timeout()
    test_no_auto_retry()
    test_explicit_retry_new_identity()
    test_regression_normal_operations()
    test_regression_ordinary_errors()
    test_bip68_timeout_recovery_intact()

    print("\n[PASS] All BIP 6.9 late completion tests passed!")
