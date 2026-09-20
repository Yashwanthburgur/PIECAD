"""SessionMemory — lightweight, per-session, in-memory knowledge store.

SESSION MEMORY answers the question:       "What have we learned, decided or
preferred during this session?"
(distinct from DESIGN STATE, which answers "What exists?").

The two are deliberately kept separate and never mixed.

Scoped strictly to one agent session:
- No persistence across processes.
- No database.
- No embeddings / vector search.
- No automatic promotion of LLM guesses into durable facts.

Each memory entry carries an explicit ``kind`` so downstream logic can clearly
distinguish facts, preferences, decisions, conventions, corrections, assumptions
and inferences. The API offers ``set/get/remove/clear/snapshot/relevant`` and
supports selective retrieval rather than dumping the whole store.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

# Valid memory entry kinds.
KIND_FACT = "fact"
KIND_PREFERENCE = "user_preference"
KIND_DECISION = "decision"
KIND_CONVENTION = "convention"
KIND_CORRECTION = "correction"
KIND_ASSUMPTION = "assumption"
KIND_INFERENCE = "inference"

VALID_KINDS = frozenset(
    {KIND_FACT, KIND_PREFERENCE, KIND_DECISION, KIND_CONVENTION,
     KIND_CORRECTION, KIND_ASSUMPTION, KIND_INFERENCE}
)

# Section alias: provider-facing plan section names -> memory kind.
# e.g. plan.required_memory_sections=["conventions"] => kind "convention".
_SECTION_TO_KIND: Dict[str, str] = {
    "facts": KIND_FACT,
    "preferences": KIND_PREFERENCE,
    "user_preferences": KIND_PREFERENCE,
    "conventions": KIND_CONVENTION,
    "decisions": KIND_DECISION,
    "corrections": KIND_CORRECTION,
    "assumptions": KIND_ASSUMPTION,
    "inferences": KIND_INFERENCE,
}


def resolve_section_kinds(section: str) -> List[str]:
    """Map a plan section name (possibly plural) to one or more memory kinds."""
    direct = _SECTION_TO_KIND.get(section)
    if direct is not None:
        return [direct]
    # Fall back to treating the name itself as a kind.
    return [section] if section in VALID_KINDS else []


# Keywords used by the deterministic relevance layer to map a user/free-text
# request into the memory sections (kinds) that are most likely relevant.
_KIND_SIGNALS: Dict[str, tuple] = {
    KIND_PREFERENCE: (
        "prefer", "preference", "like", "favorite", "i want it to", "always",
        "default size", "default to", "use mm", "use inches", "preferred",
    ),
    KIND_CONVENTION: (
        "convention", "standard", "m3", "m4", "m5", "m6", "m8", "m10",
        "iso", "diameter", "mounting hole", "hole",
    ),
    KIND_DECISION: (
        "decide", "decision", "orientation", "choose", "choice", "we decided",
        "vertical", "horizontal", "offset", "reverse",
    ),
    KIND_CORRECTION: (
        "correct", "correction", "fix", "revise", "actually", "instead",
    ),
    KIND_ASSUMPTION: ("assume", "assumption", "assuming", "guess"),
}

# Concept-based synonyms used to detect which *objects* a request refers to.
_OBJECT_SIGNALS: Dict[str, tuple] = {
    "hole": ("hole", "drill", "bore", "countersink"),
    "fillet": ("fillet", "round", "radius"),
    "chamfer": ("chamfer", "bevel"),
    "box": ("box", "block", "plate", "enclosure"),
    "cylinder": ("cylinder", "cyl", "pin", "shaft", "round stock"),
    "sketch": ("sketch", "profile", "draft"),
    "pad": ("pad", "extrude"),
    "pattern": ("pattern", "array", "linear", "circular"),
    "boolean": ("boolean", "union", "subtract", "difference", "cut", "intersect"),
}

# Concept -> memory kinds that tend to matter for that concept.
_CONCEPT_TO_KINDS: Dict[str, tuple] = {
    "hole": (KIND_CONVENTION, KIND_PREFERENCE, KIND_CORRECTION),
    "fillet": (KIND_PREFERENCE,),
    "chamfer": (KIND_PREFERENCE,),
    "material": (KIND_PREFERENCE,),
    "pattern": (KIND_CONVENTION, KIND_PREFERENCE),
    "export": (KIND_PREFERENCE,),
}


@dataclass
class MemoryEntry:
    """A single piece of session knowledge."""

    key: str
    value: Any
    kind: str = KIND_FACT
    # e.g. 'cadagent.update_memory' or a caller-provided label.
    source: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "kind": self.kind,
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"MemoryEntry(key={self.key!r}, kind={self.kind!r})"


@dataclass
class SessionMemory:
    """Per-session knowledge store with kind-tagged, selectively-queryable entries."""

    _entries: Dict[str, MemoryEntry] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # write / read
    # ------------------------------------------------------------------ #
    def set(
        self,
        key: str,
        value: Any,
        *,
        kind: str = KIND_FACT,
        source: str = "",
    ) -> "SessionMemory":
        """Set or overwrite a memory entry."""
        if kind not in VALID_KINDS:
            kind = KIND_FACT
        self._entries[key] = MemoryEntry(
            key=key, value=value, kind=kind, source=source
        )
        return self

    def get(self, key: str, default: Any = None) -> Any:
        entry = self._entries.get(key)
        return entry.value if entry is not None else default

    def get_entry(self, key: str) -> Optional[MemoryEntry]:
        return self._entries.get(key)

    def remove(self, key: str) -> bool:
        return self._entries.pop(key, None) is not None

    def clear(self) -> "SessionMemory":
        self._entries.clear()
        return self

    def entries(self) -> List[MemoryEntry]:
        return list(self._entries.values())

    def keys(self) -> List[str]:
        return list(self._entries.keys())

    def snapshot(self) -> Dict[str, Any]:
        """Full serializable snapshot (dictionary form, keyed by entry key)."""
        return {
            key: entry.to_dict() for key, entry in self._entries.items()
        }

    # ------------------------------------------------------------------ #
    # selective retrieval
    # ------------------------------------------------------------------ #
    def by_kind(self, kind: str) -> List[MemoryEntry]:
        return [e for e in self._entries.values() if e.kind == kind]

    def relevant(self, text: str, *, max_items: Optional[int] = None) -> Dict[str, Any]:
        """Deterministically select memory sections relevant to ``text``.

        This is the memory side of the "no blind memory dump" requirement: it
        returns a dict whose keys are memory kinds, values are lists of *values*
        for entries whose kind (or key/value text) matches the request.

        Returns a plain dict so callers (and a future classifier) can cheaply
        work with the selected subset without touching the whole store.
        """
        text_l = (text or "").lower()

        # 1. Determine directly-requested kinds from free-text signals.
        wanted_kinds: set = set()
        for kind, signals in _KIND_SIGNALS.items():
            if any(sig in text_l for sig in signals):
                wanted_kinds.add(kind)

        # 2. Concept-signal synonyms also imply related memory kinds (e.g. a
        #    "hole" request maps to conventions + preferences).
        for concept, kinds in _CONCEPT_TO_KINDS.items():
            if any(kw in text_l for kw in _OBJECT_SIGNALS.get(concept, ())):
                wanted_kinds.update(kinds)

        selected: Dict[str, Any] = {}
        for kind in list(wanted_kinds):
            matches = []
            for entry in self._entries.values():
                if entry.kind != kind:
                    continue
                haystack = f"{entry.key} {entry.value}".lower()
                # Include when the entry itself mentions a concept in the request,
                # or when a free-text signal applies, or key/value contain text.
                if any(sig in haystack for sig in text_l.split()) or \
                        any(sig in haystack for sig in _OBJECT_SIGNALS.get(kind, ())) or \
                        any(sig in text_l for sig in _KIND_SIGNALS.get(kind, ())):
                    matches.append(entry.value)
            if matches:
                selected[kind] = list(dict.fromkeys(matches))

        if max_items is not None and max_items > 0:
            # Trim each section to the budget while preserving order.
            selected = {k: v[:max_items] for k, v in selected.items()}
        return selected

    def __len__(self) -> int:
        return len(self._entries)
