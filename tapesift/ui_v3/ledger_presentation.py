"""Recorded-time presentation over the existing V3 clip table."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QStyle, QStyleOptionViewItem, QToolTip

from tapesift.core.exceptions import TimestampParseError
from tapesift.services.timestamp_parser import parse_timestamp
from tapesift.ui_core.clip_list import (
    COL_DUR, COL_END, COL_NUM, COL_START, COL_STATUS, COL_TITLE,
    PlayRailDelegate, REVIEW_TOOLTIP_ROLE,
)


class LedgerDelegateV3(PlayRailDelegate):
    """Use the same rows, selection and trim-preview values with two lines."""

    def __init__(self, clip_list) -> None:
        super().__init__(clip_list.table)
        self.clip_list = clip_list
        self._syncing_accessibility = False
        clip_list.table.model().dataChanged.connect(self.sync_accessibility)

    def clip_for(self, index):
        clip_id = index.siblingAtColumn(COL_NUM).data(Qt.ItemDataRole.UserRole)
        return next((clip for clip in self.clip_list._clips if clip.id == clip_id), None)

    def row_text(self, index) -> dict[str, str]:
        clip = self.clip_for(index)
        if clip is None:
            return {}
        details = clip.details or {}
        clock = " ".join(str(details.get(key, "")).strip()
                         for key in ("quarter", "game_clock")
                         if str(details.get(key, "")).strip())
        start = str(index.siblingAtColumn(COL_START).data() or "—")
        end = str(index.siblingAtColumn(COL_END).data() or "—")
        raw_duration = str(index.siblingAtColumn(COL_DUR).data() or "")
        try:
            duration = f"{parse_timestamp(raw_duration) / 1000:.3f}".rstrip("0").rstrip(".") + "s"
        except TimestampParseError:
            duration = "—"
        return {"headline": clock or clip.clip_title or "Unnamed play",
                "clock": clock, "range": f"{start} – {end}", "duration": duration,
                "status": str(index.siblingAtColumn(COL_STATUS).data() or "—")}

    def tooltip(self, index) -> str:
        clip = self.clip_for(index)
        if clip is None:
            return ""
        text = self.row_text(index)
        details = clip.details or {}
        lines = [clip.clip_title or "Unnamed play",
                 f"Game: {text['clock'] or 'Not recorded'}",
                 f"Source: {text['range']}", f"Duration: {text['duration']}",
                 f"Review: {text['status']}"]
        explanation = index.siblingAtColumn(COL_STATUS).data(REVIEW_TOOLTIP_ROLE)
        if explanation:
            lines.append(str(explanation))
        for key, label in (("player_name", "Player"), ("other_players", "Other players"),
                           ("run_pass", "Family"), ("play_type", "Concept"),
                           ("play_action", "Play action"), ("result", "Result"),
                           ("down_distance", "Down and distance"), ("ball_on", "Ball on")):
            value = str(details.get(key, "")).strip()
            if value:
                lines.append(f"{label}: {value}")
        if "trim preview" in str(index.siblingAtColumn(COL_START).data(Qt.ItemDataRole.ToolTipRole) or ""):
            lines.append("Trim preview; release to save")
        return "\n".join(lines)

    def sync_accessibility(self, first=None, last=None, roles=None) -> None:
        # Keep native keyboard/assistive-tool access in step with custom pixels,
        # including unsaved trim previews from the existing hidden time items.
        if self._syncing_accessibility:
            return
        if roles and not any(role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole)
                             for role in roles):
            return
        table = self.clip_list.table
        rows = range(first.row(), last.row()+1) if first is not None else range(table.rowCount())
        self._syncing_accessibility = True
        try:
            for row in rows:
                index = table.model().index(row, COL_TITLE)
                text = self.tooltip(index)
                if not text:
                    continue
                for column in (COL_NUM, COL_TITLE, COL_STATUS):
                    item = table.item(row, column)
                    if item is not None:
                        item.setData(Qt.ItemDataRole.AccessibleTextRole, text)
                        item.setToolTip(text)
        finally:
            self._syncing_accessibility = False

    def helpEvent(self, event, view, option, index) -> bool:  # noqa: N802
        text = self.tooltip(index)
        if text:
            QToolTip.showText(event.globalPos(), text, view, option.rect)
            return True
        return super().helpEvent(event, view, option, index)

    def paint(self, painter, option, index) -> None:
        text = self.row_text(index)
        if not text or index.column() not in (COL_TITLE, COL_STATUS):
            return super().paint(painter, option, index)
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.text = ""
        table = self.clip_list.table
        painter.save()
        table.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, table)
        rect = option.rect.adjusted(4, 2, -4, -2)
        top = rect.adjusted(0, 0, 0, -(rect.height() // 2))
        bottom = rect.adjusted(0, rect.height() // 2, 0, 0)
        if index.column() == COL_TITLE:
            lines = ((text["headline"], top, 12, "#d9e0da"),
                     (text["range"], bottom, 11, "#a9b4ad"))
        else:
            brush = index.data(Qt.ItemDataRole.ForegroundRole)
            status_colour = brush.color() if brush is not None else QColor("#a9b4ad")
            lines = ((text["duration"], top, 11, "#c3ccc6"),
                     (text["status"], bottom, 10, status_colour))
        for value, box, pixels, colour in lines:
            font = QFont(option.font)
            font.setPixelSize(pixels)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor(colour))
            value = painter.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, box.width())
            painter.drawText(box, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)
        painter.restore()
