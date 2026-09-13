"""Guided clip-details popover for the I-key workflow.

Pressing I (in guided mode) marks the in-point and opens this compact,
keyboard-friendly popover so play details can be typed while the video keeps
rolling. Pressing O (or the Mark Out button) sets the out-point; Save creates
the clip, auto-named from the details when no name is given.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QVBoxLayout,
)

from tapesift.core.config import AppSettings
from tapesift.services.detail_service import DETAIL_FIELDS
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_core.tag_edit import (
    AddTagCombo, DetailEdit, TagLineEdit, configure_detail_edit,
)


class GuidedDetailsPopover(QDialog):
    mark_out_requested = Signal()
    save_requested = Signal()
    cancelled = Signal()

    def __init__(self, parent=None, settings: AppSettings | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Clip Details")
        # Tool window: compact, floats above the editor, no taskbar entry.
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setModal(False)

        layout = QVBoxLayout(self)
        self.marks_label = QLabel("In: - Out: -")
        self.marks_label.setProperty("role", "subtle")
        layout.addWidget(self.marks_label)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(
            "Clip name (leave empty to auto-name from details)")
        layout.addWidget(self.name_edit)

        grid = QGridLayout()
        self.detail_edits: dict[str, DetailEdit] = {}
        for index, (key, label_text) in enumerate(DETAIL_FIELDS):
            row, col = divmod(index, 2)
            cell = QVBoxLayout()
            small = QLabel(label_text)
            small.setProperty("role", "subtle")
            edit = DetailEdit()
            self.detail_edits[key] = edit
            cell.addWidget(small)
            cell.addWidget(edit)
            grid.addLayout(cell, row, col)
        layout.addLayout(grid)

        self.tags_edit = TagLineEdit()
        self.tags_edit.setPlaceholderText("tags, comma, separated")
        self.add_tag_combo = AddTagCombo(self.tags_edit)
        self.add_tag_combo.hide()
        tags_row = QHBoxLayout()
        tags_row.addWidget(self.tags_edit, 1)
        tags_row.addWidget(self.add_tag_combo)
        layout.addLayout(tags_row)
        self.apply_dropdown_mode()

        buttons = QHBoxLayout()
        self.mark_out_btn = QPushButton("Mark &Out @ playhead")
        self.mark_out_btn.setToolTip(
            "Set the clip's out-point at the current frame (Ctrl+O). The O key "
            "also works while the video window has focus.")
        self.mark_out_btn.clicked.connect(self.mark_out_requested.emit)
        self.save_btn = QPushButton("&Save Clip")
        self.save_btn.setDefault(True)
        self.save_btn.setToolTip(
            "Create the clip (Enter). Uses the out-point, or the current "
            "playhead if none was set.")
        self.save_btn.clicked.connect(self.save_requested.emit)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.mark_out_btn)
        buttons.addStretch()
        buttons.addWidget(self.save_btn)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

        # Enter saves outright: the form is the last step of logging a play.
        QShortcut(QKeySequence("Return"), self, activated=self.save_requested.emit)
        QShortcut(QKeySequence("Enter"), self, activated=self.save_requested.emit)
        QShortcut(QKeySequence("Ctrl+Return"), self,
                  activated=self.save_requested.emit)
        QShortcut(QKeySequence("Ctrl+O"), self,
                  activated=self.mark_out_requested.emit)

    # ---------- state ----------

    def open_for(self, in_ms: int, out_ms: int | None = None) -> None:
        self.set_marks(in_ms, out_ms)
        self.name_edit.clear()
        for edit in self.detail_edits.values():
            edit.clear()
        self.tags_edit.clear()
        # Out already marked (the normal after-the-play flow) → the button is
        # for adjusting, not the main action.
        self.mark_out_btn.setText("Re-mark &Out @ playhead" if out_ms is not None
                                  else "Mark &Out @ playhead")
        self.show()
        self.raise_()
        self.activateWindow()
        # Straight into typing: quarter/down first, per film-review habits.
        first = next(iter(self.detail_edits.values()))
        first.setFocus()

    def set_marks(self, in_ms: int | None, out_ms: int | None) -> None:
        in_text = format_ms(in_ms, show_millis=True) if in_ms is not None else "-"
        out_text = format_ms(out_ms, show_millis=True) if out_ms is not None else "-"
        self.marks_label.setText(f"In: {in_text}   Out: {out_text}")

    def apply_dropdown_mode(self) -> None:
        s = self.settings
        fixed = bool(s and s.use_fixed_dropdowns)
        for key, edit in self.detail_edits.items():
            configure_detail_edit(edit, key, s)
        self.add_tag_combo.setVisible(fixed)
        if fixed and s:
            self.add_tag_combo.set_fixed_tags(s.fixed_tags)
            self.tags_edit.set_known_tags(s.fixed_tags)

    def set_vocabulary(self, detail_values: dict[str, list[str]],
                       all_tags: list[str]) -> None:
        if not (self.settings and self.settings.use_fixed_dropdowns):
            self.tags_edit.set_known_tags(all_tags)
        for key, edit in self.detail_edits.items():
            edit.set_known_values(detail_values.get(key, []))

    def collect(self) -> tuple[str, dict[str, str], list[str]]:
        details = {key: edit.text().strip()
                   for key, edit in self.detail_edits.items()
                   if edit.text().strip()}
        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        return self.name_edit.text().strip(), details, tags

    def reject(self) -> None:
        self.cancelled.emit()
        super().reject()
