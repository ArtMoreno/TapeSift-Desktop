"""Bootstrap for the TapeSift desktop application."""

from __future__ import annotations

from tapesift.app_v2 import run as run_v2
from tapesift.ui_v3.main_window import MainWindowV3
from tapesift.ui_v3.theme import stylesheet


def run() -> int:
    return run_v2(
        window_class=MainWindowV3,
        stylesheet_factory=stylesheet,
    )
