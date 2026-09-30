"""A7.2 — Deterministic, eval-only scripted LLM provider.

This module provides an offline ``ScriptedProvider`` that is drop-in compatible
with the ``LLMProvider`` interface consumed by ``CADAgent`` (via
``_generate_with_retry`` -> ``self.provider.generate_with_tools(...)``).

It mirrors the scripted-provider pattern already used across the test suite
(e.g. ``tests/test_context_error_recovery.py`` and ``tests/test_a34_turn_logging.py``)
so no new provider contract is invented and ``core/agent.py`` needs no change.

A script is an ordered sequence of ``(content, tool_calls)`` steps:

    * ``content``: assistant text (``str`` or ``None``) for that step.
    * ``tool_calls``: ``None`` for a plain-text/final response, or a list of
      ``(name, args_dict)`` pairs the agent should execute. ``args_dict`` is
      JSON-serialized into ``function.arguments`` exactly as the real provider
      would.

The provider advances one step per call, so a script like
``[(None, [("box", {...})]), ("Done.", None)]`` deterministically drives a
tool-call phase followed by a final-text phase that terminates the ReAct loop.

This is evaluation infrastructure only: it is not imported by core or adapters.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

# A single scripted step: (content, tool_calls)
Step = Tuple[Optional[str], Optional[List[Tuple[str, Dict[str, Any]]]]]


class ScriptedProvider:
    """Deterministic provider returning a fixed ordered sequence of responses.

    Compatible with the object shape expected by ``CADAgent``:

    * ``generate_with_tools(messages, tools=None)`` returns an object exposing
      ``.content`` and ``.tool_calls`` (each tool call exposing ``.id`` and
      ``.function.name`` / ``.function.arguments``).
    * ``last_usage`` is maintained in the same general shape expected by the
      existing telemetry (``prompt_tokens``/``completion_tokens``/``total_tokens``
      plus ``model``/``provider``), defaulting to ``None`` when unspecified.

    The last scripted step is repeated once the script is exhausted, so a
    terminal (plain-text) final step keeps returning text rather than raising
    and never hangs the ReAct loop. An explicitly empty script returns an empty
    final-text response deterministically.
    """

    def __init__(
        self,
        responses: List[Step],
        usage: Optional[Dict[str, Any]] = None,
    ) -> None:
        # Store as ``script`` to match the established test-suite pattern.
        self.script: List[Step] = list(responses)
        self.calls = 0
        # Optional fixed usage dict reported on every call (None => no usage).
        self.last_usage: Optional[Dict[str, Any]] = (
            dict(usage) if usage else None
        )

    # -- LLMProvider-compatible surface --------------------------------- #
    def generate_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Any:
        """Return the next scripted response deterministically.

        ``messages``/``tools`` are accepted for interface compatibility and
        intentionally ignored: responses come only from the fixed script.
        """
        if not self.script:
            # Empty script: deterministic empty, non-tool-call response.
            self.calls += 1
            return SimpleNamespace(content=None, tool_calls=None)

        idx = min(self.calls, len(self.script) - 1)
        self.calls += 1
        content, tool_calls = self.script[idx]

        tcs = None
        if tool_calls is not None:
            tcs = [
                SimpleNamespace(
                    id=f"call_{i}",
                    function=SimpleNamespace(
                        name=name,
                        arguments=json.dumps(args),
                    ),
                )
                for i, (name, args) in enumerate(tool_calls)
            ]

        return SimpleNamespace(content=content, tool_calls=tcs)


def provider_from_fixture(fixture: Dict[str, Any]) -> Optional[ScriptedProvider]:
    """Build a ``ScriptedProvider`` from ``fixture["scripted_responses"]``.

    Returns ``None`` when the fixture does not declare scripted responses, so
    the runner can preserve its existing (live-provider) behavior unchanged.

    Accepted ``scripted_responses`` forms:
      * a list of ``[tool_calls, content]`` pairs (the ``tests/`` style), where
        ``tool_calls`` is ``None`` or a list of ``[name, args]`` pairs; or
      * a list of ``{"tool_calls": ..., "content": ...}`` dicts.
    The optional ``fixture["scripted_usage"]`` supplies a fixed usage dict.
    """
    raw = fixture.get("scripted_responses")
    if raw is None:
        return None

    script: List[Step] = []
    for item in raw:
        if isinstance(item, dict):
            tool_calls = item.get("tool_calls")
            content = item.get("content")
        else:
            # [tool_calls, content] positional form.
            tool_calls, content = item[0], item[1]

        normalized_calls: Optional[List[Tuple[str, Dict[str, Any]]]] = None
        if tool_calls is not None:
            normalized_calls = [(name, args) for name, args in tool_calls]

        script.append((content, normalized_calls))

    return ScriptedProvider(script, usage=fixture.get("scripted_usage"))
