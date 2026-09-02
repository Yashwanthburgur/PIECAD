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

        # 2. Get intent from LLM
        response = self.provider.generate_with_tools(messages=self.messages, tools=tools)

        if not getattr(response, "tool_calls", None):
            reply = response.content or "Done."
            self.messages.append({"role": "assistant", "content": reply})
            return reply

        # 3. Execute tool calls through the adapter
        results = []
        for tc in response.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            try:
                out = self.adapter.execute_command(name, args)
                results.append(out)
            except Exception as e:
                results.append(f"Execution error on {name}: {e}")

        summary = "\n".join(results)
        self.messages.append({"role": "assistant", "content": summary})
        return summary