"""Generic LLM Provider Interface and Implementation."""

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from openai import OpenAI
from dotenv import load_dotenv

env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


class LLMProvider:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        raw_key = (
            api_key
            or os.getenv("DEEPSEEK_API_KEY")
            or os.getenv("GROQ_API_KEY")
            or os.getenv("API_KEY")
            or os.getenv("OPENAI_API_KEY")
        )
        self.api_key = raw_key.strip() if raw_key else None

        raw_base_url = base_url or os.getenv(
            "LLM_BASE_URL", "https://api.deepseek.com")
        self.base_url = raw_base_url.strip() if raw_base_url else None

        raw_model = model or os.getenv("LLM_MODEL", "deepseek-chat")
        self.model = raw_model.strip() if raw_model else None

        if not self.api_key:
            raise ValueError(
                "No API key found. Set GROQ_API_KEY (or API_KEY) in .env")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
        )

        # Exact provider token usage from the most recent generate_with_tools
        # call (None when the provider does not report usage).
        self.last_usage: Optional[Dict[str, Any]] = None

    def generate_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """Sends chat messages and optional tool definitions to the LLM.

        The return value (the assistant message) is unchanged for backward
        compatibility. Exact provider token usage, when reported by the API, is
        stored on ``self.last_usage`` (a dict with prompt_tokens /
        completion_tokens / total_tokens) or None when unavailable.
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        response = self.client.chat.completions.create(**kwargs)
        message = response.choices[0].message

        # Capture exact provider token usage if the API reports it.
        self.last_usage = None
        if hasattr(response, 'usage') and response.usage:
            usage = {
                "prompt_tokens": getattr(response.usage, 'prompt_tokens', None),
                "completion_tokens": getattr(response.usage, 'completion_tokens', None),
                "total_tokens": getattr(response.usage, 'total_tokens', None),
            }
            usage = {k: v for k, v in usage.items() if v is not None}
            if usage:
                self.last_usage = usage

        return message
