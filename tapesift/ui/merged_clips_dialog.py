"""Review clips that look like they hold more than one play.

Shown straight after detection. Each row offers the exact split point the
detector already found, so fixing a merged play is a tick rather than a
hunt through the film.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHeaderView, QLabel,
    QPushButton, QHBoxLayout, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from tapesift.services.play_detect_service import MergedSuspect
from tapesift.services.timestamp_parser import format_ms


class MergedClipsDialog(QDialog):
    preview_requested = Signal(int)   # ms

    def __init__(self, suspects: list[MergedSuspect], parent=None) -> None:
        super().__init__(parent)
        self.suspects = suspects
        self.setWindowTitle("Possible merged plays")
        self.setMinimumSize(720, 420)

        layout = QVBoxLayout(self)
        blurb = QLabel(
            f"{len(suspects)} of the detected clips look like they contain "
            "more than one play - usually because the boundary between them "
            "wasn't marked in the film. Tick the ones to split; the split "
            "points come from the segment boundaries already found.")
        blurb.setWordWrap(True)
        blurb.setProperty("role", "subtle")
        layout.addWidget(blurb)

        self.table = QTableWidget(len(suspects), 5)
        self.table.setHorizontalHeaderLabels(
            ["Split", "Clip range", "Length", "Split at", "Why"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        for row, s in enumerate(suspects):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable |
                           Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(
                f"{format_ms(s.start_ms)} – {format_ms(s.end_ms)}"))
            self.table.setItem(row, 2, QTableWidgetItem(
                f"{s.duration_ms / 1000:.1f}s"))
            self.table.setItem(row, 3, QTableWidgetItem(
                ", ".join(format_ms(p) for p in s.split_points_ms)))
            self.table.setItem(row, 4, QTableWidgetItem(s.reason))
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch)
        self.table.resizeColumnsToContents()
        self.table.itemDoubleClicked.connect(self._preview_row)
        layout.addWidget(self.table, 1)

        hint = QLabel("Double-click a row to jump the playhead to its split "
                      "point and see it for yourself.")
        hint.setProperty("role", "subtle")
        layout.addWidget(hint)

        buttons_row = QHBoxLayout()
        all_btn = QPushButton("Select all")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("Select none")
        none_btn.clicked.connect(lambda: self._set_all(False))
        buttons_row.addWidget(all_btn)
        buttons_row.addWidget(none_btn)
        buttons_row.addStretch()
        layout.addLayout(buttons_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Split ticked")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Leave as is")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)

    def _preview_row(self, item: QTableWidgetItem) -> None:
        s = self.suspects[item.row()]
        if s.split_points_ms:
            self.preview_requested.emit(s.split_points_ms[0])

    def chosen(self) -> list[MergedSuspect]:
        return [s for row, s in enumerate(self.suspects)
                if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]
