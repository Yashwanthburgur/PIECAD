"""Tests for SessionMemory (BIP 4.2, Part 4).

Proves: write/read/update/snapshot/relevance, kind tagging, and exclusions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context.memory import (  # noqa: E402
    SessionMemory,
    KIND_FACT,
    KIND_PREFERENCE,
    KIND_CONVENTION,
    KIND_DECISION,
    KIND_CORRECTION,
    KIND_ASSUMPTION,
)


def _sample_memory() -> SessionMemory:
    mem = SessionMemory()
    mem.set("units", "mm", kind=KIND_PREFERENCE)
    mem.set("preferred_hole_size", "M6", kind=KIND_CONVENTION)
    mem.set("preferred_material", "Al6061", kind=KIND_PREFERENCE)
    mem.set("bracket_orientation", "vertical", kind=KIND_DECISION)
    mem.set("default_hole", "M8", kind=KIND_CORRECTION)
    mem.set("thread_assumption", "coarse", kind=KIND_ASSUMPTION)
    return mem


def test_memory_write():
    mem = SessionMemory()
    mem.set("units", "mm", kind=KIND_PREFERENCE)
    assert mem.get("units") == "mm"


def test_memory_read():
    mem = _sample_memory()
    assert mem.get("bracket_orientation") == "vertical"
    assert mem.get("missing", "x") == "x"


def test_memory_update():
    mem = SessionMemory()
    mem.set("units", "mm", kind=KIND_PREFERENCE)
    mem.set("units", "inches", kind=KIND_PREFERENCE)
    assert mem.get("units") == "inches"
    assert len(mem) == 1


def test_memory_snapshot():
    mem = _sample_memory()
    snap = mem.snapshot()
    assert snap["units"]["value"] == "mm"
    assert snap["units"]["kind"] == KIND_PREFERENCE
    # Deterministic serializable form.
    import json
    json.dumps(snap)


def test_memory_relevance_selects_conventions_for_holes():
    mem = _sample_memory()
    # A hole request should surface conventions + preferences, not everything.
    sel = mem.relevant("Add mounting holes for M6")
    assert KIND_CONVENTION in sel
    assert "M6" in sel[KIND_CONVENTION]
    # Decisions about bracket orientation should NOT be dumped for a hole request.
    decision_vals = [v for k, v in sel.items()]
    flat = [item for sub in decision_vals for item in (sub or [])]
    assert "vertical" not in flat


def test_memory_relevance_excludes_unrelated_kinds():
    mem = _sample_memory()
    sel = mem.relevant("just reverse that choice")
    assert KIND_DECISION in sel or KIND_CORRECTION in sel
    # It should not surface material preference for a pure orientation decision.
    flat = [item for sub in sel.values() for item in (sub or [])]
    assert "Al6061" not in flat


def test_memory_clear_and_remove():
    mem = _sample_memory()
    assert mem.remove("units") is True
    assert mem.remove("nope") is False
    mem.clear()
    assert len(mem) == 0
