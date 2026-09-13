"""FFmpeg detection, encoder detection, and command building.

All commands are built as argument lists (never shell strings) and run
with shell=False. Times are passed to FFmpeg as seconds with millisecond
precision.
"""

from __future__ import annotations

import functools
from fractions import Fraction
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from tapesift.core.exceptions import ExportError
from tapesift.models.clip import Clip
from tapesift.models.export_settings import ExportPreset, ReelSettings

log = logging.getLogger(__name__)

# Hide console windows spawned by subprocesses on Windows.
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
# Background encodes run below everything else so the preview never starves.
IDLE_PRIORITY_CLASS = 0x00000040 if os.name == "nt" else 0

_COMMON_LOCATIONS = [
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    r"C:\Program Files (x86)\ffmpeg\bin",
    r"C:\tools\ffmpeg\bin",
]

HW_ENCODERS = {
    "h264_nvenc": "NVIDIA NVENC",
    "h264_qsv": "Intel Quick Sync",
    "h264_amf": "AMD AMF",
}


def _subprocess_kwargs() -> dict:
    return {
        "creationflags": CREATE_NO_WINDOW,
        "stdin": subprocess.DEVNULL,
    }


def bundled_dir() -> Path | None:
    """The FFmpeg shipped alongside TapeSift, if there is one.

    Installed builds carry their own FFmpeg so a new user never has to go
    find one - that download is the single biggest reason someone abandons
    a trial. It also means we know exactly which FFmpeg is running: version
    8.1.2 needs an NVIDIA driver >= 610 for NVENC, while 7.1 works on 591,
    and that difference is not something to leave to whatever a user
    happens to have on PATH.

    Checked in order: the PyInstaller bundle, a folder beside the
    executable, then vendor/ffmpeg for runs from source.
    """
    candidates = []
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.append(Path(meipass) / "ffmpeg")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "ffmpeg")
    candidates.append(Path(__file__).resolve().parents[2] / "vendor" / "ffmpeg")
    for folder in candidates:
        if folder.is_dir():
            return folder
    return None


def find_executable(name: str, configured_path: str = "") -> str:
    """Locate ffmpeg/ffprobe.

    Order: an explicit configured path (the user's override always wins) >
    the bundled copy > PATH > common install folders. Bundled deliberately
    beats PATH: a shipped build must behave the same on every machine, and
    on a developer's box it simply isn't there.
    """
    if configured_path:
        p = Path(configured_path)
        if p.is_file():
            return str(p)
    exe_name = f"{name}.exe" if os.name == "nt" else name
    bundled = bundled_dir()
    if bundled:
        candidate = bundled / exe_name
        if candidate.is_file():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    exe = f"{name}.exe" if os.name == "nt" else name
    for folder in _COMMON_LOCATIONS:
        candidate = Path(folder) / exe
        if candidate.is_file():
            return str(candidate)
    # WinGet-style install locations
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        base = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if base.is_dir():
            for candidate in base.glob(f"**/{exe}"):
                return str(candidate)
    return ""


@functools.lru_cache(maxsize=8)
def get_version(executable: str) -> str:
    """Return the tool's version line, or '' if it cannot run."""
    if not executable:
        return ""
    try:
        proc = subprocess.run(
            [executable, "-version"], capture_output=True, text=True, timeout=15,
            **_subprocess_kwargs(),
        )
        first_line = (proc.stdout or "").splitlines()
        return first_line[0] if first_line else ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("Could not run %s: %s", executable, exc)
        return ""


@functools.lru_cache(maxsize=4)
def available_encoders(ffmpeg_path: str) -> frozenset[str]:
    """Every encoder name this FFmpeg reports. Cached - it costs a process
    launch, and it is asked once per export command otherwise."""
    if not ffmpeg_path:
        return frozenset()
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"], capture_output=True,
            text=True, timeout=20, **_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    return frozenset(re.findall(r"^\s*\S{6}\s+(\S+)", proc.stdout or "",
                                re.MULTILINE))


def detect_hw_encoders(ffmpeg_path: str) -> list[str]:
    """Return names of hardware H.264 encoders this FFmpeg build reports."""
    if not ffmpeg_path:
        return []
    try:
        proc = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"], capture_output=True,
            text=True, timeout=20, **_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    available = []
    for encoder in HW_ENCODERS:
        if re.search(rf"\b{encoder}\b", proc.stdout or ""):
            available.append(encoder)
    return available


def ms_to_ffmpeg_time(ms: int) -> str:
    return f"{ms / 1000:.3f}"


def _scale_filter(preset: ExportPreset) -> str:
    """Build the -vf chain for scaling/cropping presets. '' if none needed."""
    filters: list[str] = []
    if preset.target_width and preset.target_height:
        w, h = preset.target_width, preset.target_height
        if preset.blurred_background:
            # Blurred pad background handled via filter_complex elsewhere; keep simple:
            filters.append(
                f"split[a][b];[a]scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},boxblur=20[bg];"
                f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
                f"[bg][fg]overlay=(W-w)/2:(H-h)/2"
            )
        elif preset.center_crop:
            filters.append(
                f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
            )
        else:
            filters.append(
                f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
            )
    elif preset.max_width and preset.max_height:
        filters.append(
            f"scale='min({preset.max_width},iw)':'min({preset.max_height},ih)':"
            f"force_original_aspect_ratio=decrease"
        )
    return ",".join(filters)


# Approximate CRF-equivalent bitrates at 1080p. libopenh264 has no CRF
# mode at all, so a quality target has to become a rate target; these are
# deliberately generous, since film review cares more about readable
# jersey numbers than about file size.
_CRF_TO_BITRATE = {18: "12M", 20: "9M", 23: "6M", 26: "3M"}


def _crf_as_bitrate(crf: int) -> str:
    nearest = min(_CRF_TO_BITRATE, key=lambda c: abs(c - crf))
    return _CRF_TO_BITRATE[nearest]


def software_h264(ffmpeg_path: str = "") -> str:
    """The best H.264 software encoder this FFmpeg actually has.

    The FFmpeg TapeSift ships is an LGPL build, which cannot include
    x264 - that is what makes it redistributable alongside software we
    sell. Asking it for libx264 fails outright with "Encoder not found",
    which would break every export and every scrub proxy. libopenh264 is
    the LGPL stand-in: measured on 720p test footage it encodes at 1.51s
    per 30s clip against x264 veryfast's 1.20s, so the proxy pipeline the
    transport depends on stays comfortably faster than real time.

    Substitution only happens on positive knowledge. If the encoder list
    can't be read at all, we keep libx264 rather than downgrade a machine
    that was working fine - an empty list means "couldn't ask", not
    "doesn't have it".
    """
    encoders = available_encoders(ffmpeg_path) if ffmpeg_path else frozenset()
    if encoders and "libx264" not in encoders and "libopenh264" in encoders:
        return "libopenh264"
    return "libx264"


def _encode_args(preset: ExportPreset, ffmpeg_path: str = "") -> list[str]:
    args: list[str] = []
    codec = preset.hardware_encoder or preset.video_codec
    if not preset.hardware_encoder and codec == "libx264":
        codec = software_h264(ffmpeg_path)
    openh264 = codec == "libopenh264"
    args += ["-c:v", codec]
    if preset.hardware_encoder:
        # Hardware encoders use bitrate/quality flags rather than CRF.
        args += ["-b:v", preset.video_bitrate or "8M"]
    elif preset.video_bitrate:
        args += ["-b:v", preset.video_bitrate]
    elif preset.crf is not None:
        args += (["-b:v", _crf_as_bitrate(preset.crf)] if openh264
                 else ["-crf", str(preset.crf)])
    # -preset is an x264 concept; libopenh264 rejects it.
    if not preset.hardware_encoder and preset.encoder_preset and not openh264:
        args += ["-preset", preset.encoder_preset]
    if preset.frame_rate:
        args += ["-r", f"{preset.frame_rate:g}"]
    if preset.pixel_format:
        args += ["-pix_fmt", preset.pixel_format]
    args += ["-c:a", preset.audio_codec]
    if preset.audio_bitrate:
        args += ["-b:a", preset.audio_bitrate]
    return args



@functools.lru_cache(maxsize=32)
def _probe_constant_frame_rate(ffprobe_path: str, source: str,
                               size: int, mtime_ns: int) -> str:
    # Size and mtime invalidate successful probes when a file is replaced.
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=avg_frame_rate,r_frame_rate", "-of", "json", source],
        capture_output=True, text=True, timeout=20, check=True,
        **_subprocess_kwargs())
    stream = json.loads(result.stdout)["streams"][0]
    if not all(isinstance(stream[key], str) for key in ("avg_frame_rate", "r_frame_rate")):
        raise ValueError("FFprobe returned non-text frame rates")
    average = Fraction(stream["avg_frame_rate"])
    nominal = Fraction(stream["r_frame_rate"])
    if average <= 0 or nominal <= 0:
        raise ValueError("The video has no positive frame rate")
    # Metadata agreement is only an eligibility check; leave irregular cadence alone.
    return f"{average.numerator}/{average.denominator}" if average == nominal else ""


def _source_frame_rate(ffmpeg_path: str, source: Path) -> str:
    try:
        source = source.resolve(strict=True)
        stat = source.stat()
        sibling = Path(ffmpeg_path).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        probe = str(sibling) if sibling.is_file() else find_executable("ffprobe")
        if not probe:
            raise ValueError("FFprobe is unavailable")
        return _probe_constant_frame_rate(probe, str(source), stat.st_size, stat.st_mtime_ns)
    except (OSError, subprocess.SubprocessError, ValueError, ZeroDivisionError, KeyError, IndexError, TypeError) as exc:
        raise ExportError(
            f"Could not determine the export source frame rate: {exc}",
            "Check that the source is readable and FFprobe is available beside FFmpeg.") from exc


def build_clip_command(ffmpeg_path: str, source: Path, output: Path, clip: Clip,
                       preset: ExportPreset, accurate: bool = True) -> list[str]:
    """Build the FFmpeg command to export one clip.

    Accurate cut: seek before input (fast) then re-encode with exact duration.
    Fast cut: stream copy; keyframe-bound, may be slightly off.
    """
    start = ms_to_ffmpeg_time(clip.start_ms)
    duration = ms_to_ffmpeg_time(clip.duration_ms)
    cmd = [ffmpeg_path, "-hide_banner", "-y", "-ss", start, "-i", str(source),
           "-t", duration]

    if preset.stream_copy or not accurate:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]
    else:
        vf = _scale_filter(preset)
        source_rate = "" if preset.frame_rate else _source_frame_rate(ffmpeg_path, source)
        if preset.frame_rate or source_rate:
            # A subframe input seek otherwise offsets output PTS and drops the final picture.
            vf = ",".join(filter(None, ("setpts=PTS-STARTPTS", vf)))
            if source_rate:
                cmd += ["-r", source_rate]
        if vf:
            cmd += ["-vf", vf]
        cmd += _encode_args(preset, ffmpeg_path)
    cmd += ["-progress", "pipe:1", "-nostats", str(output)]
    return cmd


def build_composited_command(
        ffmpeg_path: str, audio_wav: Path, output: Path, *,
        width: int, height: int, frame_rate: int,
        preset: ExportPreset) -> list[str]:
    """Encode compositor-owned RGBA frames and mux canonical Voiceover WAV.

    The Python compositor already owns the exact 16:9 or 9:16 canvas. Legacy
    scaling/cropping flags must therefore never be applied a second time.
    Raw frames arrive on stdin; progress remains on stdout for the queue.
    """
    for label, value in (
        ("width", width), ("height", height), ("frame rate", frame_rate),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"Composited {label} must be a positive integer")
    if preset.stream_copy:
        raise ValueError("Composited exports cannot use stream copy")
    if preset.target_width or preset.target_height \
            or preset.center_crop or preset.blurred_background:
        raise ValueError(
            "Composited exports cannot use a legacy canvas transform preset"
        )
    if preset.frame_rate and abs(preset.frame_rate - frame_rate) > 1e-9:
        raise ValueError(
            "Technical preset frame rate differs from the composition clock"
        )

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-y",
        "-f", "rawvideo",
        "-pixel_format", "rgba",
        "-video_size", f"{width}x{height}",
        "-framerate", str(frame_rate),
        "-i", "pipe:0",
        "-i", str(audio_wav),
        "-map", "0:v:0",
        "-map", "1:a:0",
    ]
    if not preset.frame_rate:
        cmd += ["-r", str(frame_rate)]
    cmd += _encode_args(preset, ffmpeg_path)
    cmd += [
        "-shortest",
        "-movflags", "+faststart",
        "-progress", "pipe:1",
        "-nostats",
        str(output),
    ]
    return cmd


def build_concat_command(ffmpeg_path: str, list_file: Path, output: Path) -> list[str]:
    """Concatenate pre-cut clip files (same encoding) via the concat demuxer."""
    return [ffmpeg_path, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file), "-c", "copy",
            "-progress", "pipe:1", "-nostats", str(output)]


def build_reel_reencode_command(ffmpeg_path: str, list_file: Path, output: Path,
                                preset: ExportPreset,
                                reel: ReelSettings | None = None,
                                total_duration_s: float = 0.0) -> list[str]:
    """Concatenate with re-encode (needed for fades or mixed inputs)."""
    cmd = [ffmpeg_path, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
           "-i", str(list_file)]
    filters: list[str] = []
    vf = _scale_filter(preset)
    if vf:
        filters.append(vf)
    if reel:
        if reel.fade_in:
            filters.append(f"fade=t=in:st=0:d={reel.fade_duration:g}")
        if reel.fade_out and total_duration_s > reel.fade_duration:
            start = max(0.0, total_duration_s - reel.fade_duration)
            filters.append(f"fade=t=out:st={start:.3f}:d={reel.fade_duration:g}")
    if filters:
        cmd += ["-vf", ",".join(filters)]
    cmd += _encode_args(preset, ffmpeg_path)
    cmd += ["-progress", "pipe:1", "-nostats", str(output)]
    return cmd


def build_thumbnail_command(ffmpeg_path: str, source: Path, output: Path,
                            time_ms: int, width: int = 320) -> list[str]:
    return [ffmpeg_path, "-hide_banner", "-y",
            "-ss", ms_to_ffmpeg_time(time_ms), "-i", str(source),
            "-frames:v", "1", "-update", "1",
            "-vf", f"scale={width}:-1", str(output)]


_PROGRESS_RE = re.compile(r"out_time_us=(\d+)")


def parse_progress_line(line: str) -> int | None:
    """Extract processed microseconds from an FFmpeg -progress line, or None."""
    m = _PROGRESS_RE.search(line)
    if m:
        return int(m.group(1))
    return None


def command_to_display_string(cmd: list[str]) -> str:
    """Human-readable command for logs/queue UI (not for execution)."""
    return " ".join(f'"{a}"' if " " in a else a for a in cmd)
