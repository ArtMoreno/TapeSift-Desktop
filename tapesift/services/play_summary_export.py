"""Export a saved play snapshot and its measured field without changing the clip."""

from __future__ import annotations

from pathlib import Path
import tempfile

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter

from tapesift.core.exceptions import ExportCancelledError, ExportError
from tapesift.models.clip import Clip
from tapesift.models.export_settings import SOURCE_QUALITY
from tapesift.models.project import Project
from tapesift.services import ffmpeg_service, ffprobe_service
from tapesift.services.export_service import FFmpegRunner, export_clip


def render_summary(clip: Clip, project: Project, field_image: QImage) -> QImage:
    """Render on the GUI thread; PNG and video use this same frozen image."""
    image = QImage(1280, 720, QImage.Format.Format_RGB32)
    image.fill(QColor("#080c0a"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#9caea2"))
    painter.setFont(QFont("IBM Plex Sans", 15))
    painter.drawText(QRectF(48, 28, 1184, 30), "TapeSift · Play summary")
    painter.setPen(QColor("#efeee5"))
    painter.setFont(QFont("IBM Plex Sans", 24, QFont.Weight.DemiBold))
    title = clip.clip_title or f"Play {clip.clip_number or clip.order_index + 1:03d}"
    painter.drawText(QRectF(48, 76, 1184, 88),
                     painter.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, 1184))
    painter.setFont(QFont("IBM Plex Sans", 15))
    details = clip.details
    situation = "  ·  ".join(value for value in (
        details.get("quarter", ""), details.get("down_distance", ""),
        f"Ball on {details['ball_on']}" if details.get("ball_on") else "",
        f"Gain {details['yards']} yd" if details.get("yards") else "",
        f"YAC {details['yac']} yd" if details.get("yac") else "",
    ) if value)
    painter.drawText(QRectF(48, 172, 1184, 58), Qt.TextFlag.TextWordWrap, situation)
    if not field_image.isNull():
        fitted = field_image.scaled(1184, 310, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.SmoothTransformation)
        painter.drawImage((1280 - fitted.width()) // 2,
                          244 + (310 - fitted.height()) // 2, fitted)
    painter.setPen(QColor("#7edca0"))
    painter.drawText(QRectF(48, 570, 1184, 56), painter.fontMetrics().elidedText(
        details.get("result", ""), Qt.TextElideMode.ElideRight, 1184))
    painter.setPen(QColor("#c2c9c3"))
    primary = details.get("player_name", "")
    if primary in (details.get("quarterback"), details.get("receiver_name"), details.get("ball_carrier")):
        primary = ""
    people = "  ·  ".join(value for value in (
        f"QB {details['quarterback']}" if details.get("quarterback") else "",
        f"Primary {primary}" if primary else "",
        f"Receiver {details['receiver_name']}" if details.get("receiver_name") else "",
        f"Carrier {details['ball_carrier']}" if details.get("ball_carrier") else "",
    ) if value)
    painter.drawText(QRectF(48, 628, 1184, 28), painter.fontMetrics().elidedText(
        people, Qt.TextElideMode.ElideRight, 1184))
    if details.get("other_players"):
        painter.drawText(QRectF(48, 660, 1184, 28), painter.fontMetrics().elidedText(
            "Also involved: " + details["other_players"], Qt.TextElideMode.ElideRight, 1184))
    painter.end()
    return image


def save_summary_image(image: QImage, output: Path) -> None:
    """Leave an existing destination intact if PNG encoding fails."""
    if image.isNull():
        raise ExportError("The field summary image is empty.")
    with tempfile.TemporaryDirectory(prefix="tapesift-summary-", dir=output.parent) as folder:
        temporary = Path(folder) / "summary.png"
        if not image.save(str(temporary), "PNG"):
            raise ExportError("Could not save the field summary image.")
        temporary.replace(output)


def export_summary_video(ffmpeg_path: str, project: Project, clip: Clip,
                         image: QImage, output: Path, runner: FFmpegRunner,
                         on_progress=None) -> None:
    """Prepend a two-second card to an accurately cut clip, retaining audio."""
    if clip.start_ms < 0 or clip.duration_ms <= 0:
        raise ExportError("The selected play has invalid film boundaries.")
    if Path(project.source_video_path).resolve() == output.resolve():
        raise ExportError("Choose an export filename different from the source video.")
    if runner.cancel_event.is_set():
        raise ExportCancelledError("Export was cancelled.")
    ffprobe = Path(ffmpeg_path).with_name("ffprobe" + Path(ffmpeg_path).suffix)
    probe_path = str(ffprobe) if ffprobe.is_file() else ffmpeg_service.find_executable("ffprobe")
    with tempfile.TemporaryDirectory(prefix="tapesift-play-", dir=output.parent) as folder:
        temporary = Path(folder)
        card = temporary / "summary.png"
        if image.isNull() or not image.save(str(card), "PNG"):
            raise ExportError("Could not prepare the field summary image.")
        cut = temporary / "play.mp4"
        export_clip(ffmpeg_path, project, clip, cut, SOURCE_QUALITY, True, runner,
                    (lambda pct: on_progress(pct * .45)) if on_progress else None)
        metadata = ffprobe_service.probe_video(probe_path, cut)
        if runner.cancel_event.is_set():
            raise ExportCancelledError("Export was cancelled.")
        width, height = metadata.width, metadata.height
        fps = metadata.frame_rate
        if width <= 0 or height <= 0 or fps <= 0:
            raise ExportError("The cut play has no usable video dimensions or frame rate.")
        if abs(metadata.duration_ms - clip.duration_ms) > max(100, 2000 / fps):
            raise ExportError("The source could not provide the entire selected play. Check its boundaries.")
        partial = temporary / "summary-play.mp4"
        duration = ffmpeg_service.ms_to_ffmpeg_time(clip.duration_ms)
        cmd = [ffmpeg_path, "-hide_banner", "-y", "-loop", "1", "-framerate", f"{fps:g}",
               "-t", "2", "-i", str(card), "-i", str(cut)]
        filters = [
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x080c0a,"
            f"setsar=1,fps={fps:g},trim=duration=2,setpts=PTS-STARTPTS[card]",
            f"[1:v]setsar=1,fps={fps:g},trim=duration={duration},setpts=PTS-STARTPTS[film]",
        ]
        if metadata.audio_codec:
            cmd += ["-f", "lavfi", "-t", "2", "-i", "anullsrc=r=48000:cl=stereo"]
            filters += [
                "[2:a]asetpts=PTS-STARTPTS[silence]",
                f"[1:a]aresample=48000,aformat=channel_layouts=stereo,apad,"
                f"atrim=duration={duration},asetpts=PTS-STARTPTS[audio]",
                "[card][silence][film][audio]concat=n=2:v=1:a=1[v][a]",
            ]
        else:
            filters += ["[card][film]concat=n=2:v=1:a=0[v]"]
        cmd += ["-filter_complex", ";".join(filters), "-map", "[v]"]
        if metadata.audio_codec:
            cmd += ["-map", "[a]"]
        cmd += ffmpeg_service._encode_args(SOURCE_QUALITY, ffmpeg_path)
        cmd += ["-t", ffmpeg_service.ms_to_ffmpeg_time(clip.duration_ms + 2000),
                "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(partial)]
        runner.run(cmd, clip.duration_ms + 2000,
                   (lambda pct: on_progress(45 + pct * .55)) if on_progress else None)
        if runner.cancel_event.is_set():
            raise ExportCancelledError("Export was cancelled.")
        partial.replace(output)
