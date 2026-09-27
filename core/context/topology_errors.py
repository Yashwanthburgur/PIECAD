"""BIP 9.3 — StaleTopologyError.

A distinct error type representing a face/edge reference whose stored geometric
signature (or topology version) no longer matches the current geometry.

This is detection infrastructure only: raising this error tells the Agent
recovery loop that the reference must be re-queried via get_faces/get_edges
BEFORE the original tool call can be retried. No automatic re-query is
performed inside this module.
"""


class StaleTopologyError(RuntimeError):
    """Raised when a stored topology reference no longer matches live geometry.

    Attributes:
        object_id: The target object the reference belongs to (e.g., "box1")
        ref_type: "edge" or "face"
        ref_id: The specific reference ID (e.g., "box1_edge_1"), if known
        details: Human-readable mismatch details (stored vs current values)
    """

    def __init__(self, message: str, *, object_id: str = "",
                 ref_type: str = "", ref_id: str = "",
                 details: list | None = None):
        super().__init__(message)
        self.object_id = object_id
        self.ref_type = ref_type
        self.ref_id = ref_id
        self.details = details or []

    def to_dict(self) -> dict:
        """Serializable form for structured tool-result payloads."""
        return {
            "error": str(self),
            "error_type": "StaleTopologyError",
            "object_id": self.object_id,
            "ref_type": self.ref_type,
            "ref_id": self.ref_id,
            "details": list(self.details),
        }
