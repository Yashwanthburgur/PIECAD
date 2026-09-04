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

        # 1. Ask adapter for its active tools
        tools = self.adapter.get_tools()

        # 2. Get current CAD state
        try:
            state_json = self.adapter.get_state()
        except Exception:
            state_json = "[]"

        # 3. Summarize state to prevent context exhaustion
        summarized_state = self._summarize_state(state_json)

        # 4. Inject summarized state into LLM context so it knows what exists
        self.messages.append(
            {"role": "system", "content": f"CURRENT CAD STATE: {summarized_state}"}
        )

        # 4. Retry loop for self-correction
        for attempt in range(self.MAX_RETRIES):
            # Get intent from LLM
            response = self.provider.generate_with_tools(
                messages=self.messages, tools=tools)

            if not getattr(response, "tool_calls", None):
                reply = response.content or "Done."
                self.messages.append({"role": "assistant", "content": reply})
                return reply

            # Execute tool calls through the adapter
            results = []
            execution_failed = False
            last_error = ""

            for tc in response.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments)
                try:
                    out = self.adapter.execute_command(name, args)
                    results.append(out)
                except Exception as e:
                    results.append(f"Execution error on {name}: {e}")
                    execution_failed = True
                    last_error = str(e)

            # Check if any result contains "Error" or "Failed"
            has_error_in_result = any(
                "Error" in r or "Failed" in r for r in results
            )

            if execution_failed or has_error_in_result:
                # Inject surgical error message for the LLM to self-correct
                error_summary = last_error or "Unknown error in tool execution"
                self.messages.append(
                    {
                        "role": "system",
                        "content": f"Attempt {attempt+1} failed on tool execution. Error: {error_summary}. Adjust parameters and regenerate.",
                    }
                )
                # Continue the outer loop to re-trigger the LLM
                continue

            # All tools executed successfully
            summary = "\n".join(results)
            self.messages.append({"role": "assistant", "content": summary})
            return summary

        # Loop exhausted all retries
        fail_msg = (
            f"Operation failed after {self.MAX_RETRIES} attempts. "
            f"Last error: {last_error}"
        )
        self.messages.append({"role": "assistant", "content": fail_msg})
        return fail_msg
