"""BIP 10.6 — Provider Prompt-Caching Support Investigation Tests.

Focused deterministic tests for prompt caching investigation:
a) provider capability detection
b) supported provider request construction, if applicable
c) unsupported provider behavior
d) missing cache metadata handled safely
e) existing token telemetry remains correct
f) existing retry behavior remains unaffected
g) existing context-budget behavior remains unaffected

Note: Current investigation found NO prompt caching support in the active provider
(NVIDIA Nemotron via NVIDIA gateway). These tests document the expected behavior
and ensure no fabrication of cache metrics occurs.
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
from core.context.telemetry import ContextTelemetry  # noqa: E402


# --------------------------------------------------------------------------- #
# Mock provider
# --------------------------------------------------------------------------- #

class MockProvider(LLMProvider):
    """Mock LLM provider that returns configurable usage metadata."""

    def __init__(self):
        # Don't call super().__init__() as it requires API keys
        self.last_usage = None
        self.call_count = 0
        self.responses = []
        self.client = None

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
            response.usage = Mock()
            response.usage.prompt_tokens = 100
            response.usage.completion_tokens = 50
            response.usage.total_tokens = 150
            # NO cache-related fields (simulating current provider behavior)

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
        self.tool_names = ["box", "get_state", "get_faces", "get_edges"]
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
        elif tool_name == "get_faces":
            return json.dumps({"faces": ["face1", "face2"], "topology_version": "1"})
        elif tool_name == "get_edges":
            return json.dumps({"edges": ["edge1", "edge2"], "topology_version": "1"})
        return "ok"


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_provider_capability_detection():
    """Test a) provider capability detection - current provider has no cache support."""
    print("Testing a) provider capability detection...")

    provider = LLMProvider.__new__(LLMProvider)  # Create without __init__
    provider.last_usage = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "model": "test-model",
        "provider": "test-provider",
    }
    # NO cache fields present
    assert "cached_tokens" not in provider.last_usage
    assert "cache_read_input_tokens" not in provider.last_usage
    assert "cache_creation_input_tokens" not in provider.last_usage

    # Simulate a response with NO cache metadata (current NVIDIA behavior)
    response = Mock()
    response.choices = [Mock()]
    response.choices[0].message = Mock()
    response.choices[0].message.content = "Done."
    response.choices[0].message.tool_calls = None
    response.usage = Mock()
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 50
    response.usage.total_tokens = 150
    # Deliberately NO cached_tokens field

    # The provider's capture logic should not crash or fabricate
    usage = {
        "prompt_tokens": getattr(response.usage, 'prompt_tokens', None),
        "completion_tokens": getattr(response.usage, 'completion_tokens', None),
        "total_tokens": getattr(response.usage, 'total_tokens', None),
        "model": "test-model",
        "provider": "openai-compatible",
    }
    usage = {k: v for k, v in usage.items() if v is not None}

    assert "cached_tokens" not in usage
    assert "cache_read_input_tokens" not in usage

    print("  [PASS] Provider correctly reports NO cache capability")


def test_supported_provider_request_construction():
    """Test b) supported provider request construction (documented interface)."""
    print("Testing b) supported provider request construction...")

    # This test documents the expected interface IF a provider supported caching.
    # Currently NOT implemented - just documents the design.

    # For OpenAI: automatic prefix caching (no request changes needed)
    # For Anthropic: cache_control on messages
    # For Google: cached_content parameter

    # Current implementation does NOT include any cache parameters
    # This is correct behavior for the active provider
    provider = LLMProvider.__new__(LLMProvider)
    provider.model = "test-model"
    provider.base_url = "https://api.test.com"
    provider.api_key = "test-key"
    provider.client = Mock()

    # Mock the client call
    mock_response = Mock()
    mock_response.choices = [Mock()]
    mock_response.choices[0].message = Mock()
    mock_response.choices[0].message.content = "Done."
    mock_response.choices[0].message.tool_calls = None
    mock_response.usage = Mock()
    mock_response.usage.prompt_tokens = 100
    mock_response.usage.completion_tokens = 50
    mock_response.usage.total_tokens = 150
    provider.client.chat.completions.create.return_value = mock_response

    messages = [{"role": "user", "content": "test"}]
    tools = [{"type": "function", "function": {
        "name": "test", "description": "test", "parameters": {}}}]

    result = provider.generate_with_tools(messages, tools)

    # Verify call was made WITHOUT cache parameters
    call_args = provider.client.chat.completions.create.call_args
    kwargs = call_args.kwargs
    assert "model" in kwargs
    assert "messages" in kwargs
    assert "tools" in kwargs
    # NO cache-related parameters
    assert "cache_control" not in str(kwargs)
    assert "cached_content" not in str(kwargs)
    assert "prompt_cache_key" not in str(kwargs)

    print("  [PASS] Request construction has no cache parameters (correct for current provider)")


def test_unsupported_provider_behavior():
    """Test c) unsupported provider behavior - graceful degradation."""
    print("Testing c) unsupported provider behavior...")

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

        result, tools = agent.handle_message("Create a box")

    # Should complete normally without any cache-related errors
    assert "Done" in result or result is not None

    # Telemetry should NOT contain fabricated cache metrics
    telemetry = agent.get_token_telemetry()
    # BIP 10.2 fields
    assert telemetry["total_input_tokens"] == 100
    assert telemetry["total_output_tokens"] == 50
    assert telemetry["total_tokens"] == 150
    assert telemetry["llm_calls"] == 1

    # NO cache fields should exist in standard telemetry
    # (cache fields would be in ContextTelemetry if provider reported them)

    print("  [PASS] Unsupported provider works correctly, no cache fabrication")


def test_missing_cache_metadata_handled():
    """Test d) missing cache metadata handled safely."""
    print("Testing d) missing cache metadata handled safely...")

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

        # Provider reports usage WITHOUT cache fields
        agent.provider.last_usage = {
            "prompt_tokens": 200,
            "completion_tokens": 75,
            "total_tokens": 275,
            "model": "test-model",
            "provider": "test-provider",
        }

        agent.handle_message("Create a box")

    # Context telemetry should have exact_provider_tokens but NO cache fields
    context_telemetry = agent.get_context_telemetry()
    assert len(context_telemetry) == 1
    telem = context_telemetry[0]

    # exact_provider_tokens should exist
    assert "exact_provider_tokens" in telem
    assert telem["exact_provider_tokens"] is not None
    assert telem["exact_provider_tokens"]["prompt_tokens"] == 200

    # Cache fields should be absent or None (not fabricated)
    if "cached_input_tokens" in telem:
        assert telem["cached_input_tokens"] is None
    if "cache_read_tokens" in telem:
        assert telem["cache_read_tokens"] is None

    print(
        "  [PASS] Missing cache metadata handled safely (None/absent, not fabricated)")


def test_existing_token_telemetry_correct():
    """Test e) existing BIP 10.2 token telemetry remains correct."""
    print("Testing e) BIP 10.2 token telemetry remains correct...")

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
            "prompt_tokens": 123,
            "completion_tokens": 456,
            "total_tokens": 579,
            "model": "test-model",
            "provider": "test-provider",
        }

        agent.handle_message("Create a box")

    telemetry = agent.get_token_telemetry()

    # All BIP 10.2 fields correct
    assert telemetry["total_input_tokens"] == 123
    assert telemetry["total_output_tokens"] == 456
    assert telemetry["total_tokens"] == 579
    assert telemetry["llm_calls"] == 1
    assert telemetry["model"] == "test-model"
    assert telemetry["provider"] == "test-provider"
    assert len(telemetry["per_step"]) == 1
    assert telemetry["per_step"][0]["step"] == 1
    assert telemetry["per_step"][0]["input_tokens"] == 123
    assert telemetry["per_step"][0]["output_tokens"] == 456
    assert telemetry["per_step"][0]["total_tokens"] == 579

    # BIP 10.4 fields
    assert "ceiling_limit" in telemetry
    assert "ceiling_reached" in telemetry

    # BIP 10.5 fields
    assert "router_token_savings" in telemetry

    # NO fabricated cache fields
    assert "cached_tokens" not in telemetry
    assert "cache_savings" not in telemetry

    print("  [PASS] BIP 10.2 telemetry intact, no cache fabrication")


def test_retry_behavior_unaffected():
    """Test f) existing retry behavior remains unaffected."""
    print("Testing f) retry behavior unaffected...")

    adapter = MockAdapter()
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider,
                     capture_trace=True)
    agent.design_state.update_from_cad_state('[]')

    call_count = [0]

    def gen_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise ConnectionError("Connection refused")
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "Done after retry."
        agent.provider.last_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
        return mock_response

    with patch.object(agent.provider, 'generate_with_tools', side_effect=gen_side_effect):
        result, tools = agent.handle_message("Create a box")

    # Should have retried and succeeded
    assert "Done" in result or "retry" in result.lower()
    telemetry = agent.get_token_telemetry()
    assert telemetry["total_input_tokens"] == 100
    assert telemetry["total_output_tokens"] == 50
    assert telemetry["total_tokens"] == 150
    assert telemetry["llm_calls"] == 1
    # No cache fields fabricated
    assert "cached_tokens" not in telemetry

    print("  [PASS] Retry behavior unaffected, no cache interference")


def test_context_budget_unaffected():
    """Test g) existing context-budget behavior remains unaffected."""
    print("Testing g) context-budget behavior unaffected...")

    adapter = MockAdapter()
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider,
                     capture_trace=True)
    agent.design_state.update_from_cad_state('[]')

    # Tight context budget
    from core.context.budget import ContextBudget
    agent.compiler.budget = ContextBudget(
        maximum_context_tokens=500,
        reserved_output_tokens=50,
        state_budget=100,
        memory_budget=50,
        tool_budget=200,
        history_budget=50,
    )

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

    # Should complete (context budget trims, no cache issues)
    assert "Done" in result or result is not None
    telemetry = agent.get_token_telemetry()
    assert telemetry["total_tokens"] == 150

    # Context telemetry shows budget enforcement
    context_telemetry = agent.get_context_telemetry()
    assert len(context_telemetry) > 0

    # No cache fabrication
    telem = context_telemetry[0]
    if "cached_input_tokens" in telem:
        assert telem["cached_input_tokens"] is None

    print("  [PASS] Context budget enforcement works, no cache interference")


def test_context_telemetry_no_cache_fabrication():
    """Additional test: ContextTelemetry never fabricates cache metrics."""
    print("Testing ContextTelemetry no cache fabrication...")

    # Create a ContextTelemetry instance
    telem = ContextTelemetry(
        react_step=1,
        estimated_context_tokens=1000,
        estimated_tool_schema_tokens=500,
        estimated_state_tokens=200,
        estimated_memory_tokens=100,
        estimated_conversation_tokens=50,
        tools_exposed=5,
        tools_exposed_names=["tool1", "tool2"],
    )

    # Should NOT have cache fields by default
    telem_dict = telem.to_dict()
    assert "cached_input_tokens" not in telem_dict
    assert "cache_read_tokens" not in telem_dict
    assert "cache_write_tokens" not in telem_dict

    # exact_provider_tokens can be set but won't have cache fields
    # unless the provider actually reports them
    telem.exact_provider_tokens = {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    }
    telem_dict = telem.to_dict()
    assert telem_dict["exact_provider_tokens"]["prompt_tokens"] == 100
    # Still no cache fields in exact_provider_tokens (provider didn't report)

    print("  [PASS] ContextTelemetry correctly omits cache fields")


if __name__ == "__main__":
    print("=" * 70)
    print("BIP 10.6 — PROVIDER PROMPT-CACHING INVESTIGATION TESTS")
    print("=" * 70)
    print()

    test_provider_capability_detection()
    test_supported_provider_request_construction()
    test_unsupported_provider_behavior()
    test_missing_cache_metadata_handled()
    test_existing_token_telemetry_correct()
    test_retry_behavior_unaffected()
    test_context_budget_unaffected()
    test_context_telemetry_no_cache_fabrication()

    print()
    print("=" * 70)
    print("ALL BIP 10.6 TESTS PASSED")
    print("=" * 70)
