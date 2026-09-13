"""A proxy is only 'ready' if a decoder can read it.

The bug this covers: an NVENC fault produced a full-length 467 MB proxy of
broken NAL units. FFmpeg exited 0, the worker promoted the file, the
container parsed and reported the correct duration - and playback froze on
every seek that landed in the damage, with nothing in the log to explain it.
Exit code alone is not evidence of playable video.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tapesift.services import proxy_service


def _ffmpeg() -> str | None:
    from tapesift.services.ffmpeg_service import find_executable
    return find_executable("ffmpeg")


ffmpeg_required = pytest.mark.skipif(
    _ffmpeg() is None, reason="needs an ffmpeg binary")


@pytest.fixture(scope="module")
def good_clip(tmp_path_factory) -> Path:
    """Six seconds of synthetic video that genuinely decodes."""
    out = tmp_path_factory.mktemp("proxy_verify") / "good.mp4"
    subprocess.run(
        [_ffmpeg(), "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=6",
         "-c:v", "mpeg4", "-b:v", "300k", "-pix_fmt", "yuv420p",
         str(out)],
        check=True, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return out


@ffmpeg_required
def test_a_real_encode_passes(good_clip):
    assert proxy_service.verify_playable(_ffmpeg(), good_clip, 6_000) == ""


@ffmpeg_required
def test_preview_preserves_irregular_frames_and_nonzero_start(tmp_path):
    source, target = tmp_path / "source.mp4", tmp_path / "preview.mp4"
    kwargs = dict(check=True, capture_output=True,
                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    subprocess.run([
        _ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i",
        "testsrc2=size=96x64:rate=25:duration=0.6", "-vf",
        r"settb=1/1000,setpts=N*40+mod(N\,3)*7+5000", "-c:v", "mpeg4",
        "-b:v", "300k", "-fps_mode", "passthrough", "-enc_time_base:v", "1:1000",
        str(source)], **kwargs)
    subprocess.run(proxy_service.build_proxy_command(_ffmpeg(), source, target), **kwargs)
    expected = proxy_service.video_timestamps(_ffmpeg(), source)
    actual = proxy_service.video_timestamps(_ffmpeg(), target)
    assert len(expected) >= 10
    assert proxy_service.verify_timing(expected, actual) == ""


@ffmpeg_required
def test_missing_file_is_rejected(tmp_path):
    problem = proxy_service.verify_playable(
        _ffmpeg(), tmp_path / "nope.mp4", 6_000)
    assert "missing" in problem


@ffmpeg_required
def test_empty_file_is_rejected(tmp_path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    assert "empty" in proxy_service.verify_playable(_ffmpeg(), empty, 6_000)


@ffmpeg_required
def test_corrupt_payload_is_rejected(good_clip, tmp_path):
    """The shape of the real failure: valid container, damaged frame data.

    The header and duration survive - which is exactly why a probe that only
    reads metadata called the broken 467 MB file healthy - so the corruption
    has to be found by decoding.
    """
    broken = tmp_path / "broken.mp4"
    data = bytearray(good_clip.read_bytes())
    # Leave the leading boxes intact and shred the middle, where the frames
    # live. Deterministic, not random, so a failure is reproducible.
    start = int(len(data) * 0.35)
    end = int(len(data) * 0.85)
    for index in range(start, end, 7):
        data[index] = (data[index] + 0x5A) & 0xFF
    broken.write_bytes(bytes(data))

    problem = proxy_service.verify_playable(_ffmpeg(), broken, 6_000)
    assert problem, "corrupt payload must not be reported as playable"
    assert "decode" in problem or "rejected" in problem


@ffmpeg_required
def test_truncated_file_is_rejected(good_clip, tmp_path):
    """A half-written proxy - the other way an encode 'succeeds'."""
    cut = tmp_path / "cut.mp4"
    data = good_clip.read_bytes()
    cut.write_bytes(data[: len(data) // 3])
    assert proxy_service.verify_playable(_ffmpeg(), cut, 6_000) != ""
