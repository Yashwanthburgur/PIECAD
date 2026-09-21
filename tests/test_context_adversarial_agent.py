"""BIP 4.2.2 — Adversarial: H. Agent Integration Regression.

Verify the Context Engine integration in CADAgent does NOT break handle_message(),
tool execution, ReAct iteration, retry behavior, tool-result recording,
conversation recording, DesignState updates, or telemetry generation.

Uses a lightweight stub adapter + scripted provider (the minimal adapter surface
required by the CADAdapter ABC), mirroring the existing test architecture.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from core.adapters.interfaces import CADAdapter  # noqa: E402
from core.agent import CADAgent  # noqa: E402


class ScriptedProvider:
    """Returns scripted LLM responses: list of (content_or_None, tool_calls)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def generate_with_tools(self, messages, tools=None):
        from types import SimpleNamespace
        step = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        content, tool_calls = step
        tcs = None
        if tool_calls:
            tcs = [
                SimpleNamespace(
                    id=f"call_{i}",
                    function=SimpleNamespace(
                        name=name,
                        arguments=json.dumps(args),
                    ),
                )
                for i, (name, args) in enumerate(tool_calls)
            ]
        return SimpleNamespace(content=content, tool_calls=tcs)


class StubAdapter(CADAdapter):
    """Minimal adapter surface (per CADAdapter ABC) driving its own state."""

    def __init__(self, tool_names=None):
        self.tool_names = tool_names or [
            "box", "hole", "get_state", "get_faces"]
        self.objects = []

    def get_tools(self):
        return [{"type": "function", "function": {
            "name": n, "description": f"run {n}",
            "parameters": {"type": "object", "properties": {}}}}
            for n in self.tool_names]

    def execute_command(self, tool_name, **kwargs):
        # Append a new object on a creator tool to exercise state evolution.
        if tool_name == "box":
            self.objects.append({"id": "box1", "label": "Box", "type": "Part::Box",
                                 "visible": True, "parents": [], "children": [],
                                 "properties": {"Length": kwargs.get("Length", 100.0)}})
        if tool_name == "hole":
            self.objects.append({"id": "hole1", "label": "Hole", "type": "Part::Cut",
                                 "visible": False, "parents": [], "children": ["box1"],
                                 "properties": {}})
        return json.dumps({"status": "success", "id": self.objects[-1]["id"]})

    def get_state(self):
        return json.dumps(self.objects)


def test_agent_full_tool_flow():
    # Producer: box tool called once, then provider finalises.
    prov = ScriptedProvider([
        (None, [("box", {"Length": 100.0})]),
        ("Created Box001.", None),
    ])
    from core.agent import CADAgent
    agent = CADAgent(adapter=StubAdapter(), provider=prov)
    reply, session_tools = agent.handle_message("Create a 100 x 50 x 20 box")
    assert isinstance(reply, str)
    assert "box" in session_tools
    # DesignState was updated from adapter state.
    assert "box1" in agent.design_state.objects


def test_agent_records_tool_result_into_design_state():
    prov = ScriptedProvider([
        (None, [("box", {"Length": 100.0})]),
        ("Done.", None),
    ])
    from core.agent import CADAgent
    agent = CADAgent(adapter=StubAdapter(), provider=prov)
    agent.handle_message("Make a box")
    ops = agent.design_state.get_recent_operations()
    assert any(op.tool == "box" for op in ops)


def test_agent_records_conversation():
    prov = ScriptedProvider([("Created.", None)])
    from core.agent import CADAgent
    agent = CADAgent(adapter=StubAdapter(), provider=prov)
    agent.handle_message("Create a box")
    # The user request + assistant reply are recorded in conversation context.
    texts = " ".join(
        t.content or "" for t in agent.conversation.recent_turns())
    assert "Create a box" in texts
    assert "Created." in texts


def test_agent_retry_on_transient_error():
    class FlakyAdapter(StubAdapter):
        def __init__(self):
            super().__init__(["box", "get_state"])
            self.attempts = 0

        def execute_command(self, tool_name, **kwargs):
            if tool_name == "box" and self.attempts == 0:
                self.attempts += 1
                raise ConnectionError("connection reset by peer")
            return json.dumps({"status": "success", "id": "box1"})

    prov = ScriptedProvider([
        (None, [("box", {})]),
        ("Created.", None),
    ])
    from core.agent import CADAgent
    agent = CADAgent(adapter=FlakyAdapter(), provider=prov)
    reply, _ = agent.handle_message("Create a box")
    assert agent.adapter.attempts == 1  # retried, not failed
    assert isinstance(reply, str)


def test_agent_telemetry_generated():
    prov = ScriptedProvider([("Created.", None)])
    from core.agent import CADAgent
    agent = CADAgent(adapter=StubAdapter(), provider=prov)
    agent.handle_message("Create a box")
    tel = agent.get_context_telemetry()
    assert isinstance(tel, list)
    assert len(tel) >= 1
    entry = tel[0]
    assert "estimated_context_tokens" in entry
    assert "tools_exposed" in entry


def test_agent_react_multi_step_iteration():
    # Two tool calls across two ReAct steps, then completion.
    prov = ScriptedProvider([
        (None, [("box", {"Length": 100.0})]),
        (None, [("hole", {"Diameter": 20.0})]),
        ("Hole added through box.", None),
    ])
    from core.agent import CADAgent
    agent = CADAgent(adapter=StubAdapter(), provider=prov)
    reply, session_tools = agent.handle_message("Add a hole through the box")
    assert "box" in session_tools
    assert "hole" in session_tools
    assert agent.provider.calls == 3  # two tool steps + one final text
    # Both operations tracked.
    tools_used = {op.tool for op in agent.design_state.get_recent_operations()}
    assert {"box", "hole"} <= tools_used


def test_agent_captures_exact_provider_tokens_in_telemetry():
    """BIP 4.2.1: exact provider token usage must be wired into telemetry
    (and kept distinct from estimated context tokens)."""
    class UsageProvider(ScriptedProvider):
        def __init__(self, script):
            super().__init__(script)
            self.last_usage = {
                "prompt_tokens": 1200,
                "completion_tokens": 240,
                "total_tokens": 1440,
            }

    prov = UsageProvider([("Created.", None)])
    agent = CADAgent(adapter=StubAdapter(), provider=prov)
    reply, _ = agent.handle_message("Create a box")
    tel = agent.get_context_telemetry()
    assert tel, "expected at least one telemetry entry"
    entry = tel[0]
    # Exact provider-reported tokens are captured separately.
    assert entry["exact_provider_tokens"] == {
        "prompt_tokens": 1200,
        "completion_tokens": 240,
        "total_tokens": 1440,
    }
    assert entry["input_tokens"] == 1200
    assert entry["output_tokens"] == 240
    assert entry["total_tokens"] == 1440
    # The estimated context tokens remain distinct from exact values.
    assert entry["estimated_context_tokens"] > 0
    assert entry["estimated_context_tokens"] != entry["total_tokens"]


def test_agent_handles_provider_without_usage():
    """A provider that does not report usage must not break handle_message. """

    class NoUsageProvider(ScriptedProvider):
        def __init__(self, script):
            super().__init__(script)
            # last_usage defaults to None when not exposed.

    prov = NoUsageProvider([("Done.", None)])
    agent = CADAgent(adapter=StubAdapter(), provider=prov)
    reply, _ = agent.handle_message("Create a box")
    tel = agent.get_context_telemetry()
    assert tel
    # No exact provider tokens present; no fabricated values.
    assert tel[0]["exact_provider_tokens"] is None
    assert tel[0]["input_tokens"] is None
