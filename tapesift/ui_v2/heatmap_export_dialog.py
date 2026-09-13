"""Pick what the heat map comes out as, and where it lands.

Formats are checkboxes rather than a dropdown because they are not
alternatives: the picture goes in the deck, the CSV goes to whoever wants
to count, and they have to be the same export or the two disagree.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from tapesift.services.heatmap_export import FORMATS, export_all
from tapesift.services.heatmap_service import HeatmapData

DEFAULT_FORMATS = ("png", "pdf", "csv")


class HeatmapExportDialog(QDialog):
    """Choose formats and a folder; every file comes from one render."""

    def __init__(self, data: HeatmapData, folder: Path,
                 stem: str = "heatmap", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Export Game Heat Map")
        self.setObjectName("V2HeatmapExportDialog")
        self.setMinimumWidth(460)
        self._data = data
        self.written: list[Path] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        summary = QLabel(
            f"{data.play_count} plays  .  {len(data.players)} players"
            f"  .  {data.unassigned} unassigned")
        summary.setProperty("role", "pickerTitle")
        layout.addWidget(summary)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name"))
        self.stem_edit = QLineEdit(stem)
        name_row.addWidget(self.stem_edit, 1)
        layout.addLayout(name_row)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder"))
        self.folder_edit = QLineEdit(str(folder))
        folder_row.addWidget(self.folder_edit, 1)
        browse = QPushButton("Browse")
        browse.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        browse.clicked.connect(self._browse)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        self.checks: dict[str, QCheckBox] = {}
        for key, label, _filter in FORMATS:
            box = QCheckBox(label)
            box.setChecked(key in DEFAULT_FORMATS)
            layout.addWidget(box)
            self.checks[key] = box

        note = QLabel(
            "The PNG carries the full heat map data inside the file, so the "
            "numbers travel with the picture.")
        note.setWordWrap(True)
        note.setProperty("role", "pickerTitle")
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_formats(self) -> tuple[str, ...]:
        return tuple(key for key, box in self.checks.items() if box.isChecked())

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(
            self, "Export heat map to", self.folder_edit.text())
        if chosen:
            self.folder_edit.setText(chosen)

    def _save(self) -> None:
        formats = self.selected_formats()
        if not formats:
            QMessageBox.information(
                self, "Export Game Heat Map", "Pick at least one format.")
            return
        stem = self.stem_edit.text().strip() or "heatmap"
        folder = Path(self.folder_edit.text().strip() or ".")
        try:
            self.written = export_all(self._data, folder, stem, formats)
        except OSError as error:
            QMessageBox.warning(
                self, "Export Game Heat Map", f"Could not write it: {error}")
            return
        self.accept()
