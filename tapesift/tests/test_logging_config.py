"""Regression coverage for TapeSift's Windows desktop logging bootstrap."""

from __future__ import annotations

import logging

from tapesift.core import logging_config


def test_setup_logging_without_stderr_keeps_pythonw_startup_alive(
        monkeypatch, tmp_path):
    """The desktop shortcut runs under pythonw, where stderr is ``None``."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    root.handlers = []
    monkeypatch.setattr(logging_config.paths, "logs_dir", lambda: tmp_path)
    monkeypatch.setattr(logging_config.sys, "stderr", None)

    try:
        logging_config.setup_logging()
        logging.getLogger("tapesift.test").info("pythonw launch survived")

        console_handlers = [
            handler for handler in root.handlers
            if type(handler) is logging.StreamHandler
        ]
        assert console_handlers == []
        assert "pythonw launch survived" in (
            tmp_path / "tapesift.log").read_text(encoding="utf-8")
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers = original_handlers
        root.setLevel(original_level)
