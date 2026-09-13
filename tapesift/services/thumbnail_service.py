"""Thumbnail generation (Qt-free)."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from tapesift.models.clip import Clip
from tapesift.services import background_service, ffmpeg_service

log = logging.getLogger(__name__)

THUMB_MODE_MIDPOINT = "midpoint"
THUMB_MODE_FIRST_FRAME = "first_frame"
THUMB_MODE_CENTRAL = "central"


def thumbnail_time_ms(clip: Clip, mode: str = THUMB_MODE_MIDPOINT,
                      custom_ms: int | None = None) -> int:
    if custom_ms is not None:
        return custom_ms
    if mode == THUMB_MODE_FIRST_FRAME:
        return clip.start_ms
    if mode == THUMB_MODE_CENTRAL and clip.central_timestamp_ms is not None:
        return clip.central_timestamp_ms
    return clip.start_ms + clip.duration_ms // 2


def generate_thumbnail(ffmpeg_path: str, source: Path, clip: Clip,
                       cache_dir: Path, mode: str = THUMB_MODE_MIDPOINT,
                       custom_ms: int | None = None) -> Path | None:
    """Generate (or regenerate) a clip thumbnail. Returns the path, or None on failure."""
    if not ffmpeg_path or not source.is_file():
        return None
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{clip.id}.jpg"
    time_ms = thumbnail_time_ms(clip, mode, custom_ms)
    cmd = ffmpeg_service.build_thumbnail_command(ffmpeg_path, source, out, time_ms)
    try:
        # Idle priority AND registered with the background gate: a batch of
        # these (e.g. after detecting 50 plays) must never disturb playback.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True,
                                creationflags=ffmpeg_service.CREATE_NO_WINDOW |
                                ffmpeg_service.IDLE_PRIORITY_CLASS,
                                stdin=subprocess.DEVNULL)
    except OSError as exc:
        log.warning("Thumbnail generation failed for clip %s: %s", clip.id, exc)
        return None
    background_service.register(proc.pid)
    try:
        proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        log.warning("Thumbnail generation timed out for clip %s", clip.id)
        return None
    finally:
        background_service.unregister(proc.pid)
    if proc.returncode != 0 or not out.exists():
        log.warning("Thumbnail generation failed for clip %s", clip.id)
        return None
    return out
