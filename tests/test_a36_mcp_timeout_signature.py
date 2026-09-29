"""Regression test for A3.6: MCP timeout signature mismatch fix.

This test verifies that FreeCADAdapter._execute_mcp_tool accepts the
timeout and operation_id arguments without raising TypeError.
"""
import json
from unittest.mock import MagicMock, patch

from adapters.freecad.adapter import FreeCADAdapter, _MCPWorker, _get_mcp_worker


def test_execute_mcp_tool_accepts_timeout_and_operation_id():
    """Verify _execute_mcp_tool accepts timeout and operation_id without TypeError."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    # Mock the worker to avoid actual MCP connection
    mock_worker = MagicMock(spec=_MCPWorker)
    mock_worker.execute_tool.return_value = '{"success": true, "id": "test1"}'

    with patch("adapters.freecad.adapter._get_mcp_worker", return_value=mock_worker):
        # This call used to raise TypeError: got an unexpected keyword argument 'timeout'
        result = adapter._execute_mcp_tool(
            name="test_tool",
            args={"param": "value"},
            timeout=120.0,
            operation_id="op_123"
        )

        # Verify the call was made with correct arguments
        mock_worker.execute_tool.assert_called_once_with(
            "test_tool", {"param": "value"}, timeout=120.0, operation_id="op_123"
        )

        # Verify result is returned
        assert result == '{"success": true, "id": "test1"}'
        print("  [PASS] _execute_mcp_tool accepts timeout and operation_id")


def test_execute_mcp_tool_default_timeout():
    """Verify _execute_mcp_tool works with default timeout."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    mock_worker = MagicMock(spec=_MCPWorker)
    mock_worker.execute_tool.return_value = '{"success": true}'

    with patch("adapters.freecad.adapter._get_mcp_worker", return_value=mock_worker):
        # Call with defaults (no timeout/operation_id)
        result = adapter._execute_mcp_tool(
            name="test_tool",
            args={"param": "value"}
        )

        mock_worker.execute_tool.assert_called_once()
        call_kwargs = mock_worker.execute_tool.call_args
        assert call_kwargs is not None
        # Should use default timeout
        assert call_kwargs[1]['timeout'] == 120.0  # _DEFAULT_MCP_TIMEOUT
        print("  [PASS] _execute_mcp_tool works with default timeout")


def test_worker_execute_tool_signature():
    """Verify _MCPWorker.execute_tool signature accepts timeout and operation_id."""
    # This tests the actual signature at the worker level
    from adapters.freecad.adapter import _MCPWorker
    import inspect

    sig = inspect.signature(_MCPWorker.execute_tool)
    params = list(sig.parameters.keys())

    assert "timeout" in params, "Worker.execute_tool must accept timeout"
    assert "operation_id" in params, "Worker.execute_tool must accept operation_id"

    print("  [PASS] _MCPWorker.execute_tool signature is correct")


if __name__ == "__main__":
    test_execute_mcp_tool_accepts_timeout_and_operation_id()
    test_execute_mcp_tool_default_timeout()
    test_worker_execute_tool_signature()
    print("\nAll A3.6 MCP timeout signature tests passed!")
