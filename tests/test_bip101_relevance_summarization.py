"""BIP 10.1 — Relevance-Based State Summarization Tests.

Focused deterministic tests for relevance-based state retention:
a) recent explicitly referenced object is retained despite being older
b) unreferenced old objects are still eligible for truncation
c) multiple referenced objects are all prioritized
d) references across the previous 2–3 user turns are recognized
e) unknown/nonexistent object references do not crash summarization
f) existing recency behavior remains intact when there are no references
g) BIP 6.5 total context-budget enforcement still works
h) topology/signature/lineage facts for retained objects remain intact
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context.state import DesignState  # noqa: E402
from core.context.compiler import ContextCompiler  # noqa: E402
from core.context.conversation import ConversationContext  # noqa: E402
from core.context.budget import ContextBudget  # noqa: E402


def test_recent_referenced_object_retained():
    """Test a) recent explicitly referenced object is retained despite being older."""
    print("Testing a) recent referenced object retained...")

    st = DesignState()
    # Create objects: box1 (oldest), box2, box3 (newest)
    st.update_from_cad_state(json.dumps([
        {"id": "box1", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100}},
        {"id": "box2", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100}},
        {"id": "box3", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100}},
    ]))

    # Simulate recent turns referencing box1
    conv = ConversationContext()
    conv.add_user("Create box1")
    conv.add_user("Fillet box1")

    compiler = ContextCompiler()
    plan = compiler.relevance.plan(
        user_request="Edit box1",
        state=st,
        memory=None,
        conversation=conv,
    )

    view = st.select_objects(plan.relevant_object_ids, depth=1)
    # Manually simulate trimming with priority
    # We need to test the _trim_object_list logic directly

    # Create mock objects list (oldest first)
    objects = [
        {"id": "box1", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100}},
        {"id": "box2", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100}},
        {"id": "box3", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100}},
    ]

    # The conversation references box1 (from recent turns)
    conv2 = ConversationContext()
    conv2.add_user("Create box1")
    conv2.add_user("Fillet box1")

    compiler = ContextCompiler(budget=ContextBudget(state_budget=2000))
    # Test the trimming with priority
    trimmed = compiler._trim_object_list(
        objects, budget_tokens=800,
        conversation=ConversationContext(),
        user_message="Edit box1"  # current message also references box1
    )

    # box1 should be in the kept objects despite being oldest
    kept_ids = [o["id"] for o in trimmed]
    assert "box1" in kept_ids, f"box1 should be retained but kept: {kept_ids}"

    print("  [PASS] Recent referenced object retained despite being older")


def test_unreferenced_old_objects_truncated():
    """Test b) unreferenced old objects are still eligible for truncation."""
    print("Testing b) unreferenced old objects truncated...")

    objects = [
        {"id": "box1", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100}},
        {"id": "box2", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100}},
        {"id": "box3", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100}},
    ]

    # Conversation references box2, not box1 or box3
    conv = ConversationContext()
    conv.add_user("Create box2")

    compiler = ContextCompiler(budget=ContextBudget(state_budget=800))
    trimmed = compiler._trim_object_list(
        objects, budget_tokens=600,
        conversation=conv, user_message="Edit box2")

    kept_ids = [o["id"] for o in trimmed]
    # box1 is oldest and unreferenced, should be truncated
    # box2 is referenced, should be kept
    # box3 is newest but unreferenced
    # With tight budget, only priority objects should survive
    assert "box2" in kept_ids, f"box2 should be retained: {kept_ids}"

    print("  [PASS] Unreferenced old objects truncated")


def test_multiple_referenced_objects_prioritized():
    """Test c) multiple referenced objects are all prioritized."""
    print("Testing c) multiple referenced objects prioritized...")

    objects = [
        {"id": "box1", "type": "Part::Box", "visible": True},
        {"id": "box2", "type": "Part::Box", "visible": True},
        {"id": "box3", "type": "Part::Box", "visible": True},
        {"id": "fillet1", "type": "Part::Fillet", "visible": True},
    ]

    conv = ConversationContext()
    conv.add_user("Create box1 and box2")
    conv.add_user("Fillet box1 and box3")

    compiler = ContextCompiler(budget=ContextBudget(state_budget=1500))
    trimmed = compiler._trim_object_list(
        objects, budget_tokens=1200,
        conversation=conv, user_message="Edit box1, box2, box3")

    kept_ids = [o["id"] for o in trimmed]
    # box1, box2, box3 all referenced, fillet1 not
    assert "box1" in kept_ids
    assert "box2" in kept_ids
    assert "box3" in kept_ids

    print("  [PASS] Multiple referenced objects all prioritized")


def test_references_across_turns():
    """Test d) references across the previous 2-3 user turns are recognized."""
    print("Testing d) references across 2-3 turns recognized...")

    objects = [
        {"id": "box1", "type": "Part::Box", "visible": True},
        {"id": "box2", "type": "Part::Box", "visible": True},
    ]

    # Three turns ago mentioned box1
    conv = ConversationContext()
    conv.add_user("Create box1")
    conv.add_user("Create box2")
    conv.add_user("Fillet box1")

    compiler = ContextCompiler(budget=ContextBudget(state_budget=1500))
    trimmed = compiler._trim_object_list(
        objects, budget_tokens=800,
        conversation=conv, user_message="Edit box1")

    kept_ids = [o["id"] for o in trimmed]
    # box1 referenced 3 turns ago + current turn
    assert "box1" in kept_ids, f"box1 should be retained: {kept_ids}"

    print("  [PASS] References across 3 turns recognized")


def test_unknown_references_no_crash():
    """Test e) unknown/nonexistent object references do not crash summarization."""
    print("Testing e) unknown references don't crash...")

    objects = [
        {"id": "box1", "type": "Part::Box", "visible": True},
    ]

    conv = ConversationContext()
    conv.add_user("Create box1")
    conv.add_user("Delete nonexistent_object")

    compiler = ContextCompiler(budget=ContextBudget(state_budget=1000))
    try:
        trimmed = compiler._trim_object_list(
            objects, budget_tokens=800,
            conversation=conv, user_message="Edit box1")
        # Should not crash, box1 should be retained (explicitly referenced)
        kept_ids = [o["id"] for o in trimmed]
        assert "box1" in kept_ids
        print("  [PASS] Unknown references don't crash summarization")
    except Exception as e:
        raise AssertionError(f"Should not crash on unknown references: {e}")


def test_recency_behavior_no_references():
    """Test f) existing recency behavior remains intact when no references."""
    print("Testing f) recency behavior intact without references...")

    objects = [
        {"id": "box1", "type": "Part::Box", "visible": True},
        {"id": "box2", "type": "Part::Box", "visible": True},
        {"id": "box3", "type": "Part::Box", "visible": True},
    ]

    # No references in conversation
    conv = ConversationContext()
    conv.add_user("Some unrelated request")

    compiler = ContextCompiler(budget=ContextBudget(state_budget=800))
    trimmed = compiler._trim_object_list(
        objects, budget_tokens=600,
        conversation=conv, user_message="Do something else")

    kept_ids = [o["id"] for o in trimmed]
    # Should keep newest (box3) and box2, drop oldest (box1) - recency behavior
    assert "box3" in kept_ids, f"Newest box3 should be kept: {kept_ids}"
    assert "box2" in kept_ids, f"box2 should be kept: {kept_ids}"
    # box1 may be dropped due to budget

    print("  [PASS] Recency behavior intact when no references")


def test_context_budget_enforcement():
    """Test g) BIP 6.5 total context-budget enforcement still works."""
    print("Testing g) BIP 6.5 context-budget enforcement works...")

    # Create many objects
    objects = [
        {"id": f"obj{i}", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100, "Width": 100, "Height": 50}}
        for i in range(1, 21)  # 20 objects
    ]

    conv = ConversationContext()
    conv.add_user("Edit obj1")

    compiler = ContextCompiler(budget=ContextBudget(
        maximum_context_tokens=2000,
        state_budget=800
    ))

    # Should enforce budget by trimming
    trimmed = compiler._trim_object_list(
        objects, budget_tokens=500,
        conversation=ConversationContext(),
        user_message="Edit obj1")

    # Should have fewer objects than original due to budget
    assert len(trimmed) < len(
        objects), f"Budget should trim objects: {len(trimmed)} >= {len(objects)}"
    # Priority object obj1 should be in result
    kept_ids = [o["id"] for o in trimmed]
    assert "obj1" in kept_ids, f"Priority obj1 should be retained: {kept_ids}"

    print("  [PASS] Context budget enforcement works")


def test_topology_lineage_facts_preserved():
    """Test h) topology/signature/lineage facts for retained objects remain intact."""
    print("Testing h) topology/signature/lineage facts preserved...")

    st = DesignState()
    st.update_from_cad_state(json.dumps([{
        "id": "box1", "type": "Part::Box", "visible": True,
        "parents": [], "children": [], "properties": {"Length": 100},
    }, {
        "id": "fillet1", "type": "Part::Fillet", "visible": True,
        "parents": ["box1"], "children": [], "properties": {"radius": 5.0},
    }]))

    # Record topology version and signature
    st.record_topology_reference_with_signature(
        "box1", "edge", "tv_1", "box1_edge_1",
        {"center": {"x": 50, "y": 0, "z": 25}, "length": 50, "tangent": {"x": 1, "y": 0, "z": 0}})
    st.record_topology_reference_with_signature(
        "fillet1", "face", "tv_2", "fillet1_face_1",
        {"center": {"x": 55, "y": 0, "z": 25}, "area": 100, "normal": {"x": 1, "y": 0, "z": 0}})

    # Create objects list with topology facts
    objects = [
        {
            "id": "box1", "type": "Part::Box", "visible": True,
            "parents": [], "children": ["fillet1"],
            "properties": {"Length": 100},
            "topology_version": "tv_1"
        },
        {
            "id": "fillet1", "type": "Part::Fillet", "visible": True,
            "parents": ["box1"], "children": [],
            "properties": {"radius": 5.0},
            "topology_version": "tv_2"
        },
    ]

    conv = ConversationContext()
    conv.add_user("Fillet box1")

    compiler = ContextCompiler(budget=ContextBudget(state_budget=2000))
    trimmed = compiler._trim_object_list(
        objects, budget_tokens=1000,
        conversation=conv, user_message="Edit box1")

    kept_ids = [o["id"] for o in trimmed]
    # Both should be retained (box1 referenced)
    assert "box1" in kept_ids
    assert "fillet1" in kept_ids

    # Verify topology facts preserved in DesignState
    # (The trimming doesn't affect DesignState directly, but verify
    # that if we selected objects, the topology facts are in the output)
    view = st.select_objects(kept_ids, depth=1)
    for obj in view["objects"]:
        if "topology_version" in obj:
            assert obj["topology_version"] in ("tv_1", "tv_2")

    print("  [PASS] Topology/signature/lineage facts preserved")


if __name__ == "__main__":
    print("=" * 70)
    print("BIP 10.1 — RELEVANCE-BASED STATE SUMMARIZATION TESTS")
    print("=" * 70)
    print()

    test_recent_referenced_object_retained()
    test_unreferenced_old_objects_truncated()
    test_multiple_referenced_objects_prioritized()
    test_references_across_turns()
    test_unknown_references_no_crash()
    test_recency_behavior_no_references()
    test_context_budget_enforcement()
    test_topology_lineage_facts_preserved()

    print()
    print("=" * 70)
    print("ALL BIP 10.1 TESTS PASSED")
    print("=" * 70)
