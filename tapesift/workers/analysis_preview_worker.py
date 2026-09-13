"""Low-priority source-frame sampling for the Detect Plays Analyze view."""

from __future__ import annotations

import logging
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from tapesift.services import background_service, ffmpeg_service

log = logging.getLogger(__name__)


class AnalysisPreviewWorker(QThread):
    """Extract a few honest source frames without touching detector output."""

    frame_ready = Signal(int, int, str)  # sample index, source ms, image path
    preview_failed = Signal(str)

    def __init__(
        self,
        ffmpeg_path: str,
        source: Path,
        duration_ms: int,
        cache_dir: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.source = source
        self.duration_ms = max(0, int(duration_ms))
        self.cache_dir = cache_dir
        self._stop_requested = False
        self._process: subprocess.Popen | None = None
        self._process_lock = threading.Lock()

    @staticmethod
    def sample_timestamps(duration_ms: int) -> list[int]:
        """Return five stable, well-spaced positions inside the source."""
        duration = max(0, int(duration_ms))
        if duration <= 0:
            return []
        return [
            min(duration - 1, max(0, round(duration * fraction)))
            for fraction in (0.12, 0.26, 0.385, 0.56, 0.78)
        ]

    def stop(self) -> None:
        self._stop_requested = True
        with self._process_lock:
            process = self._process
        if process and process.poll() is None:
            process.kill()

    def run(self) -> None:
        if (
            not self.ffmpeg_path
            or not self.source.is_file()
            or self.duration_ms <= 0
        ):
            self.preview_failed.emit(
                "Source frames are unavailable for the live preview.")
            return

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        emitted = 0
        last_error = ""
        for index, timestamp_ms in enumerate(
            self.sample_timestamps(self.duration_ms)
        ):
            if self._stop_requested:
                return
            output = self.cache_dir / f"analysis-frame-{index}.jpg"
            command = ffmpeg_service.build_thumbnail_command(
                self.ffmpeg_path,
                self.source,
                output,
                timestamp_ms,
                width=760,
            )
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=(
                        ffmpeg_service.CREATE_NO_WINDOW
                        | ffmpeg_service.IDLE_PRIORITY_CLASS
                    ),
                    stdin=subprocess.DEVNULL,
                )
            except OSError as exc:
                last_error = str(exc)
                log.warning("Analyze preview frame failed: %s", exc)
                break

            with self._process_lock:
                self._process = process
            background_service.register(process.pid)
            try:
                _stdout, stderr = process.communicate(timeout=45)
            except subprocess.TimeoutExpired:
                process.kill()
                _stdout, stderr = process.communicate()
                last_error = "frame extraction timed out"
            finally:
                background_service.unregister(process.pid)
                with self._process_lock:
                    if self._process is process:
                        self._process = None

            if self._stop_requested:
                return
            if process.returncode == 0 and output.is_file():
                emitted += 1
                self.frame_ready.emit(index, timestamp_ms, str(output))
            else:
                last_error = (
                    stderr.strip().splitlines()[-1]
                    if stderr and stderr.strip()
                    else f"FFmpeg exited with code {process.returncode}"
                )
                log.warning(
                    "Analyze preview frame %d failed: %s",
                    index,
                    last_error,
                )

        if emitted == 0 and not self._stop_requested:
            self.preview_failed.emit(
                last_error or
                "Source frames are unavailable for the live preview.")
