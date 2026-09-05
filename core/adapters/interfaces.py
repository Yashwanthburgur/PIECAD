"""Universal CAD Adapter interface."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class CADAdapter(ABC):
    @abstractmethod
    def get_tools(self) -> List[Dict[str, Any]]:
        """Return tool definitions supported by this CAD system."""
        pass

    @abstractmethod
    def execute_command(self, tool_name: str, parameters: Dict[str, Any]) -> str:
        """Execute a tool call against the CAD system."""
        pass

    @abstractmethod
    def get_state(self) -> str:
        """Return a JSON string representing the current document objects."""
        pass

    def export_glb(self, filepath: str) -> str:
        """Exports the current visible CAD state to a .glb file (backend API method, not an LLM tool)."""
        raise NotImplementedError("export_glb not implemented by this adapter")
