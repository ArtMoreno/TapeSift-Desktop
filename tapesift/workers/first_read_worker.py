"""Background First Read over a whole game.

The batch logic lives in ``first_read_batch``; this is the thread around it.
Results are emitted one play at a time rather than collected at the end, so
the window can save each suggestion as it lands - a run that is stopped, or
that dies, keeps every play it already paid for.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from tapesift.services import first_read_batch

log = logging.getLogger(__name__)


class FirstReadWorker(QThread):
    """Reads plays off the GUI thread and reports each one as it finishes."""

    #: clip_id, analysis_json - the caller persists it.
    result_ready = Signal(str, str)
    #: One BatchProgress, for the dialog.
    progressed = Signal(object)
    #: One BatchSummary, when the run ends for any reason.
    finished_batch = Signal(object)

    def __init__(self, clips, *, ffmpeg_path: str, source: Path,
                 scratch_dir: Path, api_key: str, model: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        # Editing a live clip must not change frames halfway through a batch.
        self._clips = copy.deepcopy(list(clips))
        self._ffmpeg_path = ffmpeg_path
        self._source = source
        self._scratch_dir = scratch_dir
        self._api_key = api_key
        self._model = model
        self._stop = False

    def stop(self) -> None:
        """Ask the run to end after the play in flight."""
        self._stop = True

    def plan(self):
        return first_read_batch.plan(self._clips)

    def run(self) -> None:
        try:
            summary = first_read_batch.run(
                self._clips,
                ffmpeg_path=self._ffmpeg_path,
                source=self._source,
                scratch_dir=self._scratch_dir,
                api_key=self._api_key,
                model=self._model,
                save=lambda clip_id, analysis: self.result_ready.emit(
                    clip_id, analysis),
                on_progress=self.progressed.emit,
                should_cancel=lambda: self._stop,
            )
        except Exception:
            log.exception("First Read worker stopped unexpectedly")
            summary = first_read_batch.BatchSummary(
                stopped_reason="Unexpected error. Completed suggestions were kept.")
        self.finished_batch.emit(summary)
