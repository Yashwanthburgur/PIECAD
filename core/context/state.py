"""DesignState — authoritative, lightweight, serializable CAD state abstraction.

This is the single source of truth for *what exists* in the CAD document from the
Context Engine's perspective. It deliberately mirrors the real FreeCAD state
returned by ``CADAdapter.get_state()`` (a JSON list of objects keyed by ``id``
with ``type``/``visible``/``parents``/``children``/``properties``) so that no
duplicate CAD metadata is invented.

DESIGN STATE answers the question:   "What exists?"
(distinct from SESSION MEMORY:      "What have we learned, decided or preferred?")

The state is intentionally lightweight and fully serializable. Updates are
incremental: ``update_from_tool_result`` and ``update_from_cad_state`` merge object
records / history rather than re-serializing the whole document on every step.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


# Keys that commonly carry a "known to be relevant fact" about an object.
# Used to decide whether a property survives into a *selective* state view.
_DIMENSIONAL_KEYS = ("Length", "Width", "Height", "Radius", "Diameter")
_FEATURE_TYPE_KEYWORDS = (
    "hole", "fillet", "chamfer", "box", "cylinder", "sketch", "pad",
    "pocket", "cut", "fuse", "boolean", "shell", "pattern",
)


def _normalize_id(obj_id: Any) -> str:
    return str(obj_id)


def _current_ts() -> float:
    return time.time()


@dataclass
class DesignObject:
    """A single CAD object record kept in DesignState.

    ``relationships`` holds parent/child links as reported by the real CAD state
    (``parents``/``children`` arrays). No fake relationships are ever synthesised.
    ``properties`` mirrors the adapter's ``properties`` dict (dimensions etc.).
    """

    object_id: str
    object_type: str = ""
    label: str = ""
    visible: bool = True
    properties: Dict[str, Any] = field(default_factory=dict)
    parents: List[str] = field(default_factory=list)
    children: List[str] = field(default_factory=list)
    updated_at: float = field(default_factory=_current_ts)

    # -- conveniences ----------------------------------------------------- #
    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "DesignObject":
        """Build a DesignObject from an adapter state record dict."""
        return cls(
            object_id=_normalize_id(
                raw.get("id") or raw.get("label") or "unknown"),
            object_type=str(raw.get("type") or raw.get("shape_type") or ""),
            label=str(raw.get("label") or raw.get("name") or ""),
            visible=bool(raw.get("visible", True)),
            properties=dict(raw.get("properties") or {}),
            parents=[str(p) for p in (raw.get("parents") or [])],
            children=[str(c) for c in (raw.get("children") or [])],
        )

    def is_solid(self) -> bool:
        """Best-effort check that this object represents physical solid geometry."""
        if self.object_type.lower() in ("solid", "part", "body"):
            return True
        if any(k in self.properties for k in _DIMENSIONAL_KEYS):
            return True
        return any(kw in self.object_type.lower() for kw in _FEATURE_TYPE_KEYWORDS)

    def relationships(self) -> Dict[str, Any]:
        """Return the relationship links for this object (may be empty)."""
        rel: Dict[str, Any] = {}
        if self.parents:
            rel["parents"] = list(self.parents)
        if self.children:
            rel["children"] = list(self.children)
        return rel

    def to_dict(self, *, minimal: bool = False) -> Dict[str, Any]:
        """Serialize this object. ``minimal`` keeps only the most useful keys."""
        base: Dict[str, Any] = {
            "id": self.object_id,
            "type": self.object_type,
            "visible": self.visible,
        }
        if not minimal:
            base["label"] = self.label
            base["properties"] = dict(self.properties)
        rel = self.relationships()
        if rel:
            base["relationships"] = rel
        return base

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"DesignObject(id={self.object_id!r}, type={self.object_type!r})"


@dataclass
class RecentOperation:
    """A recorded CAD operation (tool name + outcome summary)."""

    tool: str
    target_id: Optional[str] = None
    args: Dict[str, Any] = field(default_factory=dict)
    success: bool = True
    ts: float = field(default_factory=_current_ts)

    # BIP 4.3.2: Requested-vs-achieved integrity.
    # When a recovery operation changes parameters (e.g., requested radius 500,
    # achieved radius 5), we preserve the originally requested args so the
    # LLM and final response can see the mismatch. The `args` field holds the
    # final achieved parameters; `requested_args` holds the original request.
    requested_args: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "tool": self.tool,
            "target_id": self.target_id,
            "success": self.success,
            "args": dict(self.args),
        }
        if self.requested_args is not None:
            d["requested_args"] = dict(self.requested_args)
        return d


@dataclass
class DesignState:
    """Structured, queryable view of the current CAD document."""

    active_document: Optional[str] = None

    # indexed objects: object_id -> DesignObject
    objects: Dict[str, DesignObject] = field(default_factory=dict)

    # feature_tree is derived from object relationships (parents/children).
    selected_entities: List[str] = field(default_factory=list)

    recent_operations: List[RecentOperation] = field(default_factory=list)
    recent_errors: List[str] = field(default_factory=list)

    current_task: Optional[str] = None

    # current_intent is OPTIONAL for now: a future intent/classification layer
    # may populate it with a structured intent record.
    current_intent: Optional[Dict[str, Any]] = None

    # derived_facts: lightweight facts inferred deterministically from the state
    # (e.g. "selection is an edge"). Kept explicit and small.
    derived_facts: Dict[str, Any] = field(default_factory=dict)

    # State availability tracking (BIP 4.3.2): distinguishes between an empty
    # document and a document whose authoritative state is currently unknown
    # due to a failed retrieval. When retrieval fails, we preserve the last
    # known-good objects and mark state as UNAVAILABLE.
    state_available: bool = True
    state_stale: bool = False
    last_successful_sync: Optional[float] = None

    # ---- construction / updates ---------------------------------------- #

    def update_from_cad_state(self, state_json: str) -> "DesignState":
        """Ingest a full adapter state string (JSON list of object dicts).

        This is an incremental upsert: existing objects keep their updated_at
        timestamps unless the incoming record changes them; new records are added.
        """
        try:
            parsed = json.loads(state_json) if isinstance(
                state_json, str) else state_json
        except (json.JSONDecodeError, TypeError):
            # Mark state as unavailable without wiping objects.
            self.state_available = False
            self.state_stale = True
            return self

        if isinstance(parsed, dict):
            # Some adapters may return a dict wrapper; drill into common keys.
            for key in ("objects", "state", "document"):
                if isinstance(parsed.get(key), list):
                    parsed = parsed[key]
                    break

        if not isinstance(parsed, list):
            # Mark state as unavailable without wiping objects.
            self.state_available = False
            self.state_stale = True
            return self

        self.objects.clear()
        for raw in parsed:
            if not isinstance(raw, dict):
                continue
            obj = DesignObject.from_dict(raw)
            self.objects[obj.object_id] = obj

        # Successful sync — state is fresh and available.
        self.state_available = True
        self.state_stale = False
        self.last_successful_sync = _current_ts()
        self._derive_facts()
        return self

    def mark_state_unavailable(self) -> "DesignState":
        """Mark the CAD state as unavailable without clearing known objects.

        Called when a state retrieval fails (connection error, timeout, etc.).
        The last known-good objects are preserved but flagged as stale.
        """
        if self.state_available:
            self.state_available = False
            self.state_stale = True
        return self

    def update_from_tool_result(
        self,
        tool: str,
        result: Any,
        *,
        target_id: Optional[str] = None,
        args: Optional[Dict[str, Any]] = None,
        success: bool = True,
        error: Optional[str] = None,
        requested_args: Optional[Dict[str, Any]] = None,
    ) -> "DesignState":
        """Record the outcome of a CAD tool execution into the state.

        This records the *operation* and any explicit error. It does not attempt
        to re-serialize the whole document (call ``update_from_cad_state`` for a
        full refresh when a new object snapshot is available).
        """
        if success:
            self.recent_operations.append(
                RecentOperation(tool=tool, target_id=target_id,
                                args=args or {},
                                requested_args=requested_args)
            )
            self.recent_operations = self._bounded(self.recent_operations, 20)
        if error:
            self.recent_errors.append(f"{tool}: {error}")
            self.recent_errors = self.recent_errors[-10:]

        # Best-effort: reflect a newly created object id if the result names one.
        if success and target_id:
            self._ensure_object_present(target_id)
            self.current_task = self.current_task or self._hint_task(
                tool, target_id)

        self._derive_facts()
        return self

    def touch_object(self, object_id: str) -> "DesignState":
        """Mark an object id as known (adds a placeholder if not present)."""
        self._ensure_object_present(object_id)
        return self

    def set_selection(self, selection: Iterable[str]) -> "DesignState":
        self.selected_entities = [str(s) for s in selection][-50:]
        self._derive_facts()
        return self

    def set_intent(self, intent: Optional[Dict[str, Any]]) -> "DesignState":
        """Populate current_intent (future intent/decision layer writes here)."""
        self.current_intent = intent
        return self

    # ---- queries -------------------------------------------------------- #

    def get_object(self, object_id: str) -> Optional[DesignObject]:
        """Return a single object by id without exposing the full state."""
        return self.objects.get(_normalize_id(object_id))

    def get_selected_entities(self) -> List[str]:
        return list(self.selected_entities)

    def get_recent_operations(self, n: int = 5) -> List[RecentOperation]:
        return list(self.recent_operations[-n:])

    def get_recent_errors(self, n: int = 5) -> List[str]:
        return list(self.recent_errors[-n:])

    def resolve_active_object(self, object_id: str) -> Optional[DesignObject]:
        """Resolve a possibly-hidden (ghost) target to its visible child.

        Mirrors the agent's GHOST resolution rule: if the object is not visible,
        follow its ``children`` list to find the first visible active object.
        """
        obj = self.get_object(object_id)
        if obj is None:
            return None
        if obj.visible:
            return obj
        for child_id in obj.children:
            child = self.get_object(child_id)
            if child is not None and child.visible:
                return child
        return obj

    # ---- snapshots / serialization ------------------------------------- #

    def snapshot(self) -> Dict[str, Any]:
        """Return a full serializable dict of the current state."""
        return {
            "active_document": self.active_document,
            "objects": [o.to_dict(minimal=False) for o in self.objects.values()],
            "selected_entities": list(self.selected_entities),
            "recent_operations": [op.to_dict() for op in self.recent_operations],
            "recent_errors": list(self.recent_errors),
            "current_task": self.current_task,
            "current_intent": dict(self.current_intent) if self.current_intent else None,
            "derived_facts": dict(self.derived_facts),
            "state_available": self.state_available,
            "state_stale": self.state_stale,
            "last_successful_sync": self.last_successful_sync,
        }

    def clear(self) -> "DesignState":
        """Reset the state (retains nothing)."""
        self.objects.clear()
        self.selected_entities = []
        self.recent_operations = []
        self.recent_errors = []
        self.current_task = None
        self.current_intent = None
        self.derived_facts = {}
        return self

    # ---- selective view ------------------------------------------------- #

    def select_objects(self, object_ids: Iterable[str], *, depth: int = 1) -> Dict[str, Any]:
        """Build a selective view containing the named objects plus their
        immediate relationship neighbours (up to ``depth`` hops).

        This is the foundation of the "no blind state dump" compiler stage: it
        returns ONLY the requested subset instead of every object.
        """
        wanted: List[DesignObject] = []
        seen: set = set()
        queue = [_normalize_id(i) for i in object_ids]
        # -- expand relationships to the requested depth --
        for _ in range(max(0, depth) + 1):
            next_queue: List[str] = []
            for oid in queue:
                if oid in seen:
                    continue
                seen.add(oid)
                obj = self.objects.get(oid)
                if obj is None:
                    continue
                wanted.append(obj)
                for rel_id in list(obj.parents) + list(obj.children):
                    if rel_id not in seen and rel_id in self.objects:
                        next_queue.append(rel_id)
            queue = next_queue
            if not queue:
                break

        return {
            "selected": [o.object_id for o in wanted],
            "objects": [o.to_dict(minimal=False) for o in wanted],
        }

    def summary(self) -> Dict[str, Any]:
        """Compact, cheap description used by the relevance layer and routing.
        Exposes object ids + types + a solid count (mirrors ToolRouter gating).
        """
        solids = sum(1 for o in self.objects.values() if o.is_solid())
        return {
            "active_document": self.active_document,
            "object_count": len(self.objects),
            "solid_count": solids,
            "selected_entities": list(self.selected_entities),
            "object_ids": [o.object_id for o in self.objects.values()],
            "object_types": {
                o.object_id: o.object_type for o in self.objects.values()
            },
            "derived_facts": dict(self.derived_facts),
            "state_available": self.state_available,
            "state_stale": self.state_stale,
        }

    # ---- internals ------------------------------------------------------- #

    def _ensure_object_present(self, object_id: str) -> None:
        oid = _normalize_id(object_id)
        if oid not in self.objects:
            self.objects[oid] = DesignObject(object_id=oid)

    def _derive_facts(self) -> None:
        facts: Dict[str, Any] = {
            "solid_count": sum(1 for o in self.objects.values() if o.is_solid()),
            "object_count": len(self.objects),
        }
        if self.selected_entities:
            # Deterministic, cheap classification of the current selection.
            kinds = []
            for oid in self.selected_entities:
                oid_l = oid.lower()
                obj = self.objects.get(oid)
                t = obj.object_type.lower() if obj is not None else ""
                if "sketch" in t or "wire" in t or "sketch" in oid_l:
                    kinds.append("sketch")
                elif "edge" in t or "vertex" in t or \
                        oid_l.startswith(("edge", "vert")) or "edge" in oid_l:
                    kinds.append("edge")
                elif "face" in t or "face" in oid_l:
                    kinds.append("face")
                elif obj is not None:
                    kinds.append(obj.object_type)
                else:
                    kinds.append("unknown")
            facts["selection_kind"] = kinds
        self.derived_facts = facts

    @staticmethod
    def _bounded(seq: List[Any], n: int) -> List[Any]:
        return seq[-n:]

    def _hint_task(self, tool: str, target_id: str) -> str:
        return f"{tool} on {target_id}"
