"""Background metadata extraction so loading a video never blocks the UI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from tapesift.core.exceptions import TapeSiftError
from tapesift.services import ffprobe_service


class MetadataWorker(QThread):
    finished_ok = Signal(object)   # VideoMetadata
    failed = Signal(str)           # readable error text

    def __init__(self, ffprobe_path: str, video_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.ffprobe_path = ffprobe_path
        self.video_path = video_path

    def run(self) -> None:
        try:
            meta = ffprobe_service.probe_video(self.ffprobe_path, self.video_path)
            self.finished_ok.emit(meta)
        except TapeSiftError as exc:
            self.failed.emit(exc.user_text())
        except Exception as exc:
            self.failed.emit(f"Unexpected error while reading the video: {exc}")
