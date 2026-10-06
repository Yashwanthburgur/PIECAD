"""Operation Lifecycle Management for BIP 7.0.

Defines the operation lifecycle states and tracking for mutation serialization
and concurrency control.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class OperationStatus(Enum):
    """Lifecycle states for a CAD mutation operation."""
    CREATED = "created"           # Operation ID generated, not yet executing
    RUNNING = "running"           # Actively executing (holds mutation gate)
    SUCCEEDED = "succeeded"       # Completed successfully, state verified
    FAILED = "failed"             # Failed with non-timeout error
    TIMED_OUT = "timed_out"       # Timeout occurred, underlying may still run
    UNRESOLVED = "unresolved"     # Timed out, awaiting reconciliation
    RECONCILED = "reconciled"     # State refresh determined final outcome
    SUPERSEDED = "superseded"     # Newer operation made this obsolete


@dataclass
class OperationRecord:
    """Complete record of an operation's lifecycle."""
    operation_id: str
    tool: str
    args: Dict[str, Any]
    target_id: Optional[str]
    step: int
    status: OperationStatus = OperationStatus.CREATED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    late_completion_detected: bool = False
    reconciled_at: Optional[float] = None
    # "completed" | "not_completed" | "unknown"
    reconciliation_result: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "tool": self.tool,
            "args": self.args,
            "target_id": self.target_id,
            "step": self.step,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "late_completion_detected": self.late_completion_detected,
            "reconciled_at": self.reconciled_at,
            "reconciliation_result": self.reconciliation_result,
        }


class OperationRegistry:
    """Thread-safe registry for tracking operation lifecycles.

    Provides bounded storage with automatic cleanup of reconciled operations.
    Unresolved operations are protected from premature cleanup.
    """

    MAX_OPERATIONS = 100  # Bounded to prevent memory growth
    MAX_UNRESOLVED_AGE = 3600.0  # 1 hour max age for unresolved ops

    def __init__(self):
        self._operations: Dict[str, OperationRecord] = {}
        self._lock = threading.RLock()
        self._unresolved_ids: set = set()  # Track unresolved for quick lookup

    def create(self, operation_id: str, tool: str, args: Dict[str, Any],
               target_id: Optional[str], step: int) -> OperationRecord:
        """Register a new operation."""
        with self._lock:
            self._enforce_bounds()
            record = OperationRecord(
                operation_id=operation_id,
                tool=tool,
                args=dict(args),
                target_id=target_id,
                step=step,
                status=OperationStatus.CREATED,
            )
            self._operations[operation_id] = record
            return record

    def start(self, operation_id: str) -> bool:
        """Mark operation as running (acquired mutation gate)."""
        with self._lock:
            record = self._operations.get(operation_id)
            if not record:
                return False
            if record.status != OperationStatus.CREATED:
                return False
            record.status = OperationStatus.RUNNING
            record.started_at = time.time()
            return True

    def succeed(self, operation_id: str) -> bool:
        """Mark operation as succeeded."""
        with self._lock:
            record = self._operations.get(operation_id)
            if not record:
                return False
            record.status = OperationStatus.SUCCEEDED
            record.completed_at = time.time()
            self._unresolved_ids.discard(operation_id)
            return True

    def fail(self, operation_id: str, error_type: str, error_message: str) -> bool:
        """Mark operation as failed (non-timeout)."""
        with self._lock:
            record = self._operations.get(operation_id)
            if not record:
                return False
            record.status = OperationStatus.FAILED
            record.completed_at = time.time()
            record.error_type = error_type
            record.error_message = error_message
            self._unresolved_ids.discard(operation_id)
            return True

    def timeout(self, operation_id: str, error_type: str, error_message: str) -> bool:
        """Mark operation as timed out -> unresolved."""
        with self._lock:
            record = self._operations.get(operation_id)
            if not record:
                return False
            record.status = OperationStatus.UNRESOLVED
            record.completed_at = time.time()  # Timeout time
            record.error_type = error_type
            record.error_message = error_message
            self._unresolved_ids.add(operation_id)
            return True

    def mark_late_completion(self, operation_id: str) -> bool:
        """Mark that late completion was detected."""
        with self._lock:
            record = self._operations.get(operation_id)
            if not record:
                return False
            record.late_completion_detected = True
            return True

    def reconcile(self, operation_id: str, result: str) -> bool:
        """Reconcile an unresolved operation after state refresh.

        Args:
            operation_id: The operation to reconcile
            result: One of "completed", "not_completed", "unknown"
        """
        with self._lock:
            record = self._operations.get(operation_id)
            if not record:
                return False
            if record.status not in (OperationStatus.TIMED_OUT, OperationStatus.UNRESOLVED):
                return False
            record.status = OperationStatus.RECONCILED
            record.reconciled_at = time.time()
            record.reconciliation_result = result
            self._unresolved_ids.discard(operation_id)
            return True

    def supersede(self, operation_id: str) -> bool:
        """Mark operation as superseded by a newer operation."""
        with self._lock:
            record = self._operations.get(operation_id)
            if not record:
                return False
            record.status = OperationStatus.SUPERSEDED
            self._unresolved_ids.discard(operation_id)
            return True

    def get(self, operation_id: str) -> Optional[OperationRecord]:
        """Get operation record by ID."""
        with self._lock:
            return self._operations.get(operation_id)

    def get_unresolved(self) -> Dict[str, OperationRecord]:
        """Get all unresolved operations."""
        with self._lock:
            return {
                op_id: self._operations[op_id]
                for op_id in self._unresolved_ids
                if op_id in self._operations
            }

    def has_unresolved_for_target(self, target_id: str) -> bool:
        """Check if there's an unresolved operation for a target."""
        with self._lock:
            for op_id in self._unresolved_ids:
                record = self._operations.get(op_id)
                if record and record.target_id == target_id:
                    return True
            return False

    def has_any_unresolved(self) -> bool:
        """Check if any unresolved operations exist."""
        with self._lock:
            return len(self._unresolved_ids) > 0

    def get_oldest_unresolved_age(self) -> float:
        """Get age of oldest unresolved operation in seconds."""
        with self._lock:
            oldest = float('inf')
            now = time.time()
            for op_id in self._unresolved_ids:
                record = self._operations.get(op_id)
                if record:
                    age = now - record.created_at
                    if age < oldest:
                        oldest = age
            return oldest if oldest != float('inf') else 0.0

    def cleanup_reconciled(self, max_age: float = 300.0) -> int:
        """Remove old reconciled/completed/failed/superseded operations.

        Args:
            max_age: Maximum age in seconds for cleaned operations.

        Returns:
            Number of operations removed.
        """
        with self._lock:
            now = time.time()
            to_remove = []
            for op_id, record in self._operations.items():
                if record.status in (
                    OperationStatus.SUCCEEDED,
                    OperationStatus.FAILED,
                    OperationStatus.RECONCILED,
                    OperationStatus.SUPERSEDED,
                ):
                    age = now - (record.completed_at or record.created_at)
                    if age > max_age:
                        to_remove.append(op_id)
            for op_id in to_remove:
                self._operations.pop(op_id, None)
                self._unresolved_ids.discard(op_id)
            return len(to_remove)

    def _enforce_bounds(self) -> None:
        """Enforce maximum registry size by removing oldest reconciled operations."""
        if len(self._operations) < self.MAX_OPERATIONS:
            return
        # Remove oldest reconciled/failed/superseded first
        candidates = [
            (op_id, record.completed_at or record.created_at)
            for op_id, record in self._operations.items()
            if record.status in (
                OperationStatus.SUCCEEDED,
                OperationStatus.FAILED,
                OperationStatus.RECONCILED,
                OperationStatus.SUPERSEDED,
            )
        ]
        candidates.sort(key=lambda x: x[1])  # Oldest first
        for op_id, _ in candidates:
            if len(self._operations) < self.MAX_OPERATIONS:
                break
            self._operations.pop(op_id, None)
            self._unresolved_ids.discard(op_id)

    def get_all(self) -> Dict[str, OperationRecord]:
        """Get all operations (for debugging)."""
        with self._lock:
            return dict(self._operations)


# Mutation gate for serializing CAD mutations
class MutationGate:
    """Serialization gate for CAD mutations.

    Ensures only one mutation executes against the CAD document at a time.
    Unresolved (timed-out) operations maintain a logical hold on the gate
    until reconciled.
    """

    def __init__(self, registry: OperationRegistry):
        # Physical CAD-execution mutex. Held ONLY while a mutation is inside the
        # actual CAD execution critical section (adapter.execute_command). It is
        # released by the owning operation on success, failure, exception, or at
        # the timeout boundary when control returns to the caller.
        self._lock = threading.Lock()
        # Guards gate metadata (_current_operation_id) across threads.
        self._meta = threading.Lock()
        self._registry = registry
        self._current_operation_id: Optional[str] = None

        # Default bounded acquisition timeout (seconds) for physical lock.
        # Can be overridden per-call via acquire(..., timeout=...).
        self.DEFAULT_ACQUIRE_TIMEOUT = 10.0

    def acquire(self, operation_id: str, timeout: float = -1.0):
        """Acquire the mutation gate for an operation.

        Args:
            operation_id: The operation acquiring the gate
            timeout: Timeout in seconds (-1 = use DEFAULT_ACQUIRE_TIMEOUT, 0 = non-blocking)

        Returns:
            tuple: (acquired: bool, error_type: Optional[str])
                - (True, None) if acquired successfully
                - (False, "unresolved_operation") if blocked by unresolved operation
                - (False, "mutation_gate_timeout") if physical lock timeout
        """
        # First check if there's an unresolved operation blocking us. This is a
        # LOGICAL barrier (registry-derived) and deliberately does NOT depend on
        # holding the physical lock: a timed-out operation must not be able to
        # permanently lock the gate.
        with self._registry._lock:
            if self._registry.has_any_unresolved():
                # Check if we're the unresolved one (re-entry after reconciliation)
                unresolved = self._registry.get_unresolved()
                if operation_id not in unresolved:
                    return False, "unresolved_operation"  # Blocked by another unresolved operation

        # Use default timeout if -1 provided
        if timeout < 0:
            timeout = self.DEFAULT_ACQUIRE_TIMEOUT

        # Try to acquire physical lock with timeout
        acquired = self._lock.acquire(timeout=timeout)
        if not acquired:
            return False, "mutation_gate_timeout"

        with self._meta:
            self._current_operation_id = operation_id
        self._registry.start(operation_id)
        return True, None

    def release(self, operation_id: str) -> bool:
        """Release the mutation gate.

        Args:
            operation_id: The operation releasing the gate

        Returns:
            True if released, False if not the current holder.
        """
        with self._meta:
            if self._current_operation_id != operation_id:
                return False
            self._current_operation_id = None
        try:
            self._lock.release()
        except RuntimeError:
            # Lock already released (e.g. at a timeout boundary). Treat as a
            # no-op rather than raising, so a stray second release is harmless.
            return False
        return True

    def get_current(self) -> Optional[str]:
        """Get the currently executing operation ID."""
        with self._meta:
            return self._current_operation_id

    def is_held(self) -> bool:
        """Check if the gate is currently held."""
        return self._lock.locked()

    def force_release_for_reconciliation(self, operation_id: str) -> bool:
        """Force release the gate for a reconciled operation.

        This is called when an unresolved operation is reconciled and we
        need to allow the next mutation to proceed.

        Normally the physical lock is already released at the timeout boundary
        (the serialization boundary ends when control returns to the caller),
        so this is a logical cleanup. As a safety net, if the operation still
        owns the physical lock (a caller failed to release it), we release it
        here so the system can never remain permanently locked.

        Args:
            operation_id: The operation to force-release

        Returns:
            True if the gate was held by this operation and is now released.
        """
        with self._meta:
            if self._current_operation_id != operation_id:
                return False
            self._current_operation_id = None
        # Safety net: only release the physical lock if it is still held. This
        # operation was the logical owner, so releasing it cannot steal the
        # critical section from a different active mutation.
        if self._lock.locked():
            try:
                self._lock.release()
            except RuntimeError:
                pass
        return True
