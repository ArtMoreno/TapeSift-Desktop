"""Modal backdrop: dim the workspace behind open dialogs.

Dialogs in TapeSift are separate top-level windows, so the main window
keeps painting at full brightness behind them - the interface reads flat,
with no sense of which layer is active. This module paints a translucent
scrim over the main window while any modal dialog is open, the same
layered cue macOS and modern web apps use.

One ``BackdropController`` is installed per main window. It watches
application events (installed as a filter on ``QApplication`` it sees
every ``Show``/``Hide``, which are rare) and tracks the set of visible
modal dialogs that belong to its window, including nested dialogs opened
from other dialogs. The scrim fades in with the first dialog and out
with the last.

The scrim itself is a plain child widget of the main window; separate
dialog windows stack above it naturally. It paints in ``paintEvent``
rather than QSS so the corner radius can follow the frameless shell -
rounded at rest, square while maximized.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation
from PySide6.QtGui import QColor, QPainter, Qt
from PySide6.QtWidgets import (
    QApplication, QDialog, QGraphicsOpacityEffect, QMainWindow, QWidget,
)

from tapesift.ui_v2.tokens import COLORS, RADIUS_WINDOW, SCRIM_ALPHA

FADE_MS = 140


class ModalBackdrop(QWidget):
    """The dimming scrim itself: a translucent wash over the window."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.setObjectName("ModalBackdrop")
        self._window = window
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API name
        color = QColor(COLORS["backdrop"])
        color.setAlpha(SCRIM_ALPHA)
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        if self._window.isMaximized() or self._window.isFullScreen():
            painter.drawRect(self.rect())
        else:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.drawRoundedRect(
                self.rect(), RADIUS_WINDOW, RADIUS_WINDOW)
        painter.end()

    def scrim_opacity(self) -> float:
        """Current fade level; tests read this instead of poking privates."""
        return self._opacity.opacity()

    def _opacity_effect(self) -> QGraphicsOpacityEffect:
        """The controller animates this; one graphics effect per widget."""
        return self._opacity


class BackdropController(QObject):
    """Show and hide a :class:`ModalBackdrop` as modal dialogs come and go."""

    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self._window = window
        self._backdrop = ModalBackdrop(window)
        self._dialogs: set[QDialog] = set()
        self._fade: QPropertyAnimation | None = None
        window.installEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    @property
    def backdrop(self) -> ModalBackdrop:
        return self._backdrop

    def tracked_dialog_count(self) -> int:
        """How many modal dialogs currently hold the scrim open."""
        return len(self._dialogs)

    # ---------- event watching ----------

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        if watched is self._window:
            if event.type() == QEvent.Type.Resize:
                self._backdrop.setGeometry(self._window.rect())
            return False
        event_type = event.type()
        if event_type == QEvent.Type.Show:
            self._maybe_track(watched)
        elif event_type == QEvent.Type.Hide:
            self._maybe_untrack(watched)
        return False

    def _maybe_track(self, watched) -> None:
        if not isinstance(watched, QDialog) or not watched.isModal():
            return
        if not self._owns(watched):
            return
        was_empty = not self._dialogs
        self._dialogs.add(watched)
        if was_empty:
            self._set_scrim_visible(True)

    def _maybe_untrack(self, watched) -> None:
        if not isinstance(watched, QDialog):
            return
        if watched in self._dialogs:
            self._dialogs.discard(watched)
            if not self._dialogs:
                self._set_scrim_visible(False)

    def _owns(self, dialog: QDialog) -> bool:
        """True when the dialog's modal block covers this window.

        Nested dialogs (Settings opening a list editor, say) resolve to
        the same main window by walking the whole parent chain. Dialogs
        with no parent at all fall back to this window when it is the
        visible one being blocked.
        """
        widget = dialog.parentWidget()
        while widget is not None:
            if widget is self._window:
                return True
            widget = widget.parentWidget()
        if dialog.parentWidget() is None and self._window.isVisible():
            # Parent-less modal dialogs (startup prompts, message boxes)
            # still block this window when it is the only one showing.
            return True
        return False

    # ---------- scrim visibility ----------

    def _set_scrim_visible(self, visible: bool) -> None:
        if self._fade is not None:
            self._fade.stop()
            self._fade = None
        if visible:
            self._backdrop.setGeometry(self._window.rect())
            self._backdrop.show()
            self._backdrop.raise_()
        target = 1.0 if visible else 0.0
        fade = QPropertyAnimation(
            self._backdrop._opacity_effect(), b"opacity", self)
        fade.setDuration(FADE_MS)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        fade.setStartValue(self._backdrop.scrim_opacity())
        fade.setEndValue(target)
        fade.finished.connect(lambda: self._fade_finished(fade))
        if not visible:
            fade.finished.connect(self._hide_when_dimmed)
        fade.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
        self._fade = fade

    def _fade_finished(self, fade: QPropertyAnimation) -> None:
        if self._fade is fade:
            self._fade = None

    def _hide_when_dimmed(self) -> None:
        if not self._dialogs:
            self._backdrop.hide()


def install_modal_backdrop(window: QMainWindow) -> BackdropController:
    """Attach modal-dialog dimming to *window*; returns the controller."""
    return BackdropController(window)
