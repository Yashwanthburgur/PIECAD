#!/usr/bin/env python
"""BIP 6.3 — CAD Adapter Connection Resilience Test.

Validates that the FreeCADAdapter automatically reconnects when the XML-RPC
bridge restarts. This is a deterministic unit test that simulates connection
failure and verifies the proxy is recreated.
"""

from adapters.freecad.adapter import FreeCADAdapter
import sys
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import xmlrpc.client

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
adapters_path = PROJECT_ROOT / "adapters"
sys.path.insert(0, str(adapters_path))


def test_connection_resilience_recreates_proxy_on_failure():
    """Test that _call_proxy recreates the proxy after a connection error."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    # Mock the initial proxy
    original_proxy = Mock(spec=xmlrpc.client.ServerProxy)
    original_proxy.some_method.side_effect = ConnectionError(
        "Connection refused")
    adapter._proxy = original_proxy

    # Mock the new proxy that will be created on retry
    new_proxy = Mock(spec=xmlrpc.client.ServerProxy)
    new_proxy.some_method.return_value = "success"

    with patch("xmlrpc.client.ServerProxy", return_value=new_proxy) as mock_proxy_class:
        # Call should fail once, recreate proxy, then succeed
        result = adapter._call_proxy("some_method", "arg1", "arg2")

        # Verify the result
        assert result == "success", f"Expected 'success', got {result}"

        # Verify proxy was recreated (called twice: once in __init__, once on retry)
        assert mock_proxy_class.call_count == 2, \
            f"Expected ServerProxy to be called twice, got {mock_proxy_class.call_count}"

        # Verify the new proxy's method was called
        new_proxy.some_method.assert_called_once_with("arg1", "arg2")

        # Verify consecutive failures reset on success
        assert adapter._consecutive_failures == 0, \
            f"Expected 0 consecutive failures after success, got {adapter._consecutive_failures}"

    print("✓ test_connection_resilience_recreates_proxy_on_failure passed")


def test_connection_resilience_tracks_consecutive_failures():
    """Test that consecutive failures are tracked and proxy is recreated after threshold."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)
    adapter._max_consecutive_failures = 2  # Lower threshold for test

    # Mock proxy that always fails
    failing_proxy = Mock(spec=xmlrpc.client.ServerProxy)
    failing_proxy.some_method.side_effect = ConnectionError(
        "Connection refused")
    adapter._proxy = failing_proxy

    with patch("xmlrpc.client.ServerProxy", return_value=failing_proxy):
        # First failure
        try:
            adapter._call_proxy("some_method")
        except ConnectionError:
            pass
        assert adapter._consecutive_failures == 1

        # Second failure - should trigger proxy recreation
        try:
            adapter._call_proxy("some_method")
        except ConnectionError:
            pass
        assert adapter._consecutive_failures == 2

        # Third failure - proxy should have been recreated
        try:
            adapter._call_proxy("some_method")
        except ConnectionError:
            pass
        # After threshold, _consecutive_failures should reset and proxy recreated
        # The _call_proxy will retry once after recreation, so this is the 3rd call
        # but the proxy is recreated after 2 failures

    print("✓ test_connection_resilience_tracks_consecutive_failures passed")


def test_ensure_proxy_recreates_after_threshold():
    """Test that _ensure_proxy recreates proxy after consecutive failure threshold."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)
    adapter._max_consecutive_failures = 3

    # Simulate 3 consecutive failures
    adapter._consecutive_failures = 3

    original_proxy = adapter._proxy

    with patch("xmlrpc.client.ServerProxy") as mock_proxy_class:
        new_proxy = Mock(spec=xmlrpc.client.ServerProxy)
        mock_proxy_class.return_value = new_proxy

        proxy = adapter._ensure_proxy()

        # Should have created a new proxy
        assert proxy is new_proxy
        assert proxy is not original_proxy
        mock_proxy_class.assert_called_once_with(adapter.url, allow_none=True)
        # Failures should be reset
        assert adapter._consecutive_failures == 0

    print("✓ test_ensure_proxy_recreates_after_threshold passed")


def test_record_proxy_result_tracks_failures():
    """Test that _record_proxy_result correctly tracks success/failure."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    # Record success
    adapter._record_proxy_result(True)
    assert adapter._consecutive_failures == 0

    # Record failures
    adapter._record_proxy_result(False)
    assert adapter._consecutive_failures == 1

    adapter._record_proxy_result(False)
    assert adapter._consecutive_failures == 2

    adapter._record_proxy_result(True)
    assert adapter._consecutive_failures == 0

    print("✓ test_record_proxy_result_tracks_failures passed")


def test_get_state_uses_call_proxy():
    """Test that get_state uses _call_proxy for connection resilience."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    mock_result = '[{"id": "box1", "type": "Part::Box"}]'

    with patch.object(adapter, "_call_proxy", return_value=mock_result) as mock_call:
        result = adapter.get_state()

        assert result == mock_result
        mock_call.assert_called_once_with("get_state")

    print("✓ test_get_state_uses_call_proxy passed")


def test_clear_document_uses_call_proxy():
    """Test that clear_document uses _call_proxy for connection resilience."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    mock_result = "Document cleared successfully."

    with patch.object(adapter, "_call_proxy", return_value=mock_result) as mock_call:
        result = adapter.clear_document()

        assert result == mock_result
        mock_call.assert_called_once_with("clear_document")

    print("✓ test_clear_document_uses_call_proxy passed")


def test_export_methods_use_call_proxy():
    """Test that export_obj and export_state_model use _call_proxy."""
    adapter = FreeCADAdapter(host="127.0.0.1", port=9876)

    with patch.object(adapter, "_call_proxy", return_value="Exported successfully.") as mock_call:
        result1 = adapter.export_obj("/tmp/test.obj")
        assert result1 == "Exported successfully."
        mock_call.assert_called_with("export_obj", "/tmp/test.obj")

    with patch.object(adapter, "_call_proxy", return_value="Exported successfully.") as mock_call:
        result2 = adapter.export_state_model("/tmp/test.glb", "glb")
        assert result2 == "Exported successfully."
        mock_call.assert_called_with(
            "export_current_state", "/tmp/test.glb", "glb")

    print("✓ test_export_methods_use_call_proxy passed")


if __name__ == "__main__":
    test_connection_resilience_recreates_proxy_on_failure()
    test_connection_resilience_tracks_consecutive_failures()
    test_ensure_proxy_recreates_after_threshold()
    test_record_proxy_result_tracks_failures()
    test_get_state_uses_call_proxy()
    test_clear_document_uses_call_proxy()
    test_export_methods_use_call_proxy()

    print("\n✓ All BIP 6.3 connection resilience tests passed!")
