"""One measured field for the Review ribbon, enlarged placement and image export."""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from tapesift.services.football_context import logo_path, spot_label, team_abbreviation
from tapesift.services.play_field_service import describe_play, place
from tapesift.ui_v3.icons import brand_pixmap


class PlayFieldWidget(QWidget):
    request_edit = Signal()
    yard_clicked = Signal(int)

    def __init__(self, parent=None, *, compact=False):
        super().__init__(parent)
        self.compact = compact
        self.details: dict[str, str] = {}
        self.game_team_ids: list[str] = []
        self.context = describe_play({}, []).context
        self._logos: dict[str, QPixmap] = {}
        self._quarter_context = None
        self._ifi_watermark = brand_pixmap("ifi-horizontal-approved.jpg", 180)
        self.setMinimumHeight(38 if compact else 150)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Selected play field. Activate to edit ball placement")

    def set_details(self, details: dict[str, str], game_team_ids: list[str]):
        self.details = dict(details)
        self.game_team_ids = list(game_team_ids)
        for team in self.game_team_ids:
            if team not in self._logos:
                path = logo_path(team)
                self._logos[team] = QPixmap(str(path)) if path else QPixmap()
        geometry = describe_play(self.details, self.game_team_ids)
        self.context = geometry.context
        self.setToolTip(" · ".join(filter(None, (
            self.details.get("ball_on"), self.details.get("down_distance"),
            self.details.get("result"), geometry.message, "Click to edit field"))))
        self.update()

    def set_quarters(self, spans, visible_range):
        self._quarter_context = (list(spans), tuple(visible_range))
        self.update()

    def _point(self, yard: float, lateral: float, rect: QRectF) -> QPointF:
        # Perspective changes only width at this depth; yard positions stay measurable.
        margin = 24 if self.compact else 46
        top = rect.top() + (4 if self.compact else 25)
        bottom = rect.bottom() - (4 if self.compact else 27)
        inset = 5 if self.compact else 22
        left = rect.left() + margin + inset * (1-lateral)
        width = rect.width() - 2*margin - 2*inset*(1-lateral)
        fraction = yard/100
        if self.details.get("field_flip") == "1":
            fraction = 1-fraction
        return QPointF(left + fraction*width, top + lateral*(bottom-top))

    def yard_at(self, point: QPointF) -> int | None:
        rect = QRectF(self.rect())
        top, bottom = self._point(0, 0, rect).y(), self._point(0, 1, rect).y()
        if bottom <= top or not top <= point.y() <= bottom:
            return None
        lateral = (point.y()-top)/(bottom-top)
        a, b = self._point(0, lateral, rect), self._point(100, lateral, rect)
        if not min(a.x(), b.x()) <= point.x() <= max(a.x(), b.x()):
            return None
        return max(0, min(100, round(100*(point.x()-a.x())/(b.x()-a.x()))))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.compact:
                self.request_edit.emit()
            else:
                yard = self.yard_at(event.position())
                if yard is not None:
                    self.yard_clicked.emit(yard)
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.request_edit.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        self._draw(painter, QRectF(self.rect()))
        painter.end()

    def to_image(self, width=1200, height=330) -> QImage:
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#080c0a"))
        painter = QPainter(image)
        self._draw(painter, QRectF(0, 0, width, height))
        painter.end()
        return image

    def _draw(self, p: QPainter, rect: QRectF):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(rect, QColor("#080c0a"))
        quarter_rect = None
        if self.compact and self._quarter_context is not None:
            quarter_rect = QRectF(rect.left()+24, rect.bottom()-18, rect.width()-48, 14)
            rect = rect.adjusted(0, 0, 0, -14)
        geometry = describe_play(self.details, self.game_team_ids)
        point = lambda yard, lateral=.5: self._point(yard, lateral, rect)
        p.setPen(QPen(QColor("#63805a"), 1))
        p.setBrush(QColor("#1e3b22"))
        p.drawPolygon(QPolygonF([point(0, 0), point(100, 0), point(100, 1), point(0, 1)]))
        for yard in range(0, 100, 10):
            if yard % 20 == 0:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#24472a"))
                p.drawPolygon(QPolygonF([point(yard, 0), point(yard+10, 0),
                                        point(yard+10, 1), point(yard, 1)]))
        if not self._ifi_watermark.isNull():
            p.save()
            p.setOpacity(.12)
            width = rect.width() * .28
            height = width * self._ifi_watermark.height() / self._ifi_watermark.width()
            center = point(50)
            p.drawPixmap(QRectF(center.x()-width/2, center.y()-height/2, width, height),
                         self._ifi_watermark, QRectF(self._ifi_watermark.rect()))
            p.restore()
        font = p.font()
        font.setPixelSize(8 if self.compact else 11)
        p.setFont(font)
        for yard in range(0, 101, 5):
            p.setPen(QPen(QColor("#668864" if yard % 10 else "#9caf8c"), .7))
            p.drawLine(point(yard, 0), point(yard, 1))
            if yard % 10 == 0 and 0 < yard < 100:
                anchor = point(yard, .16)
                p.setPen(QColor("#d3dbbf"))
                p.drawText(QRectF(anchor.x()-12, anchor.y()-6, 24, 12),
                           Qt.AlignmentFlag.AlignCenter, str(min(yard, 100-yard)))
        if not self.compact:
            p.setPen(QPen(QColor("#8eaa82"), .8))
            for yard in range(1, 100):
                for hash_y in (.375, .625):
                    p.drawLine(point(yard, hash_y-.012), point(yard, hash_y+.012))
        for index, yard in enumerate((0, 100)):
            team = self.game_team_ids[index] if index < len(self.game_team_ids) else ""
            text = team_abbreviation(team) if team else ("OWN" if index == 0 else "OPP")
            anchor = point(yard)
            side = -1 if anchor.x() < rect.center().x() else 1
            text_rect = QRectF(anchor.x()-23 if side < 0 else anchor.x()+2,
                               rect.center().y()-7, 22, 14)
            p.setPen(QColor("#d8e0cf"))
            p.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text[:5])
            logo = self._logos.get(team)
            if not self.compact and logo is not None and not logo.isNull():
                p.save()
                p.setOpacity(.18)
                center = point(12 if index == 0 else 88)
                p.drawPixmap(QRectF(center.x()-24, center.y()-24, 48, 48),
                             logo, QRectF(logo.rect()))
                p.restore()
        for yard, color in ((geometry.context.los_yards, "#50bdff"),
                            (geometry.context.to_gain_yards, "#efbd52")):
            if yard is not None:
                p.setPen(QPen(QColor(color), 1.5 if self.compact else 2))
                p.drawLine(point(yard, 0), point(yard, 1))
        lateral = {"left": .375, "middle": .5, "right": .625}.get(geometry.hash, .5)
        if geometry.flipped:
            lateral = 1-lateral
        los = geometry.context.los_yards

        def segment(start, end, color, aerial=False):
            a, b = point(start, lateral), point(end, lateral)
            p.setPen(QPen(QColor(color), 2 if self.compact else 3,
                         Qt.PenStyle.DashLine if aerial else Qt.PenStyle.SolidLine))
            if aerial and not self.compact:
                path = QPainterPath(a)
                lift = min(35, abs(a.x()-b.x())*.2)
                path.cubicTo(QPointF(a.x(), a.y()-lift), QPointF(b.x(), b.y()-lift), b)
                p.drawPath(path)
            else:
                p.drawLine(a, b)
            p.setBrush(QColor(color))
            p.drawEllipse(b, 3 if self.compact else 4, 3 if self.compact else 4)

        if los is not None:
            if geometry.end is not None:
                if geometry.catch is not None:
                    segment(los, geometry.catch, "#f0ecdc", True)
                    segment(geometry.catch, geometry.end, "#7ce6a2")
                else:
                    segment(los, geometry.end, "#f0ecdc" if geometry.passing else "#7ce6a2",
                            geometry.passing)
            if geometry.event is not None:
                segment(los, geometry.event, "#f0ecdc", "interception" in
                        {item.casefold() for item in geometry.results})
                if geometry.return_end is not None:
                    segment(geometry.event, geometry.return_end, "#f68183")
            if geometry.enforced is not None:
                segment(los, geometry.enforced, "#efbd52")
            p.setPen(QPen(QColor("#080c0a"), 1))
            p.setBrush(QColor("#f3ead5"))
            p.drawEllipse(point(los, lateral), 3 if self.compact else 5, 3 if self.compact else 5)
            if not self.compact:
                # Only explicit event roles get a jersey; clip focus may be any position.
                badges = []
                if geometry.passing and self.details.get("quarterback"):
                    badges.append((los, self.details["quarterback"], "QB"))
                if geometry.end is not None:
                    role_key = "receiver_name" if geometry.passing else "ball_carrier"
                    if self.details.get(role_key):
                        badges.append((geometry.catch if geometry.catch is not None else geometry.end,
                                       self.details[role_key], "REC" if geometry.passing else "RUN"))
                for yard, name, fallback in badges:
                    match = re.search(r"(?:#\s*|^)(\d{1,2})(?=\s|$)", name)
                    text = match[1] if match else fallback
                    anchor = point(yard, lateral)
                    center = QPointF(anchor.x(), anchor.y()+22)
                    p.setPen(QPen(QColor("#dfebd0"), 1))
                    p.setBrush(QColor("#173321"))
                    p.drawLine(anchor, center)
                    p.drawEllipse(center, 12, 12)
                    p.drawText(QRectF(center.x()-12, center.y()-12, 24, 24),
                               Qt.AlignmentFlag.AlignCenter, text)
                label = f"START {spot_label(los)}"
                if geometry.end is not None:
                    label += f"   →   {spot_label(geometry.end)}  ({self.details.get('yards')} yd)"
                if geometry.catch is not None:
                    label += f"   ·   CATCH {spot_label(geometry.catch)} / YAC {self.details.get('yac')}"
                p.setPen(QColor("#e4e8dd"))
                p.drawText(QRectF(rect.left()+10, rect.top(), rect.width()-20, 21),
                           Qt.AlignmentFlag.AlignCenter, label)
        if not self.compact:
            summary = " · ".join(filter(None, ("Schematic · not tracked routes", self.details.get("run_pass"),
                self.details.get("play_type"), self.details.get("result"), geometry.message)))
            p.setPen(QColor("#c0cabb"))
            summary = p.fontMetrics().elidedText(summary, Qt.TextElideMode.ElideRight,
                                                int(rect.width()-20))
            p.drawText(QRectF(rect.left()+10, rect.bottom()-22, rect.width()-20, 20),
                       Qt.AlignmentFlag.AlignCenter, summary)


        if quarter_rect is not None:
            spans, (start, end) = self._quarter_context
            p.fillRect(quarter_rect, QColor("#18341e"))
            font = p.font()
            font.setPixelSize(10)
            p.setFont(font)
            p.save()
            p.setClipRect(quarter_rect)
            for begin, finish, quarter in spans:
                if end <= start or finish <= start or begin >= end:
                    continue
                left = quarter_rect.left() + (max(begin, start)-start)/(end-start)*quarter_rect.width()
                right = quarter_rect.left() + (min(finish, end)-start)/(end-start)*quarter_rect.width()
                area = QRectF(left, quarter_rect.top(), right-left, quarter_rect.height())
                active = quarter == self.details.get("quarter")
                from tapesift.services.heatmap_palette import quarter_colour
                p.fillRect(area, QColor(quarter_colour(quarter)).darker(230 if active else 320))
                p.setPen(QColor("#e9e8d8" if active else "#b9c8b9"))
                if area.width() >= p.fontMetrics().horizontalAdvance(quarter)+6:
                    p.drawText(area, Qt.AlignmentFlag.AlignCenter, quarter)
                if active:
                    p.setPen(QPen(QColor("#d7b666"), 1))
                    p.drawLine(area.bottomLeft(), area.bottomRight())
            p.restore()


class PlayFieldDialog(QDialog):
    details_changed = Signal(dict)

    def __init__(self, details: dict[str, str], game_team_ids: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Play field · TapeSift")
        self.setObjectName("PlayFieldDialog")
        self.setStyleSheet("""
            QDialog#PlayFieldDialog { background: #080c0a; color: #e9eee4; }
            QDialog#PlayFieldDialog QLabel { color: #b9c8ba; }
            QDialog#PlayFieldDialog QPushButton, QDialog#PlayFieldDialog QComboBox,
            QDialog#PlayFieldDialog QLineEdit {
                background: #101a13; color: #e9eee4; border: 1px solid #3c5142;
                border-radius: 3px; padding: 5px 8px; min-height: 24px;
            }
            QDialog#PlayFieldDialog QPushButton:hover { border-color: #8da98c; }
            QDialog#PlayFieldDialog QPushButton:disabled { color: #647766; }
        """)
        self.resize(880, 390)
        self.details = dict(details)
        self.game_team_ids = list(game_team_ids)
        self._history: list[dict[str, str]] = []
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.mode = QComboBox()
        for title, key in (("Set start", "start"), ("Set finish", "finish"), ("Set catch", "catch"),
                           ("Turnover / recovery spot", "event"), ("Return finish", "return"),
                           ("Enforced spot", "enforced")):
            self.mode.addItem(title, key)
        self.mode.setAccessibleName("Placement mode")
        controls.addWidget(self.mode)
        self.flip = QPushButton("Flip field")
        self.flip.clicked.connect(lambda: self._change({"field_flip":
            "0" if self.details.get("field_flip") == "1" else "1"}))
        controls.addWidget(self.flip)
        self.hash = QComboBox()
        self.hash.setAccessibleName("Starting hash relative to offense")
        for title, value in (("Hash not set", ""), ("Left hash", "left"),
                             ("Middle", "middle"), ("Right hash", "right")):
            self.hash.addItem(title, value)
        self.hash.currentIndexChanged.connect(lambda: self._change({"field_hash": self.hash.currentData()}))
        controls.addWidget(self.hash)
        self.undo = QPushButton("Undo placement")
        self.undo.clicked.connect(self._undo)
        controls.addWidget(self.undo)
        layout.addLayout(controls)
        self.field = PlayFieldWidget()
        self.field.yard_clicked.connect(self._place)
        layout.addWidget(self.field, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        participants = QHBoxLayout()
        participants.addWidget(QLabel("Recovering team"))
        self.recovery = QComboBox()
        self.recovery.addItem("Not set", "")
        self.recovery.addItem("Offense", "offense")
        self.recovery.addItem("Defense", "defense")
        self.recovery.currentIndexChanged.connect(lambda: self._change(
            {"field_recovery_team": self.recovery.currentData()}))
        participants.addWidget(self.recovery)
        participants.addStretch()
        self.people = QLabel()
        self.people.setWordWrap(True)
        participants.addWidget(self.people)
        layout.addLayout(participants)
        roles = QHBoxLayout()
        self.receiver = QLineEdit(self.details.get("receiver_name", ""))
        self.receiver.setPlaceholderText("Receiver · optional # and name")
        self.receiver.setAccessibleName("Explicit receiver name and jersey")
        self.receiver.editingFinished.connect(lambda: self._change({"receiver_name": self.receiver.text().strip()}))
        roles.addWidget(self.receiver)
        self.carrier = QLineEdit(self.details.get("ball_carrier", ""))
        self.carrier.setPlaceholderText("Ball carrier · optional # and name")
        self.carrier.setAccessibleName("Explicit ball carrier name and jersey")
        self.carrier.editingFinished.connect(lambda: self._change({"ball_carrier": self.carrier.text().strip()}))
        roles.addWidget(self.carrier)
        layout.addLayout(roles)
        footer = QHBoxLayout()
        explanation = QLabel("Schematic · not tracked routes. Placements stay in this clip's draft until Save.")
        explanation.setWordWrap(True)
        footer.addWidget(explanation)
        footer.addStretch()
        close = QPushButton("Done")
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        layout.addLayout(footer)
        self._refresh()

    def _refresh(self):
        self.field.set_details(self.details, self.game_team_ids)
        self.hash.blockSignals(True)
        self.hash.setCurrentIndex(max(0, self.hash.findData(self.details.get("field_hash", ""))))
        self.hash.blockSignals(False)
        self.recovery.blockSignals(True)
        self.recovery.setCurrentIndex(max(0, self.recovery.findData(self.details.get("field_recovery_team", ""))))
        self.recovery.blockSignals(False)
        self.undo.setEnabled(bool(self._history))
        self.receiver.setText(self.details.get("receiver_name", ""))
        self.carrier.setText(self.details.get("ball_carrier", ""))
        geometry = describe_play(self.details, self.game_team_ids)
        self.status.setText(geometry.message or "Click a yard line to place the selected point.")
        people = [f"{role}: {self.details[key]}" for key, role in
                  (("quarterback", "QB"), ("receiver_name", "Receiver")) if self.details.get(key)]
        self.people.setText(" · ".join(people))

    def _change(self, values):
        updated = dict(self.details)
        updated.update(values)
        if updated != self.details:
            self._history.append(dict(self.details))
            self.details = updated
            self._refresh()
            self.details_changed.emit(dict(self.details))

    def _place(self, yard):
        try:
            self._change(place(self.details, self.mode.currentData(), yard))
        except ValueError as exc:
            self.status.setText(str(exc))

    def _undo(self):
        if self._history:
            self.details = self._history.pop()
            self._refresh()
            self.details_changed.emit(dict(self.details))
