"""Post-mortem crash dialog shown on next launch after a crash.

Local-first: nothing is uploaded. The user can open the crash folder or copy
the report text to paste into an email/chat. Dismissing marks the report as
seen so the dialog does not re-appear for the same crash.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QVBoxLayout,
)

from tapesift.core import crash_reporter


def maybe_show_crash_dialog(parent=None) -> None:
    """Show the crash dialog if unread reports exist. No-op otherwise."""
    reports = crash_reporter.pending_reports()
    if not reports:
        return
    dlg = CrashDialog(reports, parent)
    dlg.exec()
    crash_reporter.mark_reports_seen(reports)


class CrashDialog(QDialog):
    def __init__(self, reports: list[Path], parent=None) -> None:
        super().__init__(parent)
        self.reports = reports
        self.setWindowTitle("TapeSift crashed last run")
        self.setMinimumWidth(560)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "TapeSift didn't close cleanly last time. No data was sent "
            "anywhere - this report stays on your machine. Send the report "
            "below to the developer to help fix it."))

        latest = reports[0]
        try:
            text = latest.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = f"(could not read {latest.name})"
        self._text = QPlainTextEdit(text)
        self._text.setReadOnly(True)
        self._text.setMaximumHeight(260)
        layout.addWidget(self._text)

        folder_btn = QPushButton("Open crash folder")
        folder_btn.clicked.connect(self._open_folder)
        copy_btn = QPushButton("Copy report")
        copy_btn.clicked.connect(self._copy)

        actions = QHBoxLayout()
        actions.addWidget(folder_btn)
        actions.addWidget(copy_btn)
        actions.addStretch()
        layout.addLayout(actions)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        box.accepted.connect(self.accept)
        layout.addWidget(box)
        self.setLayout(layout)

    def _open_folder(self) -> None:
        folder = self.reports[0].parent
        # Open the folder in the OS file manager (local-only).
        try:
            import subprocess
            subprocess.Popen(["explorer", str(folder)])
        except Exception:
            QMessageBox.information(
                self, "Crash folder",
                f"Crash reports are in:\n{folder}")

    def _copy(self) -> None:
        from PySide6.QtWidgets import QApplication
        cb = self._text.toPlainText()
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(cb)
        QMessageBox.information(self, "Copied", "Report text copied to clipboard.")
