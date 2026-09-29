"""Tests for A3.4: Persistent per-turn run logging to JSONL."""
import json
import tempfile
import os
import shutil
from unittest.mock import MagicMock, patch
from pathlib import Path

from fastapi.testclient import TestClient

from core.agent import CADAgent
from core.adapters.interfaces import CADAdapter
from core import api as api_module


class DummyAdapter(CADAdapter):
    """Minimal adapter for API testing."""

    def __init__(self):
        self._tools = []
        self._state = "[]"
        self._rpc_count = 0

    def get_tools(self):
        return self._tools

    def get_state(self):
        return self._state

    def execute_command(self, name, **kwargs):
        if name == "box":
            return '{"success": true, "id": "box1"}'
        return '{"success": true}'

    def get_rpc_count(self) -> int:
        return self._rpc_count

    def reset_rpc_count(self) -> None:
        self._rpc_count = 0


class ScriptedProvider:
    """Provider that returns canned responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.last_usage = None

    def generate_with_tools(self, messages, tools):
        if not self.responses:
            raise RuntimeError("No more scripted responses")
        tool_calls, content = self.responses.pop(0)
        response = MagicMock()
        response.tool_calls = tool_calls
        response.content = content
        return response


def test_a34_creates_jsonl_record():
    """Verify one completed /chat turn creates one JSONL record."""
    # Create temp directory for runs
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a test runs directory
        test_runs_dir = Path(tmpdir) / "runs"
        test_runs_dir.mkdir()

        adapter = DummyAdapter()
        provider = ScriptedProvider([
            ([MagicMock(function=MagicMock(
                name="box", arguments='{"id": "box1"}'))], None),
            ([], "Done."),  # Final response to complete the turn successfully
        ])
        agent = CADAgent(adapter=adapter, provider=provider)

        # Change working directory to temp dir so "runs/" resolves there
        old_cwd = os.getcwd()
        os.chdir(tmpdir)

        try:
            with patch("core.api.agent", agent):
                client = TestClient(api_module.app)

                response = client.post(
                    "/chat", json={"message": "Create a box"})
                assert response.status_code == 200

                # Check that runs/turns.jsonl was created
                log_file = test_runs_dir / "turns.jsonl"
                assert log_file.exists(), "runs/turns.jsonl should be created"

                # Read and parse the line
                lines = log_file.read_text(
                    encoding="utf-8").strip().split("\n")
                assert len(lines) == 1, "Should have exactly one line"

                record = json.loads(lines[0])

                # Verify required fields
                assert "timestamp" in record
                assert "prompt" in record
                assert "steps" in record
                assert "total_tokens" in record
                assert "rpc_trips" in record
                assert "duration_seconds" in record
                assert "success" in record
                assert "termination_reason" in record

                # Verify values
                assert record["prompt"] == "Create a box"
                assert record["success"] is True
                assert record["termination_reason"] == "success"
                assert isinstance(record["steps"], int)
                assert isinstance(record["total_tokens"], int)
                assert isinstance(record["rpc_trips"], int)
                assert isinstance(record["duration_seconds"], float)

                print(
                    "  [PASS] One turn creates one valid JSONL record with all required fields")
        finally:
            os.chdir(old_cwd)


def test_a34_multiple_turns_append_lines():
    """Verify multiple turns append multiple independent lines."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_runs_dir = Path(tmpdir) / "runs"
        test_runs_dir.mkdir()

        adapter = DummyAdapter()
        provider = ScriptedProvider([
            ([MagicMock(function=MagicMock(
                name="box", arguments='{"id": "box1"}'))], None),
            ([MagicMock(function=MagicMock(
                name="cylinder", arguments='{"id": "cyl1"}'))], None),
            ([], "Done."),
        ])
        agent = CADAgent(adapter=adapter, provider=provider)

        old_cwd = os.getcwd()
        os.chdir(tmpdir)

        try:
            with patch("core.api.agent", agent):
                client = TestClient(api_module.app)

                # First turn
                response1 = client.post(
                    "/chat", json={"message": "Create a box"})
                assert response1.status_code == 200

                # Second turn
                response2 = client.post(
                    "/chat", json={"message": "Create a cylinder"})
                assert response2.status_code == 200

                # Third turn (no tool calls)
                response3 = client.post("/chat", json={"message": "Done"})
                assert response3.status_code == 200

                # Check log file
                log_file = test_runs_dir / "turns.jsonl"
                lines = log_file.read_text(
                    encoding="utf-8").strip().split("\n")
                assert len(
                    lines) == 3, f"Should have 3 lines, got {len(lines)}"

                # Verify each line is valid JSON and has correct prompt
                prompts = []
                for line in lines:
                    record = json.loads(line)
                    prompts.append(record["prompt"])

                assert prompts == ["Create a box", "Create a cylinder", "Done"]

                print(
                    "  [PASS] Multiple turns append multiple independent JSONL lines")
        finally:
            os.chdir(old_cwd)


def test_a34_each_line_valid_json():
    """Verify each line parses as valid JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_runs_dir = Path(tmpdir) / "runs"
        test_runs_dir.mkdir()

        adapter = DummyAdapter()
        provider = ScriptedProvider([
            ([MagicMock(function=MagicMock(
                name="box", arguments='{"id": "box1"}'))], None),
        ])
        agent = CADAgent(adapter=adapter, provider=provider)

        old_cwd = os.getcwd()
        os.chdir(tmpdir)

        try:
            with patch("core.api.agent", agent):
                client = TestClient(api_module.app)

                response = client.post(
                    "/chat", json={"message": "Test prompt"})
                assert response.status_code == 200

                log_file = test_runs_dir / "turns.jsonl"
                lines = log_file.read_text(
                    encoding="utf-8").strip().split("\n")

                for line in lines:
                    # This should not raise
                    record = json.loads(line)
                    assert isinstance(record, dict)

                print("  [PASS] Each line parses as valid JSON")
        finally:
            os.chdir(old_cwd)


def test_a34_required_fields_present():
    """Verify all required telemetry fields are present."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_runs_dir = Path(tmpdir) / "runs"
        test_runs_dir.mkdir()

        adapter = DummyAdapter()
        provider = ScriptedProvider([
            ([MagicMock(function=MagicMock(
                name="box", arguments='{"id": "box1"}'))], None),
        ])
        agent = CADAgent(adapter=adapter, provider=provider)

        old_cwd = os.getcwd()
        os.chdir(tmpdir)

        try:
            with patch("core.api.agent", agent):
                client = TestClient(api_module.app)

                response = client.post("/chat", json={"message": "Test"})
                assert response.status_code == 200

                log_file = test_runs_dir / "turns.jsonl"
                record = json.loads(log_file.read_text().strip())

                required_fields = [
                    "timestamp", "prompt", "steps", "total_tokens",
                    "rpc_trips", "duration_seconds", "success", "termination_reason"
                ]
                for field in required_fields:
                    assert field in record, f"Missing required field: {field}"

                print("  [PASS] All required fields present in JSONL record")
        finally:
            os.chdir(old_cwd)


def test_a34_runs_dir_created_automatically():
    """Verify runs/ directory is created automatically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_runs_dir = Path(tmpdir) / "runs"

        # Ensure runs dir doesn't exist yet
        assert not test_runs_dir.exists()

        adapter = DummyAdapter()
        provider = ScriptedProvider([
            ([MagicMock(function=MagicMock(
                name="box", arguments='{"id": "box1"}'))], None),
        ])
        agent = CADAgent(adapter=adapter, provider=provider)

        old_cwd = os.getcwd()
        os.chdir(tmpdir)

        try:
            with patch("core.api.agent", agent):
                client = TestClient(api_module.app)

                response = client.post("/chat", json={"message": "Test"})
                assert response.status_code == 200

                # Check runs dir was created
                assert test_runs_dir.exists(), "runs/ directory should be created automatically"
                assert test_runs_dir.is_dir()

                print("  [PASS] runs/ directory created automatically")
        finally:
            os.chdir(old_cwd)


def test_a34_logging_failure_does_not_fail_request():
    """Verify a logging/write failure does not fail the API request."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_runs_dir = Path(tmpdir) / "runs"
        test_runs_dir.mkdir()

        adapter = DummyAdapter()
        provider = ScriptedProvider([
            ([MagicMock(function=MagicMock(
                name="box", arguments='{"id": "box1"}'))], None),
        ])
        agent = CADAgent(adapter=adapter, provider=provider)

        old_cwd = os.getcwd()
        os.chdir(tmpdir)

        try:
            with patch("core.api.agent", agent):
                with patch("builtins.open", side_effect=PermissionError("Permission denied")):
                    client = TestClient(api_module.app)

                    # This should NOT raise, even though logging fails
                    response = client.post("/chat", json={"message": "Test"})
                    assert response.status_code == 200

                    data = response.json()
                    assert "reply" in data
                    assert "telemetry" in data

                    print(
                        "  [PASS] Logging failure does not fail the API request")
        finally:
            os.chdir(old_cwd)


def test_a34_error_turn_logged_correctly():
    """Verify failed turns are logged with success=false and proper termination_reason."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_runs_dir = Path(tmpdir) / "runs"
        test_runs_dir.mkdir()

        adapter = DummyAdapter()
        provider = ScriptedProvider([
            # Provider returns empty responses, causing max_steps exhaustion
        ])
        agent = CADAgent(adapter=adapter, provider=provider)

        old_cwd = os.getcwd()
        os.chdir(tmpdir)

        try:
            with patch("core.api.agent", agent):
                client = TestClient(api_module.app)

                response = client.post("/chat", json={"message": "Test"})
                assert response.status_code == 200

                log_file = test_runs_dir / "turns.jsonl"
                record = json.loads(log_file.read_text().strip())

                # Should be marked as failure
                assert record["success"] is False
                assert record["termination_reason"] in [
                    "max_steps", "error", "llm_error"]

                print(
                    f"  [PASS] Error turn logged correctly: success={record['success']}, reason={record['termination_reason']}")
        finally:
            os.chdir(old_cwd)


if __name__ == "__main__":
    test_a34_creates_jsonl_record()
    test_a34_multiple_turns_append_lines()
    test_a34_each_line_valid_json()
    test_a34_required_fields_present()
    test_a34_runs_dir_created_automatically()
    test_a34_logging_failure_does_not_fail_request()
    test_a34_error_turn_logged_correctly()
    print("\nAll A3.4 turn logging tests passed!")
