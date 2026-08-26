from abc import ABC, abstractmethod
from typing import Any, Dict, List

class CADAdapter(ABC):
    """
    The boundary between PieCAD's core and a specific CAD system.
    No CAD-specific imports (e.g., FreeCAD, Part) are allowed outside of implementations of this interface.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Establish connection to the CAD application."""
        pass

    @abstractmethod
    def execute_command(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        """Execute a generalized CAD command."""
        pass

    @abstractmethod
    def get_state(self) -> Dict[str, Any]:
        """Retrieve the current authoritative state from the CAD application."""
        pass