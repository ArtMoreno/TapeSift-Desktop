"""Apply metadata to several clips at once.

Only the fields you fill in are applied - everything left blank is left
alone on every clip, so this can never wipe work by omission.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit,
    QVBoxLayout,
)

from tapesift.core.config import AppSettings
from tapesift.services.detail_service import DETAIL_FIELDS
from tapesift.ui_core.tag_edit import (
    DetailEdit, TagLineEdit, configure_detail_edit,
)


class BulkEditDialog(QDialog):
    def __init__(self, count: int, settings: AppSettings,
                 known_tags: list[str] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(f"Edit {count} clips")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        blurb = QLabel(
            f"Anything you set here is applied to all {count} selected clips. "
            "Blank fields are left untouched.")
        blurb.setWordWrap(True)
        blurb.setProperty("role", "subtle")
        layout.addWidget(blurb)

        form = QFormLayout()
        self.label_edit = QLineEdit()
        form.addRow("Label:", self.label_edit)

        self.tags_edit = TagLineEdit(known_tags or [])
        self.tags_edit.setPlaceholderText("comma, separated")
        form.addRow("Add tags:", self.tags_edit)
        self.replace_tags = QCheckBox("Replace existing tags instead of adding")
        form.addRow("", self.replace_tags)

        self.detail_edits: dict[str, DetailEdit] = {}
        for key, label_text in DETAIL_FIELDS:
            edit = DetailEdit()
            configure_detail_edit(edit, key, settings)
            self.detail_edits[key] = edit
            form.addRow(f"{label_text}:", edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        """Only the fields the user actually filled in."""
        return {
            "label": self.label_edit.text().strip(),
            "tags": [t.strip() for t in self.tags_edit.text().split(",")
                     if t.strip()],
            "replace_tags": self.replace_tags.isChecked(),
            "details": {k: e.text().strip()
                        for k, e in self.detail_edits.items()
                        if e.text().strip()},
        }
