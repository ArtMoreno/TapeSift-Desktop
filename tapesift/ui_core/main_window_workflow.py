"""MainWindow workflow methods, lifted verbatim from the V1 shell.

Phase 2B of the lean UI separation. The class body below is line-for-line
the body of the former tapesift.ui.main_window.MainWindow; only the class
name changed. A window composes this mixin AHEAD of QMainWindow in its
bases so super() inside these methods still resolves to QMainWindow.
"""

from __future__ import annotations

import contextlib
import copy
import logging
import os
import sqlite3
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction, QDesktopServices, QKeySequence, QShortcut,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QInputDialog, QLabel,
    QMainWindow, QMenu, QMessageBox, QPushButton, QHBoxLayout, QSplitter,
    QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from tapesift import __version__
from tapesift.core import config, paths as core_paths
from tapesift.core.perf import PerfSpan, PerfTimer
from tapesift.core.config import AppSettings
from tapesift.core.exceptions import TapeSiftError
from tapesift.models.clip import Clip, ExportStatus
from tapesift.models.export_job import JobStatus
from tapesift.models.telestration import marks_from_json, marks_to_json
from tapesift.services import (
    autodetect_capture_service, autodetect_export_service, detail_service,
    export_service, filename_service, library_service, play_detect_service,
    recovery_service, result_service, snap_prediction_service, tag_service,
)
from tapesift.services.tag_style_service import primary_timeline_tag
from tapesift.services.clip_factory import (
    ClipDefaults, clip_from_csv_row, clip_from_parsed_row, clip_from_range,
    clip_from_timestamp,
)
from tapesift.services import project_service
from tapesift.services.project_service import ProjectSession
from tapesift.services.timestamp_parser import format_ms, parse_timestamp
from tapesift.ui.add_clip_dialogs import RangeClipDialog, TimestampClipDialog
from tapesift.ui.about_dialog import AboutDialog
from tapesift.ui_core.clip_editor import ClipEditor
from tapesift.ui_core.clip_list import (
    ClipListWidget, compute_warnings, is_ignored_autodetect_clip,
)
from tapesift.ui_core.first_read_ui import (
    needs_logging, unresolved_count,
)
from tapesift.ui.coverage_review_dialog import CoverageReviewDialog
from tapesift.ui.detector_micro_world import DetectorMicroWorldDialog
from tapesift.services.play_detect_service import REVIEW_PREFIX, REVIEW_TAG
from tapesift.ui.how_it_works_dialog import (
    SECTIONS as HOW_IT_WORKS_SECTIONS, HowItWorksDialog,
)
from tapesift.ui.duplicate_clips_dialog import DuplicateClipsDialog
from tapesift.ui.export_panel import ExportPanel
from tapesift.ui.import_dialog import BulkPasteDialog, CsvImportDialog
from tapesift.ui_core.library_screen import LibrarySearchScreen
from tapesift.ui.bulk_edit_dialog import BulkEditDialog
from tapesift.ui.merged_clips_dialog import MergedClipsDialog
from tapesift.ui.play_detect_dialog import PlayDetectDialog
from tapesift.services import first_read_batch, first_read_service
from tapesift.ui_v2.first_read_confirm import FirstReadConfirm
from tapesift.ui_v2.first_read_dialog import FirstReadDialog
from tapesift.workers.first_read_worker import FirstReadWorker
from tapesift.ui.project_settings_dialog import ProjectSettingsDialog
from tapesift.ui.settings_dialog import SettingsDialog
from tapesift.ui.dialog_components import ActionDialog
from tapesift.ui.shortcuts_dialog import ShortcutsDialog
from tapesift.ui_core.start_screen import StartScreen
from tapesift.ui_core.video_player import VideoPlayer
from tapesift.ui_core.timeline_variants import DUAL, normalize_timeline_variant
from tapesift.ui_core.shortcuts_overlay import ShortcutsOverlay
from tapesift.ui_core.timeline import (
    PLAY_COLORS, PLAY_LABELS, TIMELINE_COLOR_MODE_LABELS, TimelineBlock,
    build_timeline_legend, classify_play_kind, timeline_category,
)
from tapesift.services import background_service, ffmpeg_service, proxy_service
from tapesift.workers.export_worker import ExportWorker
from tapesift.workers.metadata_worker import MetadataWorker
from tapesift.workers.proxy_worker import ProxyWorker
from tapesift.workers.snap_prediction_worker import SnapPredictionWorker
from tapesift.workers.thumbnail_worker import ThumbnailWorker

log = logging.getLogger(__name__)

#: Workers that outlived the window that owned them. Qt must not destroy a
#: running QThread and Python must not collect the wrapper, so a worker that
#: would not stop in time waits here until it ends on its own.
_detached_workers: list = []


def _forget_detached(worker) -> None:
    if worker in _detached_workers:
        _detached_workers.remove(worker)


class MainWindowWorkflow:
    project_settings_dialog_class = ProjectSettingsDialog

    # Downstream workflows (notably Voiceover) consume only mutations that
    # survived selection and read-only checks. The raw surface signal remains
    # private to this acceptance boundary.
    telestration_edit_accepted = Signal(str, object)

    def __init__(
            self, settings: AppSettings, timeline_variant: str = DUAL) -> None:
        super().__init__()
        self.settings = settings
        self.timeline_variant = normalize_timeline_variant(timeline_variant)
        self.session: ProjectSession | None = None
        self.export_worker: ExportWorker | None = None
        self.thumb_worker: ThumbnailWorker | None = None
        self._pending_thumbnail_clips: list[Clip] | None = None
        self.metadata_worker: MetadataWorker | None = None
        self.proxy_worker: ProxyWorker | None = None
        self._proxy_retry_timer = QTimer(self)
        self._proxy_retry_timer.setSingleShot(True)
        self._proxy_retry_timer.timeout.connect(self._retry_auto_preview)
        self.snap_prediction_worker: SnapPredictionWorker | None = None
        self._index_thread: threading.Thread | None = None
        self._index_timer: "QTimer | None" = None
        self._index_pending = False
        # Incremented for every open/create so deferred thumbnail/index work
        # cannot finish against a project that the user has already replaced.
        self._session_activation_serial = 0
        self._last_proxy_pct = -10
        self._coverage_review_dialog: CoverageReviewDialog | None = None

        self.setWindowTitle("TapeSift")
        self.resize(1440, 900)
        self.setAcceptDrops(True)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        with PerfSpan("init_start_screen"):
            self.start_screen = self._make_start_screen(settings)
            self.start_screen.new_project_requested.connect(self._new_project)
            self.start_screen.open_project_requested.connect(self._open_project)
            self.start_screen.settings_requested.connect(self._open_settings)
            self.start_screen.library_requested.connect(self.show_library)
            self.start_screen.rename_project_requested.connect(
                self._rename_project)
            self.start_screen.delete_project_requested.connect(
                self._delete_project)
            self.start_screen.duplicate_project_requested.connect(
                self._duplicate_project)
            self.start_screen.resume_project_requested.connect(
                self._resume_project)
            for signal_name, slot_name in (
                    ("new_project_with_video_requested",
                     "_start_configured_project"),
                    ("retry_video_requested", "_retry_open_video"),
                    ("open_logs_requested", "_open_logs")):
                signal = getattr(self.start_screen, signal_name, None)
                slot = getattr(self, slot_name, None)
                if signal is not None and slot is not None:
                    signal.connect(slot)
            self.stack.addWidget(self.start_screen)

        with PerfSpan("init_build_workspace"):
            self.workspace = self._build_workspace()
            self.stack.addWidget(self.workspace)

        with PerfSpan("init_library_screen"):
            self.library_screen = self._make_library_screen(settings)
            self.library_screen.back_requested.connect(self._leave_library)
            self.library_screen.open_clip_requested.connect(
                self._open_clip_from_library)
            self.library_screen.apply_edit = self._apply_library_edit
            self.library_screen.apply_game_year = self._apply_library_game_year
            self.stack.addWidget(self.library_screen)

        with PerfSpan("init_menu_and_shortcuts"):
            self._build_menu()
            self._build_shortcuts()

        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self._autosave)
        self.statusBar().showMessage("Welcome to TapeSift")

        # 2.2: a persistent indicator of who owns the keyboard (playback vs
        # typing). Sits on the right side of the status bar.
        self._focus_indicator = QLabel("PLAYBACK ACTIVE")
        self._focus_indicator.setProperty("role", "focus")
        self._focus_indicator.setMinimumWidth(220)
        self.statusBar().addPermanentWidget(self._focus_indicator)
        # Update the indicator whenever focus enters/leaves any widget.
        self.focus_changed_signal = None
        QApplication.instance().focusObjectChanged.connect(
            self._update_focus_indicator)
        self._update_focus_indicator()

        self._check_recovery()

    # ---------- workspace construction ----------

    def _build_workspace(self) -> QWidget:
        workspace = QWidget()
        outer = QVBoxLayout(workspace)
        outer.setContentsMargins(8, 4, 8, 4)

        # Top bar
        top = QHBoxLayout()
        self.project_label = QLabel("")
        self.project_label.setProperty("role", "heading")
        top.addWidget(self.project_label)
        self.metadata_label = QLabel("")
        self.metadata_label.setProperty("role", "subtle")
        top.addWidget(self.metadata_label, 1)
        # Scrubbing smoothness depends on which file the preview is playing,
        # so never leave that invisible.
        self.preview_source_label = QLabel("")
        self.preview_source_label.setToolTip(
            "Which file the preview is playing. The original is slow to seek "
            "(large HD frames, keyframes seconds apart); the optimized copy "
            "seeks about 10x faster.")
        top.addWidget(self.preview_source_label)
        # Where you are in the review pass.
        self.review_label = QLabel("")
        self.review_label.setProperty("role", "subtle")
        self.review_label.setToolTip(
            "Review mode (F5): Ctrl+Down/Up moves between clips, Enter saves and "
            "advances, R replays, Ctrl+L loops.")
        top.addWidget(self.review_label)
        load_btn = QPushButton("Load Video…")
        load_btn.clicked.connect(self._browse_video)
        self.relink_btn = QPushButton("Relink Source…")
        self.relink_btn.clicked.connect(self._relink_source)
        self.relink_btn.hide()
        for b in (load_btn, self.relink_btn):
            top.addWidget(b)
        top.addStretch()
        # One primary creation control instead of four competing buttons.
        new_clip_btn = QToolButton()
        new_clip_btn.setText("New Clip")
        new_clip_btn.setProperty("accent", "true")
        new_clip_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        new_clip_btn.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        new_menu = QMenu(new_clip_btn)
        self._add_action(new_menu, "From In/Out Range", "",
                         self._add_from_marks)
        self._add_action(new_menu, "From Current Position…", "",
                         self._add_by_timestamp)
        self._add_action(new_menu, "Enter Start/End…", "", self._add_by_range)
        new_menu.addSeparator()
        self._add_action(
            new_menu, "Detect Plays (Beta)…", "", self._detect_plays)
        self._add_action(
            new_menu, "First Read: Run/Pass…", "", self._first_read)
        new_menu.addSeparator()
        self._add_action(new_menu, "Bulk Paste…", "", self._bulk_paste)
        self._add_action(new_menu, "Import CSV…", "", self._csv_import)
        new_clip_btn.setMenu(new_menu)
        top.addWidget(new_clip_btn)
        outer.addLayout(top)

        # Smooth-scrub offer (hidden until a long-GOP HD film loads unproxied)
        self.proxy_banner_widget = QWidget()
        banner = QHBoxLayout(self.proxy_banner_widget)
        banner.setContentsMargins(0, 0, 0, 0)
        self.proxy_banner = QLabel("")
        self.proxy_banner.setProperty("role", "warning")
        build_now_btn = QPushButton("Build Now (~4 min)")
        build_now_btn.setProperty("accent", "true")
        build_now_btn.clicked.connect(self._build_proxy_on_demand)
        dismiss_btn = QPushButton("Not now")
        dismiss_btn.clicked.connect(self._dismiss_proxy_banner)
        banner.addWidget(self.proxy_banner)
        banner.addWidget(build_now_btn)
        banner.addWidget(dismiss_btn)
        banner.addStretch()
        self.proxy_banner_widget.hide()
        outer.addWidget(self.proxy_banner_widget)

        # Middle: player | clip editor
        middle = QSplitter(Qt.Orientation.Horizontal)
        self.player = VideoPlayer(
            self.settings, timeline_variant=self.timeline_variant)
        self.player.add_clip_requested.connect(self._add_from_in_out)
        self.player.clip_block_activated.connect(self._timeline_clip_activated)
        self.player.clip_trim_preview.connect(self._timeline_trim_preview)
        self.player.clip_trim_finished.connect(self._timeline_trim_finished)
        self.player.unclaimed_footage_activated.connect(
            self._unclaimed_footage_clicked)
        self.player.grid_cell_edit_requested.connect(
            self._grid_cell_edit_requested)
        self.player.grid_cell_choice_picked.connect(
            self._grid_cell_choice_picked)
        self.player.grid_player_filter_changed.connect(
            self._grid_player_filter_changed)
        self.player.timeline_context_requested.connect(
            self._timeline_context_menu_requested)
        self.player.timeline_color_mode_changed.connect(
            self._timeline_color_mode_changed)
        self.player.predicted_snap_requested.connect(
            self._predicted_snap_requested)
        self.player.clicked.connect(self._return_focus_to_playback)
        self.player.telestration_marks_changed.connect(
            self._telestration_marks_edited)
        self._selected_clip_id: str | None = None
        self._syncing_selection = False
        middle.addWidget(self.player)
        self.clip_editor = self._make_clip_editor(self.settings)
        self.clip_editor.edit_started.connect(self._clip_edit_started)
        self.clip_editor.clip_edited.connect(self._clip_edited)
        self.clip_editor.quick_export_requested.connect(self._quick_export_clip)
        self.clip_editor.save_and_advance.connect(self._advance_after_save)
        self.clip_editor.return_to_playback.connect(self._return_focus_to_playback)
        middle.addWidget(self.clip_editor)
        middle.setStretchFactor(0, 3)
        middle.setStretchFactor(1, 1)

        # Bottom: clip list, then export queue
        bottom = QSplitter(Qt.Orientation.Vertical)
        bottom.addWidget(middle)
        self.clip_list = ClipListWidget()
        self.clip_list.selection_changed.connect(self._selection_changed)
        self.clip_list.previous_requested.connect(lambda: self._goto_clip(-1))
        self.clip_list.next_requested.connect(lambda: self._goto_clip(1))
        self.clip_list.reorder_requested.connect(self._reorder)
        self.clip_list.delete_requested.connect(self._delete_clips)
        self.clip_list.duplicate_requested.connect(self._duplicate_clip)
        self.clip_list.bulk_edit_requested.connect(self._bulk_edit)
        self.clip_list.mark_reviewed_requested.connect(
            self._mark_detection_reviewed)
        self.clip_list.mark_missed_requested.connect(
            self._mark_missed_detection)
        self.clip_list.confirm_false_positive_requested.connect(
            self._confirm_detection_false_positive)
        self.clip_list.view_mode_changed.connect(self._clip_view_changed)
        self.clip_list.review_filter_changed.connect(
            lambda _mode: self._sync_autodetect_batch_clip_view())
        self.clip_list.set_compact(self.settings.clip_list_compact)
        self.clip_list.preview_requested.connect(self._preview_clip)
        self.clip_list.toggle_enabled_requested.connect(self._toggle_enabled)
        self.clip_list.toggle_reel_requested.connect(self._toggle_reel)
        bottom.addWidget(self.clip_list)
        self.export_panel = ExportPanel()
        self.export_panel.export_requested.connect(self._start_export)
        self.export_panel.output_folder_change_requested.connect(
            self._change_output_folder)
        self.export_panel.cancel_current_requested.connect(self._cancel_current)
        self.export_panel.cancel_all_requested.connect(self._cancel_all)
        self.export_panel.remove_queued_requested.connect(
            self._remove_queued_export)
        self.export_panel.retry_requested.connect(self._retry_job)
        bottom.addWidget(self.export_panel)
        bottom.setStretchFactor(0, 4)
        bottom.setStretchFactor(1, 3)
        bottom.setStretchFactor(2, 1)
        outer.addWidget(bottom, 1)
        return workspace

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        # The library (every clip across every project) was only reachable from
        # the start screen; expose it from inside a project too.
        self._add_action(
            file_menu, "Go to Library", "Ctrl+Shift+L", self.show_library)
        file_menu.addSeparator()
        self._add_action(file_menu, "Save Project", "Ctrl+S", self._save_project)
        self._add_action(
            file_menu, "Export Autodetect Correction Data…", "",
            self._export_autodetect_corrections)
        self._add_action(
            file_menu, "View Autodetect Batch Score…", "",
            self._show_autodetect_batch_score)
        self.rename_action = self._add_action(
            file_menu, "Rename Project…", "F2", self._rename_open_project)
        self.rename_action.setEnabled(False)   # no project open yet
        self._add_action(file_menu, "Close Project", "", self._close_project)
        self._export_folder_action = self._add_action(
            file_menu, "Open Export Folder", "",
            self._open_export_folder)
        self._export_folder_action.setEnabled(False)
        file_menu.addSeparator()
        self._add_action(file_menu, "Project Settings…", "",
                         self._open_project_settings)
        self._add_action(file_menu, "Settings…", "", self._open_settings)
        self._add_action(file_menu, "Open Logs Folder", "", self._open_logs)
        file_menu.addSeparator()
        self._add_action(file_menu, "Exit", "", self.close)

        edit_menu = self.menuBar().addMenu("&Edit")
        self._add_action(edit_menu, "Undo", "Ctrl+Z", self._undo)
        self._add_action(edit_menu, "Redo", "Ctrl+Y", self._redo)
        edit_menu.addSeparator()
        # Also in the menu, not only on a key: see Review Mode above for why
        # a shortcut alone is not enough to call a feature reachable.
        self.details_action = QAction("Play Details Panel", self)
        self.details_action.setCheckable(True)
        self.details_action.setShortcut(QKeySequence("Ctrl+I"))
        self.details_action.setToolTip(
            "Open or close the play details on the selected clip.")
        self.details_action.triggered.connect(self._toggle_details_panel)
        edit_menu.addAction(self.details_action)
        edit_menu.addSeparator()
        self._add_action(edit_menu, "Sort Clips by Start Time…", "",
                         self._sort_clips_by_time)
        self._add_action(edit_menu, "Find Duplicate Clips…", "",
                         self._find_duplicate_clips)

        playback_menu = self.menuBar().addMenu("&Playback")
        # Review mode persists between sessions and changes how playback
        # behaves, so it needs to be visible and reachable without a
        # function key - keyboard utilities routinely swallow the F row
        # before an application ever sees it.
        self.review_action = QAction("Review Mode", self)
        self.review_action.setCheckable(True)
        self.review_action.setChecked(self.settings.review_mode)
        self.review_action.setShortcuts(
            [QKeySequence("F5"), QKeySequence("Ctrl+Shift+R")])
        self.review_action.setToolTip(
            "Play each clip on its own, with the details panel focused, and "
            "move to the next one when you save.")
        self.review_action.triggered.connect(self._toggle_review_mode)
        playback_menu.addAction(self.review_action)
        self.review_coverage_action = self._add_action(
            playback_menu,
            "Review Detection Coverage…",
            "Ctrl+Shift+D",
            self._show_coverage_review,
        )
        playback_menu.addSeparator()
        self.start_autodetect_batch_action = self._add_action(
            playback_menu,
            "Start Autodetect Test Batch from In/Out Range…",
            "",
            self._start_autodetect_test_batch,
        )
        self.play_autodetect_batch_action = self._add_action(
            playback_menu,
            "Play Active Autodetect Test Range",
            "",
            self._play_active_autodetect_test_range,
        )
        self.finish_autodetect_batch_action = self._add_action(
            playback_menu,
            "Finish Autodetect Test Batch…",
            "",
            self._finish_autodetect_test_batch,
        )
        self.cancel_autodetect_batch_action = self._add_action(
            playback_menu,
            "Cancel Autodetect Test Batch…",
            "",
            self._cancel_autodetect_test_batch,
        )
        playback_menu.addSeparator()
        self._add_action(playback_menu, "Build Smooth-Scrub Preview…", "",
                         self._build_proxy_on_demand)
        self._add_action(playback_menu, "Use Original Video for Preview", "",
                         self._use_original_preview)
        self._add_action(playback_menu, "Show Full Timeline", "Ctrl+0",
                         self.player.reset_timeline_zoom)

        # Sits before Help because it answers "what do I do" rather than
        # "what does this button do".
        how_menu = self.menuBar().addMenu("How TapeSift &Works")
        for index, (title, _) in enumerate(HOW_IT_WORKS_SECTIONS):
            self._add_action(how_menu, title, "",
                             lambda _=False, i=index: self._show_how_it_works(i))
        # A detector lab visualization, not user documentation. Kept out of
        # the production menu; set TAPESIFT_DEV_TOOLS=1 to bring it back.
        if os.environ.get("TAPESIFT_DEV_TOOLS") == "1":
            how_menu.addSeparator()
            self._add_action(
                how_menu,
                "Detector Micro-World...",
                "",
                self._show_detector_micro_world,
            )

        help_menu = self.menuBar().addMenu("&Help")
        self._add_action(help_menu, "Keyboard Shortcuts", "F1", self._show_shortcuts)
        self._add_action(help_menu, "Getting Started", "", self._show_welcome)
        help_menu.addSeparator()
        self._add_action(help_menu, "About TapeSift", "", self._show_about)

    def _add_action(self, menu, text: str, shortcut: str, slot) -> QAction:
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        menu.addAction(action)
        return action

    def _build_shortcuts(self) -> None:
        self._transport_shortcuts: dict[str, QShortcut] = {}

        def add(
                key: str, slot, *,
                context: Qt.ShortcutContext =
                Qt.ShortcutContext.WindowShortcut) -> QShortcut:
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(context)
            sc.activated.connect(slot)
            return sc

        def add_workspace(
                key: str, slot, *,
                allow_while_typing: bool = False) -> QShortcut:
            """Add one command shared by the main and floating workspaces."""
            shortcut = add(
                key,
                self._transport_shortcut(
                    slot, allow_while_typing=allow_while_typing),
                context=Qt.ShortcutContext.ApplicationShortcut,
            )
            self._transport_shortcuts[key] = shortcut
            return shortcut

        add_workspace("Space", self.player.toggle_play)
        for key, slot in (
                ("J", self.player.shuttle_reverse),
                ("K", self.player.shuttle_stop),
                ("L", self.player.shuttle_forward)):
            add_workspace(key, slot)
        # NLE-style: paused Left/Right = exact one frame (auto-repeats while
        # held for continuous movement); while playing they fall back to jumps.
        # Modifier keys give the larger jumps.
        add_workspace("Left", lambda: self.player.step_or_jump(-1))
        add_workspace("Right", lambda: self.player.step_or_jump(1))
        add_workspace("Shift+Left", self.player.jump_backward)
        add_workspace("Shift+Right", self.player.jump_forward)
        add_workspace(
            "Ctrl+Left", lambda: self.player.seek_relative(-5000))
        add_workspace(
            "Ctrl+Right", lambda: self.player.seek_relative(5000))
        add_workspace("I", self._i_pressed)
        add_workspace("O", self._o_pressed)
        add_workspace("A", self.player.request_add_clip)
        add_workspace("Delete", self._shortcut_delete)
        add_workspace("Ctrl+D", self._shortcut_duplicate)
        # Review-mode navigation. Ctrl+arrows keep working while typing in the
        # inspector, which bare letters could not.
        add_workspace(
            "Ctrl+Down", lambda: self._goto_clip(1),
            allow_while_typing=True)
        add_workspace(
            "Ctrl+Up", lambda: self._goto_clip(-1),
            allow_while_typing=True)
        add_workspace("PgDown", lambda: self._goto_clip(1))
        add_workspace("PgUp", lambda: self._goto_clip(-1))
        add_workspace(
            "Ctrl+Shift+Down", self._goto_next_unlogged,
            allow_while_typing=True)
        # 3.1: jump to the first / last clip.
        add_workspace("Ctrl+Home", lambda: self._goto_edge(True))
        add_workspace("Ctrl+End", lambda: self._goto_edge(False))
        add_workspace("Ctrl+L", self._toggle_loop)
        add_workspace("R", self._replay_clip)
        add_workspace("N", self._dismiss_clip)
        add_workspace("[", self._nudge_in)
        add_workspace("]", self._nudge_out)
        self._cut_shortcut = add_workspace(
            "C", self._cut_clip_at_playhead)
        add_workspace("M", self._merge_selected_clips)
        # The sheet is useful from every screen, so it is not a workspace key.
        add("?", self._toggle_shortcuts_overlay,
            context=Qt.ShortcutContext.ApplicationShortcut)
        add_workspace("Ctrl+K", self._split_clip)
        # Not Ctrl+D - that duplicates a clip. Ctrl+B is the sidebar fold
        # in most editors, and it is free here.
        add_workspace("Ctrl+B", self.clip_editor.toggle_collapsed)
        add_workspace("U", lambda: self._goto_unlogged(1))
        add_workspace("Shift+U", lambda: self._goto_unlogged(-1))
        add_workspace("Ctrl+M", self._repeat_metadata)
        add_workspace("Ctrl+Shift+V", self._paste_timestamp_seek)
        add_workspace("Ctrl+E", self._quick_export_selected)
        add_workspace("S", self.player.toggle_timeline_snapping)
        add_workspace("G", self._predicted_snap_requested)
        # 2.3: Esc returns keyboard control to playback from any text field.
        add_workspace(
            "Esc", self._shortcut_escape, allow_while_typing=True)
        # Home and Library have their own Space/Escape/Ctrl+E meanings.
        # Disabling the project shortcuts there prevents Qt from treating the
        # two valid bindings as ambiguous before either handler can run.
        self.stack.currentChanged.connect(
            self._sync_transport_shortcut_page)
        self._sync_transport_shortcut_page(self.stack.currentIndex())

    def _sync_transport_shortcut_page(self, _index: int) -> None:
        """Enable project editing keys only while its workspace is visible."""
        enabled = self.stack.currentWidget() is self.workspace
        for shortcut in self._transport_shortcuts.values():
            shortcut.setEnabled(enabled)

    def _shortcut(self, fn):
        def wrapper() -> None:
            if self.session and not self._typing_in_text_field():
                fn()
        return wrapper

    def _transport_shortcut(self, fn, *, allow_while_typing: bool = False):
        """Route one editing command from this window or its floating docks.

        The existing handlers remain authoritative. This wrapper expands only
        their shortcut scope and refuses dialogs, menus, unrelated application
        windows, and (except for explicit navigation/Escape commands) editable
        controls.
        """
        def wrapper() -> None:
            if not self._transport_shortcut_allowed(
                    allow_while_typing=allow_while_typing):
                return
            fn()
        return wrapper

    def _transport_shortcut_allowed(
            self, *, allow_while_typing: bool = False) -> bool:
        # A project may remain open while Library is the active stacked page.
        # Library owns Space/Ctrl+E and its other local bindings, so the
        # project editing layer must not compete with them there.
        if not self.session or self.stack.currentWidget() is not self.workspace:
            return False
        app = QApplication.instance()
        if app is None:
            return allow_while_typing or not self._typing_in_text_field()
        if app.activeModalWidget() is not None \
                or app.activePopupWidget() is not None:
            return False
        active = app.activeWindow()
        allowed = {self, *self._transport_auxiliary_windows()}
        if active is not None and active not in allowed:
            return False
        return allow_while_typing or not self._typing_in_text_field()

    def _transport_auxiliary_windows(self) -> tuple[QWidget, ...]:
        """Top-level TapeSift windows allowed to dispatch editing keys."""
        return ()

    def _shortcut_play(self) -> None:
        if self.session and not self._typing_in_text_field():
            self.player.toggle_play()

    def _typing_in_text_field(self) -> bool:
        from PySide6.QtWidgets import (
            QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit,
        )
        # A floating QDockWidget is a separate native window. MainWindow's
        # focusWidget() cannot see its editor, while QApplication can.
        w = QApplication.focusWidget() or self.focusWidget()
        return (
            isinstance(
                w, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox))
            or isinstance(w, QComboBox) and w.isEditable()
        )

    def _shortcut_escape(self) -> None:
        """2.3: Esc returns keyboard control to playback.

        If a text field has focus, blur it and hand focus back to the video
        surface so spacebar / JKL work again. In the scoped detector-pending
        view, a subsequent Esc returns to the full clip list without ending
        the active batch. Menus and dialogs handle their own Esc.
        """
        if self._typing_in_text_field():
            self._return_focus_to_playback()
        elif (
            self.session
            and self.clip_list.review_filter_mode == "autodetect_pending"
        ):
            active_batch = self.session.active_autodetect_review_batch()
            self.clip_list.show_review_filter("all")
            message = "Showing all clips."
            if active_batch is not None:
                message += " The autodetect test batch remains active."
            self.statusBar().showMessage(
                message,
                4000,
            )

    def _return_focus_to_playback(self) -> None:
        """Blur any active editor and focus the video surface (specs 1.3/1.4/2.3)."""
        w = QApplication.focusWidget() or self.focusWidget()
        if w is not None:
            w.clearFocus()
        self.player.video_widget.setFocus()
        self._update_focus_indicator()

    def _update_focus_indicator(self) -> None:
        """2.2: show whether playback or typing owns the keyboard."""
        if self._focus_indicator is None:
            return
        if self._typing_in_text_field():
            self._focus_indicator.setText("TYPING IN NOTES • Esc to return")
            self._focus_indicator.setProperty("mode", "typing")
        else:
            self._focus_indicator.setText("PLAYBACK ACTIVE")
            self._focus_indicator.setProperty("mode", "playback")
        # Re-polish so the mode property change applies its stylesheet.
        self._focus_indicator.style().unpolish(self._focus_indicator)
        self._focus_indicator.style().polish(self._focus_indicator)

    # ---------- project lifecycle ----------

    def _check_recovery(self) -> None:
        pending = recovery_service.pending_recovery()
        if pending:
            db_path, name = pending
            answer = QMessageBox.question(
                self, "Restore project?",
                f"TapeSift did not close cleanly last time.\n"
                f"Reopen the project '{name or Path(db_path).stem}'?")
            if answer == QMessageBox.StandardButton.Yes:
                self._open_project(db_path)
            else:
                recovery_service.mark_closed()

    def _new_project(self, name: str, folder: str, output_folder: str) -> None:
        try:
            session = ProjectSession.create(name, Path(folder), Path(output_folder))
            self._initialize_project_defaults(session)
        except TapeSiftError as exc:
            QMessageBox.warning(self, "Could not create project", exc.user_text())
            return
        self._activate_session(session)

    def _open_project(self, db_path: str) -> None:
        # Keep the open path measurable in a normal desktop launch, not only
        # under TAPESIFT_PERF=1 - see PerfSpan.  The split between db and
        # activation is what tells you whether a slow open is SQLite
        # open/migration or the UI work that follows it.
        with PerfSpan("open_project", path=Path(db_path).name) as span:
            try:
                with PerfSpan("open_project_db"):
                    session = ProjectSession.open(Path(db_path))
            except TapeSiftError as exc:
                log.warning("Could not open project %s: %s", db_path, exc)
                QMessageBox.warning(
                    self, "Could not open project", exc.user_text())
                return
            except Exception as exc:
                # Anything that is not a TapeSiftError used to escape this
                # method entirely.  On the command-line/double-click path
                # _open_project is called straight out of app_v2.run, so an
                # unexpected sqlite, migration or attribute error there took
                # the whole launch down instead of landing on the start
                # screen - which is exactly the shape of "opening an existing
                # project is unreliable, I have to make a new one first".
                log.exception("Unexpected failure opening project %s", db_path)
                QMessageBox.warning(
                    self,
                    "Could not open project",
                    f"TapeSift could not open this project:\n\n{exc}\n\n"
                    "The details are in the log (Help → Open Logs).",
                )
                self._show_start_screen_after_failed_open()
                return
            span.note(clips=len(getattr(session, "clips", ()) or ()))
            self._activate_session(session)

    def _show_start_screen_after_failed_open(self) -> None:
        """Leave a failed open on the start screen, never on a blank shell."""
        try:
            self.stack.setCurrentWidget(self.start_screen)
        except Exception:
            log.exception("Could not fall back to the start screen")

    def _project_is_open(self, db_path: str) -> bool:
        return bool(self.session and str(self.session.db_path) == db_path)

    def _replace_recent(self, old_path: str, new_path: str | None) -> None:
        """Point the recent list at a moved project, or drop a deleted one."""
        recents = self.settings.recent_projects
        if old_path in recents:
            index = recents.index(old_path)
            if new_path:
                recents[index] = new_path
            else:
                recents.pop(index)
        elif new_path and new_path not in recents:
            recents.insert(0, new_path)
        self.settings.save()
        self.start_screen.refresh_recent()

    def _rename_project(self, db_path: str, new_name: str) -> None:
        if self._project_is_open(db_path):
            QMessageBox.information(
                self, "Project is open",
                "Close this project before renaming it.")
            return
        try:
            new_path = project_service.rename_project(Path(db_path), new_name)
        except TapeSiftError as exc:
            QMessageBox.warning(self, "Could not rename project", exc.user_text())
            return
        # The library index is keyed on the project path - move it too, so
        # search results keep opening the right file.
        library_service.remove_project(db_path)
        try:
            library_service.reindex_project_file(str(new_path))
        except Exception:
            log.exception("Could not re-index renamed project")
        self._replace_recent(db_path, str(new_path))
        self.statusBar().showMessage(f"Renamed to '{new_name}'", 4000)

    def _rename_open_project(self) -> None:
        """Rename the project you are working in, without closing it first.

        Renaming touches the file on disk, so the session has to be saved
        and closed around it - but that is an implementation detail, not
        something to make the user do. The project is reopened afterwards
        with the same clip selected, so the rename feels like an edit
        rather than a round trip through the home screen.
        """
        if not self.session:
            return
        old_path = str(self.session.db_path)
        current = self.session.project.name
        new_name, ok = QInputDialog.getText(
            self, "Rename Project", "Project name:", text=current)
        if not ok or not new_name.strip() or new_name.strip() == current:
            return
        new_name = new_name.strip()

        row = self._current_row()
        position_ms = self.player.position_ms()
        self._stop_thumbnails()
        self._stop_proxy_worker()
        self._stop_snap_prediction_worker()
        self.session.save()
        # Deliberately not indexing here. _index_current_project writes on a
        # daemon thread, so an index of the old path could land *after* the
        # removal below and leave the catalog pointing at a file that no
        # longer exists. The new path is indexed synchronously further down.
        # The export bridge's restore threads each opened their own SQLite
        # handle when the project was activated. Joining them is what makes
        # the rename below legal on Windows: with a handle still open,
        # os.rename fails with WinError 32, the file keeps its old name and
        # the recent list goes stale. The normal close path joins them via
        # _before_session_close; the rename path must do the same before it
        # closes and renames, and it must stay synchronous.
        cancel_bridge = getattr(self, "_cancel_export_bridge_threads", None)
        if cancel_bridge is not None:
            cancel_bridge(wait=True)
        try:
            self.session.close()
        except Exception:
            log.exception("Closing session before rename")
        # Drop the reference as well. Leaving it set makes the reopen below
        # try to close an already-closed session, which re-indexes the old
        # path moments after we removed it.
        self.session = None
        recovery_service.mark_closed()

        try:
            new_path = project_service.rename_project(Path(old_path), new_name)
        except TapeSiftError as exc:
            QMessageBox.warning(self, "Could not rename project",
                                exc.user_text())
            self._open_project(old_path)      # put the user back where he was
            return

        library_service.remove_project(old_path)
        self._replace_recent(old_path, str(new_path))
        self._open_project(str(new_path))
        try:
            library_service.reindex_project_file(str(new_path))
        except Exception:
            log.exception("Could not re-index renamed project")
        if row >= 0:
            self._select_row(row)
        if position_ms:
            self.player.seek_to(position_ms)
        self.statusBar().showMessage(f"Renamed to '{new_name}'", 4000)

    def _duplicate_project(self, db_path: str) -> None:
        if self._project_is_open(db_path):
            QMessageBox.information(
                self, "Project is open",
                "Close this project before duplicating it.")
            return
        try:
            new_path = project_service.duplicate_project(Path(db_path))
        except TapeSiftError as exc:
            QMessageBox.warning(self, "Could not duplicate project",
                                exc.user_text())
            return
        try:
            library_service.reindex_project_file(str(new_path))
        except Exception:
            log.exception("Could not index duplicated project")
        self.settings.add_recent_project(str(new_path))
        self.settings.save()
        self.start_screen.refresh_recent()
        self.statusBar().showMessage(
            f"Duplicated to '{new_path.stem}'", 4000)

    def _delete_project(self, db_path: str) -> None:
        if self._project_is_open(db_path):
            QMessageBox.information(
                self, "Project is open",
                "Close this project before deleting it.")
            return
        try:
            project_service.delete_project(Path(db_path))
        except TapeSiftError as exc:
            QMessageBox.warning(self, "Could not delete project", exc.user_text())
            return
        library_service.remove_project(db_path)   # drop its clips from search
        self._replace_recent(db_path, None)
        self.statusBar().showMessage(
            f"Deleted '{Path(db_path).stem}' - video and exports untouched", 5000)

    def _repoint_project_output_off_system_drive(self) -> None:
        """Move a project's exports off the system drive, if it never chose.

        The app-level default already moved, but every project stores its own
        output folder, so existing projects kept writing to C: - the drive
        that is nearly full. Repointing the setting is the whole fix: clips
        already exported stay exactly where they are, and only a folder still
        sitting inside the old default is touched. A folder the user pointed
        somewhere deliberately is left alone.
        """
        if not self.session:
            return
        project = self.session.project
        current = (project.output_folder or "").strip()
        if not current:
            return
        legacy_root = core_paths.legacy_exports_dir()
        new_root = core_paths.default_exports_dir()
        if new_root == legacy_root:
            return                      # no data drive; nothing to move to
        try:
            relative = Path(current).relative_to(legacy_root)
        except (ValueError, OSError):
            return                      # not under the old default - leave it
        replacement = new_root / relative
        project.output_folder = str(replacement)
        log.info(
            "Moved project exports off the system drive: %s -> %s",
            current, replacement)

    def _initialize_project_defaults(self, session: ProjectSession) -> None:
        """Copy preferences once; reopening must preserve the saved project."""
        project = session.project
        project.naming_template = self.settings.naming_template
        project.output_organization = self.settings.output_organization
        project.default_preset = self.settings.default_preset
        project.accurate_cut = self.settings.accurate_cut
        session.save()

    def _activate_session(self, session: ProjectSession) -> bool:
        """Make `session` the open project. False if the user said not to.

        The incoming session is already open by the time it gets here, so a
        refusal has to close it or it leaks. The old code called
        _close_project, ignored the answer, and overwrote self.session -
        which left the *previous* project open, unsaved and unreferenced,
        its file handle held for the life of the process. Same shape as the
        closeEvent bug: the export prompt's "no" was collected and then
        thrown away.
        """
        with PerfSpan("activate_session"):
            if self.session:
                if not self._close_project():
                    with contextlib.suppress(Exception):
                        session.close()
                    return False
            self.session = session
            # A player instance survives project switches. Clear the old
            # project's selection-bound presentation before the incoming
            # ledger is populated, so no frame can show another project's
            # telestration while the new session opens.
            self._selected_clip_id = None
            self.player.set_selected_clip_id(None)
            self._load_clip_telestration(None)
            self._session_activation_serial += 1
            activation_serial = self._session_activation_serial
            read_only = bool(getattr(session, "read_only", False))
            suppressed_fragments = 0
            if not read_only:
                try:
                    suppressed_fragments = \
                        session.suppress_legacy_unclassified_clips()
                except Exception:
                    log.exception(
                        "Could not suppress legacy unclassified detector clips")
                self._repoint_project_output_off_system_drive()
                self.settings.add_recent_project(str(session.db_path))
                self.settings.save()
                recovery_service.mark_open(
                    session.db_path, session.project.name)
            self.rename_action.setEnabled(not read_only)
            self.project_label.setText(session.project.name)
            self.setWindowTitle(f"{session.project.name} - TapeSift")
            self.stack.setCurrentWidget(self.workspace)
            if read_only:
                self.autosave_timer.stop()
            else:
                self.autosave_timer.start(
                    self.settings.autosave_interval_seconds * 1000)

            if session.project.has_source:
                source = Path(session.project.source_video_path)
                if source.is_file():
                    with PerfSpan("load_preview_source"):
                        self._load_preview_source(
                            source, session.project.source_metadata.frame_rate)
                    self._show_metadata()
                    self.relink_btn.hide()
                else:
                    self.metadata_label.setText(
                        f"Source video missing: {source} - use Relink Source.")
                    self.relink_btn.show()
            else:
                self.metadata_label.setText(
                    "No video loaded yet - click Load Video.")
            with PerfSpan("refresh_clip_list",
                          clips=len(getattr(session, "clips", ()) or ())):
                self._refresh_clip_list()

            # The workspace is usable as soon as the project is selected.
            # Thumbnail generation and catalog indexing are non-critical open
            # work; queue them after Qt has painted the editor.  The identity
            # guard prevents a fast project switch from applying old work to
            # the new session.
            QTimer.singleShot(
                0, self,
                lambda s=session, n=activation_serial, ro=read_only:
                self._finish_deferred_session_open(s, n, ro))
            message = f"Opened project '{session.project.name}'"
            if suppressed_fragments:
                message += (
                    f" · moved {suppressed_fragments} legacy detector "
                    "fragments off the Clips list")
            self.statusBar().showMessage(message)
            return True

    def _finish_deferred_session_open(
            self, session: ProjectSession, activation_serial: int,
            read_only: bool) -> None:
        """Run nonessential project-open work after the workspace is visible."""
        if (
            self.session is not session
            or activation_serial != self._session_activation_serial
        ):
            return
        # Landing on the first unreviewed autodetect clip is a courtesy, not
        # part of making the editor usable - and it walks every clip in the
        # project plus the candidate table to work out which one that is. On
        # a real project (dozens of detected plays, all still pending) that
        # ran before the workspace had painted once, so the courtesy was
        # being paid for with the blank window the open is judged by.
        with PerfSpan("focus_pending_autodetect"):
            try:
                if session.active_autodetect_review_batch():
                    self._focus_first_pending_autodetect_clip()
            except Exception:
                log.exception("Could not focus pending autodetect clips")
        with PerfSpan("start_thumbnails"):
            self._start_thumbnails()
        if not read_only:
            with PerfSpan("index_current_project"):
                self._index_current_project()

    def _close_project(self) -> bool:
        """Close the open project. False means closing was cancelled.

        The answer matters: `closeEvent` used to call this, ignore it, and
        accept the close anyway - so declining "cancel the export?" shut the
        window regardless, skipping the save, the index flush and the
        recovery marker on the way out.
        """
        if self.export_worker and self.export_worker.isRunning():
            answer = QMessageBox.question(
                self, "Export running",
                "An export is still running. Cancel it and close the project?")
            if answer != QMessageBox.StandardButton.Yes:
                return False
            self.export_worker.cancel_all()
            self.export_worker.finished.connect(self.export_worker.deleteLater)
            # Bounded, not fire-and-forget. Cancelling terminates the FFmpeg
            # child, so the thread unwinds in well under a second - and this
            # thread is parented to the window, so leaving it running is how
            # a close turns into an abort rather than a close.
            self._join_worker(self.export_worker, ms=5000)
            self.export_worker = None
        if self._coverage_review_dialog is not None:
            self._coverage_review_dialog.close()
            self._coverage_review_dialog = None
        self._stop_background_workers()
        if self.session:
            read_only = bool(getattr(self.session, "read_only", False))
            # Warn on unsaved edits before discarding the session. Edits are
            # persisted eagerly now, so this is a safety net for anything
            # still only in memory (e.g. an in-progress, unapplied inspector
            # edit held in the Clip object but not yet committed).
            if self.session.dirty and not read_only:
                answer = QMessageBox.question(
                    self, "Unsaved changes",
                    "You have changes that were not saved. Save them before "
                    "closing?")
                if answer == QMessageBox.StandardButton.Yes:
                    if not self._try_save():
                        # They asked to keep the work and we could not. Going
                        # on would close the project and lose exactly what
                        # they just said to save, so stop instead.
                        QMessageBox.warning(
                            self, "Could not save",
                            f"TapeSift could not write "
                            f"{self.session.db_path}.\n\nClosing was "
                            "cancelled so nothing is lost. Check that the "
                            "file is not open elsewhere, then try again.")
                        return False
            # Window shells may own project-bound input devices or temporary
            # capture state. Run their synchronous teardown only after every
            # user-refusal/save-failure gate has passed, but before this
            # session's SQLite connection can close.
            self._before_session_close()
            if not read_only:
                self._index_current_project()
            # Flush any debounced index write now so a pending timer doesn't
            # fire after the session is gone, then join the thread.
            if self._index_timer is not None:
                self._index_timer.stop()
            if not read_only:
                self._index_flush()
            else:
                self._index_pending = False
            # The catalog index is written on a daemon thread; join it so a
            # normal close never orphans a half-written library.db.
            if self._index_thread is not None:
                self._index_thread.join(timeout=5)
                self._index_thread = None
            try:
                self.session.close()
            except Exception:
                log.exception("Error while closing project")
            if not read_only:
                recovery_service.mark_closed()
            self.session = None
        self._selected_clip_id = None
        self.player.set_selected_clip_id(None)
        self._load_clip_telestration(None)
        self.rename_action.setEnabled(False)
        self.autosave_timer.stop()
        self.player.unload()
        self.player.set_predicted_snap_state("disabled")
        # Full exit still saves and joins workers, but has no Home page to
        # display. Rebuilding it delayed the native window disappearing.
        if not getattr(self, "_app_closing", False):
            self.setWindowTitle("TapeSift")
            self.start_screen.refresh_recent()
            self.stack.setCurrentWidget(self.start_screen)
        return True

    def _before_session_close(self) -> None:
        """Synchronous shell hook before the active session connection closes."""

    def _try_save(self) -> bool:
        """Save the session, reporting a failure instead of raising.

        A save can fail for reasons that have nothing to do with this code -
        the project file locked by a backup agent or the companion server, a
        full disk, a dropped network share. Both callers are Qt slots, and
        PySide terminates the process on an exception that escapes one, so
        an unreachable file used to take the whole app down. The session
        stays dirty on failure, which means the next autosave retries.
        """
        if self.session is None:
            return False
        try:
            self.session.save()
        except (TapeSiftError, sqlite3.Error, OSError) as exc:
            log.exception("Could not save project %s", self.session.db_path)
            detail = (exc.user_text() if isinstance(exc, TapeSiftError)
                      else str(exc))
            self.statusBar().showMessage(f"Could not save: {detail}", 8000)
            return False
        return True

    def _save_project(self) -> None:
        if not self.session:
            return
        if not self._try_save():
            # An explicit Ctrl+S deserves more than a status line that
            # disappears; an autosave does not, or a locked file would put a
            # modal in front of the user every thirty seconds.
            QMessageBox.warning(
                self, "Could not save",
                f"TapeSift could not write {self.session.db_path}.\n\n"
                "Your work is still open and unsaved. Check that the file is "
                "not open elsewhere and that the drive has space, then try "
                "again.")
            return
        self._index_current_project()
        self.statusBar().showMessage("Project saved", 3000)

    def _autosave(self) -> None:
        if self.session and self.session.dirty:
            if not self._try_save():
                return
            self._index_current_project()
            self.statusBar().showMessage("Autosaved", 2000)

    def _index_current_project(self, delay_ms: int = 250) -> None:
        """Update the cross-project search catalog for the open project.

        The payload is snapshotted here (UI thread, cheap) and written on a
        daemon thread so catalog I/O never touches playback or blocks the UI.
        The thread is tracked so close() can join it.

        Repeated calls (every edit, every autosave) are coalesced: a short
        timer collapses a burst of mutations into a single write, so we never
        spawn overlapping index threads for the same project.
        """
        if not self.session:
            return
        self._index_pending = True
        if self._index_timer is None:
            self._index_timer = QTimer(self)
            self._index_timer.setSingleShot(True)
            self._index_timer.timeout.connect(self._index_flush)
        self._index_timer.start(delay_ms)

    def _index_flush(self) -> None:
        if not self._index_pending or not self.session:
            self._index_pending = False
            return
        self._index_pending = False
        import threading
        project = self.session.project
        rows = library_service.build_index_payload(
            str(self.session.db_path), project.name,
            project.source_video_path, self.session.clips,
            opponent=project.opponent, game_year=project.game_year)
        self._index_thread = threading.Thread(
            target=library_service.write_project_index,
            args=(str(self.session.db_path), rows), daemon=True)
        self._index_thread.start()

    # ---------- review mode: step through clips and log them ----------

    def _clip_row(self, clip_id: str) -> int:
        if not self.session:
            return -1
        for row, clip in enumerate(self.session.clips):
            if clip.id == clip_id:
                return row
        return -1

    def _current_row(self) -> int:
        ids = self.clip_list.selected_clip_ids()
        return self._clip_row(ids[0]) if ids else -1

    def _select_row(self, row: int) -> None:
        if self.session and 0 <= row < len(self.session.clips):
            self.clip_list.select_clip_index(row)

    def _land_on_row(self, row: int) -> None:
        """Select a clip row; the shared selection path handles playback."""
        self._select_row(row)

    def _active_autodetect_candidate_ids(
        self,
        batch: dict,
    ) -> set[str]:
        if not self.session:
            return set()
        return {
            str(candidate["id"])
            for candidate in self.session.autodetect_repo.list_candidates(
                batch["session_id"])
            if int(candidate["created_start_ms"]) >= int(batch["start_ms"])
            and int(candidate["created_end_ms"]) <= int(batch["end_ms"])
        }

    def _clip_is_active_batch_candidate(
        self,
        clip: Clip,
        batch: dict,
        candidate_ids: set[str] | None = None,
    ) -> bool:
        lineage = clip.detection_lineage
        if (
            not lineage
            or lineage.get("session_id") != batch["session_id"]
            or str(lineage.get("derivation", "")).startswith("duplicate")
        ):
            return False
        roots = lineage.get("candidate_ids", [])
        if isinstance(roots, str):
            roots = [roots]
        owned = (
            candidate_ids
            if candidate_ids is not None
            else self._active_autodetect_candidate_ids(batch)
        )
        return bool(owned.intersection(str(root) for root in roots))

    def _active_autodetect_batch_clips(
        self,
        *,
        pending_only: bool = False,
    ) -> list[Clip]:
        if not self.session:
            return []
        batch = self.session.active_autodetect_review_batch()
        if batch is None:
            return []
        candidate_ids = self._active_autodetect_candidate_ids(batch)
        members: list[Clip] = []
        for clip in self.session.clips:
            lineage = clip.detection_lineage
            is_candidate = self._clip_is_active_batch_candidate(
                clip, batch, candidate_ids)
            is_recovery = (
                lineage.get("batch_id") == batch["id"]
                and bool(lineage.get("recovery_id"))
                and not str(
                    lineage.get("derivation", "")
                ).startswith("duplicate")
            )
            if not (is_candidate or is_recovery):
                continue
            if pending_only:
                if is_candidate:
                    terminal = (
                        lineage.get("review_batch_id") == batch["id"]
                        and (
                            bool(clip.enabled and lineage.get("reviewed_at"))
                            or bool(
                                not clip.enabled
                                and lineage.get(
                                    "false_positive_confirmed_at")
                            )
                        )
                    )
                else:
                    terminal = (
                        bool(lineage.get("recovery_withdrawn_at"))
                        or bool(clip.enabled and lineage.get("reviewed_at"))
                    )
                if terminal:
                    continue
            members.append(clip)
        return members

    def _expanded_autodetect_review_clip_ids(
        self,
        clip_ids: list[str],
    ) -> list[str]:
        """Offer to review every enabled section linked to one candidate."""
        selected = list(dict.fromkeys(clip_ids))
        if not self.session:
            return selected
        batch = self.session.active_autodetect_review_batch()
        if batch is None:
            return selected
        owned_candidate_ids = self._active_autodetect_candidate_ids(batch)
        selected_roots: set[str] = set()
        for clip in self.session.clips:
            if clip.id not in selected:
                continue
            roots = clip.detection_lineage.get("candidate_ids", [])
            if isinstance(roots, str):
                roots = [roots]
            selected_roots.update(
                str(root) for root in roots
                if str(root) in owned_candidate_ids
            )
        if not selected_roots:
            return selected

        related = []
        for clip in self.session.clips:
            lineage = clip.detection_lineage
            roots = lineage.get("candidate_ids", [])
            if isinstance(roots, str):
                roots = [roots]
            if (
                clip.enabled
                and not str(
                    lineage.get("derivation", "")
                ).startswith("duplicate")
                and selected_roots.intersection(
                    str(root) for root in roots)
            ):
                related.append(clip.id)
        if set(related) <= set(selected):
            return selected

        answer = QMessageBox.question(
            self,
            "Review all linked sections?",
            f"This detector candidate now has {len(related)} enabled "
            "sections. Mark all of them reviewed together?\n\n"
            "Choose Yes only after checking every section.",
        )
        return (
            related
            if answer == QMessageBox.StandardButton.Yes
            else selected
        )

    def _pending_autodetect_advance_order(
        self,
        clip_ids: list[str],
    ) -> list[str] | None:
        """Remember a row-based successor before a decision hides its row."""
        if (
            not self.session
            or self.clip_list.review_filter_mode != "autodetect_pending"
            or self.session.active_autodetect_review_batch() is None
        ):
            return None
        pending_ids = [
            clip.id for clip in
            self._active_autodetect_batch_clips(pending_only=True)
        ]
        acted = set(clip_ids)
        positions = [
            index for index, clip_id in enumerate(pending_ids)
            if clip_id in acted
        ]
        if not positions:
            return [
                clip_id for clip_id in pending_ids
                if clip_id not in acted
            ]
        anchor = max(positions)
        wrapped = pending_ids[anchor + 1:] + pending_ids[:anchor + 1]
        return [clip_id for clip_id in wrapped if clip_id not in acted]

    def _advance_pending_autodetect_review(
        self,
        preferred_ids: list[str] | None,
    ) -> int | None:
        """Select and play the next unresolved row after a review decision."""
        if preferred_ids is None:
            return None
        pending = self._active_autodetect_batch_clips(pending_only=True)
        if not pending:
            self.player.clear_clip_range()
            self.clip_list.show_review_filter("all")
            return 0
        pending_by_id = {clip.id: clip for clip in pending}
        target_id = next(
            (
                clip_id for clip_id in preferred_ids
                if clip_id in pending_by_id
            ),
            pending[0].id,
        )
        self.clip_list.update_review_filter_scope(set(pending_by_id))
        self.clip_list.select_clip_id(target_id, reveal=False)
        return len(pending)

    def _focus_first_pending_autodetect_clip(self) -> bool:
        pending = self._active_autodetect_batch_clips(pending_only=True)
        if not pending:
            return False
        self.clip_list.show_review_filter(
            "autodetect_pending",
            clip_ids={clip.id for clip in pending},
        )
        self.clip_list.select_clip_id(pending[0].id, reveal=False)
        self.statusBar().showMessage(
            f"{len(pending)} autodetect correction"
            f"{'s' if len(pending) != 1 else ''} still need review.",
            5000,
        )
        return True

    def _goto_edge(self, first: bool) -> None:
        """Ctrl+Home / Ctrl+End - jump to the first / last clip (3.1)."""
        if not self.session or not self.session.clips:
            return
        pending_only = (
            self.clip_list.review_filter_mode == "autodetect_pending")
        batch_clips = (
            self._active_autodetect_batch_clips(pending_only=True)
            if pending_only
            else []
        )
        if pending_only and batch_clips:
            target = batch_clips[0] if first else batch_clips[-1]
            self.clip_list.select_clip_id(
                target.id, reveal=False)
            return
        if pending_only and self.session.active_autodetect_review_batch():
            self.statusBar().showMessage(
                "No autodetect corrections are pending.", 3000)
            return
        self._land_on_row(0 if first else len(self.session.clips) - 1)

    def _goto_clip(self, delta: int) -> None:
        """Ctrl+Down / Ctrl+Up - works even while typing in the inspector."""
        if not self.session or not self.session.clips:
            return
        pending_only = (
            self.clip_list.review_filter_mode == "autodetect_pending")
        batch_clips = (
            self._active_autodetect_batch_clips(pending_only=True)
            if pending_only
            else []
        )
        if pending_only and batch_clips:
            ids = [clip.id for clip in batch_clips]
            selected = self.clip_list.selected_clip_ids()
            current_id = selected[0] if selected else ""
            if current_id not in ids:
                target = batch_clips[0] if delta > 0 else batch_clips[-1]
                self.clip_list.select_clip_id(
                    target.id, reveal=False)
                return
            target_index = ids.index(current_id) + delta
            if not 0 <= target_index < len(batch_clips):
                self.statusBar().showMessage(
                    "Start of autodetect test batch"
                    if target_index < 0
                    else "End of autodetect test batch",
                    3000,
                )
                return
            self.clip_list.select_clip_id(
                batch_clips[target_index].id, reveal=False)
            return
        if pending_only and self.session.active_autodetect_review_batch():
            self.statusBar().showMessage(
                "No autodetect corrections are pending.", 3000)
            return
        row = self._current_row()
        target = 0 if row < 0 else row + delta
        if not (0 <= target < len(self.session.clips)):
            self.statusBar().showMessage(
                "Start of clip list" if target < 0 else "End of clip list", 3000)
            return
        self._land_on_row(target)

    def _goto_next_unlogged(self) -> None:
        """Ctrl+Shift+Down - resume where the logging left off."""
        if not self.session:
            return
        start = self._current_row() + 1
        for row in range(max(0, start), len(self.session.clips)):
            if needs_logging(self.session.clips[row]):
                self._select_row(row)
                return
        self.statusBar().showMessage("No unlogged clips after this one", 4000)

    def _resume_project(self, path: str) -> None:
        """Home-screen Resume: open the film and land on the first clip
        that still needs details, already in review mode."""
        self._open_project(path)
        if not self.session:
            return
        if not self.settings.review_mode:
            self._toggle_review_mode()
        first = next((row for row, clip in enumerate(self.session.clips)
                      if needs_logging(clip)), None)
        if first is None:
            self.statusBar().showMessage("Every clip is already logged", 4000)
            return
        self._select_row(first)
        remaining = sum(needs_logging(c) for c in self.session.clips)
        self.statusBar().showMessage(
            f"Resuming - {remaining} clip{'s' if remaining != 1 else ''} "
            "still to log", 6000)

    def _toggle_details_panel(self) -> None:
        """Open or close Play details on the selected clip."""
        open_now = self.clip_editor.toggle_details()
        self.details_action.setChecked(open_now)
        self.statusBar().showMessage(
            "Play details open - Ctrl+I closes it" if open_now
            else "Play details closed", 3000)

    def _toggle_review_mode(self) -> None:
        self.settings.review_mode = not self.settings.review_mode
        self.review_action.setChecked(self.settings.review_mode)
        self.settings.save()
        state = "on" if self.settings.review_mode else "off"
        self._update_review_label()
        if self.settings.review_mode:
            self._enter_current_clip()
        else:
            self.player.clear_clip_range()
        self.statusBar().showMessage(
            f"Review mode {state} - Ctrl+Down/Up moves between clips, "
            "Enter saves and advances", 6000)

    def _toggle_loop(self) -> None:
        self.settings.review_loop = not self.settings.review_loop
        self.settings.save()
        self.player.set_range_loop(self.settings.review_loop)
        self.statusBar().showMessage(
            f"Loop {'on' if self.settings.review_loop else 'off'}", 3000)
        self._update_review_label()

    def _replay_clip(self) -> None:
        if not self._typing_in_text_field():
            self.player.replay_range()

    def _enter_current_clip(self) -> None:
        """Landing on a clip in review mode: play it, ready the inspector."""
        if not (self.session and self.settings.review_mode):
            return
        ids = self.clip_list.selected_clip_ids()
        if not ids:
            return
        clip = self.session.get_clip(ids[0])
        if clip is None:
            return
        if self.settings.review_autoplay and \
                Path(self.session.project.source_video_path).is_file():
            self.player.play_clip_range(clip.start_ms, clip.end_ms,
                                        self.settings.review_loop)
        # Details live in the side inspector, never over the video.
        self.clip_editor.begin_review_entry()
        self.details_action.setChecked(self.clip_editor.details_are_open())

    def _goto_unlogged(self, delta: int) -> None:
        """Jump to the next play that still needs logging.

        Save + Next advances to the literal next clip, which is right when
        you are working straight through and useless when you are cleaning
        up what a detector left behind. The filter could already show them;
        this is the missing half - a way to move between them.
        """
        if not self.session or not self.session.clips:
            return
        clips = self.session.clips
        row = self._current_row()
        start = 0 if row < 0 else row
        for step in range(1, len(clips) + 1):
            index = start + delta * step
            if not 0 <= index < len(clips):
                break
            if needs_logging(clips[index]):
                self._land_on_row(index)
                return
        # Say what is left rather than just refusing to move.
        remaining = sum(needs_logging(clip) for clip in clips)
        if not remaining:
            message = "Every play is logged."
        elif delta > 0:
            message = (
                f"No unlogged play after this one - "
                f"{remaining} earlier. Shift+U goes back.")
        else:
            message = (
                f"No unlogged play before this one - {remaining} later.")
        self.statusBar().showMessage(message, 4000)

    def _advance_after_save(self) -> None:
        if self.settings.review_mode and self.settings.review_auto_advance:
            self._goto_clip(1)

    def _nudge_in(self) -> None:
        self._nudge_bound(start=True)

    def _nudge_out(self) -> None:
        self._nudge_bound(start=False)

    def _nudge_bound(self, start: bool) -> None:
        """[ and ] retime the selected clip to the playhead."""
        if not self.session or self._typing_in_text_field():
            return
        ids = self.clip_list.selected_clip_ids()
        if not ids:
            return
        clip = self.session.get_clip(ids[0])
        if clip is None:
            return
        pos = self.player.position_ms()
        if start:
            if pos >= clip.end_ms:
                self.statusBar().showMessage(
                    "In-point must come before the out-point", 4000)
                return
        else:
            if pos <= clip.start_ms:
                self.statusBar().showMessage(
                    "Out-point must come after the in-point", 4000)
                return
        self.session.trim_clip_boundary(
            clip.id, "start" if start else "end", pos,
            minimum_duration_ms=1,
        )
        self._sync_pending_autodetect_clip_range(clip)
        self._refresh_clip_list()
        self._select_row(self._clip_row(clip.id))
        self.statusBar().showMessage(
            f"{'In' if start else 'Out'}-point set to "
            f"{format_ms(pos, show_millis=True)}", 3000)

    def _dismiss_clip(self) -> None:
        """N - exclude this clip from export and move on."""
        if not self.session or self._typing_in_text_field():
            return
        ids = self.clip_list.selected_clip_ids()
        if not ids:
            return
        clip = self.session.get_clip(ids[0])
        if clip is None:
            return
        self.session.set_clip_enabled(clip.id, False)
        row = self._clip_row(clip.id)
        self._refresh_clip_list()
        self._select_row(min(row + 1, len(self.session.clips) - 1))
        self.statusBar().showMessage(
            f"'{clip.clip_title or 'Clip'}' excluded from export", 3000)

    def _update_review_label(self) -> None:
        if not self.session or not self.session.clips:
            self.review_label.setText("")
            return
        total = len(self.session.clips)
        row = self._current_row()
        unlogged = sum(needs_logging(c) for c in self.session.clips)
        first_read_calls = unresolved_count(self.session.clips)
        bits = []
        if self.settings.review_mode:
            bits.append("REVIEW")
        if self.settings.review_loop:
            bits.append("LOOP")
        prefix = " · ".join(bits)
        position = f"Clip {row + 1} / {total}" if row >= 0 else f"{total} clips"
        text = f"{position} · {unlogged} unlogged"
        if first_read_calls:
            noun = "call" if first_read_calls == 1 else "calls"
            text += f" · {first_read_calls} First Read {noun}"
        self.review_label.setText(f"{prefix} · {text}" if prefix else text)

    # ---------- automatic play detection ----------

    def _first_read(self) -> None:
        """Suggest run or pass on every detected play.

        The one feature that leaves this machine, so it checks consent
        before it checks anything else convenient.
        """
        if not self._require_session():
            return
        session = self.session
        if getattr(session, "read_only", False):
            QMessageBox.information(
                self, "Read-only project",
                "Open a writable project before running First Read.")
            return
        if not self.settings.first_read_enabled:
            QMessageBox.information(
                self, "First Read is off",
                "Turn First Read on in Settings and add your API key. It is "
                "the only feature that contacts the network, so it stays off "
                "until you enable it.")
            return
        if not self.settings.first_read_api_key:
            QMessageBox.information(
                self, "First Read needs your API key",
                "Add your OpenRouter key in Settings. Frames go from this "
                "machine to your provider; the key is never written into a "
                "project or an export.")
            return
        project = self.session.project
        if not project.has_source:
            QMessageBox.information(self, "No video loaded",
                                    "Load a video first.")
            return
        source = Path(project.source_video_path)
        if not source.is_file():
            QMessageBox.warning(self, "Source missing",
                                "Relink the source video first.")
            return
        clips = list(self.session.clips)
        if not clips:
            QMessageBox.information(
                self, "No plays yet",
                "Run Detect Plays first, or add clips by hand. First Read "
                "reads plays that already exist.")
            return
        if getattr(self, "_first_read_worker", None) is not None:
            QMessageBox.information(
                self, "First Read is already running",
                "Wait for the current run to finish, or stop it.")
            return

        selected = first_read_batch.plan(clips)
        if not selected.count:
            QMessageBox.information(
                self, "Nothing left to read", selected.describe())
            return
        confirm = FirstReadConfirm(selected, project.name or "this game", parent=self)
        if confirm.exec() != QDialog.DialogCode.Accepted:
            return
        if self.session is not session or Path(project.source_video_path) != source:
            return

        scratch = Path(self.session.db_path).parent / "first-read"
        worker = FirstReadWorker(
            clips, ffmpeg_path=self.settings.ffmpeg_path, source=source,
            scratch_dir=scratch, api_key=self.settings.first_read_api_key,
            model=self.settings.first_read_model, parent=self)
        dialog = FirstReadDialog(selected.count, self)
        self._first_read_worker = worker
        self._first_read_dialog = dialog
        self._first_read_context = (
            session, str(source),
            {clip.id: (clip.start_ms, clip.end_ms) for clip in clips})

        worker.result_ready.connect(self._store_first_read)
        worker.progressed.connect(dialog.show_progress)
        worker.finished_batch.connect(dialog.finish)
        worker.finished_batch.connect(self._first_read_finished)
        worker.finished.connect(self._first_read_thread_finished)
        dialog.stop_requested.connect(worker.stop)
        worker.start()
        dialog.show()

    def _store_first_read(self, clip_id: str, analysis_json: str) -> None:
        """Save one suggestion. It is analysis, never a label."""
        if self.sender() is not getattr(self, "_first_read_worker", None):
            return
        context = getattr(self, "_first_read_context", None)
        if context is None:
            return
        session, source, ranges = context
        if (self.session is not session or getattr(session, "read_only", False)
                or Path(session.project.source_video_path) != Path(source)):
            return
        clip = session.get_clip(clip_id)
        if clip is None or (clip.start_ms, clip.end_ms) != ranges.get(clip_id):
            return
        read = first_read_service.load(analysis_json)
        if read is None:
            return
        # Find Snap may have updated analysis while this request was in flight.
        clip = session.store_clip_analysis(
            clip_id, first_read_service.store(clip.analysis_json(), read))
        if clip is None:
            return
        project = self.session.project
        self.clip_list.update_row(
            clip, project.source_duration_ms, project.naming_template,
            project.name, self.settings.separator_style)
        if (self.clip_list.selected_clip_ids() == [clip_id]
                and self.clip_editor._clip is clip):
            self.clip_editor._refresh_first_read_advisory()
        self._update_review_label()

    def _first_read_finished(self, summary) -> None:
        if self.sender() is not getattr(self, "_first_read_worker", None):
            return
        self.statusBar().showMessage(summary.describe(), 12_000)
        self._update_review_label()

    def _first_read_thread_finished(self) -> None:
        worker = self.sender()
        if worker is getattr(self, "_first_read_worker", None):
            self._first_read_worker = None
            self._first_read_context = None
        worker.deleteLater()

    def _detect_plays(self) -> None:
        # Both the menu and ledger enter here; pythonw has no visible stderr.
        try:
            self._run_detection_workflow()
        except (TapeSiftError, sqlite3.Error, OSError) as exc:
            log.exception("Could not complete detection")
            detail = (exc.user_text() if isinstance(exc, TapeSiftError)
                      else str(exc))
            QMessageBox.warning(
                self, "Could not complete detection",
                f"{detail}\n\n"
                "Check that the project is writable and the drive has space, "
                "then try again. Details are in Help → Open Logs.")

    def _make_play_detect_dialog(self, source: Path, duration_ms: int):
        return PlayDetectDialog(self.settings.ffmpeg_path, source, duration_ms, self)

    def _run_detection_workflow(self) -> None:
        if not self._require_session():
            return
        if self.session.active_autodetect_review_batch() is not None:
            QMessageBox.information(
                self,
                "Finish the active test batch",
                "Finish the current autodetect test batch before running "
                "detection again.",
            )
            return
        project = self.session.project
        if not project.has_source:
            QMessageBox.information(self, "No video loaded",
                                    "Load a video first.")
            return
        source = Path(project.source_video_path)
        if not source.is_file():
            QMessageBox.warning(self, "Source missing",
                                "Relink the source video first.")
            return
        # Prefer the proxy: same timeline, several times faster to decode.
        output_folder = Path(project.output_folder or self.session.db_path.parent)
        analysis_source = proxy_service.find_ready_proxy(source, output_folder) \
            or source

        dialog = self._make_play_detect_dialog(analysis_source, project.source_duration_ms)
        if dialog.exec() != PlayDetectDialog.DialogCode.Accepted:
            return
        if dialog.result is None:
            return
        candidates = dialog.play_candidates()
        kept_keys = {(c["candidate_kind"], c["candidate_index"]) for c in candidates}
        all_keys = {(kind, index)
                    for kind, rows in (("play", dialog.result.plays),
                                       ("unclassified", dialog.result.unclassified))
                    for index in range(len(rows))}
        selection = {"schema_version": 1,
                     "kept": [list(key) for key in sorted(kept_keys)],
                     "dismissed": [list(key) for key in sorted(all_keys - kept_keys)]}

        defaults = self._clip_defaults()
        defaults.pre_roll_ms = 0      # detected boundaries are already framed
        defaults.post_roll_ms = 0
        clips, clamped, flagged = [], False, 0
        for index, candidate in enumerate(candidates, start=1):
            clip, was_clamped = clip_from_range(
                candidate["created_start_ms"],
                candidate["created_end_ms"],
                "",
                self._duration_ms(),
                defaults,
            )
            clip.clip_title = f"Play {index:03d}"
            if candidate["needs_review"]:
                # Both a visible prefix and a tag: the prefix so it stands out
                # in the list, the tag so it can be filtered and cleared in
                # bulk once checked.
                clip.clip_title = f"{REVIEW_PREFIX} {clip.clip_title}"
                clip.tags = [*clip.tags, REVIEW_TAG]
                flagged += 1
            clips.append(clip)
            clamped |= was_clamped

        def source_identity(path: Path) -> dict:
            try:
                stat = path.stat()
            except OSError:
                return {"path": str(path)}
            return {
                "path": str(path),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }

        self.session.add_detected_clips(
            clips,
            candidates,
            detector_id=autodetect_capture_service.DETECTOR_ID,
            detector_version=autodetect_capture_service.DETECTOR_VERSION,
            app_version=__version__,
            source={
                "source": source_identity(source),
                "analysis_source": source_identity(analysis_source),
                "duration_ms": project.source_duration_ms,
            },
            parameters=dialog.capture_parameters(),
            result=autodetect_capture_service.serialize_detection_result(
                dialog.result),
            ui_options={
                "first_camera_angle_only":
                    dialog.wide_only_check.isChecked(),
                # The partition distinguishes deliberate Keep from legacy
                # blanket materialization and survives later project activation.
                "candidate_selection": selection,
                "unclassified_materialized": any(kind == "unclassified" for kind, _ in kept_keys),
            },
            runtime_seconds=dialog.runtime_seconds,
            provenance={
                "strength": "advisory",
                "detector_source_sha256":
                    autodetect_capture_service.detector_source_sha256(),
                "source_content_sha256": "",
                "analysis_content_sha256": "",
                "ffmpeg_version": ffmpeg_service.get_version(
                    self.settings.ffmpeg_path),
            },
        )
        admission = self.session.last_detection_admission()
        added_clips = [
            clip
            for clip_id in admission["added_clip_ids"]
            if (clip := self.session.get_clip(clip_id)) is not None
        ]
        if added_clips:
            self._after_clips_added(added_clips, clamped)
        else:
            self._refresh_clip_list()
        reused = len(admission["reused_clip_ids"])
        ignored = sum(kind == "unclassified" for kind, _ in all_keys - kept_keys)
        note = (f" {flagged} need checking - filter on '{REVIEW_TAG}'."
                if flagged else "")
        if ignored:
            note += (
                f" {ignored} unclassified source fragment"
                f"{'s' if ignored != 1 else ''} stayed off the Clips list "
                "and remain recoverable on the timeline.")
        self.statusBar().showMessage(
            f"Added {len(added_clips)} new detected clips; reused {reused} "
            "exact existing range"
            f"{'s' if reused != 1 else ''} without duplicating." + note +
            " Select one and start logging details. After watching clips, "
            "right-click the selection and mark it reviewed.", 15000)
        if dialog.result is not None:
            self._review_merged_clips(
                dialog.result,
                eligible_clip_ids={clip.id for clip in added_clips},
            )
            self._refresh_timeline_presentation()

    def _review_merged_clips(
        self,
        result,
        eligible_clip_ids: set[str] | None = None,
    ) -> None:
        """Offer to split detected clips that look like several plays."""
        if eligible_clip_ids is not None and not eligible_clip_ids:
            return
        suspects = play_detect_service.find_merged_plays(result)
        if not suspects:
            return
        dialog = MergedClipsDialog(suspects, self)
        dialog.preview_requested.connect(self.player.seek_to)
        if dialog.exec() != MergedClipsDialog.DialogCode.Accepted:
            return
        chosen = dialog.chosen()
        if not chosen:
            return
        # Work from the end so earlier splits don't shift later ones.
        made = 0
        for suspect in sorted(chosen, key=lambda s: s.start_ms, reverse=True):
            for point in sorted(suspect.split_points_ms, reverse=True):
                clip = next((
                    c for c in self.session.clips
                    if c.start_ms <= point < c.end_ms
                    and (
                        eligible_clip_ids is None
                        or c.id in eligible_clip_ids
                    )
                ), None)
                if clip is None:
                    continue
                try:
                    if self.session.split_clip(clip.id, point):
                        made += 1
                except TapeSiftError:
                    continue
        self._refresh_clip_list()
        self.statusBar().showMessage(
            f"Split {made} clip(s) - Ctrl+Z to undo", 6000)

    def _show_coverage_review(self) -> None:
        """Open the everyday queue for uncertain clips and source gaps."""
        if not self._require_session():
            return
        queue = self.session.autodetect_coverage_review_queue()
        if queue is None:
            QMessageBox.information(
                self,
                "No detection coverage to review",
                "Run Detect Plays on this film first. Older detection runs "
                "without a full-source coverage ledger cannot be reviewed "
                "here.",
            )
            return
        if self._coverage_review_dialog is None:
            dialog = CoverageReviewDialog(queue, self)
            dialog.inspect_requested.connect(
                self._inspect_coverage_review_item)
            dialog.create_clip_requested.connect(
                self._create_possible_missed_review_clip)
            dialog.dismiss_requested.connect(
                self._dismiss_possible_missed)
            dialog.restore_requested.connect(
                self._restore_possible_missed)
            dialog.accept_clip_requested.connect(
                self._accept_coverage_candidate)
            dialog.reject_clip_requested.connect(
                self._reject_coverage_candidate)
            self._coverage_review_dialog = dialog
        else:
            self._coverage_review_dialog.refresh(queue)
        self._coverage_review_dialog.show()
        self._coverage_review_dialog.raise_()
        self._coverage_review_dialog.activateWindow()

    def _refresh_coverage_review_dialog(self) -> None:
        """Keep an open or hidden queue synchronized with editor actions."""
        if self._coverage_review_dialog is None or not self.session:
            return
        queue = self.session.autodetect_coverage_review_queue()
        if queue is not None:
            self._coverage_review_dialog.refresh(queue)

    def _coverage_review_item(
            self, segment_index: int) -> dict | None:
        if not self.session:
            return None
        queue = self.session.autodetect_coverage_review_queue()
        if queue is None:
            return None
        return next((
            item for item in queue["items"]
            if item.get("item_kind") == "possible_missed"
            and int(item.get("segment_index", -1)) == int(segment_index)
        ), None)

    def _inspect_coverage_review_item(
            self, start_ms: int, end_ms: int, clip_id: str) -> None:
        """Select a linked clip and expose its exact source interval."""
        if not self.session:
            return
        if clip_id and self.session.get_clip(clip_id) is not None:
            self.clip_list.select_clip_id(clip_id)
        duration = max(0, end_ms - start_ms)
        context_ms = max(5_000, min(15_000, duration * 2))
        self.player.focus_source_range(
            start_ms,
            end_ms,
            context_ms=context_ms,
            seek_center=True,
        )
        self.statusBar().showMessage(
            f"Inspecting source {format_ms(start_ms)} to "
            f"{format_ms(end_ms)}", 5000)

    def _create_possible_missed_review_clip(
            self, segment_index: int) -> None:
        """Preserve a possible miss as an exact, undoable editing clip."""
        if not self.session:
            return
        item = self._coverage_review_item(segment_index)
        if item is None or item.get("status") != "pending":
            self._refresh_coverage_review_dialog()
            return
        start_ms = int(item["start_ms"])
        end_ms = int(item["end_ms"])
        defaults = self._clip_defaults()
        defaults.pre_roll_ms = 0
        defaults.post_roll_ms = 0
        clip, clamped = clip_from_range(
            start_ms,
            end_ms,
            "",
            self._duration_ms(),
            defaults,
        )
        clip.clip_title = (
            f"{REVIEW_PREFIX} Possible missed {format_ms(start_ms)}")
        clip.tags = [*clip.tags, REVIEW_TAG, "coverage-review"]
        try:
            self.session.add_possible_missed_review_clip(
                segment_index, clip)
        except TapeSiftError as exc:
            QMessageBox.information(
                self, "Could not create review clip", exc.user_text())
            self._refresh_coverage_review_dialog()
            return
        self._after_clips_added([clip], clamped)
        self.player.focus_source_range(
            start_ms, end_ms, context_ms=10_000, seek_center=True)
        self._refresh_coverage_review_dialog()
        self.statusBar().showMessage(
            "Created a full-range review clip. Ctrl+Z restores the amber "
            "gap.", 6000)

    def _dismiss_possible_missed(self, segment_index: int) -> None:
        """Record a reversible editor decision without deleting footage."""
        if not self.session:
            return
        try:
            changed = self.session.dismiss_possible_missed(segment_index)
        except TapeSiftError as exc:
            QMessageBox.information(
                self, "Could not resolve source gap", exc.user_text())
            self._refresh_coverage_review_dialog()
            return
        self._refresh_timeline_presentation()
        self._refresh_coverage_review_dialog()
        if changed:
            self.statusBar().showMessage(
                "Marked as not a play. Restore to Pending remains available.",
                5000,
            )

    def _restore_possible_missed(self, segment_index: int) -> None:
        """Return a resolved source gap to the active amber queue."""
        if not self.session:
            return
        try:
            changed = self.session.restore_possible_missed(segment_index)
        except TapeSiftError as exc:
            QMessageBox.information(
                self, "Could not restore source gap", exc.user_text())
            self._refresh_coverage_review_dialog()
            return
        self._refresh_timeline_presentation()
        self._refresh_coverage_review_dialog()
        if changed:
            self.statusBar().showMessage(
                "Restored the source gap to pending review.", 4000)

    def _accept_coverage_candidate(self, clip_id: str) -> None:
        """Use the existing provenance-safe review action for a candidate."""
        self._mark_detection_reviewed([clip_id])
        self._refresh_coverage_review_dialog()

    def _reject_coverage_candidate(self, clip_id: str) -> None:
        """Confirm that an uncertain detector proposal contains no play."""
        self._confirm_detection_false_positive([clip_id])
        self._refresh_coverage_review_dialog()

    # ---------- scrub proxy ----------

    def _load_preview_source(
            self, source: Path, frame_rate: float = 30.0) -> None:
        """Load the best available preview without a startup source swap.

        A ready proxy is the preferred first source. Loading the original and
        immediately hot-swapping it created an asynchronous race: clip
        selection could request playback while the proxy was loading, then
        the swap's saved paused state would arrive late and cancel playback.
        Hot-swapping remains available for proxies that finish later.
        """
        ready: Path | None = None
        if self.session and self.settings.ffmpeg_path:
            output_folder = Path(
                self.session.project.output_folder
                or self.session.db_path.parent)
            ready = proxy_service.find_ready_proxy(source, output_folder)
        if ready is not None:
            self.player.load(ready, frame_rate)
            self._set_preview_source_label("optimized")
            self.statusBar().showMessage(
                "Preview: scrub-optimized copy", 4000)
            return
        self.player.load(source, frame_rate)
        self._setup_preview_proxy(source)

    def _setup_preview_proxy(self, source: Path, force: bool = False) -> None:
        """Use the scrub-optimized preview copy; build it only when asked.

        An existing proxy is free to use, so it is swapped in automatically.
        Building one is a full transcode that competes with the preview
        decoder, so it happens only on demand (or if auto-build is enabled).
        """
        if not (self.session and self.settings.ffmpeg_path):
            return
        self._proxy_retry_timer.stop()
        if self.proxy_worker and self.proxy_worker.isRunning() and self.proxy_worker.source == source:
            return
        output_folder = Path(self.session.project.output_folder or
                             self.session.db_path.parent)
        ready = proxy_service.find_ready_proxy(source, output_folder)
        if ready and not force:
            self.proxy_banner_widget.hide()
            self.player.swap_source(ready)
            self._set_preview_source_label("optimized")
            self.statusBar().showMessage("Preview: scrub-optimized copy", 4000)
            return
        if not ready:
            self._set_preview_source_label("original")
        if not (force or self.settings.scrub_proxy_enabled):
            self.proxy_banner_widget.hide()
            return
        self._stop_proxy_worker()
        # One heavy job at a time - concurrent encodes were what made
        # scrubbing choppy before.
        if background_service.is_busy() or (self.proxy_worker and self.proxy_worker.isRunning()):
            if not force:
                self._proxy_retry_timer.start(2000)
                self.statusBar().showMessage("Smooth-scrub preview queued until background work finishes", 4000)
                return
            QMessageBox.information(
                self, "Background work in progress",
                "Another background job is still running.\nLet it finish, "
                "then build this preview.")
            return
        if ready:
            # Release Windows' handle to the old preview. The worker keeps its
            # bytes until the replacement passes decode and timing checks.
            self.player.swap_source(source)
        hw = ffmpeg_service.detect_hw_encoders(self.settings.ffmpeg_path)
        self._last_proxy_pct = -10
        worker = ProxyWorker(
            self.settings.ffmpeg_path, source, output_folder,
            self.session.project.source_duration_ms, hw, self)
        worker.progress.connect(self._proxy_progress)
        worker.proxy_ready.connect(self._proxy_ready)
        worker.failed.connect(
            lambda msg, worker=worker: self._proxy_failed(worker, msg))
        worker.finished.connect(
            lambda worker=worker: self._proxy_worker_finished(worker))
        self.proxy_worker = worker
        worker.start()
        self.proxy_banner_widget.hide()
        self._set_preview_source_label("building")
        self.statusBar().showMessage(
            "Building smooth-scrub preview in the background", 6000)

    def _retry_auto_preview(self) -> None:
        if self.settings.scrub_proxy_enabled and self.session and self.session.project.has_source:
            source = Path(self.session.project.source_video_path)
            if source.is_file():
                self._setup_preview_proxy(source)

    def _set_preview_source_label(self, mode: str) -> None:
        """mode: optimized | original | building"""
        text, role, tip = {
            "optimized": ("Preview: optimized", "subtle",
                          "Seeking is fast on this film."),
            "original": ("Preview: original - scrubbing will be choppy",
                         "warning",
                         "Build a smooth-scrub preview to fix this."),
            "building": ("◐ Optimizing preview…", "subtle",
                         "Builds in the background. Processing time depends on the film and hardware."),
        }.get(mode, ("", "subtle", ""))
        self.preview_source_label.setText(text)
        self.preview_source_label.setProperty("role", role)
        self.preview_source_label.setToolTip(tip)
        # Re-polish so the role-based colour actually updates.
        self.preview_source_label.style().unpolish(self.preview_source_label)
        self.preview_source_label.style().polish(self.preview_source_label)

    def _offer_proxy_build(self, source: Path) -> None:
        """Long-GOP HD film seeks slowly; offer the one-click fix, once."""
        if getattr(self, "_proxy_offered_for", None) == str(source):
            return
        self._proxy_offered_for = str(source)
        self.proxy_banner.setText(
            "  Scrubbing this film will be choppy (large HD file). "
            "Build a smooth-scrub preview? ")
        self.proxy_banner_widget.show()

    def _dismiss_proxy_banner(self) -> None:
        self.proxy_banner_widget.hide()

    def _build_proxy_on_demand(self) -> None:
        """Playback > Build Smooth-Scrub Preview."""
        if not self.session or not self.session.project.has_source:
            QMessageBox.information(self, "No video loaded",
                                    "Load a video first.")
            return
        source = Path(self.session.project.source_video_path)
        if not source.is_file():
            QMessageBox.warning(self, "Source missing",
                                "Relink the source video first.")
            return
        if self.proxy_worker and self.proxy_worker.isRunning():
            QMessageBox.information(
                self, "Already building",
                "A smooth-scrub preview is already being built for this video.")
            return
        dialog = ActionDialog(
            title="Build smooth-scrub preview",
            subtitle="Create a faster local preview for this film.",
            eyebrow="PLAYBACK  /  OPTIMIZE",
            body=(
                "TapeSift will encode a lightweight 720p copy so rewind, "
                "seeking, and high-speed shuttle stay smooth.\n\n"
                "This runs in the background at low priority, but a long game "
                "can take several minutes and use some CPU. Your original film "
                "is never changed."
            ),
            confirm_text="Build preview",
            parent=self,
        )
        if dialog.exec() == ActionDialog.DialogCode.Accepted:
            self.proxy_banner_widget.hide()
            self._setup_preview_proxy(source, force=True)

    def _use_original_preview(self) -> None:
        """Switch the preview back to the untouched source file."""
        if not self.session or not self.session.project.has_source:
            return
        self._stop_proxy_worker()
        source = Path(self.session.project.source_video_path)
        if source.is_file():
            self.player.swap_source(source)
            self.statusBar().showMessage("Preview: original video", 4000)

    def _proxy_progress(self, pct: float) -> None:
        if pct - self._last_proxy_pct >= 10:  # status bar only every 10%
            self._last_proxy_pct = pct
            self.statusBar().showMessage(
                f"Optimizing preview for smooth scrubbing… {pct:.0f}%", 4000)

    def _proxy_ready(self, source: str, proxy: str) -> None:
        # Only swap if that video is still what the player is showing.
        if self.session and self.session.project.source_video_path == source:
            self.player.swap_source(Path(proxy))
            self.proxy_banner_widget.hide()
            self._set_preview_source_label("optimized")
            self.statusBar().showMessage(
                "Preview optimized - scrubbing is now smooth at all speeds", 8000)

    def _stop_proxy_worker(self) -> None:
        self._proxy_retry_timer.stop()
        worker = self.proxy_worker
        if worker is None:
            return
        if worker.isRunning():
            worker.cancel()
            # Keep the reference until finished. Clearing it here allowed a
            # second proxy worker to start while the cancelled encode was
            # still draining.
            return
        self._proxy_worker_finished(worker)

    def _proxy_worker_finished(self, worker: ProxyWorker) -> None:
        """Release a proxy worker only after its thread has actually exited."""
        if self.proxy_worker is worker:
            self.proxy_worker = None
            if self.session and Path(self.session.project.source_video_path) == worker.source:
                showing = Path(self.player.player.source().toLocalFile())
                self._set_preview_source_label(
                    "original" if showing == worker.source else "optimized")
        worker.deleteLater()

    def _proxy_failed(self, worker: ProxyWorker, message: str) -> None:
        log.warning("Proxy failed: %s", message)
        if (self.proxy_worker is worker and self.session
                and Path(self.session.project.source_video_path) == worker.source):
            self.statusBar().showMessage(
                f"Preview build failed; existing files kept. {message}", 12000)

    # ---------- worker shutdown ----------

    def _join_worker(self, worker, *, ms: int = 3000) -> None:
        """Finish a QThread, or stop owning it. Never destroy a live one.

        Qt aborts the process when a running QThread is destroyed, and every
        worker here is parented to this window - so anything still going when
        the window is destroyed takes the app down with it. Dropping a large
        file on the start screen and closing straight away was enough to do
        it, because nothing joined the metadata probe.

        Result signals are dropped first: work that lands after this point
        would be delivered into a window that is already tearing down.
        """
        if worker is None:
            return
        for name in ("finished_ok", "failed", "thumbnail_ready", "progress",
                     "result_ready", "progressed", "finished_batch"):
            signal = getattr(worker, name, None)
            if signal is None:
                continue
            with contextlib.suppress(RuntimeError, TypeError):
                signal.disconnect()
        if not worker.isRunning():
            return
        for name in ("stop", "cancel", "cancel_all"):
            request_stop = getattr(worker, name, None)
            if callable(request_stop):
                with contextlib.suppress(Exception):
                    request_stop()
                break
        if worker.wait(ms):
            return
        # Still running - an ffprobe against a slow network path, say. Its
        # own timeout will end it, but holding the close for sixty seconds
        # is worse than letting it finish unowned, so hand it off instead of
        # destroying it underneath itself.
        log.warning("%s did not stop in %dms; detaching it to finish alone",
                    type(worker).__name__, ms)
        with contextlib.suppress(RuntimeError):
            worker.setParent(None)
        _detached_workers.append(worker)
        worker.finished.connect(lambda w=worker: _forget_detached(w))

    def _stop_background_workers(self) -> None:
        """Join every QThread this window owns, before anything destroys it.

        The proxy and thumbnail workers own their own references: their
        finished handlers clear them, and two comments in this file record
        what clearing them early cost last time - a second encode starting
        while the cancelled one was still draining. So this waits for those
        and leaves their bookkeeping alone. Only the metadata probes, which
        have no finished handler to do it, are cleared here.
        """
        self._stop_thumbnails()
        self._stop_proxy_worker()
        self._stop_snap_prediction_worker()
        # Queued results may survive disconnect; invalidate their commit context.
        self._first_read_context = None
        for name in ("thumb_worker", "proxy_worker", "snap_prediction_worker"):
            self._join_worker(getattr(self, name, None))
        for name in ("metadata_worker", "_probe_worker", "_first_read_worker"):
            self._join_worker(getattr(self, name, None))
            setattr(self, name, None)
        dialog = getattr(self, "_first_read_dialog", None)
        if dialog is not None:
            dialog.hide()

    # ---------- library search ----------

    def show_library(self) -> None:
        self.library_screen.refresh()
        self.stack.setCurrentWidget(self.library_screen)

    def _make_library_screen(self, settings) -> LibrarySearchScreen:
        """Which library this window uses. Subclasses answer differently.

        V2 used to let this build and then swap it out, which meant every
        launch constructed a library screen - and its QMediaPlayer - only
        to throw it away. The deleteLater never took effect, so the
        discarded screen stayed a child of the window with a live decoder
        for the life of the process. Choosing up front costs nothing and
        leaks nothing.
        """
        return LibrarySearchScreen(settings)

    def _make_clip_editor(self, settings) -> ClipEditor:
        return ClipEditor(settings)

    def _make_start_screen(self, settings) -> StartScreen:
        return StartScreen(settings)

    def _leave_library(self) -> None:
        target = self.workspace if self.session else self.start_screen
        if target is self.start_screen:
            self.start_screen.refresh_recent()
        self.stack.setCurrentWidget(target)

    def _apply_library_game_year(self, project_path: str, value: str) -> tuple[bool, str]:
        from tapesift.models.project import normalize_game_year
        from tapesift.database.connection import open_project_db
        from datetime import datetime, timezone
        conn = None
        live = self._project_is_open(project_path)
        try:
            year = normalize_game_year(value)
            if live and self.session.read_only:
                return False, "This project is open read-only."
            if not live and not Path(project_path).is_file():
                return False, "This project's file is unavailable. Reconnect its drive and try again."
            worker = getattr(self, "_index_thread", None)
            if worker and worker.is_alive():
                worker.join(timeout=5)
                if worker.is_alive():
                    return False, "The Library is still updating. Try Apply to game again shortly."
            conn = self.session.conn if live else open_project_db(Path(project_path))
            if conn.in_transaction:
                return False, "Finish the current save before changing the game year."
            updated = datetime.now(timezone.utc).isoformat()
            with conn:
                cursor = conn.execute("UPDATE projects SET game_year=?, updated_at=? WHERE id=(SELECT id FROM projects ORDER BY id LIMIT 1)", (year, updated))
                if cursor.rowcount != 1:
                    raise ValueError("This project no longer has game details.")
            if live:
                self.session.project.game_year = year
                self.session.project.updated_at = updated
            try:
                library_service.reindex_project_file(project_path)
            except Exception as exc:
                return False, f"Year saved to the project, but the Library could not refresh: {exc}"
            return True, ""
        except Exception as exc:
            return False, str(exc)
        finally:
            if conn is not None and not live:
                conn.close()

    def _apply_library_edit(self, project_path: str, clip_id: str,
                            changes: dict) -> tuple[bool, str]:
        """Persist a metadata edit made in Library Search.

        The library is only an index: the edit goes to the project file -
        or through the LIVE session when that project is open, so the next
        autosave can't overwrite it.
        """
        try:
            if self._project_is_open(project_path):
                clip = self.session.get_clip(clip_id)
                if clip is None:
                    return False, "That clip no longer exists in the project."
                self.session.checkpoint("edit clip (library)")
                clip.clip_title = changes["clip_title"].strip()
                clip.tags = [t.strip() for t in changes["tags"] if t.strip()]
                clip.notes = changes["notes"]
                edited = detail_service.merge_editor_details(
                    clip.details, changes["details"])
                clip.tags = detail_service.sync_detail_tags(clip.tags, clip.details, edited)
                clip.details = edited
                clip.touch()
                self.session.save()
                self._refresh_clip_list()
            else:
                project_service.edit_clip_metadata(
                    Path(project_path), clip_id,
                    clip_title=changes["clip_title"], tags=changes["tags"],
                    notes=changes["notes"], details=changes["details"])
            library_service.reindex_project_file(project_path)
            return True, ""
        except TapeSiftError as exc:
            return False, exc.user_text()
        except Exception as exc:
            log.exception("Library edit failed")
            return False, str(exc)

    def _open_clip_from_library(self, project_path: str, clip_id: str) -> None:
        already_open = self.session and str(self.session.db_path) == project_path
        if not already_open:
            self._open_project(project_path)
        if not self.session or str(self.session.db_path) != project_path:
            return  # open failed; _open_project already reported it
        clip = self.session.get_clip(clip_id)
        if clip is None:
            self.stack.setCurrentWidget(self.workspace)
            return
        self.clip_list.select_clip_id(clip_id)
        self.stack.setCurrentWidget(self.workspace)
        if self.session.project.has_source and \
                Path(self.session.project.source_video_path).is_file():
            self.player.seek_to(clip.start_ms)
        self.statusBar().showMessage(
            f"Opened '{clip.clip_title or 'clip'}' from library search", 4000)

    def closeEvent(self, event) -> None:
        if not self._close_project():
            # The user answered "no" to cancelling a running export. Closing
            # anyway is what this used to do, and it discarded the save, the
            # index flush and the recovery marker along with the answer.
            event.ignore()
            return
        background_service.resume_all()  # never exit leaving a job suspended
        event.accept()

    # ---------- drag & drop ----------

    VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}

    def _dropped_video(self, event) -> Path | None:
        urls = event.mimeData().urls()
        if urls:
            path = Path(urls[0].toLocalFile())
            if path.suffix.lower() in self.VIDEO_EXTENSIONS and path.is_file():
                return path
        return None

    def dragEnterEvent(self, event) -> None:
        if self._dropped_video(event):
            self.start_screen.set_drag_active(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self.start_screen.set_drag_active(False)

    def dropEvent(self, event) -> None:
        self.start_screen.set_drag_active(False)
        path = self._dropped_video(event)
        if not path:
            return
        if not self.session and hasattr(self.start_screen, "begin_loading"):
            # Probe-first: read the file on the start screen and only create a
            # project if it opens, so an unreadable file leaves no orphan
            # project and the user sees the error where they dropped it.
            self._start_project_from_video(path)
            event.acceptProposedAction()
            return
        if not self.session:
            # Fallback (no V2 start screen): create a project named after it.
            folder = Path(self.settings.default_project_folder)
            output = Path(self.settings.default_output_folder) / path.stem
            try:
                name = path.stem
                candidate = name
                suffix = 2
                while (folder / f"{candidate}{project_service.PROJECT_FILE_EXTENSION}").exists():
                    candidate = f"{name}_{suffix}"
                    suffix += 1
                self._new_project(candidate, str(folder), str(output))
            except Exception:
                log.exception("Could not auto-create project for dropped file")
                return
        if self.session:
            self._load_video(path)
        event.acceptProposedAction()

    def _start_project_from_video(self, path: Path) -> None:
        """Probe a dropped/opened video before creating a project for it.

        Loading and error feedback appear on the start screen; the project is
        created only once the file reads successfully.
        """
        self.start_screen.begin_loading(path.name)
        self._probe_worker = MetadataWorker(
            self.settings.ffprobe_path, path, self)
        self._probe_worker.finished_ok.connect(
            lambda meta, p=path: self._project_from_probed_video(p, meta))
        self._probe_worker.failed.connect(
            lambda msg, p=path: self.start_screen.show_load_error(p.name, msg))
        self._probe_worker.start()

    def _project_from_probed_video(self, path: Path, meta) -> None:
        folder = Path(self.settings.default_project_folder)
        output = Path(self.settings.default_output_folder) / path.stem
        name = path.stem
        candidate = name
        suffix = 2
        while (folder / f"{candidate}"
               f"{project_service.PROJECT_FILE_EXTENSION}").exists():
            candidate = f"{name}_{suffix}"
            suffix += 1
        try:
            session = ProjectSession.create(candidate, folder, output)
            self._initialize_project_defaults(session)
        except TapeSiftError as exc:
            self.start_screen.show_load_error(path.name, exc.user_text())
            return
        if not self._activate_session(session):  # switches to the workspace
            # Declined - the current project is still open. Clear the drop
            # zone's spinner so it does not sit loading a video that is not
            # going to arrive.
            if hasattr(self.start_screen, "clear_load_state"):
                self.start_screen.clear_load_state()
            return
        if hasattr(self.start_screen, "clear_load_state"):
            self.start_screen.clear_load_state()
        self._metadata_loaded(meta)         # applies the metadata we just read

    def _retry_open_video(self) -> None:
        """Error-state "Choose another video" -> pick a file and probe it."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open video", "",
            "Video files (*.mp4 *.mov *.mkv *.avi *.m4v *.webm);;"
            "All files (*)")
        if path:
            self._start_project_from_video(Path(path))

    # ---------- video loading ----------

    def _browse_video(self) -> None:
        if not self.session:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Load source video", "",
            "Video files (*.mp4 *.mov *.mkv *.avi *.m4v *.webm);;All files (*)")
        if path:
            self._load_video(Path(path))

    def _load_video(self, path: Path) -> None:
        if not self.session:
            return
        self.metadata_label.setText(f"Reading metadata from {path.name}…")
        self.metadata_worker = MetadataWorker(self.settings.ffprobe_path, path, self)
        self.metadata_worker.finished_ok.connect(self._metadata_loaded)
        self.metadata_worker.failed.connect(self._metadata_failed)
        self.metadata_worker.start()

    def _metadata_loaded(self, meta) -> None:
        if not self.session:
            return
        self.session.project.source_video_path = meta.path
        self.session.project.source_duration_ms = meta.duration_ms
        self.session.project.source_metadata = meta
        self.session.save()
        self.settings.add_recent_video(meta.path)
        self.settings.save()
        # "MIAMI O VS. FLORIDA STATE D" -> opponent "Florida State", when the
        # team name in Settings lets us tell the two sides apart.
        if not self.session.project.opponent:
            guess = detail_service.opponent_from_filename(
                meta.path, self.settings.my_team)
            if guess:
                self.session.project.opponent = guess
                self.statusBar().showMessage(
                    f"Opponent set to '{guess}' from the filename - change it "
                    "in Project Settings if that's wrong.", 6000)
        self._load_preview_source(Path(meta.path), meta.frame_rate)
        self.relink_btn.hide()
        self._show_metadata()
        self._refresh_clip_list()
        self.statusBar().showMessage(f"Loaded {Path(meta.path).name}", 4000)

    def _metadata_failed(self, error_text: str) -> None:
        log.error("Video load failed: %s", error_text)
        QMessageBox.warning(self, "Could not load video", error_text)
        if self.session and self.session.project.has_source:
            self._show_metadata()  # restore previous state display
        else:
            self.metadata_label.setText("No video loaded yet - click Load Video.")

    def _show_metadata(self) -> None:
        if not self.session:
            return
        meta = self.session.project.source_metadata
        if not meta.path:
            return
        size_mb = meta.file_size_bytes / (1024 * 1024)
        self.metadata_label.setText(
            f"{Path(meta.path).name}  •  {format_ms(meta.duration_ms, always_hours=True)}"
            f"  •  {size_mb:,.1f} MB  •  {meta.resolution}"
            f"  •  {meta.frame_rate:g} fps  •  {meta.video_codec}/{meta.audio_codec or 'no audio'}")
        self.metadata_label.setToolTip(meta.path)

    def _relink_source(self) -> None:
        if not self.session:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Relink source video", "",
            "Video files (*.mp4 *.mov *.mkv *.avi *.m4v *.webm);;All files (*)")
        if path:
            self._load_video(Path(path))

    # ---------- clip creation flows ----------

    def _clip_defaults(self) -> ClipDefaults:
        project = self.session.project if self.session else None
        return ClipDefaults(
            pre_roll_ms=project.pre_roll_ms if project else
            round(self.settings.default_pre_roll_seconds * 1000),
            post_roll_ms=project.post_roll_ms if project else
            round(self.settings.default_post_roll_seconds * 1000),
            separator_style=self.settings.separator_style,
        )

    def _duration_ms(self) -> int:
        return self.session.project.source_duration_ms if self.session else 0

    def _require_session(self) -> bool:
        if not self.session:
            return False
        if not self.session.project.has_source:
            QMessageBox.information(self, "No video loaded",
                                    "Load a source video before adding clips.")
            return False
        return True

    def _add_by_timestamp(self) -> None:
        if not self._require_session():
            return
        dialog = TimestampClipDialog(self._clip_defaults(),
                                     self.player.position_ms(),
                                     self._known_tags(), self)
        if dialog.exec() != TimestampClipDialog.DialogCode.Accepted:
            return
        name, label, tags, notes = dialog.name_values()
        pre_ms, post_ms = dialog.roll_values()
        # Reuse these rolls next time - the last-used values are the best
        # guess for the next clip.
        self.settings.default_pre_roll_seconds = pre_ms / 1000
        self.settings.default_post_roll_seconds = post_ms / 1000
        self.settings.save()
        clip, clamped = clip_from_timestamp(
            dialog.timestamp_ms, name, self._duration_ms(), self._clip_defaults(),
            label=label, tags=tags, notes=notes,
            pre_roll_ms=pre_ms, post_roll_ms=post_ms)
        self.session.add_clip(clip)
        self._remember_metadata(clip)
        self._after_clips_added([clip], clamped)

    def _add_by_range(self) -> None:
        if not self._require_session():
            return
        dialog = RangeClipDialog(self._clip_defaults(),
                                 known_tags=self._known_tags(), parent=self)
        if dialog.exec() != RangeClipDialog.DialogCode.Accepted:
            return
        name, label, tags, notes = dialog.name_values()
        clip, clamped = clip_from_range(
            dialog.start_ms, dialog.end_ms, name, self._duration_ms(),
            self._clip_defaults(), label=label, tags=tags, notes=notes)
        self.session.add_clip(clip)
        self._after_clips_added([clip], clamped)

    # ---------- guided I-key workflow ----------

    def _guided_popover(self):
        if not hasattr(self, "_guided") or self._guided is None:
            from tapesift.ui.guided_details import GuidedDetailsPopover
            self._guided = GuidedDetailsPopover(self, self.settings)
            self._guided.mark_out_requested.connect(
                lambda: self.player.set_out_point())
            self._guided.save_requested.connect(self._guided_save)
            self._guided.cancelled.connect(self._guided_cancelled)
            self.player.out_point_set.connect(
                lambda ms: self._guided.set_marks(self.player.in_point_ms, ms)
                if self._guided.isVisible() else None)
            self._guided_paused_playback = False
        return self._guided

    def _i_pressed(self) -> None:
        """I marks the snap. Silent and instant - nothing covers the play."""
        self.player.set_in_point()
        if self.settings.details_prompt == config.DETAILS_AT_IN:
            self._open_details_popover()

    def _o_pressed(self) -> None:
        """O ends the play - the moment you know the result, so log it now."""
        self.player.set_out_point()
        if self.settings.details_prompt == config.DETAILS_AFTER_OUT:
            self._open_details_popover()
        elif self.settings.details_prompt == config.DETAILS_OFF and \
                self.player.in_point_ms is not None:
            # One-key flow: both marks set - typing goes straight to the name.
            self.player.focus_name()

    def _open_details_popover(self) -> None:
        if not (self.session and self.session.project.has_source):
            return
        if self.player.in_point_ms is None:
            self.statusBar().showMessage("Press I at the snap first.", 4000)
            return
        popover = self._guided_popover()
        detail_values, all_tags = self._collect_vocabulary()
        popover.set_vocabulary(detail_values, all_tags)
        if self.settings.pause_on_details and \
                self.player.player.playbackState() == \
                self.player.player.PlaybackState.PlayingState:
            self.player.player.pause()
            self._guided_paused_playback = True
        popover.open_for(self.player.in_point_ms, self.player.out_point_ms)

    def _guided_save(self) -> None:
        if not self.session:
            return
        popover = self._guided_popover()
        in_ms = self.player.in_point_ms
        if in_ms is None:
            self.statusBar().showMessage("No in-point set - press I first.", 4000)
            return
        out_ms = self.player.out_point_ms
        if out_ms is None or out_ms <= in_ms:
            out_ms = self.player.position_ms()
        if out_ms <= in_ms:
            self.statusBar().showMessage(
                "Out-point must be after the in-point - play further or press "
                "Mark Out later in the video.", 5000)
            return
        name, details, tags = popover.collect()
        if not name and details:
            name = detail_service.compose_clip_name(details)
        tags = detail_service.merge_tags(tags,
                                         detail_service.details_to_tags(details))
        clip, clamped = clip_from_range(
            in_ms, out_ms, name, self._duration_ms(),
            self._clip_defaults(), tags=tags)
        clip.details = details
        self.session.add_clip(clip)
        self._remember_metadata(clip)
        self.player.clear_marks()
        popover.hide()
        self._resume_after_guided()
        self.player.focus_transport()
        if self.settings.resume_after_save:
            self.player.player.play()
        self._after_clips_added([clip], clamped)

    def _guided_cancelled(self) -> None:
        # Escape closes the popover without touching marks or clips.
        self._resume_after_guided()

    def _resume_after_guided(self) -> None:
        if getattr(self, "_guided_paused_playback", False):
            self.player.player.play()
            self._guided_paused_playback = False

    def _find_duplicate_clips(self) -> None:
        """Review clips that cover the same range, and remove the extras.

        Running detection twice over one project appends a second full set of
        candidates, and before overlapping clips were drawn in lanes the
        copies were invisible - they painted exactly on top of each other.
        """
        # Deliberately not _require_session(): that insists on a loaded source
        # video, and tidying duplicate ranges is valid without one.
        if not self.session or not self.session.clips:
            return
        dialog = DuplicateClipsDialog(self.session.clips, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        removable = dialog.clip_ids_to_remove()
        if not removable:
            return
        self.session.remove_clips(removable)
        self._selected_clip_id = None
        self.clip_editor.set_clip(None)
        self._refresh_clip_list()
        self._reconcile_selected_clip_telestration()
        self._index_current_project()
        self.statusBar().showMessage(
            f"Removed {len(removable)} duplicate clip(s).  Ctrl+Z to undo.",
            8000)

    def _sort_clips_by_time(self) -> None:
        """Put an existing list back into film order, optionally renumbering."""
        if not self._require_session() or not self.session.clips:
            return
        clips = self.session.clips
        already = all(a.start_ms <= b.start_ms
                      for a, b in zip(clips, clips[1:]))
        numbers_match = all(c.clip_number == i
                            for i, c in enumerate(clips, start=1))
        if already and numbers_match:
            self.statusBar().showMessage(
                "Clips are already in start-time order.", 4000)
            return

        dialog = ActionDialog(
            title="Sort clips by start time",
            subtitle=f"Put all {len(clips)} clips back into film order.",
            eyebrow="EDIT  /  ORGANIZE CLIPS",
            body=(
                "Clip numbers appear in export filenames. Renumbering changes "
                "the names of future exports; files you already exported are "
                "not touched."
            ),
            confirm_text="Sort clips",
            checkbox_text=f"Also renumber them 1–{len(clips)} to match",
            checkbox_checked=True,
            parent=self,
        )
        if dialog.exec() != ActionDialog.DialogCode.Accepted:
            return

        renumber = bool(dialog.checkbox and dialog.checkbox.isChecked())
        self.session.sort_clips_by_time(renumber)
        self._refresh_clip_list()
        self._index_current_project()
        self.statusBar().showMessage(
            f"Sorted {len(clips)} clips into film order"
            + (" and renumbered them" if renumber else "")
            + " - Ctrl+Z to undo", 6000)

    def _clip_view_changed(self, compact: bool) -> None:
        self.settings.clip_list_compact = compact
        self.settings.save()

    def _bulk_edit(self, clip_ids: list[str]) -> None:
        """Apply label/tags/details to every selected clip at once."""
        if not self.session or len(clip_ids) < 2:
            return
        dialog = BulkEditDialog(len(clip_ids), self.settings,
                                self._known_tags(), self)
        if dialog.exec() != BulkEditDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        if not (values["label"] or values["tags"] or values["details"]):
            self.statusBar().showMessage("Nothing to apply.", 3000)
            return
        self.session.checkpoint(f"edit {len(clip_ids)} clips")
        for clip_id in clip_ids:
            clip = self.session.get_clip(clip_id)
            if clip is None:
                continue
            if values["label"]:
                clip.label = values["label"]
            if values["tags"]:
                clip.tags = (list(values["tags"]) if values["replace_tags"]
                             else detail_service.merge_tags(clip.tags,
                                                            values["tags"]))
            previous_details = dict(clip.details)
            clip.details.update(values["details"])
            clip.tags = detail_service.sync_detail_tags(
                clip.tags, previous_details, clip.details)
            # Details also travel as tags so the library can find them.
            clip.tags = detail_service.merge_tags(
                clip.tags, detail_service.details_to_tags(values["details"]))
            clip.touch()
        self.session.commit()
        self._refresh_clip_list()
        self.statusBar().showMessage(
            f"Updated {len(clip_ids)} clips - Ctrl+Z to undo", 5000)

    def _split_clip(self) -> None:
        """Ctrl+K - cut the selected clip in two at the playhead.

        The fast fix for an over-long detected play: both halves stay in
        sequence and the new tail keeps only the quarter.
        """
        if not self.session or self._typing_in_text_field():
            return
        ids = self.clip_list.selected_clip_ids()
        if len(ids) != 1:
            self.statusBar().showMessage("Select one clip to split.", 4000)
            return
        self._split_clip_at(ids[0], self.player.position_ms())

    def _cut_clip_at_playhead(self) -> None:
        """C - cut the clip crossed by the playhead.

        A selected clip wins when ranges overlap. With no selected target,
        cut only when the playhead crosses exactly one clip; otherwise abstain
        rather than split the wrong overlapping play.
        """
        if not self.session or self._typing_in_text_field():
            return
        at_ms = self.player.position_ms()
        candidates = [
            clip for clip in self.session.clips
            if clip.start_ms < at_ms < clip.end_ms
        ]
        selected_ids = set(self.clip_list.selected_clip_ids())
        selected_candidates = [
            clip for clip in candidates if clip.id in selected_ids
        ]
        if len(selected_candidates) == 1:
            target = selected_candidates[0]
        elif len(candidates) == 1:
            target = candidates[0]
        elif not candidates:
            self.statusBar().showMessage(
                "Move the playhead inside a clip, then press C to cut.",
                4000,
            )
            return
        else:
            self.statusBar().showMessage(
                "Multiple clips overlap here. Select the one to cut, "
                "then press C.",
                5000,
            )
            return
        self._split_clip_at(target.id, at_ms)

    def _split_clip_at(self, clip_id: str, at_ms: int) -> None:
        """Split one clip at an explicit timestamp from any UI action."""
        if not self.session:
            return
        clip = self.session.get_clip(clip_id)
        if clip is None:
            return
        self.clip_list.select_clip_id(clip_id)
        self.player.player.pause()
        if not self.settings.review_mode:
            self.player.clear_clip_range()
        self.player.seek_to(at_ms)
        try:
            tail = self.session.split_clip(clip_id, at_ms)
        except TapeSiftError as exc:
            self.statusBar().showMessage(exc.message, 5000)
            return
        if tail is None:
            return
        self._refresh_clip_list()
        self._select_row(self._clip_row(tail.id))
        self._start_thumbnails([tail])
        self.statusBar().showMessage(
            f"Created Clip {tail.clip_number} at "
            f"{format_ms(at_ms, show_millis=True)}"
            " - Ctrl+Z to undo", 5000)

    def _remember_metadata(self, clip: Clip) -> None:
        """Keep the last clip's metadata for one-shot reuse (Ctrl+M)."""
        self._last_metadata = {
            "label": clip.label,
            "tags": list(clip.tags),
            "details": dict(clip.details),
        }

    def _repeat_metadata(self) -> None:
        """Ctrl+M: apply the previous clip's metadata to the selected clip.

        Fills gaps rather than overwriting: existing values on the clip win,
        tags merge, details only add missing keys.
        """
        last = getattr(self, "_last_metadata", None)
        if not self.session or not last:
            self.statusBar().showMessage(
                "No previous clip metadata to repeat yet.", 4000)
            return
        ids = self.clip_list.selected_clip_ids()
        if len(ids) != 1:
            self.statusBar().showMessage("Select one clip first.", 4000)
            return
        clip = self.session.get_clip(ids[0])
        if clip is None:
            return
        self.session.checkpoint("repeat metadata")
        if not clip.label:
            clip.label = last["label"]
        clip.tags = detail_service.merge_tags(clip.tags, last["tags"])
        for key, value in last["details"].items():
            clip.details.setdefault(key, value)
        clip.touch()
        self.session.commit()
        self._refresh_clip_list()
        self._select_row(self._clip_row(clip.id))
        self.statusBar().showMessage(
            "Applied the previous clip's metadata.", 3000)

    def _paste_timestamp_seek(self) -> None:
        """Ctrl+Shift+V: jump to a timestamp sitting on the clipboard."""
        from PySide6.QtWidgets import QApplication
        text = (QApplication.clipboard().text() or "").strip()
        if not text:
            self.statusBar().showMessage("Clipboard is empty.", 3000)
            return
        try:
            ms = parse_timestamp(text)
        except TapeSiftError:
            self.statusBar().showMessage(
                f"Clipboard doesn't look like a timestamp: '{text[:40]}'", 5000)
            return
        self.player.seek_to(ms)
        self.statusBar().showMessage(
            f"Jumped to {format_ms(ms, show_millis=True)} (from clipboard)",
            4000)

    def _add_from_marks(self) -> None:
        """New Clip > From In/Out Range - same path as the A key."""
        self.player.request_add_clip()

    def _add_from_in_out(self, in_ms: int, out_ms: int, name: str = "") -> None:
        if not self._require_session():
            return
        # A name typed in the mark bar means the user already said everything
        # they wanted to - create the clip immediately, no dialog.
        if name:
            # Repeated names auto-number instead of colliding.
            titled = filename_service.unique_title(
                name, [c.clip_title for c in self.session.clips])
            clip, clamped = clip_from_range(
                in_ms, out_ms, titled, self._duration_ms(),
                self._clip_defaults())
            self.session.add_clip(clip)
            self._remember_metadata(clip)
            self.player.clear_marks()
            self.player.clear_name()
            self._after_clips_added([clip], clamped)
            if titled != name:
                self.statusBar().showMessage(
                    f"A clip named '{name}' already exists - saved as "
                    f"'{titled}'.", 5000)
            # Hand the keyboard straight back to the transport.
            self.player.focus_transport()
            if self.settings.resume_after_save:
                self.player.player.play()
            return
        # One form, not two: when details prompting is on, A opens the same
        # popover O uses rather than a second, near-identical dialog.
        if self.settings.details_prompt != config.DETAILS_OFF:
            self._open_details_popover()
            return
        dialog = RangeClipDialog(self._clip_defaults(), in_ms, out_ms,
                                 title="Name This Clip",
                                 known_tags=self._known_tags(), parent=self)
        if dialog.exec() != RangeClipDialog.DialogCode.Accepted:
            return
        name, label, tags, notes = dialog.name_values()
        clip, clamped = clip_from_range(
            dialog.start_ms, dialog.end_ms, name, self._duration_ms(),
            self._clip_defaults(), label=label, tags=tags, notes=notes)
        self.session.add_clip(clip)
        self.player.clear_marks()
        self._after_clips_added([clip], clamped)

    def _bulk_paste(self) -> None:
        if not self._require_session():
            return
        dialog = BulkPasteDialog(self)
        if dialog.exec() != BulkPasteDialog.DialogCode.Accepted or not dialog.result:
            return
        defaults = self._clip_defaults()
        clips = []
        any_clamped = False
        for row in dialog.result.rows:
            clip, clamped = clip_from_parsed_row(row, self._duration_ms(), defaults)
            clips.append(clip)
            any_clamped = any_clamped or clamped
        self.session.add_clips(clips)
        self._after_clips_added(clips, any_clamped)

    def _csv_import(self) -> None:
        if not self._require_session():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import CSV", "", "CSV files (*.csv);;All files (*)")
        if not path:
            return
        try:
            dialog = CsvImportDialog(Path(path), self)
        except TapeSiftError as exc:
            QMessageBox.warning(self, "CSV import failed", exc.user_text())
            return
        if dialog.exec() != CsvImportDialog.DialogCode.Accepted or not dialog.result:
            return
        defaults = self._clip_defaults()
        clips = []
        any_clamped = False
        for row in dialog.result.rows:
            clip, clamped = clip_from_csv_row(row, self._duration_ms(), defaults)
            clips.append(clip)
            any_clamped = any_clamped or clamped
        self.session.add_clips(clips)
        self._after_clips_added(clips, any_clamped)

    def _after_clips_added(self, clips: list[Clip], clamped: bool) -> None:
        self._refresh_clip_list()
        self._start_thumbnails(clips)
        if len(clips) == 1 and self.session:
            # Select the new clip so it's immediately editable in the inspector.
            self.clip_list.select_clip_id(clips[0].id)
        message = f"Added {len(clips)} clip(s)."
        if clamped:
            message += " Some times were clamped to fit within the video."
        self.statusBar().showMessage(message, 5000)

    # ---------- clip list interactions ----------

    def _refresh_clip_list(self) -> None:
        with PerfTimer("clip_list_rebuild"):
            if not self.session:
                return
            project = self.session.project
            self.clip_list.set_clips(
                self.session.clips, project.source_duration_ms, project.naming_template,
                project.name, self.settings.separator_style)
            self._sync_autodetect_batch_clip_view()
            self.clip_editor.set_context(
                project.naming_template, project.name,
                self.settings.separator_style, project.source_duration_ms)
            self._refresh_timeline_presentation()
            self.player.set_period_markers(project.quarter_markers_ms)
            detail_values, all_tags = self._collect_vocabulary()
            detail_values = self._with_roster_names(detail_values)
            self.clip_editor.set_vocabulary(detail_values, all_tags)
            self.clip_editor.set_player_options(self._roster_options())
            self.export_panel.set_output_folder(project.output_folder)
            self._update_review_label()
            self._refresh_coverage_review_dialog()

    def _sync_autodetect_batch_clip_view(self) -> None:
        """Keep pending filters and navigation aligned with live batch roots."""
        if not self.session:
            self.clip_list.set_navigation_scope(None)
            return
        active = self.session.active_autodetect_review_batch()
        pending_only = (
            self.clip_list.review_filter_mode == "autodetect_pending")
        if active is None:
            self.clip_list.set_navigation_scope(None)
            if pending_only:
                self.clip_list.update_review_filter_scope({
                    clip.id for clip in self.session.clips
                })
            return
        if pending_only:
            members = self._active_autodetect_batch_clips(pending_only=True)
            member_ids = [clip.id for clip in members]
            self.clip_list.set_navigation_scope(member_ids)
            self.clip_list.update_review_filter_scope(set(member_ids))
        else:
            self.clip_list.set_navigation_scope(None)

    def _refresh_clip_block(self, clip: Clip) -> None:
        """Refresh a single clip's timeline block (colour/label) after an edit.

        Cheap targeted alternative to _refresh_clip_list: it only rebuilds the
        lightweight TimelineBlock list and repaints the cached timeline pixmap.
        The clip-list table, vocabulary and export panel are untouched.
        """
        if not self.session:
            return
        self._refresh_timeline_presentation()

    def _refresh_timeline_presentation(self) -> None:
        """Rebuild lightweight block colors and the matching popup key."""
        mode = self.settings.timeline_color_by
        if mode not in TIMELINE_COLOR_MODE_LABELS:
            mode = "play_type"
            self.settings.timeline_color_by = mode

        if not self.session:
            entries = build_timeline_legend(mode, {})
            self.player.set_timeline_key(mode, entries)
            self.player.set_coverage_segments([])
            self.player.set_ignored_fragment_summary(0, 0)
            self.clip_list.set_ignored_fragment_summary(0, 0)
            self.player.set_clip_blocks([])
            self.player.set_attribute_clips([])
            return

        project = self.session.project
        warnings = {}
        if mode == "review_status":
            warnings = compute_warnings(
                self.session.clips,
                project.source_duration_ms,
                project.naming_template,
                project.name,
                self.settings.separator_style,
            )

        categorized: list[tuple[Clip, str, str]] = []
        categories: dict[str, str] = {}
        color_overrides: dict[str, str] = {}
        for clip in self.session.clips:
            if is_ignored_autodetect_clip(clip):
                continue
            semantic = classify_play_kind(
                clip.details, clip.tags, clip.clip_title, clip.label)
            if semantic in {"penalty", "rpo", "touchdown"}:
                key = semantic
                label = PLAY_LABELS[semantic]
                color_overrides.setdefault(
                    key, PLAY_COLORS[semantic].name())
            elif mode == "primary_tag":
                key, label, custom_color = primary_timeline_tag(
                    clip.tags, project.tag_styles)
                if custom_color:
                    color_overrides.setdefault(key, custom_color)
            else:
                key, label = timeline_category(
                    mode,
                    clip.details,
                    clip.tags,
                    clip.clip_title,
                    clip.label,
                    enabled=clip.enabled,
                    needs_fix=bool(warnings.get(clip.id)),
                )
            categorized.append((clip, key, label))
            categories.setdefault(key, label)

        entries = build_timeline_legend(mode, categories, color_overrides)
        colours = {key: colour.name() for key, _label, colour in entries}
        selected = set(self.clip_list.selected_clip_ids())
        self.player.set_timeline_key(mode, entries)
        coverage = self.session.latest_autodetect_coverage()
        fragments = [
            segment for segment in coverage
            if self.session._is_recoverable_fragment_segment(segment)
            and segment.get("review_status") != "clip_created"
        ]
        fragment_ms = sum(
            max(
                0,
                int(segment.get("end_ms", 0))
                - int(segment.get("start_ms", 0)),
            )
            for segment in fragments
        )
        # Unclassified detector review spans and otherwise unclaimed spans
        # share one quiet, recoverable timeline language. Keep the persisted
        # detector ledger intact; normalize only the presentation copy.
        timeline_coverage = []
        for segment in coverage:
            item = dict(segment)
            if self.session._is_recoverable_fragment_segment(item):
                item["kind"] = "possible_missed"
            timeline_coverage.append(item)
        self.player.set_coverage_segments(timeline_coverage)
        self.player.set_ignored_fragment_summary(
            len(fragments), fragment_ms)
        self.clip_list.set_ignored_fragment_summary(
            len(fragments), fragment_ms)
        timeline_blocks: list[TimelineBlock] = []
        for clip, key, label in categorized:
            prediction = snap_prediction_service.cached_prediction(clip)
            timeline_blocks.append(TimelineBlock(
                start_ms=clip.start_ms,
                end_ms=clip.end_ms,
                selected=clip.id in selected,
                clip_id=clip.id,
                kind=key,
                title=clip.clip_title,
                colour=colours.get(key, ""),
                category_label=label,
                detected=bool(clip.detection_lineage),
                predicted_snap_ms=(
                    int(prediction["source_ms"])
                    if prediction is not None else None
                ),
                snap_confidence=(
                    float(prediction.get("confidence", 0.0))
                    if prediction is not None else None
                ),
                snap_eligible=(
                    bool(prediction.get("eligible", False))
                    if prediction is not None else False
                ),
            ))
        self.player.set_clip_blocks(timeline_blocks)
        self.player.set_attribute_clips(
            [clip for clip, _key, _label in categorized])

    def _timeline_color_mode_changed(self, mode: str) -> None:
        """Persist a presentation choice and repaint only timeline metadata."""
        if mode not in TIMELINE_COLOR_MODE_LABELS:
            return
        if self.settings.timeline_color_by != mode:
            self.settings.timeline_color_by = mode
            self.settings.save()
        self._refresh_timeline_presentation()
        self.statusBar().showMessage(
            f"Timeline colors: {TIMELINE_COLOR_MODE_LABELS[mode]}", 3000)

    def _collect_vocabulary(self) -> tuple[dict[str, list[str]], list[str]]:
        """Every detail value and tag used in this project, for autocomplete."""
        detail_values: dict[str, set[str]] = {}
        tags: set[str] = set()
        player_variants: list[str] = []
        if self.session:
            for clip in self.session.clips:
                for key, value in clip.details.items():
                    if key in {"result", "action"}:
                        detail_values.setdefault(key, set()).update(
                            result_service.split_results(value))
                    elif key == "player_name":
                        if value.strip():
                            player_variants.append(value.strip())
                        detail_values.setdefault(key, set()).add(value)
                    elif key == "other_players":
                        detail_values.setdefault(key, set()).add(value)
                        player_variants.extend(
                            detail_service.split_players(value))
                    else:
                        detail_values.setdefault(key, set()).add(value)
                tags.update(clip.tags)
        if player_variants:
            detail_values["player_name"] = set(
                tag_service.merge_player_variants(player_variants).values())
        return ({k: sorted(v) for k, v in detail_values.items()}, sorted(tags))

    def _known_tags(self) -> list[str]:
        return self._collect_vocabulary()[1]

    def _change_output_folder(self) -> None:
        if not self.session:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Choose export folder", self.session.project.output_folder)
        if not folder:
            return
        self.session.project.output_folder = folder
        self.session.save()
        self.export_panel.set_output_folder(folder)
        self.statusBar().showMessage(f"Exports will now save to {folder}", 5000)

    def select_clip(
            self, clip_id: str, *, seek: bool = True,
            seek_ms: int | None = None, reveal_in_list: bool = True,
            focus_player: bool = True, review_autoplay: bool = True) -> bool:
        """Synchronize the list, inspector, timeline, and playhead.

        This is the authoritative path for selecting one clip. It updates
        only selection presentation and never rebuilds the clip table,
        starts thumbnail work, or changes project data.
        """
        if not self.session or self._syncing_selection:
            return False
        clip = self.session.get_clip(clip_id)
        if clip is None:
            return False

        self._syncing_selection = True
        try:
            if self.clip_list.selected_clip_ids() != [clip_id]:
                if not self.clip_list.select_clip_id(
                        clip_id, reveal=reveal_in_list):
                    return False
            # Timeline and Tag Map clicks can select the current play again.
            # Reloading that same object would erase the inspector's draft.
            if not (self.clip_editor._clip is clip
                    and self.clip_editor.save_state_label.property("state") == "dirty"):
                self.clip_editor.set_clip(clip)
            self._selected_clip_id = clip.id
            self.player.set_selected_clip_id(clip.id)
            self._load_clip_telestration(clip)
            self._update_review_label()

            autodetect_review_playback = (
                self.clip_list.review_filter_mode == "autodetect_pending"
                and self.session.active_autodetect_review_batch() is not None
            )
            if autodetect_review_playback and review_autoplay:
                self.player.play_clip_range(
                    clip.start_ms, clip.end_ms, False)
            elif self.settings.review_mode and review_autoplay:
                self._enter_current_clip()
            elif seek:
                self.player.shuttle_stop()
                self.player.clear_clip_range()
                target_ms = clip.start_ms if seek_ms is None else seek_ms
                self.player.seek_to(max(
                    clip.start_ms, min(clip.end_ms, target_ms)))
        finally:
            self._syncing_selection = False

        self._sync_predicted_snap_action()
        if focus_player:
            self._return_focus_to_playback()
        return True

    def _selection_changed(self, clip_ids: list[str]) -> None:
        with PerfTimer("clip_select"):
            if not self.session or self._syncing_selection:
                return
            if len(clip_ids) == 1:
                self.select_clip(
                    clip_ids[0],
                    focus_player=not self.settings.review_mode,
                    review_autoplay=True,
                )
                return

            self.clip_editor.set_clip(None)
            self._selected_clip_id = None
            self.player.set_selected_clip_id(None)
            self._load_clip_telestration(None)
            self._update_review_label()
            self._sync_predicted_snap_action()

    def _load_clip_telestration(self, clip: Clip | None) -> None:
        """Show exactly one clip's marks, silently, or an empty surface."""
        stored = marks_from_json(clip.overlays) if clip is not None else []
        self.player.set_telestration_marks(stored)
        # One choke point for every selection change, so the rail can never
        # be left armed over a surface that has no play to save a stroke to.
        self.player.set_telestration_enabled(clip is not None)

    def _reconcile_selected_clip_telestration(self) -> Clip | None:
        """Rebind selection after the session replaces its Clip objects."""
        clip = (
            self.session.get_clip(self._selected_clip_id)
            if self.session is not None and self._selected_clip_id
            else None
        )
        if clip is not None:
            self.player.set_selected_clip_id(clip.id)
            self._load_clip_telestration(clip)
            return clip
        self._selected_clip_id = None
        self.player.set_selected_clip_id(None)
        self._load_clip_telestration(None)
        return None

    def _telestration_marks_edited(self) -> None:
        """Write a surface edit to the one selected clip, never globally."""
        session = self.session
        clip = (
            session.get_clip(self._selected_clip_id)
            if session is not None and self._selected_clip_id
            else None
        )
        if clip is None:
            # A tool may still be armed when the table selection is cleared.
            # Do not leave that unowned stroke visible or let it attach itself
            # to whichever play is selected next.
            self._load_clip_telestration(None)
            return
        if getattr(session, "read_only", False):
            # Research sessions may inspect stored overlays but never mutate
            # them. Repaint the authoritative stored copy after an attempted
            # surface edit.
            self._load_clip_telestration(clip)
            return

        serialized = marks_to_json(self.player.telestration_marks())
        if serialized == clip.overlays:
            return
        clip.overlays = serialized
        clip.touch()
        session.dirty = True
        self.telestration_edit_accepted.emit(
            clip.id, copy.deepcopy(serialized))

    def _sync_predicted_snap_action(self) -> None:
        """Reflect the selected play's cached/background snap state."""
        if not self.session or not self._selected_clip_id:
            self.player.set_predicted_snap_state("disabled")
            return
        clip = self.session.get_clip(self._selected_clip_id)
        if clip is None:
            self.player.set_predicted_snap_state("disabled")
            return
        worker = self.snap_prediction_worker
        if worker is not None and worker.isRunning() \
                and worker.clip_id == clip.id:
            self.player.set_predicted_snap_state("finding")
            return
        prediction = snap_prediction_service.cached_prediction(clip)
        if prediction is not None:
            self.player.set_predicted_snap_state("ready", prediction)
            return
        source = Path(self.session.project.source_video_path)
        if getattr(self.session, "read_only", False) or not source.is_file():
            self.player.set_predicted_snap_state("disabled")
            return
        self.player.set_predicted_snap_state("missing")

    def _jump_to_predicted_snap(
            self, clip: Clip, prediction: dict) -> None:
        source_ms = int(prediction["source_ms"])
        source_ms = max(clip.start_ms, min(clip.end_ms, source_ms))
        self.player.shuttle_stop()
        self.player.seek_to(source_ms)
        quality = "Predicted snap" if prediction.get("eligible") else \
            "Low-confidence snap estimate"
        self.statusBar().showMessage(
            f"{quality} for "
            f"'{clip.clip_title or f'Clip {clip.clip_number}'}': "
            f"{format_ms(source_ms, show_millis=True)}",
            5000,
        )

    def _predicted_snap_requested(self) -> None:
        """Jump to a cached snap, or calculate one without blocking playback."""
        if not self.session or not self._selected_clip_id:
            self.statusBar().showMessage(
                "Select one play before using Find Snap", 4000)
            return
        clip = self.session.get_clip(self._selected_clip_id)
        if clip is None:
            return
        prediction = snap_prediction_service.cached_prediction(clip)
        if prediction is not None:
            self._jump_to_predicted_snap(clip, prediction)
            return
        if getattr(self.session, "read_only", False):
            self.statusBar().showMessage(
                "Snap predictions cannot be saved in read-only review mode",
                5000,
            )
            return
        worker = self.snap_prediction_worker
        if worker is not None and worker.isRunning():
            target = "this play" if worker.clip_id == clip.id else \
                "another play"
            self.statusBar().showMessage(
                f"Snap analysis is already running for {target}", 4000)
            return
        source = Path(self.session.project.source_video_path)
        if not source.is_file():
            self.statusBar().showMessage(
                "Relink the source video before using Find Snap", 5000)
            return
        output_folder = Path(
            self.session.project.output_folder or self.session.db_path.parent)
        analysis_source = (
            proxy_service.find_ready_proxy(source, output_folder) or source)
        angle_starts = self.session.detector_angle_starts(clip.id)
        worker = SnapPredictionWorker(
            self.settings.ffmpeg_path,
            analysis_source,
            clip.id,
            clip.start_ms,
            clip.end_ms,
            angle_starts,
            self,
        )
        self.snap_prediction_worker = worker
        worker.prediction_ready.connect(self._snap_prediction_ready)
        worker.failed.connect(self._snap_prediction_failed)
        worker.finished.connect(
            lambda worker=worker: self._snap_prediction_finished(worker))
        self.player.set_predicted_snap_state("finding")
        self.statusBar().showMessage(
            f"Finding the snap for "
            f"'{clip.clip_title or f'Clip {clip.clip_number}'}'...",
            5000,
        )
        worker.start()

    def _snap_prediction_ready(
            self, clip_id: str, prediction: object) -> None:
        if not self.session or not isinstance(prediction, dict):
            return
        clip = self.session.get_clip(clip_id)
        if clip is None:
            return
        if int(prediction.get("clip_start_ms", -1)) != clip.start_ms \
                or int(prediction.get("clip_end_ms", -1)) != clip.end_ms:
            self.statusBar().showMessage(
                "The play changed while snap analysis was running; "
                "the estimate was discarded",
                6000,
            )
            return
        try:
            self.session.cache_snap_prediction(clip_id, prediction)
        except TapeSiftError as exc:
            self.statusBar().showMessage(exc.user_text(), 7000)
            return
        self._refresh_timeline_presentation()
        self._sync_predicted_snap_action()
        if self._selected_clip_id == clip_id:
            self._jump_to_predicted_snap(clip, prediction)
        else:
            self.statusBar().showMessage(
                f"Predicted snap saved for "
                f"'{clip.clip_title or f'Clip {clip.clip_number}'}'",
                5000,
            )

    def _snap_prediction_failed(self, clip_id: str, message: str) -> None:
        if self._selected_clip_id == clip_id:
            self._sync_predicted_snap_action()
        first_line = str(message).splitlines()[0]
        self.statusBar().showMessage(first_line, 8000)

    def _snap_prediction_finished(self, worker: SnapPredictionWorker) -> None:
        if self.snap_prediction_worker is worker:
            self.snap_prediction_worker = None
        worker.deleteLater()
        self._sync_predicted_snap_action()

    def _stop_snap_prediction_worker(self) -> None:
        worker = self.snap_prediction_worker
        if worker is None:
            return
        if worker.isRunning():
            worker.cancel()
            # The worker polls cancellation every 100 ms and terminates its
            # private FFmpeg process before returning.
            worker.wait(1500)
        if not worker.isRunning():
            self._snap_prediction_finished(worker)

    def _sync_pending_autodetect_clip_range(self, clip: Clip) -> None:
        """Keep a corrected pending clip's playback stop at its new boundary."""
        if (
            not self.session
            or self.clip_list.review_filter_mode != "autodetect_pending"
            or self.session.active_autodetect_review_batch() is None
            or clip.id != self._selected_clip_id
        ):
            return
        member_ids = {
            member.id for member in
            self._active_autodetect_batch_clips(pending_only=False)
        }
        if clip.id in member_ids:
            self.player.set_clip_range(
                clip.start_ms, clip.end_ms, False)

    def _timeline_clip_activated(self, clip_id: str, clicked_ms: int) -> None:
        """Select a play block and seek to the exact point that was clicked."""
        if not self.session:
            return
        clip = self.session.get_clip(clip_id)
        if clip is None:
            return
        clicked_ms = max(clip.start_ms, min(clip.end_ms, clicked_ms))
        if not self.select_clip(
                clip_id, seek_ms=clicked_ms, focus_player=True,
                review_autoplay=False):
            return
        self.statusBar().showMessage(
            f"Selected '{clip.clip_title or f'Clip {clip.clip_number}'}' at "
            f"{format_ms(clicked_ms, show_millis=True)}",
            3000)

    def _timeline_trim_preview(
            self, clip_id: str, edge: str, position_ms: int) -> None:
        """Show exact, unsaved trim feedback while the pointer is moving."""
        if not self.session:
            return
        clip = self.session.get_clip(clip_id)
        if clip is None:
            return
        original = clip.start_ms if edge == "start" else clip.end_ms
        duration = clip.end_ms - position_ms if edge == "start" \
            else position_ms - clip.start_ms
        preview_start = position_ms if edge == "start" else clip.start_ms
        preview_end = position_ms if edge == "end" else clip.end_ms
        self.clip_list.preview_bounds(
            clip_id, preview_start, preview_end)
        delta_seconds = (position_ms - original) / 1000
        self.statusBar().showMessage(
            f"Trim {edge}: {format_ms(position_ms, show_millis=True)}  |  "
            f"duration {format_ms(duration, show_millis=True)}  |  "
            f"{delta_seconds:+.3f}s"
            f"{self._trim_overlap_note(clip_id, preview_start, preview_end)}")

    def _trim_overlap_note(
            self, clip_id: str, start_ms: int, end_ms: int) -> str:
        """Name the plays a drag is about to overlap, while it can be undone.

        Overlapping plays are legal in TapeSift, so this warns rather than
        clamps: two annotations over the same footage is a real thing a coach
        does, and silently moving someone's boundary is worse than saying so.
        """
        if not self.session:
            return ""
        clashes = [
            other for other in self.session.clips
            if other.id != clip_id
            and other.start_ms < end_ms and start_ms < other.end_ms
        ]
        if not clashes:
            return ""
        names = ", ".join(
            other.clip_title or f"Play {other.clip_number:02d}"
            for other in clashes[:2])
        extra = f" +{len(clashes) - 2}" if len(clashes) > 2 else ""
        return f"  |  ⚠ overlaps {names}{extra}"

    def _timeline_trim_finished(
            self, clip_id: str, edge: str, position_ms: int) -> None:
        """Commit one boundary edit after a trim gesture is released."""
        if not self.session:
            return
        clip = self.session.get_clip(clip_id)
        if clip is None:
            return
        original = clip.start_ms if edge == "start" else clip.end_ms
        if position_ms == original:
            self._refresh_timeline_presentation()
            self.statusBar().showMessage("Clip boundary unchanged", 2000)
            self._return_focus_to_playback()
            return

        clip = self.session.trim_clip_boundary(clip_id, edge, position_ms)
        if clip is None:
            self._refresh_timeline_presentation()
            return
        project = self.session.project
        self.clip_list.update_row(
            clip, project.source_duration_ms, project.naming_template,
            project.name, self.settings.separator_style)
        self.clip_editor.set_clip(clip)
        self._refresh_clip_block(clip)
        self._sync_pending_autodetect_clip_range(clip)
        self._index_current_project()
        self._return_focus_to_playback()
        self.statusBar().showMessage(
            f"Trimmed {edge} to "
            f"{format_ms(position_ms, show_millis=True)}  |  "
            f"duration {format_ms(clip.duration_ms, show_millis=True)}  |  "
            "Ctrl+Z to undo",
            5000)

    def _timeline_context_menu_requested(
            self, clip_id: str, clicked_ms: int, global_pos) -> None:
        """Right-click actions for precise timeline editing."""
        if not self.session:
            return
        clip = self.session.get_clip(clip_id) if clip_id else None
        can_split = bool(
            clip and clip.start_ms < clicked_ms < clip.end_ms)
        reclaim_options = self.session.fragment_reclaim_options(
            clip_id=clip_id,
            at_ms=None if clip is not None else clicked_ms,
        )

        menu = QMenu(self)
        split_action = menu.addAction("Cut clip here  (C)")
        split_action.setEnabled(can_split)
        if reclaim_options:
            menu.addSeparator()
            for option in reclaim_options:
                target = self.session.get_clip(option.clip_id)
                if target is None:
                    continue
                amount = option.reclaimed_ms / 1000
                if clip is not None:
                    side = "before" if option.edge == "start" else "after"
                    text = (
                        f"Reclaim {amount:.1f}s {side} this play")
                else:
                    target_name = target.clip_title \
                        or f"Play {target.clip_number:02d}"
                    edge_name = "start" if option.edge == "start" else "end"
                    text = (
                        f"Add {amount:.1f}s to {target_name} ({edge_name})")
                action = menu.addAction(text)
                action.setToolTip(
                    "Absorb preserved source footage into this play without "
                    "crossing another play. Ctrl+Z restores the boundary.")
                action.triggered.connect(
                    lambda _checked=False, value=option:
                        self._reclaim_preserved_footage(value))
        # The detector cuts one play in two when the broadcast switches camera
        # angle mid-play. Merging is the fix, and it is the inverse of Cut.
        merge_ids = self._merge_candidate_ids(clip)
        if len(merge_ids) >= 2:
            menu.addSeparator()
            selected = set(self.clip_list.selected_clip_ids())
            if len(selected) >= 2 and set(merge_ids) == selected:
                text = f"Merge {len(merge_ids)} selected plays"
            else:
                text = "Merge with next play"
            merge_action = menu.addAction(text)
            merge_action.setToolTip(
                "Fuse these into one play covering the whole span. The "
                "result starts unlogged. Ctrl+Z undoes it.")
            merge_action.triggered.connect(
                lambda _checked=False, ids=merge_ids: self._merge_clips(ids))

        menu.addSeparator()
        undo_action = menu.addAction("Undo last edit")
        undo_action.setEnabled(self.session.can_undo())

        if clip is not None:
            split_action.triggered.connect(
                lambda: self._split_clip_at(clip.id, clicked_ms))
        undo_action.triggered.connect(self._undo)
        # Keep the menu alive while it is open. popup() avoids a nested event
        # loop, which also keeps playback and background UI events responsive.
        self._timeline_context_menu = menu
        menu.popup(global_pos)

    def _merge_candidate_ids(self, clip: Clip | None) -> list[str]:
        """What a merge from this block would fuse.

        A multi-selection wins when the clicked block is part of it, so the
        ledger's existing Ctrl/Shift selection is the multi-select gesture.
        Otherwise the offer is the pair this block forms with the next play,
        which is the split the detector actually produces.
        """
        if not self.session or clip is None:
            return []
        selected = self.clip_list.selected_clip_ids()
        if len(selected) >= 2 and clip.id in selected:
            return list(selected)
        ordered = sorted(self.session.clips, key=lambda c: c.start_ms)
        index = next(
            (i for i, item in enumerate(ordered) if item.id == clip.id), None)
        if index is None or index + 1 >= len(ordered):
            return []
        return [clip.id, ordered[index + 1].id]

    def _toggle_shortcuts_overlay(self) -> None:
        """Show or hide the keyboard sheet, centred on the window."""
        overlay = getattr(self, "_shortcuts_overlay", None)
        if overlay is None:
            overlay = ShortcutsOverlay(self)
            self._shortcuts_overlay = overlay
        if overlay.isVisible():
            overlay.hide()
            return
        overlay.adjustSize()
        size = overlay.sizeHint()
        # Never wider or taller than the window it sits on: three columns of
        # meanings measure well past 1700px on their own.
        width = min(size.width(), max(320, self.width() - 60))
        height = min(size.height(), max(240, self.height() - 60))
        overlay.resize(width, height)
        overlay.move(
            max(0, (self.width() - width) // 2),
            max(0, (self.height() - height) // 2))
        overlay.show()
        overlay.raise_()
        overlay.setFocus(Qt.FocusReason.OtherFocusReason)

    def _grid_cell_edit_requested(self, clip_id: str, key: str) -> None:
        """Offer that cell's choices and save the pick like any edit.

        The write goes through ClipEditor.apply_quick_details, which is
        the same path the inspector chips and the quick tags use - so it
        takes the undo checkpoint, mirrors into tags where that option is
        on, and refreshes every surface. Editing here is a shortcut to an
        existing action, never a second way to write a clip.
        """
        from tapesift.ui_v2.attribute_grid import (
            EDIT_CHOICES, EDIT_TYPED, normalize_down_distance,
            set_down_keeping_distance)

        choices = EDIT_CHOICES.get(key)
        if not self.session or not (choices or key in EDIT_TYPED):
            return
        if not self.select_clip(clip_id, seek=False):
            return
        clip = self.session.get_clip(clip_id)
        if clip is None:
            return
        menu = QMenu(self)
        for label, values in choices or ():
            # A down pick must not throw away the distance beside it.
            if key == "down":
                values = {"down_distance": set_down_keeping_distance(
                    clip.details.get("down_distance", ""), label)}
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(self._grid_choice_is_current(clip, values))
            action.triggered.connect(
                lambda _checked=False, applied=dict(values):
                self.clip_editor.apply_quick_details(applied))
        prompt = EDIT_TYPED.get(key)
        if prompt is not None:
            menu.addSeparator()
            typed = menu.addAction(f"Type {prompt[0].lower()}…")
            typed.triggered.connect(
                lambda _checked=False, k=key, c=clip_id:
                self._grid_cell_typed(k, c))
        self._grid_edit_menu = menu
        grid = self.player.attribute_grid
        menu.popup(grid.mapToGlobal(grid.rect().center()))

    @staticmethod
    def _grid_choice_is_current(clip, values: dict) -> bool:
        """Is this exactly what the clip already says?

        Clearing sets every field empty, and "all of no fields match" is
        vacuously true - so a Clear entry showed as ticked on every play
        until this compared the empty case directly.
        """
        return all(
            clip.details.get(field, "").strip().casefold()
            == value.strip().casefold()
            for field, value in values.items())

    def _grid_player_filter_changed(self, key: str) -> None:
        """Isolating a player on the grid isolates them in the list too.

        The grid dims rather than hides, because position is what carries
        the "and when". A list has no time axis, so there hiding is right
        - and the search box already filters on details, so this is the
        same machinery, driven from the picture instead of typed.
        """
        if not self.session:
            return
        name = ""
        if key:
            grid = self.player.attribute_grid
            roster = getattr(grid, "_roster", {})
            name = roster.get(key, "")
            if not name:
                from tapesift.ui_v2.attribute_grid import _player_key
                name = next(
                    (clip.details.get("player_name", "")
                     for clip in self.session.clips
                     if _player_key(clip, roster) == key), "")
        self.clip_list.filter_edit.setText(name)
        self.statusBar().showMessage(
            f"Showing {name}" if name else "Showing every play", 3000)

    def project_roster(self):
        """The squad for this project, or the bundled one it was seeded from."""
        from tapesift.services.roster_service import (
            Roster, load_bundled, load_for_project)

        cached = getattr(self, "_roster_cache", None)
        if cached is not None:
            return cached
        folder = None
        if self.session is not None:
            folder = getattr(self.session, "project_folder", None)
        roster = load_for_project(folder)
        if not len(roster):
            slug = getattr(self.settings, "default_roster", "") or ""
            roster = load_bundled(slug) if slug else Roster()
        self._roster_cache = roster
        return roster

    def _roster_options(self) -> list[str]:
        """The squad as a picker reads it: number first, then name.

        Sorted by number rather than alphabetically, because that is how a
        jersey is read off film and how a coach thinks about a roster.
        """
        roster = self.project_roster()
        if not len(roster):
            return []
        players = sorted(
            roster,
            key=lambda p: (int(p.number) if p.number.isdigit() else 999,
                           p.name))
        return [f"{player.number} {player.name}" for player in players]

    def _with_roster_names(
            self, detail_values: dict[str, list[str]]) -> dict[str, list[str]]:
        """Offer the squad in the player field, by number and by name.

        Both forms go in, so typing 4 narrows to the two players wearing
        it and typing Fle narrows to Fletcher. Nobody reads a name off a
        jersey; they read a number, which is exactly the thing the field
        could not accept before.
        """
        roster = self.project_roster()
        if not len(roster):
            return detail_values
        merged = dict(detail_values)
        existing = list(merged.get("player_name", []))
        offered = list(existing)
        for player in roster:
            for form in (player.name, f"{player.number} {player.name}"):
                if form not in offered:
                    offered.append(form)
        merged["player_name"] = offered
        return merged

    def _bulk_apply_to_selection(self, values: dict[str, str], *,
                                 preserve_distance: bool = False) -> bool:
        """Route an edit to every selected play, when more than one is.

        Returns True when it handled the edit, so single-selection keeps
        the normal inspector path and its per-clip behaviour.
        """
        if not self.session:
            return False
        selected = self.clip_list.selected_clip_ids()
        if len(selected) < 2:
            return False
        count = self.session.apply_details_to_clips(
            selected, values, preserve_distance=preserve_distance)
        if not count:
            return False
        # The refresh rebuilds rows, so the selection has to be put back
        # or a second bulk edit would land on one play instead of the set.
        self._refresh_clip_list()
        self.clip_list.select_clip_id(selected[0], reveal=False)
        described = ", ".join(
            value or f"cleared {field}" for field, value in values.items())
        self.statusBar().showMessage(
            f"{described} on {count} plays - Ctrl+Z to undo", 4000)
        return True

    def _grid_cell_choice_picked(
            self, clip_id: str, key: str, index: int) -> None:
        """Apply the nth choice of a row straight from the keyboard.

        Same write as the menu, minus the menu. Logging a game is hundreds
        of small edits and a popup between each one is the difference
        between the grid being faster than the panel and slower.
        """
        from tapesift.ui_v2.attribute_grid import (
            EDIT_CHOICES, set_down_keeping_distance)

        choices = EDIT_CHOICES.get(key)
        if not self.session or not choices or not (0 <= index < len(choices)):
            return
        if not self.select_clip(clip_id, seek=False):
            return
        clip = self.session.get_clip(clip_id)
        if clip is None:
            return
        label, values = choices[index]
        if key == "down":
            values = {"down_distance": set_down_keeping_distance(
                clip.details.get("down_distance", ""), label)}
        if self._bulk_apply_to_selection(dict(values), preserve_distance=key == "down"):
            return
        self.clip_editor.apply_quick_details(dict(values))

    def _grid_cell_typed(self, key: str, clip_id: str) -> None:
        """Type a value a menu cannot hold, then save it like any other."""
        from tapesift.ui_v2.attribute_grid import (
            EDIT_TYPED, normalize_down_distance)

        prompt = EDIT_TYPED.get(key)
        clip = self.session.get_clip(clip_id) if self.session else None
        if prompt is None or clip is None:
            return
        field = "down_distance" if key == "down" else key
        current = clip.details.get(field, "")
        text, accepted = QInputDialog.getText(
            self, prompt[0], f"{prompt[0]}  ({prompt[1]})", text=current)
        if not accepted:
            return
        value = normalize_down_distance(text) if key == "down" else text.strip()
        # An unreadable entry is not a reason to blank what was there.
        if not value and text.strip():
            return
        if key == "yards" and value:
            from tapesift.services.football_vocab import parse_yards
            yards = parse_yards(value)
            if yards is None:
                return
            value = str(yards)
        if not self.select_clip(clip_id, seek=False):
            return
        self.clip_editor.apply_quick_details({field: value})

    def _unclaimed_footage_clicked(self, start_ms: int, end_ms: int) -> None:
        """Offer the reclaim options for a gap the rail just reported.

        Reclaiming already existed on the timeline's right-click menu, but
        nothing showed where to use it. The rail makes the gaps visible;
        this makes them actionable in one click.
        """
        if not self.session:
            return
        middle = (int(start_ms) + int(end_ms)) // 2
        options = self.session.fragment_reclaim_options(
            clip_id="", at_ms=middle)
        if not options:
            self.statusBar().showMessage(
                "No neighbouring play can absorb this footage", 3000)
            return
        menu = QMenu(self)
        for option in options:
            target = self.session.get_clip(option.clip_id)
            if target is None:
                continue
            name = target.clip_title or f"Play {target.clip_number:02d}"
            edge = "start" if option.edge == "start" else "end"
            action = menu.addAction(
                f"Add {option.reclaimed_ms / 1000:.1f}s to {name} ({edge})")
            action.triggered.connect(
                lambda _checked=False, value=option:
                self._reclaim_preserved_footage(value))
        menu.addSeparator()
        cut_action = menu.addAction("New clip from this footage")
        cut_action.triggered.connect(
            lambda _checked=False, a=int(start_ms), b=int(end_ms):
            self._clip_from_unclaimed(a, b))
        self._unclaimed_menu = menu
        menu.popup(self.player.slider.mapToGlobal(
            self.player.slider.rect().center()))

    def _clip_from_unclaimed(self, start_ms: int, end_ms: int) -> None:
        """Turn an unclaimed gap straight into a play."""
        if not self.session:
            return
        clip = self.session.add_clip(
            Clip(start_ms=int(start_ms), end_ms=int(end_ms)))
        self._refresh_clip_list()
        self.select_clip(clip.id)
        self.statusBar().showMessage(
            "Created a play from unclaimed footage. Ctrl+Z to undo", 4000)

    def _merge_selected_clips(self) -> None:
        """Keyboard merge. Needs an explicit multi-selection to act on.

        Unlike the context menu there is no clicked block to fall back on, so
        a single selection is left alone rather than guessing that the next
        play belongs with it.
        """
        if not self.session:
            return
        selected = self.clip_list.selected_clip_ids()
        if len(selected) < 2:
            self.statusBar().showMessage(
                "Select two or more plays in the ledger to merge them", 3000)
            return
        self._merge_clips(selected)

    def _merge_clips(self, clip_ids: list[str]) -> None:
        if not self.session:
            return
        merged = self.session.merge_clips(clip_ids)
        if merged is None:
            return
        self._refresh_clip_list()
        self.clip_editor.set_clip(merged)
        self.select_clip(merged.id)
        self.statusBar().showMessage(
            f"Merged {len(clip_ids)} plays into one. It starts unlogged - "
            "Ctrl+Z to undo",
            4000,
        )

    def _reclaim_preserved_footage(
            self,
            option: project_service.FragmentReclaimOption,
    ) -> None:
        """Run one guarded fragment-to-play boundary extension."""
        if not self.session:
            return
        try:
            applied = self.session.reclaim_fragment_into_clip(
                option.segment_index,
                option.clip_id,
                option.edge,
            )
        except TapeSiftError as exc:
            QMessageBox.information(
                self, "Could not reclaim footage", exc.user_text())
            self._refresh_clip_list()
            return
        clip = self.session.get_clip(applied.clip_id)
        if clip is None:
            self._refresh_clip_list()
            return
        project = self.session.project
        self.clip_list.update_row(
            clip,
            project.source_duration_ms,
            project.naming_template,
            project.name,
            self.settings.separator_style,
        )
        self.clip_list.select_clip_id(clip.id)
        self.clip_editor.set_clip(clip)
        self._refresh_timeline_presentation()
        self._refresh_coverage_review_dialog()
        self._sync_pending_autodetect_clip_range(clip)
        self._index_current_project()
        boundary = clip.start_ms if applied.edge == "start" else clip.end_ms
        blocker = ""
        if applied.blocker_clip_id:
            blocker_name = applied.blocker_title or "the neighboring play"
            blocker = f"  |  stopped at {blocker_name}"
        self.statusBar().showMessage(
            f"Reclaimed {applied.reclaimed_ms / 1000:.1f}s into the "
            f"{applied.edge} of this play  |  "
            f"boundary {format_ms(boundary, show_millis=True)}"
            f"{blocker}  |  Ctrl+Z to undo",
            7000,
        )
        self._return_focus_to_playback()

    def _clip_edited(self, clip_id: str) -> None:
        with PerfTimer("apply_changes"):
            if self.session:
                self.session.commit()
                # Metadata-only edit: the clip-list no longer shows thumbnails
                # (#4), so regenerating them here just burns ffmpeg time and
                # flips session.dirty for no visible gain. New clips (which
                # genuinely need a start-screen/Library thumbnail) are handled
                # at creation, not here.
                clip = self.session.get_clip(clip_id)
                if clip is not None:
                    project = self.session.project
                    # Targeted update: rewrite just this clip's row (keeps the
                    # list selection + scroll position - spec 3.3) and refresh
                    # its timeline block colour. No full rebuild of the list /
                    # timeline / vocabulary / export panel.
                    self.clip_list.update_row(
                        clip, project.source_duration_ms, project.naming_template,
                        project.name, self.settings.separator_style)
                    self._refresh_clip_block(clip)
                    self._sync_pending_autodetect_clip_range(clip)
                self._update_review_label()
                # 1.3: after saving, hand keyboard control back to playback -
                # unless we're in review mode, where the inspector stays focused
                # so the next clip can be typed immediately.
                if not self.settings.review_mode:
                    self._return_focus_to_playback()

    def _clip_edit_started(self, clip_id: str) -> None:
        """Capture metadata Undo before ClipEditor changes the live model."""
        if self.session and self.session.get_clip(clip_id) is not None:
            self.session.checkpoint("edit clip", include_logging_defaults=True)

    def _reorder(self, from_index: int, to_index: int) -> None:
        if self.session:
            self.session.move_clip(from_index, to_index)
            self._refresh_clip_list()

    def _delete_clips(self, clip_ids: list[str]) -> None:
        if not self.session or not clip_ids:
            return
        active = self.session.active_autodetect_review_batch()
        if active is not None:
            selected = [
                clip for clip in self.session.clips if clip.id in clip_ids
            ]
            active_candidate_ids = self._active_autodetect_candidate_ids(
                active)
            protected_candidates = [
                clip for clip in selected
                if self._clip_is_active_batch_candidate(
                    clip, active, active_candidate_ids)
            ]
            protected_recoveries = [
                clip for clip in selected
                if clip.detection_lineage.get("batch_id") == active["id"]
                and clip.detection_lineage.get("recovery_id")
                and not str(
                    clip.detection_lineage.get("derivation", "")
                ).startswith("duplicate")
            ]
            protected_count = (
                len(protected_candidates) + len(protected_recoveries)
            )
            if protected_count:
                if (
                    protected_count != len(selected)
                    or protected_candidates and protected_recoveries
                ):
                    QMessageBox.information(
                        self,
                        "Review test clips separately",
                        "During an active autodetect test, select detector "
                        "candidates or manually recovered misses separately "
                        "before removing them.",
                    )
                    return
                if protected_candidates:
                    answer = QMessageBox.question(
                        self,
                        "Confirm these are not plays?",
                        "Deleting a detector candidate would lose the review "
                        "decision. Confirm the selected section contains no "
                        "real play and exclude it instead?",
                    )
                    if answer == QMessageBox.StandardButton.Yes:
                        self._confirm_detection_false_positive(clip_ids)
                    return
                answer = QMessageBox.question(
                    self,
                    "Withdraw recovered missed play?",
                    "Deleting this test-batch clip would silently remove a "
                    "known miss. Withdraw the selected recovered play and "
                    "exclude all of its correction sections instead?",
                )
                if answer == QMessageBox.StandardButton.Yes:
                    try:
                        count = self.session.withdraw_missed_detection(
                            clip_ids)
                    except TapeSiftError as exc:
                        QMessageBox.information(
                            self, "Could not withdraw missed play",
                            exc.user_text())
                        return
                    self._refresh_clip_list()
                    self.statusBar().showMessage(
                        f"Withdrew {count} recovered missed play"
                        f"{'s' if count != 1 else ''}.",
                        4000,
                    )
                return
        if self.settings.confirm_before_delete:
            answer = QMessageBox.question(
                self, "Delete clips",
                f"Delete {len(clip_ids)} clip(s)? Exported files are not affected.")
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.session.remove_clips(clip_ids)
        self.clip_editor.set_clip(None)
        self._refresh_clip_list()
        self._reconcile_selected_clip_telestration()

    def _shortcut_delete(self) -> None:
        if self.session and not self._typing_in_text_field():
            self._delete_clips(self.clip_list.selected_clip_ids())

    def _duplicate_clip(self, clip_id: str) -> None:
        if not self.session:
            return
        dup = self.session.duplicate_clip(clip_id)
        self._refresh_clip_list()
        if dup:
            # Select the new version so edits target it immediately.
            self.clip_list.select_clip_id(dup.id)
            self.statusBar().showMessage(
                f"Created '{dup.clip_title or 'clip version'}' - both versions "
                "export as separate files.", 5000)

    def _shortcut_duplicate(self) -> None:
        if self.session and not self._typing_in_text_field():
            ids = self.clip_list.selected_clip_ids()
            if len(ids) == 1:
                self._duplicate_clip(ids[0])

    def _preview_clip(self, clip_id: str) -> None:
        if not self.session:
            return
        clip = self.session.get_clip(clip_id)
        if clip:
            self.player.seek_to(clip.start_ms)
            self.player.player.play()

    def _toggle_enabled(self, clip_id: str, enabled: bool) -> None:
        if self.session:
            clip = self.session.set_clip_enabled(clip_id, enabled)
            if clip:
                self._refresh_clip_list()

    def _toggle_reel(self, clip_id: str, include: bool) -> None:
        if self.session:
            clip = self.session.get_clip(clip_id)
            if clip:
                clip.include_in_reel = include
                self.session.dirty = True

    def _mark_detection_reviewed(self, clip_ids: list[str]) -> None:
        if not self.session:
            return
        review_ids = self._expanded_autodetect_review_clip_ids(clip_ids)
        disabled_ids = [
            clip_id for clip_id in review_ids
            if (
                (clip := self.session.get_clip(clip_id)) is not None
                and not clip.enabled
            )
        ]
        if disabled_ids:
            answer = QMessageBox.question(
                self,
                "Include and mark as real plays?",
                f"{len(disabled_ids)} selected detector clip"
                f"{'s are' if len(disabled_ids) != 1 else ' is'} currently "
                "OFF.\n\nMarking reviewed means the corrected clip contains "
                "a real play. Include "
                f"{'them' if len(disabled_ids) != 1 else 'it'} and mark "
                "reviewed?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                for clip_id in disabled_ids:
                    self.session.set_clip_enabled(clip_id, True)
            except TapeSiftError as exc:
                self._refresh_clip_list()
                QMessageBox.information(
                    self,
                    "Could not include corrected play",
                    exc.user_text(),
                )
                return
        advance_order = self._pending_autodetect_advance_order(review_ids)
        try:
            count = self.session.mark_detection_reviewed(review_ids)
        except TapeSiftError as exc:
            QMessageBox.information(
                self, "Could not mark detection reviewed", exc.user_text())
            return
        self._refresh_clip_list()
        remaining = self._advance_pending_autodetect_review(advance_order)
        if not count:
            self.statusBar().showMessage(
                "Those detected clips were already marked reviewed.", 3000)
            return
        next_text = ""
        if remaining == 0:
            next_text = (
                " All batch candidates are resolved; finish the test batch.")
        elif remaining is not None:
            next_text = (
                f" {remaining} remain; the next clip is ready.")
        self.statusBar().showMessage(
            f"Marked {count} detected clip"
            f"{'s' if count != 1 else ''} reviewed."
            f"{next_text}",
            5000,
        )

    def _mark_missed_detection(self, clip_ids: list[str]) -> None:
        if not self.session:
            return
        try:
            count = self.session.mark_missed_detection(clip_ids)
        except TapeSiftError as exc:
            QMessageBox.information(
                self, "Could not mark missed play", exc.user_text())
            return
        if not count:
            self.statusBar().showMessage(
                "Those clips are already linked to autodetect data.", 3000)
            return
        self._refresh_clip_list()
        self.statusBar().showMessage(
            f"Recorded {count} manually recovered missed play"
            f"{'s' if count != 1 else ''} in the active test batch.",
            5000,
        )

    def _confirm_detection_false_positive(
        self,
        clip_ids: list[str],
    ) -> None:
        if not self.session:
            return
        advance_order = self._pending_autodetect_advance_order(clip_ids)
        try:
            count = self.session.confirm_detection_false_positive(clip_ids)
        except TapeSiftError as exc:
            QMessageBox.information(
                self, "Could not confirm not-a-play", exc.user_text())
            return
        self._refresh_clip_list()
        remaining = self._advance_pending_autodetect_review(advance_order)
        if not count:
            self.statusBar().showMessage(
                "Those detector clips were already confirmed and excluded.",
                3000,
            )
            return
        next_text = ""
        if remaining == 0:
            next_text = (
                " All batch candidates are resolved; finish the test batch.")
        elif remaining is not None:
            next_text = (
                f" {remaining} remain; the next clip is ready.")
        self.statusBar().showMessage(
            f"Confirmed and excluded {count} detector section"
            f"{'s' if count != 1 else ''} containing no play."
            f"{next_text}",
            6000,
        )

    def _start_autodetect_test_batch(self) -> None:
        if not self._require_session():
            return
        source_path = Path(self.session.project.source_video_path)
        if not source_path.is_file():
            QMessageBox.information(
                self,
                "Source film is unavailable",
                "Relink the source film before starting an autodetect test.",
            )
            return
        in_ms = self.player.in_point_ms
        out_ms = self.player.out_point_ms
        if in_ms is None or out_ms is None or out_ms <= in_ms:
            QMessageBox.information(
                self,
                "Set a short test range",
                "Press I at the start of a 10–15 minute section and O at the "
                "end, then start the autodetect test batch again.",
            )
            return
        try:
            batch = self.session.start_autodetect_review_batch(in_ms, out_ms)
        except TapeSiftError as exc:
            QMessageBox.information(
                self, "Could not start test batch", exc.user_text())
            return

        first = next(iter(self._active_autodetect_batch_clips()), None)
        if first is not None:
            self.clip_list.select_clip_id(first.id)
        self.player.play_clip_range(
            batch["start_ms"], batch["end_ms"], False)
        self.statusBar().showMessage(
            "Autodetect test batch started. Watch the complete source range, "
            "including every gap, then review each candidate and mark misses.",
            8000,
        )

    def _play_active_autodetect_test_range(self) -> None:
        if not self.session:
            return
        try:
            batch = self.session.validate_active_autodetect_batch_source()
        except TapeSiftError as exc:
            QMessageBox.information(
                self, "Could not play test range", exc.user_text())
            return
        self.player.play_clip_range(
            batch["start_ms"], batch["end_ms"], False)
        self.statusBar().showMessage(
            "Playing every second of the active test range so completely "
            "missed plays can be found.",
            6000,
        )

    def _finish_autodetect_test_batch(self) -> None:
        if not self.session:
            return
        batch = self.session.active_autodetect_review_batch()
        if batch is None:
            QMessageBox.information(
                self, "No active test batch",
                "Start a short autodetect test batch first.")
            return
        answer = QMessageBox.question(
            self,
            "Finish autodetect test batch?",
            "Confirm that you watched the complete I/O range, reviewed every "
            "detector candidate, and added every missed play you found.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            completed = self.session.complete_autodetect_review_batch(
                batch_id=batch["id"])
        except (
            TapeSiftError,
            autodetect_export_service.AutodetectExportError,
        ) as exc:
            detail = exc.user_text() if isinstance(
                exc, TapeSiftError) else str(exc)
            QMessageBox.information(
                self, "Batch is not ready", detail)
            self._focus_first_pending_autodetect_clip()
            return
        except Exception:
            log.exception("Unexpected error completing autodetect test batch")
            QMessageBox.critical(
                self,
                "Could not finish test batch",
                "TapeSift hit an unexpected error while finishing this batch. "
                "The batch remains open; no review work was discarded. "
                "Try again, and check the log if the problem continues.",
            )
            return
        # Completion is durable even if assembling the display payload fails.
        self.player.clear_clip_range()
        self.clip_list.show_review_filter("all")
        self.clip_list.set_navigation_scope(None)
        try:
            bundle = autodetect_export_service.build_correction_bundle(
                self.session.conn,
                session_selector=completed["session_id"],
            )
        except autodetect_export_service.AutodetectExportError as exc:
            QMessageBox.warning(
                self,
                "Batch completed, but score is unavailable",
                str(exc),
            )
            return
        except Exception:
            log.exception("Unexpected error assembling completed batch score")
            QMessageBox.warning(
                self,
                "Batch completed, but score is unavailable",
                "The batch was saved, but TapeSift could not assemble its "
                "score display. Use View Autodetect Batch Score to retry.",
            )
            return
        result = next(
            (
                item for item in bundle.get("review_batches", [])
                if item["id"] == completed["id"]
            ),
            None,
        )
        if result is None:
            QMessageBox.warning(
                self,
                "Batch completed, but score is unavailable",
                "The completed batch was not present in the score export.",
            )
            return
        QMessageBox.information(
            self,
            "Autodetect development batch complete",
            self._autodetect_score_text(result),
        )

    def _cancel_autodetect_test_batch(self) -> None:
        if not self.session:
            return
        batch = self.session.active_autodetect_review_batch()
        if batch is None:
            QMessageBox.information(
                self, "No active test batch",
                "There is no autodetect test batch to cancel.")
            return
        answer = QMessageBox.question(
            self,
            "Cancel autodetect test batch?",
            "Cancel this test range and withdraw its review labels? Your "
            "ordinary clips will remain in the project.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.session.cancel_autodetect_review_batch(
                batch_id=batch["id"])
        except TapeSiftError as exc:
            QMessageBox.warning(
                self, "Could not cancel test batch", exc.user_text())
            return
        self.player.clear_clip_range()
        self.clip_list.show_review_filter("all")
        self._refresh_clip_list()
        self.statusBar().showMessage(
            "Autodetect test batch cancelled. You can set a new I/O range.",
            5000,
        )

    def _show_autodetect_batch_score(self) -> None:
        if not self.session:
            QMessageBox.information(
                self, "No project open", "Open a project first.")
            return
        try:
            self.session.save()
            sessions = autodetect_export_service.list_captured_sessions(
                self.session.conn)
            selected = None
            active_fallback = None
            for captured in reversed(sessions):
                bundle = autodetect_export_service.build_correction_bundle(
                    self.session.conn, session_selector=captured["id"])
                batches = bundle.get("review_batches", [])
                if active_fallback is None:
                    active_fallback = next(
                        (
                            item for item in reversed(batches)
                            if item["status"] == "active"
                        ),
                        None,
                    )
                selected = next(
                    (
                        item for item in reversed(batches)
                        if item["status"] == "completed"
                    ),
                    None,
                )
                if selected is not None:
                    break
        except (
            TapeSiftError,
            autodetect_export_service.AutodetectExportError,
        ) as exc:
            detail = exc.user_text() if isinstance(
                exc, TapeSiftError) else str(exc)
            QMessageBox.information(
                self, "No autodetect score", detail)
            return
        except Exception:
            log.exception("Unexpected error assembling autodetect batch score")
            QMessageBox.warning(
                self,
                "Autodetect score is unavailable",
                "TapeSift could not assemble the saved batch score. The "
                "project and review data were not changed; check the log if "
                "the problem continues.",
            )
            return
        batch = selected or active_fallback
        if batch is None:
            QMessageBox.information(
                self,
                "No autodetect score",
                "Start a short autodetect test batch from an I/O range first.",
            )
            return
        QMessageBox.information(
            self,
            "Autodetect development batch score",
            self._autodetect_score_text(batch),
        )

    @staticmethod
    def _autodetect_score_text(batch: dict) -> str:
        score = batch["development_score"]
        metrics = score.get("primary_metrics")

        def percent(value) -> str:
            return "N/A" if value is None else f"{100 * value:.1f}%"

        eligible = batch["eligible_for_scoped_development_metrics"]
        if not eligible:
            metric_text = (
                "Metrics stay hidden until the complete source range is "
                "confirmed.\n"
                f"Pending detector candidates: "
                f"{batch['pending_candidate_count']}\n"
                f"Pending recovered misses: "
                f"{batch['pending_recovery_count']}\n"
                f"Confirmed truth progress: "
                f"{score['reviewed_truth_count']} / "
                f"{score['recommended_truth_per_batch']} recommended"
            )
        elif metrics is None:
            metric_text = "No reviewed items are scoreable yet."
        elif not score.get("ready_for_tuning", False):
            boundary = score["all_one_to_one_boundary_metrics"]
            median_error = boundary["median_max_boundary_error_ms"]
            boundary_text = (
                "N/A" if median_error is None
                else f"{median_error / 1000:.2f} s"
            )
            metric_text = (
                "Small-sample diagnostic - not ready for tuning.\n"
                "Headline precision, recall, and F1 stay hidden until the "
                "recommended batch size.\n"
                f"Confirmed truth plays: {metrics['truth_count']}\n"
                f"Detector predictions: {metrics['prediction_count']}\n"
                f"Matches at IoU {metrics['iou_threshold']:.2f}: "
                f"{metrics['matched_count']}\n"
                f"False positives: {metrics['false_positive_count']}\n"
                f"False negatives: {metrics['false_negative_count']}\n"
                f"Diagnostic median worst-edge error: {boundary_text}\n"
                f"Batch size: {score['reviewed_truth_count']} / "
                f"{score['recommended_truth_per_batch']} recommended"
            )
        else:
            boundary = score["all_one_to_one_boundary_metrics"]
            median_error = boundary["median_max_boundary_error_ms"]
            boundary_text = (
                "N/A" if median_error is None
                else f"{median_error / 1000:.2f} s"
            )
            metric_text = (
                f"Precision: {percent(metrics['precision'])}\n"
                f"Recall: {percent(metrics['recall'])}\n"
                f"F1: {percent(metrics['f1'])}\n"
                f"Median worst-edge error: {boundary_text}\n"
                f"Confirmed truth plays: {metrics['truth_count']}\n"
                f"Detector predictions: {metrics['prediction_count']}\n"
                f"False positives: {metrics['false_positive_count']}\n"
                f"False negatives: {metrics['false_negative_count']}\n"
                f"Batch size: {score['reviewed_truth_count']} / "
                f"{score['recommended_truth_per_batch']} recommended"
            )
        ready_for_tuning = bool(score.get("ready_for_tuning", False))
        state = (
            "Completed scoped development batch"
            if eligible and ready_for_tuning
            else "Completed small-sample diagnostic"
            if eligible
            else "In-progress development preview"
        )
        scope_note = (
            "This score covers only the confirmed short range; it is not a "
            "whole-game or generalization claim."
            if eligible and ready_for_tuning
            else
            "This confirmed range remains a diagnostic until it reaches the "
            "recommended truth count; it is not ready for detector tuning."
            if eligible
            else
            "No accuracy score is shown until the complete short range is "
            "confirmed; this preview makes no whole-game claim."
        )
        return (
            f"{state}\n"
            f"Range: {format_ms(batch['start_ms'])} – "
            f"{format_ms(batch['end_ms'])}\n\n"
            f"{metric_text}\n\n"
            f"{scope_note}"
        )

    def _export_autodetect_corrections(self) -> None:
        if not self.session:
            QMessageBox.information(
                self, "No project open", "Open a project first.")
            return
        self.session.save()
        try:
            bundle = autodetect_export_service.build_correction_bundle(
                self.session.conn)
        except autodetect_export_service.AutodetectExportError as exc:
            QMessageBox.information(
                self, "No correction data to export", str(exc))
            return
        default_path = self.session.db_path.with_name(
            f"tapesift-autodetect-{bundle['film_id']}-"
            f"{bundle['capture_sha256'][:8]}.json")
        selected, _ = QFileDialog.getSaveFileName(
            self,
            "Export Autodetect Correction Data",
            str(default_path),
            "JSON files (*.json)",
        )
        if not selected:
            return
        try:
            created = autodetect_export_service.write_correction_bundle(
                Path(selected), bundle)
        except (
            autodetect_export_service.AutodetectExportError, OSError,
        ) as exc:
            QMessageBox.warning(
                self, "Could not export correction data", str(exc))
            return
        self.statusBar().showMessage(
            ("Exported" if created else "Export already matches")
            + f" autodetect correction data to {selected}",
            6000,
        )

    def _undo(self) -> None:
        if self.session:
            desc = self.session.undo()
            if desc:
                self.statusBar().showMessage(f"Undid: {desc}", 3000)
            self._refresh_clip_list()
            self._reconcile_selected_clip_telestration()
            self.clip_editor.set_clip(None)

    def _redo(self) -> None:
        if self.session:
            desc = self.session.redo()
            if desc:
                self.statusBar().showMessage(f"Redid: {desc}", 3000)
            self._refresh_clip_list()
            self._reconcile_selected_clip_telestration()
            self.clip_editor.set_clip(None)

    # ---------- thumbnails ----------

    def _thumbnail_cache_dir(self) -> Path:
        # Must be a REAL filesystem path: ffmpeg (a separate process) writes
        # here, so AppData is unusable under Store-Python virtualization.
        assert self.session is not None
        output = self.session.project.output_folder
        if output:
            return Path(output) / "Thumbnails"
        return self.session.db_path.parent / "Thumbnails"

    def _start_thumbnails(self, clips: list[Clip] | None = None) -> None:
        if not self.session or not self.session.project.has_source:
            return
        source = Path(self.session.project.source_video_path)
        if not source.is_file() or not self.settings.ffmpeg_path:
            return
        if clips is None:
            targets = [c for c in self.session.clips if not c.thumbnail_path]
        else:
            requested_ids = {c.id for c in clips if c is not None}
            # Resolve against the live session so a queued request cannot keep
            # stale Clip objects alive across an undo or project switch.
            targets = [c for c in self.session.clips if c.id in requested_ids]
        if not targets:
            return
        if self.thumb_worker is not None and self.thumb_worker.isRunning():
            # Keep only the newest request and let the current worker drain.
            # The finished handler starts this pending batch without blocking
            # the UI or overlapping ffmpeg jobs.
            self._pending_thumbnail_clips = targets
            self.thumb_worker.stop()
            return
        if self.thumb_worker is not None:
            self._thumbnail_worker_finished(self.thumb_worker)
        self.thumb_worker = ThumbnailWorker(
            self.settings.ffmpeg_path, source, targets,
            self._thumbnail_cache_dir(), self)
        self.thumb_worker.thumbnail_ready.connect(self._thumbnail_ready)
        worker = self.thumb_worker
        worker.finished.connect(
            lambda worker=worker: self._thumbnail_worker_finished(worker))
        self.thumb_worker.start()

    def _stop_thumbnails(self) -> None:
        self._pending_thumbnail_clips = None
        worker = self.thumb_worker
        if worker is None:
            return
        if worker.isRunning():
            worker.stop()
            # Non-blocking: the worker checks its stop flag each clip and exits
            # on its own. Keep the reference until it has really stopped so a
            # replacement cannot overlap it.
            return
        self._thumbnail_worker_finished(worker)

    def _thumbnail_worker_finished(self, worker: ThumbnailWorker) -> None:
        """Release the worker, then start the latest queued thumbnail batch."""
        if self.thumb_worker is not worker:
            worker.deleteLater()
            return
        self.thumb_worker = None
        worker.deleteLater()
        pending = self._pending_thumbnail_clips
        self._pending_thumbnail_clips = None
        if pending and self.session:
            QTimer.singleShot(0, lambda clips=pending: self._start_thumbnails(clips))

    def _thumbnail_ready(self, clip_id: str, path: str) -> None:
        # Clip-list no longer shows thumbnails (removed for the beta); keep
        # the path on the clip so the start screen and Library still use it.
        if self.session:
            clip = self.session.get_clip(clip_id)
            if clip:
                clip.thumbnail_path = path

    # ---------- export ----------

    def _focus_export(self) -> None:
        if self.session:
            self.export_panel.set_clip_context(self.session.clips)
        self.export_panel.export_btn.setFocus()

    def _quick_export_selected(self) -> None:
        """Ctrl+E exports the one selected clip with no batch setup."""
        ids = self.clip_list.selected_clip_ids()
        if len(ids) != 1:
            self.statusBar().showMessage(
                "Select one clip to use Quick Export", 4000)
            return
        self._quick_export_clip(ids[0])

    def _quick_export_clip(self, clip_id: str) -> None:
        """Export one clip through the normal non-blocking queue."""
        if not self.session:
            return
        clip = self.session.get_clip(clip_id)
        if clip is None:
            self.statusBar().showMessage(
                "The selected clip is no longer available", 4000)
            return
        if not clip.enabled:
            self.statusBar().showMessage(
                "This clip is excluded from export. Enable it first.", 5000)
            return
        preset_name = (
            clip.export_preset or self.session.project.default_preset
            or self.settings.default_preset
        )
        self._start_export(
            "individual", preset_name, self.session.project.accurate_cut,
            clips=[clip], quick=True)

    def _start_export(self, mode: str, preset_name: str, accurate: bool,
                      clips: list[Clip] | None = None,
                      quick: bool = False) -> None:
        # Remember the chosen preset as the new default.
        if (not quick and preset_name
                and preset_name != self.settings.default_preset):
            self.settings.default_preset = preset_name
            self.settings.save()
        if not self._require_session():
            return
        if self.export_worker and self.export_worker.isRunning():
            QMessageBox.information(self, "Export running",
                                    "Wait for the current export to finish, or cancel it.")
            return
        session = self.session
        source = Path(session.project.source_video_path)
        if not source.is_file():
            QMessageBox.warning(
                self, "Source missing",
                f"The source video is missing:\n{source}\n\n"
                "Use Relink Source to point at its new location.")
            return
        try:
            plan = export_service.plan_export(
                session.project, clips if clips is not None else session.clips,
                mode, preset_name, accurate,
                self.settings.separator_style)
        except TapeSiftError as exc:
            QMessageBox.warning(self, "Cannot export", exc.user_text())
            return
        if not plan.jobs:
            QMessageBox.information(self, "Nothing to export",
                                    "\n".join(plan.warnings) or "No clips to export.")
            return
        if plan.warnings:
            answer = QMessageBox.question(
                self, "Export warnings",
                "\n".join(plan.warnings) + "\n\nContinue anyway?")
            if answer != QMessageBox.StandardButton.Yes:
                return

        session.save()
        self._launch_export_plan(plan, accurate, quick=quick)

    def _launch_export_plan(self, plan, accurate: bool, *, quick: bool) -> None:
        session = self.session
        clips_by_id = {c.id: c for c in session.clips}
        hw = "" if self.settings.hardware_acceleration == "cpu" \
            else self.settings.hardware_acceleration
        self.export_panel.set_clip_context(session.clips)
        self.export_panel.load_jobs(plan.jobs)
        self.export_worker = ExportWorker(
            self.settings.ffmpeg_path, session.project, plan.jobs, clips_by_id,
            accurate, hardware_encoder=hw, parent=self)
        w = self.export_worker
        w.job_started.connect(self.export_panel.on_job_started)
        w.job_started.connect(self._job_started)
        w.job_progress.connect(self.export_panel.on_job_progress)
        w.job_completed.connect(self.export_panel.on_job_completed)
        w.job_completed.connect(self._job_completed)
        w.job_failed.connect(self.export_panel.on_job_failed)
        w.job_failed.connect(self._job_failed)
        w.job_cancelled.connect(self.export_panel.on_job_cancelled)
        w.job_cancelled.connect(self._job_cancelled)
        # ``all_finished`` is emitted from inside ``QThread.run``.  At that
        # instant ``isRunning()`` is still true, so announcing completion from
        # that signal briefly re-enables Export while the normal running-job
        # guard still rejects the next click.  Qt's own ``finished`` signal is
        # delivered only after the worker thread has actually exited.
        w.finished.connect(lambda worker=w: self._export_finished(worker))
        w.finished.connect(w.deleteLater)
        self.export_panel.set_running(True)
        w.start()
        if quick and len(plan.jobs) == 1:
            self.statusBar().showMessage(
                f"Quick Export started: {plan.jobs[0].display_name}")
        else:
            self.statusBar().showMessage(
                f"Exporting {len(plan.jobs)} job(s)…")

    def _job_for_id(self, job_id: str):
        return self.export_panel.jobs.get(job_id)

    def _set_clip_status(self, job_id: str, status: ExportStatus,
                         exported_path: str = "") -> None:
        job = self._job_for_id(job_id)
        if not job or not self.session:
            return
        if job.clip_id:
            clip = self.session.get_clip(job.clip_id)
            if clip:
                clip.export_status = status
                if exported_path:
                    clip.exported_path = exported_path
                self.session.dirty = True
                self.clip_list.update_status(clip.id, status)

    def _job_started(self, job_id: str) -> None:
        self._set_clip_status(job_id, ExportStatus.EXPORTING)

    def _job_completed(self, job_id: str, output_path: str) -> None:
        self._set_clip_status(job_id, ExportStatus.COMPLETED, output_path)

    def _job_failed(self, job_id: str, _error: str) -> None:
        self._set_clip_status(job_id, ExportStatus.FAILED)

    def _job_cancelled(self, job_id: str) -> None:
        self._set_clip_status(job_id, ExportStatus.CANCELLED)

    def _export_finished(self, worker: ExportWorker | None = None) -> None:
        if worker is not None and self.export_worker is worker:
            self.export_worker = None
        self.export_panel.set_running(False)
        if self.session:
            self.session.save()
        jobs = list(self.export_panel.jobs.values())
        done = sum(1 for j in jobs if j.status == JobStatus.COMPLETED)
        failed = sum(1 for j in jobs if j.status == JobStatus.FAILED)
        message = f"Export finished: {done} succeeded"
        if failed:
            message += f", {failed} failed (see queue for details)"
        # Say where it went, and leave a way to go look.  The old message was
        # a count that cleared itself after eight seconds, so a finished
        # export was indistinguishable from one that never ran - which is how
        # the same clip ends up exported four times.
        folder = self.last_export_folder()
        if done and folder is not None:
            message += f"  ·  {folder}"
            self._export_folder_action.setEnabled(True)
            self._export_folder_action.setText(
                f"Open Export Folder ({folder.name})")
        log.info(
            "Export finished: %s succeeded, %s failed, output=%s",
            done, failed, folder)
        self.statusBar().showMessage(message, 20000)

    def last_export_folder(self) -> Path | None:
        """Where this project's exports land, if it is knowable."""
        if not self.session:
            return None
        folder = (self.session.project.output_folder or "").strip()
        return Path(folder) if folder else None

    def _open_export_folder(self) -> None:
        """Reveal the export folder in the system file manager."""
        folder = self.last_export_folder()
        if folder is None:
            self.statusBar().showMessage("No export folder for this project", 4000)
            return
        if not folder.is_dir():
            self.statusBar().showMessage(
                f"Export folder is missing: {folder}", 6000)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _cancel_current(self) -> None:
        if self.export_worker:
            self.export_worker.cancel_current()

    def _cancel_all(self) -> None:
        if self.export_worker:
            self.export_worker.cancel_all()

    def _remove_queued_export(self, job_id: str) -> None:
        job = self._job_for_id(job_id)
        if job is None or job.status != JobStatus.WAITING:
            return
        if self.export_worker and self.export_worker.isRunning():
            if not self.export_worker.cancel_queued(job_id):
                return
        job.status = JobStatus.CANCELLED
        self.export_panel.on_job_cancelled(job_id)
        self._job_cancelled(job_id)

    def _retry_job(self, job_id: str) -> None:
        if self.export_worker and self.export_worker.isRunning():
            return
        job = self._job_for_id(job_id)
        if not job or not self.session:
            return
        job.status = JobStatus.WAITING
        job.error_message = ""
        clips_by_id = {c.id: c for c in self.session.clips}
        hw = "" if self.settings.hardware_acceleration == "cpu" \
            else self.settings.hardware_acceleration
        self.export_worker = ExportWorker(
            self.settings.ffmpeg_path, self.session.project, [job], clips_by_id,
            self.export_panel.accurate_check.isChecked(), hardware_encoder=hw,
            parent=self)
        w = self.export_worker
        w.job_started.connect(self.export_panel.on_job_started)
        w.job_progress.connect(self.export_panel.on_job_progress)
        w.job_completed.connect(self.export_panel.on_job_completed)
        w.job_completed.connect(self._job_completed)
        w.job_failed.connect(self.export_panel.on_job_failed)
        w.finished.connect(lambda worker=w: self._export_finished(worker))
        w.finished.connect(w.deleteLater)
        self.export_panel.set_running(True)
        w.start()

    # ---------- misc ----------

    def _open_project_settings(self) -> None:
        if not self._require_session():
            return
        dialog = self.project_settings_dialog_class(
            self.session.project, self._known_tags(), self)
        while dialog.exec() == ProjectSettingsDialog.DialogCode.Accepted:
            project = self.session.project
            previous = {name: copy.deepcopy(getattr(project, name)) for name in (
                "opponent", "game_year", "output_folder", "naming_template", "quarter_markers_ms", "tag_styles")}
            dialog.apply_to(project)
            try:
                self.session.save()
            except Exception as exc:
                # The DB transaction rolls back; restore the same live Project too.
                for name, value in previous.items():
                    setattr(project, name, value)
                QMessageBox.critical(self, "Project settings were not saved", str(exc))
                continue
            self.export_panel.set_output_folder(project.output_folder)
            self._refresh_clip_list()
            self._index_current_project()   # opponent flows into the library
            self.statusBar().showMessage("Project settings saved", 3000)
            return

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == SettingsDialog.DialogCode.Accepted:
            self.player.audio.setVolume(self.settings.volume / 100)
            self.clip_editor.apply_dropdown_mode()
            self.clip_editor.apply_density(self.settings.inspector_density)
            self.clip_editor.apply_field_layout()
            if getattr(self, "_guided", None):
                self._guided.apply_dropdown_mode()
            if self.session:
                self._refresh_clip_list()
            self.statusBar().showMessage("Settings saved", 3000)

    def _open_logs(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(core_paths.logs_dir())))

    def _show_shortcuts(self) -> None:
        ShortcutsDialog(self).exec()

    def _show_welcome(self) -> None:
        from tapesift.ui.welcome_dialog import WelcomeDialog
        WelcomeDialog(self).exec()

    def _show_how_it_works(self, section: int = 0) -> None:
        HowItWorksDialog(self, section).exec()

    def _show_detector_micro_world(self) -> None:
        DetectorMicroWorldDialog(self).exec()

    def _show_about(self) -> None:
        ffmpeg = ffmpeg_service.get_version(self.settings.ffmpeg_path) or "not found"
        AboutDialog(ffmpeg, self).exec()
