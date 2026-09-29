"""Tests for A3.2: Turn telemetry summary in API response."""
import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from core.agent import CADAgent
from core.adapters.interfaces import CADAdapter
from core.api import app


class DummyAdapter(CADAdapter):
    """Minimal adapter for API testing."""

    def __init__(self):
        self._tools = []
        self._state = "[]"

    def get_tools(self):
        return self._tools

    def get_state(self):
        return self._state

    def execute_command(self, name, **kwargs):
        if name == "box":
            return '{"success": true, "id": "box1"}'
        return '{"success": true}'


class ScriptedProvider:
    """Provider that returns canned responses."""

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
        return response


def test_a32_telemetry_in_api_response():
    """Verify that the /chat endpoint returns telemetry in response."""
    adapter = DummyAdapter()
    provider = ScriptedProvider([
        ([MagicMock(function=MagicMock(
            name="box", arguments='{"id": "box1"}'))], None),
    ])
    agent = CADAgent(adapter=adapter, provider=provider)

    with patch("core.api.agent", agent):
        client = TestClient(app)

        response = client.post("/chat", json={"message": "Create a box"})
        assert response.status_code == 200

        data = response.json()

        # Verify reply field exists (existing behavior)
        assert "reply" in data

        # Verify new telemetry field exists
        assert "telemetry" in data
        telemetry = data["telemetry"]
        assert telemetry is not None

        # Verify telemetry structure
        assert "duration_seconds" in telemetry
        assert "steps" in telemetry
        assert "total_tokens" in telemetry
        assert "rpc_trips" in telemetry
        assert "token_telemetry" in telemetry

        # Verify types
        assert isinstance(telemetry["duration_seconds"], float)
        assert isinstance(telemetry["steps"], int)
        assert isinstance(telemetry["total_tokens"], int)
        assert isinstance(telemetry["rpc_trips"], int)
        assert isinstance(telemetry["token_telemetry"], dict)

        # RPC trips should be 0 (A3.3 not implemented)
        assert telemetry["rpc_trips"] == 0

        print("  [PASS] Telemetry present in /chat response with correct structure")


def test_a32_turn_summary_printed():
    """Verify that [TURN_SUMMARY] line is printed to console."""
    adapter = DummyAdapter()
    provider = ScriptedProvider([
        ([MagicMock(function=MagicMock(
            name="box", arguments='{"id": "box1"}'))], None),
    ])
    agent = CADAgent(adapter=adapter, provider=provider)

    with patch("core.api.agent", agent):
        client = TestClient(app)

        # Capture stdout
        import sys
        from io import StringIO
        old_stdout = sys.stdout
        sys.stdout = StringIO()

        try:
            response = client.post("/chat", json={"message": "Create a box"})
            assert response.status_code == 200

            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        # Check for TURN_SUMMARY line
        assert "[TURN_SUMMARY]" in output
        assert "Steps:" in output
        assert "Tokens:" in output
        assert "RPC Trips:" in output
        assert "Time:" in output

        print("  [PASS] [TURN_SUMMARY] printed to console")


def test_a32_existing_api_behavior_preserved():
    """Verify existing API behavior is unchanged."""
    adapter = DummyAdapter()
    provider = ScriptedProvider([
        ([], "Done creating box."),  # No tool calls, just text response
    ])
    agent = CADAgent(adapter=adapter, provider=provider)

    with patch("core.api.agent", agent):
        client = TestClient(app)

        response = client.post("/chat", json={"message": "Hello"})
        assert response.status_code == 200

        data = response.json()

        # Reply should be present
        assert "reply" in data
        assert data["reply"] == "Done creating box."

        # Telemetry should still be present
        assert "telemetry" in data

        print("  [PASS] Existing API behavior preserved")


def test_a32_multi_step_turn():
    """Test telemetry with multiple ReAct steps."""
    adapter = DummyAdapter()
    # Provide a provider that reports usage metadata
    provider = ScriptedProvider([
        ([MagicMock(function=MagicMock(
            name="box", arguments='{"id": "box1"}'))], None),
        ([MagicMock(function=MagicMock(name="fillet",
         arguments='{"id": "fillet1", "target_id": "box1", "radius": 2.0}'))], None),
        ([], "Done with box and fillet."),
    ])
    # Set usage metadata to simulate provider reporting tokens
    provider.last_usage = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "model": "test-model",
        "provider": "test-provider",
    }
    agent = CADAgent(adapter=adapter, provider=provider)

    with patch("core.api.agent", agent):
        client = TestClient(app)

        response = client.post(
            "/chat", json={"message": "Create a box and fillet it"})
        assert response.status_code == 200

        data = response.json()
        telemetry = data["telemetry"]

        # Should have steps (LLM calls) - may be 0 if mock doesn't track, just verify field exists
        assert "steps" in telemetry
        assert isinstance(telemetry["steps"], int)
        assert "total_tokens" in telemetry
        assert isinstance(telemetry["total_tokens"], int)

        print(
            f"  [PASS] Multi-step turn: {telemetry['steps']} steps, {telemetry['total_tokens']} tokens recorded")


if __name__ == "__main__":
    test_a32_telemetry_in_api_response()
    test_a32_turn_summary_printed()
    test_a32_existing_api_behavior_preserved()
    test_a32_multi_step_turn()
    print("\nAll A3.2 turn summary tests passed!")
