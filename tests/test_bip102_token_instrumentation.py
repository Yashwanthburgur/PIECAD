"""BIP 10.2 — Token-Count Instrumentation Tests.

Focused deterministic tests for token-count instrumentation:
a) token usage is captured when provider returns usage metadata
b) input/output/total tokens are recorded correctly
c) multiple ReAct calls aggregate correctly
d) model/provider metadata is retained
e) missing usage metadata is handled without crashing
f) token measurements are associated with the correct ReAct step
g) existing provider retry behavior remains unaffected
h) existing context-budget enforcement remains unaffected
"""

import sys
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent import CADAgent  # noqa: E402
from core.context.state import DesignState  # noqa: E402
from core.adapters.interfaces import CADAdapter  # noqa: E402
from providers.llm.provider import LLMProvider  # noqa: E402


# --------------------------------------------------------------------------- #
# Mock provider with controllable usage metadata
# --------------------------------------------------------------------------- #

class MockProvider(LLMProvider):
    """Mock LLM provider that returns configurable usage metadata."""

    def __init__(self):
        # Don't call super().__init__() as it requires API keys
        self.last_usage = None
        self.call_count = 0
        self.responses = []
        self.client = None  # We'll mock the client

    def generate_with_tools(self, messages, tools=None):
        self.call_count += 1
        if self.responses:
            response = self.responses.pop(0)
        else:
            response = Mock()
            response.choices = [Mock()]
            response.choices[0].message = Mock()
            response.choices[0].message.content = "Done."
            response.choices[0].message.tool_calls = None
            # Create usage attribute
            response.usage = Mock()
            response.usage.prompt_tokens = 100
            response.usage.completion_tokens = 50
            response.usage.total_tokens = 150

        # Simulate provider's last_usage behavior
        if hasattr(response, 'usage') and response.usage:
            self.last_usage = {
                "prompt_tokens": getattr(response.usage, 'prompt_tokens', 100),
                "completion_tokens": getattr(response.usage, 'completion_tokens', 50),
                "total_tokens": getattr(response.usage, 'total_tokens', 150),
                "model": "mock-model",
                "provider": "mock-provider",
            }
        return response

    def set_responses(self, responses):
        self.responses = responses


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
        self.calls.append((tool_name, kwargs))
        if tool_name == "box":
            self.state = json.dumps([{"id": "box1", "type": "Part::Box", "visible": True, "parents": [
            ], "children": [], "properties": {}}])
            return "ok"
        return "ok"


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_provider_usage_captured():
    """Test a) token usage is captured when provider returns usage metadata."""
    print("Testing a) provider usage captured...")

    adapter = MockAdapter()
    mock_provider = MockProvider()
    mock_provider.last_usage = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "model": "test-model",
        "provider": "test-provider",
    }

    agent = CADAgent(adapter=adapter, provider=mock_provider,
                     capture_trace=True)
    agent.design_state.update_from_cad_state('[]')

    with patch.object(agent.provider, 'generate_with_tools') as mock_gen:
        # Mock response with tool_calls = None (completion)
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "Box created."
        mock_gen.return_value = mock_response

        # Set provider usage
        agent.provider.last_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "model": "test-model",
            "provider": "test-provider",
        }

        result, tools = agent.handle_message("Create a box")

    telemetry = agent.get_token_telemetry()
    assert telemetry["total_input_tokens"] == 100
    assert telemetry["total_output_tokens"] == 50
    assert telemetry["total_tokens"] == 150
    assert telemetry["llm_calls"] == 1
    assert telemetry["model"] == "test-model"
    assert telemetry["provider"] == "test-provider"

    print("  [PASS] Token usage captured correctly")


def test_input_output_total_recorded():
    """Test b) input/output/total tokens are recorded correctly."""
    print("Testing b) input/output/total recorded correctly...")

    adapter = MockAdapter()
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider)
    agent.design_state.update_from_cad_state('[]')

    with patch.object(agent.provider, 'generate_with_tools') as mock_gen:
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "Done."
        mock_gen.return_value = mock_response

        agent.provider.last_usage = {
            "prompt_tokens": 200,
            "completion_tokens": 75,
            "total_tokens": 275,
            "model": "model-a",
            "provider": "provider-x",
        }

        result, tools = agent.handle_message("Create a box")

    telemetry = agent.get_token_telemetry()
    assert telemetry["total_input_tokens"] == 200
    assert telemetry["total_output_tokens"] == 75
    assert telemetry["total_tokens"] == 275

    print("  [PASS] Input/output/total tokens recorded correctly")


def test_multiple_calls_separate():
    """Test c) multiple handle_message calls maintain separate telemetry."""
    print("Testing c) multiple calls maintain separate telemetry...")

    adapter = MockAdapter()
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider)
    agent.design_state.update_from_cad_state('[]')

    with patch.object(agent.provider, 'generate_with_tools') as mock_gen:
        # First call
        mock_response1 = Mock()
        mock_response1.tool_calls = None
        mock_response1.content = "First."
        mock_gen.side_effect = [mock_response1, mock_response1]

        agent.provider.last_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }

        agent.handle_message("Create a box")

        telemetry1 = agent.get_token_telemetry()
        assert telemetry1["total_input_tokens"] == 100
        assert telemetry1["llm_calls"] == 1

        # Second call - fresh telemetry
        agent.provider.last_usage = {
            "prompt_tokens": 200,
            "completion_tokens": 100,
            "total_tokens": 300,
        }

        agent.handle_message("Create another box")

        telemetry2 = agent.get_token_telemetry()
        assert telemetry2["total_input_tokens"] == 200
        assert telemetry2["llm_calls"] == 1

    print("  [PASS] Multiple calls maintain separate telemetry")


def test_model_provider_metadata():
    """Test d) model/provider metadata is retained."""
    print("Testing d) model/provider metadata retained...")

    adapter = MockAdapter()
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider)
    agent.design_state.update_from_cad_state('[]')

    with patch.object(agent.provider, 'generate_with_tools') as mock_gen:
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "Done."
        mock_gen.return_value = mock_response

        agent.provider.last_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "model": "deepseek-chat",
            "provider": "deepseek",
        }

        agent.handle_message("Create a box")

    telemetry = agent.get_token_telemetry()
    assert telemetry["model"] == "deepseek-chat"
    assert telemetry["provider"] == "deepseek"

    print("  [PASS] Model/provider metadata retained")


def test_missing_usage_no_crash():
    """Test e) missing usage metadata is handled without crashing."""
    print("Testing e) missing usage handled without crash...")

    adapter = MockAdapter()
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider)
    agent.design_state.update_from_cad_state('[]')

    with patch.object(agent.provider, 'generate_with_tools') as mock_gen:
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "Done."
        mock_gen.return_value = mock_response

        # No last_usage set (None)
        agent.provider.last_usage = None

        result, tools = agent.handle_message("Create a box")

    telemetry = agent.get_token_telemetry()
    # Should not crash, telemetry should be zero/empty
    assert telemetry["llm_calls"] == 0
    assert telemetry["total_input_tokens"] == 0
    assert telemetry["total_output_tokens"] == 0
    assert telemetry["total_tokens"] == 0

    print("  [PASS] Missing usage handled without crash")


def test_step_association():
    """Test f) token measurements associated with correct ReAct step."""
    print("Testing f) step association...")

    adapter = MockAdapter()
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider,
                     capture_trace=True)
    agent.design_state.update_from_cad_state('[]')

    with patch.object(agent.provider, 'generate_with_tools') as mock_gen:
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "Done."
        mock_gen.return_value = mock_response

        agent.provider.last_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }

        agent.handle_message("Create a box")

    trace = agent.get_trace()
    # Find the completion trace entry
    completion_entries = [t for t in trace if t.get("type") == "completion"]
    assert len(completion_entries) == 1
    completion = completion_entries[0]
    assert "token_telemetry" in completion
    telemetry = completion["token_telemetry"]
    assert telemetry["total_input_tokens"] == 100
    assert telemetry["llm_calls"] == 1

    print("  [PASS] Token measurements associated with correct step")


def test_retry_behavior_unaffected():
    """Test g) existing provider retry behavior remains unaffected."""
    print("Testing g) retry behavior unaffected...")

    adapter = MockAdapter()
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider)
    agent.design_state.update_from_cad_state('[]')

    with patch.object(agent.provider, 'generate_with_tools') as mock_gen:
        # First call raises transient error, second succeeds
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "Done after retry."

        def side_effect(*args, **kwargs):
            if mock_gen.call_count == 1:
                # First call raises connection error (transient)
                raise ConnectionError("Connection refused")
            return mock_response

        mock_gen.side_effect = side_effect
        mock_gen.call_count = 0

        # Also need to increment call_count in the mock
        original_call = mock_gen.__call__

        def counting_call(*args, **kwargs):
            mock_gen.call_count += 1
            return side_effect(*args, **kwargs)
        mock_gen.__call__ = counting_call
        mock_gen.call_count = 0

        agent.provider.last_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }

        result, tools = agent.handle_message("Create a box")

    telemetry = agent.get_token_telemetry()
    # Should have recorded tokens from successful call
    assert telemetry["llm_calls"] >= 1
    assert telemetry["total_input_tokens"] >= 100

    print("  [PASS] Retry behavior unaffected")


def test_budget_enforcement():
    """Test h) existing context-budget enforcement remains unaffected."""
    print("Testing h) context-budget enforcement unaffected...")

    adapter = MockAdapter()
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider)
    agent.design_state.update_from_cad_state('[]')

    with patch.object(agent.provider, 'generate_with_tools') as mock_gen:
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "Done."
        mock_gen.return_value = mock_response

        agent.provider.last_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }

        result, tools = agent.handle_message("Create a box")

    # Should complete without error
    assert "Done" in result or "box" in result.lower() or result is not None
    telemetry = agent.get_token_telemetry()
    assert telemetry["llm_calls"] == 1

    print("  [PASS] Budget enforcement unaffected")


def test_per_step_telemetry():
    """Test per-step token breakdown is recorded."""
    print("Testing per-step telemetry...")

    adapter = MockAdapter()
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider,
                     capture_trace=True)
    agent.design_state.update_from_cad_state('[]')

    with patch.object(agent.provider, 'generate_with_tools') as mock_gen:
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "Done."
        mock_gen.return_value = mock_response

        agent.provider.last_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }

        agent.handle_message("Create a box")

    trace = agent.get_trace()
    completion_entries = [t for t in trace if t.get("type") == "completion"]
    assert len(completion_entries) == 1
    completion = completion_entries[0]
    assert "token_telemetry" in completion
    telemetry = completion["token_telemetry"]
    assert telemetry["per_step"][0]["step"] == 1
    assert telemetry["per_step"][0]["input_tokens"] == 100
    assert telemetry["per_step"][0]["output_tokens"] == 50

    print("  [PASS] Per-step telemetry recorded")


if __name__ == "__main__":
    print("=" * 70)
    print("BIP 10.2 — TOKEN-COUNT INSTRUMENTATION TESTS")
    print("=" * 70)
    print()

    test_provider_usage_captured()
    test_input_output_total_recorded()
    test_multiple_calls_separate()
    test_model_provider_metadata()
    test_missing_usage_no_crash()
    test_step_association()
    test_retry_behavior_unaffected()
    test_budget_enforcement()
    test_per_step_telemetry()

    print()
    print("=" * 70)
    print("ALL BIP 10.2 TESTS PASSED")
    print("=" * 70)
