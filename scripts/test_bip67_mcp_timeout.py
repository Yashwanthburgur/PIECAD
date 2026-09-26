#!/usr/bin/env python
"""BIP 6.7 — MCP Tool Execution Timeout / Hang Protection Test.

Validates that the _MCPWorker enforces a timeout on MCP tool execution.
"""

from adapters.freecad.adapter import _MCPWorker, _DEFAULT_MCP_TIMEOUT
import sys
import time
import asyncio
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock, AsyncMock

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
adapters_path = PROJECT_ROOT / "adapters"
sys.path.insert(0, str(adapters_path))


def test_mcp_worker_execute_tool_succeeds_before_timeout():
    """Test that execute_tool returns normally when the tool completes before timeout."""
    worker = _MCPWorker()

    # Mock the event loop and _submit_async
    mock_loop = Mock()
    mock_loop.is_running.return_value = True
    worker._loop = mock_loop
    worker._started.set()

    # Mock _submit_async to return quickly
    with patch.object(worker, '_submit_async', return_value='{"success": true, "result": "ok"}') as mock_submit:
        result = worker.execute_tool(
            "test_tool", {"arg": "value"}, timeout=5.0)

        assert result == '{"success": true, "result": "ok"}'
        mock_submit.assert_called_once()
        # Verify timeout was passed
        call_args = mock_submit.call_args
        assert call_args[1]['timeout'] == 5.0

    print("✓ test_mcp_worker_execute_tool_succeeds_before_timeout passed")


def test_mcp_worker_execute_tool_timeout():
    """Test that execute_tool returns a structured timeout error when the tool exceeds timeout."""
    worker = _MCPWorker()

    # Mock the event loop
    mock_loop = Mock()
    mock_loop.is_running.return_value = True
    worker._loop = mock_loop
    worker._started.set()

    # Mock _submit_async to raise asyncio.TimeoutError
    def mock_submit_async(coro, timeout=60.0):
        raise asyncio.TimeoutError("Operation timed out")

    with patch.object(worker, '_submit_async', side_effect=mock_submit_async):
        with patch.object(worker, '_record_mcp_result') as mock_record:
            result = worker.execute_tool(
                "slow_tool", {"arg": "value"}, timeout=2.0)

            # Should return structured timeout error JSON
            import json
            parsed = json.loads(result)
            assert parsed["success"] is False
            assert "error_type" in parsed
            assert parsed["error_type"] == "mcp_timeout"
            assert "timed out" in parsed["error"].lower()
            assert parsed["timeout_seconds"] == 2.0
            mock_record.assert_called_with(False)

    print("✓ test_mcp_worker_execute_tool_timeout passed")


def test_mcp_worker_execute_tool_transport_error_retry():
    """Test that transport errors still trigger retry (BIP 6.4 behavior preserved)."""
    worker = _MCPWorker()

    mock_loop = Mock()
    mock_loop.is_running.return_value = True
    worker._loop = mock_loop
    worker._started.set()

    call_count = [0]

    def mock_submit_async(coro, timeout=60.0):
        call_count[0] += 1
        if call_count[0] == 1:
            raise Exception("Broken pipe: stdio transport closed")
        else:
            return '{"success": true, "result": "retry ok"}'

    with patch.object(worker, '_submit_async', side_effect=mock_submit_async):
        with patch.object(worker, '_record_mcp_result') as mock_record:
            result = worker.execute_tool(
                "test_tool", {"arg": "value"}, timeout=5.0)

            assert call_count[0] == 2  # Initial + retry
            assert result == '{"success": true, "result": "retry ok"}'
            # Should record False on first attempt, True on retry
            assert mock_record.call_count == 2

    print("✓ test_mcp_worker_execute_tool_transport_error_retry passed")


def test_execute_tool_uses_default_timeout():
    """Test that execute_tool uses _DEFAULT_MCP_TIMEOUT when no timeout provided."""
    from adapters.freecad.adapter import _DEFAULT_MCP_TIMEOUT
    worker = _MCPWorker()

    mock_loop = Mock()
    mock_loop.is_running.return_value = True
    worker._loop = mock_loop
    worker._started.set()

    captured_timeout = {}

    def mock_submit_async(coro, timeout=60.0):
        captured_timeout['timeout'] = timeout
        return '{"success": true}'

    with patch.object(worker, '_submit_async', side_effect=mock_submit_async):
        # No explicit timeout
        worker.execute_tool("test_tool", {"arg": "value"})
        assert captured_timeout['timeout'] == _DEFAULT_MCP_TIMEOUT

        # Test with explicit timeout
        worker.execute_tool("test_tool", {"arg": "value"}, timeout=30.0)
        assert captured_timeout['timeout'] == 30.0

    print("✓ test_execute_tool_uses_default_timeout passed")


def test_run_mcp_tool_passes_timeout():
    """Test that FreeCADAdapter._run_mcp_tool passes timeout to worker."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(
        0, str(Path(__file__).resolve().parent.parent / "adapters"))

    from adapters.freecad.adapter import FreeCADAdapter, _DEFAULT_MCP_TIMEOUT

    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    with patch('adapters.freecad.adapter._get_mcp_worker') as mock_get_worker:
        mock_worker = Mock()
        mock_worker.execute_tool.return_value = '{"success": true}'
        mock_get_worker.return_value = mock_worker

        # Test default timeout
        adapter._run_mcp_tool("test_tool", {"arg": "value"})
        call_args = mock_worker.execute_tool.call_args
        assert call_args[1]['timeout'] == _DEFAULT_MCP_TIMEOUT

        # Test custom timeout
        adapter._run_mcp_tool("test_tool", {"arg": "value"}, timeout=45.0)
        call_args = mock_worker.execute_tool.call_args
        assert call_args[1]['timeout'] == 45.0

    print("✓ test_run_mcp_tool_passes_timeout passed")


def test_execute_command_passes_timeout_to_mcp_tools():
    """Test that execute_command extracts _timeout and passes to MCP tools."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(
        0, str(Path(__file__).resolve().parent.parent / "adapters"))

    from adapters.freecad.adapter import FreeCADAdapter, _DEFAULT_MCP_TIMEOUT

    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    # Mock the MCP tool execution
    with patch.object(adapter, '_run_mcp_tool', return_value='{"success": true}') as mock_run:
        with patch.object(adapter, '_call_proxy', return_value='success'):
            # Test with default timeout for MCP tool
            adapter.execute_command(
                "partdesign_sketch_constraint", arg="value")
            call_args = mock_run.call_args
            assert call_args[1]['timeout'] == _DEFAULT_MCP_TIMEOUT

            # Test with custom timeout for MCP tool
            adapter.execute_command(
                "partdesign_sketch_constraint", arg="value", _timeout=45.0)
            call_args = mock_run.call_args
            assert call_args[1]['timeout'] == 45.0

    print("✓ test_execute_command_passes_timeout_to_mcp_tools passed")


if __name__ == "__main__":
    test_mcp_worker_execute_tool_succeeds_before_timeout()
    test_mcp_worker_execute_tool_timeout()
    test_mcp_worker_execute_tool_transport_error_retry()
    test_execute_tool_uses_default_timeout()
    test_run_mcp_tool_passes_timeout()
    test_execute_command_passes_timeout_to_mcp_tools()

    print("\n✓ All BIP 6.7 MCP timeout/hang protection tests passed!")
