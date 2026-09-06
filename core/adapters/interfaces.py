"""Universal CAD Adapter interface."""
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class CADAdapter(ABC):
    @abstractmethod
    def get_tools(self) -> List[Dict[str, Any]]:
        """Return tool definitions supported by this CAD system."""
        pass

    @abstractmethod
    def execute_command(self, tool_name: str, **kwargs: Any) -> str:
        """Execute a tool call against the CAD system.

        FIX: this was previously declared as
        `execute_command(self, tool_name, parameters: Dict)`, which never
        matched how it's actually called (`adapter.execute_command(name,
        **args)` in core/agent.py) or how FreeCADAdapter actually
        implements it (**kwargs). Python's ABC machinery doesn't enforce
        signature matching so this went unnoticed -- but any future
        adapter (Onshape, SolidWorks) written against the old declared
        signature would silently break when the orchestrator calls it.
        """
        pass

    @abstractmethod
    def get_state(self) -> str:
        """Return a JSON string representing the current document objects."""
        pass

    def export_glb(self, filepath: str) -> str:
        """Exports the current visible CAD state to a .glb file (backend API method, not an LLM tool)."""
        raise NotImplementedError("export_glb not implemented by this adapter")
