"""Background thumbnail generation for the clip list."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from tapesift.models.clip import Clip
from tapesift.services import thumbnail_service


class ThumbnailWorker(QThread):
    thumbnail_ready = Signal(str, str)  # clip_id, thumbnail path

    def __init__(self, ffmpeg_path: str, source: Path, clips: list[Clip],
                 cache_dir: Path, parent=None) -> None:
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.source = source
        self.clips = list(clips)
        self.cache_dir = cache_dir
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        for clip in self.clips:
            if self._stop:
                return
            path = thumbnail_service.generate_thumbnail(
                self.ffmpeg_path, self.source, clip, self.cache_dir)
            if path:
                self.thumbnail_ready.emit(clip.id, str(path))
