"""Bootstrap for the TapeSift interface."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from tapesift.app import (
    _apply_application_identity,
    _install_windows_app_identity,
    _project_from_args,
)
from tapesift.core.config import AppSettings
from tapesift.core.logging_config import install_excepthook, setup_logging
from tapesift.core import perf
from tapesift.core.perf import PerfSpan, PerfTimer
from tapesift.core import crash_reporter
from tapesift.services import ffmpeg_service
from tapesift.ui.ffmpeg_setup_dialog import ensure_ffmpeg
from tapesift.ui.welcome_dialog import show_if_first_run
from tapesift.ui_core.timeline_variants import timeline_variant_from_args
from tapesift.ui_v2.main_window import MainWindowV2
from tapesift.ui_v2.fonts import load_v2_fonts
from tapesift.ui_v2.theme import stylesheet

if TYPE_CHECKING:
    from tapesift.research.run_pass_temporal_review import TemporalReviewSession

log = logging.getLogger("tapesift.v2")


def _temporal_review_session_class():
    """Imported on use, not at startup.

    Every normal launch was paying to import research/ so that two opt-in
    command-line modes could exist. That also made a sandbox CONTRIBUTING.md
    calls frozen into a hard dependency of the shipped app.
    """
    from tapesift.research.run_pass_temporal_review import TemporalReviewSession
    return TemporalReviewSession


def _option_values(args: list[str], option: str) -> list[str]:
    """Read repeatable ``--name=value`` or ``--name value`` options."""
    values: list[str] = []
    prefix = f"{option}="
    index = 0
    while index < len(args):
        argument = args[index]
        if argument.startswith(prefix):
            value = argument[len(prefix):].strip()
            if value:
                values.append(value)
        elif argument == option and index + 1 < len(args):
            value = args[index + 1].strip()
            if value and not value.startswith("-"):
                values.append(value)
                index += 1
        index += 1
    return values


def _option_path(args: list[str], option: str) -> Path | None:
    values = _option_values(args, option)
    return Path(values[-1]) if values else None


def _temporal_review_session_from_args(
        args: list[str]) -> tuple[TemporalReviewSession, tuple[Path, ...]]:
    manifests = tuple(
        Path(value) for value in
        _option_values(args, "--temporal-review-manifest"))
    state_path = _option_path(args, "--temporal-review-state")
    if not manifests:
        raise ValueError(
            "Temporal Review needs at least one feature manifest.")
    if state_path is None:
        raise ValueError("Temporal Review needs a review-state path.")
    judgments_path = _option_path(
        args, "--temporal-review-judgments")
    if judgments_path is None:
        judgments_path = state_path.with_name(
            "temporal-v2.1-reviewed-onsets.jsonl")
    projects = tuple(
        Path(value) for value in
        _option_values(args, "--temporal-review-project"))
    session = _temporal_review_session_class().load_or_create(
        manifests,
        state_path,
        judgments_path,
        only_flagged=True,
    )
    return session, projects


def _snap_calibration_session_from_args(
        args: list[str]) -> tuple[TemporalReviewSession, tuple[Path, ...]]:
    """Build the all-clips, label-blind exact-snap calibration queue."""
    manifests = tuple(
        Path(value) for value in
        _option_values(args, "--snap-calibration-manifest"))
    state_path = _option_path(args, "--snap-calibration-state")
    if not manifests:
        raise ValueError(
            "Snap Calibration needs at least one feature manifest.")
    if state_path is None:
        raise ValueError("Snap Calibration needs a calibration-state path.")
    judgments_path = _option_path(
        args, "--snap-calibration-judgments")
    if judgments_path is None:
        judgments_path = state_path.with_name(
            "snap-calibration-v1-judgments.jsonl")
    projects = tuple(
        Path(value) for value in
        _option_values(args, "--snap-calibration-project"))
    session = _temporal_review_session_class().load_or_create(
        manifests,
        state_path,
        judgments_path,
        review_id="snap-calibration-v1",
        only_flagged=False,
        workflow="snap_calibration",
    )
    return session, projects


class _FirstPaintWatcher(QObject):
    """Mark the first paint the main window actually performs.

    ``show()`` returning means nothing was painted yet - Qt paints on the
    next event-loop turn, after every widget has been polished and laid
    out.  The first visible frame is that first Paint event on the
    top-level, so that is what gets marked.  Removes itself afterwards;
    the standing cost is one event-filter call per event until then.
    """

    def __init__(self, window, app) -> None:
        super().__init__(window)
        self._window = window
        self._app = app
        window.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Paint:
            self._window.removeEventFilter(self)
            perf.mark("first_frame")
            # ``TAPESIFT_PERF_EXIT_AT_FIRST_FRAME`` lets a benchmark harness
            # stop a headless launch exactly where a user would start looking.
            if os.environ.get("TAPESIFT_PERF_EXIT_AT_FIRST_FRAME", "").strip():
                QTimer.singleShot(0, self._app.quit)
            self.deleteLater()
        return False


def _watch_first_frame(window, app) -> None:
    _FirstPaintWatcher(window, app)


def run(*, window_class=MainWindowV2, stylesheet_factory=stylesheet) -> int:
    perf.mark("run_entered")
    with PerfSpan("app_v2_run_bootstrap"):
        setup_logging()
        install_excepthook()
        crash_reporter.install_crash_reporter()

    _install_windows_app_identity()
    with PerfSpan("app_v2_qapplication"):
        app = QApplication(sys.argv)
        load_v2_fonts()
    _apply_application_identity(app)
    args = sys.argv[1:]
    library_mode = "--library" in args
    temporal_review = "--temporal-review" in args
    snap_calibration = "--snap-calibration" in args
    if temporal_review and snap_calibration:
        QMessageBox.critical(
            None,
            "TapeSift Research Mode",
            "Temporal Review and Snap Calibration cannot run together.",
        )
        return 2
    run_pass_lab = (
        "--run-pass-lab" in args
        or temporal_review
        or snap_calibration
    )
    timeline_variant = timeline_variant_from_args(args)
    mode_title = (
        " - Snap Calibration"
        if snap_calibration
        else (
            " - Temporal Review"
            if temporal_review
            else (" - Run/Pass Lab" if run_pass_lab else "")
        )
    )
    app.setApplicationDisplayName(f"TapeSift{mode_title}")

    with PerfSpan("app_v2_settings_load"):
        settings = AppSettings.load()
    # Build and apply are separate costs: build is string work in Python,
    # apply is Qt parsing the sheet and repolishing every live widget.
    with PerfSpan("app_v2_stylesheet_build"):
        sheet = stylesheet_factory()
    with PerfSpan("app_v2_stylesheet_apply", chars=len(sheet)):
        app.setStyleSheet(sheet)

    if not ensure_ffmpeg(settings):
        log.warning("FFmpeg setup was cancelled; continuing without media tools")
    else:
        # Version probes launch two external FFmpeg processes.  On this
        # Windows installation those probes have taken 10-20 seconds each,
        # even though the paths were already validated by ensure_ffmpeg().
        # They are diagnostics, not a prerequisite for showing the home
        # screen, so keep them opt-in for a troubleshooting launch.
        log.info(
            "FFmpeg tools ready: %s / %s",
            Path(settings.ffmpeg_path).name,
            Path(settings.ffprobe_path).name,
        )
        if os.environ.get("TAPESIFT_FFMPEG_VERSION_CHECK", "").strip():
            log.info("FFmpeg: %s", ffmpeg_service.get_version(settings.ffmpeg_path))
            log.info("FFprobe: %s", ffmpeg_service.get_version(settings.ffprobe_path))

    temporal_session = None
    temporal_projects: tuple[Path, ...] = ()
    if temporal_review:
        try:
            temporal_session, temporal_projects = \
                _temporal_review_session_from_args(args)
        except Exception as exc:
            QMessageBox.critical(
                None,
                "TapeSift Temporal Review",
                f"Temporal Review could not start:\n\n{exc}",
            )
            return 2

    snap_session = None
    snap_projects: tuple[Path, ...] = ()
    if snap_calibration:
        try:
            snap_session, snap_projects = \
                _snap_calibration_session_from_args(args)
        except Exception as exc:
            QMessageBox.critical(
                None,
                "TapeSift Snap Calibration",
                f"Snap Calibration could not start:\n\n{exc}",
            )
            return 2

    # Build the window against an unstyled application, then restore the sheet
    # once. With the sheet live, every reparent during construction repolishes
    # its whole subtree: 721 QBoxLayout.addWidget calls cost 0.72 ms each
    # instead of microseconds, and construction measured 1903 ms styled against
    # 316 ms unstyled. One polish pass over the finished tree costs ~969 ms, so
    # the ordering is worth ~620 ms.
    #
    # The sheet is applied above rather than here because ensure_ffmpeg and the
    # CLI-mode error dialogs can run before this point, and they must be styled.
    # Dropping it only around construction keeps them styled while skipping the
    # repolish cascade. Nothing shows a dialog inside the block below.
    sheet_during_construct = app.styleSheet()
    app.setStyleSheet("")
    try:
        with PerfSpan("app_v2_window_construct"):
            window = window_class(
                settings,
                timeline_variant=timeline_variant,
                run_pass_lab=run_pass_lab,
                temporal_review_session=temporal_session,
                temporal_review_project_paths=temporal_projects,
                snap_calibration_session=snap_session,
                snap_calibration_project_paths=snap_projects,
            )
    finally:
        with PerfSpan("app_v2_stylesheet_restyle", chars=len(sheet_during_construct)):
            app.setStyleSheet(sheet_during_construct)
    # TapeSift is a dense editing workspace: ledger, player, inspector and
    # deck all want room at once. Opening at the default size leaves the
    # window floating mid-screen with the desktop showing around it, which
    # is what made it feel weightless. Maximized gives every panel its
    # intended proportions, and the title bar still restores it.
    # Plain show(). showMaximized() on this frameless, translucent
    # window crashes natively - a minidump, below Python, which is why
    # the log looked clean every time and I kept blaming the wrong
    # thing. Opening size belongs to the window's own geometry restore.
    window.show()
    perf.mark("window_shown")
    _watch_first_frame(window, app)
    if snap_calibration:
        QTimer.singleShot(0, window.start_snap_calibration)
    elif temporal_review:
        QTimer.singleShot(0, window.start_temporal_review)
    elif library_mode:
        window.show_library()
    else:
        project = _project_from_args(args)
        if not project:
            log.info("launch path: start screen (no project argument)")
            show_if_first_run(settings, window)
        else:
            log.info("launch path: opening project from argument %s", project)
            # Opened after show() so anything this raises has a visible
            # parent to sit on. Failures are handled inside _open_project,
            # which now falls back to the start screen instead of letting an
            # unexpected error escape and take the whole launch down.
            window._open_project(str(project))
    # Surface any crash from the previous run (local-only dialog).
    from tapesift.ui.crash_dialog import maybe_show_crash_dialog
    maybe_show_crash_dialog(window)
    return app.exec()
