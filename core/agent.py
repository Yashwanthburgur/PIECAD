"""PieCAD Core Agent Loop."""

import json
from typing import Any, Dict, List, Tuple
from providers.llm.provider import LLMProvider

CAD_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "create_box",
            "description": "Create a 3D box solid in the active FreeCAD document.",
            "parameters": {
                "type": "object",
                "properties": {
                    "length": {"type": "number", "description": "Length along X axis in mm"},
                    "width": {"type": "number", "description": "Width along Y axis in mm"},
                    "height": {"type": "number", "description": "Height along Z axis in mm"},
                },
                "required": ["length", "width", "height"],
            },
        },
    }
]

SYSTEM_PROMPT = """You are PieCAD, an expert CAD modeling assistant.
When a user asks to generate or manipulate geometry, invoke the relevant CAD tool.
Be concise and confirm actions clearly."""


class CADAgent:
    def __init__(self):
        self.provider = LLMProvider()
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

    def handle_message(self, user_message: str) -> Tuple[str, List[Dict[str, Any]]]:
        self.messages.append({"role": "user", "content": user_message})

        response_msg = self.provider.generate_with_tools(
            messages=self.messages,
            tools=CAD_TOOLS
        )

        tool_calls_list = []
        if response_msg.tool_calls:
            for tc in response_msg.tool_calls:
                tool_calls_list.append({
                    "name": tc.function.name,
                    "args": json.loads(tc.function.arguments)
                })
            reply = f"Generated {len(tool_calls_list)} CAD action(s)."
        else:
            reply = response_msg.content or "Done."

        self.messages.append({"role": "assistant", "content": reply})
        return reply, tool_calls_list