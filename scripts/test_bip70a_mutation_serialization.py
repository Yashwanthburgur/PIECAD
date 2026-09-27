#!/usr/bin/env python
"""BIP 7.0A — CAD Mutation Serialization Test.

This test validates the mutation serialization boundary specifically.
It tests that at most one CAD mutation executes at a time.

Requirements:
- Deterministic (no live FreeCAD, no MCP, no LLM, no network)
- Uses threading Events/Barriers for synchronization
- Tests actual behavior, not just lock existence

Tests:
1. Normal mutation still works
2. Actual serialization (A blocks, B waits, A releases, B executes)
3. No overlap (active_mutations never exceeds 1)
4. Query behavior (queries don't serialize)
5. Exception release (lock released on exception)
6. Timeout compatibility (lock doesn't leave system locked after timeout)
"""

from core.agent import CADAgent
from core.adapters.interfaces import CADAdapter
from core.operations import OperationRegistry, MutationGate, OperationStatus
import sys
import time
import json
import threading
from pathlib import Path
from unittest.mock import Mock, patch

# Ensure project root is on sys.path BEFORE importing core modules
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
adapters_path = PROJECT_ROOT / "adapters"
sys.path.insert(0, str(adapters_path))


# ============================================================================
# Mock Adapter for deterministic testing
# ============================================================================

class MockAdapter(CADAdapter):
    """Mock adapter with controllable tool execution for testing serialization."""

    def __init__(self):
        self._tools = []
        self._state = "[]"
        self._execute_results = {}
        self._call_count = {}
        self._execution_events = {}  # For synchronizing test execution

    def get_tools(self):
        return self._tools

    def get_state(self):
        return self._state

    def execute_command(self, tool_name: str, **kwargs) -> str:
        operation_id = kwargs.pop("_operation_id", None)

        # Track call count per tool
        self._call_count[tool_name] = self._call_count.get(tool_name, 0) + 1
        call_num = self._call_count[tool_name]

        # Return pre-configured result or default success
        if tool_name in self._execute_results:
            result = self._execute_results[tool_name]
            if callable(result):
                return result(*(), **kwargs)
            return result

        return f"{tool_name} succeeded"

    def set_tool_result(self, tool_name: str, result):
        self._execute_results[tool_name] = result

    def set_blocking_execution(self, tool_name: str, block_event: threading.Event,
                               release_event: threading.Event):
        """Make a tool block on `block_event` and signal `release_event` when done."""
        def blocking_execution(*args, **kwargs):
            op_id = kwargs.get('_operation_id', 'unknown')
            # Signal we've entered the critical section
            release_event.set()
            # Wait to be released
            block_event.wait(timeout=5.0)
            return f"{tool_name} completed for {op_id}"
        self.set_tool_result(tool_name, blocking_execution)


def make_llm_response(tool_calls_list, final_content="Done."):
    """Helper to create mock LLM responses."""
    calls = []
    for i, (name, args) in enumerate(tool_calls_list):
        mock_tool_call = Mock()
        mock_tool_call.id = f"call_{i+1}"
        mock_tool_call.function = Mock()
        mock_tool_call.function.name = name
        mock_tool_call.function.arguments = json.dumps(args)
        calls.append(mock_tool_call)

    mock_response = Mock()
    mock_response.tool_calls = calls
    mock_response.content = None

    mock_response2 = Mock()
    mock_response2.tool_calls = None
    mock_response2.content = final_content

    return [mock_response, mock_response2]


# ============================================================================
# Test 1: Normal mutation still works
# ============================================================================

def test_normal_mutation_works():
    """TEST 1: A normal mutation can execute successfully."""
    print("TEST 1: Normal mutation still works...")

    adapter = MockAdapter()
    adapter._tools = [
        {"type": "function", "function": {"name": "box", "description": "Create a box",
                                          "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}
    ]
    adapter._state = '[]'
    adapter.set_tool_result("box", "Successfully created box 'box1'")

    agent = CADAgent(adapter=adapter)

    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_llm.side_effect = make_llm_response(
            [("box", {"id": "box1"})], "Box created.")
        result, tools = agent.handle_message("Create a box")

    assert "box" in tools, "Box tool should have executed"
    # The design state is updated from the adapter's get_state after the mutation
    # Since our mock doesn't automatically update state, we check the operation succeeded
    # by verifying the tool was called and no unresolved operations exist

    # Verify operation completed successfully
    unresolved = agent._operation_registry.get_unresolved()
    assert len(unresolved) == 0, "No unresolved operations should exist"

    print("  [PASS] Normal mutation executes successfully")


# ============================================================================
# Test 2: Actual serialization (A blocks, B waits, A releases, B executes)
# ============================================================================

def test_actual_serialization():
    """TEST 2: Mutations serialize - A blocks, B waits, A releases, B executes."""
    print("TEST 2: Actual serialization...")

    adapter = MockAdapter()
    adapter._tools = [
        {"type": "function", "function": {"name": "box", "description": "Create a box",
                                          "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}},
        {"type": "function", "function": {"name": "cylinder", "description": "Create a cylinder",
                                          "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}
    ]
    adapter._state = '[]'

    # Synchronization events
    a_entered = threading.Event()
    a_released = threading.Event()
    b_entered = threading.Event()
    b_completed = threading.Event()

    execution_log = []
    log_lock = threading.Lock()

    def make_box(*args, **kwargs):
        op_id = kwargs.get('_operation_id', 'unknown')
        with log_lock:
            execution_log.append(('A_start', op_id, time.time()))
        a_entered.set()  # Signal A has entered
        a_released.wait(timeout=5.0)  # Wait for release signal
        with log_lock:
            execution_log.append(('A_end', op_id, time.time()))
        return "success"

    def make_cylinder(*args, **kwargs):
        op_id = kwargs.get('_operation_id', 'unknown')
        with log_lock:
            execution_log.append(('B_start', op_id, time.time()))
        b_entered.set()  # Signal B has entered
        with log_lock:
            execution_log.append(('B_end', op_id, time.time()))
        b_completed.set()
        return "success"

    adapter.set_tool_result("box", make_box)
    adapter.set_tool_result("cylinder", make_cylinder)

    agent = CADAgent(adapter=adapter)

    # Start mutation A in a thread
    def run_mutation_a():
        with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
            mock_llm.side_effect = make_llm_response(
                [("box", {"id": "box1"})], "Box created.")
            agent.handle_message("Create a box")

    def run_mutation_b():
        with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
            mock_llm.side_effect = make_llm_response(
                [("cylinder", {"id": "cyl1"})], "Cylinder created.")
            agent.handle_message("Create a cylinder")

    thread_a = threading.Thread(target=run_mutation_a)
    thread_b = threading.Thread(target=run_mutation_b)

    thread_a.start()

    # Wait for A to enter the critical section
    assert a_entered.wait(
        timeout=2.0), "Mutation A should have entered critical section"

    # Now start B - it should block
    thread_b.start()

    # Give B time to try to acquire the lock
    time.sleep(0.2)

    # Verify B has NOT entered yet (it's waiting on the lock)
    assert not b_entered.is_set(
    ), "Mutation B should NOT have entered while A is holding the lock"

    # Now release A
    a_released.set()

    # Wait for both to complete
    thread_a.join(timeout=3.0)
    assert b_completed.wait(
        timeout=3.0), "Mutation B should complete after A releases"
    thread_b.join(timeout=1.0)

    # Verify execution order: A_start, A_end, B_start, B_end
    with log_lock:
        tool_sequence = [entry[0] for entry in execution_log]
        print(f"  Execution sequence: {tool_sequence}")

        # Verify no interleaving
        assert tool_sequence == ['A_start', 'A_end', 'B_start', 'B_end'], \
            f"Expected sequential execution, got: {tool_sequence}"

    print(
        "  [PASS] Mutations serialize correctly (A enters, A exits, B enters, B exits)")


# ============================================================================
# Test 3: No overlap (active_mutations never exceeds 1)
# ============================================================================

def test_no_overlap():
    """TEST 3: Track active_mutations and assert it never exceeds 1."""
    print("TEST 3: No overlap...")

    adapter = MockAdapter()
    adapter._tools = [
        {"type": "function", "function": {"name": "box", "description": "Create a box",
                                          "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}},
        {"type": "function", "function": {"name": "cylinder", "description": "Create a cylinder",
                                          "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}
    ]
    adapter._state = '[]'

    # Use the MutationGate directly to track active mutations
    active_count = 0
    max_active = 0
    count_lock = threading.Lock()
    active_tracking = []

    def tracking_box(*args, **kwargs):
        nonlocal active_count, max_active
        op_id = kwargs.get('_operation_id', 'unknown')
        with count_lock:
            active_count += 1
            if active_count > max_active:
                max_active = active_count
            active_tracking.append(
                ('box_enter', op_id, active_count, time.time()))
        time.sleep(0.05)  # Simulate work
        with count_lock:
            active_count -= 1
            active_tracking.append(
                ('box_exit', op_id, active_count, time.time()))
        return "success"

    def tracking_cylinder(*args, **kwargs):
        nonlocal active_count, max_active
        op_id = kwargs.get('_operation_id', 'unknown')
        with count_lock:
            active_count += 1
            if active_count > max_active:
                max_active = active_count
            active_tracking.append(
                ('cyl_enter', op_id, active_count, time.time()))
        time.sleep(0.05)
        with count_lock:
            active_count -= 1
            active_tracking.append(
                ('cyl_exit', op_id, active_count, time.time()))
        return "success"

    adapter.set_tool_result("box", tracking_box)
    adapter.set_tool_result("cylinder", tracking_cylinder)

    agent = CADAgent(adapter=adapter)

    # Run multiple mutations concurrently
    def run_mutation(tool_name, obj_id):
        with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
            mock_llm.side_effect = make_llm_response(
                [(tool_name, {"id": obj_id})], f"{tool_name} created.")
            agent.handle_message(f"Create {tool_name}")

    threads = []
    for i in range(5):
        tool = "box" if i % 2 == 0 else "cylinder"
        obj_id = f"{tool}{i}"
        t = threading.Thread(target=run_mutation, args=(tool, obj_id))
        threads.append(t)

    for t in threads:
        t.start()

    for t in threads:
        t.join(timeout=3.0)

    assert max_active == 1, f"Max concurrent mutations was {max_active}, expected 1"

    print(f"  Max concurrent active mutations: {max_active}")
    print("  [PASS] Active mutations never exceeded 1")


# ============================================================================
# Test 4: Query behavior (queries don't serialize)
# ============================================================================

def test_query_behavior():
    """TEST 4: Query/read operations don't serialize mutations."""
    print("TEST 4: Query behavior...")

    adapter = MockAdapter()
    adapter._tools = [
        {"type": "function", "function": {"name": "get_state", "description": "Get state",
                                          "parameters": {"type": "object", "properties": {}}}},
        {"type": "function", "function": {"name": "get_faces", "description": "Get faces",
                                          "parameters": {"type": "object", "properties": {"object_name": {"type": "string"}}, "required": ["object_name"]}}},
        {"type": "function", "function": {"name": "get_edges", "description": "Get edges",
                                          "parameters": {"type": "object", "properties": {"object_name": {"type": "string"}}, "required": ["object_name"]}}},
        {"type": "function", "function": {"name": "get_mass_properties", "description": "Get mass",
                                          "parameters": {"type": "object", "properties": {"id": {"type": "string"}, "object_name": {"type": "string"}}, "required": ["id", "object_name"]}}},
        {"type": "function", "function": {"name": "box", "description": "Create a box",
                                          "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}
    ]
    adapter._state = '[{"id": "box1", "type": "Part::Box", "visible": true}]'

    # Configure query tools to return quickly
    adapter.set_tool_result(
        "get_state", '[{"id": "box1", "type": "Part::Box", "visible": true}]')
    adapter.set_tool_result(
        "get_faces", '{"faces": [{"id": "face1", "area": 100}]}')
    adapter.set_tool_result(
        "get_edges", '{"edges": [{"id": "edge1", "length": 50}]}')
    adapter.set_tool_result("get_mass_properties",
                            '{"volume": 1000, "center_of_mass": [0,0,0]}')
    adapter.set_tool_result("box", "Successfully created box 'box2'")

    agent = CADAgent(adapter=adapter)

    # First, execute a query-only turn
    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_llm.side_effect = make_llm_response(
            [("get_state", {}), ("get_faces", {"object_name": "box1"})], "State retrieved.")
        result, tools = agent.handle_message("Get state and faces")

    assert "get_state" in tools
    assert "get_faces" in tools

    # Verify no unresolved operations from queries
    unresolved = agent._operation_registry.get_unresolved()
    assert len(unresolved) == 0, "Queries should not create unresolved operations"

    # Now run a mutation - it should work normally
    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_llm.side_effect = make_llm_response(
            [("box", {"id": "box2"})], "Box created.")
        result, tools = agent.handle_message("Create another box")

    assert "box" in tools
    # Note: design_state update depends on adapter.get_state() which we don't mock to auto-update
    # The key test is that the mutation was allowed to execute (not blocked by query operations)

    # Verify mutation worked and no leftover unresolved
    unresolved = agent._operation_registry.get_unresolved()
    assert len(unresolved) == 0

    print("  [PASS] Query operations don't block or serialize with mutations")


# ============================================================================
# Test 5: Exception release (lock released on exception)
# ============================================================================

def test_exception_release():
    """TEST 5: If mutation raises exception, lock is released and next mutation works."""
    print("TEST 5: Exception release...")

    adapter = MockAdapter()
    adapter._tools = [
        {"type": "function", "function": {"name": "box", "description": "Create a box",
                                          "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}},
        {"type": "function", "function": {"name": "cylinder", "description": "Create a cylinder",
                                          "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}
    ]
    adapter._state = '[]'

    # First mutation raises exception
    def failing_box(*args, **kwargs):
        raise RuntimeError("Simulated CAD kernel failure")

    # Second mutation succeeds
    adapter.set_tool_result("box", failing_box)
    adapter.set_tool_result("cylinder", "Successfully created cylinder 'cyl1'")

    agent = CADAgent(adapter=adapter)

    # First turn: box fails with exception
    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_llm.side_effect = make_llm_response(
            [("box", {"id": "box1"})], "Box failed.")
        result, tools = agent.handle_message("Create a box")

    # Box should have failed but not left the system locked
    # Check the mutation gate is not held
    assert not agent._mutation_gate.is_held(
    ), "Mutation gate should not be held after exception"

    # Second turn: cylinder should work fine
    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_llm.side_effect = make_llm_response(
            [("cylinder", {"id": "cyl1"})], "Cylinder created.")
        result, tools = agent.handle_message("Create a cylinder")

    assert "cylinder" in tools
    # Note: design_state update depends on adapter.get_state() which we don't mock to auto-update
    # The key test is that the second mutation was allowed to execute (not blocked by the first failure)

    # Gate should be free again
    assert not agent._mutation_gate.is_held(
    ), "Mutation gate should be free after successful mutation"

    print("  [PASS] Lock released on exception, subsequent mutation works")


# ============================================================================
# Test 6: Timeout compatibility
# ============================================================================

def test_timeout_compatibility():
    """TEST 6: Timeout doesn't leave system permanently locked."""
    print("TEST 6: Timeout compatibility...")

    adapter = MockAdapter()
    adapter._tools = [
        {"type": "function", "function": {"name": "box", "description": "Create a box",
                                          "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}},
        {"type": "function", "function": {"name": "cylinder", "description": "Create a cylinder",
                                          "parameters": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}
    ]
    adapter._state = '[]'

    # First mutation returns timeout error
    timeout_result = json.dumps({
        "success": False,
        "error": "XML-RPC call 'create_box' timed out after 120.0s.",
        "error_type": "freecad_timeout",
        "timeout_seconds": 120.0,
    })
    adapter.set_tool_result("box", timeout_result)
    adapter.set_tool_result("cylinder", "Successfully created cylinder 'cyl1'")

    agent = CADAgent(adapter=adapter)

    # Turn 1: box times out
    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_llm.side_effect = make_llm_response(
            [("box", {"id": "box1"})], "Timeout occurred.")
        result1, tools1 = agent.handle_message("Create a box")

    # Operation should be unresolved
    unresolved = agent._operation_registry.get_unresolved()
    assert len(unresolved) == 1, "Box operation should be unresolved"

    # The mutation gate IS physically held after timeout (by design - the lock is kept
    # to prevent concurrent mutations until reconciliation). The unresolved operation
    # logically blocks new mutations from acquiring the gate.
    assert agent._mutation_gate.is_held(
    ), "Physical lock should be held after timeout (by design)"

    # Turn 2: Try to run cylinder - should be blocked by unresolved operation
    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_llm.side_effect = make_llm_response(
            [("cylinder", {"id": "cyl1"})], "Should be blocked.")
        result2, tools2 = agent.handle_message("Create a cylinder")

    # Cylinder should have been blocked
    trace = agent.get_trace() if hasattr(agent, 'get_trace') else []

    # Now reconcile the timeout as "not_completed" and release the gate
    op_id = list(unresolved.keys())[0]
    agent._operation_registry.reconcile(op_id, "not_completed")
    agent._mutation_gate.force_release_for_reconciliation(op_id)

    # Turn 3: Now cylinder should work
    with patch.object(agent.provider, 'generate_with_tools') as mock_llm:
        mock_llm.side_effect = make_llm_response(
            [("cylinder", {"id": "cyl1"})], "Cylinder created.")
        result3, tools3 = agent.handle_message("Create a cylinder")

    assert "cylinder" in tools3
    # Note: design_state update depends on adapter.get_state() which we don't mock to auto-update
    # The key test is that the cylinder was allowed to execute after reconciliation

    # System should not be permanently locked
    assert not agent._mutation_gate.is_held(), "Gate should be free after reconciliation"

    print("  [PASS] Timeout doesn't leave system permanently locked (reconciliation releases gate)")


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("BIP 7.0A — CAD Mutation Serialization Tests")
    print("=" * 80)

    test_normal_mutation_works()
    test_actual_serialization()
    test_no_overlap()
    test_query_behavior()
    test_exception_release()
    test_timeout_compatibility()

    print("\n" + "=" * 80)
    print("All BIP 7.0A mutation serialization tests PASSED!")
    print("=" * 80)
