"""V3 faces for the existing transport buttons; playback stays in VideoPlayer."""

import shiboken6

from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer

from tapesift.ui_v2 import deck_icons


class V3TransportSurface(QObject):
    """Paint live Qt button states without replacing the bound hit targets."""

    def __init__(self, deck):
        surface = deck.transport_island
        super().__init__(surface)
        self.surface = surface
        self.buttons = (deck.step_back_btn, deck.rewind_btn, deck.play_btn,
                        deck.fast_forward_btn, deck.step_fwd_btn)
        self.glyphs = ("deckv2_frame_back", "shuttle_back", "deckv2_play",
                       "shuttle_fwd", "deckv2_frame_fwd")
        self.marks = ({deck.in_button: "[", deck.out_button: "]"}
                      if hasattr(deck, "in_button") else {})
        self.icons = {
            name: QSvgRenderer(deck_icons._document(name).replace(
                "currentColor", "#f0eee3").encode(), self)
            for name in (*self.glyphs, "deckv2_pause")
        }
        # Symmetric triangle with its filled-area centre on the button centre.
        # Keep this V3 face independent of the established jog-wheel artwork.
        self.icons["deckv2_play"] = QSvgRenderer(
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            b'<path d="M6.8 3 L22.4 12 L6.8 21 Z" fill="#f0eee3"/></svg>', self)
        # The old focus halo handler toggles the opacity effect now used by
        # these invisible hit targets, exposing generic Qt art on focus-out.
        deck.play_btn.removeEventFilter(deck)
        deck.play_btn.graphicsEffect().setEnabled(True)
        surface.installEventFilter(self)
        for button in self.buttons:
            button.installEventFilter(self)
            button.pressed.connect(surface.update)
            button.released.connect(surface.update)
        for button in self.marks:
            button.installEventFilter(self)
        surface.update()

    def eventFilter(self, watched, event):
        # Qt may emit child style/focus events after destroying the parent
        # surface during native teardown; there is then nothing to repaint.
        if not shiboken6.isValid(self.surface):
            return False
        if watched is self.surface and event.type() == QEvent.Type.Paint:
            self._paint()
            return True
        if watched in self.marks and event.type() == QEvent.Type.Paint:
            self._paint_mark(watched)
            return True
        if event.type() in (
            QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
            QEvent.Type.FocusIn, QEvent.Type.FocusOut,
            QEvent.Type.EnabledChange, QEvent.Type.DynamicPropertyChange,
        ):
            # Queued paint observes state after QAbstractButton handles the
            # event, including a held press dragged outside and canceled.
            self.surface.update()
            if watched in self.marks:
                watched.update()
        return False

    def _paint_mark(self, button):
        painter = QPainter(button)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(button.rect()).adjusted(.5, .5, -.5, -.5)
        down = button.isEnabled() and button.isDown()
        hover = button.isEnabled() and button.underMouse()
        painter.setBrush(QColor("#303940" if down else "#252e34" if hover else "#141c21"))
        painter.setPen(QPen(QColor("#d8e1e8" if button.hasFocus() else "#394650"), 1))
        painter.drawRoundedRect(rect, 2, 2)
        if not button.isEnabled():
            painter.setOpacity(.45)
        font = QFont(button.font())
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor("#e4e8eb"))
        painter.drawText(rect.translated(0, int(down)), Qt.AlignmentFlag.AlignCenter,
                         button.text() + " " + self.marks[button])
        painter.end()

    def _paint(self):
        painter = QPainter(self.surface)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for index, button in enumerate(self.buttons):
            painter.save()
            enabled = button.isEnabled()
            down = enabled and button.isDown()
            hover = enabled and button.underMouse()
            focus = enabled and button.hasFocus()
            bounds = QRectF(button.geometry())
            if not enabled:
                painter.setOpacity(.35)
            play = index == 2
            fill = ("#164a3d" if down else "#265f50" if hover else "#193e34") if play else (
                "#303333" if down else "#252828" if hover else "#191b1b")
            edge = "#d8e1e8" if focus else "#47665c" if play else "#59656e" if hover else "#394650"
            painter.setBrush(QColor(fill))
            painter.setPen(QPen(QColor(edge), 1))
            painter.drawRoundedRect(bounds.adjusted(.5, 1.5, -.5, -1.5), 3, 3)
            glyph = self.glyphs[index]
            if index == 2 and self.surface._playing:
                glyph = "deckv2_pause"
            center = bounds.center()
            # A square viewport preserves the source SVG proportions at both DPIs.
            width = height = min(bounds.width(), bounds.height()) * .62
            target = QRectF(center.x() - width / 2,
                            center.y() - height / 2 + (.45 if down else 0),
                            width, height)
            self.icons[glyph].render(painter, target)
            painter.restore()
        painter.end()
