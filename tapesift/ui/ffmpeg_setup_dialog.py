"""Setup screen shown when FFmpeg/FFprobe cannot be found."""

from __future__ import annotations

from pathlib import Path
import os

from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from tapesift.core.config import AppSettings
from tapesift.services import ffmpeg_service


class FFmpegSetupDialog(QDialog):
    """Lets the user locate ffmpeg.exe / ffprobe.exe manually."""

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("TapeSift - FFmpeg Setup")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        heading = QLabel("FFmpeg is required")
        heading.setProperty("role", "heading")
        layout.addWidget(heading)
        installation = (
            "Download a Windows build from ffmpeg.org, unzip it, then select "
            "ffmpeg.exe and ffprobe.exe from its bin folder."
            if os.name == "nt" else
            "Install FFmpeg with your package manager (sudo pacman -S ffmpeg "
            "on Arch/Omarchy, or sudo apt install ffmpeg on Ubuntu). "
            "Then select ffmpeg and ffprobe, usually in /usr/bin."
        )
        explanation = QLabel(
            "TapeSift uses FFmpeg to read and cut video, and FFprobe to inspect "
            "files. They could not be found on this computer.\n\n"
            + installation
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)

        self.ffmpeg_edit = QLineEdit(self.settings.ffmpeg_path)
        self.ffprobe_edit = QLineEdit(self.settings.ffprobe_path)
        layout.addLayout(self._path_row("ffmpeg.exe:", self.ffmpeg_edit))
        layout.addLayout(self._path_row("ffprobe.exe:", self.ffprobe_edit))

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        test_btn = QPushButton("Test installation")
        test_btn.clicked.connect(self._test)
        ok_btn = QPushButton("Save and continue")
        ok_btn.setProperty("accent", "true")
        ok_btn.clicked.connect(self._accept_if_valid)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(test_btn)
        buttons.addStretch()
        buttons.addWidget(cancel_btn)
        buttons.addWidget(ok_btn)
        layout.addLayout(buttons)

    def _path_row(self, label_text: str, edit: QLineEdit) -> QHBoxLayout:
        row = QHBoxLayout()
        label = QLabel(label_text)
        label.setMinimumWidth(90)
        browse = QPushButton("Browse…")

        def pick() -> None:
            path, _ = QFileDialog.getOpenFileName(
                self, f"Locate {label_text.rstrip(':')}", "",
                "Executables (*.exe);;All files (*)" if os.name == "nt" else "All files (*)")
            if path:
                edit.setText(path)
                # Convenience: auto-fill the sibling tool from the same folder.
                sibling_name = "ffprobe" if "ffmpeg" in label_text else "ffmpeg"
                if os.name == "nt":
                    sibling_name += ".exe"
                sibling = Path(path).parent / sibling_name
                target = self.ffprobe_edit if "ffmpeg" in label_text else self.ffmpeg_edit
                if sibling.is_file() and not target.text():
                    target.setText(str(sibling))

        browse.clicked.connect(pick)
        row.addWidget(label)
        row.addWidget(edit, 1)
        row.addWidget(browse)
        return row

    def _test(self) -> bool:
        ffmpeg_v = ffmpeg_service.get_version(self.ffmpeg_edit.text().strip())
        ffprobe_v = ffmpeg_service.get_version(self.ffprobe_edit.text().strip())
        lines = []
        lines.append(f"FFmpeg: {ffmpeg_v or 'NOT working - check the path'}")
        lines.append(f"FFprobe: {ffprobe_v or 'NOT working - check the path'}")
        ok = bool(ffmpeg_v and ffprobe_v)
        self.status_label.setProperty("role", "subtle" if ok else "error")
        self.status_label.setText("\n".join(lines))
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
        return ok

    def _accept_if_valid(self) -> None:
        if self._test():
            self.settings.ffmpeg_path = self.ffmpeg_edit.text().strip()
            self.settings.ffprobe_path = self.ffprobe_edit.text().strip()
            self.settings.ffmpeg_path_manual = True   # a deliberate choice
            self.settings.save()
            self.accept()


def ensure_ffmpeg(settings: AppSettings, parent=None) -> bool:
    """Detect FFmpeg/FFprobe; prompt with the setup dialog if missing.

    Returns True when both tools are available and stored in settings.
    """
    # Only a manual choice is honoured as an override. Re-resolving on every
    # start also means a stored path that has since been uninstalled heals
    # itself instead of failing every export.
    stored_ffmpeg = settings.ffmpeg_path if settings.ffmpeg_path_manual else ""
    stored_ffprobe = settings.ffprobe_path if settings.ffmpeg_path_manual else ""
    ffmpeg = ffmpeg_service.find_executable("ffmpeg", stored_ffmpeg)
    ffprobe = ffmpeg_service.find_executable("ffprobe", stored_ffprobe)
    if ffmpeg and ffprobe:
        if (ffmpeg, ffprobe) != (settings.ffmpeg_path, settings.ffprobe_path):
            settings.ffmpeg_path = ffmpeg
            settings.ffprobe_path = ffprobe
            settings.save()
        return True
    dialog = FFmpegSetupDialog(settings, parent)
    return dialog.exec() == QDialog.DialogCode.Accepted
