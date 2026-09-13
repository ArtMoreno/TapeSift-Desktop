"""Video inspection via FFprobe."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from tapesift.core.exceptions import FFprobeNotFoundError, InvalidVideoError
from tapesift.models.video_metadata import VideoMetadata
from tapesift.services.ffmpeg_service import _subprocess_kwargs

log = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}


def _parse_frame_rate(rate: str) -> float:
    if not rate or rate == "0/0":
        return 0.0
    if "/" in rate:
        num, _, den = rate.partition("/")
        try:
            d = float(den)
            return round(float(num) / d, 3) if d else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


def probe_video(ffprobe_path: str, video_path: Path) -> VideoMetadata:
    """Extract metadata. Raises InvalidVideoError on unreadable/corrupt files."""
    if not ffprobe_path:
        raise FFprobeNotFoundError(
            "FFprobe is not configured.",
            "Open Settings > FFmpeg and point TapeSift at ffprobe.exe.",
        )
    if not video_path.is_file():
        raise InvalidVideoError(
            f"File not found: {video_path}",
            "Check that the file still exists at this location.",
        )

    cmd = [ffprobe_path, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", str(video_path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                              **_subprocess_kwargs())
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InvalidVideoError(
            f"Could not inspect the video: {exc}",
            "Verify the FFprobe path in Settings and that the file is readable.",
        )

    if proc.returncode != 0 or not proc.stdout.strip():
        detail = (proc.stderr or "").strip().splitlines()
        detail_text = detail[-1] if detail else "no detail available"
        raise InvalidVideoError(
            f"This file could not be read as a video ({detail_text}).",
            "The file may be corrupt or use an unsupported format.",
        )

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise InvalidVideoError("FFprobe returned unreadable output for this file.")

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    if video_stream is None:
        raise InvalidVideoError(
            "No video stream was found in this file.",
            "The file may be audio-only or corrupt.",
        )

    duration_s = float(fmt.get("duration") or video_stream.get("duration") or 0.0)
    meta = VideoMetadata(
        path=str(video_path),
        container=fmt.get("format_name", ""),
        duration_ms=round(duration_s * 1000),
        file_size_bytes=int(fmt.get("size") or video_path.stat().st_size),
        width=int(video_stream.get("width") or 0),
        height=int(video_stream.get("height") or 0),
        frame_rate=_parse_frame_rate(video_stream.get("avg_frame_rate", "")),
        video_codec=video_stream.get("codec_name", ""),
        audio_codec=audio_stream.get("codec_name", "") if audio_stream else "",
        stream_count=len(streams),
        time_base=video_stream.get("time_base", ""),
    )
    log.info("Probed %s: %s, %.1fs, %s", video_path.name, meta.resolution,
             meta.duration_seconds, meta.video_codec)
    return meta
