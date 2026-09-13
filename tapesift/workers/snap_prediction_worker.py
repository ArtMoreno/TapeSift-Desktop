"""Background bridge for the on-demand predicted-snap timeline action."""

from __future__ import annotations

from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, Signal

from tapesift.core.exceptions import TapeSiftError
from tapesift.services.snap_prediction_service import (
    SnapPredictionCancelled,
    predict_snap,
)


class SnapPredictionWorker(QThread):
    prediction_ready = Signal(str, object)  # clip id, prediction dictionary
    failed = Signal(str, str)               # clip id, readable error

    def __init__(
            self,
            ffmpeg_path: str,
            source: Path,
            clip_id: str,
            start_ms: int,
            end_ms: int,
            angle_starts_ms: tuple[int, ...] = (),
            parent=None,
    ) -> None:
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.source = Path(source)
        self.clip_id = clip_id
        self.start_ms = int(start_ms)
        self.end_ms = int(end_ms)
        self.angle_starts_ms = tuple(int(value) for value in angle_starts_ms)
        self._cancel_event = Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            prediction = predict_snap(
                self.ffmpeg_path,
                self.source,
                self.start_ms,
                self.end_ms,
                angle_starts_ms=self.angle_starts_ms,
                cancel_event=self._cancel_event,
            )
            if not self._cancel_event.is_set():
                self.prediction_ready.emit(self.clip_id, prediction)
        except SnapPredictionCancelled:
            return
        except TapeSiftError as exc:
            self.failed.emit(self.clip_id, exc.user_text())
        except Exception as exc:
            self.failed.emit(
                self.clip_id,
                f"Unexpected error while predicting the snap: {exc}",
            )
