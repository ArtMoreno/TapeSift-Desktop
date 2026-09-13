"""Synthetic media checks for per-play exports; no analyst film is opened."""

import os
from pathlib import Path
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from tapesift.core.exceptions import ExportCancelledError, ExportError
from tapesift.models.clip import Clip
from tapesift.models.project import Project
from tapesift.services import ffmpeg_service, ffprobe_service
from tapesift.services.export_service import FFmpegRunner
from tapesift.services.play_summary_export import export_summary_video, save_summary_image


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _run(cmd):
    result = subprocess.run(cmd, capture_output=True, timeout=45,
                            creationflags=ffmpeg_service.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    return result.stdout


@pytest.mark.parametrize("audio", [False, True])
def test_card_then_exact_selected_film_preserves_audio_and_destination(tmp_path, audio):
    ffmpeg = ffmpeg_service.find_executable("ffmpeg")
    ffprobe = ffmpeg_service.find_executable("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg fixture tools unavailable")
    source = tmp_path / "source.mkv"
    cmd = [ffmpeg, "-v", "error", "-y"]
    for color in ("red", "green", "blue"):
        cmd += ["-f", "lavfi", "-i", f"color={color}:s=160x90:r=30:d=1"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:duration=3"]
    cmd += ["-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]"]
    if audio:
        cmd += ["-map", "3:a", "-c:a", "pcm_s16le"]
    cmd += ["-c:v", "ffv1", str(source)]
    _run(cmd)
    original = source.read_bytes()
    project = Project("Fixture", source_video_path=str(source))
    clip = Clip(1000, 2000)
    card = QImage(160, 90, QImage.Format.Format_RGB32)
    card.fill(QColor("yellow"))
    output = tmp_path / "result.mp4"
    values = []
    export_summary_video(ffmpeg, project, clip, card, output, FFmpegRunner(), values.append)
    meta = ffprobe_service.probe_video(ffprobe, output)
    assert abs(meta.duration_ms - 3000) <= 80
    assert bool(meta.audio_codec) is audio
    assert values[-1] == 100
    for second, expected in ((0.5, "yellow"), (2.5, "green")):
        raw = _run([ffmpeg, "-v", "error", "-ss", str(second), "-i", str(output),
                    "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
        r, g, b = raw[:3]
        if expected == "yellow":
            assert r > 200 and g > 200 and b < 40
        else:
            assert g > 70 and r < 40 and b < 40
    if audio:
        silence = _run([ffmpeg, "-v", "error", "-ss", "0.5", "-i", str(output),
                        "-t", "0.1", "-f", "s16le", "-ac", "1", "pipe:1"])
        assert silence and set(silence) == {0}
        sound = _run([ffmpeg, "-v", "error", "-ss", "2.5", "-i", str(output),
                      "-t", "0.1", "-f", "s16le", "-ac", "1", "pipe:1"])
        assert any(sound)
    assert source.read_bytes() == original
    cancelled = FFmpegRunner()
    cancelled.cancel()
    existing = output.read_bytes()
    with pytest.raises(ExportCancelledError):
        export_summary_video(ffmpeg, project, clip, card, output, cancelled)
    assert output.read_bytes() == existing
    running = FFmpegRunner()
    with pytest.raises(ExportCancelledError):
        export_summary_video(ffmpeg, project, clip, card, output, running,
                             lambda value: running.cancel() if value > 0 else None)
    assert output.read_bytes() == existing
    with pytest.raises(ExportError):
        export_summary_video(ffmpeg, project, clip, card, source, FFmpegRunner())
    assert not list(tmp_path.glob("tapesift-play-*"))


def test_export_preview_is_a_saved_snapshot_and_png_matches_preview(qapp, tmp_path):
    from tapesift.ui_v3.play_export_dialog import PlayExportDialog
    clip = Clip(0, 1000, clip_title="Q1 Pass", details={"quarter": "Q1"})
    project = Project("Fixture")
    field = QImage(600, 140, QImage.Format.Format_RGB32)
    field.fill(QColor("#214c2f"))
    dialog = PlayExportDialog(clip, project, field)
    clip.details["quarter"] = "Q4"
    clip.clip_title = "Changed after preview"
    project.name = "Changed project"
    assert dialog.clip.details["quarter"] == "Q1"
    assert dialog.clip.clip_title == "Q1 Pass"
    assert dialog.project.name == "Fixture"
    image_path = tmp_path / "summary.png"
    save_summary_image(dialog.summary_image, image_path)
    assert QImage(str(image_path)) == dialog.summary_image
    with pytest.raises(ExportError):
        save_summary_image(QImage(), image_path)
    assert QImage(str(image_path)) == dialog.summary_image
    requested = []
    dialog.video_only_requested.connect(lambda: requested.append(True))
    dialog.video_button.click()
    assert requested == [True]
    dialog.close()
