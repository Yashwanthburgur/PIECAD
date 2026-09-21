"""BIP 4.2.2 — Adversarial: G. Compiler Determinism.

For identical request / conversation / state / memory / tools / plan, repeated
compilation must yield identical selected tools, sections, ordering, and the
non-transient part of telemetry (no hidden randomness).

Transient fields that genuinely vary between runs (compilation_time_ms,
llm_latency_ms, timestamps) are excluded from the strict equality comparison.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from core.context.compiler import ContextCompiler  # noqa: E402
from core.context.state import DesignState  # noqa: E402
from core.context.conversation import ConversationContext  # noqa: E402
from core.context.memory import SessionMemory  # noqa: E402
from core.context.plan import ContextPlan  # noqa: E402

_TRANSIENT_KEYS = {"compilation_time_ms", "llm_latency_ms"}


def _strip_jitter(obj):
    """Recursively drop known transient timing fields for stable comparison."""
    if isinstance(obj, dict):
        return {k: _strip_jitter(v) for k, v in obj.items()
                if k not in _TRANSIENT_KEYS}
    if isinstance(obj, list):
        return [_strip_jitter(v) for v in obj]
    return obj


def _build():
    st = DesignState()
    st.update_from_cad_state(json.dumps([
        {"id": "box1", "label": "Box", "type": "Part::Box", "visible": True,
         "parents": [], "children": [], "properties": {"Length": 100.0}},
        {"id": "hole1", "label": "Hole", "type": "Part::Cut", "visible": False,
         "parents": [], "children": ["box1"], "properties": {}},
    ]))
    mem = SessionMemory()
    mem.set("units", "mm", kind="user_preference")
    mem.set("preferred_hole_size", "M6", kind="convention")
    ctx = ConversationContext(default_recent_window=3)
    ctx.add_user("Create a box")
    ctx.add_assistant("done")
    tools = [{"type": "function",
              "function": {"name": n, "parameters": {"type": "object",
                                                     "properties": {}}}}
             for n in ("box", "hole", "get_state", "get_faces", "edit_feature",
                       "export", "fillet")]
    return st, mem, ctx, tools


def test_repeated_compile_is_identical():
    st, mem, ctx, tools = _build()
    comp = ContextCompiler()
    results = []
    for _ in range(5):
        c = comp.compile(
            user_message="Increase the hole diameter",
            conversation_context=ctx, design_state=st, session_memory=mem,
            available_tools=tools, react_step=1,
        )
        results.append(_strip_jitter(
            (c.to_dict(), c.telemetry.to_dict())))
    for r in results[1:]:
        assert r == results[0]


def test_with_explicit_plan_is_identical():
    st, mem, ctx, tools = _build()
    plan = ContextPlan(required_tools=["hole", "edit_feature"],
                       relevant_object_ids=["hole1", "box1"],
                       required_state_sections=["objects"],
                       required_memory_sections=["conventions"])
    comp = ContextCompiler()
    outs = [_strip_jitter(comp.compile(
        user_message="Edit the hole",
        conversation_context=ctx, design_state=st, session_memory=mem,
        available_tools=tools, optional_context_plan=plan,
    ).to_dict()) for _ in range(3)]
    assert outs[0] == outs[1] == outs[2]


def test_tool_ordering_stable():
    st, mem, ctx, tools = _build()
    comp = ContextCompiler()
    names = []
    for _ in range(3):
        c = comp.compile(
            user_message="Increase the hole diameter",
            conversation_context=ctx, design_state=st, session_memory=mem,
            available_tools=tools, react_step=2,
        )
        names.append([t["function"]["name"] for t in c.tools])
    assert names[0] == names[1] == names[2]


def test_telemetry_estimation_is_stable():
    # Token estimates and breakdown are deterministic (timing excluded).
    st, mem, ctx, tools = _build()
    comp = ContextCompiler()
    est = []
    for _ in range(3):
        c = comp.compile(
            user_message="Increase the hole diameter",
            conversation_context=ctx, design_state=st, session_memory=mem,
            available_tools=tools, react_step=1,
        )
        t = c.telemetry
        est.append((t.estimated_context_tokens, t.estimated_state_tokens,
                    t.estimated_memory_tokens, t.estimated_conversation_tokens,
                    t.estimated_tool_schema_tokens, tuple(t.tools_exposed_names),
                    tuple(t.dropped_sections)))
    assert est[0] == est[1] == est[2]
