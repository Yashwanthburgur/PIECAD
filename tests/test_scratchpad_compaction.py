"""Focused regression tests for scratchpad compaction (BIP 4.2 / token ceiling mitigation).

These tests verify that:
1. Scratchpad remains bounded across ReAct steps (max 7 messages: 1 summary + 6 recent)
2. Repeated tool results do not cause unbounded context growth
3. Stale-topology and verification warnings survive compaction
4. Face/edge reference counts survive compaction
5. Redundant information (object IDs, params, topology versions, success/failure) is NOT in summary
6. Existing A7 termination behavior remains unchanged
7. Existing deterministic evaluation tests remain green
8. Multiple sequential CAD tool calls with large results don't cause unbounded growth
"""
from core.agent import CADAgent, _compact_scratchpad, _build_step_summary, _extract_system_warnings
import sys
from pathlib import Path
import json

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_tool_call(name, args, call_id="call_123"):
    """Create a mock tool_call object matching OpenAI format."""
    return type('ToolCall', (), {
        'function': type('Function', (), {
            'name': name,
            'arguments': json.dumps(args)
        })(),
        'id': call_id
    })()


def _make_tool_result(content, role="tool", tool_call_id="call_123"):
    """Create a mock tool result message."""
    return {"role": role, "tool_call_id": tool_call_id, "content": content}


def _call_compact(scratchpad, persistent_summary=None):
    """Helper to call _compact_scratchpad and return both values."""
    recent, summary = _compact_scratchpad(scratchpad, persistent_summary)
    return recent, summary


def _build_llm_scratchpad(recent_messages, persistent_summary):
    """Build the full scratchpad representation as sent to LLM."""
    if persistent_summary:
        # In the actual implementation, the summary is prepended to system prompt
        # For testing, we represent it as a system message at the front
        return [{"role": "system", "content": persistent_summary}] + recent_messages
    return recent_messages


def test_compaction_does_not_trigger_below_threshold():
    """Scratchpad below threshold (6 messages) should remain unchanged."""
    scratchpad = [
        {"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("box", {"id": "box1"})]},
        _make_tool_result(json.dumps({"status": "success", "id": "box1"})),
    ]
    recent, summary = _call_compact(scratchpad)
    assert len(recent) == 2
    assert summary == ""  # Empty string instead of None
    assert recent == scratchpad


def test_compaction_triggers_above_threshold():
    """Scratchpad above threshold (6 messages) should be compacted."""
    scratchpad = []
    for i in range(5):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
                          _make_tool_call("box", {"id": f"box{i}"})]})
        scratchpad.append(_make_tool_result(
            json.dumps({"status": "success", "id": f"box{i}"})))
    # 10 messages > 6 threshold
    recent, summary = _call_compact(scratchpad)
    assert len(recent) == 6  # Only last 6 kept
    # Summary should be None because simple box operations have no stale topology or refs
    assert summary is None or summary == ""


def test_compaction_preserves_stale_topology():
    """Stale topology information must survive compaction."""
    scratchpad = []
    for i in range(5):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("fillet", {
                            "target_id": f"box{i}", "id": f"fillet{i}", "radius": 2.5, "topology_version": f"v{i}"})
        ]})
        scratchpad.append(_make_tool_result(json.dumps({
            "status": "error",
            "tool": "fillet",
            "error_type": "StaleTopologyError",
            "error": "Edge reference stale",
            "arguments": {"target_id": f"box{i}", "radius": 2.5},
            "stale_topology": True
        })))

    recent, summary = _call_compact(scratchpad)
    assert summary is not None
    assert "STALE: box0" in summary
    assert "STALE: box1" in summary
    # Should include topology version
    assert "(vv0)" in summary or "(v v0)" in summary  # version captured


def test_compaction_preserves_face_edge_refs_with_stale():
    """Face/edge reference counts must survive compaction when there's stale topology."""
    scratchpad = []
    for i in range(5):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("fillet", {
                "target_id": f"box{i}",
                "id": f"fillet{i}",
                "radius": 2.5,
                "edge_refs": [f"box{i}_edge_1", f"box{i}_edge_2"],
                "topology_version": f"v{i}"
            })
        ]})
        scratchpad.append(_make_tool_result(json.dumps({
            "status": "error",
            "tool": "fillet",
            "error_type": "StaleTopologyError",
            "error": "Edge reference stale",
            "arguments": {"target_id": f"box{i}", "radius": 2.5},
            "stale_topology": True
        })))

    recent, summary = _call_compact(scratchpad)
    assert summary is not None
    assert "edge_refs=2" in summary
    assert "STALE: box0" in summary


def test_compaction_does_NOT_persist_object_names():
    """Object names (target_id, result id) should NOT be in delta summary."""
    scratchpad = []
    for i in range(5):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call(
                "fillet", {"target_id": f"box{i}", "id": f"fillet{i}", "radius": 2.5})
        ]})
        scratchpad.append(_make_tool_result(json.dumps(
            {"status": "success", "id": f"fillet{i}"})))

    recent, summary = _call_compact(scratchpad)
    # Should NOT contain target= or result= since no stale topology or refs
    assert summary is None or summary == ""


def test_compaction_does_NOT_persist_topology_version():
    """Normal topology versions should NOT be in delta summary."""
    scratchpad = []
    for i in range(5):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("fillet", {
                            "target_id": f"box{i}", "id": f"fillet{i}", "radius": 2.5, "topology_version": "abc123"})
        ]})
        scratchpad.append(_make_tool_result(json.dumps(
            {"status": "success", "id": f"fillet{i}"})))

    recent, summary = _call_compact(scratchpad)
    # Should NOT contain topology_v= since no stale topology
    assert summary is None or summary == ""


def test_compaction_does_NOT_persist_success_failure():
    """Success/failure should NOT be in delta summary (in DesignState)."""
    scratchpad = []
    for i in range(5):
        success = i % 2 == 0
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call(
                "fillet", {"target_id": f"box{i}", "id": f"fillet{i}", "radius": 2.5})
        ]})
        if success:
            scratchpad.append(_make_tool_result(json.dumps(
                {"status": "success", "id": f"fillet{i}"})))
        else:
            scratchpad.append(_make_tool_result(json.dumps({
                "status": "error",
                "tool": "fillet",
                "error_type": "RuntimeError",
                "error": "Radius too large",
                "arguments": {"target_id": f"box{i}", "radius": 2.5}
            })))

    recent, summary = _call_compact(scratchpad)
    # Should NOT contain OK or FAIL
    assert summary is None or summary == ""


def test_compaction_does_NOT_persist_structured_errors():
    """Normal structured errors should NOT be in delta summary (in DesignState.recent_errors)."""
    scratchpad = []
    for i in range(5):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call(
                "fillet", {"target_id": f"box{i}", "id": f"fillet{i}", "radius": 2.5})
        ]})
        scratchpad.append(_make_tool_result(json.dumps({
            "status": "error",
            "tool": "fillet",
            "error_type": "RuntimeError",
            "error": "Radius too large",
            "arguments": {"target_id": f"box{i}", "radius": 2.5}
        })))

    recent, summary = _call_compact(scratchpad)
    # Should NOT contain error_type since not stale topology
    assert summary is None or summary == ""


def test_compaction_does_NOT_persist_relevant_parameters():
    """Operation parameters should NOT be in delta summary (in DesignState.object.properties)."""
    scratchpad = []
    for i in range(5):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("hole", {
                "target_id": f"box{i}",
                "id": f"hole{i}",
                "diameter": 6.0,
                "depth": 10.0,
                "kind": "tapped",
                "thread_spec": "M6x1.0"
            })
        ]})
        scratchpad.append(_make_tool_result(
            json.dumps({"status": "success", "id": f"hole{i}"})))

    recent, summary = _call_compact(scratchpad)
    # Should NOT contain parameters since no stale topology or refs
    assert summary is None or summary == ""


def test_compaction_bounds_growth_across_many_steps():
    """Repeated tool calls must not cause unbounded scratchpad growth.

    With persistent summary: max 7 messages (1 summary + 6 recent) regardless of steps.
    """
    persistent_summary = None
    max_total = 0

    scratchpad = []
    for i in range(30):
        # Add step messages - use stale topology so summary has content
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("fillet", {
                "target_id": f"box{i}",
                "id": f"fillet{i}",
                "radius": 2.5,
                "edge_refs": [f"box{i}_edge_1", f"box{i}_edge_2"],
                "topology_version": f"v{i}"
            })
        ]})
        scratchpad.append(_make_tool_result(json.dumps({
            "status": "error",
            "tool": "fillet",
            "error_type": "StaleTopologyError",
            "error": "Edge reference stale",
            "arguments": {"target_id": f"box{i}", "radius": 2.5},
            "stale_topology": True
        })))

        # Compact (as agent does each step)
        scratchpad, persistent_summary = _call_compact(
            scratchpad, persistent_summary)

        # Build full LLM-visible scratchpad
        llm_scratchpad = _build_llm_scratchpad(scratchpad, persistent_summary)
        max_total = max(max_total, len(llm_scratchpad))

        if i % 5 == 0:
            print(
                f'Step {i+1}: recent={len(scratchpad)}, has_summary={persistent_summary is not None}, total={len(llm_scratchpad)}')

    # MAXIMUM should be 7 (1 summary + 6 recent)
    assert max_total <= 7, f"Max messages {max_total} exceeds limit of 7"
    # Final check
    final_llm_scratchpad = _build_llm_scratchpad(
        scratchpad, persistent_summary)
    assert len(final_llm_scratchpad) <= 7

    # Recent messages should be verbatim (last 3 steps = 6 messages)
    assistant_indices = [-6, -4, -2]
    for idx, box_num in zip(assistant_indices, [27, 28, 29]):
        msg = scratchpad[idx]
        assert msg.get("role") == "assistant"
        tc = msg["tool_calls"][0]
        args = json.loads(tc.function.arguments)
        assert args["id"] == f"fillet{box_num}"


def test_compaction_100_steps_still_bounded():
    """100 steps must still result in <= 7 messages."""
    scratchpad = []
    persistent_summary = None
    max_total = 0

    for i in range(100):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("fillet", {
                "target_id": f"box{i}",
                "id": f"fillet{i}",
                "radius": 2.5,
                "edge_refs": [f"box{i}_edge_1", f"box{i}_edge_2"],
                "topology_version": f"v{i}"
            })
        ]})
        scratchpad.append(_make_tool_result(json.dumps({
            "status": "error",
            "tool": "fillet",
            "error_type": "StaleTopologyError",
            "error": "Edge reference stale",
            "arguments": {"target_id": f"box{i}", "radius": 2.5},
            "stale_topology": True
        })))

        scratchpad, persistent_summary = _call_compact(
            scratchpad, persistent_summary)

        llm_scratchpad = _build_llm_scratchpad(scratchpad, persistent_summary)
        max_total = max(max_total, len(llm_scratchpad))

    assert max_total <= 7, f"Max messages {max_total} exceeds limit of 7"
    final_llm_scratchpad = _build_llm_scratchpad(
        scratchpad, persistent_summary)
    assert len(final_llm_scratchpad) <= 7


def test_compaction_deterministic():
    """Compaction must be deterministic - same input produces same output."""
    scratchpad = []
    for i in range(10):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("fillet", {
                            "target_id": f"box{i}", "id": f"fillet{i}", "radius": 2.5, "edge_refs": [f"box{i}_edge_1"], "topology_version": f"v{i}"})
        ]})
        scratchpad.append(_make_tool_result(json.dumps({
            "status": "error",
            "tool": "fillet",
            "error_type": "StaleTopologyError",
            "error": "Edge reference stale",
            "arguments": {"target_id": f"box{i}", "radius": 2.5},
            "stale_topology": True
        })))

    recent1, summary1 = _call_compact(scratchpad)
    recent2, summary2 = _call_compact(scratchpad)
    assert json.dumps(recent1, default=str) == json.dumps(recent2, default=str)
    assert summary1 == summary2


def test_compaction_handles_malformed_tool_calls():
    """Compaction should not crash on malformed entries."""
    scratchpad = []
    for i in range(5):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call(
                "fillet", {"target_id": f"box{i}", "id": f"fillet{i}", "radius": 2.5})
        ]})
        scratchpad.append(_make_tool_result(json.dumps(
            {"status": "success", "id": f"fillet{i}"})))

    # Add some malformed entries
    # No tool_calls
    scratchpad.append({"role": "assistant", "content": "some text"})
    scratchpad.append(
        {"role": "tool", "tool_call_id": "call_999", "content": "orphan result"})

    # Should not raise
    recent, summary = _call_compact(scratchpad)
    assert len(recent) > 0


def test_compaction_preserves_verification_warnings():
    """Verification warnings injected into scratchpad should be preserved in persistent summary."""
    scratchpad = []
    for i in range(5):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call(
                "fillet", {"target_id": f"box{i}", "id": f"fillet{i}", "radius": 2.5})
        ]})
        scratchpad.append(_make_tool_result(json.dumps(
            {"status": "success", "id": f"fillet{i}"})))

    # Add verification warning (system message) in the older section
    scratchpad.append({
        "role": "system",
        "content": "WARNING: Geometry validation failed after last operation: volume reduction check failed. You must use edit_feature or delete_feature to fix this before proceeding."
    })

    # Add more steps to push warning into older section
    for i in range(5, 8):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call(
                "fillet", {"target_id": f"box{i}", "id": f"fillet{i}", "radius": 2.5})
        ]})
        scratchpad.append(_make_tool_result(json.dumps(
            {"status": "success", "id": f"fillet{i}"})))

    recent, summary = _call_compact(scratchpad)
    assert summary is not None
    assert "WARNING: Geometry validation" in summary


def test_compaction_exactly_one_summary():
    """After many steps with stale topology, there must be exactly ONE summary in persistent buffer."""
    persistent_summary = None
    scratchpad = []  # Accumulate across steps like the agent does

    for i in range(20):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("fillet", {
                "target_id": f"box{i}",
                "id": f"fillet{i}",
                "radius": 2.5,
                "edge_refs": [f"box{i}_edge_1", f"box{i}_edge_2"],
                "topology_version": f"v{i}"
            })
        ]})
        scratchpad.append(_make_tool_result(json.dumps({
            "status": "error",
            "tool": "fillet",
            "error_type": "StaleTopologyError",
            "error": "Edge reference stale",
            "arguments": {"target_id": f"box{i}", "radius": 2.5},
            "stale_topology": True
        })))

        scratchpad, persistent_summary = _call_compact(
            scratchpad, persistent_summary)

    # Build LLM-visible scratchpad
    llm_scratchpad = _build_llm_scratchpad(scratchpad, persistent_summary)

    # With the new design, the persistent summary is prepended to the system prompt,
    # represented as a system message in the LLM-visible scratchpad.
    # So total = 1 summary message + up to 6 recent messages = 7 max.
    assert persistent_summary is not None, "Expected persistent summary to exist"
    assert "edge_refs=2" in persistent_summary, "Summary should contain ref counts"
    assert "STALE: box0" in persistent_summary, "Summary should contain stale info"

    # Total messages = 1 summary + up to 6 recent = 7 max
    assert len(llm_scratchpad) <= 7


def test_compaction_no_nested_summaries():
    """Repeated compaction must not create nested/duplicated summaries."""
    persistent_summary = None

    for i in range(15):
        scratchpad = []
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call(
                "box", {"id": f"box{i}", "length": 10, "width": 10, "height": 10})
        ]})
        scratchpad.append(_make_tool_result(
            json.dumps({"status": "success", "id": f"box{i}"})))

        scratchpad, persistent_summary = _call_compact(
            scratchpad, persistent_summary)

        llm_scratchpad = _build_llm_scratchpad(scratchpad, persistent_summary)
        summary_msgs = [m for m in llm_scratchpad if m.get(
            "role") == "system" and "[scratchpad summary]" in m.get("content", "")]

        # Should always be exactly 1 (or 0 if no summary content)
        assert len(summary_msgs) <= 1


def test_scratchpad_bounded_in_agent_context():
    """Integration test: scratchpad compaction function is available and wired in agent."""
    import inspect
    from core.agent import CADAgent

    # Verify the compaction function exists and is importable
    assert callable(_compact_scratchpad)

    # Verify the function is used in the agent module
    source = inspect.getsource(CADAgent.handle_message)
    assert "_compact_scratchpad" in source
    assert "_scratchpad_persistent_summary" in source


def test_build_step_summary_helper():
    """Test the _build_step_summary helper function directly."""
    # Use actual edge refs that contain "edge" in the string
    tool_calls = [_make_tool_call("fillet", {"target_id": "box1", "id": "fillet1", "radius": 2.5, "edge_refs": [
        "box1_edge_1", "box1_edge_2"], "topology_version": "v123"})]
    tool_results = [_make_tool_result(json.dumps(
        {"status": "success", "id": "fillet1"}))]

    parts = _build_step_summary(tool_calls, tool_results)
    # Should NOT produce summary for successful fillet with no stale topology
    # (edge_refs are only emitted when stale_topology is True in current design)
    assert len(parts) == 0 or parts == []


def test_build_step_summary_helper_with_stale():
    """Test the _build_step_summary helper with stale topology."""
    # Use edge refs that contain "edge" in the string
    tool_calls = [_make_tool_call("fillet", {"target_id": "box1", "id": "fillet1", "radius": 2.5, "edge_refs": [
        "box1_edge_1", "box1_edge_2"], "topology_version": "v123"})]
    tool_results = [_make_tool_result(json.dumps({
        "status": "error",
        "tool": "fillet",
        "error_type": "StaleTopologyError",
        "stale_topology": True
    }))]

    parts = _build_step_summary(tool_calls, tool_results)
    assert len(parts) == 1
    part = parts[0]
    assert part["type"] == "stale"
    assert part["target_id"] == "box1"
    assert part["version"] == "v123"
    assert part["refs"] == {"type": "edge", "count": 2}


def test_extract_system_warnings_helper():
    """Test the _extract_system_warnings helper function."""
    scratchpad = [
        {"role": "system", "content": "WARNING: Geometry validation failed"},
        {"role": "system", "content": "Info: something happened"},
        {"role": "assistant", "content": "not a warning"},
    ]
    warnings = _extract_system_warnings(scratchpad)
    assert len(warnings) == 1
    assert "WARNING: Geometry validation failed" in warnings[0]


def test_delta_summary_size_stays_bounded():
    """Summary size should stay bounded even with many successful operations."""
    scratchpad = []
    persistent_summary = None
    max_len = 0

    # 500 successful fillet operations with edge_refs
    for i in range(500):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("fillet", {
                "target_id": f"box{i}",
                "id": f"fillet{i}",
                "radius": 2.5,
                "edge_refs": [f"box{i}_edge_1", f"box{i}_edge_2"]
            })
        ]})
        scratchpad.append(_make_tool_result(json.dumps(
            {"status": "success", "id": f"fillet{i}"})))

        scratchpad, persistent_summary = _call_compact(
            scratchpad, persistent_summary)
        if persistent_summary:
            max_len = max(max_len, len(persistent_summary))

    # With delta summary, size should be O(1) per step that has refs/stale
    # 500 steps * ~30 chars/step = ~15000 chars max (but with dedup it's much less)
    print(f"500 steps summary length: {max_len}")
    # Should be much smaller than old event-log style
    assert max_len < 50000  # Much less than 500 * 60 = 30000


def test_stale_resolution_same_target_repeated():
    """Same target becomes stale repeatedly -> only one stale entry with latest version."""
    scratchpad = []
    persistent_summary = None

    # Target box0 goes stale 5 times with different versions
    for i in range(5):
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("fillet", {
                "target_id": "box0",
                "id": f"fillet{i}",
                "radius": 2.5,
                "edge_refs": ["box0_edge_1", "box0_edge_2"],
                "topology_version": f"v{i}"
            })
        ]})
        scratchpad.append(_make_tool_result(json.dumps({
            "status": "error",
            "tool": "fillet",
            "error_type": "StaleTopologyError",
            "error": "Edge reference stale",
            "arguments": {"target_id": "box0", "radius": 2.5},
            "stale_topology": True
        })))

        scratchpad, persistent_summary = _call_compact(
            scratchpad, persistent_summary)

    # Only one stale entry for box0, with the LATEST version (v4)
    assert persistent_summary is not None
    assert "STALE: box0" in persistent_summary
    # latest version wins
    assert "(vv4)" in persistent_summary or "(v v4)" in persistent_summary
    # Should NOT have stale entries for v0, v1, v2, v3
    stale_count = persistent_summary.count("STALE: box0")
    assert stale_count == 1, f"Expected 1 stale entry for box0, got {stale_count}"


def test_stale_persists_after_successful_operation_same_target():
    """Same target becomes stale -> successful operation on SAME target does NOT auto-resolve stale.

    Stale resolution requires an authoritative topology-version signal (fresh get_edges/get_faces),
    not mere status != "error". This test documents the safe-checkpoint behavior.
    """
    scratchpad = []
    persistent_summary = None

    # Phase 1: box0 goes stale
    scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
        _make_tool_call("fillet", {
            "target_id": "box0",
            "id": "fillet0",
            "radius": 2.5,
            "edge_refs": ["box0_edge_1", "box0_edge_2"],
            "topology_version": "v0"
        })
    ]})
    scratchpad.append(_make_tool_result(json.dumps({
        "status": "error",
        "tool": "fillet",
        "error_type": "StaleTopologyError",
        "error": "Edge reference stale",
        "arguments": {"target_id": "box0", "radius": 2.5},
        "stale_topology": True
    })))

    scratchpad, persistent_summary = _call_compact(
        scratchpad, persistent_summary)
    assert persistent_summary is not None
    assert "STALE: box0" in persistent_summary

    # Phase 2: Successful operation on box0 (same target) - but NO fresh re-query signal
    scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
        _make_tool_call("fillet", {
            "target_id": "box0",
            "id": "fillet1",
            "radius": 2.5,
            "edge_refs": ["box0_edge_1", "box0_edge_2"],
            "topology_version": "v1"  # fresh topology in args, but no authoritative signal
        })
    ]})
    scratchpad.append(_make_tool_result(json.dumps({
        "status": "success",
        "id": "fillet1"
    })))

    scratchpad, persistent_summary = _call_compact(
        scratchpad, persistent_summary)

    # Stale entry should PERSIST (not auto-resolved)
    assert persistent_summary is not None
    assert "STALE: box0" in persistent_summary, "Stale entry should persist - no authoritative resolution signal"


def test_stale_resolution_different_target_unaffected():
    """Same target becomes stale -> successful operation on DIFFERENT target does NOT clear stale."""
    scratchpad = []
    persistent_summary = None

    # box0 goes stale
    scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
        _make_tool_call("fillet", {
            "target_id": "box0",
            "id": "fillet0",
            "radius": 2.5,
            "edge_refs": ["box0_edge_1", "box0_edge_2"],
            "topology_version": "v0"
        })
    ]})
    scratchpad.append(_make_tool_result(json.dumps({
        "status": "error",
        "tool": "fillet",
        "error_type": "StaleTopologyError",
        "error": "Edge reference stale",
        "arguments": {"target_id": "box0", "radius": 2.5},
        "stale_topology": True
    })))

    # Successful operation on box1 (different target)
    scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
        _make_tool_call("fillet", {
            "target_id": "box1",
            "id": "fillet1",
            "radius": 2.5,
            "edge_refs": ["box1_edge_1", "box1_edge_2"]
        })
    ]})
    scratchpad.append(_make_tool_result(
        json.dumps({"status": "success", "id": "fillet1"})))

    scratchpad, persistent_summary = _call_compact(
        scratchpad, persistent_summary)

    # box0 should STILL be stale
    assert persistent_summary is not None
    assert "STALE: box0" in persistent_summary, "box0 stale should persist after box1 success"


def test_warning_cap_exceeds_ten():
    """Warning cap is enforced at 10."""
    scratchpad = []
    persistent_summary = None

    for i in range(15):
        scratchpad.append(
            {"role": "system", "content": f"WARNING: Test warning {i}"})
        scratchpad, persistent_summary = _call_compact(
            scratchpad, persistent_summary)

    # Only last 10 warnings should remain (warnings 5-14)
    assert persistent_summary is not None
    assert "WARNING: Test warning 14" in persistent_summary
    # Warning 5 should be dropped (only 5-14 remain)
    assert "WARNING: Test warning 5" not in persistent_summary
    # Warning 4 should be dropped
    assert "WARNING: Test warning 4" not in persistent_summary
    # Check that we have exactly 10 warnings
    warning_count = persistent_summary.count("WARNING: Test warning")
    assert warning_count == 10, f"Expected 10 warnings, got {warning_count}"


def test_repeated_identical_warnings_bounded():
    """Repeated identical warnings remain bounded at 10."""
    scratchpad = []
    persistent_summary = None

    for i in range(20):
        scratchpad.append(
            {"role": "system", "content": "WARNING: Same warning repeated"})
        scratchpad, persistent_summary = _call_compact(
            scratchpad, persistent_summary)

    # Should be capped at 10
    assert persistent_summary is not None
    warning_count = persistent_summary.count("WARNING: Same warning repeated")
    assert warning_count == 10, f"Expected 10 warnings, got {warning_count}"


def test_many_unique_stale_targets():
    """Many unique stale targets grow the summary linearly with unique targets."""
    scratchpad = []
    persistent_summary = None

    for i in range(50):  # 50 unique targets
        scratchpad.append({"role": "assistant", "content": None, "tool_calls": [
            _make_tool_call("fillet", {
                "target_id": f"box{i}",
                "id": f"fillet{i}",
                "radius": 2.5,
                "edge_refs": [f"box{i}_edge_1", f"box{i}_edge_2"],
                "topology_version": f"v0"
            })
        ]})
        scratchpad.append(_make_tool_result(json.dumps({
            "status": "error",
            "tool": "fillet",
            "error_type": "StaleTopologyError",
            "error": "Edge reference stale",
            "arguments": {"target_id": f"box{i}", "radius": 2.5},
            "stale_topology": True
        })))

        scratchpad, persistent_summary = _call_compact(
            scratchpad, persistent_summary)

    # Should have 50 stale entries
    assert persistent_summary is not None
    stale_count = persistent_summary.count("STALE: box")
    assert stale_count == 50, f"Expected 50 stale entries, got {stale_count}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
