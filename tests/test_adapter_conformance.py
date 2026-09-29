"""Adapter conformance tests (Track A / A0.7).

These tests lock down the ``CADAdapter`` contract so interface drift such as
the A0.6 audit item (an abstract ``execute_command`` using a ``parameters``
dict while real implementations and call sites use keyword arguments) is
caught automatically.

Design constraints:
* No live FreeCAD / XML-RPC connection is required.
* The concrete ``FreeCADAdapter`` is inspected *statically* (signatures and
  ABC resolution) rather than invoked, so this stays a fast unit test.
* A minimal in-process adapter exercises the intended calling convention end
  to end.
"""

import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.adapters.interfaces import CADAdapter  # noqa: E402


class _ConformantAdapter(CADAdapter):
    """Minimal adapter implementing the documented contract."""

    def __init__(self) -> None:
        self.calls: List[Any] = []

    def get_tools(self) -> List[Dict[str, Any]]:
        return [{"type": "function", "function": {"name": "box"}}]

    def execute_command(self, tool_name: str, **kwargs) -> str:
        self.calls.append((tool_name, kwargs))
        return json.dumps({"status": "success", "tool": tool_name})

    def get_state(self) -> str:
        return "[]"


def test_abc_cannot_be_instantiated_directly():
    try:
        CADAdapter()  # type: ignore[abstract]
    except TypeError:
        return
    raise AssertionError("CADAdapter must remain abstract")


def test_conformant_adapter_satisfies_contract():
    adapter = _ConformantAdapter()
    tools = adapter.get_tools()
    assert isinstance(tools, list) and tools
    assert tools[0]["function"]["name"] == "box"

    state = adapter.get_state()
    assert json.loads(state) == []

    # Keyword-argument calling convention used throughout the codebase.
    out = adapter.execute_command("get_faces", object_name="Box")
    assert json.loads(out)["status"] == "success"
    assert adapter.calls[-1] == ("get_faces", {"object_name": "Box"})


def test_execute_command_accepts_kwargs_not_parameters_dict():
    """The ABC must expose ``(tool_name, **kwargs)``, not a ``parameters`` dict.

    This is the concrete guard for the A0.6 audit finding.
    """
    sig = inspect.signature(CADAdapter.execute_command)
    params = list(sig.parameters.values())
    assert params[0].name == "self"
    assert params[1].name == "tool_name"
    assert any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params
    ), "execute_command must accept **kwargs"
    assert "parameters" not in sig.parameters


def test_freecad_adapter_matches_abc_signatures():
    """Concrete adapter must be call-compatible with the ABC contract."""
    from adapters.freecad.adapter import FreeCADAdapter

    assert issubclass(FreeCADAdapter, CADAdapter)

    abc_sig = inspect.signature(CADAdapter.execute_command)
    impl_sig = inspect.signature(FreeCADAdapter.execute_command)
    # Same parameter names/kinds for the positional + var-keyword surface.
    assert list(abc_sig.parameters) == list(impl_sig.parameters), (
        f"FreeCADAdapter.execute_command signature {impl_sig} diverged from "
        f"ABC {abc_sig}"
    )

    # get_tools / get_state must be concrete (overridden), not inherited ABC.
    assert FreeCADAdapter.get_tools is not CADAdapter.get_tools
    assert FreeCADAdapter.get_state is not CADAdapter.get_state
    assert FreeCADAdapter.execute_command is not CADAdapter.execute_command


def test_freecad_adapter_is_registered_in_api_factory():
    """The API adapter factory must reference an importable CADAdapter subclass."""
    factory = {"freecad": "adapters.freecad.adapter.FreeCADAdapter"}
    module_path, class_name = factory["freecad"].rsplit(".", 1)

    import importlib

    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)
    assert issubclass(cls, CADAdapter)


def test_abstract_methods_are_all_implemented_by_registered_adapter():
    from adapters.freecad.adapter import FreeCADAdapter

    assert FreeCADAdapter.__abstractmethods__ == frozenset(), (
        f"FreeCADAdapter has unimplemented abstract methods: "
        f"{FreeCADAdapter.__abstractmethods__}"
    )


def test_get_tools_returns_openai_function_schema():
    """Tool definitions must follow the OpenAI function-calling schema shape."""
    adapter = _ConformantAdapter()
    for tool in adapter.get_tools():
        assert isinstance(tool, dict)
        assert "function" in tool
        assert "name" in tool["function"]
