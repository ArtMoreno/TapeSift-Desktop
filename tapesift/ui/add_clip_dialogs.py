"""Dialogs for the clip-creation flows: single timestamp, range, and in/out naming.

Every flow asks for the clip name up front; the name drives the export filename.
"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QVBoxLayout,
)

from tapesift.core.exceptions import TapeSiftError
from tapesift.services import filename_service
from tapesift.services.clip_factory import ROLL_PRESETS, ClipDefaults
from tapesift.services.timestamp_parser import format_ms, parse_range, parse_timestamp
from tapesift.ui_core.tag_edit import TagLineEdit


class _NameFieldsMixin:
    """Shared name/label/tags/notes fields + live filename preview."""

    def _build_name_fields(self, form: QFormLayout, separator: str,
                           known_tags: list[str] | None = None) -> None:
        self._separator = separator
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g. Opening sequence")
        self.name_edit.setToolTip("The clip name - also becomes the export filename")
        self.label_edit = QLineEdit()
        self.tags_edit = TagLineEdit(known_tags or [])
        self.tags_edit.setPlaceholderText("comma, separated, tags")
        self.notes_edit = QLineEdit()
        form.addRow("Clip name:", self.name_edit)
        form.addRow("Label (optional):", self.label_edit)
        form.addRow("Tags (optional):", self.tags_edit)
        form.addRow("Notes (optional):", self.notes_edit)
        self.filename_preview = QLabel("")
        self.filename_preview.setProperty("role", "subtle")
        form.addRow("Filename base:", self.filename_preview)
        self.name_edit.textChanged.connect(self._update_name_preview)
        self._update_name_preview()

    def _update_name_preview(self) -> None:
        base = filename_service.sanitize_filename_base(
            self.name_edit.text(), self._separator)
        self.filename_preview.setText(base or "(auto-generated if left empty)")

    def name_values(self) -> tuple[str, str, list[str], str]:
        tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        return (self.name_edit.text().strip(), self.label_edit.text().strip(),
                tags, self.notes_edit.text().strip())


class TimestampClipDialog(QDialog, _NameFieldsMixin):
    """Add a clip from a single timestamp + pre/post-roll."""

    def __init__(self, defaults: ClipDefaults, current_position_ms: int | None = None,
                 known_tags: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Clip - Single Timestamp")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.timestamp_edit = QLineEdit()
        self.timestamp_edit.setPlaceholderText("e.g. 18:45 or 01:03:14.250 or 194.5")
        # A timestamp on the clipboard beats the playhead: you copied it
        # because you want to use it.
        clipboard_text = (QGuiApplication.clipboard().text() or "").strip()
        clipboard_ms: int | None = None
        if clipboard_text and len(clipboard_text) <= 16:
            try:
                clipboard_ms = parse_timestamp(clipboard_text)
            except TapeSiftError:
                clipboard_ms = None
        if clipboard_ms is not None:
            self.timestamp_edit.setText(clipboard_text)
            self.timestamp_edit.selectAll()
        if clipboard_ms is None and current_position_ms is not None:
            self.timestamp_edit.setText(format_ms(current_position_ms, show_millis=True))
        form.addRow("Timestamp:", self.timestamp_edit)

        roll_row = QHBoxLayout()
        self.roll_combo = QComboBox()
        for name, pre, post in ROLL_PRESETS:
            self.roll_combo.addItem(name, (pre, post))
        self.roll_combo.addItem("Custom", None)
        self.pre_spin = QDoubleSpinBox()
        self.pre_spin.setRange(0, 600)
        self.pre_spin.setSuffix(" s before")
        self.pre_spin.setValue(defaults.pre_roll_ms / 1000)
        self.post_spin = QDoubleSpinBox()
        self.post_spin.setRange(0, 600)
        self.post_spin.setSuffix(" s after")
        self.post_spin.setValue(defaults.post_roll_ms / 1000)
        roll_row.addWidget(self.roll_combo)
        roll_row.addWidget(self.pre_spin)
        roll_row.addWidget(self.post_spin)
        form.addRow("Pre/post-roll:", roll_row)
        # Select preset matching defaults, else Custom.
        match = next((i for i, (_, pre, post) in enumerate(ROLL_PRESETS)
                      if pre == defaults.pre_roll_ms and post == defaults.post_roll_ms),
                     self.roll_combo.count() - 1)
        self.roll_combo.setCurrentIndex(match)
        self.roll_combo.currentIndexChanged.connect(self._roll_preset_changed)

        self._build_name_fields(form, defaults.separator_style, known_tags)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.timestamp_ms = 0

    def _roll_preset_changed(self) -> None:
        data = self.roll_combo.currentData()
        if data:
            self.pre_spin.setValue(data[0] / 1000)
            self.post_spin.setValue(data[1] / 1000)

    def _validate_and_accept(self) -> None:
        try:
            self.timestamp_ms = parse_timestamp(self.timestamp_edit.text())
        except TapeSiftError as exc:
            self.error_label.setText(exc.user_text())
            self.error_label.show()
            return
        self.accept()

    def roll_values(self) -> tuple[int, int]:
        return round(self.pre_spin.value() * 1000), round(self.post_spin.value() * 1000)


class RangeClipDialog(QDialog, _NameFieldsMixin):
    """Add a clip from an exact start/end range."""

    def __init__(self, defaults: ClipDefaults, start_ms: int | None = None,
                 end_ms: int | None = None, title: str = "Add Clip - Start/End Range",
                 known_tags: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.start_edit = QLineEdit()
        self.start_edit.setPlaceholderText("e.g. 18:40")
        self.end_edit = QLineEdit()
        self.end_edit.setPlaceholderText("e.g. 18:55")
        if start_ms is not None:
            self.start_edit.setText(format_ms(start_ms, show_millis=True))
        if end_ms is not None:
            self.end_edit.setText(format_ms(end_ms, show_millis=True))
        form.addRow("Start time:", self.start_edit)
        form.addRow("End time:", self.end_edit)
        self._build_name_fields(form, defaults.separator_style, known_tags)
        layout.addLayout(form)

        self.error_label = QLabel("")
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.start_ms = 0
        self.end_ms = 0

    def _validate_and_accept(self) -> None:
        try:
            self.start_ms, self.end_ms = parse_range(
                self.start_edit.text(), self.end_edit.text())
        except TapeSiftError as exc:
            self.error_label.setText(exc.user_text())
            self.error_label.show()
            return
        self.accept()
