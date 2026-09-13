"""HWND-level native frame behaviors on a real Windows Qt session.

The main suite runs on the offscreen platform, where the frameless flag is
deliberately skipped, so this is the only place the native frame work is
exercised. Skips everywhere else.

Deliberately builds ONE minimal window for the whole module: the V2
multi-window segfault family (see docs/TESTING.md) is a Windows teardown-order
problem, and a single throwaway QMainWindow keeps this file out of that
lottery while still probing the real Win32 behaviors.
"""

from __future__ import annotations

import ctypes
import sys

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QPushButton, QWidget

from tapesift.ui_v2.windows_frame import install_native_frame

pytestmark = [
    pytest.mark.skipif(
        sys.platform != "win32",
        reason="HWND-level behavior is Windows-only"),
    # The suite-wide teardown destroys top-level widgets after every test.
    # This file shares one window across the module on purpose (see above),
    # so it opts out rather than losing it after the first test.
    pytest.mark.keep_widgets,
]

WS_THICKFRAME = 0x00040000
GWL_STYLE = -16
WM_NCHITTEST = 0x0084
HTCLIENT = 1
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

if sys.platform == "win32":
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    user32.GetWindowLongPtrW.restype = ctypes.c_longlong
    user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_ssize_t]
    user32.SetWindowPos.restype = ctypes.c_int
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.GetWindowRect.restype = ctypes.c_int
    user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    user32.IsZoomed.restype = ctypes.c_int
    user32.IsZoomed.argtypes = [ctypes.c_void_p]
    dwmapi.DwmGetWindowAttribute.restype = ctypes.c_int
    dwmapi.DwmGetWindowAttribute.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]



class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


def _pump(app, ms: int = 80) -> None:
    """Process Qt events for ``ms`` milliseconds."""
    from PySide6.QtTest import QTest
    QTest.qWait(ms)


def _hwnd(window) -> ctypes.c_void_p:
    return ctypes.c_void_p(int(window.winId()))


def _hittest(hwnd, x: int, y: int) -> int:
    packed = (y << 16) | (x & 0xFFFF)
    return int(user32.SendMessageW(hwnd, WM_NCHITTEST, 0, packed))


def _window_rect(hwnd) -> RECT:
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect


@pytest.fixture(scope="module")
def framed_window():
    app = QApplication.instance() or QApplication([])
    if app.platformName() != "windows":
        pytest.skip("requires the real Windows QPA platform")

    window = QMainWindow()
    window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
    window.resize(800, 600)

    shell = QWidget(window)
    shell.setObjectName("TestMasthead")
    shell.setFixedHeight(56)
    button = QPushButton("X", shell)
    button.setObjectName("TestWindowButton")
    button.setFixedSize(38, 32)
    window.setMenuWidget(shell)

    assert install_native_frame(window, shell) is True
    window.show()
    _pump(app)
    # Place the button at the shell's right after layout settles.
    button.move(shell.width() - 46, 12)
    _pump(app)

    yield window, shell, button

    window.close()
    _pump(app, 40)
    window.deleteLater()
    _pump(app, 40)


def test_styles_include_thickframe(framed_window) -> None:
    window, _, _ = framed_window
    style = user32.GetWindowLongPtrW(_hwnd(window), GWL_STYLE)
    assert style & WS_THICKFRAME


def test_dwm_corner_preference_round(framed_window) -> None:
    window, _, _ = framed_window
    preference = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(
        _hwnd(window), DWMWA_WINDOW_CORNER_PREFERENCE,
        ctypes.byref(preference), ctypes.sizeof(preference))
    assert preference.value == DWMWCP_ROUND


def test_all_edges_and_corners_resize(framed_window) -> None:
    window, _, _ = framed_window
    app = QApplication.instance()
    window.showNormal()
    _pump(app)
    hwnd = _hwnd(window)
    rect = _window_rect(hwnd)
    width = rect.right - rect.left
    height = rect.bottom - rect.top

    assert _hittest(hwnd, rect.left + width // 2, rect.top + 1) == HTTOP
    assert _hittest(hwnd, rect.left + width // 2, rect.bottom - 1) == HTBOTTOM
    assert _hittest(hwnd, rect.left + 1, rect.top + height // 2) == HTLEFT
    assert _hittest(hwnd, rect.right - 1, rect.top + height // 2) == HTRIGHT
    assert _hittest(hwnd, rect.left + 1, rect.top + 1) == HTTOPLEFT
    assert _hittest(hwnd, rect.right - 1, rect.top + 1) == HTTOPRIGHT
    assert _hittest(hwnd, rect.left + 1, rect.bottom - 1) == HTBOTTOMLEFT
    assert _hittest(hwnd, rect.right - 1, rect.bottom - 1) == HTBOTTOMRIGHT


def test_title_bar_drags_and_children_stay_clickable(framed_window) -> None:
    window, shell, button = framed_window
    app = QApplication.instance()
    window.showNormal()
    _pump(app)
    hwnd = _hwnd(window)
    origin = window.mapToGlobal(QPoint(0, 0))
    shell_geo = shell.geometry()

    # Empty masthead space drags the window.
    drag_x = origin.x() + shell_geo.x() + 20
    drag_y = origin.y() + shell_geo.y() + 20
    assert _hittest(hwnd, drag_x, drag_y) == HTCAPTION

    # A control inside the masthead stays clickable.
    center = button.geometry().center()
    button_x = origin.x() + center.x()
    button_y = origin.y() + center.y()
    assert _hittest(hwnd, button_x, button_y) == HTCLIENT

    # Ordinary client area below the masthead is plain client.
    client_y = origin.y() + window.height() - 30
    assert _hittest(hwnd, origin.x() + window.width() // 2, client_y) == HTCLIENT


def test_native_resize_changes_geometry(framed_window) -> None:
    window, _, _ = framed_window
    app = QApplication.instance()
    window.showNormal()
    _pump(app)
    hwnd = _hwnd(window)
    rect = _window_rect(hwnd)
    target_width = rect.right - rect.left + 120
    target_height = rect.bottom - rect.top + 60
    user32.SetWindowPos(
        hwnd, 0, rect.left, rect.top, target_width, target_height,
        SWP_NOZORDER | SWP_NOACTIVATE)
    _pump(app)
    assert window.width() == target_width
    assert window.height() == target_height


def test_no_resize_ring_while_maximized(framed_window) -> None:
    window, _, _ = framed_window
    app = QApplication.instance()
    window.showMaximized()
    _pump(app)
    hwnd = _hwnd(window)
    rect = _window_rect(hwnd)
    height = rect.bottom - rect.top
    assert window.isMaximized()
    # Qt maximizes frameless windows by resizing to the work area, so the
    # native IsZoomed flag stays clear; the resize ring must be off
    # regardless (the left edge must not answer as a resize handle).
    assert _hittest(hwnd, rect.left + 1, rect.top + height // 2) != HTLEFT
    window.showNormal()
    _pump(app)
    assert not window.isMaximized()


def test_maximize_restore_minimize_cycle(framed_window) -> None:
    window, _, _ = framed_window
    app = QApplication.instance()
    window.showNormal()
    _pump(app)
    geometry_before = window.geometry()

    window.showMaximized()
    _pump(app)
    assert window.isMaximized()

    window.showNormal()
    _pump(app)
    assert not window.isMaximized()
    assert window.geometry() == geometry_before

    window.showMinimized()
    _pump(app)
    assert window.isMinimized()

    window.showNormal()
    _pump(app)
    assert not window.isMinimized()
