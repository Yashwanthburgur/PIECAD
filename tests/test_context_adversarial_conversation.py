"""BIP 4.2.2 — Adversarial: B. Conversation Relevance.

Verify the compiler retains relevant history without blindly retaining everything,
and that the current user intent is always retained.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json  # noqa: E402

from core.context.conversation import ConversationContext  # noqa: E402
from core.context.compiler import ContextCompiler  # noqa: E402
from core.context.state import DesignState  # noqa: E402
from core.context.memory import SessionMemory  # noqa: E402

_BOX = {"id": "box1", "label": "Box", "type": "Part::Box", "visible": True,
        "parents": [], "children": [], "properties": {"Length": 100, "Width": 50}}
_CYL = {"id": "cylinder1", "label": "Cylinder", "type": "Part::Cylinder", "visible": True,
        "parents": [], "children": [], "properties": {"Radius": 10, "Height": 40}}


def _state(objs):
    st = DesignState()
    st.update_from_cad_state(json.dumps(objs))
    return st


def _tools():
    names = ["box", "cylinder", "hole", "fillet", "get_state", "get_mass_properties",
             "edit_feature", "export"]
    return [{"type": "function",
             "function": {"name": n, "description": f"run {n}",
                          "parameters": {"type": "object", "properties": {}}}}
            for n in names]


def test_current_user_intent_always_retained():
    ctx = ConversationContext(default_recent_window=3)
    ctx.add_user("Create a box")
    ctx.add_user("Create a cylinder")
    # Ask about the box again -> current turn retained.
    ctx.add_user("What is the volume of the box?")
    msgs = ctx.to_messages("What is the volume of the box?")
    last = msgs[-1] if msgs else {}
    assert last.get("content") == "What is the volume of the box?"


def test_compiler_retains_relevant_recent_history():
    comp = ContextCompiler()
    ctx = ConversationContext(default_recent_window=4)
    ctx.add_user("Create a box")
    ctx.add_assistant("done")
    ctx.add_user("Create a cylinder")
    ctx.add_assistant("done")
    compiled = comp.compile(
        user_message="Check the box dimensions",
        conversation_context=ctx,
        design_state=_state([_BOX, _CYL]),
        session_memory=SessionMemory(),
        available_tools=_tools(),
    )
    # Current request is always retained (as user_context).
    assert compiled.user_context == "Check the box dimensions"
    # The box creation turn (relevant concept) is retained in the recent window.
    assert any("Create a box" in m.get("content", "")
               for m in compiled.conversation)


def test_older_relevant_turn_included_when_hole_requested():
    comp = ContextCompiler()
    ctx = ConversationContext(default_recent_window=2)
    # An OLD turn (beyond window) about a hole.
    ctx.add_user("Create a box")
    ctx.add_assistant("box1 created")
    ctx.add_user("Drill an M6 hole")
    ctx.add_assistant("hole1 created")
    ctx.add_user("Something unrelated")
    ctx.add_assistant("ok")
    compiled = comp.compile(
        user_message="Increase the hole diameter",
        conversation_context=ctx,
        design_state=_state([_BOX, _CYL]),
        session_memory=SessionMemory(),
        available_tools=_tools(),
    )
    texts = " ".join(m.get("content", "") for m in compiled.conversation)
    assert "hole" in texts.lower()


def test_irrelevant_history_excluded():
    comp = ContextCompiler()
    ctx = ConversationContext(default_recent_window=2)
    ctx.add_user("Add a fillet")
    ctx.add_assistant("done")
    ctx.add_user("Discuss the weather")
    ctx.add_assistant("sunny")
    ctx2 = ConversationContext(default_recent_window=2)
    ctx2.add_user("review last week's expenses")
    ctx2._turns += ctx._turns
    compiled = comp.compile(
        user_message="Add a fillet",
        conversation_context=ctx2,
        design_state=_state([_BOX]),
        session_memory=SessionMemory(),
        available_tools=_tools(),
    )
    texts = " ".join(m.get("content", "") for m in compiled.conversation)
    # The current "fillet" request is retained.
    assert compiled.user_context == "Add a fillet"
    # The far-back expenses turn is NOT included (out of window, no shared concept
    # beyond "fillet" which is in the recent window text but not an expense).
    assert "expenses" not in texts.lower()


def test_repeated_concepts_do_not_blow_up_context():
    comp = ContextCompiler()
    ctx = ConversationContext(default_recent_window=5)
    for i in range(30):
        ctx.add_user(f"Add hole number {i}")
        ctx.add_assistant("done")
    compiled = comp.compile(
        user_message="Add a hole here",
        conversation_context=ctx,
        design_state=_state([_BOX]),
        session_memory=SessionMemory(),
        available_tools=_tools(),
    )
    # Even with 30 repeats, compiled conversation is bounded and the current
    # request is retained.
    assert compiled.user_context == "Add a hole here"
    assert len(compiled.conversation) <= 20
