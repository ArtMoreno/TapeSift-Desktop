"""Watching First Read work.

Play detection shows live source frames moving through its scan. This shows
the frames First Read is actually reading - the real contact sheet, at the
layout the model receives - so a misread play can be understood rather than
just counted.

The dialog never owns the work. ``Keep working`` hides it and the run
carries on; closing the window does the same. Only ``Stop`` ends the run.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)

#: How many finished plays stay on screen. Enough to read the engine's
#: rhythm - a film it is reading badly shows up here long before the run
#: ends - without becoming a second play list.
VERDICT_HISTORY = 6

_VERDICT_STYLE = {
    "run": "color:#e0b341;border-color:#5c4a1e;",
    "pass": "color:#7ca6e6;border-color:#2f4360;",
    "ask": "color:#e0b341;border-color:#5c4a1e;background:#2a2416;",
    "failed": "color:#686555;border-color:#2c2d27;",
}


class VerdictStrip(QWidget):
    """The last few calls, in the order they landed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(6)
        self._row.addStretch(1)
        self._chips: list[QLabel] = []

    def add(self, text: str, kind: str) -> None:
        chip = QLabel(text, self)
        chip.setObjectName("FirstReadVerdict")
        chip.setStyleSheet(
            "QLabel#FirstReadVerdict{border:1px solid;border-radius:3px;"
            "padding:2px 7px;font-size:10pt;"
            + _VERDICT_STYLE.get(kind, _VERDICT_STYLE["failed"]) + "}")
        self._row.insertWidget(self._row.count() - 1, chip)
        self._chips.append(chip)
        while len(self._chips) > VERDICT_HISTORY:
            old = self._chips.pop(0)
            self._row.removeWidget(old)
            old.deleteLater()


class FirstReadDialog(QDialog):
    """Progress for one First Read run."""

    stop_requested = Signal()

    def __init__(self, total: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("First Read")
        self.setModal(False)
        self._total = max(total, 0)
        self._suggested = 0
        self._needs = 0
        self._failed = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 18)
        outer.setSpacing(14)

        self.sheet = QLabel("Preparing the first play…", self)
        self.sheet.setObjectName("FirstReadSheet")
        self.sheet.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sheet.setMinimumSize(560, 250)
        self.sheet.setFrameShape(QFrame.Shape.StyledPanel)
        self.sheet.setStyleSheet(
            "QLabel#FirstReadSheet{background:#0e0f0c;border:1px solid #424239;"
            "border-radius:4px;color:#686555;}")
        outer.addWidget(self.sheet)

        self.caption = QLabel("", self)
        self.caption.setProperty("role", "subtle")
        outer.addWidget(self.caption)

        self.verdicts = VerdictStrip(self)
        outer.addWidget(self.verdicts)

        self.bar = QProgressBar(self)
        self.bar.setRange(0, self._total or 1)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        outer.addWidget(self.bar)

        self.tally = QLabel("", self)
        self.tally.setProperty("role", "subtle")
        outer.addWidget(self.tally)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.stop_button = QPushButton("Stop", self)
        self.stop_button.clicked.connect(self._stop)
        buttons.addWidget(self.stop_button)
        self.hide_button = QPushButton("Keep working", self)
        self.hide_button.clicked.connect(self.hide)
        buttons.addWidget(self.hide_button)
        outer.addLayout(buttons)

        self._refresh_tally()

    def _stop(self) -> None:
        self.stop_button.setEnabled(False)
        self.stop_button.setText("Stopping…")
        self.stop_requested.emit()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Closing means "let it run without me", never "cancel".

        A run costs money already spent and plays already answered. Losing
        that to a stray click on the window frame would be the worst
        available reading of the gesture.
        """
        event.ignore()
        self.hide()

    def show_progress(self, progress) -> None:
        """One finished play: its frames, its verdict, the running totals."""
        read = getattr(progress, "read", None)
        # A failed/missing preview must never keep the previous play visible.
        self.sheet.setText("No frames available for this play.")
        sheet_bytes = getattr(progress, "sheet_bytes", None)
        sheet_path: Path | None = getattr(progress, "sheet_path", None)
        if read is None or not read.error:
            pixmap = QPixmap()
            if sheet_bytes is not None:
                pixmap.loadFromData(sheet_bytes)
            elif sheet_path is not None and Path(sheet_path).is_file():
                pixmap.load(str(sheet_path))
            if not pixmap.isNull():
                self.sheet.setPixmap(pixmap.scaled(
                    self.sheet.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))

        index = getattr(progress, "index", 0)
        if read is not None:
            if read.error:
                kind, word = "failed", "no frames"
                self._failed += 1
            elif read.needs_analyst:
                kind, word = "ask", "disagree"
                self._needs += 1
            else:
                kind, word = read.label, read.label
                self._suggested += 1
            self.verdicts.add(f"{index} {word}", kind)

        self.caption.setText(
            f"Play {index} of {self._total}  |  8 frames, 960px, both views")
        self.bar.setValue(index)
        self._refresh_tally()

    def _refresh_tally(self) -> None:
        done = self._suggested + self._needs + self._failed
        spent = done * 0.0012
        parts = [f"{self._suggested} suggested",
                 f"{self._needs} need your call"]
        if self._failed:
            parts.append(f"{self._failed} could not be read")
        self.tally.setText("  ·  ".join(parts) + f"  ·  ${spent:.2f}")

    def finish(self, summary) -> None:
        """Swap the controls for a way out once the run is over."""
        self.bar.setValue(self.bar.maximum())
        self.caption.setText(summary.describe())
        self.stop_button.hide()
        self.hide_button.setText("Close")
        self.hide_button.clicked.disconnect()
        self.hide_button.clicked.connect(self.accept)
