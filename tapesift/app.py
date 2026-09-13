"""Shared application identity and project-file argument parsing."""

from __future__ import annotations

import ctypes
import logging
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from tapesift.database.connection import SUPPORTED_PROJECT_FILE_EXTENSIONS

log = logging.getLogger("tapesift")

APP_USER_MODEL_ID = "IFI.TapeSift.Desktop"
APP_ICON_PATH = (
    Path(__file__).resolve().parent / "resources" / "icons" / "tapesift.ico"
)


def _install_windows_app_identity() -> None:
    """Give every TapeSift launch mode one stable Windows shell identity."""
    if sys.platform != "win32":
        return
    try:
        result = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            APP_USER_MODEL_ID
        )
    except (AttributeError, OSError):
        log.debug("Windows AppUserModelID is unavailable", exc_info=True)
        return
    if result != 0:
        log.debug("Windows rejected AppUserModelID with HRESULT %s", result)


def _apply_application_identity(app: QApplication) -> None:
    """Apply the unified Timeline T icon and TapeSift application metadata."""
    app.setApplicationName("TapeSift")
    app.setApplicationDisplayName("TapeSift")
    app.setOrganizationName("TapeSift")
    app.setDesktopFileName("tapesift")
    if APP_ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(APP_ICON_PATH)))


def _project_from_args(args: list[str]) -> Path | None:
    """The project file this launch was asked to open, if any.

    The installer registers both current and legacy project extensions, so
    double-clicking either kind starts the app with its path as an argument.
    """
    for arg in args:
        if arg.startswith("-"):
            continue
        candidate = Path(arg)
        if (candidate.suffix.lower() in SUPPORTED_PROJECT_FILE_EXTENSIONS
                and candidate.is_file()):
            return candidate
    return None
