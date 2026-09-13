"""Review and remove clips that describe the same piece of film."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from tapesift.models.clip import Clip
from tapesift.services.duplicate_service import (
    DuplicateGroup, find_duplicate_groups, metadata_score,
)
from tapesift.services.timestamp_parser import format_ms

COL_RANGE, COL_COPIES, COL_KEEP, COL_REMOVE = range(4)


class DuplicateClipsDialog(QDialog):
    """Show duplicate groups and, on confirmation, report what to remove.

    The dialog never touches the project. It hands back a list of clip ids and
    the caller performs one undoable removal.
    """

    def __init__(self, clips: list[Clip], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Duplicate Clips")
        self.setMinimumSize(760, 460)
        self.groups: list[DuplicateGroup] = find_duplicate_groups(clips)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        removable = sum(len(group.remove) for group in self.groups)
        if self.groups:
            headline = (
                f"{len(clips)} clips describe "
                f"{len(clips) - removable} distinct ranges.")
            detail = (
                f"{removable} clip(s) in {len(self.groups)} group(s) repeat a "
                "range that another clip already covers. The copy carrying "
                "the most logged detail is kept.")
        else:
            headline = "No duplicate clips found."
            detail = (
                "Every clip in this project covers its own range.")
        title = QLabel(headline)
        title.setProperty("role", "heading")
        layout.addWidget(title)
        body = QLabel(detail)
        body.setProperty("role", "subtle")
        body.setWordWrap(True)
        layout.addWidget(body)

        self.table = QTableWidget(len(self.groups), 4)
        self.table.setHorizontalHeaderLabels(
            ["Range", "Copies", "Keeping", "Removing"])
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(
            COL_RANGE, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(
            COL_COPIES, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_KEEP, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_REMOVE, QHeaderView.ResizeMode.Stretch)
        for row, group in enumerate(self.groups):
            self._fill_row(row, group)
        layout.addWidget(self.table, 1)

        note = QLabel(
            "Removal is a single undoable edit - Ctrl+Z restores every clip.")
        note.setProperty("role", "subtle")
        layout.addWidget(note)

        buttons = QDialogButtonBox()
        self.remove_button = buttons.addButton(
            f"Remove {removable} duplicate clip(s)",
            QDialogButtonBox.ButtonRole.AcceptRole)
        self.remove_button.setProperty("primary", "true")
        self.remove_button.setEnabled(bool(removable))
        cancel = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        cancel.setText("Close" if not removable else "Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(buttons)
        layout.addLayout(row)

    def _fill_row(self, row: int, group: DuplicateGroup) -> None:
        span = (f"{format_ms(group.start_ms, show_millis=True)} - "
                f"{format_ms(group.end_ms, show_millis=True)}")
        self._set(row, COL_RANGE, span)
        self._set(row, COL_COPIES, str(len(group.clips)))
        self._set(row, COL_KEEP, self._describe(group.keep))
        self._set(
            row, COL_REMOVE,
            ", ".join(self._describe(clip) for clip in group.remove))

    @staticmethod
    def _describe(clip: Clip) -> str:
        name = clip.clip_title.strip() or f"Play {clip.clip_number:02d}"
        score = metadata_score(clip)
        return f"{name} ({score})" if score else f"{name} (unlogged)"

    def _set(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        if column in (COL_RANGE, COL_COPIES):
            item.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(row, column, item)

    def clip_ids_to_remove(self) -> list[str]:
        return [clip.id for group in self.groups for clip in group.remove]
