"""PieCAD Core Adapter Interfaces."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

class CADAdapter(ABC):
    """Abstract interface for all CAD systems (FreeCAD, Onshape, etc.)."""

    @abstractmethod
    def get_tools(self) -> List[Dict[str, Any]]:
        """Return the list of tool schemas supported by this CAD system."""
        pass

    @abstractmethod
    def execute_command(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        """Execute a specific structured tool call against the CAD system."""
        pass
    
    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Return current CAD workspace state (e.g., connection status)."""
        pass