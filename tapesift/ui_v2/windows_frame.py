"""Native Windows frame behaviors for the frameless TapeSift shell.

The V2 shell is deliberately frameless (``Qt.FramelessWindowHint``) so the
in-app masthead can carry the window controls. On Windows that removes the
native frame entirely: no resize borders, no edge snapping, and - on
Windows 11 - no DWM corner rounding, so the window reads as a square slab
that cannot be resized from any edge.

This module re-adds the *behaviors* of the native frame without bringing
back the native title bar:

* ``WS_THICKFRAME`` is restored on the HWND, giving native resize hit
  regions, correct resize cursors, Aero edge snapping, and snap layouts
  via drag-to-top.
* ``WM_NCCALCSIZE`` keeps the client area equal to the whole window, so
  the custom masthead layout is unchanged; while maximized the invisible
  border is inset so content is never clipped at the screen edges.
* ``WM_NCHITTEST`` routes the masthead band to ``HTCAPTION`` (native
  dragging, native double-click maximize/restore) while interactive
  children (menus, buttons, fields) stay clickable and the outer
  border ring resizes.
* The DWM corner preference is pinned to ``ROUND`` so Windows 11 rounds
  the window; DWM drops the radius automatically while maximized.
* ``WM_NCACTIVATE`` is consumed so the invisible frame never redraws.

Everything is inert off Windows and on non-"windows" Qt platforms, so
the offscreen test path and other platforms see no change at all.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import (
    QAbstractNativeEventFilter, QEvent, QObject, QPoint, Qt,
)
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractScrollArea,
    QAbstractSpinBox, QApplication, QComboBox, QLineEdit, QMenuBar,
    QSlider, QWidget,
)

# --- Win32 constants -------------------------------------------------------
WS_THICKFRAME = 0x00040000
GWL_STYLE = -16

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_NCACTIVATE = 0x0086

HTCAPTION = 2
HTCLIENT = 1
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

SWP_FRAMECHANGED = 0x0020
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010

SM_CXSIZEFRAME = 32
SM_CXPADDEDBORDER = 92

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2
DWMWCP_DONOTROUND = 1

# ctypes declarations -------------------------------------------------------
if sys.platform == "win32":
    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi

    user32.GetWindowLongPtrW.restype = ctypes.c_longlong
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_longlong
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int,
                                         ctypes.c_longlong]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.IsZoomed.restype = wintypes.BOOL
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.GetDpiForWindow.restype = wintypes.UINT
    user32.GetDpiForWindow.argtypes = [wintypes.HWND]

    dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long
    dwmapi.DwmExtendFrameIntoClientArea.argtypes = [
        wintypes.HWND, ctypes.c_void_p]
    dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
    dwmapi.DwmSetWindowAttribute.argtypes = [
        wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

    class MARGINS(ctypes.Structure):
        _fields_ = [
            ("cxLeftWidth", ctypes.c_int),
            ("cxRightWidth", ctypes.c_int),
            ("cyTopHeight", ctypes.c_int),
            ("cyBottomHeight", ctypes.c_int),
        ]

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long), ("top", ctypes.c_long),
            ("right", ctypes.c_long), ("bottom", ctypes.c_long),
        ]

    class NCCALCSIZE_PARAMS(ctypes.Structure):
        _fields_ = [
            ("rgrc", RECT * 3),
            ("lppos", ctypes.c_void_p),
        ]

    class MSG(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("message", wintypes.UINT),
            ("wParam", wintypes.WPARAM),
            ("lParam", wintypes.LPARAM),
            ("time", wintypes.DWORD),
            ("pt", wintypes.POINT),
        ]


def _platform_supported() -> bool:
    """True only on a real Windows Qt session (never offscreen tests)."""
    if sys.platform != "win32":
        return False
    app = QApplication.instance()
    if app is None:
        return False
    return app.platformName() == "windows"


_INTERACTIVE_TYPES = (
    QAbstractButton, QMenuBar, QLineEdit, QComboBox, QAbstractSpinBox,
    QAbstractScrollArea, QSlider, QAbstractItemView,
)


class _StyleGuard(QObject):
    """Re-asserts HWND styles when Qt changes the window state.

    Qt can re-apply HWND styles when the window state changes (e.g.
    fullscreen toggles), which would silently drop the thick frame.
    Separate from the native filter because PySide6 does not dispatch
    QAbstractNativeEventFilter virtuals on a QObject multiple-inheritance
    subclass; this plain QObject carries the Qt-side event filter.
    """

    def __init__(self, owner: "WindowsNativeFrameFilter") -> None:
        super().__init__()
        self._owner = owner

    def eventFilter(self, watched, event) -> bool:
        if (
            watched is self._owner._window
            and event.type() == QEvent.Type.WindowStateChange
            and self._owner._hwnd is not None
        ):
            try:
                self._owner._apply_styles(self._owner._hwnd)
            except (ValueError, TypeError, RuntimeError, OSError):
                pass
            self._sync_maximized_property()
        return False

    def _sync_maximized_property(self) -> None:
        """Toggle the QSS radius off while maximized.

        The window, masthead, and status bar each paint the corner pixels,
        so all three carry the ``tsMaximized`` property and re-polish so
        the corner radius rules in the stylesheet switch cleanly.
        """
        window = self._owner._window
        if window is None:
            return
        maximized = bool(window.isMaximized())
        widgets = [window, self._owner._title_bar]
        try:
            status_bar = window.statusBar()
            if status_bar is not None:
                widgets.append(status_bar)
        except RuntimeError:
            pass
        for widget in widgets:
            if widget is None:
                continue
            try:
                widget.setProperty("tsMaximized", maximized)
                style = widget.style()
                style.unpolish(widget)
                style.polish(widget)
            except RuntimeError:
                pass


class WindowsNativeFrameFilter(QAbstractNativeEventFilter):
    """One app-wide filter; only acts on the HWND it was attached to.

    Deliberately a plain QAbstractNativeEventFilter subclass: PySide6
    delivers native messages (as a usable pointer) only on this shape -
    a QObject multiple-inheritance subclass receives no native events at
    all. The Qt-side state-change guard lives in ``_StyleGuard``.
    """

    def __init__(self) -> None:
        super().__init__()
        self._hwnd: ctypes.c_void_p | None = None
        self._window: QWidget | None = None
        self._title_bar: QWidget | None = None
        self._guard: _StyleGuard | None = None

    # -- attach ---------------------------------------------------------
    def attach(self, window: QWidget, title_bar: QWidget) -> bool:
        """Apply the frame to ``window``; re-applies harmlessly on re-show.

        Returns True when the native frame is active for this window.
        """
        if not _platform_supported():
            return False
        try:
            raw_wid = window.winId()
            if isinstance(raw_wid, (bytes, bytearray)):
                hwnd = ctypes.c_void_p(int.from_bytes(raw_wid, "little"))
            elif raw_wid is None:
                return False
            else:
                hwnd = ctypes.c_void_p(int(raw_wid))
        except (ValueError, TypeError, RuntimeError):
            return False
        if hwnd.value == 0:
            return False
        self._window = window
        self._title_bar = title_bar
        self._hwnd = hwnd
        if self._guard is None:
            self._guard = _StyleGuard(self)
        try:
            window.installEventFilter(self._guard)
        except RuntimeError:
            pass
        self._apply_styles(hwnd)
        self._guard._sync_maximized_property()
        return True

    def _apply_styles(self, hwnd) -> None:
        style = user32.GetWindowLongPtrW(hwnd, GWL_STYLE)
        user32.SetWindowLongPtrW(hwnd, GWL_STYLE, style | WS_THICKFRAME)
        # DWM glass frame: on opaque windows it keeps the invisible border
        # rendered in the page color. Translucent windows skip the glass so
        # their own alpha composition is not disturbed.
        translucent = bool(
            self._window
            and self._window.testAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground))
        if not translucent:
            margins = MARGINS(-1, -1, -1, -1)
            dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        # Qt maximizes frameless windows by resizing to the work area
        # rather than setting WS_MAXIMIZE, so DWM never sees the window
        # as zoomed and would keep rounding it; pin the preference
        # explicitly on both sides of the state change.
        maximized = bool(self._window and self._window.isMaximized())
        preference = ctypes.c_int(
            DWMWCP_DONOTROUND if maximized else DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference), ctypes.sizeof(preference))
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE
            | SWP_NOZORDER | SWP_NOACTIVATE)

    # -- helpers ---------------------------------------------------------
    def _border(self) -> int:
        """Invisible resize border thickness, DPI-aware."""
        hwnd = self._hwnd
        if hwnd is None:
            return 8
        dpi = int(user32.GetDpiForWindow(hwnd) or 96)
        get_metrics = getattr(user32, "GetSystemMetricsForDpi", None)
        if get_metrics is None:
            return 8
        return (
            int(get_metrics(SM_CXSIZEFRAME, dpi))
            + int(get_metrics(SM_CXPADDEDBORDER, dpi)))

    def _in_title_bar(self, local_x: int, local_y: int,
                      screen_x: int, screen_y: int) -> bool:
        """True when the point should drag the window (HTCAPTION)."""
        shell = self._title_bar
        window = self._window
        if shell is None or window is None:
            return False
        try:
            if not shell.isVisible() or not shell.isEnabled():
                return False
            geo = shell.geometry()
        except RuntimeError:  # C++ object destroyed mid-probe
            return False
        if not (geo.x() <= local_x < geo.x() + geo.width()
                and geo.y() <= local_y < geo.y() + geo.height()):
            return False
        # Only empty shell space drags; anything interactive stays clickable.
        try:
            child = shell.childAt(QPoint(local_x - geo.x(), local_y - geo.y()))
        except RuntimeError:
            return False
        if child is None:
            return True
        if isinstance(child, _INTERACTIVE_TYPES):
            return False
        # Containers (identity hosts, action hosts) are drag surface.
        return True

    # -- message handling -------------------------------------------------
    def _on_nccalcsize(self, msg) -> int:
        # Keep client == window so the custom masthead layout is unchanged.
        # A native maximize (drag-to-top snap) overflows the work area by
        # the invisible border; inset the client so content is not clipped.
        # Qt's own showMaximized() resizes exactly to the work area, so
        # IsZoomed stays clear there and no inset is applied.
        if msg.wParam and user32.IsZoomed(self._hwnd):
            try:
                params = NCCALCSIZE_PARAMS.from_address(int(msg.lParam))
            except (ValueError, TypeError):
                return 0
            border = self._border()
            params.rgrc[0].left += border
            params.rgrc[0].top += border
            params.rgrc[0].right -= border
            params.rgrc[0].bottom -= border
        return 0

    def _on_nchittest(self, msg) -> int:
        packed = int(msg.lParam)
        screen_x = ctypes.c_short(packed & 0xFFFF).value
        screen_y = ctypes.c_short((packed >> 16) & 0xFFFF).value
        window = self._window
        if window is None:
            return HTCLIENT
        try:
            origin = window.mapToGlobal(QPoint(0, 0))
            local_x = screen_x - origin.x()
            local_y = screen_y - origin.y()
            width = window.width()
            height = window.height()
        except RuntimeError:
            return HTCLIENT

        if self._window is not None and self._window.isMaximized():
            # Maximized: no resize ring; the masthead still drags (so
            # drag-down restore works) and everything else is client.
            if self._in_title_bar(local_x, local_y, screen_x, screen_y):
                return HTCAPTION
            return HTCLIENT

        border = self._border()
        left = local_x < border
        right = local_x >= width - border
        top = local_y < border
        bottom = local_y >= height - border
        if left and top:
            return HTTOPLEFT
        if right and top:
            return HTTOPRIGHT
        if left and bottom:
            return HTBOTTOMLEFT
        if right and bottom:
            return HTBOTTOMRIGHT
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM
        if self._in_title_bar(local_x, local_y, screen_x, screen_y):
            return HTCAPTION
        return HTCLIENT

    # -- QAbstractNativeEventFilter --------------------------------------
    def nativeEventFilter(self, event_type, message) -> object:
        # The whole body is guarded: native events can arrive during
        # interpreter/teardown or re-entrantly mid-API-call, and a stray
        # exception here would be printed by Qt and can crash shutdown.
        try:
            if self._hwnd is None:
                return (False, 0)
            if isinstance(message, (bytes, bytearray)):
                if len(message) < ctypes.sizeof(MSG):
                    return (False, 0)
                msg = MSG.from_buffer_copy(message)
            else:
                msg = MSG.from_address(int(message))
            # msg.hwnd arrives as a plain int; self._hwnd is a c_void_p.
            # Python falls back to identity comparison across those types,
            # and int(c_void_p) misbehaves on this build, so compare on
            # .value explicitly.
            if msg.hwnd != self._hwnd.value:
                return (False, 0)
            if msg.message == WM_NCCALCSIZE:
                return (True, self._on_nccalcsize(msg))
            if msg.message == WM_NCHITTEST:
                return (True, self._on_nchittest(msg))
            if msg.message == WM_NCACTIVATE:
                # The frame is invisible; never let Windows repaint it.
                return (True, 1)
        except (ValueError, TypeError, RuntimeError, OSError):
            return (False, 0)
        return (False, 0)


_filter: WindowsNativeFrameFilter | None = None


def install_native_frame(window: QWidget, title_bar: QWidget) -> bool:
    """Install (once) and attach the native frame to ``window``.

    Idempotent and safe to call from every show: re-attaching re-applies
    the window styles, which also covers any HWND recreation.
    """
    global _filter
    if not _platform_supported():
        return False
    if _filter is None:
        _filter = WindowsNativeFrameFilter()
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(_filter)
    if _filter is None:
        return False
    return _filter.attach(window, title_bar)
