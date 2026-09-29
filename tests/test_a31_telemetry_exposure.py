"""Tests for A3.1: Telemetry exposure at API level."""
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


def test_telemetry_exposure_via_api():
    """Verify that the /api/telemetry/turn endpoint returns structured telemetry."""
    # Create agent with test doubles
    adapter = DummyAdapter()
    provider = ScriptedProvider([
        ([MagicMock(function=MagicMock(
            name="box", arguments='{"id": "box1"}'))], None),
    ])
    agent = CADAgent(adapter=adapter, provider=provider)

    # Patch the global agent in api module
    with patch("core.api.agent", agent):
        client = TestClient(app)

        # Call chat endpoint to trigger a turn
        response = client.post("/chat", json={"message": "Create a box"})
        assert response.status_code == 200
        assert "reply" in response.json()

        # Now fetch telemetry via the API endpoint
        telemetry_response = client.get("/api/telemetry/turn")
        assert telemetry_response.status_code == 200

        data = telemetry_response.json()

        # Verify all three telemetry sections are present
        assert "token_telemetry" in data
        assert "context_telemetry" in data
        assert "router_token_savings" in data

        # Verify token_telemetry uses the public method structure
        token_tel = data["token_telemetry"]
        assert "total_input_tokens" in token_tel
        assert "total_output_tokens" in token_tel
        assert "total_tokens" in token_tel
        assert "llm_calls" in token_tel
        assert "router_token_savings" in token_tel

        # Verify context_telemetry is a list
        assert isinstance(data["context_telemetry"], list)

        # Verify router_token_savings is a list
        assert isinstance(data["router_token_savings"], list)

        print("  [PASS] Telemetry exposed correctly via /api/telemetry/turn")


def test_telemetry_exposure_matches_agent_methods():
    """Verify API telemetry matches the agent's public methods exactly."""
    adapter = DummyAdapter()
    provider = ScriptedProvider([
        ([MagicMock(function=MagicMock(
            name="box", arguments='{"id": "box1"}'))], None),
    ])
    agent = CADAgent(adapter=adapter, provider=provider)

    with patch("core.api.agent", agent):
        client = TestClient(app)

        # Trigger a turn
        client.post("/chat", json={"message": "Create a box"})

        # Get telemetry via API
        api_response = client.get("/api/telemetry/turn")
        api_data = api_response.json()

        # Get telemetry directly from agent methods
        agent_token = agent.get_token_telemetry()
        agent_context = agent.get_context_telemetry()
        agent_router = agent.get_router_token_savings()

        # They should match exactly
        assert api_data["token_telemetry"] == agent_token
        assert api_data["context_telemetry"] == agent_context
        assert api_data["router_token_savings"] == agent_router

        print("  [PASS] API telemetry matches agent public methods exactly")


if __name__ == "__main__":
    test_telemetry_exposure_via_api()
    test_telemetry_exposure_matches_agent_methods()
    print("\nAll A3.1 telemetry exposure tests passed!")
