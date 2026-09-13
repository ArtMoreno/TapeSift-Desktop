"""Everyday Coverage Guardian review queue."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from tapesift.services.timestamp_parser import format_ms


class CoverageReviewDialog(QDialog):
    """Modeless queue for uncertain candidates and possible missed footage."""

    inspect_requested = Signal(int, int, str)
    create_clip_requested = Signal(int)
    dismiss_requested = Signal(int)
    restore_requested = Signal(int)
    accept_clip_requested = Signal(str)
    reject_clip_requested = Signal(str)

    def __init__(self, queue: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Review Detection Coverage")
        self.setModal(False)
        self.resize(940, 560)
        self._items: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("COVERAGE REVIEW  /  CONSERVATIVE AUDIT")
        title.setProperty("role", "eyebrow")
        layout.addWidget(title)
        description = QLabel(
            "Review uncertain clips and every unclaimed source range. "
            "Priority uses structural evidence only; it is never a play "
            "decision.")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("CoverageReviewSummary")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([
            "STATUS", "PRIORITY", "TYPE", "SOURCE RANGE", "LENGTH",
            "WHY CHECK",
        ])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        self.table.setColumnWidth(0, 88)
        self.table.setColumnWidth(1, 94)
        self.table.setColumnWidth(2, 124)
        self.table.setColumnWidth(3, 150)
        self.table.setColumnWidth(4, 68)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        self.table.itemDoubleClicked.connect(
            lambda _item: self._inspect_selected())
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.inspect_btn = QPushButton("Inspect in Timeline")
        self.inspect_btn.clicked.connect(self._inspect_selected)
        actions.addWidget(self.inspect_btn)
        self.create_btn = QPushButton("Create Review Clip")
        self.create_btn.clicked.connect(self._create_selected)
        actions.addWidget(self.create_btn)
        self.dismiss_btn = QPushButton("Not a Play")
        self.dismiss_btn.clicked.connect(self._dismiss_selected)
        actions.addWidget(self.dismiss_btn)
        self.accept_btn = QPushButton("Accept Current Clip")
        self.accept_btn.clicked.connect(self._accept_selected)
        actions.addWidget(self.accept_btn)
        self.restore_btn = QPushButton("Restore to Pending")
        self.restore_btn.clicked.connect(self._restore_selected)
        actions.addWidget(self.restore_btn)
        actions.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        actions.addWidget(close_btn)
        layout.addLayout(actions)

        hint = QLabel(
            'Low signal never means safe to skip. Create Review Clip is '
            'undoable; "Not a Play" can always be restored.')
        hint.setProperty("role", "muted")
        layout.addWidget(hint)
        self.refresh(queue)

    @staticmethod
    def _key(item: dict) -> tuple:
        return (
            item.get("item_kind"),
            item.get("segment_index"),
            item.get("candidate_id"),
        )

    def refresh(self, queue: dict) -> None:
        selected = self._selected()
        selected_key = self._key(selected) if selected else None
        selected_was_pending = bool(
            selected and selected.get("status") == "pending")
        summary = queue.get("summary", {})
        pending = int(summary.get("pending", 0))
        possible = int(summary.get("possible_missed", 0))
        missed_seconds = int(summary.get("possible_missed_ms", 0)) / 1000
        check_first = int(summary.get("check_first", 0))
        self.summary_label.setText(
            f"{summary.get('detected_plays', 0)} detected plays   |   "
            f"{summary.get('needs_review', 0)} uncertain clips   |   "
            f"{possible} possible missed ({missed_seconds:.1f}s)   |   "
            f"{check_first} check first   |   "
            f"{pending} pending")

        self._items = list(queue.get("items", []))
        self.table.setRowCount(len(self._items))
        select_row = -1
        first_pending = -1
        for row, item in enumerate(self._items):
            status = str(item.get("status", "pending"))
            status_text = {
                "pending": "PENDING",
                "reviewed": "REVIEWED",
                "clip_created": "IN A PLAY",
                "dismissed": "NOT A PLAY",
            }.get(status, status.upper())
            kind_text = "Possible missed" \
                if item.get("item_kind") == "possible_missed" \
                else "Uncertain clip"
            attention_label = str(
                item.get("attention_label", "REQUIRED"))
            start_ms = int(item.get("start_ms", 0))
            end_ms = int(item.get("end_ms", start_ms))
            values = [
                status_text,
                attention_label,
                kind_text,
                f"{format_ms(start_ms)} - {format_ms(end_ms)}",
                f"{max(0, end_ms - start_ms) / 1000:.1f}s",
                str(item.get("attention_reason") or item.get("reason", "")),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column in {0, 1, 4}:
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if column == 1:
                    colour = {
                        "check_first": "#f0b44b",
                        "required": "#d8d8dc",
                        "review": "#c99a50",
                        "low_signal": "#7d8b82",
                    }.get(str(item.get("attention_level", "")), "#d8d8dc")
                    cell.setForeground(QColor(colour))
                    reasons = item.get("attention_reasons", [])
                    reason_text = "\n".join(
                        str(reason) for reason in reasons)
                    version = str(item.get("audit_version", ""))
                    if version:
                        reason_text += (
                            f"\n\nStructural audit v{version}; ranking only, "
                            "not a probability.")
                    cell.setToolTip(reason_text.strip())
                self.table.setItem(row, column, cell)
            if first_pending < 0 and status == "pending":
                first_pending = row
            if (
                selected_key is not None
                and self._key(item) == selected_key
                and not (
                    selected_was_pending
                    and status != "pending"
                )
            ):
                select_row = row

        if select_row < 0:
            select_row = first_pending if first_pending >= 0 \
                else (0 if self._items else -1)
        if select_row >= 0:
            self.table.selectRow(select_row)
            self.table.scrollToItem(self.table.item(select_row, 0))
        self._sync_actions()

    def _selected(self) -> dict | None:
        row = self.table.currentRow()
        return self._items[row] if 0 <= row < len(self._items) else None

    def _sync_actions(self) -> None:
        item = self._selected()
        pending = bool(item and item.get("status") == "pending")
        possible = bool(item and item.get("item_kind") == "possible_missed")
        candidate = bool(item and item.get("item_kind") == "candidate")
        self.inspect_btn.setEnabled(item is not None)
        self.create_btn.setEnabled(pending and possible)
        self.dismiss_btn.setEnabled(
            pending and (
                possible or (candidate and bool(item.get("clip_id")))))
        self.accept_btn.setEnabled(
            pending and candidate and bool(item.get("clip_id")))
        self.restore_btn.setEnabled(
            bool(possible and item.get("status") in {
                "clip_created", "dismissed"}))

    def _inspect_selected(self) -> None:
        item = self._selected()
        if item is not None:
            self.inspect_requested.emit(
                int(item["start_ms"]),
                int(item["end_ms"]),
                str(item.get("clip_id", "")),
            )

    def _create_selected(self) -> None:
        item = self._selected()
        if item is not None and item.get("item_kind") == "possible_missed":
            self.create_clip_requested.emit(int(item["segment_index"]))

    def _dismiss_selected(self) -> None:
        item = self._selected()
        if item is None:
            return
        if item.get("item_kind") == "possible_missed":
            self.dismiss_requested.emit(int(item["segment_index"]))
        elif item.get("item_kind") == "candidate" and item.get("clip_id"):
            self.reject_clip_requested.emit(str(item["clip_id"]))

    def _restore_selected(self) -> None:
        item = self._selected()
        if item is not None and item.get("item_kind") == "possible_missed":
            self.restore_requested.emit(int(item["segment_index"]))

    def _accept_selected(self) -> None:
        item = self._selected()
        if item is not None and item.get("clip_id"):
            self.accept_clip_requested.emit(str(item["clip_id"]))
