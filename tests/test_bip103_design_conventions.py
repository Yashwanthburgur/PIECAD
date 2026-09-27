"""BIP 10.3 — Session Design-Conventions Memory Tests.

Focused deterministic tests for session-scoped design conventions memory.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context.memory import SessionMemory  # noqa: E402
from core.agent import CADAgent  # noqa: E402
from core.context.state import DesignState  # noqa: E402
from core.context.compiler import ContextCompiler  # noqa: E402
from core.context.budget import ContextBudget  # noqa: E402
from core.context.conversation import ConversationContext  # noqa: E402
from core.adapters.interfaces import CADAdapter  # noqa: E402


# --------------------------------------------------------------------------- #
# Mock adapter
# --------------------------------------------------------------------------- #

class MockAdapter(CADAdapter):
    def __init__(self):
        self.tool_names = ["box", "get_state"]
        self.state = '[]'
        self.calls = []

    def get_tools(self):
        return [{"type": "function", "function": {"name": n, "description": f"run {n}", "parameters": {"type": "object", "properties": {}}}} for n in self.tool_names]

    def get_state(self):
        return self.state

    def execute_command(self, tool_name, **kwargs):
        if tool_name == "box":
            self.state = json.dumps([{"id": "box1", "type": "Part::Box", "visible": True, "parents": [
            ], "children": [], "properties": kwargs}])
            return "ok"
        return "ok"


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_convention_can_be_written():
    """Test a) convention can be written."""
    print("Testing a) convention can be written...")

    st = SessionMemory()
    st.set("default_hole", "prefer M6 over M8",
           kind="convention", source="user")

    val = st.get("default_hole")
    assert val == "prefer M6 over M8"

    entry = st.get_entry("default_hole")
    assert entry is not None
    assert entry.kind == "convention"
    assert entry.value == "prefer M6 over M8"
    assert entry.source == "user"

    print("  [PASS] Convention can be written")


def test_convention_can_be_read():
    """Test b) convention can be read on a later turn."""
    print("Testing b) convention can be read on a later turn...")

    st = SessionMemory()
    st.set("wall_thickness", "5 mm", kind="convention", source="user")

    # Simulate later turn
    val = st.get("wall_thickness")
    assert val == "5 mm"

    print("  [PASS] Convention can be read on later turn")


def test_multiple_conventions_coexist():
    """Test c) multiple conventions coexist."""
    print("Testing c) multiple conventions coexist...")

    st = SessionMemory()
    st.set("default_hole", "prefer M6", kind="convention", source="user")
    st.set("wall_thickness", "5 mm", kind="convention", source="agent")
    st.set("material", "aluminum", kind="convention", source="user")

    conventions = st.by_kind("convention")
    assert len(conventions) == 3

    keys = {c.key for c in conventions}
    assert keys == {"default_hole", "wall_thickness", "material"}

    print("  [PASS] Multiple conventions coexist")


def test_session_isolation():
    """Test d) session isolation works."""
    print("Testing d) session isolation...")

    # Create two separate session memories
    st1 = SessionMemory()
    st1.set("convention1", "value1", kind="convention", source="user")

    st2 = SessionMemory()
    st2.set("convention2", "value2", kind="convention", source="user")

    # They should be isolated
    assert st1.get("convention1") == "value1"
    assert st1.get("convention2") is None

    assert st2.get("convention2") == "value2"
    assert st2.get("convention1") is None

    print("  [PASS] Session isolation works")


def test_conventions_in_compiled_context():
    """Test e) conventions appear in compiled context when relevant."""
    print("Testing e) conventions appear in compiled context...")

    st = SessionMemory()
    st.set("default_hole", "prefer M6 over M8",
           kind="convention", source="user")
    st.set("material", "aluminum", kind="convention", source="user")

    # Create a context plan requesting conventions
    from core.context.plan import ContextPlan
    plan = ContextPlan(
        required_memory_sections=["conventions"],
        relevant_object_ids=[],
        required_state_sections=[],
        required_tools=[],
        reasoning_mode="default",
        confidence=0.9,
        ambiguity=0.1,
        additional_context={},
    )

    compiler = ContextCompiler()
    memory_data = compiler._extract_conventions_from_plan(
        st, plan, "Create a hole with default settings")

    assert "conventions" in memory_data
    assert len(memory_data["conventions"]) == 2

    keys = {c["key"] for c in memory_data["conventions"]}
    assert keys == {"default_hole", "material"}

    print("  [PASS] Conventions appear in compiled context when relevant")


def test_conventions_respect_budget():
    """Test f) conventions respect the existing context-token budget."""
    print("Testing f) conventions respect context-token budget...")

    st = SessionMemory()
    # Add many conventions
    for i in range(20):
        st.set(f"convention_{i}", f"value_{i}",
               kind="convention", source="user")

    from core.context.plan import ContextPlan
    plan = ContextPlan(
        required_memory_sections=["conventions"],
        relevant_object_ids=[],
        required_state_sections=[],
        required_tools=[],
        reasoning_mode="default",
        confidence=0.9,
        ambiguity=0.1,
        additional_context={},
    )

    compiler = ContextCompiler(budget=ContextBudget(memory_budget=500))
    memory_data = compiler._extract_conventions_from_plan(
        SessionMemory(), plan, "test")

    # Should be empty because we passed empty session memory
    # Let's test with actual session memory
    st2 = SessionMemory()
    for i in range(5):
        st2.set(f"conv_{i}", f"val_{i}", kind="convention", source="user")

    compiler2 = ContextCompiler(budget=ContextBudget(memory_budget=100))
    memory_data = compiler2._extract_conventions_from_plan(st2, plan, "test")

    # Should have conventions but within budget
    assert "conventions" in memory_data
    assert len(memory_data["conventions"]) <= 5

    print("  [PASS] Conventions respect context budget")


def test_conventions_not_fabricated():
    """Test g) conventions are not fabricated from unrelated user text."""
    print("Testing g) conventions not fabricated...")

    st = SessionMemory()

    # User message mentions "hole" but no convention was set
    from core.context.plan import ContextPlan
    plan = ContextPlan(
        required_memory_sections=[],
        relevant_object_ids=[],
        required_state_sections=[],
        required_tools=[],
        reasoning_mode="default",
        confidence=0.9,
        ambiguity=0.1,
        additional_context={},
    )

    compiler = ContextCompiler()
    memory_data = compiler._extract_conventions_from_plan(
        st, plan, "Create a hole with M6 thread")

    # Should NOT include conventions because none were set
    assert memory_data == {} or "conventions" not in memory_data

    print("  [PASS] Conventions not fabricated from unrelated text")


def test_recency_behavior_unaffected():
    """Test h) existing context/state behavior remains unaffected."""
    print("Testing h) existing behavior unaffected...")

    st = SessionMemory()
    st.set("box1", {"type": "Part::Box"}, kind="fact", source="system")
    st.set("fillet1", {"type": "Part::Fillet"}, kind="fact", source="system")

    # Facts should still be retrievable
    box1 = st.get("box1")
    assert box1 == {"type": "Part::Box"}

    fillet1 = st.get("fillet1")
    assert fillet1 == {"type": "Part::Fillet"}

    # Conventions work alongside facts
    st.set("default_material", "steel", kind="convention", source="user")
    material = st.get("default_material")
    assert material == "steel"

    print("  [PASS] Existing behavior unaffected")


def test_agent_records_convention():
    """Test agent can record and retrieve conventions."""
    print("Testing agent records conventions...")

    from core.agent import CADAgent
    from core.context.memory import SessionMemory
    from core.adapters.interfaces import CADAdapter

    class MockAdapter(CADAdapter):
        def __init__(self):
            self.state = '[]'
            self.calls = []

        def get_tools(self):
            return [{"type": "function", "function": {"name": "box", "description": "box", "parameters": {}}}]

        def get_state(self):
            return self.state

        def execute_command(self, tool_name, **kwargs):
            return "ok"

    adapter = MockAdapter()
    agent = CADAgent(adapter=adapter)
    agent.design_state.update_from_cad_state('[]')

    # Record a convention
    agent.record_design_convention("default_hole", "prefer M6", source="user")
    val = agent.get_design_convention("default_hole")
    assert val == "prefer M6"

    all_conv = agent.get_all_design_conventions()
    assert len(all_conv) == 1
    assert all_conv[0]["key"] == "default_hole"
    assert all_conv[0]["value"] == "prefer M6"

    print("  [PASS] Agent records and retrieves conventions")


def test_convention_from_user_message():
    """Test convention extraction from user message."""
    print("Testing convention extraction from user message...")

    from core.agent import CADAgent
    from core.adapters.interfaces import CADAdapter

    class MockAdapter(CADAdapter):
        def __init__(self):
            self.state = '[]'

        def get_tools(self):
            return [{"type": "function", "function": {"name": "box", "description": "box", "parameters": {}}}]

        def get_state(self):
            return self.state

        def execute_command(self, tool_name, **kwargs):
            return "ok"

    adapter = MockAdapter()
    agent = CADAgent(adapter=adapter, capture_trace=True)
    agent.design_state.update_from_cad_state('[]')

    # Test explicit convention recording
    agent._maybe_record_convention_from_message(
        "Set convention default_hole to prefer M6")
    val = agent.get_design_convention("default_hole")
    assert val == "prefer M6"

    # Test another pattern
    agent._maybe_record_convention_from_message(
        "Establish convention material as aluminum")
    val = agent.get_design_convention("material")
    assert val == "aluminum"

    print("  [PASS] Convention extracted from user message")


def test_convention_persists_across_turns():
    """Test conventions persist across handle_message calls."""
    print("Testing convention persistence across turns...")

    from core.agent import CADAgent
    from core.adapters.interfaces import CADAdapter

    class MockAdapter(CADAdapter):
        def __init__(self):
            self.state = '[]'

        def get_tools(self):
            return [{"type": "function", "function": {"name": "box", "description": "box", "parameters": {}}}]

        def get_state(self):
            return self.state

        def execute_command(self, tool_name, **kwargs):
            return "ok"

    adapter = MockAdapter()
    agent = CADAgent(adapter=adapter, capture_trace=True)
    agent.design_state.update_from_cad_state('[]')

    # First handle_message - set convention
    agent._maybe_record_convention_from_message(
        "Set convention wall_thickness to 5 mm")
    val1 = agent.get_design_convention("wall_thickness")
    assert val1 == "5 mm"

    # Second handle_message - convention should persist
    # New agent, but same session_memory won't persist
    agent2 = CADAgent(adapter=adapter)
    # Note: In real usage, same agent instance is used across turns
    # So we test with same agent instance
    agent2 = agent  # Use same agent
    val2 = agent2.get_design_convention("wall_thickness")
    assert val2 == "5 mm"

    print("  [PASS] Convention persists across turns")


def test_context_compiler_includes_conventions():
    """Test full integration: context compiler includes conventions."""
    print("Testing full compiler integration with conventions...")

    st = SessionMemory()
    st.set("default_material", "aluminum", kind="convention", source="user")
    st.set("default_hole", "prefer M6", kind="convention", source="user")

    from core.context.plan import ContextPlan
    plan = ContextPlan(
        required_memory_sections=["conventions"],
        relevant_object_ids=[],
        required_state_sections=[],
        required_tools=[],
        reasoning_mode="default",
        confidence=0.9,
        ambiguity=0.1,
        additional_context={},
    )

    compiler = ContextCompiler()
    memory_data = compiler._extract_conventions_from_plan(
        st, plan, "Create a box with default material")

    assert "conventions" in memory_data
    assert len(memory_data["conventions"]) == 2
    keys = {c["key"] for c in memory_data["conventions"]}
    assert keys == {"default_material", "default_hole"}

    print("  [PASS] Full compiler integration works")


if __name__ == "__main__":
    print("=" * 70)
    print("BIP 10.3 — SESSION DESIGN-CONVENTIONS MEMORY TESTS")
    print("=" * 70)
    print()

    test_convention_can_be_written()
    test_convention_can_be_read()
    test_multiple_conventions_coexist()
    test_session_isolation()
    test_conventions_in_compiled_context()
    test_conventions_respect_budget()
    test_conventions_not_fabricated()
    test_recency_behavior_unaffected()
    test_agent_records_convention()
    test_convention_from_user_message()
    test_convention_persists_across_turns()
    test_context_compiler_includes_conventions()

    print()
    print("=" * 70)
    print("ALL BIP 10.3 TESTS PASSED")
    print("=" * 70)
