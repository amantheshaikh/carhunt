import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from io import StringIO
from unittest.mock import patch

from tools.log_tee import _Tee, enable_log_tee


def test_tee_write_fans_out_to_all_streams():
    s1, s2 = StringIO(), StringIO()
    tee = _Tee(s1, s2)
    tee.write("hello")
    assert s1.getvalue() == "hello"
    assert s2.getvalue() == "hello"


def test_tee_write_swallows_exception_from_one_stream():
    class Bad:
        def write(self, data):
            raise IOError("closed")
    s_ok = StringIO()
    tee = _Tee(Bad(), s_ok)
    tee.write("data")  # should not raise
    assert s_ok.getvalue() == "data"


def test_tee_flush_fans_out_and_swallows_exceptions():
    class Bad:
        def flush(self):
            raise IOError("closed")
    calls = []
    class Ok:
        def flush(self):
            calls.append(True)
    tee = _Tee(Bad(), Ok())
    tee.flush()  # should not raise
    assert calls == [True]


def test_tee_reconfigure_is_noop():
    tee = _Tee(StringIO())
    assert tee.reconfigure(line_buffering=True) is None


def test_enable_log_tee_wraps_stdout_and_stderr():
    fake_log = StringIO()
    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    try:
        with patch("builtins.open", return_value=fake_log):
            enable_log_tee()
        assert isinstance(sys.stdout, _Tee)
        assert isinstance(sys.stderr, _Tee)
    finally:
        sys.stdout, sys.stderr = orig_stdout, orig_stderr


def test_enable_log_tee_swallows_open_failure():
    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    try:
        with patch("builtins.open", side_effect=OSError("permission denied")):
            enable_log_tee()  # should not raise
        # stdout/stderr left untouched since open() failed before reassignment
        assert sys.stdout is orig_stdout
        assert sys.stderr is orig_stderr
    finally:
        sys.stdout, sys.stderr = orig_stdout, orig_stderr
