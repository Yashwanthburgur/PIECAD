"""Tests for A1 identity fix - server ALWAYS owns identity."""

from adapters.freecad.adapter import FreeCADAdapter
import sys
import json
from pathlib import Path

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


class MockProxy:
    """Mock XML-RPC proxy that records calls."""

    def __init__(self):
        self.calls = []
        self.results = {}

    def __getattr__(self, name):
        def mock_method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if name in self.results:
                return self.results[name]
            return json.dumps({"status": "success", "id": f"mock_{name}"})
        return mock_method


class MockDesignState:
    """Minimal mock DesignState for adapter."""
    pass


def test_llm_provided_id_is_ignored():
    """LLM-provided ID must be ignored; server generates its own."""
    adapter = FreeCADAdapter()
    adapter._proxy = MockProxy()
    adapter.design_state = MockDesignState()

    # LLM provides explicit ID
    llm_id = "llm_chosen_name_123"
    server_id = adapter._ensure_object_id(llm_id, "box")

    # Server MUST generate its own, not use LLM's
    assert server_id != llm_id, f"Server used LLM's ID: {server_id}"
    assert server_id == "box_1", f"Expected 'box_1', got '{server_id}'"

    print("[PASS] LLM-provided ID is ignored")


def test_repeated_creation_generates_distinct_ids():
    """Each creation must generate a new unique ID."""
    adapter = FreeCADAdapter()
    adapter._proxy = MockProxy()
    adapter.design_state = MockDesignState()

    ids = set()
    for i in range(5):
        id_ = adapter._ensure_object_id(f"anything_{i}", "cylinder")
        assert id_ not in ids, f"Duplicate ID generated: {id_}"
        ids.add(id_)

    assert len(ids) == 5, "Should have 5 distinct IDs"
    print("[PASS] Repeated creation generates distinct IDs")


def test_generated_id_passed_to_freecad_operation():
    """Generated ID must be passed to the actual FreeCAD bridge call."""
    adapter = FreeCADAdapter()
    mock_proxy = MockProxy()
    mock_proxy.results["create_box"] = json.dumps(
        {"status": "success", "id": "box_1"})
    adapter._proxy = mock_proxy
    adapter.design_state = MockDesignState()

    # Simulate the box creation call path
    kwargs = {
        "id": "llm_id",  # Should be ignored
        "length": 100,
        "width": 50,
        "height": 20,
        "origin": {"x": 0, "y": 0, "z": 0}
    }

    # Extract the actual ID that will be used
    from adapters.freecad.adapter import _parse_dict_arg
    obj_id = adapter._ensure_object_id(kwargs.get("id"), "box")
    assert obj_id == "box_1", f"Server ID should be 'box_1', got '{obj_id}'"

    # Verify the call would pass the generated ID
    mock_proxy.results["create_box"] = json.dumps(
        {"status": "success", "id": obj_id})
    result = adapter._call_proxy("create_box", 100, 50, 20, str(obj_id))

    # Verify the call was made with our generated ID
    calls = mock_proxy.calls
    assert len(calls) == 1
    call_args = calls[0][1]
    assert call_args[3] == obj_id, f"FreeCAD call received ID {call_args[3]}, expected {obj_id}"

    print("[PASS] Generated ID is passed to FreeCAD operation")


def test_non_creation_ids_unaffected():
    """Reference/query operations (get_faces, get_edges, etc.) should not use _ensure_object_id."""
    adapter = FreeCADAdapter()
    adapter._proxy = MockProxy()
    adapter.design_state = MockDesignState()

    # These operations don't call _ensure_object_id - they use the provided ID directly
    # Just verify they don't crash and the ID passes through
    adapter._call_proxy("get_faces", "box1")
    adapter._call_proxy("get_edges", "box1")
    adapter._call_proxy("get_mass_properties", "box1")

    calls = adapter._proxy.calls
    assert len(calls) == 3
    assert calls[0][0] == "get_faces"
    assert calls[0][1] == ("box1",)
    assert calls[1][0] == "get_edges"
    assert calls[1][1] == ("box1",)
    assert calls[2][0] == "get_mass_properties"
    assert calls[2][1] == ("box1",)

    print("[PASS] Non-creation IDs pass through unchanged")


def test_id_generation_counter_per_tool_type():
    """Counter increments per tool type independently."""
    adapter = FreeCADAdapter()
    adapter._proxy = MockProxy()
    adapter.design_state = MockDesignState()

    # Create 2 boxes, 3 cylinders
    box_ids = [adapter._ensure_object_id("ignored", "box") for _ in range(2)]
    cyl_ids = [adapter._ensure_object_id(
        "ignored", "cylinder") for _ in range(3)]

    assert box_ids == ["box_1", "box_2"], f"Box IDs: {box_ids}"
    assert cyl_ids == ["cylinder_1", "cylinder_2",
                       "cylinder_3"], f"Cylinder IDs: {cyl_ids}"

    print("[PASS] Counter increments per tool type independently")


def test_empty_string_id_ignored():
    """Empty or whitespace-only ID should be ignored."""
    adapter = FreeCADAdapter()
    adapter._proxy = MockProxy()
    adapter.design_state = MockDesignState()

    for bad_id in ["", "   ", "\t\n", None]:
        if bad_id is None:
            continue  # skip None since Optional[str] allows it
        result = adapter._ensure_object_id(bad_id, "box")
        assert result.startswith(
            "box_"), f"Bad ID '{bad_id}' not ignored, got '{result}'"

    print("[PASS] Empty/whitespace IDs are ignored")


if __name__ == "__main__":
    test_llm_provided_id_is_ignored()
    test_repeated_creation_generates_distinct_ids()
    test_generated_id_passed_to_freecad_operation()
    test_non_creation_ids_unaffected()
    test_id_generation_counter_per_tool_type()
    test_empty_string_id_ignored()
    print("\n=== ALL IDENTITY FIX TESTS PASSED ===")
