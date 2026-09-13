"""Focus ownership: spacebar/JKL must not type into fields; Esc returns to playback.

Specs 2.1-2.4 + 1.3/1.4.

The offscreen Qt platform does not propagate focus up to
QApplication.focusWidget(), so these tests drive _typing_in_text_field()
directly (by pointing window.focusWidget() at a widget) and spy on the
focus-return handler instead of relying on platform focus propagation.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from tapesift.core.config import AppSettings
from tapesift.ui_v2.main_window import MainWindowV2


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _free_qt_widgets(qapp):
    """Destroy each test's MainWindow instead of leaving it for exit.

    Every test here builds a MainWindow, which owns a QMediaPlayer and
    QAudioOutput. Left undestroyed, four of them accumulate and Qt's
    multimedia backend fast-fails (0xC0000409) tearing them down at
    interpreter exit - which crashed this file 10/10 when run alone, while it
    happened to survive when other files ran first. Deleting them here, under
    a controlled processEvents, frees the backend cleanly.

    deleteLater, not close: close() fires closeEvent -> _close_project, which
    would touch recovery state a test may not expect.
    """
    yield
    for widget in qapp.topLevelWidgets():
        widget.deleteLater()
    # processEvents does not deliver DeferredDelete; without this the
    # widgets are only queued and never actually destroyed.
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture
def window(qapp):
    return MainWindowV2(AppSettings())


def _with_focus(window, widget):
    window.focusWidget = lambda: widget  # type: ignore[assignment]


def test_typing_in_text_field_detects_line_edit():
    win = MainWindowV2(AppSettings())
    _with_focus(win, QLineEdit())
    assert win._typing_in_text_field() is True
    _with_focus(win, QWidget())
    assert win._typing_in_text_field() is False
    _with_focus(win, None)  # type: ignore[assignment]
    assert win._typing_in_text_field() is False


def test_esc_routes_to_focus_return_only_while_typing(window):
    calls = []
    window._return_focus_to_playback = lambda: calls.append(1)  # type: ignore[assignment]

    # Not typing -> Esc is a no-op here.
    window.focusWidget = lambda: None  # type: ignore[assignment]
    window._shortcut_escape()
    assert calls == []

    # Typing -> Esc returns focus to playback.
    window.focusWidget = lambda: QLineEdit()  # type: ignore[assignment]
    window._shortcut_escape()
    assert calls == [1]


def test_return_focus_to_playback_focuses_video_and_updates_indicator(window):
    # Simulate a text field holding focus.
    field = QLineEdit()
    cleared = []
    field.clearFocus = lambda: cleared.append(1)  # type: ignore[assignment]
    window.focusWidget = lambda: field  # type: ignore[assignment]

    video_focused = []
    def _video_set_focus(*a, **k):
        video_focused.append(1)
        # Model real focus propagation: after setFocus the app-level focus
        # widget is the video surface (offscreen can't do this for us).
        window.focusWidget = lambda: window.player.video_widget  # type: ignore[assignment]

    window.player.video_widget.setFocus = _video_set_focus  # type: ignore[assignment]

    window._return_focus_to_playback()

    assert cleared == [1]          # blur the active field
    assert video_focused == [1]    # focus the video surface
    assert "PLAYBACK ACTIVE" in window._focus_indicator.text()


def test_clicking_video_triggers_focus_return(window):
    calls = []
    window._return_focus_to_playback = lambda: calls.append(1)  # type: ignore[assignment]
    window.player.clicked.emit()
    assert calls == [1]
