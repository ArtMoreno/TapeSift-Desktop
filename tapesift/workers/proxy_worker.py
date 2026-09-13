"""Background generation of the scrub-optimized preview proxy.

Runs one FFmpeg encode at low priority relative to the UI (it's a separate
process; the app thread only relays progress). Falls back to CPU encoding
automatically if the hardware encoder fails.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from tapesift.services import proxy_service
from tapesift.services.export_service import FFmpegRunner

log = logging.getLogger(__name__)


class ProxyWorker(QThread):
    progress = Signal(float)          # 0-100
    proxy_ready = Signal(str, str)    # source path, proxy path
    failed = Signal(str)              # message

    def __init__(self, ffmpeg_path: str, source: Path, output_folder: Path,
                 duration_ms: int, hw_encoders: list[str] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.source = source
        self.output_folder = output_folder
        self.duration_ms = duration_ms
        # A build can LIST an encoder whose driver can't actually open it
        # (e.g. outdated NVIDIA driver) - so we try candidates in order and
        # fall through to CPU. A failed attempt exits in well under a second.
        self.encoder_candidates = list(hw_encoders or []) + [""]
        self.runner = FFmpegRunner(low_priority=True)

    def cancel(self) -> None:
        self.runner.cancel_event.set()

    def run(self) -> None:
        target = proxy_service.proxy_path_for(self.source, self.output_folder)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".part.mp4")
        last_error = ""
        try:
            source_times = proxy_service.video_timestamps(self.ffmpeg_path, self.source)
        except Exception as exc:
            if not self.runner.cancel_event.is_set():
                self.failed.emit(f"Could not verify source timing: {exc}")
            return
        for encoder in self.encoder_candidates:
            if self.runner.cancel_event.is_set():
                tmp.unlink(missing_ok=True)
                return
            cmd = proxy_service.build_proxy_command(
                self.ffmpeg_path, self.source, tmp, encoder)
            try:
                self.runner.run(cmd, self.duration_ms, self.progress.emit)
                if self.runner.cancel_event.is_set():
                    tmp.unlink(missing_ok=True)
                    return
                # Exit code 0 is not proof the file plays. A driver-level
                # NVENC fault writes a full-length file of broken NAL units
                # and still reports success - the container parses and the
                # duration is right, so nothing downstream notices until the
                # player stalls on a seek into the damage, with no error
                # anywhere to explain it. Verify before promoting, and treat
                # a bad file exactly like a failed encoder so the fallback
                # chain does its job.
                problem = proxy_service.verify_playable(
                    self.ffmpeg_path, tmp, self.duration_ms)
                if problem:
                    raise RuntimeError(problem)
                problem = proxy_service.verify_timing(
                    source_times, proxy_service.video_timestamps(self.ffmpeg_path, tmp))
                if problem:
                    raise RuntimeError(problem)
                if self.runner.cancel_event.is_set():
                    tmp.unlink(missing_ok=True)
                    return
                tmp.replace(target)
                proxy_service.cleanup_stale_proxies(self.source, self.output_folder)
                log.info("Preview proxy ready (%s): %s",
                         encoder or "cpu", target.name)
                self.proxy_ready.emit(str(self.source), str(target))
                return
            except Exception as exc:
                last_error = str(exc)
                tmp.unlink(missing_ok=True)
                if self.runner.cancel_event.is_set():
                    return
                if encoder:
                    log.warning("Proxy encode with %s failed; trying next "
                                "encoder. (%s)", encoder, exc)
                    self.runner = FFmpegRunner(low_priority=True)
        if not self.runner.cancel_event.is_set():
            log.error("Proxy generation failed: %s", last_error)
            self.failed.emit(last_error)
