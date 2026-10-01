"""A7.3 — Deterministic evaluation fixtures tests.

Proves the seven scripted fixtures added to tests/eval_fixtures.json are scored
correctly by the existing evaluator, fully offline, and reproducibly.

No live LLM is used: each fixture carries ``scripted_responses`` and is scored
against an in-process deterministic adapter. If a live provider were required,
CADAgent would construct LLMProvider() and raise (no API key present).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.adapters.interfaces import CADAdapter  # noqa: E402
from scripts.run_evals import evaluate_fixture  # noqa: E402

FIXTURES_PATH = Path(__file__).resolve().parent / "eval_fixtures.json"

DETERMINISTIC_IDS = {
    "scripted_happy_path",
    "scripted_multi_step",
    "scripted_tool_failure",
    "scripted_retry_recovery",
    "termination_valid_final_mutation",
    "termination_post_final_verification_rejection",
    "termination_intermediate_verification_valid",
}

_STATE = json.dumps([
    {"id": "box1", "label": "box1", "shape_type": "Solid", "visible": True},
])
_MASS = json.dumps({
    "volume": 1000.0, "Volume": 1000.0,
    "bounding_box": {"XMin": 0, "XMax": 10, "YMin": 0, "YMax": 10,
                     "ZMin": 0, "ZMax": 10},
})


class StatefulStubAdapter(CADAdapter):
    """Deterministic adapter with a visible solid so state-aware assertions run.

    ``fail_tool`` always fails; ``retry_tool`` fails on the first call and
    succeeds thereafter (so the scripted two-phase retry fixture is honest).
    """

    def __init__(self):
        self._calls = {}

    def get_tools(self):
        return [{"type": "function",
                 "function": {"name": "box", "parameters": {"type": "object"}}}]

    def get_state(self):
        return _STATE

    def execute_command(self, name, **kwargs):
        n = self._calls.get(name, 0)
        self._calls[name] = n + 1
        if name in ("get_mass_properties", "get_faces", "get_edges"):
            return _MASS
        if name == "fail_tool":
            return json.dumps({"success": False, "error": "nothing to do",
                               "error_type": "ValueError"})
        if name == "retry_tool":
            if n == 0:
                return json.dumps({"success": False, "error": "radius too large",
                                   "error_type": "ValueError"})
            return json.dumps({"success": True, "id": "retry_tool1"})
        return json.dumps({"success": True, "id": f"{name}1"})


def _load_fixture(fid):
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    for fx in fixtures:
        if fx.get("id") == fid:
            return fx
    raise AssertionError(f"fixture {fid!r} not found in eval_fixtures.json")


def test_deterministic_fixtures_declare_scripted_responses():
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    by_id = {fx.get("id"): fx for fx in fixtures}
    for fid in DETERMINISTIC_IDS:
        assert fid in by_id, f"missing deterministic fixture {fid}"
        assert by_id[fid].get("scripted_responses"), (
            f"{fid} must declare scripted_responses")


def test_scripted_happy_path_scores_offline():
    res = evaluate_fixture(_load_fixture("scripted_happy_path"),
                           StatefulStubAdapter())
    assert res["passed"] is True
    assert res["tools_passed"] is True
    assert res["assertions_passed"] is True
    assert "box" in [t.lower() for t in res["got"]]


def test_scripted_multi_step_scores_tool_coverage():
    res = evaluate_fixture(_load_fixture("scripted_multi_step"),
                           StatefulStubAdapter())
    assert res["passed"] is True
    assert res["tools_passed"] is True
    got = [t.lower() for t in res["got"]]
    assert "box" in got and "cylinder" in got


def test_scripted_tool_failure_scores_via_tool_error():
    res = evaluate_fixture(_load_fixture("scripted_tool_failure"),
                           StatefulStubAdapter())
    assert res["passed"] is True
    assert res["assertions_passed"] is True
    assert res["failed_assertions"] == []


def test_scripted_retry_recovery_scores_via_retry_pattern():
    res = evaluate_fixture(_load_fixture("scripted_retry_recovery"),
                           StatefulStubAdapter())
    assert res["passed"] is True
    assert res["assertions_passed"] is True
    assert res["failed_assertions"] == []


def test_termination_valid_final_mutation_scores():
    res = evaluate_fixture(_load_fixture("termination_valid_final_mutation"),
                           StatefulStubAdapter())
    assert res["passed"] is True
    assert res["tools_passed"] is True
    assert res["assertions_passed"] is True
    assert "box" in [t.lower() for t in res["got"]]


def test_termination_post_final_verification_rejection_scores():
    """This fixture should FAIL the termination guard assertion."""
    res = evaluate_fixture(_load_fixture("termination_post_final_verification_rejection"),
                           StatefulStubAdapter())
    assert res["passed"] is False
    assert res["failure_category"] == "assertion"
    assert any("termination_guard" in str(fa)
               for fa in res["failed_assertions"])
    assert any("get_mass_properties" in str(fa)
               for fa in res["failed_assertions"])


def test_termination_intermediate_verification_valid_scores():
    res = evaluate_fixture(_load_fixture("termination_intermediate_verification_valid"),
                           StatefulStubAdapter())
    assert res["passed"] is True
    assert res["tools_passed"] is True
    assert res["assertions_passed"] is True
    got = [t.lower() for t in res["got"]]
    assert "box" in got
    assert "get_mass_properties" in got
    assert "cylinder" in got


def test_deterministic_fixtures_are_reproducible():
    """Two consecutive evaluations of each fixture yield identical outcomes."""
    for fid in DETERMINISTIC_IDS:
        fx = _load_fixture(fid)
        first = evaluate_fixture(fx, StatefulStubAdapter())
        second = evaluate_fixture(fx, StatefulStubAdapter())
        # termination_post_final_verification_rejection is designed to fail
        expected_passed = fid != "termination_post_final_verification_rejection"
        assert first["passed"] == second["passed"] == expected_passed, fid
        assert first["got"] == second["got"], fid
        assert first["failure_category"] == second["failure_category"], fid


def test_non_scripted_fixtures_unchanged():
    """The pre-existing fixtures must not have gained scripted_responses."""
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    scripted = {fx.get("id")
                for fx in fixtures if fx.get("scripted_responses")}
    assert scripted == DETERMINISTIC_IDS


if __name__ == "__main__":  # pragma: no cover
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
