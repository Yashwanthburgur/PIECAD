#!/usr/bin/env python
"""BIP 6.6 — Tool Execution Timeout / Hang Protection Test.

Validates that the adapter and bridge enforce timeouts on long-running operations.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from adapters.freecad.adapter import FreeCADAdapter
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
adapters_path = PROJECT_ROOT / "adapters"
sys.path.insert(0, str(adapters_path))


def test_call_proxy_with_timeout_succeeds():
    """Test that _call_proxy_with_timeout returns normally for fast calls."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    # Mock _call_proxy to return quickly
    with patch.object(adapter, '_call_proxy', return_value="success") as mock_call:
        result = adapter._call_proxy_with_timeout("test_method", 5.0, "arg1")

        assert result == "success"
        mock_call.assert_called_once_with("test_method", "arg1")

    print("✓ test_call_proxy_with_timeout_succeeds passed")


def test_call_proxy_with_timeout_raises_on_slow_call():
    """Test that _call_proxy_with_timeout raises RuntimeError on timeout."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    # Mock _call_proxy to sleep longer than timeout
    def slow_call(*args, **kwargs):
        time.sleep(2.0)
        return "success"

    with patch.object(adapter, '_call_proxy', side_effect=slow_call):
        try:
            adapter._call_proxy_with_timeout("test_method", 0.5, "arg1")
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "timed out" in str(e).lower()
            assert "test_method" in str(e)

    print("✓ test_call_proxy_with_timeout_raises_on_slow_call passed")


def test_execute_command_extracts_timeout():
    """Test that execute_command extracts _timeout from kwargs."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    # Mock the tool execution to capture timeout
    captured_timeout = {}

    def mock_call_proxy_with_timeout(method_name, timeout, *args, **kwargs):
        captured_timeout['timeout'] = timeout
        return "success"

    with patch.object(adapter, '_call_proxy_with_timeout', side_effect=mock_call_proxy_with_timeout):
        with patch.object(adapter, '_call_proxy', return_value="success"):
            # Test with default timeout
            adapter.execute_command(
                "box", id="test", length=10, width=10, height=10)
            assert captured_timeout['timeout'] == 120.0

            # Test with custom timeout
            adapter.execute_command(
                "box", id="test2", length=10, width=10, height=10, _timeout=30.0)
            assert captured_timeout['timeout'] == 30.0

    print("✓ test_execute_command_extracts_timeout passed")


def test_bridge_timeout_constant():
    """Test that the bridge has the timeout constant."""
    import adapters.freecad.bridge as bridge_module

    # Check the constant exists
    assert hasattr(bridge_module, '_DEFAULT_EXECUTION_TIMEOUT')
    assert bridge_module._DEFAULT_EXECUTION_TIMEOUT == 120.0

    print("✓ test_bridge_timeout_constant passed")


def test_execute_command_passes_timeout_to_bridge():
    """Test that execute_command passes _timeout to bridge operations."""
    import adapters.freecad.bridge as bridge_module

    # Mock the bridge _execute_on_main_thread to capture timeout
    original_execute = bridge_module._execute_on_main_thread
    captured_kwargs = {}

    def mock_execute(op_name, *args, **kwargs):
        captured_kwargs['op_name'] = op_name
        captured_kwargs['kwargs'] = kwargs
        return "success"

    with patch.object(bridge_module, '_execute_on_main_thread', side_effect=mock_execute):
        adapter = FreeCADAdapter(host="127.0.0.1", port=9876)
        adapter.execute_command(
            "box", id="test", length=10, width=10, height=10, _timeout=45.0)

        # The bridge call should have received _timeout in kwargs
        assert '_timeout' in captured_kwargs.get('kwargs', {})
        assert captured_kwargs['kwargs']['_timeout'] == 45.0

    print("✓ test_execute_command_passes_timeout_to_bridge passed")


if __name__ == "__main__":
    test_call_proxy_with_timeout_succeeds()
    test_call_proxy_with_timeout_raises_on_slow_call()
    test_execute_command_extracts_timeout()
    test_bridge_timeout_constant()
    test_execute_command_passes_timeout_to_bridge()

    print("\n✓ All BIP 6.6 timeout/hang protection tests passed!")
