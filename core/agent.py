"""PieCAD Core Orchestrator. CAD-agnostic."""
import json
from typing import Any, Dict, List, Optional
from providers.llm.provider import LLMProvider
from core.adapters.interfaces import CADAdapter
from core.router import ToolRouter
from core.verification.checks import check_geometry
# Context Engine (BIP 4.2): provider-independent state / memory / compilation.
from core.context import (
    ContextCompiler,
    ConversationContext,
    DesignState,
    SessionMemory,
)

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


class CADAgent:
    MAX_RETRIES = 3
    MAX_STEPS = 15

    def __init__(
        self,
        adapter: CADAdapter,
        provider: Optional[LLMProvider] = None,
        *,
        capture_trace: bool = False,
    ):
        self.adapter = adapter
        self.provider = provider or LLMProvider()
        # Tool gating: only relevant tools are exposed to the LLM per step.
        self.router = ToolRouter()
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

        # Telemetry for the most recent LLM call(s). Accessible for observability
        # without dumping large payloads into normal logs.
        self._context_telemetry: List[Dict[str, Any]] = []

        # BIP 4.3.2: Track last failed tool args per (tool, target) for
        # requested-vs-achieved integrity. When a tool fails with a non-transient
        # error, we remember the requested args. If the next successful call to
        # the same tool/target has different args, both are preserved.
        self._last_failed_args: Dict[tuple, Dict[str, Any]] = {}

    def _is_transient_error(self, error: RuntimeError) -> bool:
        """Return True if the RuntimeError wraps a transient connection/transport failure.

        The FreeCADAdapter wraps xmlrpc.client.ProtocolError, ConnectionError, and OSError
        into RuntimeError. We check the error message for indicators of transient failures.
        """
        msg = str(error).lower()
        # Connection-related transient indicators (covers XML-RPC and direct connection errors)
        transient_indicators = [
            "connection", "reset", "refused", "timeout", "unreachable",
            "broken pipe", "connection aborted", "connection lost",
            "cannot reach", "cannot connect",
        ]
        return any(ind in msg for ind in transient_indicators)

    # ------------------------------------------------------------------ #
    # Context Engine integration (BIP 4.2)
    # ------------------------------------------------------------------ #
    def _update_design_state(self, state_json: str) -> None:
        """Refresh DesignState from the adapter's current CAD state string."""
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

    def get_context_telemetry(self) -> List[Dict[str, Any]]:
        """Return recorded per-LLM-call context telemetry (estimate flags set).

        Returns a list of dicts (one per compiled reasoning step). Estimates are
        labelled `estimated_*`; exact provider token counts, when available, are
        under `exact_provider_tokens`.
        """
        return list(self._context_telemetry)

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

        # Reset per-turn context telemetry (each handle_message is a new turn).
        self._context_telemetry = []

        # Short-term scratchpad for this ReAct loop execution
        scratchpad = []

        # Accumulates every tool the agent executed across this session/prompt.
        session_tools: list = []

        # Evaluation trace (opt-in)
        if self._capture_trace:
            self._trace = []

        # Multi-step ReAct loop: max 10 steps to prevent infinite looping
        for step in range(self.MAX_STEPS):
            print(f"\n=== [ReAct Step {step+1}/{self.MAX_STEPS}] ===")

            # 1. Ask adapter for its active tools
            all_tools = self.adapter.get_tools()
            print(f"[Agent] Step {step+1}: {len(all_tools)} tools available")

            # 2. Get current CAD state
            try:
                state_json = self.adapter.get_state()
            except Exception as e:
                print(f"[Agent] Warning: Failed to get state: {e}")
                # Mark DesignState as unavailable but PRESERVE last known-good objects.
                self.design_state.mark_state_unavailable()
                # Continue with an empty state for router gating; the compiler will
                # see state_available=false and can expose the stale summary.
                state_json = "[]"

            # 2a. Parse state into objects for router-based tool gating.
            try:
                state_objects = json.loads(state_json)
            except (json.JSONDecodeError, TypeError):
                state_objects = []

            # 2b. Keep the existing ToolRouter gating (unchanged), then let the
            #     ContextEngine further add plan-required tools.
            router_tools = self.router.filter_tools(all_tools, state_objects)

            # 2c. Update the authoritative DesignState from the live CAD state.
            self._update_design_state(state_json)

            # 3. Compile a SELECTIVE context via the ContextEngine (BIP 4.2).
            #     - relevant CAD objects (no blind state dump)
            #     - relevant memory (no blind memory dump)
            #     - relevant conversation history (no blind history dump)
            #     - plan-required + routed tools (existing ToolRouter preserved)
            compiled = self.compiler.compile(
                user_message=user_message,
                conversation_context=self.conversation,
                design_state=self.design_state,
                session_memory=self.session_memory,
                available_tools=all_tools,
                react_step=step + 1,
                system_prefix=SYSTEM_PROMPT + "\n\n" + REACT_LOOP_INJECTION,
            )
            tools = compiled.tools or router_tools
            print(
                f"[Agent] Step {step+1}: {len(tools)}/{len(all_tools)} tools active "
                f"(routed OR plan-required)"
            )

            # 4. Build messages from the compiled context: system (selective
            #     state) + relevant conversation + scratchpad.
            dynamic_system = compiled.system_context
            messages = [{"role": "system", "content": dynamic_system}
                        ] + compiled.conversation + scratchpad

            # 6. Get intent from LLM
            print(f"[Agent] Calling LLM with {len(tools)} tools available...")
            response = self.provider.generate_with_tools(
                messages=messages, tools=tools
            )
            # Exact provider-reported token usage (None when a provider does not
            # report usage, or for stubs that do not expose last_usage).
            provider_usage = getattr(self.provider, "last_usage", None)

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

            # 7. If LLM returns plain text (NO tool calls):
            #    - meaningful content -> normal completion.
            #    - empty/None content -> do NOT claim "Done."; keep going so the
            #      LLM can produce a real response (bounded by MAX_STEPS).
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
                            "termination_reason": "no_tool_calls",
                        })
                    return reply, session_tools
                # Empty response: do not fabricate success. Continue the ReAct
                # loop; if steps remain the LLM gets another chance.
                print(
                    f"[Agent] WARNING: empty LLM response at step {step+1} "
                    "(no tool calls, no meaningful content). Continuing loop.")
                if self._capture_trace:
                    self._trace.append({
                        "step": step + 1,
                        "type": "empty_response",
                        "termination_reason": "empty_response_continue",
                    })
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

            for tc in response.tool_calls:
                name = tc.function.name
                # Record this session's tool usage (attempted, even on failure).
                session_tools.append(name)

                # --- Defensively parse tool arguments (recoverable error) ---
                try:
                    args = json.loads(tc.function.arguments)
                    if not isinstance(args, dict):
                        args = {}
                except (json.JSONDecodeError, TypeError) as e:
                    print(
                        f"\033[91m[ERROR] Step {step+1}: Tool '{name}' malformed "
                        f"arguments: {e}\033[0m")
                    results.append(json.dumps({
                        "status": "error",
                        "tool": name,
                        "error_type": type(e).__name__,
                        "error": f"Malformed tool arguments (invalid JSON): {e}",
                        "arguments": {},
                        "transient": False,
                        "retries": 0,
                    }, default=str))
                    self._record_tool_outcome(
                        name, {}, False,
                        error=f"Malformed tool arguments (invalid JSON): {e}",
                        out=None)
                    if self._capture_trace:
                        self._trace.append({
                            "step": step + 1,
                            "tool": name,
                            "arguments": {},
                            "result": None,
                            "success": False,
                            "error": f"Malformed tool arguments (invalid JSON): {e}",
                            "attempt": 1,
                        })
                    continue

                # Target object id for requested-vs-achieved tracking (BIP 4.3.2).
                target = args.get("target_id") or args.get("target") or \
                    args.get("object") or args.get("object_name")

                print(
                    f"[Execution] Step {step+1}: Tool '{name}' with args: {args}")

                # --- Execute with transient retry; retain non-transient errors ---
                out = None
                error = None
                success = False
                attempts = 0
                transient = False
                for attempt in range(self.MAX_RETRIES + 1):
                    attempts = attempt + 1
                    try:
                        out = self.adapter.execute_command(name, **args)
                        success = True
                        break
                    except (ConnectionError, OSError) as e:
                        error = e
                        transient = True
                        attempts = attempt + 1
                        if attempt < self.MAX_RETRIES:
                            print(
                                f"[Retry] Step {step+1}: Tool '{name}' attempt {attempt + 1} failed with transient error: {e}. Retrying...")
                            continue
                        # Exhausted transient retries -> recoverable failure.
                        break
                    except RuntimeError as e:
                        error = e
                        transient = self._is_transient_error(e)
                        attempts = attempt + 1
                        if transient and attempt < self.MAX_RETRIES:
                            print(
                                f"[Retry] Step {step+1}: Tool '{name}' attempt {attempt + 1} failed with transient error: {e}. Retrying...")
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
                elif success and tool_key in self._last_failed_args:
                    # Success after a prior non-transient failure on same tool/target.
                    # Check if achieved args differ from originally requested.
                    failed_args = self._last_failed_args.pop(tool_key)
                    if failed_args != args:
                        requested_args_for_recording = failed_args

                if success:
                    results.append(out)
                    print(
                        f"[Execution] Step {step+1}: Tool '{name}' succeeded: {out}")
                    error_msg = None
                else:
                    if error is None:
                        error = RuntimeError(
                            f"Execution error on {name}: unknown error after retries")
                    error_msg = str(error)
                    print(
                        f"\033[91m[ERROR] Step {step+1}: Tool '{name}' failed: {error_msg}\033[0m")
                    results.append(json.dumps({
                        "status": "error",
                        "tool": name,
                        "error_type": type(error).__name__,
                        "error": error_msg,
                        "arguments": args,
                        "transient": transient,
                        "retries": attempts - 1,
                    }, default=str))

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

            # 10a. Runtime geometry verification + IMMEDIATE STATE SYNC (BIP 4.3.2):
            # After EVERY successful tool execution, refresh DesignState from the
            # live CAD state BEFORE the next ReAct iteration. This ensures the
            # compiled context for the next step reflects the actual CAD state.
            state_retrieval_failed = False
            try:
                new_state_json = self.adapter.get_state()
                new_state = json.loads(new_state_json)
                # Immediately synchronize DesignState with the live CAD state.
                self._update_design_state(new_state_json)
            except Exception as e:
                print(
                    f"[Agent] Warning: Failed to get state for verification/sync: {e}")
                # Mark state as unavailable but preserve last known-good objects.
                self.design_state.mark_state_unavailable()
                state_retrieval_failed = True
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

            # 11. Loop repeats - do NOT return to user yet
            print(
                f"[Agent] Step {step+1} complete. Continuing to next step...")

        # Max steps reached
        fail_msg = f"Operation incomplete: maximum reasoning steps ({self.MAX_STEPS}) reached."
        print(f"\033[91m[ERROR] {fail_msg}\033[0m")
        # Append failure message to long-term history
        self.history.append({"role": "assistant", "content": fail_msg})
        if self._capture_trace:
            self._trace.append({
                "step": self.MAX_STEPS,
                "type": "max_steps_exhausted",
                "message": fail_msg,
                "termination_reason": "max_steps",
            })
        return fail_msg, session_tools

    def get_trace(self) -> list:
        """Return the captured ReAct execution trace (evaluation mode only).

        Returns an empty list if capture_trace was not enabled.
        """
        return self._trace if self._capture_trace else []
