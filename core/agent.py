"""PieCAD Core Agent Loop. Pure orchestration."""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from providers.llm.provider import LLMProvider
from core.adapters.interfaces import CADAdapter

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are PieCAD, an expert mechanical engineering assistant.
You translate intent into structured tool calls.
If multiple steps are needed, sequence them properly. Always use the provided tools."""

class CADAgent:
    def __init__(self, adapter: CADAdapter, provider: Optional[LLMProvider] = None) -> None:
        self.provider = provider or LLMProvider()
        self.adapter = adapter
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    def handle_message(self, user_message: str) -> str:
        """Process user intent, call LLM, and execute via Adapter."""
        self.messages.append({"role": "user", "content": str(user_message).strip()})
        
        # 1. Ask adapter for capabilities
        tools = self.adapter.get_tools()

        # 2. Get LLM intent
        response_msg = self.provider.generate_with_tools(
            messages=self.messages,
            tools=tools,
        )

        # Handle conversational fallback
        if not hasattr(response_msg, "tool_calls") or not response_msg.tool_calls:
            reply = response_msg.content or "Done."
            self.messages.append({"role": "assistant", "content": reply})
            return reply

        # 3. Execute Intent through the Adapter
        execution_results = []
        assistant_payload = {"role": "assistant", "content": None, "tool_calls": []}
        
        for tc in response_msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            
            # Serialize for history
            assistant_payload["tool_calls"].append({
                "id": tc.id, "type": "function",
                "function": {"name": name, "arguments": tc.function.arguments}
            })

            # EXECUTION BOUNDARY: Hand off to CADAdapter
            try:
                result = self.adapter.execute_command(name, args)
                execution_results.append(result)
            except Exception as e:
                execution_results.append(f"Error executing {name}: {str(e)}")

        self.messages.append(assistant_payload)
        
        # For Sprint 3A, summarize results to the user
        summary = "\n".join(execution_results)
        return f"Execution complete:\n{summary}"