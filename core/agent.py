"""PieCAD Core Orchestrator. CAD-agnostic."""
import json
from typing import Optional
from providers.llm.provider import LLMProvider
from core.adapters.interfaces import CADAdapter

SYSTEM_PROMPT = "You are PieCAD, an expert mechanical engineering copilot. Use the provided tools to generate CAD geometry."

class CADAgent:
    def __init__(self, adapter: CADAdapter, provider: Optional[LLMProvider] = None):
        self.adapter = adapter
        self.provider = provider or LLMProvider()
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def handle_message(self, user_message: str) -> str:
        self.messages.append({"role": "user", "content": user_message.strip()})

        # 1. Ask adapter for its active tools
        tools = self.adapter.get_tools()

        # 2. Get current CAD state
        try:
            state_json = self.adapter.get_state()
        except Exception:
            state_json = "[]"

        # 3. Inject state into LLM context so it knows what exists
        self.messages.append(
            {"role": "system", "content": f"CURRENT CAD STATE: {state_json}"}
        )

        # 4. Get intent from LLM
        response = self.provider.generate_with_tools(messages=self.messages, tools=tools)
        
        # 5. Execute the tools and return the result string
        if hasattr(response, "tool_calls") and response.tool_calls:
            results = []
            for tool_call in response.tool_calls:
                name = tool_call.function.name
                args = json.loads(tool_call.function.arguments)
                try:
                    res = self.adapter.execute_command(name, args)
                    results.append(res)
                except Exception as e:
                    results.append(f"Execution error on {name}: {e}")
            return "\n".join(results)
            
        # If it just wants to talk, return its text
        if hasattr(response, "content") and response.content:
            return response.content

        # Ultimate fallback so it NEVER returns None and crashes the API
        return str(response) or "Command processed."