# PieCAD Context Engine — Architecture

**BIP 4.2 — State & Session Memory Foundation**

This document describes the provider-independent Context / State Engine. It is a
**foundation** layer: its purpose is to let PIECAD maintain structured CAD state,
session memory, relevance-aware context, and dynamically compiled LLM payloads so
that a future intent/classification system can plug in without a redesign.

---

## 1. The Pipeline

```
USER REQUEST
    ↓
Context / State Engine          ← THIS BIP
    ↓
future Intent / Decision Layer  ← NOT implemented yet
    ↓
ContextPlan (required_tools, relevant_state, relevant_memory, ...)
    ↓
ContextCompiler (selective context)
    ↓
existing ToolRouter + provider (DeepSeek / GLM / other) → ReAct → CAD execution
    ↓
DesignState update → SessionMemory update → next reasoning step
```

The **Intent / Decision Layer does NOT exist yet.** The Context Engine implements
all the infrastructure underneath it through deterministic relevance rules as a
temporary selection mechanism.

---

## 2. Components

### 2.1 `DesignState` — [`state.py`](state.py)

The single authoritative abstraction answering **"what exists?"** in the CAD
document. It mirrors the real FreeCAD state (a JSON list of objects keyed by
`id`, with `type`, `visible`, `parents`, `children`, `properties`), so no
duplicate CAD metadata is invented.

- **Indexed** objects (`objects: Dict[str, DesignObject]`).
- Incremental updates: `update_from_cad_state` / `update_from_tool_result`.
- Relationship support (`parents`/`children`) exactly as reported by the adapter —
  **no fake relationships**.
- Queries: `get_object(id)`, `get_selected_entities()`, `get_recent_operations()`,
  `get_recent_errors()`, `resolve_active_object()` (ghost → visible child).
- **Selective** views: `select_objects(object_ids, depth)` and `summary()` are the
  backbone of "no blind state dump".
- Lightweight + fully serializable (`snapshot()`).

`current_intent` is **optional** and reserved for a future classifier.

### 2.2 `SessionMemory` — [`memory.py`](memory.py)

A separate, per-session abstraction answering **"what have we learned, decided, or
preferred?"**. It is deliberately **not mixed** with `DesignState`.

- Kind-tagged entries: `fact`, `user_preference`, `decision`, `convention`,
  `correction`, `assumption`, `inference`.
- API: `set/get/remove/clear/snapshot` + **selective** `relevant(text)`.
- Session-scoped only: no persistence, no database, no embeddings/vector search,
  no correction-collection pipeline, **no auto-promotion of LLM guesses**.

### 2.3 `ConversationContext` — [`conversation.py`](conversation.py)

Bounded, relevance-aware chat history. Never blindly concatenated.

- Current user turn **always** retained.
- Recent turns within a bounded window always retained.
- Older turns included **only** when they reference concepts relevant to the
  current request.

### 2.4 `ContextPlan` & `ToolSelectionPlan` — [`plan.py`](plan.py)

Provider-independent specifications of **what the next LLM step requires**:

```python
ContextPlan(
    required_tools=["hole", "get_faces", "recompute", "get_error"],
    relevant_object_ids=["box1"],
    required_state=["objects", "selection", "feature_tree"],
    required_memory=["conventions"],
    relevant_history=[...],
    reasoning_mode="modify",
    confidence=0.9,
    ambiguity=None,
)
```

`ToolSelectionPlan` describes `primary / optional / inspection / verification /
recovery` tool roles. It does **not** replace `ToolRouter` — the router still
functions and the Context Engine selects from the full adapter surface.

### 2.5 `RelevanceEngine` — [`relevance.py`](relevance.py)

The **temporary** deterministic selection mechanism. Built as a rule/strategy
list (`RelevanceRule`), not a giant if/else. Concept rules cover: hole, fillet,
chamfer, box, cylinder, sketch, pad, pocket, boolean, pattern, inspect, measure,
export, delete, modify, select, undo, redo, and vague modifications.

It is **state-aware**: relevance is a function of
`(user_request, recent_conversation, design_state, session_memory)`, not just text.
E.g. "make it round" on a selected edge surfaces fillet/chamfer + edge inspection;
on a sketch it surfaces sketch/extrude; with no selection it retains inspection
tools.

> This is **NOT** the final classifier. It is easy to replace or augment.

### 2.6 `ContextCompiler` — [`compiler.py`](compiler.py)

The central component. `compile(...)` assembles a **selective** compiled context
for one reasoning step:

```
1. determine requirements   (RelevanceEngine → ContextPlan, or caller-provided plan)
2. select state             (DesignState.select_objects / summary)
3. select memory            (SessionMemory.relevant + plan sections)
4. select history           (ConversationContext.relevant_history)
5. select tools             (existing ToolRouter ∪ plan-required tools)
6. assemble context         (system/user/conversation/state/memory/tools/metadata)
7. estimate size            (chars/4 heuristic telemetry)
8. return CompiledContext
```

It never dumps the whole document/memory/history. It uses existing message/tool
formats so it stays compatible with the ReAct loop and the provider.

### 2.7 `ContextBudget` — [`budget.py`](budget.py)

Deterministic budget mechanism for maximum context tokens, reserved output tokens,
and per-section budgets (state / memory / tools / history). Does **not**
aggressively truncate useful info; when something is dropped it is recorded in
`dropped_sections` for telemetry.

### 2.8 Telemetry — [`telemetry.py`](telemetry.py)

Per-LLM-call metrics. **No heavy tokenizer** (no tiktoken).

- Estimates use the lightweight **chars/4** heuristic and are always labelled
  `estimated_*`.
- Exact provider token counts, when reported, live under `exact_provider_tokens`
  — estimates vs exact are always distinguishable.
- Captures: ReAct step, per-section estimated tokens, tools exposed, why-tools,
  dropped sections, compilation time, latency.

---

## 3. Future Extension Point

```
Future Intent Classifier
        ↓ produces a
ContextPlan  (and optionally a ToolSelectionPlan)
        ↓ consumed by
ContextCompiler
```

The **classifier is NOT implemented yet.** The `ContextCompiler.compile(...)`
accepts an `optional_context_plan`; the moment a classifier (deterministic,
Jev/System-1, another model, or a hybrid) produces a `ContextPlan`, the Context
Engine consumes it **without any architectural change**. No part of the Context
Engine depends on a specific classifier. Jev is **not** a required dependency.

---

## 4. "No Blind Dump" Guarantees

| Section   | Behaviour                                                                                                            |
| --------- | -------------------------------------------------------------------------------------------------------------------- |
| CAD state | Only objects matching `relevant_object_ids` + relationship neighbours are compiled; otherwise a compact `summary()`. |
| Memory    | Only kinds/sections relevant to the request; unrelated decisions are excluded.                                       |
| History   | Only current + recent window + concept-relevant older turns.                                                         |
| Tools     | Full adapter surface is preserved; per-call exposure is narrowed to routed ∪ plan-required.                          |

---

## 5. Observability

Through `CADAgent.get_context_telemetry()` and `CompiledContext.metadata` one can
answer: what state did the agent know, what memory/history was used, which tools
were exposed and why (`matched_rules`), what was omitted (`dropped_sections`),
the estimated compiled-context size, and which ReAct step produced it. This is
available without dumping large payloads into normal logs.

---

## 6. Constraints Honoured

- No copy of the 172-tool capability surface is reduced; the adapter retains the
  full surface and only per-call exposure is narrowed.
- The existing `ToolRouter`, `CADAgent`, `FreeCADAdapter`, MCP server, and MCP tool
  definitions are **not** replaced or modified.
- Provider-independent: no provider-specific context logic.
- No new heavy dependencies; no `tiktoken`.
