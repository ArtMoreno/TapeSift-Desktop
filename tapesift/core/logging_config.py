"""Rotating file logging for TapeSift."""

from __future__ import annotations

import logging
import logging.handlers
import platform
import tempfile
import sys
from pathlib import Path

from tapesift import __version__
from tapesift.core import paths
from tapesift.core import crash_reporter

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def _choose_log_file() -> Path | None:
    candidates = [
        paths.logs_dir(),
        Path(tempfile.gettempdir()) / "TapeSift" / "Logs",
    ]

    for log_dir in candidates:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "tapesift.log"
            log_file.touch(exist_ok=True)
            return log_file
        except OSError:
            continue
    return None


def setup_logging(level: int = logging.INFO) -> None:
    handlers = []

    log_file = _choose_log_file()
    if log_file is not None:
        try:
            handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter(LOG_FORMAT))
            handlers.append(handler)
        except OSError:
            pass

    if not handlers:
        handlers.append(logging.NullHandler())

    # ``pythonw.exe`` deliberately starts without stdout/stderr.  Attaching a
    # StreamHandler to that missing stream lets the first log record reach the
    # rotating file and then aborts desktop-shortcut startup while trying to
    # write to ``None``.  Keep console diagnostics for terminal launches, but
    # make the normal Windows desktop entry point file-log-only.
    console = None
    if sys.stderr is not None and hasattr(sys.stderr, "write"):
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter(LOG_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    for handler in handlers:
        root.addHandler(handler)
    if console is not None:
        root.addHandler(console)

    log = logging.getLogger("tapesift")
    log.info("=== TapeSift %s starting ===", __version__)
    log.info("Python %s on %s %s", platform.python_version(), platform.system(), platform.release())


def install_excepthook() -> None:
    """Log uncaught Python exceptions and persist them as crash reports.

    Delegates to ``crash_reporter`` so Python-level uncaught exceptions and
    native crashes are recorded in the same place (``paths.crash_dir``).
    """
    crash_reporter.install_crash_reporter()
