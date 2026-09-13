"""Scrub-optimized preview media (proxies).

Long-GOP source video (a keyframe every 2-4 s) makes reverse shuttle and
high-speed scrubbing stutter: every seek must decode from the previous
keyframe - potentially hundreds of 1080p frames - before one is shown.

The fix, as in professional NLEs, is optimized preview media: a 720p proxy
with a keyframe every PROXY_GOP frames. Any seek then decodes at most
PROXY_GOP small frames, so frame delivery is fast and *consistent* in both
directions at any shuttle speed. The proxy is only ever used for preview;
exports always cut from the original file. Timelines are identical.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import subprocess
from pathlib import Path

from tapesift.services import ffmpeg_service

log = logging.getLogger(__name__)

PROXY_GOP = 15          # keyframe every 15 frames => worst-case seek decode
PROXY_HEIGHT = 720      # plenty for a preview pane, light to decode
PROXY_SUFFIX = ".preview.mp4"
PROXY_DIR_NAME = "Proxies"
# Hard cap on encode/decode threads. Without this FFmpeg grabs every core and
# starves the preview decoder - the proxy is meant to make playback smoother,
# so building it must never make playback worse.
PROXY_THREADS = 2


#: Where to sample when checking a finished proxy, as fractions of its
#: duration, and how many seconds to decode at each point. Corruption from a
#: hardware encoder is rarely at the very start - the file opens, reports a
#: correct duration, and only falls apart further in - so a header read is
#: not a check. Three short decodes cost a couple of seconds against an
#: encode that takes minutes.
VERIFY_POINTS = (0.10, 0.45, 0.80)
VERIFY_SECONDS = 4


def video_timestamps(ffmpeg_path: str, path: Path) -> list[float]:
    """Presentation timestamps relative to the container's playback origin.

    A clean decode does not reveal missing frames. Compare every video sample,
    retaining duplicates and real variable-rate gaps, before replacing a cache.
    """
    sibling = Path(ffmpeg_path).with_name(
        "ffprobe.exe" if os.name == "nt" else "ffprobe")
    probe = str(sibling) if sibling.is_file() else ffmpeg_service.find_executable("ffprobe")
    result = subprocess.run(
        [probe, "-v", "error", "-select_streams", "v:0", "-show_packets",
         "-show_entries", "packet=pts_time,flags:format=start_time", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    data = json.loads(result.stdout)
    origin = float(data.get("format", {}).get("start_time", 0))
    times = sorted(float(packet["pts_time"]) - origin
                   for packet in data["packets"] if "D" not in packet.get("flags", ""))
    if not times or not all(math.isfinite(value) for value in times):
        raise ValueError("Video has no usable presentation timestamps")
    return times


def verify_timing(expected: list[float], actual: list[float]) -> str:
    """Allow muxer rounding, but never lost/duplicated samples or retimed film."""
    if len(actual) != len(expected):
        return f"preview has {len(actual)} video samples; source has {len(expected)}"
    for index, (source_time, preview_time) in enumerate(zip(expected, actual)):
        if abs(source_time - preview_time) > 0.001:
            return f"preview timestamp differs from source at video sample {index}"
    return ""


def verify_playable(ffmpeg_path: str, path: Path, duration_ms: int) -> str:
    """Decode a few seconds at several points. Empty string means good.

    An encoder exiting 0 is not evidence that it produced playable video.
    A driver-level NVENC fault can write a full-length file of broken NAL
    units and still report success; the container parses, the duration is
    right, and playback stalls the moment a seek lands in the damage. The
    only reliable test is asking a decoder to read it.
    """
    import subprocess

    if not path.is_file() or path.stat().st_size == 0:
        return "proxy file is missing or empty"

    duration_s = max(0.0, duration_ms / 1000.0)
    offsets = [duration_s * point for point in VERIFY_POINTS] or [0.0]
    for offset in offsets:
        cmd = [
            ffmpeg_path, "-v", "error", "-nostdin",
            "-ss", f"{offset:.3f}", "-t", str(VERIFY_SECONDS),
            "-i", str(path), "-f", "null", "-",
        ]
        try:
            done = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError) as exc:
            return f"could not verify proxy: {exc}"
        if done.returncode != 0:
            return f"decoder rejected the proxy at {offset:.0f}s"
        # -v error keeps this quiet unless something genuinely failed to
        # decode, so any output at all is a real defect.
        noise = (done.stderr or "").strip()
        if noise:
            first = noise.splitlines()[0][:160]
            return f"decode errors at {offset:.0f}s: {first}"
    return ""


def proxy_path_for(source: Path, output_folder: Path) -> Path:
    """Deterministic proxy location for a source video.

    The name embeds size+mtime so an edited/replaced source video gets a
    fresh proxy instead of a stale one.
    """
    try:
        stat = source.stat()
        fingerprint = f"{source.resolve()}|{stat.st_size}|{int(stat.st_mtime)}"
    except OSError:
        fingerprint = str(source)
    digest = hashlib.sha1(fingerprint.encode("utf-8", "replace")).hexdigest()[:10]
    return output_folder / PROXY_DIR_NAME / f"{source.stem}_{digest}{PROXY_SUFFIX}"


def build_proxy_command(ffmpeg_path: str, source: Path, target: Path,
                        hw_encoder: str = "") -> list[str]:
    """FFmpeg command for a scrub-friendly proxy.

    Short GOP, no B-frames (decoder never reorders → snappier stepping),
    capped at 720p, faststart for instant seeking.
    """
    # -threads before -i limits DECODE threads; after, ENCODE threads.
    cmd = [ffmpeg_path, "-hide_banner", "-y",
           "-threads", str(PROXY_THREADS), "-i", str(source)]
    if hw_encoder == "h264_nvenc":
        video = ["-c:v", "h264_nvenc", "-preset", "p1", "-rc", "vbr",
                 "-cq", "30", "-b:v", "0"]
    elif hw_encoder == "h264_qsv":
        video = ["-c:v", "h264_qsv", "-global_quality", "30", "-preset",
                 "veryfast"]
    elif hw_encoder == "h264_amf":
        video = ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp",
                 "-qp_i", "28", "-qp_p", "30"]
    elif ffmpeg_service.software_h264(ffmpeg_path) == "libopenh264":
        # The bundled LGPL FFmpeg has no x264. openh264 takes no -preset,
        # -crf or -tune, so the proxy's quality target becomes a bitrate.
        # Measured at 1.51s per 30s of 720p vs x264 veryfast's 1.20s -
        # still far faster than real time, which is what the proxy needs.
        video = ["-c:v", "libopenh264", "-b:v", "2M"]
    else:
        video = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                 "-tune", "fastdecode"]
    cmd += video
    cmd += ["-map", "0:v:0", "-map", "0:a:0?",
            "-threads", str(PROXY_THREADS),
            "-g", str(PROXY_GOP), "-bf", "0",
            "-vf", f"scale=-2:'min({PROXY_HEIGHT},ih)'",
            "-pix_fmt", "yuv420p",
            "-fps_mode", "passthrough",
            "-enc_time_base:v", "demux",
            "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats",
            str(target)]
    return cmd


def find_ready_proxy(source: Path, output_folder: Path) -> Path | None:
    """The finished proxy for this exact source version, if one exists."""
    path = proxy_path_for(source, output_folder)
    if path.is_file() and path.stat().st_size > 0:
        return path
    return None


def cleanup_stale_proxies(source: Path, output_folder: Path) -> None:
    """Delete proxies of older versions of this source (size/mtime changed)."""
    current = proxy_path_for(source, output_folder)
    proxy_dir = output_folder / PROXY_DIR_NAME
    if not proxy_dir.is_dir():
        return
    for candidate in proxy_dir.glob(f"{source.stem}_*{PROXY_SUFFIX}"):
        if candidate != current:
            try:
                candidate.unlink()
                log.info("Removed stale proxy %s", candidate.name)
            except OSError:
                pass
