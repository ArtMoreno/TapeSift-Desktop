"""Export planning and execution (Qt-free; workers wrap this with signals).

Individual clips are cut straight from the source. Combined reels are built by
cutting each segment to a temp file with identical encoding, then concatenating
with the concat demuxer.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from tapesift.core import paths as core_paths
from tapesift.core.exceptions import ExportCancelledError, ExportError, OutputFolderError
from tapesift.models.clip import Clip
from tapesift.models.export_job import ExportJob, JobStatus, JobType
from tapesift.models.export_settings import ExportPreset, ReelSettings, get_preset
from tapesift.models.project import Project
from tapesift.services import background_service, ffmpeg_service, filename_service

log = logging.getLogger(__name__)

ProgressCallback = Callable[[float], None]  # 0..100

LOW_DISK_MARGIN_BYTES = 500 * 1024 * 1024


@dataclass
class ExportPlan:
    jobs: list[ExportJob] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    estimated_bytes: int = 0


def _output_dir_for_clip(project: Project, clip: Clip, kind: str) -> Path:
    root = Path(project.output_folder)
    organization = project.output_organization
    if organization == "flat":
        return root
    if "{" in organization and clip is not None:
        # Metadata folder template, e.g. "{player}/{quarter}/{down_distance}"
        relative = filename_service.render_folder_template(
            organization, clip, project.name, project.default_preset)
        return root / relative if relative else root
    if organization == "by_label" and clip is not None:
        return root / (filename_service.sanitize_filename_base(clip.label) or "Unlabeled")
    if organization == "by_tag" and clip is not None:
        return root / (filename_service.sanitize_filename_base(clip.tags[0])
                       if clip.tags and filename_service.sanitize_filename_base(clip.tags[0]) else "Untagged")
    if organization == "by_preset" and clip is not None:
        return root / (filename_service.sanitize_filename_base(
            clip.export_preset or project.default_preset) or "Unspecified")
    structure = core_paths.project_output_structure(root)
    return structure["reels" if kind == "reel" else "individual"]


def _validate_output_directory(root: Path, candidate: Path, *, prepare: bool) -> None:
    try:
        # A chosen root may itself be a junction. Inner folders must stay in it.
        candidate.resolve().relative_to(root.resolve())
        if prepare:
            candidate.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError, RuntimeError) as exc:
        raise OutputFolderError(
            f"Cannot use output folder {candidate}: {exc}",
            "Choose a folder inside the export destination.") from exc


def estimate_clip_bytes(clip: Clip, source_bytes: int, source_duration_ms: int,
                        preset: ExportPreset) -> int:
    """Rough size estimate: proportional slice of the source file."""
    if source_duration_ms <= 0 or source_bytes <= 0:
        return 0
    fraction = clip.duration_ms / source_duration_ms
    estimate = int(source_bytes * fraction)
    if not preset.stream_copy:
        estimate = int(estimate * 1.2)  # re-encode may grow slightly at CRF 18
    return estimate


def plan_export(project: Project, clips: list[Clip], mode: str,
                settings_preset: str = "", accurate: bool | None = None,
                separator_style: str = "hyphen", *, prepare: bool = True) -> ExportPlan:
    """Build the job list for an export run.

    mode: "individual" | "reel" | "both"
    """
    plan = ExportPlan()
    root = Path(project.output_folder)
    if mode not in {"individual", "reel", "both"}:
        raise ExportError(f"Unknown export mode: {mode}")
    if not str(project.output_folder).strip():
        raise OutputFolderError("Choose an output folder before exporting.")
    if prepare:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OutputFolderError(
                f"Cannot create the output folder {root}: {exc}",
                "Choose a different output folder in project settings.",
            )
        if not _folder_writable(root):
            raise OutputFolderError(
                f"The output folder {root} is not writable.",
                "Check permissions or choose a different folder.",
            )

    enabled = [c for c in clips if c.enabled]
    if not enabled:
        plan.warnings.append("No enabled clips to export.")
        return plan

    taken: set[str] = set()
    source_bytes = project.source_metadata.file_size_bytes

    if mode in ("individual", "both"):
        for clip in enabled:
            preset = get_preset(settings_preset or clip.export_preset or project.default_preset)
            out_dir = _output_dir_for_clip(project, clip, "clip")
            _validate_output_directory(root, out_dir, prepare=prepare)
            base = filename_service.render_template(
                project.naming_template, clip, project.name, separator_style)
            out_path = filename_service.unique_path(out_dir, base, ".mp4", taken)
            job = ExportJob(
                job_type=JobType.CLIP,
                display_name=clip.clip_title or base,
                output_path=str(out_path),
                project_id=project.id,
                clip_id=clip.id,
                preset_name=preset.name,
            )
            plan.jobs.append(job)
            plan.estimated_bytes += estimate_clip_bytes(
                clip, source_bytes, project.source_duration_ms, preset)

    if mode in ("reel", "both"):
        reel_clips = [c for c in enabled if c.include_in_reel]
        if reel_clips:
            preset = get_preset(settings_preset or project.default_preset)
            out_dir = _output_dir_for_clip(project, reel_clips[0], "reel") \
                if project.output_organization == "flat" else \
                core_paths.project_output_structure(root)["reels"]
            _validate_output_directory(root, out_dir, prepare=prepare)
            base = filename_service.sanitize_filename_base(f"{project.name} Reel",
                                                           separator_style) or "Combined-Reel"
            out_path = filename_service.unique_path(out_dir, base, ".mp4", taken)
            job = ExportJob(
                job_type=JobType.REEL,
                display_name=f"Combined reel ({len(reel_clips)} clips)",
                output_path=str(out_path),
                project_id=project.id,
                clip_ids=[c.id for c in reel_clips],
                preset_name=preset.name,
            )
            plan.jobs.append(job)
            for clip in reel_clips:
                plan.estimated_bytes += estimate_clip_bytes(
                    clip, source_bytes, project.source_duration_ms, preset)
        else:
            plan.warnings.append("No clips are marked for the combined reel.")

    free = shutil.disk_usage(root).free if prepare else 0
    if prepare and plan.estimated_bytes and free < plan.estimated_bytes + LOW_DISK_MARGIN_BYTES:
        plan.warnings.append(
            f"Low disk space: export may need ~{plan.estimated_bytes // (1024 * 1024)} MB "
            f"but only {free // (1024 * 1024)} MB is free on the output drive."
        )
    return plan


def _folder_writable(folder: Path) -> bool:
    try:
        with tempfile.TemporaryFile(dir=folder):
            pass
        return True
    except OSError:
        return False


class FFmpegRunner:
    """Runs one FFmpeg command with progress reporting and cancellation."""

    def __init__(self, cancel_event: threading.Event | None = None,
                 low_priority: bool = False) -> None:
        self.cancel_event = cancel_event or threading.Event()
        self._proc: subprocess.Popen | None = None
        # Background work (proxies, thumbnails) must never outrank the video
        # preview: idle priority means it only gets otherwise-unused cycles.
        self.low_priority = low_priority

    def cancel(self) -> None:
        self.cancel_event.set()
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    def run(self, cmd: list[str], total_duration_ms: int,
            on_progress: ProgressCallback | None = None) -> None:
        log.info("Running: %s", ffmpeg_service.command_to_display_string(cmd))
        flags = ffmpeg_service.CREATE_NO_WINDOW
        if self.low_priority:
            flags |= ffmpeg_service.IDLE_PRIORITY_CLASS
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8", errors="replace",
                creationflags=flags,
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ExportError(
                f"Could not start FFmpeg: {exc}",
                "Check the FFmpeg path in Settings > FFmpeg.",
            )
        # Background work is suspended while the user is playing/scrubbing.
        if self.low_priority:
            background_service.register(self._proc.pid)
        assert self._proc.stdout is not None

        # Drain stderr on a thread: Windows pipe buffers are tiny (~4 KB) and
        # FFmpeg blocks (deadlocking us) if stderr fills while we read stdout.
        stderr_chunks: list[str] = []

        def _drain_stderr(stream) -> None:
            for err_line in stream:
                stderr_chunks.append(err_line)

        stderr_thread = None
        if self._proc.stderr is not None:
            stderr_thread = threading.Thread(
                target=_drain_stderr, args=(self._proc.stderr,), daemon=True)
            stderr_thread.start()

        for line in self._proc.stdout:
            if self.cancel_event.is_set():
                self._proc.terminate()
                break
            us = ffmpeg_service.parse_progress_line(line)
            if us is not None and total_duration_ms > 0 and on_progress:
                pct = min(100.0, (us / 1000) / total_duration_ms * 100)
                on_progress(pct)
        returncode = self._proc.wait()
        if self.low_priority:
            background_service.unregister(self._proc.pid)
        if stderr_thread is not None:
            stderr_thread.join(timeout=10)
        stderr_output = "".join(stderr_chunks)

        if self.cancel_event.is_set():
            raise ExportCancelledError("Export was cancelled.")
        if returncode != 0:
            tail = "\n".join(stderr_output.strip().splitlines()[-8:])
            raise ExportError(
                f"FFmpeg exited with an error (code {returncode}).\n{tail}",
                "See the log file for the full command and output.",
            )
        if on_progress:
            on_progress(100.0)


def export_clip(ffmpeg_path: str, project: Project, clip: Clip, output_path: Path,
                preset: ExportPreset, accurate: bool, runner: FFmpegRunner,
                on_progress: ProgressCallback | None = None) -> str:
    """Export a single clip. Returns the display command string used."""
    source = Path(project.source_video_path)
    if not source.is_file():
        raise ExportError(
            f"The source video is missing: {source}",
            "Use Relink Source to point the project at the file's new location.",
        )
    tmp_path = output_path.with_name(output_path.stem + ".part" + output_path.suffix)
    cmd = ffmpeg_service.build_clip_command(
        ffmpeg_path, source, tmp_path, clip, preset, accurate)
    try:
        runner.run(cmd, clip.duration_ms, on_progress)
        tmp_path.replace(output_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return ffmpeg_service.command_to_display_string(cmd)


def export_multi_source_reel(
    ffmpeg_path: str, items: list[tuple[Path, Clip]], output_path: Path,
    preset: ExportPreset, runner: FFmpegRunner,
    on_progress: ProgressCallback | None = None,
) -> str:
    """Concatenate clips that come from DIFFERENT source videos.

    This is what turns a Library selection into one tape - e.g. every clip
    of a player across a whole season. Each segment is cut with identical
    encoding settings (required for concat) and then joined in the given
    order.
    """
    missing = [src for src, _ in items if not Path(src).is_file()]
    if missing:
        raise ExportError(
            f"{len(missing)} of the selected clips have a missing source "
            "video.",
            "Relink those projects, or deselect the affected clips.")
    if not items:
        raise ExportError("No clips were selected for the reel.")

    total_ms = sum(clip.duration_ms for _, clip in items) or 1
    display_cmd = ""
    with tempfile.TemporaryDirectory(prefix="tapesift_reel_") as tmp:
        tmp_dir = Path(tmp)
        segments: list[Path] = []
        done_ms = 0
        for index, (source, clip) in enumerate(items):
            if runner.cancel_event.is_set():
                raise ExportCancelledError("Export was cancelled.")
            seg = tmp_dir / f"seg_{index:04d}.mp4"
            # Concat needs uniform encoding, so segments always re-encode.
            cmd = ffmpeg_service.build_clip_command(
                ffmpeg_path, Path(source), seg, clip, preset, accurate=True)
            display_cmd = ffmpeg_service.command_to_display_string(cmd)

            def seg_progress(pct: float, _done=done_ms,
                             _len=clip.duration_ms) -> None:
                if on_progress:
                    on_progress(((_done + _len * pct / 100) / total_ms) * 90)

            runner.run(cmd, clip.duration_ms, seg_progress)
            segments.append(seg)
            done_ms += clip.duration_ms

        list_file = tmp_dir / "concat.txt"
        list_file.write_text(
            "\n".join(f"file '{str(s).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"
                      for s in segments), encoding="utf-8")
        tmp_out = tmp_dir / ("reel_out" + output_path.suffix)
        concat_cmd = [ffmpeg_path, "-hide_banner", "-y", "-f", "concat",
                      "-safe", "0", "-i", str(list_file), "-c", "copy",
                      "-progress", "pipe:1", "-nostats", str(tmp_out)]
        runner.run(concat_cmd, total_ms,
                   lambda pct: on_progress(90 + pct * 0.1) if on_progress else None)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_out), str(output_path))
    if on_progress:
        on_progress(100.0)
    return display_cmd


def export_reel(ffmpeg_path: str, project: Project, clips: list[Clip],
                output_path: Path, preset: ExportPreset, accurate: bool,
                runner: FFmpegRunner, reel_settings: ReelSettings | None = None,
                on_progress: ProgressCallback | None = None) -> str:
    """Export a combined reel: cut segments uniformly, then concatenate."""
    source = Path(project.source_video_path)
    if not source.is_file():
        raise ExportError(
            f"The source video is missing: {source}",
            "Use Relink Source to point the project at the file's new location.",
        )
    reel_settings = reel_settings or ReelSettings()
    total_ms = sum(c.duration_ms for c in clips)
    display_cmd = ""

    with tempfile.TemporaryDirectory(prefix="tapesift_reel_") as tmp:
        tmp_dir = Path(tmp)
        segment_paths: list[Path] = []
        done_ms = 0

        # Segment cutting gets 90% of the progress bar; concat the rest.
        for index, clip in enumerate(clips):
            if runner.cancel_event.is_set():
                raise ExportCancelledError("Export was cancelled.")
            seg = tmp_dir / f"seg_{index:04d}.mp4"
            # Segments must share encoding for concat, so reels always re-encode
            # unless the user chose Fast Copy explicitly.
            cmd = ffmpeg_service.build_clip_command(
                ffmpeg_path, source, seg, clip, preset,
                accurate=not preset.stream_copy)

            def seg_progress(pct: float, _done=done_ms, _len=clip.duration_ms) -> None:
                if on_progress and total_ms:
                    overall = ((_done + (_len * pct / 100)) / total_ms) * 90
                    on_progress(overall)

            runner.run(cmd, clip.duration_ms, seg_progress)
            segment_paths.append(seg)
            done_ms += clip.duration_ms

        list_file = tmp_dir / "concat.txt"
        lines = []
        for seg in segment_paths:
            escaped = str(seg).replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        list_file.write_text("\n".join(lines), encoding="utf-8")

        tmp_out = tmp_dir / ("reel_out" + output_path.suffix)
        needs_reencode = reel_settings.fade_in or reel_settings.fade_out
        if needs_reencode:
            cmd = ffmpeg_service.build_reel_reencode_command(
                ffmpeg_path, list_file, tmp_out, preset, reel_settings,
                total_duration_s=total_ms / 1000)
        else:
            cmd = ffmpeg_service.build_concat_command(ffmpeg_path, list_file, tmp_out)
        display_cmd = ffmpeg_service.command_to_display_string(cmd)

        def concat_progress(pct: float) -> None:
            if on_progress:
                on_progress(90 + pct / 10)

        runner.run(cmd, total_ms, concat_progress)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(tmp_out), str(output_path))

    return display_cmd
