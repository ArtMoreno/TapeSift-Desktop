"""Pagebook Clip Details around the existing native editor and save path."""
from __future__ import annotations

import math
import re
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QIntValidator, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QAbstractSpinBox,
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSizePolicy, QToolButton,
    QVBoxLayout, QWidget, QWidgetAction,
)

from tapesift.services.football_context import (
    FieldContext, down_distance, logo_path, parse_ball, parse_down_distance,
    spot_label, team_abbreviation, team_name, teams,
)
from tapesift.services import result_service, detail_service, drive_suggestions
from tapesift.services.football_vocab import short_for, lookup, parse_yards
from tapesift.ui_core.clip_editor import ClipEditor, STANDARD_RESULT_CHOICES
from tapesift.ui_core.tag_edit import DetailEdit, TagLineEdit
from tapesift.ui_core.flow_layout import FlowLayout
from tapesift.ui_v3.source_photo import SourcePhotoPanel
from tapesift.ui_v3.play_timing import PlayTimingPanel
from tapesift.ui_v3.icons import tinted_icon, brand_pixmap
from tapesift.ui.result_manager_dialog import unique_results
from tapesift.ui_v3.result_picker import (
    DEFAULT_SHORTCUTS, CAPTIONS, ResultsPicker, ResultShortcutsDialog,
    tint_button,
)


def team_icon(team_id: str) -> QIcon:
    path = logo_path(team_id)
    return QIcon(str(path)) if path else QIcon()


def label(text: str, parent=None) -> QLabel:
    item = QLabel(text, parent)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setWordWrap(True)
    return item


def combo(choices: tuple[tuple[str, str], ...], name: str) -> QComboBox:
    control = QComboBox()
    control.setAccessibleName(name)
    for title, value in choices:
        control.addItem(title, value)
    control.setMinimumWidth(0)
    control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    return control


def choose(control: QComboBox, value: str) -> None:
    blocked = control.blockSignals(True)
    control.setCurrentIndex(max(0, control.findData(value)))
    control.blockSignals(blocked)


class GameTeamsDialog(QDialog):
    """Stage explicit game identity; Cancel never touches the project."""

    def __init__(self, game_team_ids: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3GameTeamsDialog")
        self.setWindowTitle("Video teams")
        self.setMinimumWidth(390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)
        layout.addWidget(label("Video teams"))
        layout.addWidget(label("Choose the offense and defense once for this All-22 video. These roles apply throughout this video."))
        self.team_a = self._team_combo("Offense team")
        self.team_b = self._team_combo("Defense team")
        for i, control in enumerate((self.team_a, self.team_b)):
            layout.addWidget(label("Offense" if i == 0 else "Defense"))
            layout.addWidget(control)
            choose(control, game_team_ids[i] if len(game_team_ids) > i else "")
        self.error_label = label("")
        self.error_label.setProperty("role", "error")
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @staticmethod
    def _team_combo(name: str) -> QComboBox:
        control = combo((("Choose team", ""),), name)
        control.setEditable(True)
        control.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for team in sorted(teams().values(), key=lambda t: t["name"]):
            control.addItem(team_icon(team["id"]), team["name"], team["id"])
        control.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        control.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        return control

    def selected_ids(self) -> list[str]:
        values = []
        for control in (self.team_a, self.team_b):
            index = control.findText(control.currentText(), Qt.MatchFlag.MatchExactly)
            values.append(control.itemData(index) if index >= 0 else None)
        if values == ["", ""]:
            return []
        if any(v not in teams() for v in values) or values[0] == values[1]:
            raise ValueError("Choose two different teams from the list, or clear both.")
        return values

    def accept(self) -> None:
        try:
            self.selected_ids()
        except ValueError as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
            return
        super().accept()


class MiniFieldV3(QFrame):
    """A complete football field using only the selected play's saved context."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3MiniField")
        self.setFixedHeight(148)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.context = FieldContext()
        self.game_team_ids: list[str] = []
        self._team_logos: dict[str, QPixmap] = {}
        self._ifi_watermark = brand_pixmap("ifi-horizontal-approved.jpg", 180)

    def set_details(self, details: dict[str, str], game_team_ids: list[str]) -> None:
        self.game_team_ids = list(game_team_ids)
        self.context = FieldContext.from_details(details, game_team_ids)
        for team_id in game_team_ids:
            if team_id not in self._team_logos:
                path = logo_path(team_id)
                self._team_logos[team_id] = QPixmap(str(path)) if path else QPixmap()
        c = self.context
        description = c.message or f"LOS {spot_label(c.los_yards)}; line to gain {spot_label(c.to_gain_yards)}."
        description += " Diagram always advances left to right; not camera direction."
        if len(set(self.game_team_ids)) == 2 and not c.offense_id:
            description += " Logos are shown in game-team order; offense is unassigned."
        self.setAccessibleName(description)
        self.setToolTip(description)
        self.update()

    def _field_rect(self) -> QRectF:
        return QRectF(7, 6, max(0, self.width()-14), self.height()-32)

    def x_for_yard(self, yard: float) -> float:
        # Both ten-yard end zones stay visible; selecting a play never zooms the field.
        field = self._field_rect()
        fraction = (10 + yard) / 120
        if self.context.direction == "left":
            fraction = 1 - fraction
        return field.left() + field.width() * fraction

    def _paint_team(self, painter: QPainter, team_id: str, yard: int, field: QRectF) -> None:
        logo = self._team_logos.get(team_id)
        size = min(36, field.height() * .38)
        center = QPointF(self.x_for_yard(yard), field.center().y())
        if logo is not None and not logo.isNull():
            scale = size / max(logo.width(), logo.height())
            width, height = logo.width()*scale, logo.height()*scale
            target = QRectF(center.x()-width/2, center.y()-height/2, width, height)
            painter.drawPixmap(target, logo, QRectF(logo.rect()))
        elif team_id in teams():
            painter.setPen(QColor("#d6ddcb"))
            painter.drawText(QRectF(center.x()-26, center.y()-12, 52, 24),
                             Qt.AlignmentFlag.AlignCenter, team_abbreviation(team_id))

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        field = self._field_rect()
        c = self.context
        p.fillRect(field, QColor("#344f2a"))
        unit = field.width()/120
        for yard in range(0, 100, 5):
            p.fillRect(QRectF(field.left()+(yard+10)*unit, field.top(), 5*unit, field.height()),
                       QColor("#3c592f" if yard % 10 == 0 else "#344f2a"))
        for edge in (field.left(), field.right()-10*unit):
            p.fillRect(QRectF(edge, field.top(), 10*unit, field.height()), QColor("#233d24"))
        if not self._ifi_watermark.isNull():
            mark = self._ifi_watermark
            scale = min(field.width() * .5 / mark.width(),
                        (field.height()-12) / mark.height())
            width, height = mark.width()*scale, mark.height()*scale
            target = QRectF(field.center().x()-width/2, field.center().y()-height/2,
                            width, height)
            p.save()
            p.setClipRect(field)
            # Screen leaves the supplied artwork's black background neutral.
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
            p.setOpacity(.12)
            p.drawPixmap(target, mark, QRectF(mark.rect()))
            p.restore()
        for yard in range(0, 101, 5):
            x = self.x_for_yard(yard)
            p.setPen(QPen(QColor("#a2b08d" if yard in (0, 100) else "#758b60"),
                          1.1 if yard in (0, 100) else .65))
            p.drawLine(QPointF(x, field.top()), QPointF(x, field.bottom()))
        p.setPen(QPen(QColor("#93a47c"), .65))
        for yard in range(1, 100):
            x = self.x_for_yard(yard)
            for y in (field.top()+field.height()*.35, field.top()+field.height()*.65):
                p.drawLine(QPointF(x, y-1.5), QPointF(x, y+1.5))
        font = QFont("IBM Plex Sans")
        font.setPixelSize(9)
        p.setFont(font)
        p.setPen(QColor("#c2ceb0"))
        for yard in range(10, 100, 10):
            x = self.x_for_yard(yard)
            number = str(min(yard, 100-yard))
            for y in (field.top()+6, field.bottom()-19):
                p.drawText(QRectF(x-10, y, 20, 13), Qt.AlignmentFlag.AlignCenter, number)
        p.setPen(QPen(QColor("#94a684"), 1))
        p.drawRect(field)
        if len(set(self.game_team_ids)) == 2:
            left_team = c.offense_id or self.game_team_ids[0]
            right_team = next(team_id for team_id in self.game_team_ids if team_id != left_team)
            self._paint_team(p, left_team, 20, field)
            self._paint_team(p, right_team, 80, field)
        for yard, color in ((c.los_yards, "#41a5ef"), (c.to_gain_yards, "#e7b344")):
            if yard is not None:
                x = self.x_for_yard(yard)
                p.setPen(QPen(QColor("#16241b"), 3.5))
                p.drawLine(QPointF(x, field.top()), QPointF(x, field.bottom()))
                p.setPen(QPen(QColor(color), 2))
                p.drawLine(QPointF(x, field.top()), QPointF(x, field.bottom()))
        font.setPixelSize(11)
        p.setFont(font)
        if c.los_yards is None:
            p.setPen(QColor("#c4cebf"))
            # Keep the chosen teams visible while the analyst enters a spot.
            p.drawText(QRectF(7, field.bottom()+3, self.width()-14, 22),
                       Qt.AlignmentFlag.AlignVCenter, "Set ball position to show the lines.")
        else:
            legend_y = field.bottom()+3
            half = (self.width()-38)/2
            p.setPen(QColor("#52b0f3"))
            p.drawText(QRectF(7, legend_y, half, 22), Qt.AlignmentFlag.AlignVCenter,
                       "LOS " + spot_label(c.los_yards))
            if c.to_gain_yards is not None:
                gain = "GAIN " + ("GOAL LINE" if c.goal_to_go else spot_label(c.to_gain_yards))
                p.setPen(QColor("#e7b344"))
            else:
                gain = "Inches: gain unknown" if "Inches" in c.message else "GAIN unknown"
                p.setPen(QColor("#a8b3a0"))
            p.drawText(QRectF(9+half, legend_y, half, 22), Qt.AlignmentFlag.AlignVCenter, gain)
        if (c.direction in {"left", "right"}
                and c.offense_id in self.game_team_ids
                and len(set(self.game_team_ids)) == 2):
            middle = field.bottom()+14
            tail, head = (self.width()-29, self.width()-8) if c.direction == "right" else (self.width()-8, self.width()-29)
            sign = 1 if c.direction == "right" else -1
            p.setPen(QPen(QColor("#bdc8b5"), 1.2))
            p.drawLine(QPointF(tail, middle), QPointF(head, middle))
            p.drawPolyline(QPolygonF([QPointF(head-sign*5, middle-3), QPointF(head, middle), QPointF(head-sign*5, middle+3)]))
        p.end()


class SituationPanelV3(QWidget):
    teams_requested = Signal()

    def __init__(self, editor: ClipEditor) -> None:
        super().__init__()
        self.setObjectName("V3SituationPanel")
        self.editor = editor
        self.game_team_ids: list[str] = []
        self._syncing = False
        self._down_changed = self._ball_changed = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        heading = QHBoxLayout()
        title = label("Situation")
        title.setProperty("detailsHeading", True)
        heading.addWidget(title)
        heading.addStretch()
        self.team_button = QPushButton("Game teams")
        self.team_button.clicked.connect(self.teams_requested.emit)
        heading.addWidget(self.team_button)
        outer.addLayout(heading)
        fields = QGridLayout()
        fields.setHorizontalSpacing(10)
        fields.setVerticalSpacing(6)
        self.down_combo = combo((("Unknown", ""), ("1st", "1"), ("2nd", "2"), ("3rd", "3"), ("4th", "4")), "Down")
        self.distance_edit = DetailEdit()
        self.distance_edit.set_fixed_values(["", *map(str, range(1, 41)), "Goal", "Inches"])
        self.distance_edit.setAccessibleName("Distance to gain")
        self.distance_edit.lineEdit().setPlaceholderText("Yards, Goal or Inches")
        self.ball_edit = QLineEdit()
        self.ball_edit.setAccessibleName("Ball yard line")
        self.ball_edit.setValidator(QIntValidator(0, 50, self.ball_edit))
        self.ball_edit.setPlaceholderText("0–50")
        self.side_combo = combo((("Choose territory", ""), ("Own", "OWN"), ("Opponent", "OPP"), ("Midfield", "MID")), "Ball territory")
        self.clock_edit = self._extra("game_clock", "Game clock")
        self.clock_edit.lineEdit().setPlaceholderText("m:ss")
        for index, (caption, widget) in enumerate((
                ("Quarter", editor.detail_edits["quarter"]), ("Game clock", self.clock_edit),
                ("Down", self.down_combo), ("Distance", self.distance_edit),
                ("Ball on", self.ball_edit), ("Territory", self.side_combo))):
            self._cell(fields, caption, widget, index//2, index%2)
        outer.addLayout(fields)
        self.score_row = QHBoxLayout()
        self.score_row.setSpacing(12)
        self.score_cells = []
        for i in range(2):
            frame = QFrame()
            frame.setObjectName("V3TeamScore")
            grid = QGridLayout(frame)
            grid.setContentsMargins(8, 5, 8, 5)
            grid.setSpacing(5)
            logo = label(""); logo.setFixedSize(25, 25)
            name = label("Team " + str(i+1))
            name.setObjectName("V3TeamAbbreviation")
            grid.addWidget(logo, 0, 0)
            grid.addWidget(name, 0, 1)
            empty = label("Score unset")
            grid.addWidget(empty, 1, 0, 1, 2)
            self.score_cells.append((grid, logo, name, empty))
            self.score_row.addWidget(frame, 1)
        outer.addLayout(self.score_row)
        field_controls = QGridLayout()
        field_controls.setHorizontalSpacing(10)
        self.offense_combo = combo((("Unknown", ""),), "Offense")
        self._extra("offense_team_id", "Offense team identity").hide()
        self._extra("attack_direction", "Offense direction").hide()
        self._cell(field_controls, "Offense (optional)", self.offense_combo, 0, 0)
        outer.addLayout(field_controls)
        self.mini_field = MiniFieldV3()
        outer.addWidget(self.mini_field)
        self.saved_situation = label("")
        self.saved_situation.setObjectName("V3SituationHint")
        outer.addWidget(self.saved_situation)
        self.down_combo.currentIndexChanged.connect(self._change_down)
        self.distance_edit.currentTextChanged.connect(self._change_down)
        self.side_combo.currentIndexChanged.connect(self._change_ball)
        self.ball_edit.textChanged.connect(self._change_ball)
        self.offense_combo.currentIndexChanged.connect(lambda: self._set_choice("offense_team_id", self.offense_combo))
        for edit in (self.distance_edit.lineEdit(), self.ball_edit):
            edit.returnPressed.connect(editor._apply_from_key)
        for key in ("quarter", "down_distance", "ball_on", "game_clock", "offense_team_id", "attack_direction"):
            editor.detail_edits[key].currentTextChanged.connect(self.refresh)

    def _extra(self, key: str, name: str) -> DetailEdit:
        if key in self.editor.detail_edits:
            return self.editor.detail_edits[key]
        edit = DetailEdit(self.editor)
        edit.setAccessibleName(name)
        edit.setMinimumWidth(0)
        edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.editor.detail_edits[key] = edit
        edit.currentTextChanged.connect(self.editor._detail_value_changed)
        edit.lineEdit().returnPressed.connect(self.editor._apply_from_key)
        if self.editor._clip:
            edit.setText(self.editor._clip.details.get(key, ""))
        return edit

    @staticmethod
    def _cell(grid, caption, widget, row, column):
        cell = QWidget()
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(label(caption))
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(widget)
        widget.show()
        grid.addWidget(cell, row, column)
        grid.setColumnStretch(column, 1)

    def set_game_teams(self, values: list[str]) -> None:
        if values == self.game_team_ids:
            self.refresh()
            return
        self.game_team_ids = list(values)
        blocked = self.offense_combo.blockSignals(True)
        self.offense_combo.clear()
        self.offense_combo.addItem("Unknown", "")
        for team_id in values:
            self.offense_combo.addItem(team_icon(team_id), team_abbreviation(team_id), team_id)
        self.offense_combo.blockSignals(blocked)
        for i, (grid, logo, name, empty) in enumerate(self.score_cells):
            for index in reversed(range(grid.count())):
                if grid.getItemPosition(index)[0] == 1:
                    old = grid.takeAt(index).widget()
                    old.hide()
                    if old is not empty:
                        old.setParent(self.editor)
            team_id = values[i] if i < len(values) else ""
            name.setText(team_abbreviation(team_id) if team_id else f"Team {i+1}")
            name.setToolTip(team_name(team_id))
            logo.setPixmap(team_icon(team_id).pixmap(QSize(25, 25)))
            empty.setVisible(not team_id)
            if team_id:
                edit = self._extra("score:"+team_id, team_name(team_id)+" score")
                edit.lineEdit().setValidator(QIntValidator(0, 999, edit))
                edit.lineEdit().setPlaceholderText("Score")
                grid.addWidget(edit, 1, 0, 1, 2); edit.show()
            else:
                grid.addWidget(empty, 1, 0, 1, 2)
        self.refresh()

    def _set_choice(self, key: str, control: QComboBox) -> None:
        if not self._syncing:
            self.editor.detail_edits[key].setText(control.currentData() or "")

    def _change_down(self) -> None:
        if self._syncing:
            return
        self._down_changed = True
        distance = self.distance_edit.text().strip()
        if distance.casefold() in {"goal", "inches"}:
            distance = distance.title()
        self.editor.detail_edits["down_distance"].setText(down_distance(self.down_combo.currentData() or "", distance))

    def _change_ball(self) -> None:
        if self._syncing:
            return
        self._ball_changed = True
        side = self.side_combo.currentData() or ""
        number = self.ball_edit.text().strip()
        if side == "MID":
            self._syncing = True
            self.ball_edit.setText("50")
            self._syncing = False
            value = "50"
        else:
            value = f"{side} {number}".strip() if number else ""
        self.editor.detail_edits["ball_on"].setText(value)

    def refresh(self, *_args) -> None:
        if self.editor._loading_clip or self._syncing:
            return
        self._syncing = True
        try:
            d = self.editor._collect_details()
            down, distance = parse_down_distance(d.get("down_distance", ""))
            side, ball = parse_ball(d.get("ball_on", ""))
            if not self._down_changed:
                choose(self.down_combo, down)
                self.distance_edit.setText(distance)
            if not self._ball_changed:
                choose(self.side_combo, side)
                self.ball_edit.setText("" if ball is None else str(ball))
            self.ball_edit.setEnabled(self.editor._clip is not None and self.side_combo.currentData() != "MID")
            choose(self.offense_combo, d.get("offense_team_id", ""))
            field_details = dict(d)
            if hasattr(self.editor, "quick_rows") and self.game_team_ids:
                field_details["offense_team_id"] = self.game_team_ids[0]
            self.mini_field.set_details(field_details, self.game_team_ids)
            raw = []
            if d.get("down_distance") and not down:
                raw.append(d["down_distance"])
            if d.get("ball_on") and ball is None:
                raw.append(d["ball_on"])
            self.saved_situation.setText("Saved situation: " + " · ".join(raw) if raw else "")
            self.saved_situation.setVisible(bool(raw))
            if hasattr(self.editor, "quick_rows"):
                self.editor._refresh_video_context()
        finally:
            self._syncing = False

    def load_clip(self) -> None:
        self._down_changed = self._ball_changed = False
        self.setEnabled(self.editor._clip is not None)
        self.refresh()

    def validation_error(self) -> str:
        clock = self.clock_edit.text().strip()
        if clock and not re.fullmatch(r"\d{1,2}:[0-5]\d", clock):
            return "Use m:ss for the game clock, or leave it blank."
        if self._down_changed:
            value = self.editor.detail_edits["down_distance"].text().strip()
            if value and parse_down_distance(value) == ("", ""):
                return "Use a down, a distance in yards, Goal or Inches; unknown fields can stay blank."
        if self._ball_changed:
            value = self.editor.detail_edits["ball_on"].text().strip()
            if value and parse_ball(value)[1] is None:
                return "Ball position must be 0–50, with OWN or OPP where known."
        for team_id in self.game_team_ids:
            value = self.editor.detail_edits["score:"+team_id].text().strip()
            if value and not re.fullmatch(r"\d{1,3}", value):
                return "Use a score from 0–999, or leave it blank."
        return ""



class ClipDetailsV3(ClipEditor):
    """Keep ClipEditor as the edit/undo authority; only V3 adds this layout."""
    teams_requested = Signal()
    save_state_changed = Signal(str)
    source_photo_context_changed = Signal()
    logging_defaults_changed = Signal(dict)
    field_draft_changed = Signal(dict)
    number_clips_requested = Signal()
    ACTION_SHORTCUTS = {
        "Offense": ("Catch", "Run", "Explosive Play", "Contested Catch", "Broken Tackle", "Run Block", "Pass Block", "Big Gain"),
        "Defense": ("Tackle", "Big Hit", "Missed Tackle", "Pressure", "QB Hit", "PBU", "Coverage", "Forced Fumble"),
    }

    def __init__(self, *args, **kwargs) -> None:
        self.logging_defaults = {}
        self._carried_quarterback = ""
        self._draft_extra_details = {}
        self._pending_drive_action = None
        self._default_eligible = False
        self._loaded_clip_id = None
        self._field_dialog = None
        self.context_panel = None
        self.source_photo_panel = None
        self.play_timing = None
        self._pagebook_ready = False
        super().__init__(*args, **kwargs)

    def set_analyst_mode(self, enabled: bool) -> None:
        super().set_analyst_mode(enabled)
        if enabled and not self._pagebook_ready:
            self._install_pagebook()

    def _install_pagebook(self) -> None:
        header = self.heading_label.parentWidget()
        self._form_layout.removeWidget(header)
        self._outer_layout.insertWidget(0, header)
        self.context_panel = SituationPanelV3(self)
        self.context_panel.teams_requested.connect(self.teams_requested.emit)
        self._form_layout.insertWidget(0, self.context_panel)
        self._form_layout.removeWidget(self.primary_container)
        self._analyst_naming_layout.insertWidget(0, self.primary_container)
        self.summary_card.hide()
        self._form_layout.removeWidget(self.summary_card)
        self.summary_card.setParent(self.analyst_naming_container)
        self._analyst_naming_layout.insertWidget(0, self.summary_card)
        self.summary_card.show()
        for field in (self.detail_edits["ball_on"], self.detail_edits["down_distance"]):
            field.currentTextChanged.connect(self.context_panel.refresh)
        self.play_section = self._section("Play type")
        play_grid = QGridLayout()
        play_grid.setSpacing(6)
        for i, (key, title) in enumerate((("run", "Run"), ("pass", "Pass"), ("rpo_run", "RPO Run"), ("rpo_pass", "RPO Pass"), ("screen", "Screen"), ("play_action", "Play Action"))):
            button = self.classification_buttons[key]
            button.setText(title)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            play_grid.addWidget(button, i//2, i%2)
            button.show()
        self.play_section.layout().addLayout(play_grid)
        self.result_section = self._section("Result")
        result_grid = QGridLayout()
        result_grid.setSpacing(6)
        self._v3_result_buttons = {}
        for i, value in enumerate(("Completion", "First Down", "Touchdown", "Interception")):
            button = QPushButton(short_for("result", value))
            button.setCheckable(True)
            button.setProperty("inspectorTag", "true")
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, v=value: self._set_result_from_chip(v))
            result_grid.addWidget(button, i//2, i%2)
            self._v3_result_buttons[value] = button
        self.result_section.layout().addLayout(result_grid)
        more = QToolButton()
        more.setText("More results")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._v3_result_menu = QMenu(more)
        self._v3_result_menu.aboutToShow.connect(self._populate_result_menu)
        more.setMenu(self._v3_result_menu)
        self.result_section.layout().addWidget(more)
        self.result_section.layout().addWidget(self.detail_edits["result"])
        self.detail_edits["result"].show()
        self.detail_edits["result"].currentTextChanged.connect(self._sync_result_buttons)
        self._form_layout.insertWidget(1, self.play_section)
        self._form_layout.insertWidget(2, self.result_section)
        self.source_photo_panel = SourcePhotoPanel(self)
        self._form_layout.insertWidget(0, self.source_photo_panel)
        self._pagebook_ready = True
        self.details_section.toggle.toggled.connect(self._keep_naming_export_open)
        self.details_section.toggle.setStyleSheet("""
            QToolButton { background: transparent; color: #e6e8e6;
                border: 1px solid #262a2f; border-radius: 3px;
                padding: 6px 10px; text-align: left; font-weight: 600; }
            QToolButton:hover { background: #1c1f23; border-color: #3a4046; }
            QToolButton:pressed { background: #0a0b0c; }
            QToolButton:focus { border-color: #ffc27b; }
            QToolButton:disabled { color: #8d949a; border-color: #262a2f; }
        """)
        self.details_section.toggle.setToolTip("Naming and export controls stay open below.")
        self._title_form_label.setText("Clip name — click to edit")
        self._analyst_title_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.title_edit.setPlaceholderText("Enter a clip name…")
        self.title_edit.setAccessibleName("Clip name, editable")
        self.title_edit.setMinimumHeight(34)
        self.title_edit.setStyleSheet("""
            QLineEdit { background: #0d0e10; color: #e6e8e6;
                border: 1px solid #262a2f; border-radius: 3px;
                padding: 5px 9px; font-size: 13px; }
            QLineEdit:hover { border-color: #3a4046; }
            QLineEdit:focus { background: #0d0e10; border: 2px solid #ffc27b;
                padding: 4px 8px; selection-background-color: #1d7a45; }
            QLineEdit:disabled { color: #8d949a; border-color: #262a2f; }
        """)
        self._keep_naming_export_open()
        self._reflow_details()
        self.context_panel.load_clip()
        self._sync_result_buttons()

        self._install_quick_rows()

    def _install_quick_rows(self) -> None:
        """Rehouse the live editors in the selected compact worksheet."""
        self.form_area.widget().setObjectName("V3DetailsScrollBody")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.form_area.widget().setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QWidget#ClipEditor, QWidget#V3DetailsScrollBody, QWidget#InspectorHeader {
                background: #111214; border: none; }
            QScrollArea { background: #111214; border: none; }
        """)
        self.heading_label.parentWidget().layout().setContentsMargins(16, 6, 16, 2)
        self.top_save_next_btn = QPushButton("Save Next")
        self.top_save_next_btn.setAccessibleName("Save and go to next clip, top")
        self.top_save_next_btn.setToolTip("Save this clip's details and go to the next clip")
        self.top_save_next_btn.setFixedSize(92, 26)
        self.top_save_next_btn.clicked.connect(self.save_next_btn.click)
        self.top_save_next_btn.setEnabled(self.save_next_btn.isEnabled())
        self.save_next_btn.installEventFilter(self)
        self.heading_label.parentWidget().layout().addWidget(self.top_save_next_btn)
        self.quick_rows = QWidget()
        self.quick_rows.setObjectName("V3QuickRows")
        self._quick_layout = QVBoxLayout(self.quick_rows)
        self._quick_layout.setContentsMargins(0, 0, 0, 0)
        self._quick_layout.setSpacing(0)
        self._form_layout.insertWidget(0, self.quick_rows)
        self._form_layout.insertWidget(0, self.first_read_card)
        self.quick_play_number = label("")
        self.quick_play_number.setStyleSheet("font:600 22px 'IBM Plex Sans';color:#eef1f3;padding:4px 0;")
        self._quick_layout.addWidget(self.quick_play_number)
        self.video_context = QPushButton()
        self.video_context.setObjectName("V3VideoContext")
        self.video_context.setMinimumWidth(0)
        self.video_context.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.video_context.setToolTip("Set offense and defense once for this video")
        self.video_context.clicked.connect(self.teams_requested.emit)
        self._quick_layout.addWidget(self.video_context)
        self._quick_layout.addSpacing(6)
        self.qb_row = self._quick_row("Quarterback", self.detail_edits["quarterback"])
        self.detail_edits["quarterback"].lineEdit().setPlaceholderText("Choose QB once…")
        self.quarterback_scope = QComboBox()
        self.quarterback_scope.addItem("This clip only", "clip")
        self.quarterback_scope.addItem("This + future clips", "forward")
        self.quarterback_scope.setAccessibleName("Quarterback change scope")
        self.quarterback_scope.setToolTip("On Save: this clip only, or fill future unlogged clips from this point until the next substitution. Saved assignments stay unchanged.")
        self.quarterback_scope.currentIndexChanged.connect(self._mark_unsaved)
        self.qb_scope_row = self._quick_row("Apply QB to", self.quarterback_scope)
        self.play_timing = PlayTimingPanel()
        self.play_timing.details_changed.connect(self._stage_timing_details)
        self.quarter_buttons = {}
        quarters = QWidget()
        quarter_layout = QHBoxLayout(quarters)
        quarter_layout.setContentsMargins(0, 0, 0, 0)
        quarter_layout.setSpacing(4)
        for value in ("Q1", "Q2", "Q3", "Q4", "OT"):
            button = self._choice_button(value, "Quarter " + value)
            button.clicked.connect(lambda _checked=False, v=value: self.detail_edits["quarter"].setText(v))
            quarter_layout.addWidget(button, 1)
            self.quarter_buttons[value] = button
        self.carry_hint = label("")
        self.carry_hint.setStyleSheet("color:#8d949a; font-size:10px; padding:2px 0;")
        down_row = QWidget()
        down_layout = QHBoxLayout(down_row)
        down_layout.setContentsMargins(0, 0, 0, 0)
        down_layout.setSpacing(6)
        self.down_buttons = {}
        for value, caption in (("1", "1st"), ("2", "2nd"), ("3", "3rd"), ("4", "4th")):
            button = self._choice_button(caption, "Down " + caption)
            button.setFixedHeight(30)
            button.clicked.connect(lambda _checked=False, v=value: choose(self.context_panel.down_combo, v))
            # choose blocks signals; route clicks through the existing canonical down writer.
            button.clicked.connect(self.context_panel._change_down)
            down_layout.addWidget(button, 1)
            self.down_buttons[value] = button
        self.quick_ball = combo((("Not set", ""),), "Ball on")
        self.quick_ball.activated.connect(self._quick_ball_picked)
        distance_controls = QWidget()
        distance_picks = QGridLayout(distance_controls)
        distance_picks.setContentsMargins(0, 0, 0, 0)
        distance_picks.setSpacing(3)
        self._distance_picks = distance_picks
        self.distance_buttons = {}
        for yards in range(1, 11):
            button = self._choice_button(str(yards), f"Distance {yards} yards")
            button.setFixedHeight(26)
            button.setStyleSheet("QPushButton {min-height:26px;max-height:26px;padding:0;font:11px 'IBM Plex Sans';}")
            button.clicked.connect(lambda _checked=False, value=yards: self.context_panel.distance_edit.setText(str(value)))
            distance_picks.addWidget(button, (yards - 1) // 5, (yards - 1) % 5)
            self.distance_buttons[yards] = button
        self.situation_panel = QFrame()
        self.situation_panel.setProperty("gradingGroup", "true")
        situation_layout = QGridLayout(self.situation_panel)
        self._situation_layout = situation_layout
        self._situation_fields = []
        situation_layout.setContentsMargins(0, 6, 0, 8)
        situation_layout.setHorizontalSpacing(8)
        situation_layout.setVerticalSpacing(6)
        for row, column, caption, control in ((0, 0, "Quarter", quarters),
                (0, 1, "Ball on", self.quick_ball), (2, 0, "Down", down_row),
                (2, 1, "Distance", self.context_panel.distance_edit)):
            title = label(caption)
            title.setBuddy(control)
            control.setMinimumWidth(0)
            control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            control.setFixedHeight(30)
            situation_layout.addWidget(title, row, column)
            situation_layout.addWidget(control, row + 1, column)
            self._situation_fields.append((title, control))
            control.show()
        situation_layout.setColumnStretch(0, 3)
        situation_layout.setColumnStretch(1, 2)
        distance_heading = label("Quick distance")
        distance_heading.setStyleSheet("color:#a1aab4;font-size:11px;")
        self.distance_strip = QWidget()
        distance_strip_layout = QHBoxLayout(self.distance_strip)
        distance_strip_layout.setContentsMargins(0, 0, 0, 0)
        distance_strip_layout.setSpacing(6)
        distance_strip_layout.addWidget(distance_heading)
        distance_strip_layout.addWidget(distance_controls, 1)
        self._distance_heading = distance_heading
        situation_layout.addWidget(self.distance_strip, 4, 0, 1, 2)
        self._quick_layout.addWidget(self.situation_panel)
        self._quick_layout.addWidget(self.carry_hint)
        self.previous_gain_hint = label("")
        self.previous_gain_hint.setStyleSheet("color:#8d949a; font-size:11px; padding:4px 0;")
        self._quick_layout.addWidget(self.previous_gain_hint)
        self.drive_suggestion = QFrame()
        self.drive_suggestion.setObjectName("DriveSuggestion")
        self.drive_suggestion.setStyleSheet("QFrame#DriveSuggestion { background:#14160d; border-left:2px solid #d39a37; }")
        drive_layout = QVBoxLayout(self.drive_suggestion)
        drive_layout.setContentsMargins(8, 6, 6, 6)
        drive_layout.setSpacing(4)
        self.drive_title = label("")
        self.drive_title.setWordWrap(True)
        drive_layout.addWidget(self.drive_title)
        drive_actions = QHBoxLayout()
        drive_actions.setSpacing(6)
        self.drive_gain = label("")
        self.drive_gain.setStyleSheet("color:#efba50; font-weight:600;")
        drive_actions.addWidget(self.drive_gain)
        self.drive_apply = QPushButton("Apply suggestions")
        self.drive_apply.setStyleSheet("QPushButton { background:#14291c; color:#ade5bc; border:1px solid #3c6c4b; border-radius:3px; padding:5px 8px; } QPushButton:hover { border-color:#39e07a; } QPushButton:focus { border-color:#ffc27b; } QPushButton:disabled { color:#718075; border-color:#303b31; }")
        self.drive_apply.setAccessibleName("Apply same-drive suggestions")
        self.drive_apply.setToolTip("Confirm consecutive plays in the same drive. Saves these details and the previous play's suggested gain together.")
        self.drive_apply.clicked.connect(self._apply_drive_suggestion)
        drive_actions.addWidget(self.drive_apply)
        self.new_drive = QPushButton("New drive")
        self.new_drive.setStyleSheet("QPushButton { background:#191a10; color:#e5bf77; border:1px solid #77623a; border-radius:3px; padding:5px 8px; } QPushButton:hover { border-color:#e5bf77; } QPushButton:focus { border-color:#ffc27b; } QPushButton:checked { background:#403218; border-color:#e9aa3f; } QPushButton:disabled { color:#718075; border-color:#303b31; }")
        self.new_drive.setCheckable(True)
        self.new_drive.setToolTip("Mark a drive break and save. Click again to remove the break; suggestions still need your confirmation.")
        self.new_drive.clicked.connect(self._mark_new_drive)
        drive_actions.addWidget(self.new_drive)
        drive_layout.addLayout(drive_actions)
        self.drive_hint = label("")
        self.drive_hint.setWordWrap(True)
        self.drive_hint.setStyleSheet("color:#8d949a; font-size:10px;")
        drive_layout.addWidget(self.drive_hint)
        self.drive_suggestion.hide()
        self._quick_layout.addWidget(self.drive_suggestion)
        play_row = QWidget()
        self.play_controls = play_row
        play_layout = QGridLayout(play_row)
        play_layout.setContentsMargins(0, 0, 0, 0)
        play_layout.setSpacing(4)
        self.quick_play_buttons = {}
        for key, caption in (("run", "RUN"), ("pass", "PASS"), ("scramble", "Scramble")):
            button = self._choice_button(caption, "Play type " + caption)
            if key in {"run", "pass"}:
                button.setFixedHeight(44)
                button.setIcon(tinted_icon("run-24.svg" if key == "run" else "pass-football-24.svg", "#e9f3eb", 24))
                button.setIconSize(QSize(24, 24))
                edge, selected = ("#3e7957", "#245d3e") if key == "run" else ("#417fa3", "#19445f")
                button.setStyleSheet(f"QPushButton {{ background:#0b1410; color:#edf5ee; border:1px solid {edge}; border-radius:3px; font:600 17px 'IBM Plex Sans'; padding:3px; margin:0; }} QPushButton:checked {{ background:{selected}; border:1px solid #85cba5; }} QPushButton:hover {{ border:1px solid #c3e4ce; }} QPushButton:focus {{ border:2px solid #ffc27b; }} QPushButton:pressed {{ background:#0a0b0c; }} QPushButton:disabled {{ color:#8d949a; border-color:#262a2f; }}")
            button.clicked.connect(lambda _checked=False, value=key: self._quick_classification_picked(value))
            self.quick_play_buttons[key] = button
            play_layout.addWidget(button, 1 if key == "scramble" else 0, 1 if key == "pass" else 0)
        self.quick_play = combo((("Not set", ""),
            ("Screen", "screen"), ("RPO Run", "rpo_run"), ("RPO Pass", "rpo_pass"),
            ("No Play", "no_play")), "More play types")
        self.quick_play.insertItem(0, "More…", None)
        self.quick_play.setToolTip("Screen, RPO Run, RPO Pass, No Play, or clear the play type")
        self.quick_play.activated.connect(self._quick_play_picked)
        play_layout.addWidget(self.quick_play, 1, 1)
        play_layout.setColumnStretch(0, 1)
        play_layout.setColumnStretch(1, 1)
        self._quick_layout.addWidget(play_row)
        self.players_panel = QFrame()
        self.players_panel.setProperty("gradingGroup", "true")
        players_layout = QVBoxLayout(self.players_panel)
        players_layout.setContentsMargins(0, 10, 0, 12)
        players_layout.setSpacing(8)
        primary_row = self._quick_row("Primary player", self.detail_edits["player_name"])
        self._quick_layout.removeWidget(primary_row)
        primary_row.layout().setContentsMargins(0, 0, 0, 0)
        players_layout.addWidget(primary_row)
        self.detail_edits["player_name"].setFixedHeight(32)
        self.detail_edits["player_name"].lineEdit().setPlaceholderText("Name or number…")
        secondary = QWidget()
        secondary_layout = QVBoxLayout(secondary)
        secondary_layout.setContentsMargins(0, 0, 0, 0)
        secondary_layout.setSpacing(4)
        self.secondary_entry = TagLineEdit()
        self.secondary_entry.setPlaceholderText("Names separated by commas…")
        self.secondary_entry.setAccessibleName("Secondary players, comma separated")
        self.secondary_entry.textChanged.connect(self.detail_edits["other_players"].setText)
        self.secondary_entry.returnPressed.connect(self._apply_from_key)
        self.secondary_entry.hide()
        self.secondary_chips = QWidget()
        self._secondary_flow = FlowLayout(self.secondary_chips, spacing=4)
        secondary_layout.addWidget(self.secondary_chips)
        self.add_secondary_button = QToolButton()
        self.add_secondary_button.setText("+ Add player")
        self.add_secondary_button.setProperty("quickResult", "true")
        self.add_secondary_button.setAccessibleName("Add or edit secondary players")
        self.add_secondary_button.clicked.connect(self._edit_secondary_players)
        secondary_layout.addWidget(self.secondary_entry)
        secondary_row = self._quick_row("Secondary players", secondary)
        self._quick_layout.removeWidget(secondary_row)
        secondary_row.layout().setContentsMargins(0, 0, 0, 0)
        players_layout.addWidget(secondary_row)
        self._quick_layout.addWidget(self.players_panel)
        self.quick_results = QWidget()
        self.quick_results.setObjectName("V3QuickResults")
        results_layout = QVBoxLayout(self.quick_results)
        results_layout.setContentsMargins(0, 10, 0, 12)
        results_layout.setSpacing(8)
        result_heading = QHBoxLayout()
        result_title = label("Result")
        result_title.setStyleSheet("font:600 15px 'IBM Plex Sans';color:#eef1f3;")
        result_heading.addWidget(result_title)
        result_heading.addStretch()
        self.quick_result_more = QToolButton()
        self.quick_result_more.setText("All results")
        self.quick_result_more.setIcon(tinted_icon("chevron-down-16.svg", "#bdc8d2", 12))
        self.quick_result_more.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.quick_result_more.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.quick_result_more.setAccessibleName("Search all results")
        self.quick_result_more.setToolTip("Search, select, or add a result")
        self.quick_result_more.setFixedHeight(30)
        self.quick_result_more.setStyleSheet("QToolButton { background:transparent; color:#e6e8e6; border:1px solid #262a2f; border-radius:3px; padding:4px 7px; font:12px 'IBM Plex Sans'; } QToolButton:hover { background:#1c1f23; border-color:#3a4046; } QToolButton:focus { border-color:#ffc27b; } QToolButton:pressed { background:#0a0b0c; } QToolButton:disabled { color:#8d949a; border-color:#262a2f; }")
        self.quick_result_more.clicked.connect(self._show_results_picker)
        result_heading.addWidget(self.quick_result_more)
        self.quick_result_edit = QPushButton("Edit")
        self.quick_result_edit.setIcon(tinted_icon("pencil-24.svg", "#e6e8e6"))
        self.quick_result_edit.setAccessibleName("Edit result quick buttons")
        self.quick_result_edit.setToolTip("Choose and arrange your 16 result shortcuts")
        self.quick_result_edit.setFixedHeight(30)
        tint_button(self.quick_result_edit, "neutral")
        self.quick_result_edit.clicked.connect(self._edit_result_shortcuts)
        result_heading.addWidget(self.quick_result_edit)
        results_layout.addLayout(result_heading)
        self._quick_favorites_grid = QGridLayout()
        self._quick_favorites_grid.setSpacing(5)
        self.quick_result_buttons = {}
        self._v3_shortcuts = self.settings.v3_result_favorites if self.settings else None
        self._v3_custom_results = []
        results_layout.addLayout(self._quick_favorites_grid)
        self.yardage_panel = QWidget()
        yardage_outer = QVBoxLayout(self.yardage_panel)
        yardage_outer.setContentsMargins(0, 0, 0, 0)
        yardage_outer.setSpacing(6)
        self.quick_gain_buttons = {}
        for key, title, palette in (("yards", "Gain", "neutral"), ("yac", "YAC", "cool")):
            well = QFrame()
            well.setObjectName("V3GainPanel" if key == "yards" else "V3YacPanel")
            well.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            layout = QHBoxLayout(well)
            layout.setContentsMargins(3, 3, 3, 3)
            layout.setSpacing(2)
            caption = label(title)
            layout.addWidget(caption)
            edit = self.detail_edits[key]
            edit.set_integer_mode(True)
            edit.setFixedWidth(26 if key == "yards" else 44)
            edit.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            edit.setFixedHeight(28)
            edit.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)
            edit.lineEdit().setPlaceholderText("—")
            edit.setAccessibleName("Total gain in yards" if key == "yards" else "Yards after catch")
            edit.setToolTip("Enter yards; blank means not measured. Use yd ▾ for quick adjustments.")
            edit.setStyleSheet("QComboBox {background:#101719;border:1px solid #38444b;padding:0;min-height:24px;max-height:24px;font-size:13px;font-weight:600;} QComboBox:focus {border-color:#ffc27b;} QComboBox::drop-down {width:0;border:0;} QComboBox::down-arrow {image:none;}")
            edit.lineEdit().setStyleSheet("background:transparent;border:0;color:#e6e8e6;font-size:13px;font-weight:600;padding:0;")
            units = QToolButton()
            units.setText("yd")
            units.setFixedWidth(18 if key == "yards" else 22)
            units.setAccessibleName("Gain presets" if key == "yards" else "YAC adjustments")
            units.setToolTip("Gain presets: 5, 10, 20 yards" if key == "yards" else "Adjust yards after catch")
            units.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            units.setStyleSheet("QToolButton {background:transparent;border:0;padding:0 4px 0 0;color:#c3ccd4;font:11px 'IBM Plex Sans';} QToolButton:focus {border:1px solid #ffc27b;} QToolButton::menu-indicator {width:4px;}")
            menu = QMenu(units)
            units.setMenu(menu)
            adjustment_panel = QWidget()
            adjustments = QVBoxLayout(adjustment_panel)
            adjustments.setContentsMargins(8, 8, 8, 8)
            presets = QHBoxLayout()
            if key == "yards":
                for yards in (5, 10, 20):
                    button = self._choice_button(str(yards), f"Set gain to {yards} yards")
                    button.setCheckable(False)
                    button.setFixedSize(38, 28)
                    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                    tint_button(button, "sand")
                    button.clicked.connect(lambda _=False, y=yards: self.detail_edits["yards"].setText(str(y)))
                    button.clicked.connect(menu.close)
                    self.quick_gain_buttons[yards] = button
                    presets.addWidget(button, 1)
            adjustments.addLayout(presets)
            buttons = []
            for caption, delta in (("−", -1), ("+", 1)):
                button = self._choice_button(caption, f"{'Increase' if delta > 0 else 'Decrease'} {'gain' if key == 'yards' else 'YAC'} by one yard")
                button.setCheckable(False)
                button.setFixedSize(22, 28)
                button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                tint_button(button, palette)
                button.setStyleSheet(button.styleSheet() + "QPushButton {min-width:22px;max-width:22px;min-height:26px;max-height:26px;padding:0;}")
                button.clicked.connect(lambda _=False, k=key, d=delta: self._adjust_yardage(k, d))
                buttons.append(button)
            if key == "yards":
                layout.addWidget(buttons[0])
            else:
                for button in buttons:
                    presets.addWidget(button)
            layout.addWidget(edit, 1)
            layout.addWidget(units)
            if key == "yards":
                layout.addWidget(buttons[1])
            else:
                layout.addWidget(label("after catch"))
                layout.addStretch(1)
            status = label("Not entered" if key == "yards" else "Enter when known")
            status.setStyleSheet("font-size:10px;color:#8d949a;")
            adjustments.addWidget(status)
            action = QWidgetAction(menu)
            action.setDefaultWidget(adjustment_panel)
            menu.addAction(action)
            yardage_outer.addWidget(well)
            if key == "yards":
                self.gain_panel, self.gain_status = well, status
                self.gain_decrease_btn, self.gain_increase_btn = buttons
                self.gain_units_button = units
                self.gain_caption = layout.itemAt(0).widget()
                well.setFixedHeight(36)
            else:
                self.yac_panel, self.yac_status = well, status
                self.yac_decrease_btn, self.yac_increase_btn = buttons
                self.yac_units_button = units
                self.yac_caption = layout.itemAt(0).widget()
        self._rebuild_quick_result_buttons()
        results_layout.addWidget(self.yardage_panel)
        self.gain_origin = label("Calculated from the next play · edit Gain to override")
        self.gain_origin.setStyleSheet("color:#8d949a;font-size:10px;")
        self.gain_origin.hide()
        results_layout.addWidget(self.gain_origin)
        self.quick_result_extras = QWidget()
        self._quick_result_flow = FlowLayout(self.quick_result_extras, spacing=5)
        results_layout.addWidget(self.quick_result_extras)
        self.result_warning = label("")
        self.result_warning.setWordWrap(True)
        self.result_warning.setStyleSheet("color:#c7b58b;font-size:11px;")
        self.result_warning.hide()
        results_layout.addWidget(self.result_warning)
        self._quick_layout.addWidget(self.quick_results)
        actions = QWidget()
        actions.setObjectName("V3QuickActions")
        actions_layout = QVBoxLayout(actions)
        actions_layout.setContentsMargins(0, 10, 0, 12)
        actions_layout.setSpacing(5)
        action_heading = QHBoxLayout()
        action_title = label("Actions")
        action_title.setStyleSheet("font:600 15px 'IBM Plex Sans';color:#eef1f3;")
        action_heading.addWidget(action_title)
        action_heading.addStretch()
        self.action_template = QComboBox()
        self.action_template.addItems(self.ACTION_SHORTCUTS)
        self.action_template.setAccessibleName("Action template")
        action_heading.addWidget(self.action_template)
        self.edit_actions_button = QPushButton("Edit 8")
        tint_button(self.edit_actions_button, "neutral")
        self.edit_actions_button.setToolTip("Customize the eight shortcuts in this template")
        self.edit_actions_button.clicked.connect(self._edit_action_shortcuts)
        action_heading.addWidget(self.edit_actions_button)
        actions_layout.addLayout(action_heading)
        action_grid = QGridLayout()
        action_grid.setSpacing(5)
        self.action_buttons = []
        shortcuts = self.settings.action_shortcuts if self.settings else {}
        self._action_shortcuts = deepcopy(shortcuts) if isinstance(shortcuts, dict) else {}
        for index in range(8):
            button = QPushButton()
            button.setCheckable(True)
            button.setProperty("resultFavorite", "true")
            button.setFixedHeight(36)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, i=index: self._pick_action(i))
            tint_button(button, "neutral")
            self.action_buttons.append(button)
            action_grid.addWidget(button, index // 4, index % 4)
            action_grid.setColumnStretch(index % 4, 1)
        actions_layout.addLayout(action_grid)
        self.detail_edits["action"].enable_multiple_values()
        actions_layout.addWidget(self.detail_edits["action"])
        self.action_template.currentTextChanged.connect(self._sync_action_buttons)
        self.detail_edits["action"].currentTextChanged.connect(self._sync_action_buttons)
        self._quick_layout.addWidget(actions)
        self._quick_layout.addWidget(self.play_timing)
        self._sync_action_buttons()
        self.detail_edits["action"].lineEdit().setPlaceholderText("Select actions or type separated by ;")
        self.detail_edits["action"].setToolTip("Select multiple actions, such as Coverage; PBU; Blitz. Click again to remove one.")
        self.notes_panel = QFrame()
        self.notes_panel.setProperty("gradingGroup", "true")
        notes_layout = QVBoxLayout(self.notes_panel)
        notes_layout.setContentsMargins(0, 10, 0, 12)
        notes_layout.setSpacing(7)
        notes_heading = QHBoxLayout()
        notes_title = label("Notes")
        notes_title.setStyleSheet("font:600 15px 'IBM Plex Sans';color:#eef1f3;")
        notes_heading.addWidget(notes_title, 1)
        self.expand_notes_button = QPushButton("Expand notes")
        self.expand_notes_button.setCheckable(True)
        tint_button(self.expand_notes_button, "neutral")
        self.expand_notes_button.toggled.connect(self._resize_notes)
        notes_heading.addWidget(self.expand_notes_button)
        notes_layout.addLayout(notes_heading)
        notes_layout.addWidget(self.notes_edit)
        self._quick_layout.addWidget(self.notes_panel)
        self._resize_notes()
        self.notes_edit.setTabChangesFocus(True)
        self.notes_edit.setPlaceholderText("What stood out on this play?")
        self.quick_name_label = label("Clip name / Auto")
        self._quick_layout.addWidget(self.quick_name_label)
        self._quick_name_row = QHBoxLayout()
        self._quick_name_row.setSpacing(6)
        self._quick_name_row.addWidget(self.title_edit, 1)
        self._quick_name_row.addWidget(self.autoname_btn)
        self.autoname_btn.setText("Auto-Name")
        self.autoname_btn.setFixedWidth(86)
        self.autoname_btn.setObjectName("V3QuickAutoName")
        self.title_edit.setMinimumWidth(0)
        self.title_edit.setMinimumHeight(30)
        self.title_edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._quick_layout.addLayout(self._quick_name_row)
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.preview_label.setToolTip("Final filename, including the play-number prefix. Updates before export.")
        self.preview_label.setStyleSheet(
            "color:#8d949a; background:#0d0e10; border:1px solid #262a2f; padding:4px;")
        self.preview_label.setMinimumWidth(0)
        self.preview_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._quick_layout.addWidget(self.preview_label)
        self.number_clips_button = QPushButton("Number clips in film order")
        self.number_clips_button.setToolTip(
            "Number every clip 001, 002, 003… by source time. "
            "Updates the filename preview now; keeps your names. Ctrl+Z to undo.")
        self.number_clips_button.setFixedHeight(28)
        self.number_clips_button.clicked.connect(self.number_clips_requested.emit)
        self._quick_layout.addWidget(self.number_clips_button)
        self._quick_save_row = QHBoxLayout()
        self._quick_save_row.setSpacing(8)
        self._quick_save_row.addStretch(1)
        self._quick_save_row.addWidget(self.apply_btn, 1)
        self._quick_save_row.addWidget(self.save_next_btn, 2)
        self.apply_btn.setIcon(tinted_icon("save-20.svg", "#e6e8e6", 16))
        self.save_next_btn.setIcon(tinted_icon("save-next-20.svg", "#07130b", 16))
        self.apply_btn.setIconSize(QSize(16, 16))
        self.save_next_btn.setIconSize(QSize(16, 16))
        self._quick_layout.addLayout(self._quick_save_row)
        from tapesift.ui_v3.play_field import PlayFieldWidget
        old_field = self.context_panel.mini_field
        old_field.hide()
        self.context_panel.mini_field = PlayFieldWidget(self, compact=True)
        self.context_panel.mini_field.request_edit.connect(self.open_field_editor)
        self.context_panel.mini_field.yard_clicked.connect(lambda *_args: self.open_field_editor())
        self.context_panel.mini_field.setFixedHeight(112)
        self._quick_layout.addWidget(self.context_panel.mini_field)
        self._quick_layout.addWidget(self.context_panel.saved_situation)
        self._quick_layout.addSpacing(8)
        # Keep the existing controls and save signals; only their containers change.
        self.pinned_play = QWidget(self)
        self.pinned_play.setObjectName("V3PinnedPlay")
        self.pinned_play.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        pinned_layout = QVBoxLayout(self.pinned_play)
        pinned_layout.setContentsMargins(16, 0, 16, 10)
        pinned_layout.setSpacing(4)
        for widget in (self.quick_play_number, self.video_context, self.play_controls):
            self._quick_layout.removeWidget(widget)
            pinned_layout.addWidget(widget)
        self._outer_layout.insertWidget(1, self.pinned_play)
        for widget in (self.qb_row, self.qb_scope_row, self.drive_suggestion, self.number_clips_button):
            self._quick_layout.removeWidget(widget)
            self._quick_layout.addWidget(widget)
        self.pinned_save = QWidget(self)
        self.pinned_save.setObjectName("V3PinnedSave")
        self.pinned_save.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer_layout = QVBoxLayout(self.pinned_save)
        footer_layout.setContentsMargins(16, 8, 16, 10)
        footer_layout.setSpacing(5)
        for widget in (self.quick_name_label,):
            self._quick_layout.removeWidget(widget)
            footer_layout.addWidget(widget)
        self._quick_layout.removeItem(self._quick_name_row)
        footer_layout.addLayout(self._quick_name_row)
        self._quick_layout.removeWidget(self.preview_label)
        footer_layout.addWidget(self.preview_label)
        self.preview_label.setMaximumHeight(42)
        self._quick_layout.removeItem(self._quick_save_row)
        footer_layout.addLayout(self._quick_save_row)
        self._outer_layout.addWidget(self.pinned_save)
        for control in self.findChildren(QComboBox) + self.findChildren(QAbstractSpinBox):
            control.installEventFilter(self)
            if control.lineEdit() is not None:
                control.lineEdit().installEventFilter(self)
        extra_context = QWidget()
        extra_context_layout = QVBoxLayout(extra_context)
        extra_context_layout.setContentsMargins(0, 0, 0, 0)
        extra_context_layout.addWidget(label("Game clock"))
        extra_context_layout.addWidget(self.context_panel.clock_edit)
        for grid, _logo, _name, _empty in self.context_panel.score_cells:
            extra_context_layout.addWidget(grid.parentWidget())
        self.advanced_details_section.body_layout.addWidget(extra_context)
        # Export/range and less frequent fields remain reachable below logging.
        self._form_layout.removeWidget(self.details_section)
        self.advanced_details_section.body_layout.addWidget(self.details_section)
        self.quick_rows.setStyleSheet("""
            QWidget#V3QuickRows { background: #11171b; color: #e6e8e6; }
            QWidget[gradingGroup="true"], QWidget#V3QuickResults, QWidget#V3QuickActions,
            QFrame[quickRow="true"] { background: transparent; border: none; border-bottom: 1px solid #3b4751; border-radius: 0; }
            QWidget[gradingGroup="true"] QFrame[quickRow="true"] { background: transparent; border: none; }
            QFrame#V3GainPanel { background:#101619; border:1px solid #344049; border-radius:3px; }
            QFrame#V3GainPanel[selected="true"] { background:#204a35; border:2px solid #249356; }
            QFrame#V3YacPanel { background:transparent; border:none; }
            QLabel { color: #e6e8e6; background: transparent; font-family: 'IBM Plex Sans'; font-size: 13px; border: none; }
            QComboBox, QLineEdit, QPlainTextEdit { background: #0d0e10; color: #e6e8e6;
                border: 1px solid #262a2f; border-radius: 3px; padding: 4px 6px;
                font-family: 'IBM Plex Sans'; font-size: 13px; min-height: 20px; }
            QComboBox QLineEdit { border: none; padding: 0; min-height: 0; background: transparent; }
            QComboBox:hover, QLineEdit:hover, QPlainTextEdit:hover { border-color: #3a4046; }
            QComboBox:focus, QLineEdit:focus, QPlainTextEdit:focus { border-color: #ffc27b; }
            QComboBox:disabled, QLineEdit:disabled, QPlainTextEdit:disabled { color: #8d949a; border-color: #262a2f; }
            QPushButton#V3VideoContext { color: #8d949a; border: none; background: transparent;
                text-align: left; padding: 5px 0; font-family: 'IBM Plex Sans'; font-size: 11px; }
            QPushButton#V3VideoContext:hover { color: #e6e8e6; }
            QPushButton#V3VideoContext:focus { color: #e6e8e6; }
            QPushButton#V3QuickAutoName { background: #111214; color: #e6e8e6;
                border: 1px solid #262a2f; border-radius: 3px; padding: 5px; }
            QPushButton#V3QuickAutoName:hover { background: #1c1f23; border-color: #3a4046; }
            QPushButton#V3QuickAutoName:focus { border-color: #ffc27b; }
            QPushButton#V3QuickAutoName:disabled { color: #8d949a; border-color: #262a2f; }
            QToolButton[quickResult="true"] { background: #111214; color: #e6e8e6;
                border: 1px solid #262a2f; border-radius: 3px; padding: 5px 7px; font: 12px 'IBM Plex Sans'; }
            QToolButton[quickResult="true"]:hover { background: #1c1f23; border-color: #3a4046; }
            QToolButton[quickResult="true"]:focus { border-color: #ffc27b; }
            QToolButton[quickResult="true"]:pressed { background: #0a0b0c; }
            QToolButton[quickResult="true"]:disabled { color: #8d949a; border-color: #262a2f; }
            QPushButton[resultFavorite="true"] { background:#111214; color:#e6e8e6;
                border:1px solid #262a2f; border-radius:3px; padding:3px 2px;
                font:12px 'IBM Plex Sans'; }
            QPushButton[resultFavorite="true"]:hover { background:#1c1f23; border-color:#3a4046; }
            QPushButton[resultFavorite="true"]:checked { background:#163a26; color:#c9f2d7; border-color:#1d7a45; }
            QPushButton[resultFavorite="true"]:pressed { background:#0a0b0c; }
            QPushButton[resultFavorite="true"]:focus { border-color:#ffc27b; }
            QPushButton[resultFavorite="true"]:disabled { color:#8d949a; border-color:#262a2f; background:#111214; }
        """)
        for edit in self.detail_edits.values():
            edit.currentTextChanged.connect(self._sync_quick_rows)
        self.detail_edits["yards"].currentTextChanged.connect(self._yards_changed)
        self.pinned_play.setStyleSheet(self.quick_rows.styleSheet() + "QWidget#V3PinnedPlay { background:#16181b; border-bottom:1px solid #262a2f; }")
        self.pinned_save.setStyleSheet(self.quick_rows.styleSheet() + "QWidget#V3PinnedSave { background:#16181b; border-top:1px solid #262a2f; }")
        self.title_edit.textChanged.connect(self._sync_quick_name_label)
        self.autoname_btn.clicked.connect(self._sync_quick_name_label)
        self._reflow_details()
        self.apply_btn.setStyleSheet("""
            QPushButton { color:#e6e8e6; background:#111214; border:1px solid #1d7a45;
                border-radius:3px; padding:5px 10px; font:13px 'IBM Plex Sans'; }
            QPushButton:hover { background:#1c1f23; border-color:#39e07a; }
            QPushButton:pressed { background:#0a0b0c; }
            QPushButton:focus { border-color:#ffc27b; }
            QPushButton:disabled { color:#8d949a; background:#111214; border-color:#262a2f; }
        """)
        self.save_next_btn.setStyleSheet("""
            QPushButton { color:#062312; background:#39e07a; border:1px solid #39e07a;
                border-radius:3px; padding:5px 10px; font:13px 'IBM Plex Sans'; }
            QPushButton:hover { background:#4ae88a; border-color:#4ae88a; }
            QPushButton:pressed { background:#2fbe68; border-color:#2fbe68; }
            QPushButton:focus { background:#4ae88a; border-color:#ffc27b; }
            QPushButton:disabled { background:#1a3a28; color:#5f8a70; border-color:#1a3a28; }
        """)
        self.top_save_next_btn.setStyleSheet(self.save_next_btn.styleSheet() + """
            QPushButton { min-height:0; padding:2px 6px; font-size:11px; }
            QPushButton:disabled { background:#1a3a28; color:#5f8a70; border-color:#1a3a28; }
        """)
        self._sync_quick_rows()
        self.context_panel.refresh()

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            focus = QApplication.focusWidget()
            # Focused form controls own activation/navigation, not the video transport.
            if isinstance(focus, (QAbstractButton, QComboBox)) and self.isAncestorOf(focus):
                keys = (Qt.Key.Key_Space, Qt.Key.Key_Left, Qt.Key.Key_Right)
                if isinstance(focus, QComboBox):
                    keys += (Qt.Key.Key_W, Qt.Key.Key_B, Qt.Key.Key_E)
                if event.modifiers() == Qt.KeyboardModifier.NoModifier and event.key() in keys:
                    event.accept()
                    return True
        return super().event(event)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Wheel and hasattr(self, "pinned_play"):
            control = watched if isinstance(watched, (QComboBox, QAbstractSpinBox)) else watched.parentWidget()
            if isinstance(control, (QComboBox, QAbstractSpinBox)):
                if isinstance(control, QComboBox) and (control.view().isVisible() or QApplication.activePopupWidget() is not None):
                    return super().eventFilter(watched, event)
                QApplication.sendEvent(self.form_area.viewport(), event)
                return True
        if watched is self.save_next_btn and event.type() == QEvent.Type.EnabledChange:
            self.top_save_next_btn.setEnabled(self.save_next_btn.isEnabled())
        return super().eventFilter(watched, event)

    @staticmethod
    def _choice_button(caption: str, accessible: str) -> QPushButton:
        button = QPushButton(caption)
        button.setCheckable(True)
        button.setProperty("resultFavorite", "true")
        button.setAccessibleName(accessible)
        button.setMinimumWidth(0)
        button.setFixedHeight(28)
        button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        return button

    def _populate_penalty_menu(self) -> None:
        self.penalty_menu.clear()
        current = self.detail_edits["result"].text()
        for value in STANDARD_RESULT_CHOICES:
            item = lookup("result", value)
            if item and item.category == "Penalty":
                action = self.penalty_menu.addAction(value)
                action.setCheckable(True)
                action.setChecked(result_service.has_result(current, value))
                action.triggered.connect(lambda _checked=False, v=value: self._set_result_from_chip(v))

    def _resize_notes(self, *_args):
        expanded = hasattr(self, "expand_notes_button") and self.expand_notes_button.isChecked()
        self.notes_edit.setFixedHeight(180 if expanded else 76)
        if hasattr(self, "expand_notes_button"):
            self.expand_notes_button.setText("Collapse notes" if expanded else "Expand notes")

    def _quick_row(self, caption: str, control: QWidget) -> QFrame:
        row = QFrame()
        row.setProperty("quickRow", "true")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 4, 0, 6)
        layout.setSpacing(8)
        title = label(caption.replace("&", "&&"))
        title.setFixedWidth(112)
        title.setBuddy(control)
        layout.addWidget(title)
        control.setMinimumWidth(0)
        control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addWidget(control, 1)
        control.show()
        self._quick_layout.addWidget(row)
        return row

    def _edit_secondary_players(self) -> None:
        self.secondary_entry.show()
        current = self.secondary_entry.text().rstrip()
        if current and not current.endswith(","):
            # Opening the entry prepares an append without staging a metadata edit.
            blocked = self.secondary_entry.blockSignals(True)
            self.secondary_entry.setText(current + ", ")
            self.secondary_entry.blockSignals(blocked)
        self.secondary_entry.setFocus()
        self.secondary_entry.setCursorPosition(len(self.secondary_entry.text()))

    def _previous_drive_clip(self):
        provider = getattr(self, "previous_clip_provider", None)
        return provider(self._clip) if provider and self._clip else None

    def _sync_drive_suggestion(self) -> None:
        previous = self._previous_drive_clip()
        self.drive_suggestion.setVisible(previous is not None)
        self.previous_gain_hint.setVisible(previous is not None)
        if previous is None:
            return
        gain = parse_yards(previous.details.get("yards", ""))
        self.previous_gain_hint.setText(f"Previous play {previous.clip_number:03d}: " + (f"{gain:+d} yd" if gain is not None else "gain unknown"))
        current = self._collect_details()
        suggestion = drive_suggestions.suggest(previous, current)
        number = f"{previous.clip_number:03d}"
        def display_spot(value):
            index = self.quick_ball.findData(value)
            return self.quick_ball.itemText(index) if index >= 0 else value
        before_spot = display_spot(previous.details.get('ball_on', 'Ball on not set'))
        now_spot = display_spot(current.get('ball_on', '?'))
        self.drive_title.setText(f"Previous play {number} · {before_spot} → {now_spot}")
        self.drive_gain.setText(f"{suggestion.previous_gain:+d} yd?" if suggestion.previous_gain is not None else "")
        self.drive_apply.setText(f"Apply to {number}" if suggestion.previous_gain is not None else "Apply suggestions")
        self.drive_apply.setEnabled(suggestion.previous_gain is not None or bool(suggestion.current))
        blocked = self.new_drive.blockSignals(True)
        self.new_drive.setChecked(current.get("drive_start") == "1")
        self.new_drive.blockSignals(blocked)
        fills = " · ".join(suggestion.current.values())
        self.drive_hint.setText(f"This play: {fills} · Same drive? Apply." if fills else suggestion.message)
        if (not fills and suggestion.previous_gain is None and current.get("previous_play_id") == previous.id
                and suggestion.message.startswith("Confirm these")):
            self.drive_hint.setText("Same drive confirmed · saved")

    def _apply_drive_suggestion(self) -> None:
        previous = self._previous_drive_clip()
        if previous is None:
            return
        suggestion = drive_suggestions.suggest(previous, self._collect_details())
        if suggestion.previous_gain is None and not suggestion.current:
            return
        for key, value in suggestion.current.items():
            self.detail_edits[key].setText(value)
        self._draft_extra_details["previous_play_id"] = previous.id
        self._pending_drive_action = ("apply", previous.id, suggestion.previous_gain)
        self._apply()

    def _mark_new_drive(self, checked: bool) -> None:
        previous = self._previous_drive_clip()
        self._draft_extra_details["drive_start"] = "1" if checked else None
        self._draft_extra_details["previous_play_id"] = None
        self._pending_drive_action = ("break", previous.id, None) if checked and previous else None
        self._apply()

    def _quick_classification_picked(self, key: str) -> None:
        # Switching away from Scramble clears that mutually exclusive choice,
        # while Run/Pass still preserve concepts such as RPO and Inside Zone.
        if key in {"run", "pass"} and self.detail_edits["play_type"].text().strip().casefold() == "scramble":
            self.detail_edits["play_type"].clear()
        self._set_classification_from_chip(key)

    def _quick_play_picked(self, index: int) -> None:
        key = self.quick_play.itemData(index)
        if key:
            self._quick_classification_picked(key)
        elif key == "":
            for field in ("run_pass", "play_type", "play_action"):
                self.detail_edits[field].clear()

    def _quick_ball_picked(self, index: int) -> None:
        value = self.quick_ball.itemData(index)
        if value is not None:
            self.context_panel._ball_changed = True
            self.detail_edits["ball_on"].setText(value)

    def _sync_quick_name_label(self, *_args) -> None:
        self.quick_name_label.setText("Clip name / Auto" if self._automatic_name else "Clip name / Custom")
        self.title_edit.setToolTip("Click to edit. Typing keeps your custom name; Auto-Name resumes updates.")
        if self._automatic_name and not self.title_edit.hasFocus():
            self.title_edit.setCursorPosition(0)

    def _adjust_gain(self, delta: int) -> None:
        self._adjust_yardage("yards", delta)

    def _adjust_yardage(self, key: str, delta: int) -> None:
        edit = self.detail_edits[key]
        value = parse_yards(edit.text())
        if value is None and edit.text().strip():
            edit.setFocus()
            return
        validator = edit.lineEdit().validator()
        edit.setText(str(max(validator.bottom(), min(validator.top(), (value or 0) + delta))))

    def _yards_changed(self, *_args) -> None:
        if self._loading_clip or self._clip is None:
            return
        value = parse_yards(self.detail_edits["yards"].text())
        if value is None:
            return
        result = self.detail_edits["result"]
        outcome = "Gain" if value > 0 else "Loss" if value < 0 else "No Gain"
        result.setText(result_service.add_results(result.text(), outcome))

    def _action_values(self) -> list[str]:
        template = self.action_template.currentText()
        values = self._action_shortcuts.get(template)
        if template == "Offense" and isinstance(values, (list, tuple)) and tuple(values) == (
                "Catch", "Run", "One-Handed Catch", "Contested Catch",
                "Broken Tackle", "Run Block", "Pass Block", "Route"):
            values = self.ACTION_SHORTCUTS[template]
        if not isinstance(values, (list, tuple)) or len(values) != 8 or not all(isinstance(v, str) and v.strip() for v in values):
            values = self.ACTION_SHORTCUTS[template]
        return list(values)

    def _sync_action_buttons(self, *_args) -> None:
        current = {value.casefold() for value in result_service.split_results(self.detail_edits["action"].text())}
        for button, value in zip(self.action_buttons, self._action_values()):
            lines = value.split(" ", 1)
            button.setText("\n".join(button.fontMetrics().elidedText(
                line, Qt.TextElideMode.ElideRight, max(30, button.width()-8)) for line in lines))
            button.setAccessibleName(value)
            button.setToolTip(value + " — click to select or clear")
            button.setChecked(value.casefold() in current)

    def _pick_action(self, index: int) -> None:
        value = self._action_values()[index]
        edit = self.detail_edits["action"]
        edit.toggle_value(value)

    def _edit_action_shortcuts(self) -> None:
        template = self.action_template.currentText()
        dialog = QDialog(self)
        dialog.setWindowTitle(template + " action shortcuts")
        form = QFormLayout(dialog)
        edits = []
        for index, value in enumerate(self._action_values(), 1):
            edit = QLineEdit(value)
            edit.setMaxLength(60)
            form.addRow(f"Button {index}", edit)
            edits.append(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons)
        def save():
            values = [" ".join(edit.text().split()) for edit in edits]
            if not all(values) or len({v.casefold() for v in values}) != 8:
                QMessageBox.warning(dialog, "Check shortcuts", "Give all eight buttons different, non-empty names.")
                return
            if self.settings:
                candidate = deepcopy(self.settings)
                candidate.action_shortcuts = deepcopy(self._action_shortcuts)
                candidate.action_shortcuts[template] = values
                try:
                    candidate.save()
                except Exception as exc:
                    QMessageBox.warning(dialog, "Shortcuts were not saved", str(exc))
                    return
                self.settings.action_shortcuts = candidate.action_shortcuts
            self._action_shortcuts[template] = values
            self._sync_action_buttons()
            dialog.accept()
        buttons.accepted.connect(save)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()
        dialog.deleteLater()

    def _sync_quick_rows(self, *_args) -> None:
        if not hasattr(self, "quick_rows") or self._loading_clip:
            return
        d = self._collect_details()
        quarter = d.get("quarter", "").upper()
        for value, button in self.quarter_buttons.items():
            button.setChecked(quarter in {value, value.removeprefix("Q")})
        down, _ = parse_down_distance(d.get("down_distance", ""))
        for value, button in self.down_buttons.items():
            button.setChecked(down == value)
        blocked = self.secondary_entry.blockSignals(True)
        self.secondary_entry.setText(self.detail_edits["other_players"].text())
        self.secondary_entry.blockSignals(blocked)
        self.secondary_entry.set_known_tags([*getattr(self, "_player_options", []), *self._remembered_player_names])
        players = tuple(dict.fromkeys(detail_service.split_players(d.get("other_players", ""))))
        if players != getattr(self, "_secondary_players", None):
            self._secondary_players = players
            self._secondary_flow.removeWidget(self.add_secondary_button)
            self._secondary_flow.clear()
            for name in players:
                button = QToolButton()
                button.setText(name + " ×")
                button.setProperty("quickResult", "true")
                button.setMaximumWidth(max(80, self.width() - 148))
                button.setToolTip(name + " — remove secondary player")
                button.setAccessibleName("Remove secondary player " + name)
                button.clicked.connect(lambda _checked=False, n=name: self.detail_edits["other_players"].setText(
                    ", ".join(p for p in self._secondary_players if p != n)))
                self._secondary_flow.addWidget(button)
            self._secondary_flow.addWidget(self.add_secondary_button)
        carried = [d.get(k, "") for k in ("quarter", "quarterback") if d.get(k) and d.get(k) == (
            self._carried_quarterback if k == "quarterback" else self.logging_defaults.get(k))]
        self.carry_hint.setText((" · ".join(carried) + " · carried forward; change before saving") if self._default_eligible and carried else "")
        self.carry_hint.setVisible(bool(self.carry_hint.text()))
        description = " · ".join(d.get(k, "") for k in ("run_pass", "play_type", "play_action") if d.get(k)) or "Not set"
        scramble = d.get("play_type", "").casefold() == "scramble"
        for key, button in self.quick_play_buttons.items():
            button.setChecked(scramble if key == "scramble" else
                              not scramble and d.get("run_pass", "").casefold() == key)
        blocked = self.quick_play.blockSignals(True)
        # One temporary display item preserves compound/custom classification.
        if self.quick_play.count() > 6:
            self.quick_play.removeItem(6)
        simple = description in {"Run", "Pass", "Run · Scramble"}
        index = 0 if simple else self.quick_play.findText(description)
        if index < 0:
            self.quick_play.addItem(description, None)
            index = self.quick_play.count()-1
        self.quick_play.setCurrentIndex(index)
        self.quick_play.setToolTip("Current: " + description + "\nMore play types")
        self.quick_play.blockSignals(blocked)
        stored = d.get("result", "")
        conflicts = result_service.result_conflicts(stored)
        self.result_warning.setText("Check results: " + " ".join(conflicts) if conflicts else "")
        self.result_warning.setVisible(bool(conflicts))
        for value, button in self.quick_result_buttons.items():
            selected = result_service.has_result(stored, value)
            button.setChecked(selected)
            self._update_result_caption(value, button)
        selected_gain = result_service.has_result(stored, "Gain")
        if self.gain_panel.property("selected") != selected_gain:
            self.gain_panel.setProperty("selected", selected_gain)
            self._refresh_widget_style(self.gain_panel)
        gain = parse_yards(d.get("yards", ""))
        self.gain_status.setText("Not entered" if gain is None else f"{gain:+d} yd")
        yac = parse_yards(d.get("yac", ""))
        self.yac_status.setText("Enter when known" if yac is None else f"{yac:+d} yd after catch")
        picker = getattr(self, "_results_picker", None)
        if picker is not None:
            picker.sync(stored)
        self._quick_result_flow.clear()
        for value in result_service.split_results(stored):
            if any(result_service.has_result(value, favorite) for favorite in self.quick_result_buttons):
                continue
            button = QToolButton()
            button.setText(value + " ×")
            button.setMaximumWidth(max(80, self.width()-194))
            button.setToolTip("Remove result: " + value)
            button.setProperty("quickResult", "true")
            button.setAccessibleName("Remove result: " + value)
            button.clicked.connect(lambda _checked=False, v=value: self._set_result_from_chip(v))
            self._quick_result_flow.addWidget(button)
        self.quick_result_extras.setVisible(self._quick_result_flow.count() > 0)
        self._set_quick_tab_order()
        self._sync_quick_name_label()
        self.context_panel.refresh()
        _, distance = parse_down_distance(d.get("down_distance", ""))
        for yards, button in self.distance_buttons.items():
            button.setChecked(distance == str(yards))
        self._sync_drive_suggestion()
        self.gain_origin.setVisible(bool(d.get("yards_inferred_from")))
        if self._field_dialog is not None:
            self._field_dialog.details = dict(d)
            self._field_dialog._refresh()
        self.field_draft_changed.emit(d)

    def _refresh_video_context(self) -> None:
        if not hasattr(self, "quick_rows"):
            return
        ids = self.context_panel.game_team_ids
        names = [team_name(team_id) for team_id in ids]
        self.video_context.setText(f"{names[0]} offense / {names[1]} defense" if len(names) == 2 else "Set video offense / defense…")
        self.video_context.setToolTip(self.video_context.text() + "\nClick to change video teams")
        old = self.detail_edits["ball_on"].text()
        blocked = self.quick_ball.blockSignals(True)
        if getattr(self, "_quick_ball_teams", None) != tuple(ids):
            self._quick_ball_teams = tuple(ids)
            self.quick_ball.clear()
            self.quick_ball.addItem("Not set", "")
            for side, index, fallback in (("OWN", 0, "Own"), ("OPP", 1, "Opponent")):
                name = team_abbreviation(ids[index]) if len(ids) > index else fallback
                for yard in range(0, 50):
                    self.quick_ball.addItem(f"{name} {yard}", f"{side} {yard}")
            self.quick_ball.addItem("Midfield 50", "50")
        if old and self.quick_ball.findData(old) < 0:
            self.quick_ball.addItem(old, old)
        choose(self.quick_ball, old)
        self.quick_ball.blockSignals(blocked)

    def _keep_naming_export_open(self, *_args) -> None:
        self.details_section.set_expanded(True)
    @staticmethod
    def _section(title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("V3DetailsSection")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 8, 0, 4)
        layout.setSpacing(6)
        heading = label(title)
        heading.setProperty("detailsHeading", True)
        layout.addWidget(heading)
        return frame
    def _rebuild_quick_result_buttons(self) -> None:
        grid = self._quick_favorites_grid
        while grid.count():
            widget = grid.takeAt(0).widget()
            widget.hide()
        for button in self.quick_result_buttons.values():
            button.hide()
            button.deleteLater()
        self.quick_result_buttons = {}
        self.yardage_panel.layout().insertWidget(0, self.gain_panel)
        self.gain_caption.show()
        self.yac_caption.show()
        self.gain_panel.show()
        choices = DEFAULT_SHORTCUTS if self._v3_shortcuts is None else self._v3_shortcuts
        position = 0
        for value in unique_results(choices)[:16]:
            button = self._choice_button(CAPTIONS.get(value, value), value)
            button.setFixedHeight(36)
            button.setToolTip(value + " — click to add or remove")
            tint_button(button, "neutral")
            if value == "Penalty":
                button.setCheckable(False)
                self.penalty_menu = QMenu(button)
                self.penalty_menu.aboutToShow.connect(self._populate_penalty_menu)
                button.setMenu(self.penalty_menu)
            else:
                button.clicked.connect(lambda _=False, v=value: self._set_result_from_chip(v))
            self.quick_result_buttons[value] = button
            if value in {"Gain", "YAC"}:
                panel = self.gain_panel if value == "Gain" else self.yac_panel
                caption = self.gain_caption if value == "Gain" else self.yac_caption
                caption.hide()
                button.setFixedHeight(28)
                button.setStyleSheet("QPushButton {background:transparent;color:#e6e8e6;border:0;padding:0;font:11px 'IBM Plex Sans';} QPushButton:focus {border:1px solid #ffc27b;} QPushButton:hover {color:#c9f2d7;}")
                panel.layout().insertWidget(0, button, 1 if value == "Gain" else 0)
                if value == "YAC":
                    button.setFixedWidth(44)
                    button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                    continue
                if position % 4 == 3:
                    position += 1
                self.yardage_panel.layout().removeWidget(panel)
                grid.addWidget(panel, position // 4, position % 4, 1, 2)
                position += 2
            else:
                grid.addWidget(button, position // 4, position % 4)
                position += 1
            button.show()
        for column in range(4):
            grid.setColumnStretch(column, 1)

    def _update_result_caption(self, value, button) -> None:
        prefix = "✓ " if button.isChecked() and (value != "Gain" or self.width() >= 360) else ""
        caption = prefix + CAPTIONS.get(value, value)
        width = max(20, button.width() - 6)
        if value not in {"Gain", "YAC"} and button.fontMetrics().horizontalAdvance(caption) > width:
            lines = caption.rsplit(" ", 1) if " " in caption else [caption]
            caption = "\n".join(button.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, width) for line in lines)
        button.setText(caption.replace("&", "&&"))

    def _set_quick_tab_order(self) -> None:
        chain = [*self.quick_play_buttons.values(), self.quick_play,
                 *self.quarter_buttons.values(), self.quick_ball,
                 *self.down_buttons.values(), self.context_panel.distance_edit,
                 *self.distance_buttons.values(), self.detail_edits["player_name"]]
        chain.extend(self._secondary_flow.itemAt(i).widget() for i in range(self._secondary_flow.count()))
        chain.extend((self.secondary_entry, self.quick_result_more, self.quick_result_edit))
        for value, button in self.quick_result_buttons.items():
            if value == "YAC":
                continue
            chain.append(button)
            if value == "Gain":
                chain.extend((self.gain_decrease_btn, self.detail_edits["yards"],
                              self.gain_units_button, self.gain_increase_btn))
        if "Gain" not in self.quick_result_buttons:
            chain.extend((self.gain_decrease_btn, self.detail_edits["yards"],
                          self.gain_units_button, self.gain_increase_btn))
        if "YAC" in self.quick_result_buttons:
            chain.append(self.quick_result_buttons["YAC"])
        chain.extend((self.detail_edits["yac"], self.yac_units_button))
        chain.extend(self._quick_result_flow.itemAt(i).widget() for i in range(self._quick_result_flow.count()))
        chain.extend((self.action_template, self.edit_actions_button, *self.action_buttons,
                      self.detail_edits["action"], *self.play_timing.focus_controls,
                      self.expand_notes_button, self.notes_edit, self.detail_edits["quarterback"],
                      self.quarterback_scope, self.drive_apply, self.new_drive, self.number_clips_button,
                      self.title_edit, self.autoname_btn, self.apply_btn, self.save_next_btn))
        for before, after in zip(chain, chain[1:]):
            QWidget.setTabOrder(before, after)

    def _reflow_situation(self) -> None:
        wide = self.width() >= 390
        if wide == getattr(self, "_situation_wide", None):
            return
        self._situation_wide = wide
        grid = self._situation_layout
        for title, control in self._situation_fields:
            grid.removeWidget(title)
            grid.removeWidget(control)
        grid.removeWidget(self.distance_strip)
        for column in range(4):
            grid.setColumnStretch(column, 0)
        for index, (title, control) in enumerate(self._situation_fields):
            row, column = divmod(index, 2)
            title.setMinimumWidth(0)
            title.setMaximumWidth(54 if wide else 16777215)
            grid.addWidget(title, row if wide else row * 2, column * 2 if wide else column)
            grid.addWidget(control, row if wide else row * 2 + 1, column * 2 + 1 if wide else column)
        grid.setColumnStretch(1 if wide else 0, 3)
        grid.setColumnStretch(3 if wide else 1, 2)
        grid.addWidget(self.distance_strip, 2 if wide else 4, 0, 1, 4 if wide else 2)
        self.distance_strip.layout().setDirection(QHBoxLayout.Direction.LeftToRight if wide else QHBoxLayout.Direction.TopToBottom)
        self._distance_heading.setMinimumWidth(74 if wide else 0)
        self._distance_heading.setMaximumWidth(74 if wide else 16777215)
        columns = 10 if wide else 5
        for button in self.distance_buttons.values():
            self._distance_picks.removeWidget(button)
        for column in range(10):
            self._distance_picks.setColumnStretch(column, 1 if column < columns else 0)
        for index, button in enumerate(self.distance_buttons.values()):
            self._distance_picks.addWidget(button, index // columns, index % columns)

    def _all_result_values(self) -> list[str]:
        return unique_results(
            self._result_vocabulary, list(STANDARD_RESULT_CHOICES),
            self.settings.fixed_details.get("result", []) if self.settings else [],
            self._v3_custom_results, list(self.quick_result_buttons),
            result_service.split_results(self.detail_edits["result"].text()))

    def _save_result_preferences(self, favorites, values, parent) -> bool:
        if self.settings is not None:
            candidate = deepcopy(self.settings)
            candidate.v3_result_favorites = favorites
            candidate.fixed_details["result"] = unique_results(
                candidate.fixed_details.get("result", []), values)
            try:
                candidate.save()
            except Exception as exc:
                QMessageBox.warning(parent, "Results were not saved", str(exc))
                return False
            self.settings.v3_result_favorites = candidate.v3_result_favorites
            self.settings.fixed_details["result"] = candidate.fixed_details["result"]
        self._v3_shortcuts = favorites
        self._v3_custom_results = unique_results(self._v3_custom_results, values)
        self._rebuild_quick_result_buttons()
        self._sync_quick_rows()
        return True

    def _manage_results(self) -> None:
        self._edit_result_shortcuts()

    def _edit_result_shortcuts(self) -> None:
        dialog = ResultShortcutsDialog(list(self.quick_result_buttons), self._all_result_values(), self)
        def save():
            if self._save_result_preferences(dialog.favorites(), dialog.results(), dialog):
                dialog.accept()
        dialog.buttons.accepted.connect(save)
        dialog.exec()
        dialog.deleteLater()

    def _show_results_picker(self) -> None:
        existing = getattr(self, "_results_picker", None)
        if existing is not None:
            existing.close()
        picker = ResultsPicker(self._all_result_values(), self.detail_edits["result"].text(), self)
        self._results_picker = picker
        picker.result_toggled.connect(self._set_result_from_chip)
        def add(value, pin):
            # Preserve the canonical spelling of a result already in the library.
            value = next((v for v in self._all_result_values() if v.casefold() == value.casefold()), value)
            favorites = list(self.quick_result_buttons)
            if pin and value not in favorites:
                if len(favorites) >= 16:
                    picker.custom_name.setText(value)
                    picker.custom_name.setToolTip("All 16 shortcuts are filled. Uncheck Pin, then use Edit to replace one.")
                    picker.pin_custom.setText("All 16 full — uncheck to add to library")
                    return
                favorites.append(value)
            if self._save_result_preferences(favorites, [value], picker):
                picker.close()
                self._show_results_picker()
                self._results_picker.search.setText(value)
        picker.custom_added.connect(add)
        def finished():
            if self._results_picker is picker:
                self._results_picker = None
            picker.deleteLater()
        picker.finished.connect(finished)
        screen = self.screen().availableGeometry()
        picker.resize(min(380, screen.width() - 16), min(570, screen.height() - 32))
        anchor = self.quick_result_more.mapToGlobal(self.quick_result_more.rect().bottomRight())
        picker.move(max(screen.left(), min(anchor.x() - picker.width(), screen.right() - picker.width())),
                    max(screen.top(), min(anchor.y(), screen.bottom() - picker.height())))
        picker.show()
        picker.search.setFocus()

    def _populate_result_menu(self) -> None:
        self._v3_result_menu.clear()
        current = self.detail_edits["result"].text()
        for value in dict.fromkeys([*STANDARD_RESULT_CHOICES, *result_service.split_results(current)]):
            action = self._v3_result_menu.addAction(value)
            action.setCheckable(True)
            action.setChecked(result_service.has_result(current, value))
            action.triggered.connect(lambda _checked=False, v=value: self._set_result_from_chip(v))

    def _sync_result_buttons(self, *_args) -> None:
        if not self._pagebook_ready:
            return
        current = self.detail_edits["result"].text()
        for value, button in self._v3_result_buttons.items():
            button.setChecked(result_service.has_result(current, value))
        self.detail_edits["result"].setToolTip(current or "Add one or more results")

    def _reflow_details(self) -> None:
        if not self._pagebook_ready:
            return
        if hasattr(self, "quick_rows"):
            self.heading_label.setText("CLIP DETAILS")
            self.heading_label.setMinimumWidth(0)
            self.heading_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self.quick_play_number.setText(f"Play {self._clip.clip_number:03d}" if self._clip else "")
            self.edit_title_btn.hide()
            self.collapse_btn.hide()
            for index in range(self._form_layout.count()):
                widget = self._form_layout.itemAt(index).widget()
                if widget is not None:
                    widget.setVisible(widget is self.quick_rows)
            self._refresh_first_read_advisory()
            self.action_bar.hide()
            self.summary_card.hide()
            self._form_layout.setContentsMargins(10, 10, 10, 12)
            self._quick_layout.setSpacing(0)
            self._form_layout.setSpacing(4)
            self._form_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
            self._reflow_situation()
            self._sync_action_buttons()
            self.form_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
            self._outer_layout.setStretchFactor(self.form_area, 1)
            self._quick_name_row.addWidget(self.autoname_btn)
            self.apply_btn.setText("Save")
            self.save_next_btn.setText("Save Next")
            for button in (self.apply_btn, self.save_next_btn):
                button.setFixedHeight(34)
                button.setMinimumWidth(0)
                button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                button.show()
            for key in ("quarter", "down_distance", "ball_on", "player_name", "other_players", "quarterback", "yards", "yac", "action"):
                self.detail_cells[key].hide()
            for key in ("run_pass", "play_type", "play_action", "result"):
                cell = self.detail_cells[key]
                if self._details_grid.indexOf(cell) < 0:
                    self._details_grid.addWidget(cell, self._details_grid.rowCount(), 0, 1, 2)
                cell.show()
            self._resize_notes()
            self.detail_edits["player_name"].setFixedHeight(32)
            for value, button in self.quick_result_buttons.items():
                self._update_result_caption(value, button)
            for index in range(self._quick_result_flow.count()):
                self._quick_result_flow.itemAt(index).widget().setMaximumWidth(max(80, self.width()-194))
            self.details_section.toggle.setText("Export settings")
            for index in range(self._secondary_flow.count()):
                self._secondary_flow.itemAt(index).widget().setMaximumWidth(max(80, self.width() - 148))
            self._set_quick_tab_order()
            return
        for key in ("quarter", "down_distance", "ball_on", "result"):
            self.detail_cells[key].hide()
        for i, key in enumerate(("player_name", "other_players")):
            self._players_grid.removeWidget(self.detail_cells[key])
            self._players_grid.addWidget(self.detail_cells[key], i, 0)
        self._form_layout.setContentsMargins(12, 0, 12, 12)
        self._form_layout.setSpacing(8)
        self.form_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The scroll viewport yields height before the pinned actions on short screens.
        self.form_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self._outer_layout.setStretchFactor(self.form_area, 1)
        self.shortcut_label.setText("ENTER  Save & Next  ·  CTRL+ENTER  Save")
        self.apply_btn.setText("Save")
        self.save_next_btn.setText("Save & Next")
        if not hasattr(self, "_v3_exports_row"):
            self._v3_exports_row = QVBoxLayout()
            self._analyst_naming_layout.addLayout(self._v3_exports_row)
        for row in (self._action_button_row, self._action_button_row_2):
            for button in (self.quick_export_btn, self.package_btn, self.apply_btn, self.save_next_btn):
                row.removeWidget(button)
        for button in (self.quick_export_btn, self.package_btn):
            if self._v3_exports_row.indexOf(button) < 0:
                self._v3_exports_row.addWidget(button)
            button.show()
        self._action_button_row.addWidget(self.apply_btn, 1)
        self._action_button_row.addWidget(self.save_next_btn, 2)

    def _apply_narrow_mode(self, narrow: bool) -> None:
        if not self._pagebook_ready:
            super()._apply_narrow_mode(narrow)
        else:
            self._reflow_details()

    def apply_field_layout(self) -> None:
        super().apply_field_layout()
        self._reflow_details()

    def _sync_advanced_detail_visibility(self) -> None:
        super()._sync_advanced_detail_visibility()
        self._reflow_details()

    def _advanced_detail_keys(self) -> list[str]:
        keys = super()._advanced_detail_keys()
        if hasattr(self, "quick_rows"):
            fixed = {"quarter", "down_distance", "ball_on", "player_name",
                     "run_pass", "play_type", "play_action", "other_players", "quarterback", "yards", "yac", "result", "action"}
            return [key for key in keys if key not in fixed]
        return keys

    def set_logging_defaults(self, values: dict) -> None:
        self.logging_defaults = deepcopy(values)
        for key in ("quarter", "quarterback"):
            if values.get(key):
                self.logging_defaults[key] = str(values[key]).strip()
            else:
                self.logging_defaults.pop(key, None)

    def _collect_details(self) -> dict[str, str]:
        details = super()._collect_details()
        for key, value in self._draft_extra_details.items():
            if value is None or value == "":
                details.pop(key, None)
            else:
                details[key] = value
        if details.get("yards_inferred_from") and details.get("yards") != details.get("yards_inferred_value"):
            details.pop("yards_inferred_from", None)
            details.pop("yards_inferred_value", None)
        return details

    def _stage_timing_details(self, values: dict) -> None:
        if self._clip is None or not self.isEnabled():
            return
        self._draft_extra_details.update(values)
        self.play_timing.details = self._collect_details()
        self.play_timing.refresh()
        self._mark_unsaved()

    def open_field_editor(self) -> None:
        if self._clip is None:
            return
        from tapesift.ui_v3.play_field import PlayFieldDialog
        if self._field_dialog is not None:
            self._field_dialog.close()
        dialog = PlayFieldDialog(self._collect_details(), self.context_panel.game_team_ids, self)
        self._field_dialog = dialog
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.details_changed.connect(self.stage_field_details)
        dialog.finished.connect(lambda: setattr(self, "_field_dialog", None) if self._field_dialog is dialog else None)
        dialog.show()
        dialog.raise_()

    def stage_field_details(self, details: dict) -> None:
        if self._clip is None:
            return
        current = self._collect_details()
        # A modeless field never owns quarter, players, results, or other panel edits.
        field_keys = ("ball_on", "yards", "yac", "field_flip", "field_hash", "field_event_spot",
                      "field_return_end", "field_recovery_team", "field_enforced_spot",
                      "receiver_name", "ball_carrier")
        merged = dict(current)
        for key in field_keys:
            if key in details:
                merged[key] = details[key]
            else:
                merged.pop(key, None)
        details = merged
        self._loading_clip = True
        try:
            for key in current.keys() | details.keys():
                if key in self.detail_edits:
                    self.detail_edits[key].setText(str(details.get(key, "")))
                else:
                    self._draft_extra_details[key] = details.get(key)
        finally:
            self._loading_clip = False
        if details.get("yards") != current.get("yards"):
            self._yards_changed()
        self.context_panel._ball_changed = self.context_panel._down_changed = False
        self.context_panel.refresh()
        self._refresh_auto_name()
        self._mark_unsaved()
        self._sync_quick_rows()

    def set_clip(self, clip) -> None:
        self._pending_drive_action = None
        if self._field_dialog is not None:
            self._field_dialog.close()
            self._field_dialog = None
        provider = getattr(self, "logging_defaults_provider", None)
        if provider is not None:
            self.set_logging_defaults(provider())
        self._draft_extra_details = {}
        self._loaded_clip_id = clip.id if clip else None
        eligible = getattr(self, "is_logging_default_eligible", None)
        self._default_eligible = bool(clip and (eligible(clip) if eligible else not clip.details))
        self._carried_quarterback = detail_service.quarterback_at(self.logging_defaults, clip.start_ms if clip else 0)
        if hasattr(self, "quarterback_scope"):
            blocked = self.quarterback_scope.blockSignals(True)
            self.quarterback_scope.setCurrentIndex(1 if self._default_eligible else 0)
            self.quarterback_scope.blockSignals(blocked)
        super().set_clip(clip)
        if self.play_timing is not None:
            self.play_timing.set_clip(clip, self._collect_details())
        if clip and self._default_eligible:
            for key, value in (("quarter", self.logging_defaults.get("quarter", "")),
                               ("quarterback", self._carried_quarterback)):
                if not clip.details.get(key):
                    self.detail_edits[key].setText(value)
        if self.source_photo_panel is not None:
            self.source_photo_panel.set_clip(clip)
            self.source_photo_context_changed.emit()
        if self.context_panel is not None:
            self.context_panel.load_clip()
            self._sync_result_buttons()
            self._reflow_details()
            self._sync_quick_rows()

    def set_game_teams(self, values: list[str]) -> None:
        if self.context_panel is not None:
            self.context_panel.set_game_teams(values)

    def _set_save_state(self, text: str, state: str = "saved") -> None:
        super()._set_save_state(text, state)
        self.save_state_changed.emit(text.title())

    def _apply(self) -> bool:
        if self._clip is None:
            return False
        if self.context_panel is not None:
            error = self.context_panel.validation_error()
            if error:
                self.error_label.setText(error)
                self.error_label.show()
                self.form_area.ensureWidgetVisible(self.error_label)
                return False
        self.pending_logging_defaults = deepcopy(self.logging_defaults) if self._default_eligible else None
        if self._default_eligible:
            quarter = self.detail_edits["quarter"].text().strip()
            self.pending_logging_defaults.pop("quarter", None)
            if quarter:
                self.pending_logging_defaults["quarter"] = quarter
        quarterback = self.detail_edits["quarterback"].text().strip()
        if self.quarterback_scope.currentData() == "forward" and quarterback != self._carried_quarterback:
            self.pending_logging_defaults = detail_service.with_quarterback_change(
                self.pending_logging_defaults if self.pending_logging_defaults is not None else self.logging_defaults,
                self._clip.start_ms, quarterback)
        self._draft_extra_details["logging_saved"] = "1"
        self._save_commit_error = ""
        clip = self._clip
        before = deepcopy(clip.__dict__)
        try:
            saved = super()._apply()
            if self._save_commit_error:
                clip.__dict__.update(before)
                self.error_label.setText("Could not save: " + self._save_commit_error)
                self.error_label.show()
                self._mark_unsaved()
                return False
            if saved:
                self._draft_extra_details = {}
                self.play_timing.details = self._collect_details()
                self.play_timing.refresh()
                self._pending_drive_action = None
                if self.pending_logging_defaults is not None:
                    self.set_logging_defaults(self.pending_logging_defaults)
                    self._carried_quarterback = detail_service.quarterback_at(self.logging_defaults, clip.start_ms)
                    self.logging_defaults_changed.emit(dict(self.logging_defaults))
                self._sync_quick_rows()
            return saved
        finally:
            self.pending_logging_defaults = None
