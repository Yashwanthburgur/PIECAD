"""PieCAD Core Orchestrator. CAD-agnostic."""
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from providers.llm.provider import LLMProvider
from core.adapters.interfaces import CADAdapter
from core.router import ToolRouter
from core.verification.checks import check_geometry, GeometryVerifier, ParameterVerifier, VerificationResult
# Context Engine (BIP 4.2): provider-independent state / memory / compilation.
from core.context import (
    CompiledContext,
    ContextCompiler,
    ContextPlan,
    ConversationContext,
    DesignState,
    SessionMemory,
    StaleTopologyError,
    ToolSelectionPlan,
)
from core.intent import IntentClassifier
from core.tool_registry import get_global_registry, infer_capability_from_schema
from core.operations import (
    OperationRegistry,
    MutationGate,
    OperationStatus,
)
# BIP 10.5: Import locally in _measure_router_savings to allow test patching

# Transient LLM/transport exception types. Imported defensively so the core
# remains provider-independent: if the openai SDK is unavailable, we fall back
# to a message-based classification (see _is_transient_llm_error).
try:  # pragma: no cover - import surface depends on installed SDK
    from openai import (
        APIConnectionError as _OpenAIAPIConnectionError,
        APITimeoutError as _OpenAIAPITimeoutError,
        RateLimitError as _OpenAIRateLimitError,
        InternalServerError as _OpenAIInternalServerError,
    )
    _OPENAI_TRANSIENT_EXC: tuple = (
        _OpenAIAPIConnectionError,
        _OpenAIAPITimeoutError,
        _OpenAIRateLimitError,
        _OpenAIInternalServerError,
    )
except Exception:  # pragma: no cover
    _OPENAI_TRANSIENT_EXC = ()

# ARCHITECTURE RULE - Object Identity: Property change on an unconsumed object -> set_param in place; Topology change -> new feature object, old one is auto-hidden (Ghost).

# Base system prompt - defines the agent's role and critical rules
SYSTEM_PROMPT = """You are PieCAD, a production-grade mechanical engineering AI agent.
You control a live CAD model/document.

COMMUNICATION RULE:
You are an industrial backend execution engine. DO NOT chat. DO NOT list the steps you took. DO NOT explain your reasoning to the user.
When you finish a task, reply with MAXIMUM TWO SENTENCES stating exactly what the current visible final object is.

CRITICAL RULES:
1. STATE AWARENESS: You will be provided with the CURRENT CAD STATE. Never guess object names. Always reference exact names and dimensions from the state.
2. NO SPAMMING: Never create duplicate base geometry (e.g., Box001, Box002) to fix a mistake. If a user asks you to fix or "undo" something, use the `delete_feature` tool or use `set_param` to modify the existing object.
3. FIXING ERRORS: If a user gives you a physically impossible command (e.g., Fillet radius 50 on a 20mm box) and tells you to "fix it" or "do what is suitable", you must apply the correct modification to the EXISTING object (e.g., execute a fillet with a 5mm radius on the original box). Do NOT spawn a new box.
4. UNDO REQUESTS: If the user says "undo", look at the most recent object in the state and use `delete_feature` to remove it.
5. CONCISENESS: Do not output long conversational apologies. Just execute the tool calls to fix the geometry.
6. CRITICAL RULE: Do not stop until you have completely fulfilled ALL steps of the user's requested design. If the user asks for a box WITH a shell, you must execute both tools. Once the ENTIRE final shape is built, DO NOT call verification tools like get_faces, get_bom, or get_mass_properties. Immediately output your final text response and STOP.
7. CRITICAL RULE: If a tool like 'shell' or 'mate' is missing from your available tools, DO NOT hallucinate it. It means you must first use primitive tools (like 'box' or 'cylinder') to create solid objects. The advanced tools will automatically unlock in the next step once the base geometry exists.

GHOST OBJECT RESOLUTION:
If a target object's `visible` property is false, it has been consumed by a downstream feature (e.g., a boolean cut). You cannot operate on a hidden ghost object. Instead, resolve to the active object in its `children` list.

OBJECT IDENTITY RULES:
When modifying an object: Property changes on an unconsumed object modify it in place. Topology changes (like boolean cuts or fillets) always yield a new feature object, and the old one is automatically hidden.

FEATURE PATTERNS:
You have access to `pattern_linear` and `pattern_circular` tools. NEVER manually calculate coordinates to array multiple identical objects (like bolts or holes). Always create a single tool object and use the pattern tools to array it.

MANUFACTURING HOLES:
When a user asks for a hole, drill, or tapped/threaded hole (e.g., 'M6 tapped hole', 'threaded hole for M8 bolt', '1/4-20 UNC tapped hole'), DO NOT use cylinder and boolean subtract manually. ALWAYS use the dedicated `hole` tool.
- Set the `target_id` to the body being drilled.
- For non-tapped holes, set `kind` to 'simple' and provide the requested diameter.
- For tapped/threaded holes, set `kind` to 'tapped' and specify the full standard designation in `thread_spec` (e.g., 'M6x1.0', 'M8x1.25', 'M10x1.5', '1/4-20 UNC', '5/16-18 UNC').
- If the user gives only a nominal metric size (e.g., 'M6'), use the standard coarse designation (e.g., 'M6x1.0').
- Direct numeric diameters must NOT override standard thread callouts when `kind='tapped'` is requested. The engine will automatically use the correct tap drill diameter and tag the resulting feature with thread metadata.

SHELLING AND HOLLOWING:
When asked to hollow out a body, create an enclosure, or make an open container/box:
- You MUST follow this exact 3-step sequence:
  Step 1: Create the outer solid body (e.g. 'box').
  Step 2: Query its faces using 'get_faces'.
  Step 3: Call the 'shell' tool on the very next step. Set target_id to the solid, face_refs to the face to remove (e.g. ['box1_face_6']), and thickness to the inward value (e.g. -2.0).
- STRICT PROHIBITION: NEVER construct containers by spawning multiple boxes, bottom plates, or inner boxes. NEVER use boolean subtraction to hollow. Any manual box-within-a-box construction is an immediate system failure. Use 'shell'.

ASSEMBLY AND MATING CONSTRAINTS:
You can build multi-part assemblies by spawning independent bodies and constraining them with the `mate` tool.
- STRICT RULE: NEVER manually pre-calculate assembly coordinates or spawn components already aligned when the user asks to mate them. You MUST spawn components at their default position and use the `mate` tool.
- Available mate types:
  1. 'concentric': Aligns the central axis of two cylinders, holes, or circular edges. Leaves axial sliding free.
  2. 'coincident': Brings two planar faces into flush contact (opposing normals). Leaves planar sliding free.
- Typical workflow:
  Step 1: Create Part A (e.g. base plate with hole).
  Step 2: Create Part B (e.g. pin at origin or offset).
  Step 3: Query faces/edges with 'get_faces' or 'get_edges'.
  Step 4: Use 'mate' with mate_type='concentric' to align the pin into the hole.
  Step 5: (Optional) Use 'mate' with mate_type='coincident' to seat the pin flush.

INTERFERENCE CHECK (MANDATORY):
After completing multi-part assemblies or executing `mate` constraints, you MUST run `interference_check` to verify zero geometric collision before answering the user or exporting.
- The tool performs pairwise B-Rep boolean intersection across all active visible solids (or a specified subset).
- A clash is flagged when the common volume exceeds 1e-4 mm³ (filters numerical noise from touching faces/edges).
- If `has_clash: true` is returned, you MUST fix the assembly (adjust mates, reposition parts) and re-run `interference_check` until `has_clash: false`.
- Do NOT proceed to export or final response until the assembly is clash-free.

EXPORTING FILES:
When a user asks to save, download, or export a model (e.g., "save as STEP", "export to STL", "download the model"), you MUST use the `export` tool.
- The `export` tool saves the current visible assembly to a file in the project's exports/ folder.
- Specify the format ('step' or 'stl') and a descriptive filename without extension.
- Your FINAL TEXT RESPONSE to the user MUST explicitly include the absolute local file path returned by the tool, so the user knows exactly where to find their file.

ERROR RECOVERY AND SELF-HEALING:
If a tool returns an error, `<Fault>`, or `RuntimeError` from the CAD kernel, DO NOT apologize and DO NOT immediately ask the user for help. You are an autonomous engineer. You must diagnose the physical failure and retry with adjusted parameters.
- If `fillet` or `chamfer` fails with "BRep_API: command not done" or a topology error: Your radius/distance is physically too large for the edge, causing faces to self-intersect. Halve the radius (e.g., from 5.0 to 2.5) and call the tool again.
- If `shell` fails: The thickness might be too large, or you selected the wrong face to remove. Try a smaller thickness or flip the sign (e.g., -2.0 to 2.0).
- If `mate` fails: You likely targeted an invalid sub-element. Re-run `get_faces` or `get_edges` to verify the exact ID of the face or edge, then retry the mate.
- If `hole` fails: Your diameter may be larger than the target object itself. Reduce the diameter and retry.
ALWAYS attempt at least two mathematical corrections before informing the user that a geometry is impossible to construct.

PARAMETRIC EDITING:
If the user asks to change the size, dimension, or property of an existing part (e.g., "make the box 20mm wider", "increase the hole radius to 15"):
1. DO NOT delete and recreate the object. Doing so destroys the parametric history.
2. Use the `edit_feature` tool, providing the `target_id` and a dictionary of the properties to update (e.g., `{"Width": 70.0}`).
3. The CAD kernel will automatically cascade these dimension changes to all downstream dependent features.
4. MOVING OBJECTS: If an object becomes off-center after resizing a parent, use `edit_feature` and pass `x`, `y`, or `z` in the parameters dictionary to shift its absolute position. Do not guess coordinate names like OriginX.

EDITING HOLES/CUTS: A boolean cut object does not have a Radius or Length. To resize a hole, you MUST use edit_feature on the hidden drill tool object (e.g., if the cut is 'hole1', edit the Radius of 'hole1_drill'). NEVER delete a feature just to change its size.

"""


# Per-step injection to force ReAct loop discipline
REACT_LOOP_INJECTION = """You are in a multi-step ReAct loop. DO NOT output conversational text until you have completed ALL steps of the user's request.
- You MUST call tools to make progress.
- If you just executed a tool, evaluate the NEW state and immediately call the NEXT tool.
- Only respond with plain text (no tool calls) when the ENTIRE user request is satisfied.
- NEVER delete objects you just created unless the user explicitly asked to undo.
"""


@dataclass
class ToolExecutionResult:
    """Explicit result of a single tool execution attempt (A6.3).

    Replaces the previous positional 12-tuple + ``"__malformed__"`` string
    sentinel contract of ``_execute_tool_calls``. Fields carry exactly the
    values the caller consumed from the tuple, plus ``malformed`` to represent
    malformed tool calls explicitly instead of via a magic string.
    """

    name: Any = None
    args: Dict[str, Any] = field(default_factory=dict)
    target: Any = None
    out: Any = None
    error: Any = None
    success: bool = False
    attempts: int = 0
    transient: bool = False
    operation_id: Optional[str] = None
    is_mutation: bool = False
    gate_acquired: bool = False
    requested_args_for_recording: Any = None
    # When True this is a malformed-arguments result: ``malformed_result`` holds
    # the pre-serialized structured error to append to the caller's ``results``,
    # and no verification/execution/topology logic ran.
    malformed: bool = False
    malformed_result: Any = None


# ──────────────────────────────────────────────────────────────────────────
# Scratchpad compaction (BIP 4.2 / token ceiling mitigation)
# ──────────────────────────────────────────────────────────────────────────
# The scratchpad accumulates assistant tool calls + tool results across ReAct
# steps. To prevent unbounded token growth while preserving CAD-critical
# information, we compact older interactions into concise summaries.
#
# What MUST be preserved in summaries:
#   - stale topology state per target (NOT in DesignState)
#   - face/edge reference counts for stale targets (helps agent avoid extra get_edges)
#   - verification warnings (transient, not in DesignState)
#
# What can be dropped (redundant with DesignState/ContextCompiler):
#   - object IDs / result IDs (in DesignState.objects)
#   - topology versions (DesignState has authoritative versions)
#   - operation parameters (DesignState.object.properties)
#   - success/failure history (DesignState.recent_operations)
#   - normal errors (DesignState.recent_errors)
#   - historical event sequence
#   - face/edge refs for non-stale operations (DesignState has via get_edges)
#
# Compaction is deterministic and local — NO LLM CALLS.

# Persistent scratchpad summary buffer.
# Stored on the CADAgent instance to survive across compaction calls.
# Each compaction merges new history into this single buffer.
# keep this many recent raw messages verbatim
_SCRATCHPAD_KEEP_RECENT = 6
_SCRATCHPAD_SUMMARY_PREFIX = "[scratchpad summary] "


def _format_persistent_summary(summary_dict: dict) -> str:
    """Convert internal summary dictionary to LLM-readable string."""
    if not summary_dict:
        return ""
    parts = []
    # Stale topology entries (deduplicated per target)
    stale = summary_dict.get("stale", {})
    if stale:
        for target_id, info in stale.items():
            version = info.get("version", "unknown")
            refs = info.get("refs")
            part = f"STALE: {target_id} (v{version})"
            if refs:
                part += f" | {refs.get('type', 'edge')}_refs={refs.get('count', 1)}"
            parts.append(part)
    # Verification warnings (keep last 10)
    warnings = summary_dict.get("warnings", [])
    if warnings:
        for w in warnings[-10:]:
            # Avoid double "WARNING:" prefix if already present
            if w.startswith("WARNING:"):
                parts.append(w)
            else:
                parts.append(f"WARNING: {w}")
    return _SCRATCHPAD_SUMMARY_PREFIX + "; ".join(parts) if parts else ""


def _parse_persistent_summary(summary_str: Optional[str]) -> dict:
    """Parse persistent summary string back into dictionary structure."""
    if not summary_str:
        return {"stale": {}, "warnings": []}
    # Expected format: "[scratchpad summary] STALE: box0 (vv0) | edge_refs=2; WARNING: ..."
    prefix = _SCRATCHPAD_SUMMARY_PREFIX
    if not summary_str.startswith(prefix):
        return {"stale": {}, "warnings": []}
    content = summary_str[len(prefix):]
    stale = {}
    warnings = []
    parts = content.split("; ")
    for part in parts:
        part = part.strip()
        if part.startswith("STALE: "):
            # Format: "STALE: box0 (vv0) | edge_refs=2"
            stale_part = part[7:]  # Remove "STALE: "
            # Parse "target (vversion) | edge_refs=N"
            if " (" in stale_part:
                target_end = stale_part.index(" (")
                target_id = stale_part[:target_end]
                rest = stale_part[target_end+1:]  # "vX) | edge_refs=2"
                version_end = rest.index(")")
                version = rest[:version_end]
                refs = None
                if "|" in rest:
                    ref_part = rest.split("|", 1)[1].strip()
                    if "_refs=" in ref_part:
                        ref_type, count = ref_part.split("=")
                        refs = {"type": ref_type, "count": int(count)}
                stale[target_id] = {"version": version, "refs": refs}
        elif part.startswith("WARNING: "):
            warnings.append(part[9:])  # Remove "WARNING: "
    return {"stale": stale, "warnings": warnings}


def _build_step_summary(
    tool_calls: List[Any],
    tool_results: List[Dict[str, Any]],
) -> List[dict]:
    """
    Build summary entries for a single ReAct step's tool calls + results.

    Returns a list of summary dicts (one per tool call in the step).
    Each dict has keys: type ("stale" or "warning"), target_id, version, refs, message
    """
    summary_entries: List[dict] = []
    for tc, tr in zip(tool_calls, tool_results):
        tc_func = tc.function if hasattr(
            tc, 'function') else tc.get('function', {})
        name = tc_func.get('name', 'unknown') if isinstance(
            tc_func, dict) else getattr(tc_func, 'name', 'unknown')

        # Parse arguments
        try:
            args = json.loads(tc_func.get('arguments', '{}')) if isinstance(
                tc_func, dict) else json.loads(getattr(tc_func, 'arguments', '{}'))
        except (json.JSONDecodeError, TypeError):
            args = {}

        target_id = args.get("target_id") or args.get(
            "target") or args.get("object") or args.get("object_name")

        # Parse tool result content
        content = tr.get("content", "") if isinstance(tr, dict) else str(tr)
        try:
            result_parsed = json.loads(content) if isinstance(
                content, str) else content
        except (json.JSONDecodeError, TypeError):
            result_parsed = {"raw": content}

        # Extract only what's NOT in DesignState
        stale_topology = result_parsed.get(
            "stale_topology", False) if isinstance(result_parsed, dict) else False

        # Extract face/edge refs from args
        edge_refs = args.get("edge_refs") or args.get("face_refs")
        topology_version = args.get("topology_version")

        # Extract success/failure
        success = result_parsed.get("status") != "error" if isinstance(
            result_parsed, dict) else True

        # Emit stale topology info — NOT in DesignState
        if stale_topology and target_id:
            tv = topology_version if topology_version and topology_version != "0" else "unknown"
            refs = None
            if edge_refs:
                ref_count = len(edge_refs) if isinstance(
                    edge_refs, list) else 1
                ref_type = "edge" if "edge" in str(
                    edge_refs).lower() else "face"
                refs = {"type": ref_type, "count": ref_count}
            summary_entries.append({
                "type": "stale",
                "target_id": target_id,
                "version": tv,
                "refs": refs
            })

        # For non-stale operations, don't emit anything (all info in DesignState)
    return summary_entries


def _extract_system_warnings(scratchpad: List[Dict[str, Any]]) -> List[str]:
    """Extract verification warnings from system messages in scratchpad."""
    warnings = []
    for msg in scratchpad:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            if content and ("WARNING" in content or "warning" in content.lower()):
                truncated = content[:200] + \
                    "..." if len(content) > 200 else content
                # Ensure consistent WARNING: prefix
                if not truncated.startswith("WARNING:"):
                    truncated = f"WARNING: {truncated}"
                warnings.append(truncated)
    return warnings


def _compact_scratchpad(
    scratchpad: List[Dict[str, Any]],
    persistent_summary: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Compact the scratchpad by merging older interactions into a persistent summary.

    The persistent summary lives OUTSIDE the raw scratchpad and is stored on
    the CADAgent instance. This function:
    1. Processes ALL messages (recent + older) to track stale state
    2. Keeps only the latest `_SCRATCHPAD_KEEP_RECENT` raw messages
       (the persistent summary is NOT returned as a message)

    Args:
        scratchpad: List of raw messages (assistant tool_calls + tool results + system warnings)
        persistent_summary: Existing summary string from previous compactions (or None)

    Returns:
        Tuple of (recent_messages, updated_persistent_summary)
        - recent_messages: Last _SCRATCHPAD_KEEP_RECENT raw messages
        - updated_persistent_summary: Merged summary string (or None if empty)
    """
    # Parse existing persistent summary into dict
    summary_dict = _parse_persistent_summary(persistent_summary)

    # FIRST PASS: Process ALL messages (recent + older) to track stale state
    i = 0
    while i < len(scratchpad):
        msg = scratchpad[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            tool_calls = msg.get("tool_calls", [])
            step_tool_count = len(tool_calls)

            # Collect corresponding tool results
            tool_results = []
            for j in range(step_tool_count):
                if i + 1 + j < len(scratchpad) and scratchpad[i + 1 + j].get("role") == "tool":
                    tool_results.append(scratchpad[i + 1 + j])
                else:
                    break

            # Check for stale topology (add/update) and resolution
            for tc, tr in zip(tool_calls, tool_results):
                # Parse tool result
                content = tr.get("content", "") if isinstance(
                    tr, dict) else str(tr)
                try:
                    result_parsed = json.loads(content) if isinstance(
                        content, str) else content
                except (json.JSONDecodeError, TypeError):
                    result_parsed = {"raw": content}

                stale_topology = result_parsed.get(
                    "stale_topology", False) if isinstance(result_parsed, dict) else False
                success = result_parsed.get("status") != "error" if isinstance(
                    result_parsed, dict) else True

                # Get target_id
                tc_func = tc.function if hasattr(
                    tc, 'function') else tc.get('function', {})
                args = {}
                try:
                    args = json.loads(tc_func.get('arguments', '{}')) if isinstance(
                        tc_func, dict) else json.loads(getattr(tc_func, 'arguments', '{}'))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                target_id = args.get("target_id") or args.get(
                    "target") or args.get("object") or args.get("object_name")

                if not target_id:
                    continue

                if stale_topology:
                    # Extract stale info
                    edge_refs = args.get("edge_refs") or args.get("face_refs")
                    topology_version = args.get("topology_version")
                    tv = topology_version if topology_version and topology_version != "0" else "unknown"
                    refs = None
                    if edge_refs:
                        ref_count = len(edge_refs) if isinstance(
                            edge_refs, list) else 1
                        ref_type = "edge" if "edge" in str(
                            edge_refs).lower() else "face"
                        refs = {"type": ref_type, "count": ref_count}
                    # Update stale state (overwrites any existing entry)
                    if target_id not in summary_dict["stale"]:
                        summary_dict["stale"][target_id] = {}
                    summary_dict["stale"][target_id]["version"] = topology_version if topology_version and topology_version != "0" else "unknown"
                    if edge_refs:
                        ref_count = len(edge_refs) if isinstance(
                            edge_refs, list) else 1
                        ref_type = "edge" if "edge" in str(
                            edge_refs).lower() else "face"
                        refs = {"type": ref_type, "count": ref_count}
                        summary_dict["stale"][target_id]["refs"] = refs
                # NOTE: No automatic stale-entry resolution on success.
                # Resolution requires an authoritative topology-version signal
                # (e.g., fresh get_edges/get_faces re-query), not mere status != "error".

        elif msg.get("role") == "system":
            # Extract warnings from system messages
            warnings = _extract_system_warnings([msg])
            summary_dict["warnings"].extend(warnings)
            # Keep only last 10 warnings
            if len(summary_dict["warnings"]) > 10:
                summary_dict["warnings"] = summary_dict["warnings"][-10:]
        i += 1

    # SECOND PASS: Keep only recent raw messages for output
    recent = scratchpad[-_SCRATCHPAD_KEEP_RECENT:]

    # Format updated summary dict to string
    updated_summary = _format_persistent_summary(summary_dict)

    # Never return None for summary - use empty string
    return scratchpad[-_SCRATCHPAD_KEEP_RECENT:], updated_summary


class CADAgent:
    MAX_RETRIES = 3
    MAX_STEPS = 15
    # BIP 10.4b: Overall wall-clock deadline for a single handle_message call.
    # Bounds the whole ReAct turn (not just one provider/CAD call). None disables.
    MAX_TURN_SECONDS: Optional[float] = 600.0
    TURN_DEADLINE_TERMINATION_REASON = "turn_deadline"
    # BIP 10.4: Hard ceiling on total provider-reported LLM tokens consumed
    # within a single handle_message() call. ``None`` disables enforcement.
    MAX_TOKENS_PER_TURN: Optional[int] = 100000
    # Distinct termination reason for the per-turn token ceiling. Kept separate
    # from MAX_STEPS exhaustion, provider/API failure, context-budget
    # trimming/overflow, and tool execution failure.
    TOKEN_CEILING_TERMINATION_REASON = "token_ceiling"
    # A6.4: Consistent named termination reasons for every terminal outcome, so
    # the whole set is represented uniformly across the termination boundary.
    MAX_STEPS_TERMINATION_REASON = "max_steps"
    NO_TOOL_CALLS_TERMINATION_REASON = "no_tool_calls"
    EMPTY_RESPONSE_TERMINATION_REASON = "empty_response_continue"
    LLM_ERROR_TERMINATION_REASON = "llm_error"
    LLM_TRANSIENT_EXHAUSTED_TERMINATION_REASON = "llm_transient_exhausted"
    # Bounded retry budget for transient LLM/provider transport failures.
    MAX_LLM_RETRIES = 2
    # Base backoff (seconds) between LLM retries; doubled per attempt.
    LLM_RETRY_BACKOFF = 0.5

    # Sentinel for "parameter not provided" to distinguish from explicit None
    _UNSET = object()

    def __init__(
        self,
        adapter: CADAdapter,
        provider: Optional[LLMProvider] = None,
        *,
        capture_trace: bool = False,
        max_tokens_per_turn: Optional[int] = _UNSET,
    ):
        self.adapter = adapter
        self.provider = provider or LLMProvider()
        # Tool gating: only relevant tools are exposed to the LLM per step.
        self.router = ToolRouter()
        # Intent classifier for dynamic tool routing
        self.intent_classifier = IntentClassifier()
        # Long-term conversation history: ONLY user prompts and final agent responses
        self.history = []
        # Evaluation instrumentation (opt-in)
        self._capture_trace = capture_trace
        self._trace: list = []

        # ---- Context Engine (BIP 4.2) ----
        # Single authoritative DesignState + per-session SessionMemory + the
        # central ContextCompiler. History also feeds a ConversationContext that
        # selects the relevant subset (never a blind dump).
        self.design_state = DesignState()
        self.session_memory = SessionMemory()
        self.conversation = ConversationContext(default_recent_window=4)
        self.compiler = ContextCompiler()

        # BIP 9.3: Wire the DesignState into the adapter (if it supports the
        # optional signature-validation hook) so the adapter can raise
        # StaleTopologyError when a stored face/edge signature no longer
        # matches the live geometry.
        if hasattr(self.adapter, "design_state"):
            self.adapter.design_state = self.design_state

        # Telemetry for the most recent LLM call(s). Accessible for observability
        # without dumping large payloads into normal logs.
        self._context_telemetry: List[Dict[str, Any]] = []

        # BIP 10.4: Per-turn total-token ceiling (None disables enforcement).
        # Configurable per instance; defaults to the class-wide ceiling.
        # Explicit None disables the ceiling; not provided uses class default.
        self.max_tokens_per_turn: Optional[int] = (
            self.MAX_TOKENS_PER_TURN
            if max_tokens_per_turn is self._UNSET
            else max_tokens_per_turn
        )

        # BIP 10.2: Token-count instrumentation for the current handle_message call.
        self._token_telemetry: Dict[str, Any] = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "llm_calls": 0,
            "model": None,
            "provider": None,
            "per_step": [],
            # BIP 10.4: per-turn ceiling observability.
            "ceiling_limit": None,
            "ceiling_reached": False,
        }

        # BIP 10.5: Router token-savings instrumentation.
        # Records per-step tool-filtering token savings.
        self._router_token_savings: List[Dict[str, Any]] = []

        # BIP 10.3: Session-scoped design conventions memory.
        # These persist across handle_message calls within the same session.
        # Convention entries are stored in session_memory with kind="convention".

        # BIP 4.3.2: Track last failed tool args per (tool, target) for
        # requested-vs-achieved integrity. When a tool fails with a non-transient
        # error, we remember the requested args. If the next successful call to
        # the same tool/target has different args, both are preserved.
        self._last_failed_args: Dict[tuple, Dict[str, Any]] = {}

        # BIP 7.0: Operation lifecycle and mutation serialization
        self._operation_registry = OperationRegistry()
        self._mutation_gate = MutationGate(self._operation_registry)
        self._operation_counter: int = 0

        # BIP 8.3: Bounding-box constraints from user request (e.g., "fit within 100 x 50 x 20 mm")
        # {"max_x": 100.0, "max_y": 50.0, "max_z": 20.0}
        self._bbox_constraints: Optional[Dict[str, float]] = None

        # Persistent scratchpad summary buffer for bounded compaction
        # Stores merged historical summary across compaction rounds
        self._scratchpad_persistent_summary: Optional[str] = None

    # BIP 10.3: Design conventions management methods
    def record_design_convention(
        self,
        key: str,
        value: str,
        source: str = "agent",
    ) -> None:
        """Record a design convention in session memory.

        Args:
            key: A short identifier for the convention (e.g., "default_hole_size")
            value: The convention text (e.g., "prefer M6 over M8")
            source: Source of the convention (e.g., "user", "agent", "cadagent.update_memory")
        """
        self.session_memory.set(
            key=key,
            value=value,
            kind="convention",
            source=source,
        )

    def get_design_convention(self, key: str) -> Optional[str]:
        """Retrieve a design convention by key."""
        return self.session_memory.get(key)

    def get_all_design_conventions(self) -> List[Dict[str, Any]]:
        """Get all design conventions stored in session memory."""
        conventions = self.session_memory.by_kind("convention")
        return [
            {"key": c.key, "value": c.value, "source": c.source}
            for c in conventions
        ]

    # BIP 10.3: Detect and record explicit design conventions from user messages
    def _maybe_record_convention_from_message(self, message: str) -> None:
        """Extract and record explicit design conventions from user message.

        Looks for explicit convention statements like:
        - "set convention X to Y"
        - "establish convention X as Y"
        - "use convention X = Y"
        - "convention: X = Y"
        """
        import re

        # Pattern: "set convention <key> to <value>"
        # or "establish convention <key> as <value>"
        # or "convention <key> = <value>" or "convention: <key> = <value>"
        # Allow underscores and hyphens in keys, and any non-whitespace in values
        patterns = [
            r'set convention\s+([\w\-]+)\s+to\s+(.+)',
            r'establish convention\s+([\w\-]+)\s+as\s+(.+)',
            r'convention\s+([\w\-]+)\s*=\s*(.+)',
            r'convention:\s*([\w\-]+)\s*=\s*(.+)',
        ]

        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                key = match.group(1).strip()
                value = match.group(2).strip()
                if key and value:
                    self.record_design_convention(key, value, source="user")
                    break

    def _classify_intent(self, user_message: str):
        """Classify user intent and convert to context plan.

        Args:
            user_message: The user's input message to classify.

        Returns:
            Tuple of (intent_plan, intent_tool_plan) where:
            - intent_plan: The context plan from intent classifier
            - intent_tool_plan: The tool selection plan from intent classifier
        """
        intent_result = self.intent_classifier.classify(user_message)
        intent_plan = self.intent_classifier.to_context_plan(
            intent_result, user_message)
        intent_tool_plan = intent_result.tool_selection_plan
        print(f"[Agent] Intent classified: {intent_result.primary_intent} "
              f"(confidence={intent_result.confidence:.2f}, "
              f"tools={len(intent_result.required_tools)})")
        return intent_plan, intent_tool_plan
    # Deterministic CAD-kernel failure markers. A kernel/geometry failure is
    # NEVER transient, even when its message happens to contain a transient-
    # looking word (e.g. "Shape heal timeout", "Recompute Failed ... timeout").
    _KERNEL_FAILURE_MARKERS = (
        "kernel", "brep_api", "brep", "command not done", "invalid geometry",
        "failed to compute", "recompute failed", "makethickness",
        "shape.isvalid", "self-intersect", "self intersect",
        "null shape", "no valid geometry", "topology references",
    )

    def _is_transient_error(self, error: RuntimeError) -> bool:
        """Return True only for genuine transport/connection failures.

        Kernel/geometry failures are deterministic and must never be retried as
        transport errors merely because their message contains "timeout". We
        therefore apply a NEGATIVE filter first: if the error carries a
        CAD-kernel failure marker, it is non-transient regardless of other
        keywords.
        """
        msg = str(error).lower()
        if any(marker in msg for marker in self._KERNEL_FAILURE_MARKERS):
            return False
        transient_indicators = [
            "connection", "reset", "refused", "timeout", "unreachable",
            "broken pipe", "connection aborted", "connection lost",
            "cannot reach", "cannot connect",
        ]
        return any(ind in msg for ind in transient_indicators)

    def _is_transient_llm_error(self, error: Exception) -> bool:
        """Return True if ``error`` is a transient LLM/provider transport failure.

        Transient failures (connection drops, timeouts, rate limits, HTTP 5xx)
        are worth a bounded retry. Deterministic 4xx errors (bad request, auth,
        not found, unprocessable) are NOT retried because retrying cannot help
        and would only waste budget.
        """
        # Type-based classification when the openai SDK surface is available.
        if _OPENAI_TRANSIENT_EXC and isinstance(error, _OPENAI_TRANSIENT_EXC):
            return True

        # Message-based fallback (provider-independent / SDK missing / wrapped).
        msg = str(error).lower()
        transient_indicators = [
            "timeout", "timed out", "connection", "connection reset",
            "connection aborted", "connection lost", "temporarily unavailable",
            "rate limit", "too many requests", "429",
            "internal server error", "bad gateway", "service unavailable",
            "gateway timeout", "500", "502", "503", "504",
            "overloaded", "server error",
        ]
        if any(ind in msg for ind in transient_indicators):
            return True

        # Unknown/unclassified errors are treated as NON-transient so genuine
        # programming errors surface via the caller's except block rather than
        # being retried and masked. Only explicitly recognised transient
        # indicators (above) are retried.
        return False

    def _is_token_ceiling_reached(self) -> bool:
        """Return True if the per-turn token ceiling has been reached/exceeded.

        Only provider-reported exact tokens count toward the ceiling. Calls
        where the provider does not return usage metadata contribute 0 and
        cannot trigger the ceiling — this is the deterministic behaviour
        required by BIP 10.4 for providers that omit token usage.

        Returns:
            True if a ceiling is configured (non-None) and the cumulative
            exact total_tokens >= ceiling.
        """
        ceiling = self.max_tokens_per_turn
        if ceiling is None:
            return False
        return self._token_telemetry["total_tokens"] >= ceiling

    def _extract_bbox_constraints(self, user_message: str) -> Optional[Dict[str, float]]:
        """Extract explicit bounding-box constraints from user message.

        Looks for patterns like:
        - "fit within 100 x 50 x 20 mm"
        - "within 100mm x 50mm x 20mm"
        - "must fit within 100 x 50 x 20"
        - "keep within 100x50x20"

        Returns:
            Dict with max_x, max_y, max_z in mm, or None if no constraints found.
        """
        import re
        text = (user_message or "").lower()

        # Patterns for bounding box constraints
        # "within 100 x 50 x 20 mm" or "within 100mm x 50mm x 20mm" or "100x50x20"
        patterns = [
            r'(?:fit\s+)?within\s+(\d+(?:\.\d+)?)\s*(?:mm|millimeter)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(?:mm|millimeter)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(?:mm|millimeter)?',
            r'(?:fit\s+)?within\s+(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)',
            r'(\d+(?:\.\d+)?)\s*(?:mm|millimeter)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(?:mm|millimeter)?\s*[x×]\s*(\d+(?:\.\d+)?)\s*(?:mm|millimeter)?',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    x = float(match.group(1))
                    y = float(match.group(2))
                    z = float(match.group(3))
                    return {"max_x": x, "max_y": y, "max_z": z}
                except (ValueError, IndexError):
                    continue

        return None

    def _generate_with_retry(
        self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
    ) -> Any:
        """Call the provider with bounded retry for transient transport errors.

        Returns the provider response on success, or ``None`` if all attempts
        were exhausted by transient errors. Non-transient provider errors
        propagate unchanged (they are handled by the caller's ``except`` block).
        """
        last_error: Optional[Exception] = None
        for attempt in range(self.MAX_LLM_RETRIES + 1):
            try:
                return self.provider.generate_with_tools(
                    messages=messages, tools=tools
                )
            except Exception as e:
                if not self._is_transient_llm_error(e):
                    raise
                last_error = e
                if attempt < self.MAX_LLM_RETRIES:
                    backoff = self.LLM_RETRY_BACKOFF * (2 ** attempt)
                    print(
                        f"[Retry] LLM call attempt {attempt + 1} failed with "
                        f"transient error: {e}. Retrying in {backoff:.1f}s...")
                    try:
                        time.sleep(backoff)
                    except Exception:
                        pass
                    continue
                # Exhausted retries.
                print(
                    f"\033[91m[ERROR] LLM call failed after "
                    f"{self.MAX_LLM_RETRIES + 1} attempts: {last_error}\033[0m")
                return None
        return None

    def _invoke_llm(
        self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]
    ) -> Any:
        """Invoke the LLM provider for one ReAct step (A5.3).

        Emits the invocation diagnostic and delegates to
        ``_generate_with_retry`` so transient provider/transport failures
        (connection drops, timeouts, rate limits, HTTP 5xx) are retried with
        bounded backoff. Returns the provider response unchanged, or ``None``
        when all retries were exhausted by transient errors. Non-transient
        provider errors propagate unchanged to the caller's ``except`` block.
        """
        print(f"[Agent] Calling LLM with {len(tools)} tools available...")
        return self._generate_with_retry(messages, tools)

    def _execute_tool_calls(
        self, tool_calls: List[Any], react_step: int
    ) -> Any:
        """Execute the LLM's tool calls through the adapter (A5.5).

        Extracted verbatim from ``handle_message``: defensive argument parsing,
        transient retry with the mutation gate, structured timeout/error
        detection, requested-vs-achieved arg tracking, and operation-registry
        bookkeeping. Verification, state refresh and topology refresh remain in
        ``handle_message`` (A5.6). Mutates ``self._operation_counter``,
        ``self._operation_registry``, ``self._mutation_gate``,
        ``self._last_failed_args``, ``self.router`` and ``self.design_state`` as
        before. Yields one :class:`ToolExecutionResult` per tool call, aligned
        1:1 with ``tool_calls`` (A6.3 named-result contract).
        """

        for tc in tool_calls:
            name = tc.function.name
            # Record this session's tool usage (attempted, even on failure).
            self._session_tools_for_step.append(name)

            # --- Defensively parse tool arguments (recoverable error) ---
            try:
                args = json.loads(tc.function.arguments)
                if not isinstance(args, dict):
                    args = {}
            except (json.JSONDecodeError, TypeError) as e:
                print(
                    f"\033[91m[ERROR] Step {react_step}: Tool '{name}' malformed "
                    f"arguments: {e}\033[0m")
                malformed_result = json.dumps({
                    "status": "error",
                    "tool": name,
                    "error_type": type(e).__name__,
                    "error": f"Malformed tool arguments (invalid JSON): {e}",
                    "arguments": {},
                    "transient": False,
                    "retries": 0,
                }, default=str)
                self._record_tool_outcome(
                    name, {}, False,
                    error=f"Malformed tool arguments (invalid JSON): {e}",
                    out=None)
                if self._capture_trace:
                    self._trace.append({
                        "step": react_step,
                        "tool": name,
                        "arguments": {},
                        "result": None,
                        "success": False,
                        "error": f"Malformed tool arguments (invalid JSON): {e}",
                        "attempt": 1,
                    })
                # Hand the structured error to the caller so it is appended to
                # the caller's ``results`` (aligned 1:1 with tool_calls) and
                # skipped by verification, exactly as before.
                yield ToolExecutionResult(
                    name=name, malformed=True,
                    malformed_result=malformed_result)
                continue

            # Target object id for requested-vs-achieved tracking (BIP 4.3.2).
            target = args.get("target_id") or args.get("target") or \
                args.get("object") or args.get("object_name")

            # BIP 4.3.7 / 5.5: Auto-inject the topology version AT WHICH the
            # edge references were emitted (not the object's current version).
            # The bridge compares this against the object's live topology; if
            # the object changed since the references were captured, the
            # comparison fails and the stale reference is rejected.
            #
            # BIP 5.5: using the recorded REFERENCE version (rather than the
            # object's current version) is what makes a reused OLD edge_ref
            # actually get rejected instead of being validated against the
            # fresher object topology. If no reference version is recorded
            # (the "0"/empty unknown sentinel), inject nothing so no stale
            # reference is implicitly trusted.
            if name in ("fillet", "chamfer") and args.get("target_id") is not None:
                ref_version = self.design_state.get_recorded_reference_version(
                    args["target_id"], "edge")
                if ref_version not in (None, "", "0"):
                    args["topology_version"] = ref_version
                else:
                    args.pop("topology_version", None)

            print(
                f"[Execution] Step {react_step}: Tool '{name}' with args: {args}")

            # BIP 7.0: Generate unique operation ID for this execution attempt
            self._operation_counter += 1
            operation_id = f"op_{self._operation_counter}_{name}_{react_step}"

            # Check if this is a mutation tool that requires serialization
            mutation_tools = {"box", "cylinder", "boolean", "hole", "fillet", "chamfer",
                              "shell", "edit_feature", "pattern_linear", "pattern_circular",
                              "delete_feature", "mate", "sketch", "extrude"}
            is_mutation = name in mutation_tools

            # Create operation record in registry
            self._operation_registry.create(
                operation_id, name, dict(args), target, react_step)

            # --- Execute with transient retry; retain non-transient errors ---
            out = None
            error = None
            success = False
            attempts = 0
            transient = False
            gate_acquired = False

            for attempt in range(self.MAX_RETRIES + 1):
                attempts = attempt + 1
                try:
                    # BIP 7.0: Acquire mutation gate for mutation tools
                    if is_mutation and not gate_acquired:
                        acquired, error_type = self._mutation_gate.acquire(
                            operation_id)
                        if not acquired:
                            if error_type == "unresolved_operation":
                                # Blocked by unresolved operation - return structured error
                                # Get the unresolved operation ID for the error message
                                unresolved_ops = self._operation_registry.get_unresolved()
                                unresolved_id = next(
                                    iter(unresolved_ops.keys())) if unresolved_ops else "unknown"
                                error = RuntimeError(
                                    f"Cannot execute '{name}': mutation operation {unresolved_id} is unresolved. "
                                    f"Please wait for reconciliation or retry later.")
                            elif error_type == "mutation_gate_timeout":
                                # Physical lock timeout - return structured error
                                error = RuntimeError(
                                    f"Cannot execute '{name}': mutation gate timeout waiting for physical lock. "
                                    f"Another mutation is currently executing.")
                            else:
                                error = RuntimeError(
                                    f"Cannot execute '{name}': mutation gate acquisition failed.")
                            error_type = error_type or "mutation_gate_error"
                            transient = False
                            success = False
                            # Record trace for blocked mutation
                            if self._capture_trace:
                                self._trace.append({
                                    "step": react_step,
                                    "tool": name,
                                    "arguments": args,
                                    "result": str(error),
                                    "success": False,
                                    "error": str(error),
                                    "attempt": attempts,
                                    "transient": False,
                                })
                            break
                        gate_acquired = True

                    # Pass operation_id to adapter for tracking (adapter may ignore if not supported)
                    out = self.adapter.execute_command(
                        name, _operation_id=operation_id, **args)
                    # BIP 6.8: Check if the returned result is a structured error (e.g., timeout).
                    # Both FreeCAD timeout (BIP 6.6) and MCP timeout (BIP 6.7) return JSON
                    # with error_type. This must NOT be treated as success.
                    if isinstance(out, str):
                        try:
                            parsed = json.loads(out)
                            if isinstance(parsed, dict) and parsed.get("success") is False:
                                error = RuntimeError(parsed.get(
                                    "error", "Tool execution failed"))
                                # Detect timeout errors specifically
                                error_type = parsed.get("error_type", "")
                                if "timeout" in error_type.lower():
                                    transient = True  # timeout is transient for retry purposes
                                    # BIP 7.0: Mark operation as unresolved (timed out)
                                    self._operation_registry.timeout(
                                        operation_id, error_type, parsed.get("error", "Timeout"))
                                else:
                                    transient = self._is_transient_error(
                                        error)
                                success = False
                                break
                        except (json.JSONDecodeError, TypeError):
                            # Not JSON or not a structured error - treat as success
                            pass
                    success = True
                    break
                except (ConnectionError, OSError) as e:
                    error = e
                    transient = True
                    attempts = attempt + 1
                    # Release mutation gate on retry
                    if is_mutation and gate_acquired:
                        self._mutation_gate.release(operation_id)
                        gate_acquired = False
                    if attempt < self.MAX_RETRIES:
                        print(
                            f"[Retry] Step {react_step}: Tool '{name}' attempt {attempt + 1} failed with transient error: {e}. Retrying...")
                        continue
                    # Exhausted transient retries -> recoverable failure.
                    break
                except RuntimeError as e:
                    error = e
                    transient = self._is_transient_error(e)
                    attempts = attempt + 1
                    # Release mutation gate on retry
                    if is_mutation and gate_acquired and transient:
                        self._mutation_gate.release(operation_id)
                        gate_acquired = False
                    if transient and attempt < self.MAX_RETRIES:
                        print(
                            f"[Retry] Step {react_step}: Tool '{name}' attempt {attempt + 1} failed with transient error: {e}. Retrying...")
                        continue
                    # Non-transient failure (or exhausted transient retries)
                    # -> capture for the LLM, do NOT abort the turn.
                    break

            # BIP 4.3.2: Track requested-vs-achieved integrity.
            # If a tool fails non-transiently, remember the requested args.
            # If it succeeds on a subsequent attempt with different args,
            # preserve both so the LLM can see the mismatch.
            tool_key = (name, target)
            requested_args_for_recording = None
            if not success and not transient:
                # Non-transient failure: store the requested args for this tool/target.
                self._last_failed_args[tool_key] = dict(args)
                # BIP 7.0: Mark operation as failed in registry
                err_type = type(
                    error).__name__ if error else "RuntimeError"
                err_msg = str(error) if error else "Unknown error"
                self._operation_registry.fail(
                    operation_id, err_type, err_msg)
                # Release mutation gate on failure
                if is_mutation and gate_acquired:
                    self._mutation_gate.release(operation_id)
            elif success and tool_key in self._last_failed_args:
                # Success after a prior non-transient failure on same tool/target.
                # Check if achieved args differ from originally requested.
                failed_args = self._last_failed_args.pop(tool_key)
                if failed_args != args:
                    requested_args_for_recording = failed_args

            yield ToolExecutionResult(
                name=name,
                args=args,
                target=target,
                out=out,
                error=error,
                success=success,
                attempts=attempts,
                transient=transient,
                operation_id=operation_id,
                is_mutation=is_mutation,
                gate_acquired=gate_acquired,
                requested_args_for_recording=requested_args_for_recording,
            )

    def _evaluate_termination(
        self,
        response: Any,
        step: int,
        session_tools: List[Any],
        turn_start: Optional[float] = None,
    ):
        """Evaluate whether the ReAct loop should terminate, continue, or
        proceed to tool execution (A5.4, unified in A6.4).

        Returns a tuple ``(outcome, value)``:
          - ``("return", payload)``  -> the caller must ``return payload``
          - ``("continue", None)``   -> the caller must ``continue`` the loop
          - ``("tools", None)``      -> the caller must proceed to tool execution

        Call phases (preserving the original per-step ordering exactly):
          - ``response is None`` -> pre-step check. Evaluates the BIP 10.4b
            overall turn-deadline FIRST, exactly where it used to run (before
            the step's tools/state/LLM work). If no deadline tripped, returns
            ``("tools", None)`` so the caller proceeds with the step.
          - ``response`` present -> post-LLM check: token ceiling, then the
            no-tool-calls/completion and empty-response branches.

        No new Enum/state machine is introduced: the plain-string outcome
        mirrors the existing ``termination_reason`` string field, which is now
        consistently sourced from the ``*_TERMINATION_REASON`` constants.
        """
        # BIP 10.4b: overall wall-clock turn-deadline enforcement (pre-step).
        # Does NOT kill an in-flight CAD mutation thread (that would falsely
        # imply the operation stopped); any unresolved mutation stays tracked
        # by the OperationRegistry/barrier. Runs before the step's tools/state/
        # LLM work, identical to its original location.
        if (response is None
                and turn_start is not None
                and self.MAX_TURN_SECONDS is not None
                and (time.time() - turn_start) > self.MAX_TURN_SECONDS):
            fail_msg = (
                f"Operation stopped: overall turn deadline "
                f"({self.MAX_TURN_SECONDS:.0f}s) reached at step "
                f"{step + 1}. The CAD state may reflect completed earlier "
                f"steps; any unresolved mutation remains tracked and must "
                f"be reconciled before further mutation.")
            print(f"\033[91m[ERROR] {fail_msg}\033[0m")
            self.history.append({"role": "assistant", "content": fail_msg})
            self.conversation.add_assistant(fail_msg)
            if self._capture_trace:
                self._trace.append({
                    "step": step + 1,
                    "type": "turn_deadline_reached",
                    "message": fail_msg,
                    "termination_reason": self.TURN_DEADLINE_TERMINATION_REASON,
                })
            return ("return", (fail_msg, session_tools))

        # BIP 10.4: Per-turn total-token ceiling enforcement.
        # If this LLM call caused the cumulative provider-reported total to
        # reach/exceed the configured ceiling, stop further LLM reasoning
        # for this turn immediately — do NOT begin another ReAct step.
        # This is reported as a structured failure that is distinct from:
        #   - MAX_STEPS exhaustion (different termination_reason/message)
        #   - context-budget overflow (that trims context, never aborts)
        #   - provider/API failure (different message/type)
        #   - tool execution failure (different message/type)
        if self._is_token_ceiling_reached():
            self._token_telemetry["ceiling_reached"] = True
            fail_msg = (
                "Operation stopped: per-turn token ceiling "
                f"({self.max_tokens_per_turn} tokens) reached after "
                f"{self._token_telemetry['llm_calls']} LLM call(s) "
                f"({self._token_telemetry['total_tokens']} tokens "
                "consumed). Further reasoning for this turn was halted to "
                "bound cost; the CAD state was not modified by this step."
            )
            print(f"\033[91m[ERROR] {fail_msg}\033[0m")
            self.history.append({"role": "assistant", "content": fail_msg})
            self.conversation.add_assistant(fail_msg)
            if self._capture_trace:
                self._trace.append({
                    "step": step + 1,
                    "type": "token_ceiling_reached",
                    "message": fail_msg,
                    "termination_reason": self.TOKEN_CEILING_TERMINATION_REASON,
                    "token_telemetry": self._token_telemetry.copy(),
                })
            return ("return", (fail_msg, session_tools))

        # 7. If LLM returns plain text (NO tool calls):
        #    - meaningful content -> normal completion.
        #    - empty/None content -> do NOT claim "Done."; keep going so the
        #      LLM can produce a real response (bounded by MAX_STEPS).
        if response is None:
            # Pre-step phase with no deadline tripped: proceed with the step.
            return ("tools", None)

        if not getattr(response, "tool_calls", None):
            raw_content = getattr(response, "content", None)
            reply = raw_content.strip() if isinstance(
                raw_content, str) else None
            if reply:
                # Safely encode for Windows console (cp1252).
                safe_reply = reply.encode(
                    'ascii', 'replace').decode('ascii')
                print(
                    f"[Agent] Finished reasoning (no tool calls). Final response: {safe_reply}")
                # Append final response to long-term history
                self.history.append(
                    {"role": "assistant", "content": reply})
                self.conversation.add_assistant(reply)
                if self._capture_trace:
                    self._trace.append({
                        "step": step + 1,
                        "type": "completion",
                        "reply": reply,
                        "termination_reason": self.NO_TOOL_CALLS_TERMINATION_REASON,
                        "token_telemetry": self._token_telemetry.copy(),
                    })
                return ("return", (reply, session_tools))
            # Empty response: do not fabricate success. Continue the ReAct
            # loop; if steps remain the LLM gets another chance.
            print(
                f"[Agent] WARNING: empty LLM response at step {step+1} "
                "(no tool calls, no meaningful content). Continuing loop.")
            if self._capture_trace:
                self._trace.append({
                    "step": step + 1,
                    "type": "empty_response",
                    "termination_reason": self.EMPTY_RESPONSE_TERMINATION_REASON,
                    "token_telemetry": self._token_telemetry.copy(),
                })
            return ("continue", None)

        return ("tools", None)

    def _max_steps_termination(self, session_tools: List[Any]):
        """Max-steps exhaustion termination (A6.4).

        Extracted verbatim from the tail of ``handle_message``'s ReAct loop so
        all terminal reasons share the same termination boundary. Returns the
        structured ``(fail_msg, session_tools)`` payload the caller returns.
        """
        fail_msg = f"Operation incomplete: maximum reasoning steps ({self.MAX_STEPS}) reached."
        print(f"\033[91m[ERROR] {fail_msg}\033[0m")
        # Append failure message to long-term history
        self.history.append({"role": "assistant", "content": fail_msg})
        if self._capture_trace:
            self._trace.append({
                "step": self.MAX_STEPS,
                "type": "max_steps_exhausted",
                "message": fail_msg,
                "termination_reason": self.MAX_STEPS_TERMINATION_REASON,
            })
        return fail_msg, session_tools

    def _verify_tool_outcome(
        self,
        name: Any,
        args: Dict[str, Any],
        operation_id: str,
        is_mutation: bool,
        gate_acquired: bool,
        success: bool,
        error: Any,
        out: Any,
    ):
        """Post-tool mandatory verification (A5.6).

        Extracted verbatim from ``handle_message``: BIP 8.1 volume-reduction
        verification, BIP 8.2 face-count verification, BIP 8.3 bounding-box
        verification, BIP 8.4 mate verification, and BIP 11.1 parameter
        verification. A failed verification converts ``success`` to ``False``,
        sets ``error``, marks the operation failed in the registry, and releases
        the mutation gate — exactly as before. Returns the (possibly updated)
        ``(success, error, operation_id, is_mutation, gate_acquired)``.
        """
        if success:
            # BIP 8.x: Mandatory post-mutation verification runs BEFORE the result
            # is visible to the LLM. This prevents the LLM from terminating early
            # before mandatory verification completes.

            # BIP 8.1: Operation-specific geometry verification
            # Verify volume reduction for boolean subtract and hole operations
            if name == "boolean" and args.get("mode") == "subtract":
                # Verify that boolean subtract actually removed material
                base_id = args.get("target_id")
                result_id = args.get("id")
                if base_id and result_id:
                    try:
                        # Get mass properties of the base object (need to query before state changes)
                        # Since the base object is now hidden, we need to get it from the result
                        # The result object (Part::Cut) should have the reduced volume
                        base_mass = self.adapter.execute_command(
                            "get_mass_properties", object_name=base_id)
                        result_mass = self.adapter.execute_command(
                            "get_mass_properties", object_name=result_id)
                        ok, reason = GeometryVerifier.verify_volume_reduction(
                            base_mass, result_mass)
                        if not ok:
                            error_msg = f"Verification failed: {reason}"
                            print(
                                f"\033[91m[VERIFY] {error_msg}\033[0m")
                            # Convert success to failure for the agent recovery loop
                            success = False
                            error = RuntimeError(error_msg)
                            # Mark operation as failed
                            self._operation_registry.fail(
                                operation_id, "GeometryVerificationError", error_msg)
                            # Release mutation gate since we're treating this as failure
                            if is_mutation and gate_acquired:
                                self._mutation_gate.release(
                                    operation_id)
                    except Exception as e:
                        # Verification error - treat as verification failure
                        error_msg = f"Verification exception: {e}"
                        print(
                            f"\033[91m[VERIFY] {error_msg}\033[0m")
                        success = False
                        error = RuntimeError(error_msg)
                        # Mark operation as failed
                        self._operation_registry.fail(
                            operation_id, "GeometryVerificationError", error_msg)
                        # Release mutation gate
                        if is_mutation and gate_acquired:
                            self._mutation_gate.release(
                                operation_id)

            elif name == "hole":
                # Verify that hole operation actually removed material
                target_id = args.get("target_id")
                result_id = args.get("id")
                if target_id and result_id:
                    try:
                        target_mass = self.adapter.execute_command(
                            "get_mass_properties", object_name=target_id)
                        result_mass = self.adapter.execute_command(
                            "get_mass_properties", object_name=result_id)
                        ok, reason = GeometryVerifier.verify_volume_reduction(
                            target_mass, result_mass)
                        if not ok:
                            error_msg = f"Verification failed: {reason}"
                            print(
                                f"\033[91m[VERIFY] {error_msg}\033[0m")
                            # Convert success to failure for the agent recovery loop
                            success = False
                            error = RuntimeError(error_msg)
                            # Mark operation as failed
                            self._operation_registry.fail(
                                operation_id, "GeometryVerificationError", error_msg)
                            # Release mutation gate since we're treating this as failure
                            if is_mutation and gate_acquired:
                                self._mutation_gate.release(
                                    operation_id)
                    except Exception as e:
                        # Verification error - treat as verification failure
                        error_msg = f"Verification exception: {e}"
                        print(
                            f"\033[91m[VERIFY] {error_msg}\033[0m")
                        success = False
                        error = RuntimeError(error_msg)
                        # Mark operation as failed
                        self._operation_registry.fail(
                            operation_id, "GeometryVerificationError", error_msg)
                        # Release mutation gate
                        if is_mutation and gate_acquired:
                            self._mutation_gate.release(
                                operation_id)

            # BIP 8.2: Operation-specific face-count verification
            # Verify face count increase for fillet, chamfer, and pattern operations
            elif name in ("fillet", "chamfer", "pattern_linear", "pattern_circular"):
                target_id = args.get("target_id")
                result_id = args.get("id")
                if target_id and result_id:
                    try:
                        # Get face count before and after operation
                        # Note: we need the base object's faces. The target_id may be the base object
                        # or the result object depending on the operation.
                        # get_faces returns {"faces": [...], "topology_version": "..."}
                        # We need to extract the faces array for verification.
                        base_faces_raw = self.adapter.execute_command(
                            "get_faces", object_name=target_id)
                        result_faces_raw = self.adapter.execute_command(
                            "get_faces", object_name=result_id)
                        # Parse and extract faces array
                        try:
                            base_faces_parsed = json.loads(
                                base_faces_raw)
                            base_faces = json.dumps(base_faces_parsed.get("faces", []) if isinstance(
                                base_faces_parsed, dict) else base_faces_parsed)
                        except (json.JSONDecodeError, TypeError):
                            base_faces = "[]"
                        try:
                            result_faces_parsed = json.loads(
                                result_faces_raw)
                            result_faces = json.dumps(result_faces_parsed.get("faces", []) if isinstance(
                                result_faces_parsed, dict) else result_faces_parsed)
                        except (json.JSONDecodeError, TypeError):
                            result_faces = "[]"
                        ok, reason = GeometryVerifier.verify_face_count_increase(
                            base_faces, result_faces)
                        if not ok:
                            error_msg = f"Verification failed: {reason}"
                            print(
                                f"\033[91m[VERIFY] {error_msg}\033[0m")
                            # Convert success to failure for the agent recovery loop
                            success = False
                            error = RuntimeError(error_msg)
                            # Mark operation as failed
                            self._operation_registry.fail(
                                operation_id, "GeometryVerificationError", error_msg)
                            # Release mutation gate since we're treating this as failure
                            if is_mutation and gate_acquired:
                                self._mutation_gate.release(
                                    operation_id)
                    except Exception as e:
                        # Verification error - treat as verification failure
                        error_msg = f"Verification exception: {e}"
                        print(
                            f"\033[91m[VERIFY] {error_msg}\033[0m")
                        success = False
                        error = RuntimeError(error_msg)
                        # Mark operation as failed
                        self._operation_registry.fail(
                            operation_id, "GeometryVerificationError", error_msg)
                        # Release mutation gate
                        if is_mutation and gate_acquired:
                            self._mutation_gate.release(
                                operation_id)

            # BIP 8.3: Bounding-box verification
            # Verify geometry stays within explicit user-specified bounds
            if self._bbox_constraints and success:
                result_id = args.get("id")
                if result_id:
                    try:
                        mass_json = self.adapter.execute_command(
                            "get_mass_properties", object_name=result_id)
                        ok = GeometryVerifier.verify_within_bounding_box(
                            mass_json,
                            self._bbox_constraints.get(
                                "max_x", float('inf')),
                            self._bbox_constraints.get(
                                "max_y", float('inf')),
                            self._bbox_constraints.get(
                                "max_z", float('inf'))
                        )
                        if not ok:
                            error_msg = (
                                f"Verification failed: Geometry exceeds bounding-box constraints "
                                f"X={self._bbox_constraints.get('max_x')}mm "
                                f"Y={self._bbox_constraints.get('max_y')}mm "
                                f"Z={self._bbox_constraints.get('max_z')}mm"
                            )
                            print(
                                f"\033[91m[VERIFY] {error_msg}\033[0m")
                            # Convert success to failure for the agent recovery loop
                            success = False
                            error = RuntimeError(error_msg)
                            # Mark operation as failed
                            self._operation_registry.fail(
                                operation_id, "GeometryVerificationError", error_msg)
                            # Release mutation gate since we're treating this as failure
                            if is_mutation and gate_acquired:
                                self._mutation_gate.release(
                                    operation_id)
                    except Exception as e:
                        # Verification error - treat as verification failure
                        error_msg = f"Verification exception: {e}"
                        print(
                            f"\033[91m[VERIFY] {error_msg}\033[0m")
                        success = False
                        error = RuntimeError(error_msg)
                        # Mark operation as failed
                        self._operation_registry.fail(
                            operation_id, "GeometryVerificationError", error_msg)
                        # Release mutation gate
                        if is_mutation and gate_acquired:
                            self._mutation_gate.release(
                                operation_id)

            # BIP 8.4: Mate verification.
            # Verify the geometric relationship produced by mate
            # operations. The verifier must receive the SPECIFIC
            # referenced face/edge dicts, not the raw get_faces/get_edges
            # wrapper payload ({"faces": [...], "topology_version": ...}).
            if name == "mate" and success:
                mate_type = args.get("mate_type", "").strip().lower()
                moving_target = args.get("moving_target")
                moving_ref = args.get("moving_ref")
                fixed_target = args.get("fixed_target")
                fixed_ref = args.get("fixed_ref")

                def _pick_ref(wrapper_raw, refs_key, ref_id_key, ref_id):
                    """Extract one referenced face/edge dict from a
                    get_faces/get_edges wrapper payload. Returns None
                    when the specific reference cannot be resolved."""
                    try:
                        parsed = json.loads(wrapper_raw) if isinstance(
                            wrapper_raw, str) else wrapper_raw
                    except (json.JSONDecodeError, TypeError):
                        return None
                    entries = None
                    if isinstance(parsed, dict) and isinstance(
                            parsed.get(refs_key), list):
                        entries = parsed[refs_key]
                    elif isinstance(parsed, list):
                        entries = parsed
                    elif isinstance(parsed, dict) and ref_id_key in parsed:
                        return parsed  # already a single ref dict
                    if not entries:
                        return None
                    ref_s = str(ref_id)
                    for e in entries:
                        if isinstance(e, dict) and str(
                                e.get(ref_id_key)) == ref_s:
                            return e
                    return None

                if moving_target and moving_ref and fixed_target and fixed_ref:
                    try:
                        if mate_type == "coincident":
                            # Get face data for both mated faces
                            moving_face_raw = self.adapter.execute_command(
                                "get_faces", object_name=moving_target)
                            fixed_face_raw = self.adapter.execute_command(
                                "get_faces", object_name=fixed_target)
                            m = _pick_ref(moving_face_raw, "faces",
                                          "face_id", moving_ref)
                            f = _pick_ref(fixed_face_raw, "faces",
                                          "face_id", fixed_ref)
                            if m is None or f is None:
                                ok, reason = False, (
                                    f"mated face reference not found in "
                                    f"topology (moving={moving_ref!r}, "
                                    f"fixed={fixed_ref!r})")
                            else:
                                ok, reason = GeometryVerifier.verify_mate_coincident(
                                    json.dumps(m), json.dumps(f))
                        elif mate_type == "concentric":
                            # Get edge data for both mated edges
                            moving_edge_raw = self.adapter.execute_command(
                                "get_edges", object_name=moving_target)
                            fixed_edge_raw = self.adapter.execute_command(
                                "get_edges", object_name=fixed_target)
                            m = _pick_ref(moving_edge_raw, "edges",
                                          "edge_id", moving_ref)
                            f = _pick_ref(fixed_edge_raw, "edges",
                                          "edge_id", fixed_ref)
                            if m is None or f is None:
                                ok, reason = False, (
                                    f"mated edge reference not found in "
                                    f"topology (moving={moving_ref!r}, "
                                    f"fixed={fixed_ref!r})")
                            else:
                                ok, reason = GeometryVerifier.verify_mate_concentric(
                                    json.dumps(m), json.dumps(f))
                        else:
                            ok, reason = True, f"mate type '{mate_type}' not verified (unsupported)"

                        if not ok:
                            error_msg = f"Verification failed: {reason}"
                            print(
                                f"\033[91m[VERIFY] {error_msg}\033[0m")
                            # Convert success to failure for the agent recovery loop
                            success = False
                            error = RuntimeError(error_msg)
                            # Mark operation as failed
                            self._operation_registry.fail(
                                operation_id, "GeometryVerificationError", error_msg)
                            # Release mutation gate since we're treating this as failure
                            if is_mutation and gate_acquired:
                                self._mutation_gate.release(
                                    operation_id)
                    except Exception as e:
                        # Verification error - treat as verification failure
                        error_msg = f"Verification exception: {e}"
                        print(
                            f"\033[91m[VERIFY] {error_msg}\033[0m")
                        success = False
                        error = RuntimeError(error_msg)
                        # Mark operation as failed
                        self._operation_registry.fail(
                            operation_id, "GeometryVerificationError", error_msg)
                        # Release mutation gate
                        if is_mutation and gate_acquired:
                            self._mutation_gate.release(
                                operation_id)

            # BIP 11.1: Parameter-level verification
            # Verify that requested parameters match actual CAD result.
            # This runs after geometric verification passes.
            if success and name in ("box", "cylinder", "hole", "fillet", "chamfer",
                                    "pattern_linear", "pattern_circular", "boolean"):
                try:
                    param_ok, param_reason = ParameterVerifier.verify_operation(
                        tool=name, args=args, adapter=self.adapter,
                        result_id=args.get("id"), target_id=args.get("target_id"))
                    if param_ok == VerificationResult.FAIL:
                        error_msg = f"Parameter verification failed: {param_reason}"
                        print(f"\033[91m[VERIFY] {error_msg}\033[0m")
                        success = False
                        error = RuntimeError(error_msg)
                        self._operation_registry.fail(
                            operation_id, "ParameterVerificationError", error_msg)
                        if is_mutation and gate_acquired:
                            self._mutation_gate.release(operation_id)
                    elif param_ok == VerificationResult.UNKNOWN:
                        print(
                            f"[VERIFY] Parameter verification UNKNOWN: {param_reason}")
                except Exception as e:
                    # Verification error - treat as verification failure
                    error_msg = f"Verification exception: {e}"
                    print(
                        f"\033[91m[VERIFY] {error_msg}\033[0m")
                    success = False
                    error = RuntimeError(error_msg)
                    self._operation_registry.fail(
                        operation_id, "ParameterVerificationError", error_msg)
                    if is_mutation and gate_acquired:
                        self._mutation_gate.release(operation_id)

        return (success, error, operation_id, is_mutation, gate_acquired)

    def _refresh_state_and_verify_geometry(
        self, response: Any, step: int, scratchpad: List[Any]
    ):
        """Post-step state refresh + geometry validation (A5.6).

        Extracted verbatim from ``handle_message``: BIP 4.3.2 / 6.9 get_state()
        refresh, DesignState sync, BIP 7.0 pending-operation reconciliation,
        operation-registry cleanup, ``check_geometry`` validation, and the
        structured uncertainty/geometry warnings injected into the scratchpad.
        ``scratchpad`` must be a reference to the caller's list (mutated in
        place). Returns ``(response, step)`` unchanged for caller convenience.
        """
        # 10a. Runtime geometry verification + IMMEDIATE STATE SYNC (BIP 4.3.2):
        # After EVERY successful tool execution, refresh DesignState from the
        # live CAD state BEFORE the next ReAct iteration. This ensures the
        # compiled context for the next step reflects the actual CAD state.

        # BIP 6.9: Also refresh state after a timeout to detect late completion
        # of the underlying operation. This provides safe reconciliation.
        needs_state_refresh = True
        # Only skip if all operations in this step were query-only (get_state, get_edges, etc.)
        mutation_tools = {"box", "cylinder", "boolean", "hole", "fillet", "chamfer",
                          "shell", "edit_feature", "pattern_linear", "pattern_circular",
                          "delete_feature", "mate", "sketch", "extrude"}
        if all(tc.function.name not in mutation_tools for tc in response.tool_calls):
            needs_state_refresh = False

        state_retrieval_failed = False
        if needs_state_refresh:
            try:
                # A4.3: If we already got state from combined endpoint in topology refresh,
                # we may have already updated DesignState. But we still need to call
                # get_state() here for late completion reconciliation and registry cleanup.
                new_state_json = self.adapter.get_state()
                new_state = json.loads(new_state_json)
                # Immediately synchronize DesignState with the live CAD state.
                self._update_design_state(new_state_json)
                # BIP 7.0: Check if any unresolved operation's target now exists (late completion)
                self._check_pending_operations_against_state(new_state)
                # Periodic cleanup of old reconciled operations
                self._operation_registry.cleanup_reconciled(max_age=300.0)
            except Exception as e:
                print(
                    f"[Agent] Warning: Failed to get state for verification/sync: {e}")
                # Mark state as unavailable but preserve last known-good objects.
                self.design_state.mark_state_unavailable()
                state_retrieval_failed = True
                new_state = []
        else:
            new_state = []

        errors = check_geometry(new_state)

        # If state retrieval failed, inject a structured uncertainty notice.
        # Do NOT treat unavailable verification as valid.
        if state_retrieval_failed:
            uncertainty_warning = (
                "WARNING: Geometry verification unavailable after last operation "
                "(CAD state retrieval failed). The authoritative CAD state could "
                "not be queried. You must verify the result manually or retry."
            )
            print(f"\033[93m[VERIFY] {uncertainty_warning}\033[0m")
            scratchpad.append({
                "role": "system",
                "content": uncertainty_warning,
            })
            if self._capture_trace:
                self._trace.append({
                    "step": step + 1,
                    "type": "geometry_verification_unavailable",
                    "reason": "state_retrieval_failed",
                })

        if errors:
            warning = (
                "WARNING: Geometry validation failed after last operation: "
                f"{errors}. You must use edit_feature or delete_feature to "
                "fix this before proceeding."
            )
            print(f"\033[93m[VERIFY] {warning}\033[0m")
            # Inject the warning as context for the LLM's next reasoning step.
            scratchpad.append({
                "role": "system",
                "content": warning,
            })
            if self._capture_trace:
                self._trace.append({
                    "step": step + 1,
                    "type": "geometry_warning",
                    "errors": errors,
                })

    # ------------------------------------------------------------------ #
    # Context Engine integration (BIP 4.2)
    # ------------------------------------------------------------------ #
    def _update_design_state(self, state_json: str) -> None:
        """Refresh DesignState from the adapter's current CAD state string.

        BIP 6.3 — state-integrity guard: when a live state retrieval fails, the
        ReAct loop substitutes a synthetic empty ``"[]"`` so routing can still
        proceed. That sentinel must NOT be ingested as an authoritative empty
        document: doing so clears the preserved last-known-good objects and
        flips ``state_available`` back to True, destroying the very "preserve
        last known-good state" invariant the retrieval-error path relies on.

        A JSON string that parses to an empty object list while the caller has
        signalled unavailability is rejected here. We only treat an empty list
        as authoritative when the state is currently marked available (i.e. an
        adapter genuinely reported an empty document).
        """
        try:
            parsed = json.loads(state_json) if isinstance(
                state_json, str) else state_json
        except (json.JSONDecodeError, TypeError):
            # Malformed state: let update_from_cad_state flag it unavailable
            # (it preserves objects) rather than raising here.
            self.design_state.update_from_cad_state(state_json)
            return

        is_empty_list = parsed == []
        if is_empty_list and not self.design_state.state_available:
            # Synthetic fallback after a retrieval failure: do not overwrite
            # preserved state. Re-assert unavailability/staleness instead.
            self.design_state.mark_state_unavailable()
            return

        self.design_state.update_from_cad_state(state_json)

    def _record_tool_outcome(self, name: str, args: dict, success: bool,
                             error: Optional[str] = None, out: Any = None,
                             requested_args: Optional[Dict[str, Any]] = None) -> None:
        """Record a tool outcome into DesignState recent_operations/errors.

        Args:
            requested_args: The originally requested parameters (before recovery).
                If provided and different from `args`, both are preserved so the
                LLM can see the requested-vs-achieved mismatch.
        """
        target = args.get("target_id") or args.get("target") or \
            args.get("object") or args.get("object_name")
        self.design_state.update_from_tool_result(
            tool=name, result=out, target_id=target, args=args,
            success=success, error=error,
            requested_args=requested_args,
        )

    def _check_pending_operations_against_state(self, state_objects: List[Dict[str, Any]]) -> None:
        """BIP 7.0: Check whether any unresolved operations have late-completed.

        Safety rule: UNKNOWN != SUCCESS. Mere existence of ``target_id`` is NOT
        completion evidence (for feature operations the target existed before
        the operation started). Completion must be proven from the operation's
        own semantics:

        - feature-producing ops (fillet/chamfer/hole/boolean/shell/patterns/
          extrude): completed iff the REQUESTED result id (args["id"]) exists in
          the refreshed state, and its type agrees with the tool.
        - box/cylinder: completed iff the requested id exists AND the live
          dimensional properties match the requested values.
        - edit_feature: completed iff the target exists AND every requested
          parameter in args["parameters"] matches the live property value.
        - delete_feature: completed iff the target is ABSENT from state.
        - mate / sketch and any op without positive evidence: stay UNRESOLVED.

        Args:
            state_objects: List of object dicts from current CAD state
        """
        objects_by_id = {
            obj.get("id"): obj
            for obj in state_objects if isinstance(obj, dict)
        }
        existing_ids = set(objects_by_id)

        # Ops that produce a NEW feature object named by args["id"]; the
        # expected FreeCAD object type of that result, used as corroborating
        # evidence when available in state.
        feature_ops_result_type = {
            "fillet": ("Part::Fillet",),
            "chamfer": ("Part::Chamfer",),
            "boolean": ("Part::Cut", "Part::MultiFuse", "Part::MultiCommon"),
            "hole": ("Part::Cut",),
            "shell": ("Part::Feature", "Part::Thickness"),
            "pattern_linear": ("Part::Feature",),
            "pattern_circular": ("Part::Feature",),
            "extrude": ("Part::Pad", "Part::Pocket", "Part::Feature"),
            "sketch": ("Sketcher::SketchObject",),
        }
        primitive_ops = {"box", "cylinder"}

        def _props_match(obj: Dict[str, Any], expected: Dict[str, Any]) -> bool:
            props = obj.get("properties") or {}
            for key, want in expected.items():
                if key in ("id", "target_id", "tool_id", "mode", "origin"):
                    continue
                live_raw = props.get(key)
                if live_raw is None or want is None:
                    return False
                try:
                    live = float(live_raw)
                    want_f = float(want)
                except (TypeError, ValueError):
                    continue
                if abs(live - want_f) > 1e-6:
                    return False
            return True

        unresolved = self._operation_registry.get_unresolved()
        for op_id, record in unresolved.items():
            tool = record.tool
            target_id = record.target_id
            feature_id = record.args.get("id")

            evidence = None  # "completed" | None (= stays unresolved)

            if tool in feature_ops_result_type:
                # Completion evidence: the requested result object exists and
                # (when type info is available) has a plausible result type.
                if feature_id and feature_id in existing_ids:
                    obj = objects_by_id[feature_id]
                    expected_types = feature_ops_result_type[tool]
                    live_type = str(obj.get("type") or "")
                    if not expected_types or any(
                            t in live_type for t in expected_types):
                        evidence = "completed"
            elif tool in primitive_ops:
                if feature_id and feature_id in existing_ids:
                    obj = objects_by_id[feature_id]
                    expected: Dict[str, Any] = {}
                    if tool == "box":
                        expected = {"Length": record.args.get("length"),
                                    "Width": record.args.get("width"),
                                    "Height": record.args.get("height")}
                    else:
                        expected = {"Radius": record.args.get("radius"),
                                    "Height": record.args.get("height")}
                    expected = {k: v for k, v in expected.items()
                                if v is not None}
                    if expected and _props_match(obj, expected):
                        evidence = "completed"
            elif tool == "edit_feature":
                params = record.args.get("parameters") or {}
                if target_id and target_id in existing_ids and params:
                    obj = objects_by_id[target_id]
                    props = obj.get("properties") or {}
                    if all(
                        k in props
                        and abs(float(props[k]) - float(v)) <= 1e-6
                        for k, v in params.items()
                        if isinstance(v, (int, float))
                    ):
                        evidence = "completed"
            elif tool == "delete_feature":
                gone_id = record.args.get("target_feature_id") or target_id
                if gone_id and gone_id not in existing_ids:
                    evidence = "completed"
            # mate and any unrecognised tool: no positive evidence possible from
            # a state listing alone -> remain UNRESOLVED.

            if evidence == "completed":
                print(f"[LateCompletion] Operation {op_id} ({record.tool}) "
                      f"late-completed: semantic evidence found in refreshed "
                      f"state. Reconciling as completed.")
                self._operation_registry.mark_late_completion(op_id)
                self._operation_registry.reconcile(op_id, "completed")
                self._mutation_gate.force_release_for_reconciliation(op_id)
            else:
                # UNKNOWN stays UNRESOLVED: the logical mutation barrier is
                # preserved until genuine completion evidence appears.
                print(f"[LateCompletion] Operation {op_id} ({record.tool}) "
                      f"still unresolved after state refresh: no completion "
                      f"evidence. Barrier maintained.")

    def get_context_telemetry(self) -> List[Dict[str, Any]]:
        """Return recorded per-LLM-call context telemetry (estimate flags set).

        Returns a list of dicts (one per compiled reasoning step). Estimates are
        labelled `estimated_*`; exact provider token counts, when available, are
        under `exact_provider_tokens`.
        """
        return list(self._context_telemetry)

    def _compile_context(
        self,
        user_message: str,
        available_tools: List[Dict[str, Any]],
        react_step: int,
        optional_context_plan: Optional[ContextPlan] = None,
        tool_selection_plan: Optional[ToolSelectionPlan] = None,
    ) -> CompiledContext:
        """Compile the SELECTIVE LLM context for a single ReAct reasoning step (BIP 4.2).

        - relevant CAD objects (no blind state dump)
        - relevant memory (no blind memory dump)
        - relevant conversation history (no blind history dump)
        - plan-required + routed tools (new capability-based ToolRouter)
        """
        return self.compiler.compile(
            user_message=user_message,
            conversation_context=self.conversation,
            design_state=self.design_state,
            session_memory=self.session_memory,
            available_tools=available_tools,
            react_step=react_step,
            system_prefix=SYSTEM_PROMPT + "\n\n" + REACT_LOOP_INJECTION,
            optional_context_plan=optional_context_plan,
            tool_selection_plan=tool_selection_plan,
        )

    def handle_message(self, user_message: str):
        """Process a user message using a ReAct scratchpad pattern.

        Long-term memory (self.history): Stores ONLY user prompts and final agent responses.
        Short-term scratchpad (local variable): Stores ReAct loop internals (tool calls, results).
        The scratchpad is discarded after each handle_message call, keeping history clean.

        If capture_trace=True (evaluation mode), a detailed execution trace is recorded
        and can be retrieved via get_trace() after the call returns.
        """
        # Prevent unbounded context growth (keep last 20 messages max)
        MAX_HISTORY = 20
        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]

        # Append user message to long-term history
        self.history.append({"role": "user", "content": user_message.strip()})

        # Track the user turn in the ContextEngine conversation (selective view).
        self.conversation.add_user(user_message.strip())
        # Reflect the new task on the DesignState (best-effort, no forced parse).
        self.design_state.current_task = user_message.strip()

        # BIP 10.3: Extract and record any explicit design conventions from user message
        self._maybe_record_convention_from_message(user_message)

        # BIP 8.3: Extract bounding-box constraints from user message
        self._bbox_constraints = self._extract_bbox_constraints(user_message)
        if self._bbox_constraints:
            print(
                f"[Agent] Bounding-box constraints detected: {self._bbox_constraints}")

        # Reset per-turn context telemetry (each handle_message is a new turn).
        self._context_telemetry = []

        # BIP 10.2: Reset token telemetry for this handle_message call.
        # BIP 10.4: also reset the per-turn ceiling observability fields.
        self._token_telemetry = {
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_tokens": 0,
            "llm_calls": 0,
            "model": None,
            "provider": None,
            "per_step": [],
            "ceiling_limit": self.max_tokens_per_turn,
            "ceiling_reached": False,
        }

        # BIP 10.5: Reset router token-savings telemetry for this handle_message call.
        self._router_token_savings = []

        # BIP 10.3: Design conventions are stored in session memory (persisted across turns)
        # They are not reset per handle_message call

        # Short-term scratchpad for this ReAct loop execution
        scratchpad = []

        # Accumulates every tool the agent executed across this session/prompt.
        session_tools: list = []

        # Evaluation trace (opt-in)
        if self._capture_trace:
            self._trace = []

        # BIP 10.4b: overall turn deadline start time.
        turn_start = time.time()

        # Multi-step ReAct loop: max 10 steps to prevent infinite looping
        for step in range(self.MAX_STEPS):
            print(f"\n=== [ReAct Step {step+1}/{self.MAX_STEPS}] ===")

            # BIP 10.4b / A6.4: pre-step termination evaluation. The overall
            # wall-clock deadline is checked here (before the step's tools/
            # state/LLM work), identical to its original location. Does NOT
            # kill an in-flight CAD mutation thread; any unresolved mutation
            # stays tracked by the OperationRegistry/barrier.
            term_outcome, term_value = self._evaluate_termination(
                None, step, session_tools, turn_start)
            if term_outcome == "return":
                return term_value

            # 1. Ask adapter for its active tools
            all_tools = self.adapter.get_tools()
            print(f"[Agent] Step {step+1}: {len(all_tools)} tools available")

            # 2. Get current CAD state
            state_retrieval_failed = False
            try:
                state_json = self.adapter.get_state()
            except Exception as e:
                print(f"[Agent] Warning: Failed to get state: {e}")
                # Mark DesignState as unavailable but PRESERVE last known-good objects.
                self.design_state.mark_state_unavailable()
                # Continue with an empty state for router gating; the compiler will
                # see state_available=false and can expose the stale summary.
                state_json = "[]"
                state_retrieval_failed = True

            # 2a. Parse state into objects for router-based tool gating.
            #     On a transient retrieval failure, gate the router from the
            #     PRESERVED last-known-good DesignState rather than the synthetic
            #     empty payload, so a state blip does not strip tools for objects
            #     that still exist in the preserved state.
            if state_retrieval_failed and self.design_state.objects:
                state_objects = [o.to_dict(minimal=True)
                                 for o in self.design_state.objects.values()]
            else:
                try:
                    state_objects = json.loads(state_json)
                except (json.JSONDecodeError, TypeError):
                    state_objects = []

            # 2b. Sync registry with current tool schemas (infers capabilities for new tools)
            self.router.sync_registry(all_tools)

            # 2c. Get state-gated tools from new capability-based router
            router_tools = self.router.filter_tools(all_tools, state_objects)

            # 2d. Update the authoritative DesignState from the live CAD state.
            self._update_design_state(state_json)

            # 3. INTENT CLASSIFICATION: classify user intent once per turn (first step)
            #    and reuse the plan for all ReAct steps in this turn.
            if step == 0:
                intent_plan, intent_tool_plan = self._classify_intent(
                    user_message)
            else:
                intent_plan = None
                intent_tool_plan = None

            # 3. Compile a SELECTIVE context via the ContextEngine (BIP 4.2).
            compiled = self._compile_context(
                user_message=user_message,
                available_tools=all_tools,
                react_step=step + 1,
                optional_context_plan=intent_plan,
                tool_selection_plan=intent_tool_plan,
            )
            # Use compiled tools (which already includes plan + router via compiler._select_tools)
            tools = compiled.tools

            # BIP 10.5: Router token-savings instrumentation.
            # Measure the token cost difference between ungated (all_tools) and
            # filtered (compiled.tools) tool schemas. Use the same chars/4
            # heuristic as the existing context/token infrastructure.
            # If the compiler telemetry provides exact estimates, use those;
            # otherwise compute via estimate_json_tokens.
            try:
                from core.context.telemetry import estimate_json_tokens
                ungated_token_count = estimate_json_tokens(all_tools)
            except Exception:
                # If estimation fails for any reason, record as unavailable
                # rather than fabricating a value.
                ungated_token_count = None
            try:
                from core.context.telemetry import estimate_json_tokens
                filtered_token_count = estimate_json_tokens(tools)
            except Exception:
                filtered_token_count = None

            token_savings = None
            if (ungated_token_count is not None
                    and filtered_token_count is not None
                    and ungated_token_count > 0):
                token_savings = ungated_token_count - filtered_token_count
                if token_savings < 0:
                    # Should not happen, but guard against estimation noise.
                    token_savings = 0

            # Record per-step savings for aggregation in handle_message telemetry.
            self._router_token_savings.append({
                "react_step": step + 1,
                "tools_before_filtering": len(all_tools),
                "tools_after_filtering": len(tools),
                "ungated_schema_token_estimate": ungated_token_count,
                "filtered_schema_token_estimate": filtered_token_count,
                "token_savings_estimate": token_savings,
                "estimation_method": "chars/4 heuristic",
            })

            print(
                f"[Agent] Step {step+1}: {len(tools)}/{len(all_tools)} tools active "
                f"(capability-routed + plan-required)"
            )

            # 4. Build messages from the compiled context: system (selective
            #     state) + relevant conversation + scratchpad.
            # Compact scratchpad before sending to LLM to bound token growth.
            scratchpad, self._scratchpad_persistent_summary = _compact_scratchpad(
                scratchpad, self._scratchpad_persistent_summary)
            # Build system context with persistent summary if available
            dynamic_system = compiled.system_context
            if self._scratchpad_persistent_summary:
                dynamic_system = dynamic_system + "\n\n" + self._scratchpad_persistent_summary
            messages = [{"role": "system", "content": dynamic_system}
                        ] + compiled.conversation + scratchpad

            # 6. Get intent from LLM.
            #    Transient provider/transport failures (connection drops,
            #    timeouts, rate limits, HTTP 5xx) are retried with bounded
            #    backoff inside _generate_with_retry. If retries are exhausted,
            #    the turn degrades gracefully (structured completion) instead of
            #    letting the provider exception crash the whole turn.
            try:
                response = self._invoke_llm(messages, tools)
            except Exception as e:
                # Non-transient provider error (e.g. bad request/auth): never
                # silently reinterpreted as success.
                fail_msg = (
                    f"LLM request failed: {type(e).__name__}: {e}"
                )
                print(f"\033[91m[ERROR] {fail_msg}\033[0m")
                self.history.append({"role": "assistant", "content": fail_msg})
                self.conversation.add_assistant(fail_msg)
                if self._capture_trace:
                    self._trace.append({
                        "step": step + 1,
                        "type": "llm_error",
                        "error": fail_msg,
                        "termination_reason": self.LLM_ERROR_TERMINATION_REASON,
                        "token_telemetry": self._token_telemetry.copy(),
                    })
                return fail_msg, session_tools

            if response is None:
                # All transient retries exhausted: degrade gracefully rather
                # than raising out of the ReAct loop.
                fail_msg = (
                    "LLM provider unavailable after retries; unable to complete "
                    "the request. The CAD state was not modified by this step."
                )
                print(f"\033[91m[ERROR] {fail_msg}\033[0m")
                self.history.append({"role": "assistant", "content": fail_msg})
                self.conversation.add_assistant(fail_msg)
                if self._capture_trace:
                    self._trace.append({
                        "step": step + 1,
                        "type": "llm_unavailable",
                        "message": fail_msg,
                        "termination_reason": self.LLM_TRANSIENT_EXHAUSTED_TERMINATION_REASON,
                        "token_telemetry": self._token_telemetry.copy(),
                    })
                return fail_msg, session_tools

            # Exact provider-reported token usage (None when a provider does not
            # report usage, or for stubs that do not expose last_usage).
            provider_usage = getattr(self.provider, "last_usage", None)

            # BIP 10.2: Track token usage for this LLM call.
            if provider_usage:
                input_tokens = provider_usage.get("prompt_tokens", 0)
                output_tokens = provider_usage.get("completion_tokens", 0)
                total_tokens = provider_usage.get("total_tokens", 0)
                model = provider_usage.get("model")
                provider_name = provider_usage.get("provider")

                # Accumulate for the handle_message call
                self._token_telemetry["total_input_tokens"] += input_tokens
                self._token_telemetry["total_output_tokens"] += output_tokens
                self._token_telemetry["total_tokens"] += total_tokens
                self._token_telemetry["llm_calls"] += 1
                # Capture model/provider from first call that has it
                if self._token_telemetry["model"] is None and model:
                    self._token_telemetry["model"] = model
                if self._token_telemetry["provider"] is None and provider_name:
                    self._token_telemetry["provider"] = provider_name

                # Record per-step breakdown
                self._token_telemetry["per_step"].append({
                    "step": step + 1,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": total_tokens,
                    "model": model,
                    "provider": provider_name,
                })
            else:
                # BIP 10.4: The provider did not report usage metadata. We do
                # NOT fabricate a token count: the call contributes 0 to the
                # cumulative exact total and therefore cannot trigger the
                # per-turn ceiling. This is the deterministic behaviour for a
                # provider that omits token usage — the ceiling only enforces
                # against real provider-reported totals.
                self._token_telemetry["usage_unavailable_calls"] = (
                    self._token_telemetry.get("usage_unavailable_calls", 0) + 1
                )

            # Record compiled-context telemetry after the LLM call, attaching
            # the exact provider-reported token usage (if the provider supplied
            # it). Estimates and exact usage remain clearly separate fields.
            if compiled.telemetry is not None:
                compiled.telemetry.exact_provider_tokens = provider_usage
                if provider_usage:
                    compiled.telemetry.input_tokens = provider_usage.get(
                        "prompt_tokens")
                    compiled.telemetry.output_tokens = provider_usage.get(
                        "completion_tokens")
                    compiled.telemetry.total_tokens = provider_usage.get(
                        "total_tokens")
                self._context_telemetry.append(compiled.telemetry.to_dict())

            # BIP 10.4 / step 7: Termination/done-state evaluation (A5.4).
            # Covers token-ceiling termination, normal completion, empty-response
            # continuation, and the fall-through to tool execution.
            term_outcome, term_value = self._evaluate_termination(
                response, step, session_tools)
            if term_outcome == "return":
                return term_value
            if term_outcome == "continue":
                continue

            # 8. LLM returned tool calls - append assistant message to scratchpad
            scratchpad.append({
                "role": "assistant",
                "content": None,
                "tool_calls": response.tool_calls
            })

            # 9. Execute tool calls through the adapter.
            #    - Malformed tool arguments are captured as a recoverable error.
            #    - Transient connection/transport failures keep their existing
            #      retry treatment.
            #    - Non-transient (CAD/kernel) failures are NOT allowed to abort the
            #      turn: they are converted to a structured tool result and
            #      returned to the LLM so it can reason about recovery.
            results = []

            # A5.5: drive the extracted tool-execution generator. The generator
            # yields one ToolExecutionResult per tool call (A6.3); the
            # verification, state-refresh and topology-refresh blocks below
            # remain inline (A5.6) and re-bind the same local variable names to
            # preserve behaviour exactly.
            self._session_tools_for_step = session_tools
            for _result in self._execute_tool_calls(
                    response.tool_calls, step + 1):
                # Malformed tool arguments: append the structured error to
                # ``results`` (aligned 1:1 with tool_calls) and skip
                # verification for this call.
                if _result.malformed:
                    results.append(_result.malformed_result)
                    continue
                name = _result.name
                args = _result.args
                target = _result.target
                out = _result.out
                error = _result.error
                success = _result.success
                attempts = _result.attempts
                transient = _result.transient
                operation_id = _result.operation_id
                is_mutation = _result.is_mutation
                gate_acquired = _result.gate_acquired
                requested_args_for_recording = \
                    _result.requested_args_for_recording

                # A5.6: mandatory post-tool verification (BIP 8.1/8.2/8.3/8.4/11.1).
                # A failed verification converts success to False and records the
                # failure, preserving the pre-existing recovery semantics.
                (success, error, operation_id, is_mutation,
                 gate_acquired) = self._verify_tool_outcome(
                    name, args, operation_id, is_mutation, gate_acquired,
                    success, error, out)

                # Only append result to results after ALL verifications pass
                if success:
                    results.append(out)
                    print(
                        f"[Execution] Step {step+1}: Tool '{name}' succeeded: {out}")
                    error_msg = None
                    # BIP 7.0: Mark operation as succeeded in registry
                    self._operation_registry.succeed(operation_id)
                    # Release mutation gate on success
                    if is_mutation and gate_acquired:
                        self._mutation_gate.release(operation_id)
                else:
                    if error is None:
                        error = RuntimeError(
                            f"Execution error on {name}: unknown error after retries")
                    error_msg = str(error)

                    # BIP 9.3: Classify stale-topology failures distinctly.
                    # StaleTopologyError (or a bridge message naming stale
                    # topology references) means the face/edge reference no
                    # longer matches live geometry. This is NOT retried
                    # blindly: fresh topology is re-queried below so the next
                    # reasoning step can use a fresh reference.
                    is_stale_topology = (
                        isinstance(error, StaleTopologyError)
                        or ("stale" in error_msg.lower()
                            and ("topology" in error_msg.lower()
                                 or "reference" in error_msg.lower()))
                    )

                    print(
                        f"\033[91m[ERROR] Step {step+1}: Tool '{name}' failed: {error_msg}\033[0m")
                    results.append(json.dumps({
                        "status": "error",
                        "tool": name,
                        "error_type": ("StaleTopologyError"
                                       if is_stale_topology
                                       else type(error).__name__),
                        "error": error_msg,
                        "arguments": args,
                        "transient": transient,
                        "retries": attempts - 1,
                        "stale_topology": is_stale_topology,
                    }, default=str))
                    # Release mutation gate on non-timeout failure
                    if is_mutation and gate_acquired and not transient:
                        self._mutation_gate.release(operation_id)
                    # For timeout: release the PHYSICAL lock (timeout boundary returns
                    # control to caller, so the critical section ends). The LOGICAL
                    # unresolved barrier is maintained by the OperationRegistry.
                    if is_mutation and gate_acquired and transient:
                        self._mutation_gate.release(operation_id)

                    # BIP 9.3: After a stale-topology failure, automatically
                    # re-query fresh topology using the EXISTING get_edges /
                    # get_faces capability so the subsequent reasoning step can
                    # use a fresh reference. The ORIGINAL stale tool arguments
                    # are NOT retried.
                    if is_stale_topology:
                        stale_target = target
                        stale_ref_type = "edge"
                        if isinstance(error, StaleTopologyError):
                            stale_ref_type = error.ref_type or "edge"
                        else:
                            # Bridge-level stale message: infer ref type from
                            # the tool that failed.
                            if name in ("get_faces", "shell", "sketch"):
                                stale_ref_type = "face"
                        if stale_target:
                            try:
                                refresh_tool = ("get_edges"
                                                if stale_ref_type == "edge"
                                                else "get_faces")
                                fresh_json = self.adapter.execute_command(
                                    refresh_tool, object_name=stale_target)
                                self.design_state.update_from_tool_result(
                                    tool=refresh_tool,
                                    result=fresh_json,
                                    target_id=stale_target,
                                    args={"object_name": stale_target},
                                    success=True,
                                )
                                print(
                                    f"[Agent] Stale topology detected for "
                                    f"'{stale_target}'; re-queried fresh "
                                    f"topology via {refresh_tool}.")
                                if self._capture_trace:
                                    self._trace.append({
                                        "step": step + 1,
                                        "type": "stale_topology_requery",
                                        "tool": name,
                                        "target": stale_target,
                                        "refresh_tool": refresh_tool,
                                    })
                            except Exception as re_err:
                                # Re-query is best-effort; the stale error is
                                # still returned to the LLM for reasoning.
                                print(
                                    f"[Agent] Warning: stale-topology re-query "
                                    f"failed for '{stale_target}': {re_err}")

                # Record trace entry if capture_trace is enabled
                if self._capture_trace:
                    self._trace.append({
                        "step": step + 1,
                        "tool": name,
                        "arguments": args,
                        "result": out if out is not None else error_msg,
                        "success": success,
                        "error": error_msg if not success else None,
                        "attempt": attempts,
                        "transient": transient,
                        "token_telemetry": self._token_telemetry.copy(),
                    })

                # Record the tool outcome into the authoritative DesignState.
                # A failed operation is recorded as failed (never as success) and
                # never fabricates a CAD object.
                self._record_tool_outcome(
                    name, args, success,
                    error=(error_msg if not success else None),
                    out=out,
                    requested_args=requested_args_for_recording,
                )

                # Record tool health for dynamic availability tracking
                self.router.record_tool_result(
                    name, success, error_msg if not success else None)

                # BIP 4.3.9: After a successful topology-changing operation,
                # refresh the target object's authoritative topology fingerprint
                # so the next fillet/chamfer is not unnecessarily rejected as
                # stale. We reuse the EXISTING bridge topology query path
                # (get_edges) and the EXISTING state-sync path
                # (update_from_tool_result) — no new fingerprint is invented.
                topology_altering_tools = {
                    "fillet", "chamfer", "boolean", "hole",
                    "shell", "edit_feature", "pattern_linear",
                    "pattern_circular", "delete_feature",
                }
                # BIP 6.1: For feature-producing operations, the newly created
                # feature (args["id"]) becomes the design tip. The topology
                # refresh must query the NEW feature, not the consumed source,
                # so that its topology_version and edges/faces are tracked under
                # the correct object identity. This ensures feature lineage:
                # box → fillet → chamfer → fillet, not independent branches.
                # Use the feature's own ID (args["id"]) as the refresh target.
                feature_id = args.get("id")
                # Fall back to target_id for operations that don't create a new feature
                refresh_target = feature_id if feature_id else (
                    args.get("target_id") or args.get("object_name"))
                if success and name in topology_altering_tools and refresh_target:
                    try:
                        # BIP 5.5: capture the version at which the existing edge
                        # references were emitted BEFORE the refresh. The refresh
                        # must advance the object-level fingerprint (so the NEXT
                        # reference capture is current) but must NOT make the
                        # already-emitted references valid - otherwise a reused
                        # old edge_ref would silently pass stale-validation merely
                        # because the object-level version was refreshed.
                        emitted_ref_version = \
                            self.design_state.get_recorded_reference_version(
                                refresh_target, "edge")
                        edges_json = self.adapter.execute_command(
                            "get_edges", object_name=refresh_target)
                        self.design_state.update_from_tool_result(
                            tool="get_edges",
                            result=edges_json,
                            target_id=refresh_target,
                            args={"object_name": refresh_target},
                            success=True,
                        )
                        # Preserve the emitted reference version so it stays
                        # genuinely stale until an explicit get_edges refresh.
                        self.design_state.record_topology_reference(
                            refresh_target, "edge", emitted_ref_version)
                    except Exception as e:
                        # Refresh is best-effort: a failure here must NOT abort
                        # the turn or fabricate a topology fingerprint.
                        print(
                            f"[Agent] Warning: post-operation topology refresh "
                            f"failed for '{refresh_target}': {e}")

            # 10. Append tool results to scratchpad as tool messages. Every
            #     tool_call gets exactly one result entry (success, structured
            #     error, or malformed-args error), so the lists stay aligned.
            # OpenAI API requires tool message content to be a string.
            for tc, res in zip(response.tool_calls, results):
                # Ensure content is always a string (JSON-serialize if needed)
                if not isinstance(res, str):
                    content = json.dumps(res, default=str)
                else:
                    content = res
                scratchpad.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content
                })

            # 10a. Runtime geometry verification + IMMEDIATE STATE SYNC (BIP 4.3.2).
            # A5.6: refresh DesignState from the live CAD state, reconcile
            # pending operations, clean up the operation registry, and inject
            # any geometry uncertainty/warning into the scratchpad.
            self._refresh_state_and_verify_geometry(
                response, step, scratchpad)

            # 10b. Scratchpad compaction: bound scratchpad growth by summarizing
            # older tool interactions while preserving CAD-critical information
            # (object names, face/edge refs, topology_version, success/failure,
            # parameters, structured errors). Deterministic, no LLM calls.
            scratchpad, self._scratchpad_persistent_summary = _compact_scratchpad(
                scratchpad, self._scratchpad_persistent_summary)

            # 11. Loop repeats - do NOT return to user yet
            print(
                f"[Agent] Step {step+1} complete. Continuing to next step...")

        # Max steps reached (A6.4: unified termination boundary)
        return self._max_steps_termination(session_tools)

    def get_trace(self) -> list:
        """Return the captured ReAct execution trace (evaluation mode only).

        Returns an empty list if capture_trace was not enabled.
        """
        return self._trace if self._capture_trace else []

    def get_token_telemetry(self) -> Dict[str, Any]:
        """Return token usage telemetry for the most recent handle_message call.

        Returns a dict with:
        - total_input_tokens: sum of prompt tokens across all LLM calls
        - total_output_tokens: sum of completion tokens across all LLM calls
        - total_tokens: sum of total tokens across all LLM calls
        - llm_calls: number of LLM calls made
        - model: model name (if available)
        - provider: provider name (if available)
        - per_step: list of per-step token breakdowns
        - ceiling_limit: per-turn token ceiling (None if disabled)
        - ceiling_reached: whether the ceiling was reached
        - usage_unavailable_calls: count of LLM calls without usage metadata
        - router_token_savings: aggregated router filtering savings (BIP 10.5)

        Returns empty dict if no LLM calls were made.
        """
        telemetry = self._token_telemetry.copy()
        # BIP 10.5: Include aggregated router token savings.
        if self._router_token_savings:
            total_savings = sum(
                s.get("token_savings_estimate") or 0
                for s in self._router_token_savings
            )
            total_ungated = sum(
                s.get("ungated_schema_token_estimate") or 0
                for s in self._router_token_savings
            )
            total_filtered = sum(
                s.get("filtered_schema_token_estimate") or 0
                for s in self._router_token_savings
            )
            telemetry["router_token_savings"] = {
                "total_savings_estimate": total_savings,
                "total_ungated_estimate": total_ungated,
                "total_filtered_estimate": total_filtered,
                "steps_measured": len(self._router_token_savings),
                "per_step": self._router_token_savings,
            }
        else:
            telemetry["router_token_savings"] = {
                "total_savings_estimate": 0,
                "total_ungated_estimate": 0,
                "total_filtered_estimate": 0,
                "steps_measured": 0,
                "per_step": [],
            }
        return telemetry

    def get_router_token_savings(self) -> List[Dict[str, Any]]:
        """Return the per-step router token-savings telemetry (BIP 10.5).

        Returns a list of dicts, one per ReAct step, with:
        - react_step: step number (1-indexed)
        - tools_before_filtering: count of all available tools
        - tools_after_filtering: count of tools after router filtering
        - ungated_schema_token_estimate: estimated tokens for all tool schemas
        - filtered_schema_token_estimate: estimated tokens for filtered tool schemas
        - token_savings_estimate: estimated tokens saved by filtering
        - estimation_method: method used ("chars/4 heuristic")

        Returns empty list if no steps were measured.
        """
        return list(self._router_token_savings)
