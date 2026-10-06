"""Tests for final messages telemetry (diagnostic estimates of provider payload)."""
import json
from unittest.mock import MagicMock, patch
from core.agent import CADAgent
from core.adapters.interfaces import CADAdapter
from core.context.telemetry import estimate_tokens


class DummyAdapter(CADAdapter):
    """Minimal adapter for testing."""

    def __init__(self):
        self._tools = []
        self._state = "[]"

    def get_tools(self):
        return self._tools

    def get_state(self):
        return self._state

    def execute_command(self, name, **kwargs):
        return '{"success": true}'


class ScriptedProvider:
    """Provider that returns canned responses with usage metadata."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.last_usage = None

    def generate_with_tools(self, messages, tools):
        if not self.responses:
            raise RuntimeError("No more scripted responses")
        tool_calls, content = self.responses.pop(0)
        response = MagicMock()
        response.tool_calls = tool_calls
        response.content = content
        # Provide usage metadata like a real provider would
        self.last_usage = {
            "prompt_tokens": 1000,
            "completion_tokens": 50,
            "total_tokens": 1050,
            "model": "test-model",
            "provider": "test-provider",
        }
        return response


def test_final_messages_telemetry_fields_populated():
    """Verify the new final-messages telemetry fields are calculated and recorded."""
    adapter = DummyAdapter()
    provider = ScriptedProvider([
        ([MagicMock(function=MagicMock(
            name="box", arguments='{"id": "box1"}'))], "Done."),
    ])
    agent = CADAgent(adapter=adapter, provider=provider)

    with patch("core.api.agent", agent):
        from fastapi.testclient import TestClient
        from core.api import app
        client = TestClient(app)
        client.post("/chat", json={"message": "Create a box"})

        # Fetch telemetry from agent directly
        token_tel = agent.get_token_telemetry()
        context_tel = agent.get_context_telemetry()

    # Verify context_telemetry is a list with at least one entry
    assert isinstance(context_tel, list)
    assert len(
        context_tel) >= 1, f"Expected at least 1 context telemetry entry, got {len(context_tel)}"

    # Check the first (and likely only) entry for the new fields
    first_entry = context_tel[0]

    # New diagnostic fields should be present and non-negative
    assert "estimated_final_messages_tokens" in first_entry, \
        "estimated_final_messages_tokens missing from context telemetry"
    assert "estimated_dynamic_system_tokens" in first_entry, \
        "estimated_dynamic_system_tokens missing from context telemetry"
    assert "estimated_scratchpad_tokens" in first_entry, \
        "estimated_scratchpad_tokens missing from context telemetry"
    assert "estimated_persistent_summary_tokens" in first_entry, \
        "estimated_persistent_summary_tokens missing from context telemetry"

    # Values should be non-negative integers (chars/4 heuristic)
    assert first_entry["estimated_final_messages_tokens"] >= 0
    assert first_entry["estimated_dynamic_system_tokens"] >= 0
    assert first_entry["estimated_scratchpad_tokens"] >= 0
    assert first_entry["estimated_persistent_summary_tokens"] >= 0

    # Sanity: final_messages_tokens should be >= dynamic_system_tokens (messages includes system)
    assert first_entry["estimated_final_messages_tokens"] >= first_entry["estimated_dynamic_system_tokens"], \
        f"final_messages ({first_entry['estimated_final_messages_tokens']}) < dynamic_system ({first_entry['estimated_dynamic_system_tokens']})"

    # Sanity: final_messages should be >= scratchpad
    assert first_entry["estimated_final_messages_tokens"] >= first_entry["estimated_scratchpad_tokens"]

    # If persistent summary was used, its tokens should be > 0
    # (In this test it may be empty since no stale topology/warnings were generated)
    print(
        f"  estimated_final_messages_tokens: {first_entry['estimated_final_messages_tokens']}")
    print(
        f"  estimated_dynamic_system_tokens: {first_entry['estimated_dynamic_system_tokens']}")
    print(
        f"  estimated_scratchpad_tokens: {first_entry['estimated_scratchpad_tokens']}")
    print(
        f"  estimated_persistent_summary_tokens: {first_entry['estimated_persistent_summary_tokens']}")

    print("  [PASS] Final messages telemetry fields populated correctly")


def test_estimate_tokens_helper():
    """Verify estimate_tokens helper works as expected."""
    assert estimate_tokens("") == 0
    assert estimate_tokens("a" * 4) == 1
    assert estimate_tokens("a" * 8) == 2
    assert estimate_tokens("x") == 1  # max(1, int(1/4)) = 1
    print("  [PASS] estimate_tokens helper works correctly")


if __name__ == "__main__":
    test_estimate_tokens_helper()
    test_final_messages_telemetry_fields_populated()
    print("\nAll final messages telemetry tests passed!")
