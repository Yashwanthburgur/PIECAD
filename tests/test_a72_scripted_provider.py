"""A7.2 — Deterministic scripted provider + run_evals wiring tests.

Proves:
  * scripted responses are returned in deterministic order,
  * tool calls are exposed in the expected shape,
  * a final response terminates the agent loop offline (no live LLM),
  * a fixture with ``scripted_responses`` injects the scripted provider,
  * fixtures without ``scripted_responses`` retain existing behavior.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.adapters.interfaces import CADAdapter  # noqa: E402
from core.agent import CADAgent  # noqa: E402
from scripts.scripted_provider import (  # noqa: E402
    ScriptedProvider,
    provider_from_fixture,
)
from scripts.run_evals import evaluate_fixture  # noqa: E402


class StubAdapter(CADAdapter):
    """Minimal deterministic adapter for offline eval wiring tests."""

    def __init__(self):
        self.called = []

    def get_tools(self):
        return [
            {"type": "function",
             "function": {"name": "box", "parameters": {"type": "object"}}},
        ]

    def execute_command(self, tool_name, **kwargs):
        self.called.append(tool_name)
        return json.dumps({"success": True, "id": f"{tool_name}1"})

    def get_state(self):
        return json.dumps([])


# --------------------------------------------------------------------------- #
# ScriptedProvider unit behavior
# --------------------------------------------------------------------------- #
def test_scripted_responses_returned_in_deterministic_order():
    prov = ScriptedProvider([
        (None, [("box", {"length": 10})]),
        ("Done.", None),
    ])
    first = prov.generate_with_tools([], None)
    second = prov.generate_with_tools([], None)
    third = prov.generate_with_tools([], None)  # repeats last step

    assert first.content is None
    assert second.content == "Done."
    assert third.content == "Done."  # deterministic repeat, never raises
    assert prov.calls == 3


def test_tool_calls_exposed_in_expected_shape():
    prov = ScriptedProvider([
        (None, [("box", {"length": 10, "width": 5}),
         ("cylinder", {"radius": 2})]),
    ])
    resp = prov.generate_with_tools([], None)

    assert resp.content is None
    assert len(resp.tool_calls) == 2
    tc0 = resp.tool_calls[0]
    assert tc0.id == "call_0"
    assert tc0.function.name == "box"
    assert json.loads(tc0.function.arguments) == {"length": 10, "width": 5}
    assert resp.tool_calls[1].function.name == "cylinder"


def test_empty_script_is_deterministic_empty_response():
    prov = ScriptedProvider([])
    resp = prov.generate_with_tools([], None)
    assert resp.content is None
    assert resp.tool_calls is None


def test_last_usage_shape_is_preserved():
    prov = ScriptedProvider([("hi", None)], usage={
        "prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4,
        "model": "scripted", "provider": "scripted",
    })
    assert prov.last_usage["total_tokens"] == 4
    assert ScriptedProvider([("hi", None)]).last_usage is None


# --------------------------------------------------------------------------- #
# End-to-end offline loop termination
# --------------------------------------------------------------------------- #
def test_final_response_terminates_agent_loop_offline():
    adapter = StubAdapter()
    prov = ScriptedProvider([
        (None, [("box", {"length": 10, "width": 10, "height": 10})]),
        ("Created the box.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov, capture_trace=True)
    reply, session_tools = agent.handle_message("Make a box 10x10x10")

    assert reply == "Created the box."
    assert "box" in session_tools
    # Exactly two provider calls: one tool-call phase + one final-text phase.
    assert prov.calls == 2


# --------------------------------------------------------------------------- #
# provider_from_fixture selection
# --------------------------------------------------------------------------- #
def test_provider_from_fixture_none_when_absent():
    assert provider_from_fixture({"prompt": "x"}) is None


def test_provider_from_fixture_positional_and_dict_forms():
    positional = provider_from_fixture({
        "scripted_responses": [[[("box", {"a": 1})], None], [None, "Done."]],
    })
    assert isinstance(positional, ScriptedProvider)
    assert positional.script[0][0] is None
    assert positional.script[0][1] == [("box", {"a": 1})]
    assert positional.script[1] == ("Done.", None)

    dictform = provider_from_fixture({
        "scripted_responses": [
            {"tool_calls": [["box", {"a": 1}]], "content": None},
            {"tool_calls": None, "content": "Done."},
        ],
    })
    assert dictform.script == [(None, [("box", {"a": 1})]), ("Done.", None)]


# --------------------------------------------------------------------------- #
# run_evals wiring: scripted fixture injects provider; non-scripted unaffected
# --------------------------------------------------------------------------- #
def test_evaluate_fixture_injects_scripted_provider():
    adapter = StubAdapter()
    fixture = {
        "id": "scripted_box",
        "name": "Scripted Box",
        "prompt": "Make a box 10x10x10.",
        "expected_tools_called": ["box"],
        "neutral_assertions": [],
        "scripted_responses": [
            [[("box", {"length": 10, "width": 10, "height": 10})], None],
            [None, "Created the box."],
        ],
    }
    # No live provider / API key is available; success proves the scripted
    # provider was injected (a default LLMProvider() would raise on construction).
    res = evaluate_fixture(fixture, adapter)
    assert res["passed"] is True
    assert "box" in [t.lower() for t in res["got"]]


def test_evaluate_fixture_without_scripted_responses_unchanged():
    # The non-scripted path must not build a ScriptedProvider, preserving the
    # existing default-provider behavior (no error from provider_from_fixture).
    assert provider_from_fixture(
        {"prompt": "x", "expected_tools_called": []}) is None
