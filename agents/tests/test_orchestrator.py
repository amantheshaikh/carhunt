import sys
import os
import time
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from orchestrator import _run_phase, _generate_summary, watchdog_event, _start_watchdog


def test_run_phase_success():
    def test_fn():
        return "success"
    assert _run_phase(test_fn, 5, "test") == "success"


def test_run_phase_timeout():
    def slow_fn():
        time.sleep(2)
        return "slow"
    # Set a 1-second timeout, so it should trigger TimeoutError
    assert _run_phase(slow_fn, 1, "test_timeout") is None


def test_run_phase_exception():
    def failing_fn():
        raise ValueError("test failure")
    # Should catch exception, log traceback, and return None
    assert _run_phase(failing_fn, 5, "test_fail") is None


def test_generate_summary_missing_api_key():
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": ""}):
        summary = _generate_summary({}, {}, 0, 10.0)
        assert summary is None


def test_watchdog_cancellation():
    watchdog_event.clear()
    t = _start_watchdog("test-run-id")
    assert t.is_alive()
    
    # Cancel watchdog immediately
    watchdog_event.set()
    time.sleep(0.1)
    
    # watchdog thread should return and terminate cleanly
    assert not t.is_alive()
