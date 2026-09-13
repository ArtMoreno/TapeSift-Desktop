"""Bulk paste and CSV import dialogs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from tapesift.core.exceptions import CsvImportError
from tapesift.services import csv_import_service
from tapesift.services.bulk_paste_parser import BulkParseResult, parse_bulk_text
from tapesift.services.csv_import_service import COLUMN_ALIASES, CsvImportResult
from tapesift.services.timestamp_parser import format_ms


class BulkPasteDialog(QDialog):
    """Multi-line paste of timestamps/ranges with names."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bulk Paste Timestamps")
        self.setMinimumSize(560, 460)
        layout = QVBoxLayout(self)

        hint = QLabel(
            "One entry per line. Examples:\n"
            "    03:14 | Opening sequence\n"
            "    18:40 - 18:55 | Important reaction shot\n"
            "Single timestamps get the default pre/post-roll. "
            "Text after | becomes the clip name and filename.")
        hint.setProperty("role", "subtle")
        layout.addWidget(hint)

        self.text_edit = QPlainTextEdit()
        layout.addWidget(self.text_edit, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        row = QHBoxLayout()
        check_btn = QPushButton("Check entries")
        check_btn.clicked.connect(self._preview)
        row.addWidget(check_btn)
        row.addStretch()
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add clips")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.result: BulkParseResult | None = None

    def _parse(self) -> BulkParseResult:
        return parse_bulk_text(self.text_edit.toPlainText())

    def _preview(self) -> None:
        result = self._parse()
        parts = [f"{len(result.rows)} valid entr{'y' if len(result.rows) == 1 else 'ies'}."]
        if result.errors:
            parts.append(f"{len(result.errors)} line(s) need fixing:")
            for line_number, raw, message in result.errors[:8]:
                parts.append(f"  line {line_number}: {raw!r} - {message}")
            self.status_label.setProperty("role", "warning")
        else:
            self.status_label.setProperty("role", "subtle")
        self.status_label.setText("\n".join(parts))
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _accept_if_valid(self) -> None:
        result = self._parse()
        if not result.rows:
            self._preview()
            return
        if result.errors:
            # Show what will be skipped; the user can fix lines and re-check.
            self._preview()
            self.status_label.setText(
                self.status_label.text() +
                "\nValid entries will be added; fix the lines above or press "
                "'Add clips' again to skip them.")
            # Second press with same errors proceeds:
            if getattr(self, "_warned_once", False):
                self.result = result
                self.accept()
            self._warned_once = True
            return
        self.result = result
        self.accept()


class CsvImportDialog(QDialog):
    """CSV import with a column-mapping step and row preview."""

    MAPPABLE = list(COLUMN_ALIASES.keys())

    def __init__(self, csv_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.csv_path = csv_path
        self.setWindowTitle(f"CSV Import - {csv_path.name}")
        self.setMinimumSize(640, 520)
        self.result: CsvImportResult | None = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Match your CSV columns to TapeSift fields. "
            "Naming priority: clip_name, then title, then filename."))

        self.headers = csv_import_service.read_headers(csv_path)
        detected = csv_import_service.detect_mapping(self.headers)

        form = QFormLayout()
        self.combos: dict[str, QComboBox] = {}
        for field in self.MAPPABLE:
            combo = QComboBox()
            combo.addItem("(not used)", "")
            for header in self.headers:
                combo.addItem(header, header)
            if field in detected:
                combo.setCurrentIndex(self.headers.index(detected[field]) + 1)
            combo.currentIndexChanged.connect(self._refresh_preview)
            self.combos[field] = combo
            form.addRow(f"{field}:", combo)
        layout.addLayout(form)

        self.preview_table = QTableWidget(0, 4)
        self.preview_table.setHorizontalHeaderLabels(["Time", "End", "Name", "Label"])
        self.preview_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.preview_table, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import clips")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_preview()

    def _current_mapping(self) -> dict[str, str]:
        return {field: combo.currentData()
                for field, combo in self.combos.items() if combo.currentData()}

    def _parse(self) -> CsvImportResult | None:
        try:
            return csv_import_service.parse_csv(self.csv_path, self._current_mapping())
        except CsvImportError as exc:
            self.status_label.setProperty("role", "error")
            self.status_label.setText(exc.user_text())
            self.status_label.style().unpolish(self.status_label)
            self.status_label.style().polish(self.status_label)
            return None

    def _refresh_preview(self) -> None:
        result = self._parse()
        self.preview_table.setRowCount(0)
        if result is None:
            return
        for row_data in result.rows[:50]:
            row = self.preview_table.rowCount()
            self.preview_table.insertRow(row)
            time_ms = row_data.timestamp_ms if row_data.timestamp_ms is not None \
                else row_data.start_ms
            self.preview_table.setItem(row, 0, QTableWidgetItem(
                format_ms(time_ms or 0, show_millis=True)))
            self.preview_table.setItem(row, 1, QTableWidgetItem(
                format_ms(row_data.end_ms, show_millis=True)
                if row_data.end_ms is not None else "(pre/post-roll)"))
            self.preview_table.setItem(row, 2, QTableWidgetItem(row_data.name))
            self.preview_table.setItem(row, 3, QTableWidgetItem(row_data.label))
        parts = [f"{len(result.rows)} row(s) will import."]
        if result.errors:
            parts.append(f"{len(result.errors)} row(s) have problems and will be skipped:")
            for line_number, message in result.errors[:6]:
                parts.append(f"  row {line_number}: {message}")
            self.status_label.setProperty("role", "warning")
        else:
            self.status_label.setProperty("role", "subtle")
        self.status_label.setText("\n".join(parts))
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _accept_if_valid(self) -> None:
        result = self._parse()
        if result and result.rows:
            self.result = result
            self.accept()
