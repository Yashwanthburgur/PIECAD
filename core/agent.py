"""PieCAD Core Orchestrator. CAD-agnostic."""
import json
from typing import Optional
from providers.llm.provider import LLMProvider
from core.adapters.interfaces import CADAdapter
from core.router import ToolRouter
from core.verification.checks import check_geometry

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

    def __init__(self, adapter: CADAdapter, provider: Optional[LLMProvider] = None):
        self.adapter = adapter
        self.provider = provider or LLMProvider()
        # Tool gating: only relevant tools are exposed to the LLM per step.
        self.router = ToolRouter()
        # Long-term conversation history: ONLY user prompts and final agent responses
        self.history = []

    def _summarize_state(self, state_str: str, max_items: int = 10) -> str:
        """Summarize CAD state to prevent context exhaustion on large assemblies.

        Handles both JSON dict (property-based) and JSON list (object-based) states.
        For lists, keeps only the most recently added max_items objects.
        Always ensures object names/IDs are clearly visible.
        """
        # Try to parse state_str as JSON
        try:
            parsed_state = json.loads(state_str)
        except (json.JSONDecodeError, TypeError):
            # If parsing fails, return as-is
            return state_str

        # If it's a list (object-based state, e.g., from FreeCAD)
        if isinstance(parsed_state, list):
            # If the number of items is <= max_items, return the original JSON string
            if len(parsed_state) <= max_items:
                return state_str

            # If > max_items, keep only the most recent max_items objects
            if len(parsed_state) > max_items:
                omitted = len(parsed_state) - max_items
                recent_objects = parsed_state[-max_items:]

                # Return a new JSON dictionary wrapping the state
                return json.dumps({
                    "__META__": f"{omitted} older objects omitted to save context.",
                    "objects": recent_objects
                })

        # If it's a dict (property-based state)
        if isinstance(parsed_state, dict):
            # If the number of keys is <= max_items, return the original JSON string
            if len(parsed_state) <= max_items:
                return state_str

            # If > max_items, extract the LAST max_items (most recently added geometry)
            if len(parsed_state) > max_items:
                # Get the last max_items keys (most recent objects)
                recent_keys = list(parsed_state.keys())[-max_items:]
                omitted_count = len(parsed_state) - max_items

                # Build new dict with meta-key and recent objects only
                summarized = {
                    "__META__": f"{omitted_count} older objects omitted to save context."}
                for key in recent_keys:
                    if key in parsed_state:
                        summarized[key] = parsed_state[key]

                return json.dumps(summarized)

        # Fallback: return as-is
        return state_str

    def handle_message(self, user_message: str):
        """Process a user message using a ReAct scratchpad pattern.

        Long-term memory (self.history): Stores ONLY user prompts and final agent responses.
        Short-term scratchpad (local variable): Stores ReAct loop internals (tool calls, results).
        The scratchpad is discarded after each handle_message call, keeping history clean.
        """
        # Prevent unbounded context growth (keep last 20 messages max)
        MAX_HISTORY = 20
        if len(self.history) > MAX_HISTORY:
            self.history = self.history[-MAX_HISTORY:]

        # Append user message to long-term history
        self.history.append({"role": "user", "content": user_message.strip()})

        # Short-term scratchpad for this ReAct loop execution
        scratchpad = []

        # Accumulates every tool the agent executed across this session/prompt.
        session_tools: list = []

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
                state_json = "[]"

            # 2a. Parse state into objects for router-based tool gating.
            try:
                state_objects = json.loads(state_json)
            except (json.JSONDecodeError, TypeError):
                state_objects = []

            # 2b. Gate the tool schemas to only those relevant to current state.
            tools = self.router.filter_tools(all_tools, state_objects)
            print(
                f"[Agent] Step {step+1}: {len(tools)}/{len(all_tools)} tools active after routing")

            # 3. Summarize state to prevent context exhaustion (Context Guard)
            summarized_state = self._summarize_state(state_json)

            # 4. Build dynamic system prompt with current state + ReAct discipline
            dynamic_system = SYSTEM_PROMPT + \
                f"\n\nCURRENT CAD STATE:\n{summarized_state}\n\n{REACT_LOOP_INJECTION}"

            # 5. Build messages: [system] + history + scratchpad
            messages = [{"role": "system", "content": dynamic_system}
                        ] + self.history + scratchpad

            # 6. Get intent from LLM
            print(f"[Agent] Calling LLM with {len(tools)} tools available...")
            response = self.provider.generate_with_tools(
                messages=messages, tools=tools
            )

            # 7. If LLM returns plain text (NO tool calls): agent is done
            if not getattr(response, "tool_calls", None):
                reply = response.content or "Done."
                print(
                    f"[Agent] Finished reasoning (no tool calls). Final response: {reply}")
                # Append final response to long-term history
                self.history.append({"role": "assistant", "content": reply})
                return reply, session_tools

            # 8. LLM returned tool calls - append assistant message to scratchpad
            scratchpad.append({
                "role": "assistant",
                "content": None,
                "tool_calls": response.tool_calls
            })

            # 9. Execute tool calls through the adapter
            results = []

            for tc in response.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments)
                # Record this session's tool usage.
                session_tools.append(name)
                print(
                    f"[Execution] Step {step+1}: Tool '{name}' with args: {args}")
                try:
                    out = self.adapter.execute_command(name, **args)
                    results.append(out)
                    print(
                        f"[Execution] Step {step+1}: Tool '{name}' succeeded: {out}")
                except Exception as e:
                    error_msg = f"Execution error on {name}: {e}"
                    results.append(error_msg)
                    print(
                        f"\033[91m[ERROR] Step {step+1}: Tool '{name}' failed: {e}\033[0m")

            # 10. Append tool results to scratchpad as tool messages
            for i, tc in enumerate(response.tool_calls):
                scratchpad.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": results[i]
                })

            # 10a. Runtime geometry verification: after a tool execution, check
            # the NEW CAD state for degenerate geometry and feed a structured
            # warning back to the LLM so it can self-correct.
            try:
                new_state_json = self.adapter.get_state()
                new_state = json.loads(new_state_json)
            except Exception as e:
                print(
                    f"[Agent] Warning: Failed to get state for verification: {e}")
                new_state = []

            errors = check_geometry(new_state)
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

            # 11. Loop repeats - do NOT return to user yet
            print(
                f"[Agent] Step {step+1} complete. Continuing to next step...")

        # Max steps reached
        fail_msg = f"Operation incomplete: maximum reasoning steps ({self.MAX_STEPS}) reached."
        print(f"\033[91m[ERROR] {fail_msg}\033[0m")
        # Append failure message to long-term history
        self.history.append({"role": "assistant", "content": fail_msg})
        return fail_msg, session_tools
