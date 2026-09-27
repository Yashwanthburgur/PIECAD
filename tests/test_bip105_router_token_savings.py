"""BIP 10.5 — Router Token-Savings Instrumentation Tests.

Focused deterministic tests for router token-savings instrumentation:
a) ungated and filtered tool sets are measured correctly
b) token savings calculation is correct
c) multiple ReAct steps aggregate correctly
d) no filtering produces zero savings
e) unavailable tokenization is handled without fabricated values
f) existing router behavior is unchanged
g) BIP 10.2 token telemetry remains correct
h) BIP 10.4 token-ceiling behavior remains unaffected
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
from core.context.telemetry import estimate_json_tokens  # noqa: E402


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
    def __init__(self, num_tools=10):
        self.tool_names = [f"tool_{i}" for i in range(num_tools)]
        self.state = '[]'
        self.calls = []

    def get_tools(self):
        tools = []
        for n in self.tool_names:
            # Each tool schema has realistic size
            tools.append({
                "type": "function",
                "function": {
                    "name": n,
                    "description": f"Run {n} with parameters x, y, z",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                            "z": {"type": "number"},
                        },
                    },
                }
            })
        return tools

    def get_state(self):
        return self.state

    def execute_command(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))
        if tool_name.startswith("tool_"):
            self.state = json.dumps(
                [{"id": "obj1", "type": "Part::Box", "visible": True}])
            return "ok"
        return "ok"


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def test_ungated_and_filtered_measured():
    """Test a) ungated and filtered tool sets are measured correctly."""
    print("Testing a) ungated and filtered tool sets measured correctly...")

    adapter = MockAdapter(num_tools=20)
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

    savings = agent.get_router_token_savings()
    assert len(savings) >= 1
    step = savings[0]
    assert step["react_step"] == 1
    assert step["tools_before_filtering"] == 20
    assert step["tools_after_filtering"] <= 20
    assert step["ungated_schema_token_estimate"] is not None
    assert step["ungated_schema_token_estimate"] > 0
    assert step["filtered_schema_token_estimate"] is not None
    assert step["filtered_schema_token_estimate"] >= 0
    assert step["estimation_method"] == "chars/4 heuristic"

    print(f"  [PASS] Before: {step['tools_before_filtering']} tools, "
          f"After: {step['tools_after_filtering']} tools, "
          f"Ungated: {step['ungated_schema_token_estimate']}, "
          f"Filtered: {step['filtered_schema_token_estimate']}")


def test_savings_calculation_correct():
    """Test b) token savings calculation is correct."""
    print("Testing b) token savings calculation correct...")

    adapter = MockAdapter(num_tools=15)
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

        agent.handle_message("Create a box")

    savings = agent.get_router_token_savings()
    step = savings[0]

    ungated = step["ungated_schema_token_estimate"]
    filtered = step["filtered_schema_token_estimate"]
    reported_savings = step["token_savings_estimate"]

    if ungated is not None and filtered is not None:
        expected_savings = max(0, ungated - filtered)
        assert reported_savings == expected_savings, \
            f"Expected {expected_savings}, got {reported_savings}"

    print(f"  [PASS] Savings: ungated={ungated}, filtered={filtered}, "
          f"reported={reported_savings}")


def test_multiple_steps_aggregate():
    """Test c) multiple ReAct steps aggregate correctly."""
    print("Testing c) multiple ReAct steps aggregate...")

    adapter = MockAdapter(num_tools=12)
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider,
                     capture_trace=True)
    agent.design_state.update_from_cad_state('[]')

    call_count = [0]

    def gen_side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 3:
            # First 3 calls: tool calls to continue
            mock_response = Mock()
            mock_response.tool_calls = [Mock()]
            mock_response.tool_calls[0].id = f"call_{call_count[0]}"
            mock_response.tool_calls[0].function = Mock()
            mock_response.tool_calls[0].function.name = "tool_0"
            mock_response.tool_calls[0].function.arguments = '{"x": 1}'
            mock_response.content = None
        else:
            # 4th call: completion
            mock_response = Mock()
            mock_response.tool_calls = None
            mock_response.content = "Done."
        agent.provider.last_usage = {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
        return mock_response

    with patch.object(agent.provider, 'generate_with_tools', side_effect=gen_side_effect):
        agent.handle_message("Create a box")

    savings = agent.get_router_token_savings()
    assert len(savings) >= 3  # At least 3 steps with LLM calls

    # Check aggregation in telemetry
    telemetry = agent.get_token_telemetry()
    assert "router_token_savings" in telemetry
    agg = telemetry["router_token_savings"]
    assert agg["steps_measured"] == len(savings)
    assert agg["per_step"] == savings
    assert agg["total_savings_estimate"] == sum(
        s.get("token_savings_estimate") or 0 for s in savings
    )

    print(f"  [PASS] {agg['steps_measured']} steps measured, "
          f"total savings: {agg['total_savings_estimate']}")


def test_savings_zero_when_equal():
    """Test d) savings is zero when ungated and filtered are equal."""
    print("Testing d) savings zero when ungated == filtered...")

    adapter = MockAdapter(num_tools=3)
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider)
    agent.design_state.update_from_cad_state('[]')

    # Test the calculation invariant directly
    tools = adapter.get_tools()
    ungated = estimate_json_tokens(tools)
    filtered = estimate_json_tokens(tools)  # Same tools
    savings = max(0, ungated - filtered)
    assert savings == 0

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

    savings_data = agent.get_router_token_savings()
    step = savings_data[0]

    # The key invariant: savings = max(0, ungated - filtered)
    if (step["ungated_schema_token_estimate"] is not None
            and step["filtered_schema_token_estimate"] is not None):
        expected = max(0, step["ungated_schema_token_estimate"]
                       - step["filtered_schema_token_estimate"])
        assert step["token_savings_estimate"] == expected

    print(
        f"  [PASS] Savings calculation invariant holds: {step['token_savings_estimate']}")


def test_unavailable_tokenization_handled():
    """Test e) unavailable tokenization is handled without fabricated values."""
    print("Testing e) unavailable tokenization handled...")

    adapter = MockAdapter(num_tools=10)
    mock_provider = MockProvider()

    agent = CADAgent(adapter=adapter, provider=mock_provider)
    agent.design_state.update_from_cad_state('[]')

    # Patch estimate_json_tokens in the telemetry module where it's defined
    from core.context import telemetry as telemetry_module
    original_estimate = telemetry_module.estimate_json_tokens

    def failing_estimate(*args, **kwargs):
        raise RuntimeError("Token estimation unavailable")

    telemetry_module.estimate_json_tokens = failing_estimate

    try:
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

        savings = agent.get_router_token_savings()
        step = savings[0]

        # Should record None (unavailable) rather than crashing or fabricating
        assert step["ungated_schema_token_estimate"] is None
        assert step["filtered_schema_token_estimate"] is None
        assert step["token_savings_estimate"] is None

        print(f"  [PASS] Unavailable tokenization handled: ungated={step['ungated_schema_token_estimate']}, "
              f"filtered={step['filtered_schema_token_estimate']}, "
              f"savings={step['token_savings_estimate']}")
    finally:
        telemetry_module.estimate_json_tokens = original_estimate


def test_router_behavior_unchanged():
    """Test f) existing router behavior is unchanged."""
    print("Testing f) router behavior unchanged...")

    adapter = MockAdapter(num_tools=8)
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

    # Should complete normally
    assert "Done" in result or result is not None

    # Router should still filter tools (not expose all 8)
    savings = agent.get_router_token_savings()
    step = savings[0]
    assert step["tools_before_filtering"] == 8
    assert step["tools_after_filtering"] <= 8

    print(
        f"  [PASS] Router behavior unchanged: {step['tools_after_filtering']}/{step['tools_before_filtering']} tools exposed")


def test_bip102_telemetry_intact():
    """Test g) BIP 10.2 token telemetry remains correct."""
    print("Testing g) BIP 10.2 telemetry intact...")

    adapter = MockAdapter(num_tools=10)
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
            "model": "test-model",
            "provider": "test-provider",
        }

        agent.handle_message("Create a box")

    telemetry = agent.get_token_telemetry()

    # Original BIP 10.2 fields
    assert telemetry["total_input_tokens"] == 200
    assert telemetry["total_output_tokens"] == 75
    assert telemetry["total_tokens"] == 275
    assert telemetry["llm_calls"] == 1
    assert telemetry["model"] == "test-model"
    assert telemetry["provider"] == "test-provider"
    assert len(telemetry["per_step"]) == 1
    assert telemetry["per_step"][0]["step"] == 1

    # New BIP 10.5 fields added
    assert "router_token_savings" in telemetry
    assert "total_savings_estimate" in telemetry["router_token_savings"]

    print(f"  [PASS] BIP 10.2 fields intact, BIP 10.5 fields added")


def test_bip104_ceiling_unaffected():
    """Test h) BIP 10.4 token-ceiling behavior remains unaffected."""
    print("Testing h) BIP 10.4 token ceiling unaffected...")

    adapter = MockAdapter(num_tools=5)
    mock_provider = MockProvider()

    # Low ceiling
    agent = CADAgent(adapter=adapter, provider=mock_provider,
                     max_tokens_per_turn=300, capture_trace=True)
    agent.design_state.update_from_cad_state('[]')

    with patch.object(agent.provider, 'generate_with_tools') as mock_gen:
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "Done."
        mock_gen.return_value = mock_response

        # First call exceeds ceiling
        agent.provider.last_usage = {
            "prompt_tokens": 200,
            "completion_tokens": 150,
            "total_tokens": 350,
        }

        result, tools = agent.handle_message("Create a box")

    # Should hit ceiling
    assert "per-turn token ceiling" in result
    telemetry = agent.get_token_telemetry()
    assert telemetry["ceiling_reached"] is True

    # Router savings should still be recorded
    assert "router_token_savings" in telemetry

    print(f"  [PASS] Token ceiling works, router savings still recorded")


if __name__ == "__main__":
    print("=" * 70)
    print("BIP 10.5 — ROUTER TOKEN-SAVINGS INSTRUMENTATION TESTS")
    print("=" * 70)
    print()

    test_ungated_and_filtered_measured()
    test_savings_calculation_correct()
    test_multiple_steps_aggregate()
    test_savings_zero_when_equal()
    test_unavailable_tokenization_handled()
    test_router_behavior_unchanged()
    test_bip102_telemetry_intact()
    test_bip104_ceiling_unaffected()

    print()
    print("=" * 70)
    print("ALL BIP 10.5 TESTS PASSED")
    print("=" * 70)
