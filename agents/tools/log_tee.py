"""Shared stdout/stderr tee — writes to both the original streams and the dashboard log file."""
import sys

REFRESH_LOG = "/tmp/carhunt-refresh.log"


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for s in self._streams:
            try:
                s.write(data)
            except Exception:
                pass

    def flush(self):
        for s in self._streams:
            try:
                s.flush()
            except Exception:
                pass

    def reconfigure(self, **kw):
        pass  # satisfy reconfigure() calls on the original stdout reference


def enable_log_tee() -> None:
    """Append all stdout/stderr to the shared refresh log so the dashboard SSE stream picks it up."""
    try:
        log = open(REFRESH_LOG, "a", buffering=1)
        sys.stdout = _Tee(sys.__stdout__, log)
        sys.stderr = _Tee(sys.__stderr__, log)
    except Exception:
        pass  # non-fatal — dashboard just won't show this run's logs
