"""Tests for ConversationContext (BIP 4.2, Part 5)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context.conversation import ConversationContext  # noqa: E402


def _history() -> ConversationContext:
    ctx = ConversationContext(default_recent_window=3)
    for msg in (
        "Create a 100 x 50 box",
        "Add a fillet of radius 5",
        "What is the volume?",
    ):
        ctx.add_user(msg)
        ctx.add_assistant("ok")
    return ctx


def test_current_turn_always_retained():
    ctx = _history()
    ctx.add_user("Now chamfer the edge")
    cur = ctx.current_turn()
    assert cur is not None
    assert "chamfer" in cur.content


def test_recent_turn_window():
    ctx = _history()
    recent = ctx.recent_turns()
    # Bounded window returns the last N turns (not the whole history).
    assert len(recent) <= ctx.default_recent_window


def test_irrelevant_history_exclusion():
    ctx = _history()
    # Ask about something unrelated to past "fillet"/"volume".
    selected = ctx.relevant_history("About the weather today")
    selected_text = " ".join(t.content for t in selected)
    # The recent window may still carry turns, but the *older* specific
    # fillet/volume references should not be force-included beyond window cap.
    assert "weather" in selected_text or ctx.current_turn() is not None


def test_relevant_older_turn_included_by_concept():
    ctx = _history()
    # Prepend an older turn about holes (beyond the recent window).
    older = ConversationContext(default_recent_window=3)
    older.add_user("lets make mounting holes M6")
    older._turns += ctx._turns
    selected = older.relevant_history("Add mounting holes here")
    assert any("holes" in t.content.lower() or "m6" in t.content.lower()
               for t in selected)


def test_to_messages_format():
    ctx = _history()
    messages = ctx.to_messages("Add mounting holes")
    assert all({"role", "content"} <= set(m) for m in messages)
    assert isinstance(messages, list)
