"""Universal CAD Adapter interface."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class CADAdapter(ABC):
    @abstractmethod
    def get_tools(self) -> List[Dict[str, Any]]:
        """Return tool definitions supported by this CAD system."""
        pass

    @abstractmethod
    def execute_command(self, tool_name: str, **kwargs) -> str:
        """Execute a tool call against the CAD system."""
        pass

    @abstractmethod
    def get_state(self) -> str:
        """Return a JSON string representing the current document objects."""
        pass

    def export_obj(self, filepath: str) -> str:
        """Exports the current visible CAD state to a .obj file (backend API method, not an LLM tool)."""
        raise NotImplementedError("export_obj not implemented by this adapter")

    # A3.3: RPC call counter methods (default implementations for adapters without RPC)
    def get_rpc_count(self) -> int:
        """Return the current RPC call count for telemetry. Default 0 for non-RPC adapters."""
        return 0

    def reset_rpc_count(self) -> None:
        """Reset the RPC call counter. No-op for non-RPC adapters."""
        pass
