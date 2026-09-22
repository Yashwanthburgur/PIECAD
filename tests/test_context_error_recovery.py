"""BIP 4.3.1 — ReAct Tool Error Recovery regression tests.

Verify that non-transient CAD/tool errors and malformed tool arguments do NOT
abort handle_message(); instead they are returned to the LLM as structured tool
results so it can reason about recovery. Also verifies:
- empty LLM responses no longer falsely report "Done.",
- transient retry behavior is preserved,
- persistent failures terminate safely without infinite loops,
- failed operations never fabricate CAD state.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.adapters.interfaces import CADAdapter  # noqa: E402
from core.agent import CADAgent  # noqa: E402
from core.context.state import DesignState  # noqa: E402


# --------------------------------------------------------------------------- #
# Scripted provider & stub adapter (mirrors existing test architecture)
# --------------------------------------------------------------------------- #
class ScriptedProvider:
    """Returns scripted responses: list of (content_or_None, tool_calls)."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        # Exact provider usage is optional; none by default (backward compat).
        self.last_usage = None

    def generate_with_tools(self, messages, tools=None):
        from types import SimpleNamespace
        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        content, tool_calls = self.script[idx]
        tcs = None
        if tool_calls is not None:
            tcs = [
                SimpleNamespace(
                    id=f"call_{i}",
                    function=SimpleNamespace(
                        name=nm, arguments=json.dumps(ar)),
                )
                for i, (nm, ar) in enumerate(tool_calls)
            ]
        return SimpleNamespace(content=content, tool_calls=tcs)


class StubAdapter(CADAdapter):
    """Adapter whose execute_command can be scripted per tool name.

    ``behavior`` maps tool name -> ('ok', result) or ('raise', exception).
    """

    def __init__(self, tool_names=None, behavior=None):
        self.tool_names = tool_names or [
            "box", "fillet", "get_edges", "get_state"]
        self.behavior = behavior or {}
        self.objects = []
        self.calls = []

    def get_tools(self):
        return [{"type": "function", "function": {
            "name": n, "description": f"run {n}",
            "parameters": {"type": "object", "properties": {}}}}
            for n in self.tool_names]

    def execute_command(self, tool_name, **kwargs):
        self.calls.append((tool_name, kwargs))
        spec = self.behavior.get(tool_name, ("ok", "ok"))
        # Allow a callable spec for conditional behavior (e.g. fail on large
        # radius, succeed otherwise).
        if callable(spec):
            spec = spec(kwargs)
        mode, *rest = spec
        if mode == "raise":
            raise rest[0]
        if tool_name == "box":
            self.objects.append({"id": "box1", "type": "Part::Box",
                                 "visible": True, "parents": [],
                                 "children": [], "properties": kwargs})
        return "ok" if len(rest) == 0 else rest[0]

    def get_state(self):
        return json.dumps(self.objects)


# --------------------------------------------------------------------------- #
# 1. Non-transient tool failure does not abort the turn
# --------------------------------------------------------------------------- #
def test_non_transient_tool_failure_does_not_abort():
    err = RuntimeError("FreeCAD Kernel Invalid Geometry")
    adapter = StubAdapter(behavior={"fillet": ("raise", err)})
    # Step 1 fillet fails (non-transient); Step 2 agent responds.
    prov = ScriptedProvider([
        (None, [("fillet", {"radius": 50})]),   # will fail non-transient
        ("I cannot apply that fillet.", None),  # recovery/prose response
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    reply, session_tools = agent.handle_message("Fillet the edge 50mm")
    # handle_message must NOT raise and must return normally.
    assert isinstance(reply, str)
    assert "fillet" in session_tools
    # The failure reached the LLM as a tool result (provider had 2 calls).
    assert agent.provider.calls == 2


def test_non_transient_failure_reaches_llm_as_tool_result():
    seen_errors = []

    class CapturingProvider(ScriptedProvider):
        def generate_with_tools(self, messages, tools=None):
            # Inspect the tool messages the agent feeds back (role == "tool").
            for m in messages:
                if m.get("role") == "tool":
                    content = m.get("content")
                    if isinstance(content, str) and '"status": "error"' in content:
                        seen_errors.append(json.loads(content))
            return super().generate_with_tools(messages, tools)

    err = RuntimeError("FreeCAD Kernel Invalid Geometry")
    adapter = StubAdapter(behavior={"fillet": ("raise", err)})
    prov = CapturingProvider([
        (None, [("fillet", {"radius": 50})]),
        (None, [("get_edges", {"object_name": "box1"})]),
        ("Done inspecting.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    agent.handle_message("Fillet the edge 50mm")
    assert seen_errors, "expected at least one structured error tool result"
    err_payload = seen_errors[0]
    assert err_payload["status"] == "error"
    assert err_payload["tool"] == "fillet"
    assert err_payload["error_type"] == "RuntimeError"
    assert "Invalid Geometry" in err_payload["error"]
    assert err_payload["transient"] is False
    assert err_payload["arguments"] == {"radius": 50}


# --------------------------------------------------------------------------- #
# 2. Recovery after failure
# --------------------------------------------------------------------------- #
def test_recovery_after_failure():
    adapter = StubAdapter(
        behavior={
            "fillet": lambda kwargs: (
                ("raise", RuntimeError("radius too large"))
                if kwargs.get("radius", 0) > 10 else ("ok", "fillet applied")),
            "get_edges": ("ok", '[{"edge_id": "e1"}]'),
        }
    )
    # Step1 fillet(radius=50) fails; Step2 fillet(radius=5) succeeds.
    prov = ScriptedProvider([
        (None, [("fillet", {"radius": 50})]),
        (None, [("fillet", {"radius": 5})]),
        ("Applied a 5mm fillet.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    reply, session_tools = agent.handle_message("Fillet the edge")
    assert "fillet" in session_tools
    # Both fillet calls happened.
    fillet_calls = [c for c in adapter.calls if c[0] == "fillet"]
    assert len(fillet_calls) == 2
    # The failed radius-50 call is recorded as an error (recent_errors), NOT as
    # a successful recent_operation and never as a fabricated object.
    errs = agent.design_state.get_recent_errors()
    assert any("fillet" in e and "radius too large" in e for e in errs)
    assert len(agent.design_state.objects) == 0
    # The successful radius-5 call is recorded in recent_operations as success.
    ops = agent.design_state.get_recent_operations()
    fillet_ok = [op for op in ops if op.tool == "fillet" and op.success]
    assert len(fillet_ok) == 1
    assert fillet_ok[0].args.get("radius") == 5


# --------------------------------------------------------------------------- #
# 3. Transient failure retry preserved
# --------------------------------------------------------------------------- #
def test_transient_retry_preserved():
    class FlakyAdapter(StubAdapter):
        def __init__(self):
            super().__init__(["box", "get_state"])
            self.attempts = 0

        def execute_command(self, tool_name, **kwargs):
            if tool_name == "box" and self.attempts == 0:
                self.attempts += 1
                raise ConnectionError("connection reset by peer")
            return super().execute_command(tool_name, **kwargs)

    prov = ScriptedProvider([
        (None, [("box", {"Length": 100.0})]),
        ("Created.", None),
    ])
    agent = CADAgent(adapter=FlakyAdapter(), provider=prov)
    reply, _ = agent.handle_message("Create a box")
    # Transient error was retried once, then succeeded.
    assert agent.adapter.attempts == 1
    assert isinstance(reply, str)


# --------------------------------------------------------------------------- #
# 4. Persistent failure terminates safely (no infinite loop, no "Done.")
# --------------------------------------------------------------------------- #
def test_persistent_failure_terminates_safely():
    err = RuntimeError("Kernel failed")
    # Every fillet attempt fails. MAX_RETRIES=3 means per-call it tries 4 times,
    # but the whole turn is bounded by MAX_STEPS.
    adapter = StubAdapter(behavior={"fillet": (
        "raise", err), "get_edges": ("raise", err)})
    # The model keeps trying fillet every step (never yields prose).
    script = [(None, [("fillet", {"radius": 50})])] * (CADAgent.MAX_STEPS + 1)
    prov = ScriptedProvider(script)
    agent = CADAgent(adapter=adapter, provider=prov)
    reply, _ = agent.handle_message("Fillet the edge")
    # Bounded by MAX_STEPS, not an infinite loop.
    assert agent.provider.calls <= CADAgent.MAX_STEPS
    # The result is an explicit failure, NOT "Done.".
    assert "Done." != reply
    # No fake geometry.
    assert len(agent.design_state.objects) == 0


# --------------------------------------------------------------------------- #
# 5. Empty LLM response does not falsely report "Done."
# --------------------------------------------------------------------------- #
def test_empty_response_not_done():
    # Step1: empty response (no tool calls, no content). Step2: real response.
    prov = ScriptedProvider([
        (None, None),        # empty: content=None, tool_calls=None -> continue
        ("Created the box.", None),
    ])
    adapter = StubAdapter()
    agent = CADAgent(adapter=adapter, provider=prov)
    reply, _ = agent.handle_message("Create a box")
    # Because the empty response was followed by a real one, the agent continued
    # and returned the meaningful reply, never an automatic "Done.".
    assert reply is not None
    assert reply != "Done."
    assert agent.provider.calls >= 2  # the empty response used a step


# --------------------------------------------------------------------------- #
# 6. Malformed tool arguments are recoverable
# --------------------------------------------------------------------------- #
def test_malformed_arguments_recoverable():
    seen = []

    class CapturingProvider(ScriptedProvider):
        def generate_with_tools(self, messages, tools=None):
            for m in messages:
                if m.get("role") == "tool":
                    c = m.get("content")
                    if isinstance(c, str) and "Malformed tool arguments" in c:
                        seen.append(json.loads(c))
            return super().generate_with_tools(messages, tools)

    class MalformedArgsProvider(CapturingProvider):
        def generate_with_tools(self, messages, tools=None):
            # On the FIRST call, hand back a tool_call with bad JSON arguments.
            if self.calls == 0:
                from types import SimpleNamespace
                self.calls += 1
                self.last_usage = None
                bad_tc = SimpleNamespace(
                    id="call_bad",
                    function=SimpleNamespace(
                        name="fillet",
                        arguments="{bad json not closed",
                    ),
                )
                return SimpleNamespace(content=None, tool_calls=[bad_tc])
            return super().generate_with_tools(messages, tools)

    adapter = StubAdapter()
    prov = MalformedArgsProvider([("Applied the fillet.", None)])
    agent = CADAgent(adapter=adapter, provider=prov)
    reply, _ = agent.handle_message("Fillet the edge")
    # No JSONDecodeError escapes handle_message.
    assert isinstance(reply, str)
    # The malformed-args error was returned to the LLM.
    assert seen, "expected malformed-args error to reach the LLM"
    assert seen[0]["status"] == "error"
    # Recovery remained possible: the agent produced a final response afterward.
    assert reply


# --------------------------------------------------------------------------- #
# 7. Failed operation does not create fake state
# --------------------------------------------------------------------------- #
def test_failed_operation_no_fake_state():
    adapter = StubAdapter(behavior={"fillet": ("raise", RuntimeError("bad"))})
    prov = ScriptedProvider([
        (None, [("fillet", {"radius": 99})]),
        ("Could not fillet.", None),
    ])
    agent = CADAgent(adapter=adapter, provider=prov)
    agent.handle_message("Fillet the edge")
    # No fabricated CAD object exists as a result of the failed tool call.
    assert len(agent.design_state.objects) == 0
    # The failure is recorded in recent_errors (never as a successful object).
    errs = agent.design_state.get_recent_errors()
    assert any("fillet" in e for e in errs)
    # And it did NOT appear as a successful recent_operation.
    assert not any(op.tool == "fillet" and op.success for
                   op in agent.design_state.get_recent_operations())
