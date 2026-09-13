"""The selected V3 jog console around the already-bound wheel."""

from pathlib import Path

import shiboken6

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication, QLabel, QToolButton, QWidget

from tapesift.ui_v2 import deck_icons


class V3JogConsole(QObject):
    def __init__(self, deck):
        super().__init__(deck.jog_window)
        self.deck, self.host, self.wheel = deck, deck.jog_window, deck.jog_ring
        host, wheel = self.host, self.wheel
        # The selected source provides the material; all commands and state
        # remain live on the existing Qt widgets above it.
        asset = Path(__file__).resolve().parent.parent / "resources" / "transport" / "jog-console-material.png"
        self.material = QPixmap(str(asset))
        if self.material.isNull():
            raise FileNotFoundError(f"Jog console material missing: {asset}")
        self.icons = {name: QSvgRenderer(deck_icons._document(name).replace(
            "currentColor", "#d8d1bf").encode(), self) for name in (
                "deckv2_play", "deckv2_pause", "deckv2_frame_back", "deckv2_frame_fwd")}
        # Keep the bound objects and their commands. Only the header now moves
        # the host, so the wheel's existing angular frame gesture can run.
        wheel.removeEventFilter(host)
        host.layout().removeWidget(wheel)
        host.setFixedSize(420, 246)
        host.setWindowTitle("TapeSift Jog")
        wheel.WIDTH, wheel.HEIGHT, wheel.CENTER_Y = 208, 200, 100
        wheel.FACE_RADIUS, wheel.MARKER_RADIUS, wheel.ARC_RADIUS = 94, 86, 90
        wheel.ARC_WIDTH = 3
        wheel.setFixedSize(208, 200)
        wheel.move(16, 34)
        wheel.setMouseTracking(True)
        wheel.setAccessibleName("Jog frames; center plays or pauses")
        wheel.setToolTip("Drag the ring or scroll to step frames. Click the center to play or pause.")
        self.header = QWidget(host)
        self.header.setObjectName("JogConsoleHeader")
        self.header.setGeometry(0, 0, 420, 33)
        self.header.setStyleSheet("background:transparent;border:none;")
        self.header.setCursor(Qt.CursorShape.OpenHandCursor)
        self.header.setToolTip("Drag to move the jog console")
        title = QLabel("JOG", self.header)
        title.setGeometry(22, 6, 70, 22)
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        title.setStyleSheet("background:transparent;border:none;color:#d3cbbb;"
                           "font:11px 'Consolas';letter-spacing:1px;")
        self.close = QToolButton(self.header)
        self.close.setObjectName("JogConsoleClose")
        self.close.setGeometry(378, 3, 34, 27)
        self.close.setAccessibleName("Close jog console")
        self.close.setToolTip("Close jog console")
        self.close.clicked.connect(host.close)
        self.timecode = QLabel(host)
        self.timecode.setObjectName("JogConsoleTimecode")
        self.timecode.setGeometry(249, 77, 166, 29)
        self.frame = QLabel(host)
        self.frame.setObjectName("JogConsoleFrame")
        self.frame.setGeometry(249, 103, 155, 24)
        host.rate_label.setGeometry(249, 55, 155, 23)
        for label, size, color, tracking in ((host.rate_label, 16, "#ccc5b5", 1.1),
                                              (self.timecode, 19, "#58aebd", 1.5),
                                              (self.frame, 16, "#ccc5b5", 1.0)):
            label.setStyleSheet(f"background:transparent;border:none;padding:0;"
                                f"color:{color};font:{size}px 'Consolas';letter-spacing:{tracking}px;")
            label.show()
        self.backward, self.forward = QToolButton(host), QToolButton(host)
        for button, name, text, x, command in (
            (self.backward, "JogConsoleFrameBackward", "-1", 237, deck.step_back_btn.click),
            (self.forward, "JogConsoleFrameForward", "+1", 321, deck.step_fwd_btn.click),
        ):
            button.setObjectName(name)
            button.setText(text)
            button.setGeometry(x, 136, 71, 58)
            button.setAccessibleName("Step " + ("backward" if text == "-1" else "forward") + " one frame")
            button.setToolTip(button.accessibleName())
            button.clicked.connect(command)
        for button in (self.close, self.backward, self.forward):
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.installEventFilter(self)
            button.pressed.connect(button.update)
            button.released.connect(button.update)
        footer = QLabel("Frame step 1", host)
        footer.setGeometry(237, 207, 155, 24)
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer.setStyleSheet("background:transparent;border:0;color:#c8c0af;font:11px 'Consolas';")
        for widget in (host, wheel, self.header, host.rate_label):
            widget.installEventFilter(self)
        QApplication.instance().applicationStateChanged.connect(self._application_state_changed)
        backend = getattr(deck.bound_player, "player", None)
        if backend is not None:
            backend.playbackRateChanged.connect(self._sync_readouts)
        self._sync_readouts()

    def _sync_readouts(self, *_args):
        if not shiboken6.isValid(self.host):
            return
        wheel = self.wheel
        rate = wheel._rate
        if abs(rate) <= .001 and wheel._playing:
            backend = getattr(self.deck.bound_player, "player", None)
            rate = backend.playbackRate() if backend is not None else 1.0
        status = (f"{'FWD' if rate > 0 else 'REV'} {abs(rate):g}x"
                  if abs(rate) > .001 else "PAUSED")
        # These strings arrive after a source frame is painted. A seek request
        # or a backend position tick is not evidence of a displayed frame.
        for label, text in ((self.timecode, wheel._timecode),
                            (self.frame, f"F {wheel._frame_text}"),
                            (self.host.rate_label, status)):
            if label.text() != text:
                label.setText(text)

    def _application_state_changed(self, state):
        if state != Qt.ApplicationState.ApplicationActive:
            self._cancel_interaction()

    def _cancel_interaction(self):
        if not shiboken6.isValid(self.wheel):
            return
        self.host._moving = False
        self.header.setCursor(Qt.CursorShape.OpenHandCursor)
        self.wheel._hub_pressed = False
        self.wheel.cancel_interaction()
        self.wheel.update()
        for button in (self.close, self.backward, self.forward):
            button.setDown(False)

    def eventFilter(self, watched, event):
        if not shiboken6.isValid(self.host):
            return False
        kind = event.type()
        if kind in (QEvent.Type.Hide, QEvent.Type.UngrabMouse) or (
                kind == QEvent.Type.EnabledChange and not watched.isEnabled()):
            self._cancel_interaction()
        if kind == QEvent.Type.Show and watched is self.host:
            self._sync_readouts()
        if watched is self.header:
            if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.host._moving = True
                self.host._move_pointer_origin = event.globalPosition().toPoint()
                self.host._move_window_origin = self.host.pos()
                self.header.setCursor(Qt.CursorShape.ClosedHandCursor)
                return True
            if kind == QEvent.Type.MouseMove and self.host._moving:
                if not event.buttons() & Qt.MouseButton.LeftButton:
                    self._cancel_interaction()
                else:
                    self.host.move(self.host._move_window_origin + event.globalPosition().toPoint()
                                   - self.host._move_pointer_origin)
                    self.host._has_user_position = True
                return True
            if kind == QEvent.Type.MouseButtonRelease and self.host._moving:
                self.host._moving = False
                self.header.setCursor(Qt.CursorShape.OpenHandCursor)
                screen = QGuiApplication.screenAt(event.globalPosition().toPoint()) or self.host.screen()
                self.host.move(self.host._clamped_position(self.host.pos(), screen))
                return True
        if kind == QEvent.Type.Paint:
            if watched is self.host:
                self._paint_host()
                return True
            if watched is self.wheel:
                self._sync_readouts()
                self._paint_wheel()
                return True
            if watched is self.host.rate_label:
                self._sync_readouts()
            if watched in (self.close, self.backward, self.forward):
                self._paint_button(watched)
                return True
        if kind in (QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.MouseMove,
                    QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                    QEvent.Type.EnabledChange):
            watched.update()
        return False

    def _paint_material(self, painter, widget):
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        origin = widget.mapTo(self.host, widget.rect().topLeft())
        painter.drawPixmap(QRectF(-origin.x(), -origin.y(), 420, 246),
                           self.material, QRectF(self.material.rect()))

    def _paint_host(self):
        p = QPainter(self.host)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(self.host.rect()), 8, 8)
        p.setClipPath(outline)
        self._paint_material(p, self.host)
        p.end()

    def _paint_wheel(self):
        wheel = self.wheel
        p = QPainter(wheel)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_material(p, wheel)
        center = wheel.dial_center()
        wheel._paint_rate(p, center)
        wheel._paint_marker(p, center)
        hovered = wheel.isEnabled() and wheel.underMouse() and wheel._inside_hub(
            QPointF(wheel.mapFromGlobal(QCursor.pos())))
        if wheel._hub_pressed or hovered:
            p.setBrush(QColor(0, 0, 0, 45) if wheel._hub_pressed else QColor(255, 248, 223, 9))
            p.setPen(QPen(QColor("#131411" if wheel._hub_pressed else "#77766a"), .7))
            p.drawEllipse(center, 44, 44)
        if not wheel.isEnabled():
            p.setOpacity(.4)
        self.icons["deckv2_pause" if wheel._playing else "deckv2_play"].render(
            p, QRectF(center.x() - 17.5, center.y() - 17.5 + (.6 if wheel._hub_pressed else 0), 35, 35))
        p.end()

    def _paint_button(self, button):
        p = QPainter(button)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._paint_material(p, button)
        down, hover = button.isDown(), button.underMouse() and button.isEnabled()
        if not button.isEnabled():
            p.setOpacity(.4)
        if button is self.close:
            p.setPen(QPen(QColor("#fff3d9" if hover else "#c9c2b1"), 1.2))
            shift = .6 if down else 0
            for a, b in (((12, 9), (22, 19)), ((22, 9), (12, 19))):
                p.drawLine(QPointF(a[0], a[1] + shift), QPointF(b[0], b[1] + shift))
        else:
            if down or hover:
                p.setBrush(QColor(0, 0, 0, 65) if down else QColor(255, 246, 216, 10))
                p.setPen(QPen(QColor("#12130f" if down else "#b9b3a3"), 1))
                p.drawRoundedRect(QRectF(button.rect()).adjusted(3.5, 3.5, -4, -4), 5, 5)
            glyph = "deckv2_frame_back" if button is self.backward else "deckv2_frame_fwd"
            self.icons[glyph].render(p, QRectF(27.5, 12 + (.6 if down else 0), 16, 20))
            font = button.font()
            font.setFamily("Consolas")
            font.setPixelSize(13)
            font.setBold(False)
            p.setFont(font)
            p.setPen(QColor("#d8d1bf"))
            p.drawText(QRectF(0, 31 + (.6 if down else 0), 71, 20), Qt.AlignmentFlag.AlignCenter, button.text())
        p.end()
