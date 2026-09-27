#!/usr/bin/env python
"""BIP 7.0C — Operation Registry Lifecycle & Cleanup Test.

Validates that OperationRegistry provides:
- Explicit lifecycle semantics for each status
- Bounded registry that doesn't grow forever
- Protection of unresolved operations from cleanup
- Clean reconciliation interaction with cleanup
- Thread safety
- Barrier consistency with MutationGate
"""

from core.operations import (
    OperationRegistry,
    OperationStatus,
    MutationGate,
    OperationRecord,
)
import sys
import time
import threading
from pathlib import Path
from unittest.mock import Mock, patch

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ============================================================================
# TEST 1: Successful operation lifecycle
# ============================================================================

def test_successful_operation_lifecycle():
    """TEST 1: Successful operation lifecycle - terminal and cleanup eligible."""
    print("TEST 1: Successful operation lifecycle...")

    registry = OperationRegistry()
    op_id = "test_op_1"

    # Create operation
    record = registry.create(op_id, "box", {"id": "test_box"}, "test_box", 1)
    assert record.status == OperationStatus.CREATED
    assert record.created_at is not None

    # Start operation (acquire gate)
    assert registry.start(op_id) is True
    record = registry.get(op_id)
    assert record.status == OperationStatus.RUNNING
    assert record.started_at is not None

    # Mark as succeeded
    assert registry.succeed(op_id) is True
    record = registry.get(op_id)
    assert record.status == OperationStatus.SUCCEEDED
    assert record.completed_at is not None

    # Verify cleanup eligibility (terminal status)
    assert record.status in (OperationStatus.SUCCEEDED,)

    print("  [PASS] Successful operation lifecycle works correctly")


# ============================================================================
# TEST 2: Known failure lifecycle
# ============================================================================

def test_failed_operation_lifecycle():
    """TEST 2: Known failure lifecycle - terminal and cleanup eligible."""
    print("TEST 2: Known failure lifecycle...")

    registry = OperationRegistry()
    op_id = "test_op_2"

    record = registry.create(op_id, "fillet", {"id": "fillet1"}, "box1", 1)
    registry.start(op_id)

    # Mark as failed (non-timeout)
    assert registry.fail(op_id, "RuntimeError",
                         "BRep_API: command not done") is True
    record = registry.get(op_id)
    assert record.status == OperationStatus.FAILED
    assert record.completed_at is not None
    assert record.error_type == "RuntimeError"
    assert "BRep_API" in record.error_message

    # Verify cleanup eligibility (terminal status)
    assert record.status in (OperationStatus.FAILED,)

    print("  [PASS] Known failure lifecycle works correctly")


# ============================================================================
# TEST 3: Unresolved protection
# ============================================================================

def test_unresolved_protection():
    """TEST 3: Unresolved operations are protected from cleanup."""
    print("TEST 3: Unresolved protection...")

    registry = OperationRegistry()
    op_id = "test_op_3"

    record = registry.create(op_id, "box", {"id": "test_box"}, "test_box", 1)
    registry.start(op_id)

    # Mark as timed out -> unresolved
    assert registry.timeout(op_id, "freecad_timeout", "Timeout") is True
    record = registry.get(op_id)
    assert record.status == OperationStatus.UNRESOLVED
    assert record.completed_at is not None
    assert record.error_type == "freecad_timeout"

    # Verify it's in unresolved set
    assert registry.has_any_unresolved() is True
    unresolved = registry.get_unresolved()
    assert op_id in unresolved

    # Run cleanup - should NOT remove unresolved
    # Zero age = clean everything eligible
    removed = registry.cleanup_reconciled(max_age=0.0)
    assert removed == 0, "Unresolved operation should not be cleaned"

    # Verify still exists
    record = registry.get(op_id)
    assert record is not None
    assert record.status == OperationStatus.UNRESOLVED

    print("  [PASS] Unresolved operations are protected from cleanup")


# ============================================================================
# TEST 4: Reconciled lifecycle
# ============================================================================

def test_reconciled_lifecycle():
    """TEST 4: Reconciled lifecycle - becomes cleanup eligible after reconciliation."""
    print("TEST 4: Reconciled lifecycle...")

    registry = OperationRegistry()
    op_id = "test_op_4"

    record = registry.create(op_id, "box", {"id": "test_box"}, "test_box", 1)
    registry.start(op_id)
    registry.timeout(op_id, "freecad_timeout", "Timeout")

    # Verify unresolved
    assert registry.has_any_unresolved() is True

    # Reconcile as completed
    assert registry.reconcile(op_id, "completed") is True
    record = registry.get(op_id)
    assert record.status == OperationStatus.RECONCILED
    assert record.reconciled_at is not None
    assert record.reconciliation_result == "completed"
    assert record.late_completion_detected is False  # Not set by reconcile()

    # Verify no longer unresolved
    assert registry.has_any_unresolved() is False

    # Reconcile as not_completed
    op_id2 = "test_op_5"
    record2 = registry.create(op_id2, "cylinder", {"id": "cyl1"}, "cyl1", 1)
    registry.start(op_id2)
    registry.timeout(op_id2, "freecad_timeout", "Timeout")
    assert registry.reconcile(op_id2, "not_completed") is True
    record2 = registry.get(op_id2)
    assert record2.status == OperationStatus.RECONCILED
    assert record2.reconciliation_result == "not_completed"

    # Run cleanup - reconciled should be eligible
    # Use a small sleep to ensure age > max_age
    time.sleep(0.01)
    removed = registry.cleanup_reconciled(max_age=0.0)
    assert removed >= 1, "Reconciled operations should be cleaned"

    print("  [PASS] Reconciled lifecycle works correctly")


# ============================================================================
# TEST 5: Registry bound
# ============================================================================

def test_registry_bound():
    """TEST 5: Registry is bounded and doesn't grow without bound."""
    print("TEST 5: Registry bound...")

    registry = OperationRegistry()
    # Default MAX_OPERATIONS = 100

    # Create many terminal operations
    for i in range(150):
        op_id = f"op_{i}"
        registry.create(op_id, "box", {"id": f"box{i}"}, f"box{i}", 1)
        registry.start(op_id)
        registry.succeed(op_id)

    # Registry should be bounded
    all_ops = registry.get_all()
    assert len(all_ops) <= OperationRegistry.MAX_OPERATIONS, \
        f"Registry exceeded max size: {len(all_ops)} > {OperationRegistry.MAX_OPERATIONS}"

    print(
        f"  Registry size: {len(all_ops)} (max: {OperationRegistry.MAX_OPERATIONS})")
    print("  [PASS] Registry is bounded")


# ============================================================================
# TEST 6: Unresolved survives overflow
# ============================================================================

def test_unresolved_survives_overflow():
    """TEST 6: Unresolved operation survives registry overflow."""
    print("TEST 6: Unresolved survives overflow...")

    registry = OperationRegistry()

    # Create one unresolved operation
    unresolved_id = "unresolved_1"
    registry.create(unresolved_id, "box", {"id": "box1"}, "box1", 1)
    registry.start(unresolved_id)
    registry.timeout(unresolved_id, "freecad_timeout", "Timeout")

    # Verify it's unresolved
    assert registry.has_any_unresolved() is True
    assert unresolved_id in registry.get_unresolved()

    # Create many terminal operations to force overflow
    for i in range(200):
        op_id = f"terminal_{i}"
        registry.create(op_id, "box", {"id": f"box{i}"}, f"box{i}", 1)
        registry.start(op_id)
        registry.succeed(op_id)

    # Registry should be bounded
    all_ops = registry.get_all()
    assert len(all_ops) <= OperationRegistry.MAX_OPERATIONS

    # Unresolved should STILL exist
    assert registry.has_any_unresolved() is True
    assert unresolved_id in registry.get_unresolved()
    record = registry.get(unresolved_id)
    assert record is not None
    assert record.status == OperationStatus.UNRESOLVED

    print("  [PASS] Unresolved operation survives overflow")


# ============================================================================
# TEST 7: Most recent terminal operations retained
# ============================================================================

def test_most_recent_retained():
    """TEST 7: Most recent terminal operations retained during overflow."""
    print("TEST 7: Most recent terminal operations retained...")

    registry = OperationRegistry()

    # Create terminal operations with known timestamps
    for i in range(150):
        op_id = f"op_{i}"
        registry.create(op_id, "box", {"id": f"box{i}"}, f"box{i}", 1)
        registry.start(op_id)
        registry.succeed(op_id)
        # Small delay to ensure timestamp ordering
        time.sleep(0.001)

    # Registry should keep the most recent ones
    all_ops = registry.get_all()
    assert len(all_ops) <= OperationRegistry.MAX_OPERATIONS

    # The newest operations should be present
    # Check that highest-numbered operations exist
    for i in range(140, 150):
        op_id = f"op_{i}"
        assert op_id in all_ops, f"Recent operation {op_id} should be retained"

    # Oldest operations should be removed
    # (though exact boundary depends on timing)
    print(f"  Retained {len(all_ops)} operations")
    print("  [PASS] Most recent terminal operations retained")


# ============================================================================
# TEST 8: Thread safety
# ============================================================================

def test_thread_safety():
    """TEST 8: Concurrent registry operations are thread-safe."""
    print("TEST 8: Thread safety...")

    registry = OperationRegistry()
    errors = []
    error_lock = threading.Lock()

    def create_operations(start, count):
        try:
            for i in range(start, start + count):
                op_id = f"thread_op_{i}"
                registry.create(op_id, "box", {"id": f"box{i}"}, f"box{i}", 1)
                registry.start(op_id)
                if i % 3 == 0:
                    registry.succeed(op_id)
                elif i % 3 == 1:
                    registry.fail(op_id, "RuntimeError", "Test error")
                else:
                    registry.timeout(op_id, "freecad_timeout", "Timeout")
        except Exception as e:
            with error_lock:
                errors.append(e)

    def cleanup_operations():
        try:
            for _ in range(50):
                registry.cleanup_reconciled(max_age=0.0)
                time.sleep(0.001)
        except Exception as e:
            with error_lock:
                errors.append(e)

    def get_operations():
        try:
            for _ in range(100):
                registry.get_all()
                registry.get_unresolved()
                registry.has_any_unresolved()
                time.sleep(0.001)
        except Exception as e:
            with error_lock:
                errors.append(e)

    threads = [
        threading.Thread(target=create_operations, args=(0, 50)),
        threading.Thread(target=create_operations, args=(50, 50)),
        threading.Thread(target=cleanup_operations),
        threading.Thread(target=get_operations),
    ]

    for t in threads:
        t.start()

    for t in threads:
        t.join(timeout=5.0)

    assert len(errors) == 0, f"Thread safety errors: {errors}"

    # Verify unresolved operations are still consistent
    all_ops = registry.get_all()
    for record in all_ops.values():
        if record.status == OperationStatus.UNRESOLVED:
            assert registry.has_any_unresolved() is True

    print("  [PASS] Thread safety verified")


# ============================================================================
# TEST 9: Barrier consistency
# ============================================================================

def test_barrier_consistency():
    """TEST 9: MutationGate barrier consistency with registry cleanup."""
    print("TEST 9: Barrier consistency...")

    registry = OperationRegistry()
    gate = MutationGate(registry)

    # Create unresolved operation
    op_id = "test_op_barrier"
    registry.create(op_id, "box", {"id": "test_box"}, "test_box", 1)
    registry.start(op_id)
    registry.timeout(op_id, "freecad_timeout", "Timeout")

    # Gate should see unresolved barrier
    assert registry.has_any_unresolved() is True

    # Try to acquire gate with different operation - should be blocked
    acquired = gate.acquire("other_op", timeout=0.1)
    assert acquired is False, "Gate should block when unresolved operation exists"

    # Reconcile the operation
    registry.reconcile(op_id, "not_completed")

    # Gate should no longer see unresolved barrier
    assert registry.has_any_unresolved() is False

    # Now other operation should be able to acquire
    acquired = gate.acquire("other_op", timeout=0.1)
    assert acquired is True, "Gate should allow after reconciliation"
    gate.release("other_op")

    # Run cleanup
    registry.cleanup_reconciled(max_age=0.0)

    # Registry should still be consistent
    assert registry.has_any_unresolved() is False

    print("  [PASS] Barrier consistency verified")


# ============================================================================
# TEST 10: Existing operation ID behavior
# ============================================================================

def test_operation_id_uniqueness():
    """TEST 10: Every created operation receives a unique operation ID."""
    print("TEST 10: Operation ID uniqueness...")

    registry = OperationRegistry()

    # Create many operations
    for i in range(50):
        op_id = f"unique_op_{i}"
        record = registry.create(op_id, "box", {"id": f"box{i}"}, f"box{i}", 1)
        assert record.operation_id == op_id
        assert record.step == 1
        assert record.tool == "box"
        assert record.args["id"] == f"box{i}"

    # Verify all exist
    all_ops = registry.get_all()
    assert len(all_ops) == 50

    # Each should have unique ID
    ids = set(op_id for op_id in all_ops.keys())
    assert len(ids) == 50

    print("  [PASS] Operation IDs are unique and preserved")


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    print("=" * 80)
    print("BIP 7.0C — Operation Registry Lifecycle & Cleanup Tests")
    print("=" * 80)

    test_successful_operation_lifecycle()
    test_failed_operation_lifecycle()
    test_unresolved_protection()
    test_reconciled_lifecycle()
    test_registry_bound()
    test_unresolved_survives_overflow()
    test_most_recent_retained()
    test_thread_safety()
    test_barrier_consistency()
    test_operation_id_uniqueness()

    print("\n" + "=" * 80)
    print("All BIP 7.0C operation registry tests PASSED!")
    print("=" * 80)
