"""Background play detection - one FFmpeg analysis pass, off the UI thread."""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from tapesift.core.exceptions import TapeSiftError
from tapesift.services import play_detect_service
from tapesift.services.play_detect_service import DetectionResult

log = logging.getLogger(__name__)


class PlayDetectWorker(QThread):
    finished_ok = Signal(object)   # DetectionResult
    failed = Signal(str)

    def __init__(self, ffmpeg_path: str, source: Path, duration_ms: int,
                 separator_max_s: float, min_play_s: float, max_play_s: float,
                 parent=None) -> None:
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.source = source
        self.duration_ms = duration_ms
        self.separator_max_s = separator_max_s
        self.min_play_s = min_play_s
        self.max_play_s = max_play_s
        self._cancel_requested = threading.Event()
        self._process_lock = threading.Lock()
        self._process: subprocess.Popen | None = None

    def _process_started(self, process: subprocess.Popen) -> None:
        with self._process_lock:
            self._process = process
        if self._cancel_requested.is_set():
            self._stop_process(process)

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def cancel(self) -> None:
        self._cancel_requested.set()
        with self._process_lock:
            process = self._process
        if process is not None:
            self._stop_process(process)
        self.wait(5000)

    def run(self) -> None:
        try:
            result: DetectionResult = play_detect_service.detect_plays(
                self.ffmpeg_path, self.source, self.duration_ms,
                separator_max_s=self.separator_max_s,
                min_play_s=self.min_play_s, max_play_s=self.max_play_s,
                process_started=self._process_started)
            if not self._cancel_requested.is_set():
                self.finished_ok.emit(result)
        except Exception as exc:
            if not self._cancel_requested.is_set():
                log.exception("Play detection failed")
                self.failed.emit(
                    exc.user_text() if isinstance(exc, TapeSiftError) else str(exc))
        finally:
            with self._process_lock:
                self._process = None
