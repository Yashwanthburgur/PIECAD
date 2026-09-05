"""PieCAD Core Orchestrator. CAD-agnostic."""
import json
from typing import Optional
from providers.llm.provider import LLMProvider
from core.adapters.interfaces import CADAdapter

SYSTEM_PROMPT = """You are PieCAD, a production-grade mechanical engineering AI agent. 
You control a live FreeCAD document via tool calls. 

CRITICAL RULES:
1. STATE AWARENESS: You will be provided with the CURRENT CAD STATE. Never guess object names. Always reference exact names and dimensions from the state.
2. NO SPAMMING: Never create duplicate base geometry (e.g., Box001, Box002) to fix a mistake. If a user asks you to fix or "undo" something, use the `delete_object` tool or use `set_param` to modify the existing object.
3. FIXING ERRORS: If a user gives you a physically impossible command (e.g., Fillet radius 50 on a 20mm box) and tells you to "fix it" or "do what is suitable", you must apply the correct modification to the EXISTING object (e.g., execute a fillet with a 5mm radius on the original box). Do NOT spawn a new box.
4. UNDO REQUESTS: If the user says "undo", look at the most recent object in the state and use `delete_object` to remove it.
5. CONCISENESS: Do not output long conversational apologies. Just execute the tool calls to fix the geometry.
"""


class CADAgent:
    MAX_RETRIES = 3

    def __init__(self, adapter: CADAdapter, provider: Optional[LLMProvider] = None):
        self.adapter = adapter
        self.provider = provider or LLMProvider()
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def _summarize_state(self, state_str: str, max_items: int = 10) -> str:
        """Summarize CAD state to prevent context exhaustion on large assemblies.

        Handles both JSON dict (property-based) and JSON list (object-based) states.
        For lists, keeps only the most recently added max_items objects.
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

    def handle_message(self, user_message: str) -> str:
        self.messages.append({"role": "user", "content": user_message.strip()})

        # Multi-step ReAct loop: max 10 steps to prevent infinite looping
        for step in range(10):
            print(f"\n--- [ReAct Step {step+1}/10] ---")

            # 1. Ask adapter for its active tools
            tools = self.adapter.get_tools()
            print(
                f"[Agent] Analyzing current CAD state... ({len(tools)} tools available)")

            # 2. Get current CAD state
            try:
                state_json = self.adapter.get_state()
            except Exception:
                state_json = "[]"

            # 3. Summarize state to prevent context exhaustion (Context Guard)
            summarized_state = self._summarize_state(state_json)

            # 4. Inject summarized state into LLM context so it knows what exists
            self.messages.append(
                {"role": "system", "content": f"CURRENT CAD STATE: {summarized_state}"}
            )

            # 5. Get intent from LLM
            print(f"[Agent] Calling LLM with {len(tools)} tools available...")
            response = self.provider.generate_with_tools(
                messages=self.messages, tools=tools
            )

            # 6. If LLM returns plain text (NO tool calls): agent is done
            if not getattr(response, "tool_calls", None):
                reply = response.content or "Done."
                print(f"[Agent] Finished reasoning. Final response: {reply}")
                self.messages.append({"role": "assistant", "content": reply})
                return reply

            # 7. Execute tool calls through the adapter
            results = []

            for tc in response.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments)
                print(
                    f"[Execution] Agent decided to use tool: {name} with args: {args}")
                try:
                    out = self.adapter.execute_command(name, args)
                    results.append(out)
                    print(f"[Execution] Tool '{name}' succeeded: {out}")
                except Exception as e:
                    error_msg = f"Execution error on {name}: {e}"
                    results.append(error_msg)
                    print(f"\033[91m[ERROR] Tool '{name}' failed: {e}\033[0m")

            # 8. Append tool execution results (success or error) to message history
            # The LLM will read these in the next iteration and decide its next move
            summary = "\n".join(results)
            self.messages.append({"role": "assistant", "content": summary})

            # 9. Loop repeats - do NOT return to user yet

        # Max steps reached
        fail_msg = "Operation incomplete: maximum reasoning steps (10) reached."
        print(f"\033[91m[ERROR] {fail_msg}\033[0m")
        self.messages.append({"role": "assistant", "content": fail_msg})
        return fail_msg
