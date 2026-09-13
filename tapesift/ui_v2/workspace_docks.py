"""Small Qt helpers for saving and recovering the dock workspace."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence

from PySide6.QtCore import QByteArray, QRect, Qt
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QApplication, QDockWidget, QMainWindow


# Version 2 removes free-floating utility panels. Bumping the native Qt state
# version prevents a saved Clips/Details/Tag Map window from the experimental
# layout being restored off-screen after the stable layout ships.
WORKSPACE_STATE_VERSION = 2

# A floating panel is recoverable only when enough of its title bar remains
# on-screen to grab and move it. Checking the whole frame would incorrectly
# accept a window whose body is visible but whose title bar is inaccessible.
REACHABLE_TITLE_BAR_WIDTH = 64
REACHABLE_TITLE_BAR_HEIGHT = 32


def encode_qbytearray(value: QByteArray) -> str:
    """Return *value* as canonical ASCII Base64 for JSON persistence."""
    return base64.b64encode(bytes(value)).decode("ascii")


def decode_qbytearray(value: object) -> QByteArray | None:
    """Decode canonical Base64, returning ``None`` for absent/corrupt input."""
    if not isinstance(value, str) or not value:
        return None
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error):
        return None
    if not decoded or base64.b64encode(decoded) != encoded:
        return None
    return QByteArray(decoded)


def available_screens() -> tuple[QScreen, ...]:
    """Return the screens currently known to the active Qt application."""
    app = QApplication.instance()
    return tuple(app.screens()) if app is not None else ()


def geometry_has_reachable_title_bar(
        geometry: QRect,
        screens: Sequence[QScreen] | None = None) -> bool:
    """Whether a useful portion of a floating frame's title bar is on-screen."""
    if not geometry.isValid():
        return False

    title_bar_height = min(
        REACHABLE_TITLE_BAR_HEIGHT, geometry.height())
    required_width = min(REACHABLE_TITLE_BAR_WIDTH, geometry.width())
    title_bar = QRect(
        geometry.x(),
        geometry.y(),
        geometry.width(),
        title_bar_height,
    )
    active_screens = tuple(screens) if screens is not None \
        else available_screens()
    for screen in active_screens:
        visible = title_bar.intersected(screen.availableGeometry())
        if visible.width() >= required_width \
                and visible.height() >= title_bar_height:
            return True
    return False


def visible_floating_dock_is_reachable(
        dock: QDockWidget,
        screens: Sequence[QScreen] | None = None) -> bool:
    """Return true only for a visible floating dock with a reachable title bar."""
    return dock.isVisible() \
        and dock.isFloating() \
        and geometry_has_reachable_title_bar(dock.frameGeometry(), screens)


def redock_offscreen_floating_docks(
        window: QMainWindow,
        default_areas: Mapping[QDockWidget, Qt.DockWidgetArea],
        screens: Sequence[QScreen] | None = None) -> list[str]:
    """Redock visible off-screen floating docks and return their object names.

    Hidden docks are intentionally left alone: their visibility is persisted
    workspace state, not evidence that their saved geometry is broken.
    """
    active_screens = tuple(screens) if screens is not None \
        else available_screens()
    changed: list[str] = []
    for dock, area in default_areas.items():
        if not dock.isVisible() or not dock.isFloating():
            continue
        # Windows can move a minimized native frame to a sentinel coordinate.
        # That is not a disconnected monitor, and redocking here would turn
        # an ordinary Minimize click into an unexpected layout reset.
        if dock.isMinimized():
            continue
        if geometry_has_reachable_title_bar(
                dock.frameGeometry(), active_screens):
            continue

        # Fullscreen survives setFloating(False) on some Qt platforms, while
        # minimized/maximized state can poison the geometry saved for the
        # redocked panel. Normalize before moving the same widget home.
        dock.showNormal()
        window.removeDockWidget(dock)
        window.addDockWidget(area, dock)
        dock.setFloating(False)
        dock.show()
        changed.append(dock.objectName())
    return changed


__all__ = [
    "REACHABLE_TITLE_BAR_HEIGHT",
    "REACHABLE_TITLE_BAR_WIDTH",
    "WORKSPACE_STATE_VERSION",
    "available_screens",
    "decode_qbytearray",
    "encode_qbytearray",
    "geometry_has_reachable_title_bar",
    "redock_offscreen_floating_docks",
    "visible_floating_dock_is_reachable",
]
