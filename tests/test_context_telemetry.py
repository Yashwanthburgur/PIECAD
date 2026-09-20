"""Tests for ContextTelemetry / token estimation (BIP 4.2, Parts 13-14)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.context.telemetry import (  # noqa: E402
    ContextTelemetry,
    estimate_tokens,
    estimate_json_tokens,
    estimate_context_breakdown,
)


def test_context_token_estimation():
    # 100 chars / 4 = 25 tokens (heuristic).
    assert estimate_tokens("x" * 100) == 25
    assert estimate_tokens("") == 0
    assert estimate_json_tokens({"a": "b"}) > 0


def test_context_breakdown():
    breakdown = estimate_context_breakdown(
        context_text="hello world",
        tool_schema_text="",
        state_text="",
        memory_text="",
        conversation_text="",
    )
    assert "context" in breakdown
    assert "state" in breakdown
    assert "memory" in breakdown
    assert breakdown["context"] > 0


def test_dropped_context_reporting():
    telemetry = ContextTelemetry(
        estimated_context_tokens=2040,
        estimated_state_tokens=1240,
        estimated_memory_tokens=180,
        estimated_conversation_tokens=620,
        tools_exposed=8,
        dropped_sections=["old_history", "unrelated_objects"],
    )
    d = telemetry.to_dict()
    assert d["dropped_sections"] == ["old_history", "unrelated_objects"]
    assert d["estimated_context_tokens"] == 2040


def test_exact_vs_estimated_distinction():
    tel = ContextTelemetry(
        estimated_context_tokens=500,
        exact_provider_tokens={"input": 300, "output": 40},
    )
    d = tel.to_dict()
    # Estimates and exact provider counts are kept under separate keys.
    assert d["estimated_context_tokens"] == 500
    assert d["exact_provider_tokens"]["input"] == 300
    assert "exact_provider_tokens" in d
