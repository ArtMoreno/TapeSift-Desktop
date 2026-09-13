"""Parallel V2 shell around the production TapeSift main window."""

from __future__ import annotations

import os
import sys
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

import shiboken6

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QDialog, QDockWidget, QFrame,
    QHBoxLayout, QLabel, QMainWindow,
    QMenu, QMenuBar, QMessageBox, QProgressBar, QPushButton, QSizePolicy,
    QScrollArea, QSplitter, QToolButton, QVBoxLayout, QWidget,
)

from tapesift import __version__
from tapesift.core import paths as core_paths
from tapesift.core.perf import PerfSpan
from tapesift.core.config import AppSettings
from tapesift.database.voiceover_repository import VoiceoverRepository
from tapesift.models.composition_plan import (
    IdentityAssetRole,
    LockedTemplateIdentity,
    PixelSize,
    build_composition_plan,
)
from tapesift.models.export_job import JobStatus
from tapesift.models.export_job_snapshot import frozen_voiceover_take_id
from tapesift.models.export_package import ExportPackageSnapshot, ExportStyle
from tapesift.services import result_service
from tapesift.services import export_service
from tapesift.services.export_job_queue_service import ExportJobQueueService
from tapesift.services.project_service import ProjectSession
from tapesift.services.signature_template_store import (
    SQLiteSignatureTemplateRepository,
)
from tapesift.services.voiceover_capture import VoiceoverCaptureController
from tapesift.ui.export_panel import ExportPreviewContext
from tapesift.ui_core.clip_editor import COLLAPSED_WIDTH
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
from tapesift.ui_core.first_read_ui import needs_logging
from tapesift.ui_core.timeline_variants import (
    DUAL,
    normalize_timeline_variant,
)
from tapesift.ui_v2.components import CenteredApplicationMenu, WorkflowRibbon
from tapesift.ui_v2.control_center import ControlCenterDeck
from tapesift.ui_v2.dock_v2 import DockV2Deck, make_control_center
from tapesift.ui_v2.export_package_bridge import (
    ExportQueueRestoreThread,
    ExportQueuedCancelThread,
    ExportRetryThread,
    ExportStagingRequest,
    ExportStagingThread,
    SelectedTakePreviewRequest,
    SelectedTakePreviewResult,
    SelectedTakePreviewThread,
    request_export_worker_cancel_all,
)
from tapesift.ui_v2.voiceover_deck import VoiceoverDeckCoordinator
from tapesift.ui_v2.backdrop import install_modal_backdrop
from tapesift.ui_v2.library_screen import LibrarySearchScreenV2
from tapesift.ui_v2.quick_tag_manager import QuickTagManagerDialog
from tapesift.ui_v2.quick_tag_tray import (
    QUICK_TAGS, RUN_PASS_LAB_TAGS, QuickTagTray, serialize_quick_tags,
)
from tapesift.ui_v2.start_screen import StartScreenV2, _brand_image_label
from tapesift.ui_v2.snap_calibration_panel import SnapCalibrationPanel
from tapesift.ui_v2.signature_template_manager import (
    SignatureTemplateManagerDialog,
)
from tapesift.services.preview_compositor import (
    CompositionAssetPayload,
    register_compositor_fonts,
)
from tapesift.workers.export_worker import ExportWorker
from tapesift.ui_v2.temporal_review_panel import TemporalReviewPanel
from tapesift.ui_v2.workspace_docks import (
    WORKSPACE_STATE_VERSION,
    decode_qbytearray,
    encode_qbytearray,
    redock_offscreen_floating_docks,
)
from tapesift.workers.metadata_worker import MetadataWorker
from tapesift.services.heatmap_service import build_heatmap
from tapesift.ui_v2.heatmap_export_dialog import HeatmapExportDialog

if TYPE_CHECKING:
    # Annotation only. Importing it for real put research/ on the startup
    # path of the shipped app - a directory CONTRIBUTING.md calls frozen and
    # docs describe as a sandbox. The two research panels already do this.
    from tapesift.research.run_pass_temporal_review import TemporalReviewSession


class MainWindowV2(MainWindowWorkflow, QMainWindow):
    """A new visual shell that keeps all proven application behavior intact."""

    REVIEW_LEDGER_WIDTH = 340
    REVIEW_INSPECTOR_WIDTH = 351

    def _configure_top_level_surface(self) -> None:
        """Configure the V2-only frameless surface before first show.

        Shell V3 overrides this narrow presentation hook so it can use the
        standard Windows frame without branching the playback or project
        bootstrap.
        """
        app = QApplication.instance()
        if app is None or app.platformName() != "offscreen":
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            self.setAttribute(
                Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def _create_modal_backdrop_controller(self):
        """Install V2's frameless-window scrim presentation."""
        return install_modal_backdrop(self)

    def menuBar(self) -> QMenuBar:  # noqa: N802 - Qt API name
        """Return the authoritative menu bar after it moves into its shell."""
        centered = getattr(self, "_centered_menu_bar", None)
        if centered is not None:
            return centered
        return super().menuBar()

    def _install_centered_application_menu(self) -> None:
        """Center the existing menu hierarchy without rebuilding its actions."""
        menu_bar = super().menuBar()
        shell = CenteredApplicationMenu(menu_bar, self)
        # The selected Option 4 frame uses one 50px application row. Keep the
        # existing menu/window-control host and only trim its presentation.
        shell.setFixedHeight(50)
        self._centered_menu_bar = menu_bar
        self._centered_menu_shell = shell
        self.setMenuWidget(shell)

    def _add_action(self, menu, text: str, shortcut: str, slot) -> QAction:
        """Keep the inherited Playback QMenu wrapper alive for V2 extension."""
        action = super()._add_action(menu, text, shortcut, slot)
        if text == "Show Full Timeline":
            self._playback_menu = menu
        return action

    def _build_menu(self) -> None:
        """Extend the inherited Playback menu without rebuilding its actions."""
        super()._build_menu()
        self._build_timeline_menu()

    def _build_timeline_menu(self) -> None:
        """Route the hidden view strip through existing player controls."""
        playback_menu = self._playback_menu
        self._playback_menu_action = playback_menu.menuAction()

        playback_menu.addSeparator()
        self.timeline_menu = playback_menu.addMenu("Timeline")
        self.timeline_menu_action = self.timeline_menu.menuAction()

        self.timeline_zoom_in_action = QAction("Zoom In", self)
        self.timeline_zoom_in_action.triggered.connect(
            self.player.timeline_zoom_in.click)
        self.timeline_menu.addAction(self.timeline_zoom_in_action)

        self.timeline_zoom_out_action = QAction("Zoom Out", self)
        self.timeline_zoom_out_action.triggered.connect(
            self.player.timeline_zoom_out.click)
        self.timeline_menu.addAction(self.timeline_zoom_out_action)

        self.timeline_fit_play_action = QAction("Fit Selected Play", self)
        self.timeline_fit_play_action.triggered.connect(
            self.player.timeline_fit_play.click)
        self.timeline_menu.addAction(self.timeline_fit_play_action)

        self.timeline_predicted_snap_action = QAction(
            "Predicted Snap: Snap", self)
        self.timeline_predicted_snap_action.triggered.connect(
            self.player.predicted_snap_button.click)
        self.timeline_menu.addAction(self.timeline_predicted_snap_action)
        self.timeline_menu.addSeparator()

        # This is the same live menu owned by TimelineKeyLegend. Its dynamic
        # color-mode and legend entries are not copied or rebuilt here.
        self.player.timeline_key_menu.setTitle("Timeline Key")
        self.timeline_key_menu = self.player.timeline_key_menu
        self.timeline_menu.addMenu(self.timeline_key_menu)
        self.timeline_key_menu_action = self.timeline_key_menu.menuAction()

        self.timeline_snap_action = QAction("Snap to Clip Edges", self)
        self.timeline_snap_action.setCheckable(True)
        self.timeline_snap_action.toggled.connect(
            self.player.timeline_snap_button.setChecked)
        self.player.timeline_snap_button.toggled.connect(
            self.timeline_snap_action.setChecked)
        self.timeline_menu.addAction(self.timeline_snap_action)

        self.timeline_volume_action = QAction("Volume and Mute…", self)
        self.timeline_volume_action.triggered.connect(
            self.player.volume_btn.click)
        self.timeline_menu.addAction(self.timeline_volume_action)

        self.timeline_menu.aboutToShow.connect(
            self._sync_timeline_menu_actions)
        self._sync_timeline_menu_actions()

    def _sync_timeline_menu_actions(self) -> None:
        """Mirror the authoritative hidden controls when the menu opens."""
        player = self.player
        pairs = (
            (self.timeline_zoom_in_action, player.timeline_zoom_in),
            (self.timeline_zoom_out_action, player.timeline_zoom_out),
            (self.timeline_fit_play_action, player.timeline_fit_play),
            (self.timeline_predicted_snap_action,
             player.predicted_snap_button),
            (self.timeline_volume_action, player.volume_btn),
        )
        for action, control in pairs:
            action.setEnabled(control.isEnabled())
            action.setToolTip(control.toolTip())

        self.timeline_predicted_snap_action.setText(
            f"Predicted Snap: {player.predicted_snap_button.text()}")
        self.timeline_key_menu.menuAction().setEnabled(
            player.timeline_key_button.isEnabled())
        self.timeline_snap_action.setEnabled(
            player.timeline_snap_button.isEnabled())
        self.timeline_snap_action.setToolTip(
            player.timeline_snap_button.toolTip())
        self.timeline_snap_action.blockSignals(True)
        self.timeline_snap_action.setChecked(
            player.timeline_snap_button.isChecked())
        self.timeline_snap_action.blockSignals(False)

    def __init__(
            self, settings: AppSettings, timeline_variant: str = DUAL,
            run_pass_lab: bool = False,
            temporal_review_session: TemporalReviewSession | None = None,
            temporal_review_project_paths: tuple[Path, ...] = (),
            snap_calibration_session: TemporalReviewSession | None = None,
            snap_calibration_project_paths: tuple[Path, ...] = (),
            voiceover_capture_controller:
            VoiceoverCaptureController | None = None) -> None:
        timeline_variant = normalize_timeline_variant(timeline_variant)
        if (
            temporal_review_session is not None
            and snap_calibration_session is not None
        ):
            raise ValueError(
                "Temporal Review and Snap Calibration cannot run together.")
        self.temporal_review_session = temporal_review_session
        self.temporal_review_project_paths = tuple(
            Path(path) for path in temporal_review_project_paths)
        self.snap_calibration_session = snap_calibration_session
        self.snap_calibration_project_paths = tuple(
            Path(path) for path in snap_calibration_project_paths)
        self.run_pass_lab = bool(
            run_pass_lab
            or temporal_review_session is not None
            or snap_calibration_session is not None)
        # MainWindow asks for crash recovery during its own construction.
        # V2 cannot safely activate that recovered session until its native
        # docks and recovery timers exist, so defer the inherited check to the
        # end of this constructor.
        self._v2_initializing = True
        self._v2_recovery_pending = False
        self._review_export_scope_ids: tuple[str, ...] | None = None
        self._export_bridge_generation = 0
        self._export_restore_complete = False
        self._export_preview_request_id = 0
        self._export_preview_panel_generation = 0
        self._export_preview_worker: SelectedTakePreviewThread | None = None
        self._export_staging_worker: ExportStagingThread | None = None
        self._export_restore_worker: ExportQueueRestoreThread | None = None
        self._export_retry_worker: ExportRetryThread | None = None
        self._export_cancel_workers: set[ExportQueuedCancelThread] = set()
        self._compositor_font_error = ""
        # The dock map lives here rather than only inside the dock-building
        # method: restoring a collapsed inspector fires collapse_changed
        # while the docks are still being assembled, and the slot must find
        # an (empty) map, not a missing attribute.
        self._workspace_docks: dict[str, QDockWidget] = {}
        self._default_dock_areas: dict[
            QDockWidget, Qt.DockWidgetArea] = {}
        with PerfSpan("v2_super_init"):
            super().__init__(settings, timeline_variant=timeline_variant)
        try:
            # QFontDatabase is GUI-thread state. Workers only consume the
            # registered family after this boundary has completed.
            register_compositor_fonts()
        except Exception as exc:
            self._compositor_font_error = str(exc)
        # MainWindowV2 constructs and owns the one window-wide deck.  The
        # workflow and a bare VideoPlayer remain unaware of its presentation;
        # attach only binds the existing playback/marking behavior.
        # Dock V2 is the default; TAPESIFT_DOCK_V2=0 selects the legacy deck.
        # Both satisfy the same attach contract, so behavior wiring below this
        # line does not need to know which presentation it got.
        self.control_center = make_control_center(self)
        self.player.attach_control_center(self.control_center)
        self.voiceover_capture = (
            voiceover_capture_controller
            if voiceover_capture_controller is not None
            else VoiceoverCaptureController(parent=self)
        )
        if self.voiceover_capture.thread() is not self.thread():
            raise ValueError(
                "Voiceover capture must be created on the window's Qt thread")
        if self.voiceover_capture.parent() is not self:
            self.voiceover_capture.setParent(self)
        self.voiceover_deck = VoiceoverDeckCoordinator(
            self, self.voiceover_capture)
        # The full-width deck is a compact doorway into the one existing
        # ExportPanel. It never starts a second queue or claims a composited
        # render that the final FFmpeg path cannot yet produce.
        self.control_center.export_style_requested.connect(
            self._deck_export_style_requested)
        self.control_center.export_format_requested.connect(
            self._focus_export)
        self.control_center.export_requested.connect(self._focus_export)
        # Dock V2 asks for the settings dialog; the workflow owns it.
        settings_requested = getattr(
            self.control_center, "settings_requested", None)
        if settings_requested is not None:
            settings_requested.connect(self._open_settings)
        self.export_panel.export_style_changed.connect(
            self.control_center.set_export_style)
        self.export_panel.manage_templates_requested.connect(
            self._manage_export_templates_requested)
        self.export_panel.composited_preview_requested.connect(
            self._request_composited_export_preview)
        self.export_panel.composited_export_requested.connect(
            self._start_composited_export)
        # Qt's offscreen test platform does not route application shortcuts
        # correctly through a frameless top-level window. Production V2 uses
        # the custom TapeSift frame; V3 overrides this presentation hook and
        # keeps the platform's native opaque frame.
        self._configure_top_level_surface()
        with PerfSpan("v2_centered_menu"):
            self._install_centered_application_menu()
        self.setObjectName("TapeSiftV2")
        self.setWindowTitle(
            "TapeSift - Snap Calibration"
            if self.snap_calibration_session is not None
            else (
                "TapeSift - Temporal Review"
                if self.temporal_review_session is not None
                else (
                    "TapeSift - Run/Pass Lab"
                    if self.run_pass_lab else "TapeSift"
                )
            ))
        # The standard workbench supports standard desktop displays down to
        # 1260x768. Below that Qt clamps deliberately instead of allowing the
        # native docks or fixed control deck to collide.
        self.setMinimumSize(1260, 768)
        self.resize(1520, 940)

        # QStackedWidget reports the widest hidden page as its minimum. Once
        # native side docks exist that would add Home/Library's wide hint to
        # both dock widths and force the window past the 1595px design target.
        # The active page still owns its real minimum; hidden pages do not.
        self.stack.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._workspace_state_guard = False
        self._workspace_restored = False
        self._dock_visibility: dict[str, bool] = {}
        self._workspace_maximized_docks: set[str] = set()
        self._restore_floating_states_pending = False
        self._player_fullscreen = False
        self._player_pre_fullscreen_maximized = False
        self._app_closing = False
        self._workspace_save_timer = QTimer(self)
        self._workspace_save_timer.setSingleShot(True)
        self._workspace_save_timer.setInterval(250)
        self._workspace_save_timer.timeout.connect(self._save_workspace_now)
        self._workspace_restore_timer = QTimer(self)
        self._workspace_restore_timer.setSingleShot(True)
        self._workspace_restore_timer.timeout.connect(
            self._restore_workspace)
        self._workspace_recovery_timer = QTimer(self)
        self._workspace_recovery_timer.setSingleShot(True)
        self._workspace_recovery_timer.timeout.connect(
            self._recover_missing_monitor_docks)
        self._workspace_resize_timer = QTimer(self)
        self._workspace_resize_timer.setSingleShot(True)
        self._workspace_resize_timer.timeout.connect(
            self._resize_default_docks)
        self._workspace_window_state_timer = QTimer(self)
        self._workspace_window_state_timer.setSingleShot(True)
        self._workspace_window_state_timer.timeout.connect(
            self._restore_floating_window_states)
        self._save_after_workspace_resize = False
        with PerfSpan("v2_decorate_workspace"):
            self._decorate_workspace()
        with PerfSpan("v2_page_mastheads"):
            self._build_page_mastheads()
        with PerfSpan("v2_review_masthead"):
            self._build_review_masthead()
        self.stack.currentChanged.connect(self._workspace_page_changed)
        app = QApplication.instance()
        if app is not None:
            app.screenRemoved.connect(self._workspace_screen_removed)
        self._workspace_page_changed(self.stack.currentIndex())
        # The shortcut hint used to sit in the header forever. It never
        # changes, so it belongs on the status bar the header was competing
        # with - permanently visible, but costing no workspace height.
        self._shortcut_hint = QLabel(
            "I / O mark  •  C cut at playhead  •  F5 review  •  "
            "Ctrl+E quick export")
        self._shortcut_hint.setProperty("role", "subtle")
        self.statusBar().addPermanentWidget(self._shortcut_hint)
        self.statusBar().showMessage(
            f"TapeSift v{__version__} - your film stays local")
        # Dim the workspace behind modal dialogs so open trays read as a
        # layer above the page instead of a floating peer of it.
        with PerfSpan("v2_modal_backdrop"):
            self._backdrop_controller = \
                self._create_modal_backdrop_controller()
        self._v2_initializing = False
        if self._v2_recovery_pending:
            self._v2_recovery_pending = False
            # The inherited recovery flow can ask the user whether to reopen
            # a project. Queue it until app_v2 has shown the main window so
            # that prompt can never block invisibly behind an unshown parent.
            #
            # Bound to self as the timer's context. Without a receiver Qt has
            # nothing to cancel the connection against, so the timer still
            # fired after the window's C++ object was gone and took the
            # process with it.
            QTimer.singleShot(0, self, self._run_deferred_recovery)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ensure_native_frame()
        self._fit_to_screen_once()

    def _fit_to_screen_once(self) -> None:
        """Keep the opening window inside the display it lands on.

        With two monitors at different scale factors, Qt sizes the
        window against one screen's device pixel ratio and then shows
        it on the other: a 1520x940 window became 2280x1410 (exactly
        1.5x, the second monitor's scaling) with its left edge 960px
        off the side of the primary display.

        Clamping after show rather than chasing the DPI arithmetic:
        whatever produced the size, the window must end up on screen.
        """
        if getattr(self, '_screen_fit_done', False):
            return
        self._screen_fit_done = True
        if self.isMaximized() or self.isFullScreen():
            return
        screen = self.screen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = max(self.minimumWidth(),
                    min(self.width(), available.width()))
        height = max(self.minimumHeight(),
                     min(self.height(), available.height()))
        if (width, height) != (self.width(), self.height()):
            self.resize(width, height)
        frame = self.frameGeometry()
        if not available.contains(frame):
            self.move(
                available.x() + max(0, (available.width() - width) // 2),
                available.y() + max(0, (available.height() - height) // 2))

    def paintEvent(self, event) -> None:
        """Paint the opaque rounded base of the frameless shell.

        ``WA_TranslucentBackground`` (required for the rounded corners)
        makes Qt ignore QSS background colors on the top-level window
        itself - only children paint. Any area not covered by an opaque
        child therefore shows the desktop through: the home screen hid
        it, the review workspace's toolbar, docks, and gaps did not.
        Painting the warm canvas here guarantees an opaque base that
        children render above. The radius flattens to zero while
        maximized or fullscreen, matching the QSS radius rules on the
        masthead and status bar (the tsMaximized property).
        """
        super().paintEvent(event)
        app = QApplication.instance()
        if sys.platform != "win32" or (
                app is not None and app.platformName() == "offscreen"):
            return
        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#171815"))
        # Always rounded, in every state. Flattening while maximized meant
        # the radius depended on a window-state flag staying in sync, and any
        # missed sync left the shell showing hard square corners. The corner
        # pixels are worth more than the few transparent pixels a rounded
        # maximized window gives back to the desktop.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawRoundedRect(self.rect(), 10, 10)
        painter.end()

    def _ensure_native_frame(self) -> None:
        """Restore native frame behaviors on the frameless shell.

        The V2 shell keeps ``FramelessWindowHint`` (the in-app masthead
        carries the window controls); on Windows that strips the native
        frame entirely - no resize borders, no snapping, no DWM rounding.
        The windows_frame module re-adds those behaviors at the HWND
        level. It is inert on other platforms and on the offscreen test
        platform, so deterministic captures are unchanged.
        """
        if sys.platform != "win32":
            return
        shell = getattr(self, "_centered_menu_shell", None)
        if shell is None:
            return
        from tapesift.ui_v2.windows_frame import install_native_frame
        install_native_frame(self, shell)

    def _run_deferred_recovery(self) -> None:
        # A window torn down between queueing this and it firing has no
        # business prompting anyone. isValid is not enough on its own -
        # deleteLater only queues the destruction, so the object is still
        # valid while the event loop that will run it is the same one
        # delivering this timer. A window on its way out is not visible,
        # and recovery asking to reopen a project from behind a closing
        # window is wrong in the app for the same reason it crashes here.
        if not shiboken6.isValid(self) or not self.isVisible():
            return
        # A project supplied on the command line is opened immediately after
        # construction and wins over stale crash-recovery state.
        if self.session is None:
            self._check_recovery()

    def _check_recovery(self) -> None:
        """A read-only research launch never takes over another app session."""
        if (
            getattr(self, "temporal_review_session", None) is not None
            or getattr(self, "snap_calibration_session", None) is not None
        ):
            return
        if getattr(self, "_v2_initializing", False):
            self._v2_recovery_pending = True
            return
        super()._check_recovery()

    def _make_start_screen(self, settings) -> StartScreenV2:
        return StartScreenV2(settings)

    def _start_configured_project(
            self, name: str, folder: str, output_folder: str,
            video_path: str) -> None:
        """Probe the selected film before creating the configured project."""
        path = Path(video_path)
        self.start_screen.begin_loading(path.name)
        self._probe_worker = MetadataWorker(
            self.settings.ffprobe_path, path, self)
        self._probe_worker.finished_ok.connect(
            lambda meta, n=name, f=folder, o=output_folder, p=path:
            self._configured_project_from_probe(n, f, o, p, meta))
        self._probe_worker.failed.connect(
            lambda message, p=path:
            self.start_screen.show_load_error(p.name, message))
        self._probe_worker.start()

    def _configured_project_from_probe(
            self, name: str, folder: str, output_folder: str,
            video_path: Path, metadata) -> None:
        previous = self.session
        self._new_project(name, folder, output_folder)
        if self.session is previous or self.session is None:
            return
        if hasattr(self.start_screen, "clear_load_state"):
            self.start_screen.clear_load_state()
        self._metadata_loaded(metadata)

    def _make_library_screen(self, settings) -> LibrarySearchScreenV2:
        """Built directly, rather than swapped in after the fact."""
        return LibrarySearchScreenV2(settings)

    def _decorate_workspace(self) -> None:
        self.workspace.setObjectName("V2Workspace")
        outer = self.workspace.layout()
        assert isinstance(outer, QVBoxLayout)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(9)

        self.workflow_ribbon = WorkflowRibbon(self.workspace)
        self.workflow_ribbon.detect_requested.connect(self._v2_detect)
        self.workflow_ribbon.review_requested.connect(self._v2_review)
        self.workflow_ribbon.export_requested.connect(self._focus_export)

        # The panels must exist before the header can be dissolved into them.
        self._build_review_workspace(outer)
        self._dissolve_header(outer)

        self.clip_editor.setProperty("panel", "true")
        self.clip_list.setProperty("panel", "true")
        self.export_panel.setProperty("panel", "true")
        self.project_label.setProperty("role", "heading")
        self.preview_source_label.setProperty("role", "eyebrow")

        for button in self.workspace.findChildren(QAbstractButton):
            text = button.text().replace("&", "")
            if text in {"Add Clip", "Export", "New Clip", "Save Changes"}:
                button.setProperty("primary", "true")
        for button in (
                getattr(self, "new_clip_button", None),
                getattr(self.clip_editor, "apply_btn", None),
                getattr(self.clip_editor, "save_next_btn", None)):
            if button is not None:
                button.setProperty("primary", "true")

        # The visible workbench is the Review stage. F5 changes how clips
        # advance inside this stage; it is not a return to Detect.
        self._workspace_stage = "review"
        self._export_stage_sized = False
        self.workflow_ribbon.set_active("review")
        self.player.timeline_follow_playhead.toggled.connect(
            self._timeline_follow_playhead_changed)
        self._workspace_restore_timer.start(0)

    def _build_review_masthead(self) -> None:
        """Populate the one-row frameless masthead from Option 1."""
        shell = self._centered_menu_shell
        brand = _brand_image_label(
            "tapesift-logo.png", 25, object_name="ReviewBrandLockup")
        if brand is not None:
            shell.add_review_left_widget(brand)
        divider = QFrame()
        divider.setObjectName("ReviewBrandDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFixedSize(1, 26)
        shell.add_review_left_widget(divider)

        self.review_project_button = QToolButton()
        self.review_project_button.setObjectName("ReviewProjectButton")
        self.review_project_button.setText("No project")
        self.review_project_button.setMinimumWidth(150)
        self.review_project_button.setMaximumWidth(230)
        self.review_project_button.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.review_project_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        project_menu = QMenu(self.review_project_button)
        project_settings = project_menu.addAction("Project Settings…")
        project_settings.triggered.connect(self._open_project_settings)
        project_menu.addSeparator()
        close_project = project_menu.addAction("Close Project")
        close_project.triggered.connect(self._close_project)
        self.review_project_button.setMenu(project_menu)
        shell.add_review_left_widget(self.review_project_button, 1)

        self.review_progress_label = QLabel("0 / 0 clips reviewed")
        self.review_progress_label.setObjectName("ReviewProjectProgressLabel")
        self.review_progress_label.setMinimumWidth(104)
        shell.add_review_left_widget(self.review_progress_label)
        self.review_progress = QProgressBar()
        self.review_progress.setObjectName("ReviewProjectProgress")
        self.review_progress.setRange(0, 100)
        self.review_progress.setValue(0)
        self.review_progress.setTextVisible(False)
        self.review_progress.setFixedWidth(138)
        shell.add_review_left_widget(self.review_progress)
        # Option 4 keeps progress as one compact "N / total logged" label.
        # The second, unlabeled fill bar spent 138px while repeating the same
        # fact and is intentionally absent from the selected masthead.
        self.review_progress.hide()
        shell.hide_review_widgets_when_compact(
            self.review_progress_label,
        )

        self.review_autosave_dot = QLabel()
        self.review_autosave_dot.setObjectName("ReviewAutosaveDot")
        self.review_autosave_dot.setFixedSize(6, 6)
        shell.add_review_right_widget(self.review_autosave_dot)
        self.review_autosave_dot.hide()
        self.review_autosave_label = QLabel("Autosaved")
        self.review_autosave_label.setObjectName("ReviewAutosaveLabel")
        shell.add_review_right_widget(self.review_autosave_label)
        self.review_autosave_label.hide()
        self.review_pop_out_button = QPushButton("Pop Out")
        self.review_pop_out_button.setObjectName("ReviewPopOutButton")
        self.review_pop_out_button.clicked.connect(
            self._toggle_player_floating)
        shell.add_review_right_widget(self.review_pop_out_button)
        self.review_pop_out_button.hide()

        self.review_masthead = shell.review_left_host
        self._sync_review_masthead()

    def _build_page_mastheads(self) -> None:
        """Give Home and Library the same single-row shell as Review."""
        shell = self._centered_menu_shell
        self.start_screen.move_masthead_to(shell)
        self.library_screen.move_masthead_to(shell)

    def _dissolve_header(self, outer: QVBoxLayout) -> None:
        """Remove the header row and rehome the parts of it that stay live.

        Everything the header carried already had somewhere to go: the panels
        have permanent headings, the status bar was nearly idle, and the
        project name is in the window title. Shrinking the row could only win
        a few pixels, because a control plus its margins has a floor -
        deleting the row wins all of it.
        """
        self.workflow_ribbon.set_compact(True)
        header = next(
            (outer.itemAt(index).layout() for index in range(outer.count())
             if isinstance(outer.itemAt(index).layout(), QHBoxLayout)),
            None,
        )
        heading_row = getattr(self, "_player_heading_row", None)
        if header is None or heading_row is None:
            # Layout changed upstream: keep the ribbon on its own row rather
            # than losing the only Detect/Review/Export control.
            self.workflow_ribbon.set_compact(False)
            outer.insertWidget(0, self.workflow_ribbon)
            return

        # Nothing already in the header is moved. Detaching a live widget and
        # re-adding it under a different parent corrupted the heap here: the
        # crash surfaced later, in an unrelated allocation, which is what made
        # it look intermittent. Instead the originals stay put and hidden, and
        # cheap mirrors carry their text onto rows that already exist.
        # The app-level menu is the permanent centered navigation. The old
        # Detect / Review / Export ribbon remains alive for command routing,
        # but no longer consumes the middle of the player heading.
        self.workflow_ribbon.hide()
        heading_row.addStretch(1)

        self.preview_chip = QLabel(self.preview_source_label.text())
        self.preview_chip.setProperty("role", "eyebrow")
        self.preview_chip.setToolTip(self.preview_source_label.toolTip())
        heading_row.addWidget(self.preview_chip)
        # Option 4 gives the film the full row beneath the masthead. The
        # source state remains mirrored for tooltips/status consumers, but it
        # no longer creates a mostly-empty player heading strip.
        self.preview_chip.hide()

        self.player_detach_button = QToolButton()
        self.player_detach_button.setObjectName("V2PlayerDetachButton")
        self.player_detach_button.setProperty("quiet", "true")
        self.player_detach_button.setAccessibleName(
            "Pop out the video player")
        self.player_detach_button.clicked.connect(
            self._toggle_player_floating)
        self.player_detach_button.hide()

        self.player_fullscreen_button = QToolButton()
        self.player_fullscreen_button.setObjectName(
            "V2PlayerFullscreenButton")
        self.player_fullscreen_button.setProperty("quiet", "true")
        self.player_fullscreen_button.setAccessibleName(
            "Enter full screen video player")
        self.player_fullscreen_button.clicked.connect(
            self._toggle_player_fullscreen)
        heading_row.addWidget(self.player_fullscreen_button)
        self.player_fullscreen_button.hide()
        self._sync_player_dock_controls(
            self._workspace_docks["player"].isFloating())

        # Permanent, not normal: showMessage() hides ordinary status widgets,
        # so "PLAYBACK ACTIVE" was blanking the project name.
        self.project_status_label = QLabel(self.project_label.text())
        self.project_status_label.setProperty("role", "subtle")
        self.statusBar().addPermanentWidget(self.project_status_label)
        self.review_status_label = QLabel(self.review_label.text())
        self.review_status_label.setProperty("role", "subtle")
        self.statusBar().addPermanentWidget(self.review_status_label)

        # Keep automatic detection visible at the point where clips are
        # managed.  The workflow ribbon is intentionally hidden in Review, so
        # relying on it (or burying detection in the New Clip menu) made the
        # full-film detector effectively undiscoverable.
        if hasattr(self.clip_list, "header_actions_row"):
            detect = QToolButton()
            detect.setObjectName("ClipLedgerDetectPlays")
            detect.setText("Detect Plays")
            detect.setProperty("detectAction", "true")
            detect.setMinimumWidth(86)
            detect.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            detect.setToolTip(
                "Scan the full loaded game film and create detected play clips "
                "(Beta, All-22 only)")
            detect.setAccessibleName(
                "Detect plays in the full loaded game film")
            detect.clicked.connect(self._v2_detect)
            self.clip_list.header_actions_row.addWidget(detect, 1)
            self.detect_plays_button = detect

            export = QToolButton()
            export.setObjectName("ClipLedgerExport")
            export.setText("Export")
            export.setProperty("exportAction", "true")
            export.setMinimumWidth(64)
            export.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            export.setToolTip(
                "Configure and export the clips selected in the Clip Ledger")
            export.setAccessibleName(
                "Export selected clips from the Clip Ledger")
            export.clicked.connect(self._focus_selected_export)
            self.clip_list.header_actions_row.addWidget(export, 1)
            self.review_export_button = export
            self.clip_list.selection_changed.connect(
                self._sync_review_export_button)
            self._sync_review_export_button(
                self.clip_list.selected_clip_ids())

        # A second button sharing the original's menu, rather than the
        # original button under a new parent.
        source = next(
            (button for button in self.workspace.findChildren(QToolButton)
             if button.text().replace("&", "") == "New Clip"), None)
        if source is not None and hasattr(
                self.clip_list, "header_actions_row"):
            twin = QToolButton()
            twin.setText("New Clip")
            twin.setProperty("accent", "true")
            twin.setMinimumWidth(76)
            twin.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            twin.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            twin.setMenu(source.menu())
            self.clip_list.header_actions_row.addWidget(twin, 1)
            self.new_clip_button = twin
        if hasattr(self.clip_list, "header_actions"):
            self.clip_list.header_actions.show()

        # Load Video was the only route to its dialog, so it gains a File menu
        # entry rather than disappearing with the row.
        self._rehome_load_video()

        # What is left never changes while editing. Filename, size, resolution
        # and codec are reference data and live in Project Settings.
        for index in range(header.count()):
            widget = header.itemAt(index).widget()
            if widget is not None:
                widget.hide()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(0)
        self._header_layout = header

    def _sync_header_mirrors(self) -> None:
        """Copy the hidden header labels onto their visible stand-ins."""
        if getattr(self, "project_status_label", None) is not None:
            self.project_status_label.setText(self.project_label.text())
        if getattr(self, "review_status_label", None) is not None:
            self.review_status_label.setText(self.review_label.text())
        chip = getattr(self, "preview_chip", None)
        if chip is not None:
            chip.setText(self.preview_source_label.text())
            chip.setToolTip(self.preview_source_label.toolTip())
            chip.setProperty(
                "role", self.preview_source_label.property("role"))
            chip.style().unpolish(chip)
            chip.style().polish(chip)
        self._sync_review_masthead()

    def _sync_review_masthead(self) -> None:
        """Mirror live project progress into the full-width Review masthead."""
        button = getattr(self, "review_project_button", None)
        if button is None:
            return
        session = self.session
        if session is None:
            button.setText("No project")
            self.review_progress_label.setText("0 / 0 clips reviewed")
            self.review_progress.setValue(0)
            return
        button.setText(session.project.name)
        total = len(session.clips)
        # Same word and same predicate as the ledger's "N logged". The
        # masthead used to count any clip carrying details and call it
        # "reviewed", so it could disagree with the ledger sitting right
        # under it - the ledger additionally requires the clip be enabled.
        logged = sum(
            1 for clip in session.clips
            if clip.enabled and not needs_logging(clip))
        self.review_progress_label.setText(f"{logged} / {total} logged")
        self.review_progress.setValue(
            round(logged / total * 100) if total else 0)

    def _autosave(self) -> None:
        dirty = bool(self.session and self.session.dirty)
        super()._autosave()
        if dirty and getattr(self, "review_autosave_label", None) is not None:
            self.review_autosave_label.setText("Autosaved just now")

    def _set_preview_source_label(self, mode: str) -> None:
        super()._set_preview_source_label(mode)
        self._sync_header_mirrors()

    def _update_review_label(self) -> None:
        super()._update_review_label()
        self._sync_header_mirrors()
        self._sync_voiceover_deck()

    def _refresh_clip_list(self) -> None:
        super()._refresh_clip_list()
        if hasattr(self, "review_export_button"):
            self._sync_review_export_button(
                self.clip_list.selected_clip_ids())
        self._sync_header_mirrors()
        self._sync_voiceover_deck()

    def _sync_voiceover_deck(self) -> None:
        coordinator = getattr(self, "voiceover_deck", None)
        if coordinator is not None:
            coordinator.sync()

    def _activate_session(self, session) -> bool:
        if not super()._activate_session(session):
            return False
        if self.snap_calibration_session is not None:
            self.setWindowTitle(
                f"{session.project.name} - TapeSift Snap Calibration")
            self.clip_editor.setEnabled(False)
            self.clip_editor.setToolTip(
                "Project details are read-only in Snap Calibration.")
        elif self.temporal_review_session is not None:
            self.setWindowTitle(
                f"{session.project.name} - TapeSift Temporal Review")
            self.clip_editor.setEnabled(False)
            self.clip_editor.setToolTip(
                "Project details are read-only in Temporal Review.")
        self._sync_header_mirrors()
        self._workspace_page_changed(self.stack.currentIndex())
        self._sync_voiceover_deck()
        self._export_bridge_generation += 1
        self._restore_export_queue_async()
        return True

    def _close_project(self) -> bool:
        # closeEvent already saved the pre-teardown workspace on full exit.
        if self.session is not None and not self._app_closing:
            self._exit_player_fullscreen()
            self._normalize_transient_workspace_window_states()
            self._save_workspace_now()
        if not self._cancel_running_export_for_close():
            return False
        if not super()._close_project():
            # Declined - the project is still open, so leave the workspace
            # exactly as it was rather than reflowing it for a start screen
            # that is not coming.
            return False
        if not self._app_closing:
            self._apply_workspace_visibility()
            self._sync_voiceover_deck()
        return True

    def _before_session_close(self) -> None:
        self._cancel_export_bridge_threads(wait=True)
        super()._before_session_close()
        coordinator = getattr(self, "voiceover_deck", None)
        if coordinator is not None:
            coordinator.prepare_session_close()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # The compact deck lives inside the player, leaving no visible bottom
        # dock to participate in Qt's vertical ratio. Re-assert the player's
        # requested height after shell maximize/restore and ordinary resizing.
        timer = getattr(self, "_workspace_resize_timer", None)
        stack = getattr(self, "stack", None)
        control_dock = getattr(self, "_control_center_dock", None)
        if timer is not None and stack is not None \
                and stack.currentWidget() is getattr(self, "workspace", None) \
                and control_dock is not None \
                and control_dock.widget() is None \
                and getattr(self, "_workspace_stage", "review") == "review":
            timer.start(0)

    def closeEvent(self, event) -> None:
        self._app_closing = True
        self._exit_player_fullscreen()
        self._normalize_transient_workspace_window_states()
        for timer in (
                self._workspace_save_timer,
                self._workspace_restore_timer,
                self._workspace_recovery_timer,
                self._workspace_resize_timer,
                self._workspace_window_state_timer):
            timer.stop()
        self._save_workspace_now()
        super().closeEvent(event)
        if event.isAccepted():
            coordinator = getattr(self, "voiceover_deck", None)
            if coordinator is not None:
                coordinator.close()
        else:
            self._app_closing = False
            self._sync_voiceover_deck()

    def _rehome_load_video(self) -> None:
        # Bind the menu to a name: calling action.menu() inside a generator
        # hands back a wrapper whose C++ object is collected before use.
        file_menu = None
        for action in self.menuBar().actions():
            if action.text().replace("&", "") != "File":
                continue
            file_menu = action.menu()
            break
        button = next(
            (item for item in self.workspace.findChildren(QPushButton)
             if item.text().replace("&", "").startswith("Load Video")), None)
        if file_menu is None or button is None:
            return
        self._load_video_action = file_menu.addAction("Load Video…")
        self._load_video_action.triggered.connect(self._browse_video)
        # Owned by the window, not the menu: this menu is rebuilt when the
        # workspace is, and an action the menu owns dies with it.
        if getattr(self, "export_heatmap_action", None) is None:
            self.export_heatmap_action = QAction(
                "Export Game Heat Map…", self)
            self.export_heatmap_action.triggered.connect(self._export_heatmap)
        file_menu.addAction(self.export_heatmap_action)
        if os.environ.get("TAPESIFT_ENABLE_PHONE_COMPANION") == "1":
            if getattr(self, "companion_action", None) is None:
                self.companion_action = QAction("Phone Companion…", self)
                self.companion_action.setToolTip(
                    "Browse and tag these projects from a phone on the same "
                    "Wi-Fi or over Tailscale, and sync what it collected")
                self.companion_action.triggered.connect(self._open_companion)
            file_menu.addAction(self.companion_action)

    def _open_companion(self) -> None:
        """Serve the whole project folder, not just whatever is open.

        The phone is for looking things up across games - "every third and
        long this season" - so scoping this to the current project would
        answer a narrower question than the one being asked.
        """
        from tapesift.ui_v2.companion_dialog import CompanionDialog

        folder = Path(self.settings.default_project_folder or "")
        if self.session is not None:
            # A project open from somewhere else is the better clue about
            # where this person actually keeps their work.
            here = Path(self.session.project_path).parent
            if here.is_dir():
                folder = here
        if not folder.is_dir():
            QMessageBox.information(
                self, "Phone Companion",
                "No project folder yet. Create or open a project first.")
            return
        dialog = CompanionDialog(
            folder, self,
            current_project=(Path(self.session.project_path)
                             if self.session is not None else None))
        dialog.exec()

    def _inspector_folded(self, collapsed: bool) -> None:
        """Give the dock back the width the panel just released, or asked
        for. Qt sizes docks, not their contents, so the panel folding is
        only half of it - without this the video keeps the old column."""
        dock = getattr(self, "_workspace_docks", {}).get("play_details")
        if dock is None:
            return
        # The dock contains a scroll wrapper, so changing only ClipEditor's
        # width leaves the wrapper's 190px floor in force.  Keep the wrapper
        # and its child on the same floor or an interactive fold returns only
        # half of the inspector column to the player.
        details_scroll = getattr(self, "clip_details_scroll", None)
        open_floor = int(getattr(
            self, "_clip_details_open_minimum_width", 190))
        if details_scroll is not None:
            details_scroll.setFixedWidth(
                COLLAPSED_WIDTH if collapsed else open_floor)
            details_scroll.updateGeometry()
        want = COLLAPSED_WIDTH if collapsed else max(
            self.clip_editor.open_width(), open_floor)
        self.resizeDocks(
            [dock], [want], Qt.Orientation.Horizontal)
        # Remembered, so the panel is where it was left next launch.
        if getattr(self.settings, "workspace_play_details_collapsed",
                   None) != collapsed:
            self.settings.workspace_play_details_collapsed = collapsed
            self.settings.save()

    def _export_heatmap(self) -> None:
        """The whole game as one picture, in whatever formats are wanted."""
        session = getattr(self, "session", None)
        clips = list(getattr(session, "clips", ()) or ())
        if not clips:
            QMessageBox.information(
                self, "Export Game Heat Map",
                "Log some plays first - the heat map is built from them.")
            return
        name = getattr(session, "project_name", "") or "Game"
        data = build_heatmap(
            clips, title="Game Heat Map",
            subtitle=f"{name}  .  {len(clips)} plays")
        folder = Path(
            getattr(session, "export_folder", None)
            or getattr(session, "project_folder", None) or Path.home())
        stem = f"{name.replace(' ', '_').lower()}_heatmap"
        dialog = HeatmapExportDialog(data, folder, stem, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        kinds = ", ".join(
            item.suffix.lstrip(".").upper() for item in dialog.written)
        self.statusBar().showMessage(
            f"Heat map exported ({kinds}) to {dialog.written[0].parent}", 8000)

    def _build_review_workspace(self, outer: QVBoxLayout) -> None:
        """Make the accepted workbench's panels native, authoritative docks."""
        old_workspace = next(
            (outer.itemAt(index).widget() for index in range(outer.count())
             if isinstance(outer.itemAt(index).widget(), QSplitter)
             and outer.itemAt(index).widget().orientation()
             == Qt.Orientation.Vertical),
            None,
        )
        if old_workspace is None:
            return
        old_index = outer.indexOf(old_workspace)

        player_panel = QWidget(self.workspace)
        player_panel.setObjectName("V2ReviewPlayerPanel")
        player_panel.setProperty("reviewPanel", "true")
        player_layout = QVBoxLayout(player_panel)
        # Tight at the bottom: the Tag Map is the final player surface, and a
        # skirt under it would look like part of the dead band we are removing.
        player_layout.setContentsMargins(0, 0, 0, 0)
        player_layout.setSpacing(0)
        self._player_heading_row = QHBoxLayout()
        self._player_heading_row.setContentsMargins(0, 0, 0, 0)
        self._player_heading_row.setSpacing(8)
        player_layout.addLayout(self._player_heading_row)
        # The smooth-scrub offer belongs to the playback surface. Keeping it
        # here also makes it available when the complete player is floating
        # on a second display and the otherwise-empty central page collapses.
        outer.removeWidget(self.proxy_banner_widget)
        player_layout.addWidget(self.proxy_banner_widget)
        self.player.setProperty("panel", "false")
        # Option 4 fixes the two work surfaces and lets the film own the
        # remainder. The old 1180px floor made that impossible at 1708px and
        # forced the transport underneath the inspector. DockV2 now carries
        # its own compact contract, so the player must not add another floor.
        self.player.setMinimumWidth(0)
        self.player.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        # Telestration data and implementation remain live, but the selected
        # Review shell has no drawing rail. Hiding the existing widget keeps
        # the film surface and stored marks intact without cloning or deleting
        # any engine state.
        self.player.telestration_rail.hide()
        # Keep zoom directly reachable without restoring a second toolbar.
        # Dock V2 gives the existing controls one compact home beside the
        # centred playback island; no timeline or playback behavior is cloned.
        for control in (
                self.player.total_label,
                self.player.timeline_fit_game,
                self.player.volume_btn,
                self.player.timeline_legend,
                self.player.timeline_range_label):
            control.hide()
        for control in (
                self.player.timeline_zoom_out,
                self.player.timeline_zoom_in,
                self.player.timeline_fit_play,
                self.player.predicted_snap_button):
            control.show()
        if isinstance(self.control_center, DockV2Deck):
            self.control_center.mount_viewport_controls(
                self.player.timeline_zoom_out,
                self.player.timeline_zoom_in,
                self.player.timeline_fit_play,
                self.player.predicted_snap_button,
            )
            self.player.view_strip.hide()
        else:
            # The rollback console has no inline viewport mount. Preserve its
            # existing direct-access strip instead of dropping a capability.
            self.player.view_strip.show()
        player_layout.addWidget(self.player, 1)
        favorites = (
            serialize_quick_tags(RUN_PASS_LAB_TAGS)
            if self.run_pass_lab
            else self.settings.quick_tag_favorites
        )
        self.quick_tag_tray = QuickTagTray(favorites, player_panel)
        if self.run_pass_lab:
            self.quick_tag_tray.heading.setText("RUN / PASS LAB")
            self.quick_tag_tray.add_button.hide()
            self.quick_tag_tray.manage_button.hide()
        self.quick_tag_tray.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.quick_tag_tray.tag_requested.connect(self._apply_quick_tag)
        self.quick_tag_tray.manage_requested.connect(
            self._manage_quick_tags)
        self.temporal_review_panel = None
        self.snap_calibration_panel = None
        if (
            self.temporal_review_session is None
            and self.snap_calibration_session is None
        ):
            # The rail rides in the Tag Map header, keeping labels beside the
            # data they change and leaving the six-zone deck unambiguous.
            self.quick_tag_tray.set_inline(True)
            self.player.mount_quick_tags(self.quick_tag_tray)
            # The generated lock puts the Quick Tags in a 42px header. The
            # inherited tray stylesheet otherwise reports a 62px size hint,
            # leaving a blank shelf below 24px chips and pushing the first
            # AttributeGrid row 20px off the standard baseline.
            self.quick_tag_tray.setFixedHeight(28)
            self.player.quick_tag_slot.setFixedHeight(28)
            self.player.timeline_header.setFixedHeight(42)
        elif self.temporal_review_session is not None:
            self.quick_tag_tray.hide()
            self.temporal_review_panel = TemporalReviewPanel(
                self.temporal_review_session, player_panel)
            self.temporal_review_panel.item_requested.connect(
                self._temporal_review_item_requested)
            self.temporal_review_panel.inspect_requested.connect(
                self._temporal_review_inspect_requested)
            player_layout.addWidget(self.temporal_review_panel)
        else:
            self.quick_tag_tray.hide()
            self.snap_calibration_panel = SnapCalibrationPanel(
                self.snap_calibration_session, player_panel)
            self.snap_calibration_panel.item_requested.connect(
                self._snap_calibration_item_requested)
            self.snap_calibration_panel.inspect_requested.connect(
                self._snap_calibration_inspect_requested)
            self.snap_calibration_panel.exact_snap_requested.connect(
                self._snap_calibration_exact_requested)
            player_layout.addWidget(self.snap_calibration_panel)

        self.clip_list.setObjectName("V2ReviewClipList")
        self.clip_list.setMinimumWidth(self.REVIEW_LEDGER_WIDTH)
        self.clip_list.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.clip_list.set_sidebar_mode(True)
        self.clip_list.selection_changed.connect(
            self._quick_tag_selection_changed)
        self.clip_editor.setObjectName("V2ReviewInspector")
        # 330 was wider than the dock users actually drag to, so the
        # panel rendered at 330 and got clipped - Q4 and Manage fell
        # off the right edge. The rows restack instead now.
        # Verified floor: at 190 every row still restacks inside the
        # dock with nothing crossing the edge. Below it the RESULT
        # header's own buttons take over and the panel clips again.
        self._clip_details_open_minimum_width = \
            self.REVIEW_INSPECTOR_WIDTH
        self.clip_editor.setMinimumWidth(
            self._clip_details_open_minimum_width)
        self.clip_editor.collapse_changed.connect(self._inspector_folded)
        if getattr(self.settings, "workspace_play_details_collapsed", False):
            self.clip_editor.set_collapsed(True)
        self.clip_editor.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.clip_editor.set_analyst_mode(True)
        self.clip_editor.package_requested.connect(self._focus_export)

        # The ledger and inspector stop at the control center and scroll on
        # their own axes. Keeping these as two explicit scroll surfaces means
        # a long ledger never pushes details off-screen, and expanding detail
        # rows never changes the player's or deck's height.
        self.clip_ledger_scroll = QScrollArea(self.workspace)
        self.clip_ledger_scroll.setObjectName("V2ClipLedgerScroll")
        self.clip_ledger_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.clip_ledger_scroll.setWidgetResizable(True)
        self.clip_ledger_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.clip_ledger_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.clip_ledger_scroll.setFixedWidth(self.REVIEW_LEDGER_WIDTH)
        self.clip_ledger_scroll.setWidget(self.clip_list)

        self.clip_details_scroll = QScrollArea(self.workspace)
        self.clip_details_scroll.setObjectName("V2ClipDetailsScroll")
        self.clip_details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.clip_details_scroll.setWidgetResizable(True)
        self.clip_details_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.clip_details_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        details_width = (
            COLLAPSED_WIDTH if self.clip_editor.is_collapsed()
            else self._clip_details_open_minimum_width)
        self.clip_details_scroll.setFixedWidth(details_width)
        self.clip_details_scroll.setWidget(self.clip_editor)

        # Number keys apply the quick tag in that slot, matching the rail's
        # left-to-right order. They route through _apply_quick_tag, so they
        # share the toggle, save and undo behaviour of clicking the button.
        self._quick_tag_shortcuts: list[QShortcut] = []
        for slot in range(1, 10):
            shortcut = QShortcut(QKeySequence(str(slot)), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(
                lambda index=slot - 1: self._quick_tag_by_slot(index))
            self._quick_tag_shortcuts.append(shortcut)

        # Run and pass are the two calls made on nearly every play, so they
        # get bare keys rather than a tray slot the hand has to find. Replay
        # moves to Shift+R: it is the accessory, and R is pressed hundreds of
        # times in a logging session. Kept out of _quick_tag_shortcuts, which
        # means the nine number-key slots and nothing else.
        self._label_shortcuts: list[QShortcut] = []
        # Move the registry entry with the key, not just the binding. The
        # transport table is asserted to hold exactly one shortcut per key
        # and to be the thing that owns it; leaving replay filed under "R"
        # while it answers to Shift+R makes that table quietly untrue.
        transport = getattr(self, "_transport_shortcuts", {})
        replay = transport.pop("R", None)
        if replay is not None:
            replay.setKey(QKeySequence("Shift+R"))
            transport["Shift+R"] = replay
        for label_key, tag_key in (("R", "run"), ("P", "pass")):
            shortcut = QShortcut(QKeySequence(label_key), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.activated.connect(
                lambda key=tag_key: self._quick_tag_by_key(key))
            self._label_shortcuts.append(shortcut)

        outer.removeWidget(old_workspace)
        self._player_placeholder = QWidget(self.workspace)
        self._player_placeholder.setObjectName("V2PlayerPlaceholder")
        placeholder_layout = QVBoxLayout(self._player_placeholder)
        placeholder_layout.setContentsMargins(24, 24, 24, 24)
        placeholder_layout.addStretch(1)
        placeholder_label = QLabel(
            "The Video Player is hidden.")
        placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_label.setProperty("role", "muted")
        placeholder_layout.addWidget(placeholder_label)
        self._show_player_button = QPushButton("Show Video Player")
        self._show_player_button.setProperty("primary", "true")
        self._show_player_button.clicked.connect(self._show_player_dock)
        placeholder_layout.addWidget(
            self._show_player_button, 0, Qt.AlignmentFlag.AlignHCenter)
        placeholder_layout.addStretch(1)
        self._player_placeholder.hide()
        # outer shed rows while the docks were built, so the index captured
        # before the teardown can exceed the current count; clamp it rather
        # than relying on Qt's out-of-range append (and its warning).
        outer.insertWidget(
            min(old_index, outer.count()), self._player_placeholder, 1)
        old_workspace.deleteLater()

        self._player_panel = player_panel
        self.setDockNestingEnabled(True)
        self.setCorner(
            Qt.Corner.BottomLeftCorner,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.setCorner(
            Qt.Corner.BottomRightCorner,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.setCorner(
            Qt.Corner.TopLeftCorner,
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.setCorner(
            Qt.Corner.TopRightCorner,
            Qt.DockWidgetArea.RightDockWidgetArea,
        )

        self._workspace_state_guard = True
        dock_specs = (
            (
                "player", "VIDEO PLAYER", "tapesift.dock.player",
                player_panel, Qt.DockWidgetArea.TopDockWidgetArea,
                "workspace_player_visible",
            ),
            (
                "clips", "CLIPS", "tapesift.dock.clips",
                self.clip_ledger_scroll,
                Qt.DockWidgetArea.LeftDockWidgetArea,
                "workspace_clips_visible",
            ),
            (
                "play_details", "PLAY DETAILS",
                "tapesift.dock.play_details",
                self.clip_details_scroll,
                Qt.DockWidgetArea.RightDockWidgetArea,
                "workspace_play_details_visible",
            ),
        )
        for key, title, object_name, widget, area, setting_name in dock_specs:
            dock = QDockWidget(title, self)
            dock.setObjectName(object_name)
            # All four surfaces are locked in the main workspace. The player
            # still floats programmatically through its explicit Pop Out
            # command; NoDockWidgetFeatures prevents title-bar dragging from
            # entering Qt's fragile native dock-move loop on Windows.
            dock.setFeatures(
                QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
            # Panel names live inside their content in the standard layout.
            # A zero-height dock title removes the duplicate CLIPS / VIDEO
            # PLAYER chrome while preserving real QDockWidget behavior.
            dock_title = QWidget(dock)
            dock_title.setFixedHeight(0)
            dock.setTitleBarWidget(dock_title)
            dock.setAllowedAreas(area)
            dock.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            dock.setWidget(widget)
            self.addDockWidget(area, dock)
            dock.hide()
            dock.installEventFilter(self)
            dock.visibilityChanged.connect(
                lambda visible, dock_key=key:
                self._workspace_dock_visibility_changed(
                    dock_key, visible))
            dock.topLevelChanged.connect(
                lambda floating, dock_key=key:
                self._workspace_dock_top_level_changed(
                    dock_key, floating))
            if key == "player":
                dock.dockLocationChanged.connect(
                    lambda _area: self._schedule_workspace_save())
            self._workspace_docks[key] = dock
            self._default_dock_areas[dock] = area
            self._dock_visibility[key] = bool(
                getattr(self.settings, setting_name))

        # The window owns and constructs the one control-center widget tree;
        # VideoPlayer is attached only as its behavior/media authority.
        self._control_center_dock = QDockWidget("CONTROL CENTER", self)
        self._control_center_dock.setObjectName(
            "tapesift.dock.control_center")
        self._control_center_dock.setFeatures(
            QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        control_center_title = QWidget(self._control_center_dock)
        control_center_title.setFixedHeight(0)
        self._control_center_dock.setTitleBarWidget(control_center_title)
        self._control_center_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea)
        control_center = self.control_center
        control_center.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        if isinstance(control_center, DockV2Deck):
            # The compact strip mounts in the film column. A band spanning the
            # whole window would centre Play on the window rather than the
            # film between two panels of different widths. The dock object
            # remains as a zero-height workspace placeholder.
            self.player.mount_control_strip(control_center)
            self._control_center_dock.setMinimumHeight(0)
            self._control_center_dock.setMaximumHeight(0)
        else:
            # The rollback deck is a 197px full-width console. Squeezing it
            # into the film column makes its zones overlap at the supported
            # 1260px floor, so preserve its original bottom-dock geometry.
            self._control_center_dock.setWidget(control_center)
            control_center_height = control_center.sizeHint().height()
            self._control_center_dock.setMinimumHeight(control_center_height)
            self._control_center_dock.setMaximumHeight(control_center_height)
        self.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea,
            self._control_center_dock,
        )
        self._control_center_dock.hide()
        self._default_dock_areas[self._control_center_dock] = \
            Qt.DockWidgetArea.BottomDockWidgetArea

        # Export is a real bottom docking surface so it can replace Tag Map
        # without replacing or recreating the authoritative player. It is
        # fixed and intentionally omitted from Window.
        self._export_dock = QDockWidget("EXPORT QUEUE", self)
        self._export_dock.setObjectName("tapesift.dock.export_stage")
        self._export_dock.setFeatures(
            QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)
        export_title = QWidget(self._export_dock)
        export_title.setFixedHeight(0)
        self._export_dock.setTitleBarWidget(export_title)
        self._export_dock.setAllowedAreas(
            Qt.DockWidgetArea.BottomDockWidgetArea)
        self._export_dock.setWidget(self.export_panel)
        self.addDockWidget(
            Qt.DockWidgetArea.BottomDockWidgetArea, self._export_dock)
        self._export_dock.hide()
        self._default_dock_areas[self._export_dock] = \
            Qt.DockWidgetArea.BottomDockWidgetArea

        # The standard workbench labels the content itself: CLIP LEDGER
        # stays legible even when the panel is popped out.
        self.clip_list.heading_label.setText("CLIP LEDGER")
        self.clip_list.heading_label.show()
        self._build_window_menu()
        self._workspace_state_guard = False

    def _quick_tag_selection_changed(self, clip_ids: list[str]) -> None:
        clip = self.session.get_clip(clip_ids[0]) \
            if self.session and len(clip_ids) == 1 else None
        self.quick_tag_tray.set_clip(clip)

    def _quick_tag_by_slot(self, index: int) -> None:
        """Apply the index-th quick tag, if there is one and a play is open."""
        if self._typing_in_text_field():
            return
        tray = getattr(self, "quick_tag_tray", None)
        if tray is None or not self._selected_clip_id:
            return
        if index >= len(tray.tags):
            return
        self._apply_quick_tag(tray.tags[index].key)

    def _quick_tag_by_key(self, key: str) -> None:
        """Apply one named quick tag, for the Run/Pass Lab's R and P keys."""
        if self._typing_in_text_field():
            return
        if getattr(self, "quick_tag_tray", None) is None:
            return
        if not self._selected_clip_id:
            return
        self._apply_quick_tag(key)

    def _apply_quick_tag(self, key: str) -> None:
        """Toggle one football label through the existing save/undo path."""
        if not self.session or not self._selected_clip_id:
            return
        clip = self.session.get_clip(self._selected_clip_id)
        values = self.quick_tag_tray.details(key)
        if clip is None or not values:
            return
        was_active = all(
            result_service.has_result(
                clip.details.get(field, ""), value)
            if field == "result"
            else clip.details.get(field, "").strip().casefold()
            == value.casefold()
            for field, value in values.items()
        )
        if was_active:
            applied_values = {
                field: (
                    result_service.remove_result(
                        clip.details.get(field, ""), value)
                    if field == "result"
                    else ""
                )
                for field, value in values.items()
            }
        else:
            applied_values = dict(values)
        if "result" in applied_values and not was_active:
            applied_values["result"] = result_service.add_results(
                clip.details.get("result", ""),
                applied_values["result"],
            )
        # Plain Run/Pass means "switch away from" an RPO or Screen concept.
        # The two explicit RPO actions are how both facts are set together.
        if key in {"run", "pass"} and \
                clip.details.get("play_type", "").casefold() in {
                    "rpo", "screen", "scramble",
                }:
            applied_values["play_type"] = ""
        if key == "sack" and \
                clip.details.get("play_type", "").casefold() == "scramble":
            applied_values["play_type"] = ""
        if self.clip_editor.apply_quick_details(
                applied_values,
                remove_tag_values=(
                    tuple(values.values()) if was_active else ()
                )):
            self.quick_tag_tray.set_clip(clip)
            label = self.quick_tag_tray.buttons[key].text()
            self.statusBar().showMessage(
                (
                    f"Removed {label} from the selected clip. "
                    if was_active
                    else f"Saved {label} to the selected clip. "
                ) + "Ctrl+Z to undo",
                3500,
            )
        else:
            self.quick_tag_tray.set_clip(clip)

    def _manage_quick_tags(self, add_immediately: bool = False) -> None:
        """Open the persistent favorites editor and refresh the rail."""
        dialog = QuickTagManagerDialog(
            self.quick_tag_tray.tags,
            self.settings.fixed_details,
            QUICK_TAGS,
            self,
            add_immediately=add_immediately,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        tags = dialog.tags()
        self.quick_tag_tray.set_tags(tags)
        self.settings.quick_tag_favorites = serialize_quick_tags(tags)
        self.settings.save()
        self.statusBar().showMessage("Quick tags updated", 2500)

    def _clip_edited(self, clip_id: str) -> None:
        """Keep the quick-tag tray and the Tag Map in step with inspector edits."""
        super()._clip_edited(clip_id)
        clip = self.session.get_clip(clip_id) if self.session else None
        if getattr(self, "quick_tag_tray", None) is not None:
            self.quick_tag_tray.set_clip(clip)
        # The base class updates one clip-list row and one timeline block on
        # purpose, so it never reaches _refresh_timeline_presentation - which
        # is the only thing that rebuilds Tag Map markers. Without this, a
        # quick tag saved fine but the map kept showing the old lanes until
        # something else forced a full refresh. Undo/redo already come through
        # _refresh_clip_list, so they were never affected.

    def _advance_after_save(self) -> None:
        """Save + Next is explicit in V2, independent of F5 review playback."""
        self._goto_clip(1)

    def _v2_detect(self) -> None:
        self.workflow_ribbon.set_active("detect")
        try:
            self._detect_plays()
        finally:
            self._set_workspace_stage("review")

    def _v2_review(self) -> None:
        # The ribbon changes workspace stage. F5 remains the dedicated control
        # for sequential review/autoplay mode.
        self._set_workspace_stage("review")

    def _toggle_review_mode(self) -> None:
        super()._toggle_review_mode()
        self._set_workspace_stage("review")

    def _focus_export(self) -> None:
        self._review_export_scope_ids = None
        self._set_workspace_stage("export")
        self._refresh_export_package_context()
        super()._focus_export()
        self.export_panel.focus_primary_control()

    def _deck_export_style_requested(self, style: str) -> None:
        """Select a style, then reveal the authoritative ExportPanel."""

        self.export_panel.set_export_style(style)
        self._focus_export()

    def _composited_selected_clips(self) -> list:
        """Resolve exactly the enabled ledger selection, never all project clips."""

        if self.session is None:
            return []
        ids = list(self._review_export_scope_ids or ())
        if not ids:
            ids = list(self.clip_list.selected_clip_ids())
        if not ids and self._selected_clip_id:
            ids = [self._selected_clip_id]
        seen: set[str] = set()
        clips = []
        for clip_id in ids:
            if clip_id in seen:
                continue
            seen.add(clip_id)
            clip = self.session.get_clip(clip_id)
            if clip is not None and clip.enabled:
                clips.append(clip)
        return clips

    def _refresh_export_package_context(self) -> None:
        """Load template/take metadata without pretending live playback is VO."""

        try:
            repository = SQLiteSignatureTemplateRepository(
                core_paths.app_data_dir())
            try:
                records = repository.list_records()
            finally:
                repository.close()
        except Exception as exc:
            self.export_panel.set_signature_templates(
                (), load_error=f"Template library unavailable: {exc}")
        else:
            self.export_panel.set_signature_templates(records)

        clips = self._composited_selected_clips()
        self.export_panel.set_composited_selection(clips)
        clip = clips[0] if len(clips) == 1 else None
        image = self.player.video_widget.current_frame_image()
        voiceover_waveform = None
        voiceover_take_id = ""
        voiceover_frame_count = 0
        voiceover_has_presentation_track = False
        voiceover_label = ""
        if self.session is not None and clip is not None:
            try:
                summaries = VoiceoverRepository(
                    self.session.conn).list_summaries_for_clip(
                        self.session.project.id, clip.id)
            except Exception:
                summaries = ()
            selected_take = next(
                (take for take in summaries if take.selected), None)
            if selected_take is not None:
                voiceover_waveform = selected_take.waveform
                voiceover_take_id = selected_take.id
                voiceover_frame_count = selected_take.frame_count
                voiceover_has_presentation_track = bool(
                    selected_take.has_presentation_track)
                voiceover_label = selected_take.label

        details = clip.details if clip is not None else {}
        situation = " · ".join(filter(None, (
            details.get("quarter", "").strip(),
            details.get("down_distance", "").strip(),
            details.get("ball_on", "").strip(),
        )))
        concept = "" if clip is None else (
            clip.clip_title.strip() or clip.label.strip())
        result = details.get("result", "").strip()
        values = dict(
            source_video_path=(
                self.session.project.source_video_path
                if self.session is not None else ""),
            clip_id=clip.id if clip is not None else "",
            voiceover_waveform=voiceover_waveform,
            voiceover_take_id=voiceover_take_id,
            voiceover_frame_count=voiceover_frame_count,
            voiceover_has_presentation_track=voiceover_has_presentation_track,
            voiceover_label=voiceover_label,
            play_call_situation=situation,
            play_call_concept=concept,
            play_call_result=result,
            timeline_position=0,
            timeline_duration=max(1, voiceover_frame_count),
        )
        try:
            context = (
                ExportPreviewContext.from_image(image, **values)
                if not image.isNull()
                else ExportPreviewContext(source_frame_png=b"", **values)
            )
        except ValueError:
            context = ExportPreviewContext(source_frame_png=b"", **values)
        self.export_panel.set_preview_context(context)
        if self._export_restore_complete:
            self.export_panel.set_bridge_readiness_error("")

    def _manage_export_templates_requested(self) -> None:
        """Open the real global Template Manager, then refresh the selector."""

        dialog = SignatureTemplateManagerDialog(
            core_paths.app_data_dir(), self)
        dialog.libraryChanged.connect(self._refresh_export_package_context)
        dialog.exec()
        self._refresh_export_package_context()

    def _composited_request_parts(self):
        """Freeze the exact GUI selections used by preview and final staging."""

        if self._compositor_font_error:
            raise ValueError(
                f"Compositor fonts are unavailable: {self._compositor_font_error}")
        if self.session is None:
            raise ValueError("Open a project before exporting.")
        package = self.export_panel.export_package_snapshot()
        if package.style is ExportStyle.CLEAN:
            raise ValueError("Clean export uses the existing queue.")
        context = self.export_panel.preview_context
        if context is None:
            raise ValueError("Select one clip with a saved Voiceover take.")
        take_id = frozen_voiceover_take_id(package)
        if take_id != context.voiceover_take_id:
            raise ValueError(
                "The preview take changed. Wait for the selected take to refresh.")
        clip_ids = self.export_panel.composited_clip_ids
        if len(clip_ids) != 1 or context.clip_id != clip_ids[0]:
            raise ValueError(
                "Signature and Vertical require exactly one enabled selected clip.")
        clip = self.session.get_clip(clip_ids[0])
        if clip is None or not clip.enabled:
            raise ValueError("The selected clip is no longer available for export.")
        choice = self.export_panel.selected_template_choice()
        if choice is None or package.template is None:
            raise ValueError("Choose or create a linked Signature template.")
        if choice.template.identity.border is not None:
            raise ValueError(
                "The selected template includes a border. Linked 16:9 and 9:16 "
                "border behavior is not approved yet; choose a border-free template.")
        metadata = self.session.project.source_metadata
        if metadata.width <= 0 or metadata.height <= 0:
            raise ValueError(
                "Original source dimensions are unavailable. Relink or reload the "
                "source before composited export.")
        source_path = Path(self.session.project.source_video_path)
        if not source_path.is_file():
            raise ValueError(
                f"The linked source video is missing: {source_path}")
        locked = LockedTemplateIdentity.capture(
            package.template, choice.template)
        composition_plan = build_composition_plan(
            package,
            PixelSize(metadata.width, metadata.height),
            template_identity=locked,
        )
        identity = choice.template.identity
        assets = tuple(
            CompositionAssetPayload(role, asset.data)
            for role, asset in (
                (IdentityAssetRole.WORDMARK_LOGO, identity.wordmark_logo),
                (IdentityAssetRole.PROFILE_PHOTO, identity.profile_photo),
            )
            if asset is not None
        )
        return package, composition_plan, assets, context, clip, source_path

    def _request_composited_export_preview(self) -> None:
        if self.export_panel.export_style is ExportStyle.CLEAN:
            return
        previous = self._export_preview_worker
        if previous is not None:
            previous.cancel()
            previous.finished.connect(previous.deleteLater)
        try:
            package, plan, assets, context, _clip, source_path = \
                self._composited_request_parts()
        except Exception as exc:
            self.export_panel.set_composited_preview_error(str(exc))
            return
        assert self.session is not None
        self._export_preview_request_id += 1
        request_id = self._export_preview_request_id
        self._export_preview_panel_generation = (
            self.export_panel.composited_preview_generation)
        choice = self.export_panel.selected_template_choice()
        request = SelectedTakePreviewRequest(
            request_id=request_id,
            database_path=Path(self.session.db_path),
            ffmpeg_path=self.settings.ffmpeg_path,
            ffprobe_path=self.settings.ffprobe_path,
            source_video_path=source_path,
            project_id=self.session.project.id,
            clip_id=context.clip_id,
            take_id=context.voiceover_take_id,
            plan=plan,
            assets=assets,
            include_ink=package.include_ink,
            play_call_situation=(
                context.play_call_situation.encode("utf-8")
                if package.include_play_call else None),
            play_call_concept=(
                context.play_call_concept.encode("utf-8")
                if package.include_play_call else None),
            play_call_result=(
                context.play_call_result.encode("utf-8")
                if package.include_play_call and choice is not None
                and choice.template.identity.show_result
                and context.play_call_result else None),
        )
        worker = SelectedTakePreviewThread(request, self)
        self._export_preview_worker = worker
        worker.preview_ready.connect(self._composited_preview_ready)
        worker.preview_failed.connect(self._composited_preview_failed)
        worker.finished.connect(
            lambda w=worker: self._release_export_bridge_worker(
                "_export_preview_worker", w))
        self.export_panel.set_composited_preview_pending()
        worker.start()

    def _composited_preview_ready(
            self, result: SelectedTakePreviewResult) -> None:
        if result.request_id != self._export_preview_request_id \
                or self._export_preview_panel_generation \
                != self.export_panel.composited_preview_generation:
            return
        self.export_panel.set_composited_preview_result(
            result.image,
            take_id=result.take_id,
            audio_frame=result.audio_frame,
            source_position_ms=result.source_position_ms,
            marks_sha256=result.marks_sha256,
        )

    def _composited_preview_failed(self, request_id: int, message: str) -> None:
        if request_id == self._export_preview_request_id \
                and self._export_preview_panel_generation \
                == self.export_panel.composited_preview_generation:
            self.export_panel.set_composited_preview_error(message)

    def _start_composited_export(self) -> None:
        if self.session is None:
            return
        if not self._export_restore_complete:
            self.export_panel.set_bridge_readiness_error(
                "Restoring the durable export queue before starting new work.")
            return
        if self._export_staging_worker is not None \
                or (self.export_worker is not None
                    and self.export_worker.isRunning()):
            self.statusBar().showMessage(
                "Wait for the current export or cancel it first.", 5000)
            return
        current_clips = self._composited_selected_clips()
        if tuple(clip.id for clip in current_clips) \
                != self.export_panel.composited_clip_ids:
            self._refresh_export_package_context()
            self.statusBar().showMessage(
                "Selection changed. The exact-take preview is refreshing.", 5000)
            return
        try:
            package, composition_plan, _assets, _context, clip, source_path = \
                self._composited_request_parts()
            frozen_clips = ExportJobQueueService.freeze_clips_for_staging((clip,))
            project_snapshot = deepcopy(self.session.project)
            technical_plan = export_service.plan_export(
                project_snapshot,
                list(frozen_clips),
                "individual",
                package.technical_preset,
                True,
                self.settings.separator_style,
            )
        except Exception as exc:
            user_text = getattr(exc, "user_text", None)
            message = user_text() if callable(user_text) else str(exc)
            self.export_panel.set_bridge_readiness_error(message)
            return
        if len(technical_plan.jobs) != 1:
            self.export_panel.set_bridge_readiness_error(
                "The selected clip did not produce exactly one export job.")
            return
        if technical_plan.warnings:
            answer = QMessageBox.question(
                self,
                "Export warnings",
                "\n".join(technical_plan.warnings) + "\n\nContinue anyway?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        if not self._try_save():
            return
        job = technical_plan.jobs[0]
        job.status = JobStatus.PREPARING
        hardware = "" if self.settings.hardware_acceleration == "cpu" \
            else self.settings.hardware_acceleration
        request = ExportStagingRequest(
            database_path=Path(self.session.db_path),
            job=job,
            package=package,
            composition_plan=composition_plan,
            source_video_path=source_path,
            clips=frozen_clips,
            hardware_encoder=hardware,
        )
        worker = ExportStagingThread(request, self)
        self._export_staging_worker = worker
        worker.staged.connect(
            lambda payload, w=worker, g=self._export_bridge_generation:
            self._export_input_ready(payload, w, "_export_staging_worker", g))
        worker.staging_failed.connect(
            lambda message, w=worker, g=self._export_bridge_generation:
            self._export_input_failed(message, w, "_export_staging_worker", g))
        worker.staging_cancelled.connect(
            lambda w=worker, g=self._export_bridge_generation:
            self._export_input_failed(None, w, "_export_staging_worker", g))
        worker.finished.connect(
            lambda w=worker: self._release_export_bridge_worker(
                "_export_staging_worker", w))
        self.export_panel.upsert_job(job)
        self.export_panel.set_running(True)
        self.statusBar().showMessage(
            "Preparing immutable source, Voiceover, template, and event inputs…")
        worker.start()

    def _launch_export_plan(self, plan, accurate: bool, *, quick: bool) -> None:
        """Clean jobs use the same durable inputs and retry path as packages."""
        try:
            selected_ids = {cid for job in plan.jobs
                            for cid in ((job.clip_id,) if job.clip_id else job.clip_ids)}
            frozen = ExportJobQueueService.freeze_clips_for_staging(
                tuple(clip for clip in self.session.clips if clip.id in selected_ids))
            clips = {clip.id: clip for clip in frozen}
            project = self.session.project
            hardware = "" if self.settings.hardware_acceleration == "cpu" \
                else self.settings.hardware_acceleration
            requests = []
            for job in plan.jobs:
                package = ExportPackageSnapshot(
                    style=ExportStyle.CLEAN, technical_preset=job.preset_name,
                    accurate_cut=bool(accurate))
                descriptor = build_composition_plan(
                    package, source_size=PixelSize(
                        project.source_metadata.width,
                        project.source_metadata.height))
                selected = (job.clip_id,) if job.clip_id else tuple(job.clip_ids)
                requests.append(ExportStagingRequest(
                    database_path=Path(self.session.db_path), job=job,
                    package=package, composition_plan=descriptor,
                    source_video_path=Path(project.source_video_path),
                    clips=tuple(clips[cid] for cid in selected),
                    hardware_encoder=hardware))
        except Exception as exc:
            self.export_panel.set_bridge_readiness_error(str(exc))
            self.statusBar().showMessage(f"Cannot prepare export: {exc}", 10000)
            return
        worker = ExportStagingThread(
            requests[0], self, remaining_requests=tuple(requests[1:]))
        self._export_staging_worker = worker
        worker.staged.connect(
            lambda payload, w=worker, g=self._export_bridge_generation:
            self._export_input_ready(payload, w, "_export_staging_worker", g))
        worker.staging_failed.connect(
            lambda message, w=worker, g=self._export_bridge_generation:
            self._export_input_failed(message, w, "_export_staging_worker", g))
        worker.staging_cancelled.connect(
            lambda w=worker, g=self._export_bridge_generation:
            self._export_input_failed(None, w, "_export_staging_worker", g))
        worker.finished.connect(
            lambda w=worker: self._release_export_bridge_worker(
                "_export_staging_worker", w))
        for job in plan.jobs:
            job.status = JobStatus.PREPARING
            self.export_panel.upsert_job(job)
        self.export_panel.set_running(True)
        self.statusBar().showMessage("Preparing source and clip ranges for export…")
        worker.start()

    def _export_input_is_current(self, worker, attribute: str, generation: int) -> bool:
        database = (worker.request.database_path if attribute == "_export_staging_worker"
                    else worker.database_path)
        return (self.session is not None and getattr(self, attribute) is worker
                and generation == self._export_bridge_generation
                and Path(database).resolve() == Path(self.session.db_path).resolve())

    def _export_input_failed(self, message, worker, attribute: str, generation: int) -> None:
        if not self._export_input_is_current(worker, attribute, generation):
            if message:
                self.statusBar().showMessage(f"Previous project export: {message}", 10000)
            return
        if attribute == "_export_retry_worker":
            self._snapshot_retry_failed(message)
        elif message is None:
            self._composited_staging_cancelled()
        else:
            self._composited_staging_failed(message)

    def _export_input_ready(self, payload, worker, attribute: str, generation: int) -> None:
        database = (worker.request.database_path if attribute == "_export_staging_worker"
                    else worker.database_path)
        if self._export_input_is_current(worker, attribute, generation):
            if attribute == "_export_staging_worker":
                self._composited_export_staged(payload)
            else:
                self._snapshot_retry_ready(payload)
            return
        # A queued Qt signal can outlive the old session. Cancel only in its DB.
        batch = payload if isinstance(payload, tuple) else (payload,)
        for item in batch:
            job = getattr(item, "job", item)
            if job.status is not JobStatus.WAITING:
                continue
            cleanup = ExportQueuedCancelThread(Path(database), job.id, parent=self)
            self._export_cancel_workers.add(cleanup)
            cleanup.cancel_failed.connect(lambda jid, message: self.statusBar().showMessage(
                f"Could not cancel previous-project export {jid}: {message}", 10000))
            cleanup.finished.connect(lambda w=cleanup: self._release_export_cancel_worker(w))
            cleanup.start()

    def _composited_export_staged(self, persisted) -> None:
        batch = persisted if isinstance(persisted, tuple) else (persisted,)
        if self.session is None or any(
                item.job.project_id != self.session.project.id for item in batch):
            return
        staging_worker = self._export_staging_worker
        for item in batch:
            self.export_panel.upsert_job(item.job)
        if staging_worker is not None and staging_worker.cancel_requested:
            for item in batch:
                self._remove_queued_export(item.job.id)
            return
        self._launch_snapshot_export_worker([item.job for item in batch])

    def _composited_staging_failed(self, message: str) -> None:
        worker = self._export_staging_worker
        if worker is not None:
            for request in worker.requests:
                self.export_panel.remove_job(request.job.id)
        self.export_panel.set_bridge_readiness_error(
            f"Export staging failed: {message}")
        self.statusBar().showMessage(f"Export staging failed: {message}", 10000)

    def _composited_staging_cancelled(self) -> None:
        worker = self._export_staging_worker
        if worker is not None:
            for request in worker.requests:
                self.export_panel.remove_job(request.job.id)
        self.statusBar().showMessage("Export staging cancelled", 5000)

    def _launch_snapshot_export_worker(self, job) -> None:
        jobs = job if isinstance(job, list) else [job]
        if self.session is None or not self._export_restore_complete:
            self.export_panel.set_running(False)
            self.export_panel.set_bridge_readiness_error(
                "Durable queue recovery must finish before rendering starts.")
            return
        worker = ExportWorker(
            self.settings.ffmpeg_path,
            deepcopy(self.session.project),
            jobs,
            {},
            True,
            hardware_encoder=(
                "" if self.settings.hardware_acceleration == "cpu"
                else self.settings.hardware_acceleration),
            database_path=Path(self.session.db_path),
            parent=self,
        )
        self.export_worker = worker
        worker.job_started.connect(self.export_panel.on_job_started)
        worker.job_started.connect(self._job_started)
        worker.job_progress.connect(self.export_panel.on_job_progress)
        worker.job_completed.connect(self.export_panel.on_job_completed)
        worker.job_completed.connect(self._job_completed)
        worker.job_failed.connect(self.export_panel.on_job_failed)
        worker.job_failed.connect(self._job_failed)
        worker.job_cancelled.connect(self.export_panel.on_job_cancelled)
        worker.job_cancelled.connect(self._job_cancelled)
        # Match the Clean export handoff: the queue is available again only
        # after QThread has exited, not while ``run`` is still unwinding.
        worker.finished.connect(
            lambda w=worker: self._snapshot_export_finished(w))
        worker.finished.connect(worker.deleteLater)
        self.export_panel.set_running(True)
        self.statusBar().showMessage(
            f"Exporting {len(jobs)} queued output(s)…")
        worker.start()

    def _snapshot_export_finished(self, worker: ExportWorker) -> None:
        self.export_panel.set_running(False)
        if self.export_worker is worker:
            self.export_worker = None
        if self.session is not None:
            self.session.save()
            self._restore_export_queue_async()

    def _restore_export_queue_async(self) -> None:
        if self.session is None:
            return
        previous = self._export_restore_worker
        if previous is not None:
            previous.cancel()
            previous.finished.connect(previous.deleteLater)
        self._export_restore_complete = False
        self.export_panel.set_bridge_readiness_error(
            "Restoring the durable export queue before starting new work.")
        generation = self._export_bridge_generation
        worker = ExportQueueRestoreThread(
            Path(self.session.db_path),
            self.session.project.id,
            generation,
            self,
        )
        self._export_restore_worker = worker
        worker.summaries_ready.connect(self._export_queue_summaries_ready)
        worker.restore_failed.connect(self._export_queue_restore_failed)
        worker.finished.connect(
            lambda w=worker: self._release_export_bridge_worker(
                "_export_restore_worker", w))
        worker.start()

    def _export_queue_summaries_ready(self, payload) -> None:
        generation, summaries = payload
        if generation != self._export_bridge_generation or self.session is None:
            return
        self._export_restore_complete = True
        self.export_panel.set_bridge_readiness_error("")
        self.export_panel.load_job_summaries(summaries)

    def _export_queue_restore_failed(
            self, generation: int, message: str) -> None:
        if generation != self._export_bridge_generation:
            return
        self._export_restore_complete = False
        self.export_panel.set_bridge_readiness_error(
            f"Durable export queue could not be restored: {message}")

    def _retry_job(self, job_id: str) -> None:
        if self.session is None or not self._export_restore_complete:
            return
        if self._export_retry_worker is not None \
                or self._export_staging_worker is not None \
                or (self.export_worker is not None
                    and self.export_worker.isRunning()):
            return
        job = self.export_panel.jobs.get(job_id)
        if job is None or job.status is not JobStatus.FAILED:
            return
        self.export_panel.set_bridge_readiness_error(
            "Loading the immutable failed job for retry…")
        worker = ExportRetryThread(Path(self.session.db_path), job_id, self)
        self._export_retry_worker = worker
        worker.retry_ready.connect(
            lambda payload, w=worker, g=self._export_bridge_generation:
            self._export_input_ready(payload, w, "_export_retry_worker", g))
        worker.retry_failed.connect(
            lambda message, w=worker, g=self._export_bridge_generation:
            self._export_input_failed(message, w, "_export_retry_worker", g))
        worker.finished.connect(
            lambda w=worker: self._release_export_bridge_worker(
                "_export_retry_worker", w))
        worker.start()

    def _snapshot_retry_ready(self, job) -> None:
        if self.session is None or job.project_id != self.session.project.id:
            return
        self.export_panel.set_bridge_readiness_error("")
        self.export_panel.upsert_job(job)
        if job.status is JobStatus.CANCELLED:
            return
        worker = self._export_retry_worker
        if worker is not None and worker.cancel_requested:
            self._remove_queued_export(job.id)
            return
        self._launch_snapshot_export_worker(job)

    def _snapshot_retry_failed(self, message: str) -> None:
        self.export_panel.set_bridge_readiness_error(
            f"Retry could not load the immutable job: {message}")
        self.statusBar().showMessage(
            f"Export retry failed: {message}", 10000)

    def _remove_queued_export(self, job_id: str) -> None:
        if self.session is None:
            return
        job = self.export_panel.jobs.get(job_id)
        if job is None or job.status is not JobStatus.WAITING:
            return
        active_cancel = None
        if self.export_worker is not None and any(
                candidate.id == job_id for candidate in self.export_worker.jobs):
            active_cancel = self.export_worker.cancel_queued
        worker = ExportQueuedCancelThread(
            Path(self.session.db_path),
            job_id,
            active_cancel=active_cancel,
            parent=self,
        )
        self._export_cancel_workers.add(worker)
        worker.cancel_finished.connect(self._queued_export_cancelled)
        worker.cancel_failed.connect(self._queued_export_cancel_failed)
        worker.finished.connect(
            lambda w=worker: self._release_export_cancel_worker(w))
        worker.start()

    def _queued_export_cancelled(self, job_id: str, cancelled: bool) -> None:
        if cancelled:
            self.export_panel.on_job_cancelled(job_id)
            self._job_cancelled(job_id)
        elif self.session is not None:
            self._restore_export_queue_async()

    def _queued_export_cancel_failed(self, job_id: str, message: str) -> None:
        self.statusBar().showMessage(
            f"Could not cancel queued export {job_id}: {message}", 8000)
        if self.session is not None:
            self._restore_export_queue_async()

    def _cancel_current(self) -> None:
        if self._export_staging_worker is not None:
            self._export_staging_worker.cancel()
        if self.export_worker is not None:
            self.export_worker.cancel_current()

    def _cancel_all(self) -> None:
        if self._export_staging_worker is not None:
            self._export_staging_worker.cancel()
        if self._export_retry_worker is not None:
            self._export_retry_worker.cancel()
        worker = self.export_worker
        if worker is not None:
            request_export_worker_cancel_all(worker)
        waiting_ids = [
            job.id for job in self.export_panel.jobs.values()
            if job.status is JobStatus.WAITING
        ]
        for job_id in waiting_ids:
            self._remove_queued_export(job_id)

    def _cancel_running_export_for_close(self) -> bool:
        worker = self.export_worker
        if worker is None or not worker.isRunning():
            return True
        answer = QMessageBox.question(
            self,
            "Export running",
            "An export is still running. Cancel it and close the project?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        request_export_worker_cancel_all(worker)
        for job in tuple(worker.jobs):
            if job.status is JobStatus.WAITING:
                self._remove_queued_export(job.id)
        for cancel_worker in tuple(self._export_cancel_workers):
            cancel_worker.wait(5000)
        if not worker.wait(5000):
            self.statusBar().showMessage(
                "Export is still cancelling. Try closing again in a moment.",
                6000,
            )
            return False
        if self.export_worker is worker:
            self.export_worker = None
        worker.deleteLater()
        return True

    def _cancel_export_bridge_threads(self, *, wait: bool) -> None:
        self._export_bridge_generation += 1
        self._export_preview_request_id += 1
        workers = tuple(filter(None, (
            self._export_preview_worker,
            self._export_staging_worker,
            self._export_restore_worker,
            self._export_retry_worker,
        ))) + tuple(self._export_cancel_workers)
        for worker in workers:
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
        if wait:
            for worker in workers:
                worker.wait(5000)

    def _release_export_bridge_worker(self, attribute: str, worker) -> None:
        owns_worker = getattr(self, attribute, None) is worker
        if owns_worker:
            setattr(self, attribute, None)
        if attribute == "_export_staging_worker" \
                and owns_worker \
                and not (self.export_worker is not None
                         and self.export_worker.isRunning()):
            self.export_panel.set_running(False)
        worker.deleteLater()

    def _release_export_cancel_worker(
            self, worker: ExportQueuedCancelThread) -> None:
        self._export_cancel_workers.discard(worker)
        worker.deleteLater()

    def _sync_review_export_button(self, clip_ids: list[str]) -> None:
        """Keep the Review export action explicit without widening the rail."""
        button = getattr(self, "review_export_button", None)
        if button is None:
            return
        selected = [
            self.session.get_clip(clip_id)
            for clip_id in clip_ids
        ] if self.session else []
        valid = [clip for clip in selected
                 if clip is not None and clip.enabled]
        count = len(valid)
        button.setText("Export" if count < 2 else f"Export ({count})")
        button.setEnabled(bool(count))
        noun = "clip" if count == 1 else "clips"
        if count:
            tooltip = f"Configure and export {count} selected {noun}"
        elif clip_ids and self.session:
            tooltip = "Selected clips are excluded from export. Enable one first."
        else:
            tooltip = "Select one or more enabled clips to export"
        button.setToolTip(tooltip)

    def _focus_selected_export(self) -> None:
        """Open the existing Export stage scoped to the ledger selection."""
        if not self.session:
            return
        clip_ids = self.clip_list.selected_clip_ids()
        clips = [
            clip for clip_id in clip_ids
            if (clip := self.session.get_clip(clip_id)) is not None
            and clip.enabled
        ]
        if not clips:
            self.statusBar().showMessage(
                "Select at least one enabled clip to export", 4000)
            return
        self._review_export_scope_ids = tuple(clip.id for clip in clips)
        self._set_workspace_stage("export")
        self.export_panel.set_clip_context(clips)
        self._refresh_export_package_context()
        self.export_panel.focus_primary_control()

    def _start_export(
            self, mode: str, preset_name: str, accurate: bool,
            clips=None, quick: bool = False) -> None:
        """Honor the Review selection when Export was opened from its ledger."""
        if self._export_staging_worker is not None or self._export_retry_worker is not None:
            self.statusBar().showMessage("Wait for the current export preparation.", 5000)
            return
        if not self._export_restore_complete:
            self.export_panel.set_bridge_readiness_error(
                "Restoring the durable export queue before starting new work.")
            self.statusBar().showMessage(
                "Export queue restore is still in progress", 5000)
            return
        if (
            clips is None
            and self.session is not None
            and self._review_export_scope_ids is not None
        ):
            clips = [
                clip for clip_id in self._review_export_scope_ids
                if (clip := self.session.get_clip(clip_id)) is not None
            ]
        super()._start_export(
            mode, preset_name, accurate, clips=clips, quick=quick)

    def _set_workspace_stage(self, stage: str) -> None:
        """Switch the fixed lower surface without duplicating any panel."""
        if stage not in {"review", "export"}:
            return
        ribbon = getattr(self, "workflow_ribbon", None)
        self._workspace_stage = stage
        if ribbon is not None:
            ribbon.set_active(stage)
        player_dock = getattr(self, "_workspace_docks", {}).get("player")
        if player_dock is not None and stage == "export":
            player_dock.setMinimumHeight(0)
        self._apply_workspace_visibility()
        if stage == "export" and not self._export_stage_sized:
            self._export_stage_sized = True
            QTimer.singleShot(0, self, self._size_export_stage)
        elif stage == "review":
            self._workspace_resize_timer.start(0)
        self._schedule_workspace_save()

    def _size_export_stage(self) -> None:
        """Give the delivery queue useful room on its first reveal."""
        dock = getattr(self, "_export_dock", None)
        if dock is None or not dock.isVisible() or dock.isFloating():
            return
        target = max(330, round(self.height() * 0.46))
        # QMainWindow can ignore resizeDocks when a newly revealed bottom dock
        # has not established a useful size hint yet. Hold the requested size
        # through the first layout pass, then release it so the splitter
        # remains fully user-resizable.
        dock.setMinimumHeight(target)
        self.resizeDocks([dock], [target], Qt.Orientation.Vertical)
        QTimer.singleShot(
            120, self, self._release_export_stage_minimum)

    def _release_export_stage_minimum(self) -> None:
        dock = getattr(self, "_export_dock", None)
        if dock is not None:
            dock.setMinimumHeight(250)

    def _timeline_follow_playhead_changed(self, enabled: bool) -> None:
        if self._workspace_state_guard:
            return
        self.settings.timeline_follow_playhead = bool(enabled)
        self._schedule_workspace_save()

    def _build_window_menu(self) -> None:
        """Expose the one controlled detachable surface."""
        self.window_menu = QMenu("&Window", self)
        before = next(
            (
                action for action in self.menuBar().actions()
                if action.text().replace("&", "") == "How TapeSift Works"
            ),
            None,
        )
        if before is None:
            self.menuBar().addMenu(self.window_menu)
        else:
            self.menuBar().insertMenu(before, self.window_menu)

        self._dock_toggle_actions: dict[str, QAction] = {}
        self.float_player_action = QAction("Pop Out Video Player", self)
        self.float_player_action.triggered.connect(
            self._toggle_player_floating)
        self.window_menu.addAction(self.float_player_action)
        self.fullscreen_player_action = QAction(
            "Full Screen Video Player", self)
        self.fullscreen_player_action.setShortcut(QKeySequence("F11"))
        self.fullscreen_player_action.setShortcutContext(
            Qt.ShortcutContext.ApplicationShortcut)
        self.fullscreen_player_action.triggered.connect(
            self._toggle_player_fullscreen)
        self.window_menu.addAction(self.fullscreen_player_action)
        self.window_menu.aboutToShow.connect(
            self._sync_workspace_window_actions)
        self.window_menu.addSeparator()
        self.reset_workspace_action = QAction("Reset Workspace", self)
        self.reset_workspace_action.triggered.connect(self._reset_workspace)
        self.window_menu.addAction(self.reset_workspace_action)
        self._sync_workspace_window_actions()

    def _show_player_dock(self, _checked: bool = False) -> None:
        """Restore the one authoritative player without reconstructing it."""
        dock = self._workspace_docks.get("player")
        if dock is None:
            return
        self._dock_visibility["player"] = True
        dock.show()
        dock.raise_()
        if dock.isFloating():
            dock.activateWindow()
        self._sync_workspace_central_surface()
        self._schedule_workspace_save()

    def _toggle_player_floating(self, _checked: bool = False) -> None:
        """Pop the complete player/timeline out, or dock that same widget."""
        dock = self._workspace_docks.get("player")
        if dock is None:
            return
        if not dock.isVisible():
            self._show_player_dock()

        floating = dock.isFloating()
        self._workspace_state_guard = True
        try:
            if floating:
                self._exit_player_fullscreen(restore_previous=False)
                dock.showNormal()
                self._workspace_maximized_docks.discard("player")
                dock.setFloating(False)
                if self.dockWidgetArea(
                        dock) == Qt.DockWidgetArea.NoDockWidgetArea:
                    self.addDockWidget(
                        Qt.DockWidgetArea.TopDockWidgetArea, dock)
                dock.show()
            else:
                dock.setFloating(True)
                dock.show()
                dock.resize(max(960, dock.width()), max(600, dock.height()))
                dock.raise_()
                dock.activateWindow()
                self.player.setFocus(Qt.FocusReason.OtherFocusReason)
        finally:
            self._workspace_state_guard = False
        self._dock_visibility["player"] = True
        if floating:
            self.statusBar().showMessage(
                "Video Player docked back into the workspace", 3000)
        else:
            self.statusBar().showMessage(
                "Video Player popped out - move it to your second display",
                4500,
            )
        self._sync_player_dock_controls(not floating)
        self._sync_workspace_central_surface()
        self._schedule_workspace_save()

    def _workspace_dock_top_level_changed(
            self, key: str, floating: bool) -> None:
        """Finish Qt's native float/redock transition without new panels."""
        if key != "player":
            # Defensive recovery for legacy saved state or external Qt calls.
            # Users cannot float these panels, and any stale float is returned
            # home on the next event-loop turn.
            if floating:
                QTimer.singleShot(
                    0,
                    self,
                    lambda dock_key=key:
                    self._redock_fixed_workspace_panel(dock_key),
                )
            return
        if floating:
            # Qt rebuilds a floating dock's native flags after this signal.
            # Apply the caption controls on the next event-loop turn.
            QTimer.singleShot(
                0,
                self,
                lambda dock_key=key:
                self._apply_floating_dock_window_hints(dock_key),
            )
        else:
            self._workspace_maximized_docks.discard(key)
            if key == "player":
                self._player_fullscreen = False
                self._player_pre_fullscreen_maximized = False
        if key == "player":
            self._player_floating_changed(bool(floating))
        self._sync_workspace_window_actions()
        self._schedule_workspace_save()

    def _player_floating_changed(self, floating: bool) -> None:
        """Synchronize duplicate controls around Qt's native dock action."""
        self._sync_player_dock_controls(bool(floating))
        self._sync_workspace_central_surface()

    def _sync_player_dock_controls(self, floating: bool) -> None:
        action_text = (
            "Dock Video Player Back" if floating else "Pop Out Video Player")
        button_text = "Dock Back" if floating else "Pop Out"
        tooltip = (
            "Return the video player and editable timeline to the main window."
            if floating
            else "Move the video player and editable timeline together into "
                 "a separate window."
        )
        action = getattr(self, "float_player_action", None)
        if action is not None:
            action.setText(action_text)
            action.setToolTip(tooltip)
        button = getattr(self, "player_detach_button", None)
        if button is not None:
            button.setText(button_text)
            button.setToolTip(tooltip)
            button.setAccessibleName(action_text)
        fullscreen = bool(
            self._player_fullscreen
            or getattr(
                getattr(self, "_workspace_docks", {}).get("player"),
                "isFullScreen",
                lambda: False,
            )()
        )
        fullscreen_text = (
            "Exit Full Screen" if fullscreen
            else "Full Screen Video Player")
        fullscreen_tooltip = (
            "Exit full screen and return to the floating player window (F11)."
            if fullscreen
            else "Fill the current display with the floating player (F11)."
        )
        fullscreen_action = getattr(
            self, "fullscreen_player_action", None)
        if fullscreen_action is not None:
            fullscreen_action.setText(fullscreen_text)
            fullscreen_action.setToolTip(fullscreen_tooltip)
        fullscreen_button = getattr(
            self, "player_fullscreen_button", None)
        if fullscreen_button is not None:
            fullscreen_button.setText(
                "Exit Full Screen" if fullscreen else "Full Screen")
            fullscreen_button.setToolTip(fullscreen_tooltip)
            fullscreen_button.setAccessibleName(fullscreen_text)
            fullscreen_button.setVisible(bool(floating))
        self._sync_workspace_window_actions()

    def _sync_workspace_window_actions(self) -> None:
        """Keep the controlled player-window commands truthful."""
        active = (
            getattr(self, "stack", None) is not None
            and self.stack.currentWidget() is self.workspace
        )
        docks = getattr(self, "_workspace_docks", {})
        player = docks.get("player")
        player_can_fullscreen = bool(
            active
            and player is not None
            and player.isVisible()
            and player.isFloating()
        )
        fullscreen_action = getattr(
            self, "fullscreen_player_action", None)
        if fullscreen_action is not None:
            fullscreen_action.setEnabled(player_can_fullscreen)
        fullscreen_button = getattr(
            self, "player_fullscreen_button", None)
        if fullscreen_button is not None:
            fullscreen_button.setEnabled(player_can_fullscreen)

    def _player_fullscreen_command_allowed(self) -> bool:
        dock = getattr(self, "_workspace_docks", {}).get("player")
        if dock is None \
                or self.stack.currentWidget() is not self.workspace \
                or not dock.isVisible() \
                or not dock.isFloating():
            return False
        app = QApplication.instance()
        if app is None:
            return True
        if app.activeModalWidget() is not None:
            return False
        active = app.activeWindow()
        return active is None or active in {self, dock}

    def _toggle_player_fullscreen(self, _checked: bool = False) -> None:
        """Toggle true fullscreen for the already-floating video workspace."""
        if not self._player_fullscreen_command_allowed():
            return
        dock = self._workspace_docks["player"]
        if self._player_fullscreen or dock.isFullScreen():
            self._exit_player_fullscreen()
            return

        self._apply_floating_dock_window_hints("player")
        self._player_pre_fullscreen_maximized = bool(
            dock.windowState() & Qt.WindowState.WindowMaximized)
        if self._player_pre_fullscreen_maximized:
            self._workspace_maximized_docks.add("player")
        self._player_fullscreen = True
        old_guard = self._workspace_state_guard
        self._workspace_state_guard = True
        try:
            dock.showFullScreen()
            dock.raise_()
            dock.activateWindow()
            self.player.setFocus(Qt.FocusReason.OtherFocusReason)
        finally:
            self._workspace_state_guard = old_guard
        self._sync_player_dock_controls(True)
        self.statusBar().showMessage(
            "Video Player full screen - press F11 or Esc to exit", 4000)

    def _exit_player_fullscreen(
            self, *, restore_previous: bool = True) -> None:
        dock = getattr(self, "_workspace_docks", {}).get("player")
        if dock is None:
            return
        was_fullscreen = self._player_fullscreen or dock.isFullScreen()
        if not was_fullscreen:
            return
        restore_maximized = (
            restore_previous and self._player_pre_fullscreen_maximized)
        self._player_fullscreen = False
        self._player_pre_fullscreen_maximized = False
        old_guard = self._workspace_state_guard
        self._workspace_state_guard = True
        try:
            if restore_maximized:
                dock.showMaximized()
            else:
                dock.showNormal()
        finally:
            self._workspace_state_guard = old_guard
        if not restore_maximized:
            self._workspace_maximized_docks.discard("player")
        self._sync_player_dock_controls(dock.isFloating())

    def _shortcut_escape(self) -> None:
        """Exit player fullscreen first; otherwise retain the editing Escape."""
        dock = getattr(self, "_workspace_docks", {}).get("player")
        if self._player_fullscreen \
                or dock is not None and dock.isFullScreen():
            self._exit_player_fullscreen()
            return
        super()._shortcut_escape()

    def _apply_floating_dock_window_hints(self, key: str) -> None:
        """Add genuine native controls to the explicit player popout.

        A floating QDockWidget is intentionally still a Qt Tool window. That
        preserves its ownership, z-order, and redocking behavior; only the
        standard caption-button hints are added. Qt hides a window while
        changing these flags, so its geometry, state, activation, and focused
        editor are restored around that native-handle transition.
        """
        if key != "player":
            return
        dock = getattr(self, "_workspace_docks", {}).get(key)
        if dock is None or not dock.isFloating():
            return
        hints = (
            Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        current_flags = dock.windowFlags()
        desired_flags = current_flags | hints
        if desired_flags == current_flags:
            return

        was_visible = dock.isVisible()
        was_active = dock.isActiveWindow()
        geometry = dock.geometry()
        window_state = dock.windowState()
        focused = QApplication.focusWidget()
        focused_in_dock = (
            focused
            if focused is not None
            and (focused is dock or dock.isAncestorOf(focused))
            else None
        )
        old_guard = self._workspace_state_guard
        self._workspace_state_guard = True
        try:
            dock.setWindowFlags(desired_flags)
            dock.setGeometry(geometry)
            if was_visible:
                dock.show()
                dock.setWindowState(window_state)
                if was_active:
                    dock.raise_()
                    dock.activateWindow()
                    if focused_in_dock is not None:
                        focused_in_dock.setFocus(
                            Qt.FocusReason.OtherFocusReason)
        finally:
            self._workspace_state_guard = old_guard
        self._sync_workspace_window_actions()

    def _restore_minimized_workspace_docks(
            self, _checked: bool = False) -> None:
        """Restore the minimized player without losing maximize."""
        restored: list[QDockWidget] = []
        old_guard = self._workspace_state_guard
        self._workspace_state_guard = True
        try:
            dock = self._workspace_docks.get("player")
            if dock is not None and dock.isFloating() and dock.isMinimized():
                state = dock.windowState()
                if state & Qt.WindowState.WindowFullScreen:
                    dock.showFullScreen()
                elif state & Qt.WindowState.WindowMaximized:
                    self._workspace_maximized_docks.add("player")
                    dock.showMaximized()
                else:
                    dock.showNormal()
                restored.append(dock)
        finally:
            self._workspace_state_guard = old_guard
        if restored:
            restored[-1].raise_()
            restored[-1].activateWindow()
        self._sync_workspace_window_actions()

    def _normalize_transient_workspace_window_states(self) -> None:
        """Clear transient player states without changing its dock choice."""
        old_guard = self._workspace_state_guard
        self._workspace_state_guard = True
        try:
            dock = self._workspace_docks.get("player")
            if dock is not None and dock.isFloating():
                state = dock.windowState()
                if state & Qt.WindowState.WindowMaximized:
                    self._workspace_maximized_docks.add("player")
                if (
                    state & Qt.WindowState.WindowMinimized
                    or state & Qt.WindowState.WindowFullScreen
                ):
                    normalized = (
                        state
                        & ~Qt.WindowState.WindowMinimized
                        & ~Qt.WindowState.WindowFullScreen
                        & ~Qt.WindowState.WindowActive
                    )
                    dock.setWindowState(normalized)
        finally:
            self._workspace_state_guard = old_guard
        self._sync_workspace_window_actions()

    def _workspace_dock_window_state_changed(self, key: str) -> None:
        """Track maximize, while treating minimize/fullscreen as transient."""
        if self._workspace_state_guard or key != "player":
            return
        dock = getattr(self, "_workspace_docks", {}).get(key)
        if dock is None:
            return
        if not dock.isFloating():
            self._workspace_maximized_docks.discard(key)
            self._sync_workspace_window_actions()
            return

        state = dock.windowState()
        maximized = bool(state & Qt.WindowState.WindowMaximized)
        minimized = bool(state & Qt.WindowState.WindowMinimized)
        fullscreen = bool(state & Qt.WindowState.WindowFullScreen)
        if maximized:
            self._workspace_maximized_docks.add(key)
        elif not fullscreen:
            self._workspace_maximized_docks.discard(key)

        if key == "player" and fullscreen:
            self._player_fullscreen = True
        self._sync_workspace_window_actions()
        if minimized or fullscreen:
            return
        self._schedule_workspace_save()

    def _restore_floating_window_states(self) -> None:
        """Normalize the restored player, never utility panels."""
        if not self._restore_floating_states_pending \
                or self.stack.currentWidget() is not self.workspace:
            return
        self._restore_floating_states_pending = False
        old_guard = self._workspace_state_guard
        self._workspace_state_guard = True
        try:
            self._redock_fixed_workspace_panels()
            dock = self._workspace_docks.get("player")
            if dock is None or not dock.isFloating():
                self._workspace_maximized_docks.discard("player")
            elif dock.isVisible():
                self._apply_floating_dock_window_hints("player")
                if "player" in self._workspace_maximized_docks:
                    dock.showMaximized()
                else:
                    dock.showNormal()
            self._player_fullscreen = False
            self._player_pre_fullscreen_maximized = False
        finally:
            self._workspace_state_guard = old_guard
        player = self._workspace_docks.get("player")
        if player is not None:
            self._sync_player_dock_controls(player.isFloating())
        self._sync_workspace_window_actions()

    def _apply_saved_floating_window_state(self, key: str) -> None:
        """Finish opening the previously hidden floating player."""
        if key != "player":
            return
        dock = getattr(self, "_workspace_docks", {}).get(key)
        if dock is None or not dock.isVisible() or not dock.isFloating():
            return
        self._apply_floating_dock_window_hints(key)
        old_guard = self._workspace_state_guard
        self._workspace_state_guard = True
        try:
            if key in self._workspace_maximized_docks:
                dock.showMaximized()
            elif dock.isFullScreen() or dock.isMinimized():
                dock.showNormal()
        finally:
            self._workspace_state_guard = old_guard
        self._sync_workspace_window_actions()

    def _sync_workspace_central_surface(self) -> None:
        """Let the docks consume the main window unless the player is hidden."""
        placeholder = getattr(self, "_player_placeholder", None)
        dock = getattr(self, "_workspace_docks", {}).get("player")
        if placeholder is None or dock is None:
            return
        active = self.stack.currentWidget() is self.workspace
        player_visible = dock.isVisible()
        player_floating = player_visible and dock.isFloating()
        placeholder.setVisible(active and not player_visible)
        # QMainWindow still reserves space for its central widget even when
        # every useful workspace surface is a dock. A docked player needs the
        # center's height collapsed so the top/bottom surfaces meet. A floating
        # player needs its width collapsed instead: that lets Clips and Play
        # Details meet across the freed center.
        self.stack.setMaximumHeight(
            0 if active and player_visible and not player_floating
            else 16777215)
        self.stack.setMaximumWidth(
            0 if active and player_floating else 16777215)
        self.stack.updateGeometry()

    def _workspace_dock_visibility_changed(
            self, key: str, visible: bool) -> None:
        """Record explicit panel changes, never transient page/stage hides."""
        if self._workspace_state_guard:
            return
        if self.stack.currentWidget() is not self.workspace:
            return
        dock = getattr(self, "_workspace_docks", {}).get(key)
        was_logically_visible = self._dock_visibility.get(key, True)
        if key == "player" \
                and not visible \
                and dock is not None \
                and not dock.isMinimized() \
                and not self._app_closing:
            # QDockWidget's native close path can bypass eventFilter on
            # Windows. Treat the resulting hide as Dock Back on the next turn.
            QTimer.singleShot(0, self, self._redock_closed_player)
            return
        # QDockWidget explicitly reports visibility false while a native
        # floating window is minimized (and when a tab sibling covers it),
        # even though the panel is still logically open. Do not persist that
        # transient window state as "hidden".
        if not visible and dock is not None and dock.isVisible():
            self._sync_workspace_window_actions()
            return
        self._dock_visibility[key] = bool(visible)
        if key == "player":
            if not visible:
                if self._player_fullscreen \
                        or dock is not None and dock.isFullScreen():
                    restore_maximized = (
                        self._player_pre_fullscreen_maximized)
                    if restore_maximized:
                        self._workspace_maximized_docks.add("player")
                    self._player_fullscreen = False
                    self._player_pre_fullscreen_maximized = False
                    if dock is not None:
                        old_guard = self._workspace_state_guard
                        self._workspace_state_guard = True
                        try:
                            dock.setWindowState(
                                Qt.WindowState.WindowMaximized
                                if restore_maximized
                                else Qt.WindowState.WindowNoState
                            )
                        finally:
                            self._workspace_state_guard = old_guard
                # Native float/redock briefly emits visibility false on
                # Windows. Verify it is still hidden on the next event-loop
                # turn before stopping, so detaching during playback is
                # seamless while an actual close cannot leave hidden audio.
                QTimer.singleShot(
                    0, self, self._pause_player_if_still_hidden)
            self._sync_workspace_central_surface()
        if visible:
            self._workspace_recovery_timer.start(0)
            if not was_logically_visible \
                    and dock is not None \
                    and dock.isFloating():
                QTimer.singleShot(
                    0,
                    self,
                    lambda dock_key=key:
                    self._apply_saved_floating_window_state(dock_key),
                )
        self._sync_workspace_window_actions()
        self._schedule_workspace_save()

    def _redock_closed_player(self) -> None:
        """Return a user-closed player popout to the stable workspace."""
        if self._app_closing:
            return
        dock = self._workspace_docks.get("player")
        if dock is None or dock.isVisible():
            return
        self._workspace_state_guard = True
        try:
            dock.showNormal()
            dock.setFloating(False)
            if self.dockWidgetArea(
                    dock) == Qt.DockWidgetArea.NoDockWidgetArea:
                self.addDockWidget(
                    Qt.DockWidgetArea.TopDockWidgetArea, dock)
            dock.show()
        finally:
            self._workspace_state_guard = False
        self._dock_visibility["player"] = True
        self._workspace_maximized_docks.discard("player")
        self._player_fullscreen = False
        self._player_pre_fullscreen_maximized = False
        self._sync_player_dock_controls(False)
        self._sync_workspace_central_surface()
        self._schedule_workspace_save()

    def _pause_player_if_still_hidden(self) -> None:
        dock = getattr(self, "_workspace_docks", {}).get("player")
        if dock is None \
                or dock.isVisible() \
                or self.stack.currentWidget() is not self.workspace:
            return
        # The object remains alive and retains source, position, marks, and
        # zoom; only transport/audio stops while no player surface is visible.
        self.player.shuttle_stop()

    def _workspace_page_changed(self, _index: int) -> None:
        """Keep the floating player tied to the project page lifecycle."""
        page = self.stack.currentWidget()
        active = page is self.workspace
        # Option 4 uses the full 870px review lane below its 50px masthead.
        # Status text remains available on Home/Library and through the same
        # statusBar API, but Review does not reserve a second bottom bar.
        self.statusBar().setVisible(not active)
        shell = getattr(self, "_centered_menu_shell", None)
        if shell is not None:
            mode = (
                "review" if active
                else "home" if page is self.start_screen
                else "library" if page is self.library_screen
                else ""
            )
            shell.set_mode(mode)
        if not active:
            self._exit_player_fullscreen()
            self._normalize_transient_workspace_window_states()
        self.stack.setSizePolicy(
            QSizePolicy.Policy.Ignored
            if active else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Ignored
            if active else QSizePolicy.Policy.Preferred,
        )
        self.stack.updateGeometry()
        self._apply_workspace_visibility()
        for action in getattr(self, "_dock_toggle_actions", {}).values():
            action.setEnabled(active)
        if getattr(self, "float_player_action", None) is not None:
            self.float_player_action.setEnabled(active)
        self._sync_workspace_window_actions()
        self._sync_workspace_central_surface()
        if active:
            self._workspace_recovery_timer.start(0)
            if self._restore_floating_states_pending:
                self._workspace_window_state_timer.start(0)

    def _workspace_screen_removed(self, _screen) -> None:
        self._workspace_recovery_timer.start(0)

    def _apply_workspace_visibility(self) -> None:
        docks = getattr(self, "_workspace_docks", None)
        if not docks:
            return
        active = self.stack.currentWidget() is self.workspace
        stage = getattr(self, "_workspace_stage", "review")
        self._workspace_state_guard = True
        try:
            for key, dock in docks.items():
                # The three utility surfaces are structural parts of Review,
                # not independently closable windows.
                visible = active
                if key == "player":
                    visible = active and self._dock_visibility.get(key, True)
                dock.setVisible(visible)
            self._control_center_dock.setVisible(active)
            self._export_dock.setVisible(active and stage == "export")
        finally:
            self._workspace_state_guard = False
        self._sync_workspace_central_surface()
        player_dock = docks.get("player")
        if player_dock is not None:
            self._sync_player_dock_controls(player_dock.isFloating())
        if active and self._restore_floating_states_pending:
            self._workspace_window_state_timer.start(0)

    def _schedule_workspace_save(self) -> None:
        if self._workspace_state_guard:
            return
        self._workspace_save_timer.start()

    def _save_workspace_now(self) -> None:
        """Persist geometry, dock topology, and independent view choices."""
        if not getattr(self, "_workspace_docks", None):
            return
        maximized: set[str] = set()
        dock = self._workspace_docks.get("player")
        if dock is not None and dock.isFloating():
            state = dock.windowState()
            if state & Qt.WindowState.WindowMaximized:
                maximized.add("player")
            elif self._player_fullscreen \
                    and self._player_pre_fullscreen_maximized:
                maximized.add("player")
        self._workspace_maximized_docks = maximized
        self.settings.workspace_geometry_v1 = encode_qbytearray(
            self.saveGeometry())
        self.settings.workspace_state_v1 = encode_qbytearray(
            self.saveState(WORKSPACE_STATE_VERSION))
        self.settings.workspace_player_visible = True
        self.settings.workspace_clips_visible = True
        self.settings.workspace_play_details_visible = True
        self.settings.workspace_maximized_docks_v1 = sorted(maximized)
        self.settings.timeline_follow_playhead = \
            self.player.timeline_follow_playhead.isChecked()
        self.settings.save()

    def _restore_workspace(self) -> None:
        if self._workspace_restored:
            return
        self._workspace_restored = True
        if not getattr(self.settings, "workspace_restore_layout", False):
            # Open clean. Restoring the saved blob also replayed its
            # maximized flag, and a maximized shell paints square corners by
            # design (see paintEvent), so a stale flag made every launch look
            # harsh until the first minimize/restore cleared the state.
            # Opening clean also guarantees both side panels start even
            # instead of however the last session happened to leave them.
            self._workspace_state_guard = True
            try:
                self._apply_default_dock_layout()
                self._dock_visibility = {
                    "player": True,
                    "clips": True,
                    "play_details": True,
                }
            finally:
                self._workspace_state_guard = False
            self._apply_workspace_visibility()
            # Re-adding native docks establishes topology, not their final
            # proportions. Run the same deferred sizing pass used by Reset so
            # a clean launch fills the window instead of keeping a 620px band.
            self._workspace_resize_timer.start(0)
            return
        geometry_value = self.settings.workspace_geometry_v1
        state_value = self.settings.workspace_state_v1
        geometry = decode_qbytearray(geometry_value)
        state = decode_qbytearray(state_value)
        invalid = (
            bool(geometry_value) and geometry is None
            or bool(state_value) and state is None
        )

        self._workspace_state_guard = True
        try:
            if geometry is not None and not self.restoreGeometry(geometry):
                invalid = True
            if state is not None and not self.restoreState(
                    state, WORKSPACE_STATE_VERSION):
                invalid = True
            if invalid:
                self._apply_default_dock_layout()
            else:
                self._redock_fixed_workspace_panels()

            self._dock_visibility = {
                "player": True,
                "clips": True,
                "play_details": True,
            }
            saved_maximized = (
                self.settings.workspace_maximized_docks_v1
                if isinstance(
                    self.settings.workspace_maximized_docks_v1, list)
                else []
            )
            self._workspace_maximized_docks = {
                key for key in saved_maximized
                if key == "player"
            }
            self._restore_floating_states_pending = True
            self.player.timeline_follow_playhead.setChecked(
                bool(self.settings.timeline_follow_playhead))
        finally:
            self._workspace_state_guard = False
        self._apply_workspace_visibility()
        self._workspace_recovery_timer.start(0)
        if self.stack.currentWidget() is self.workspace:
            self._workspace_window_state_timer.start(0)
        if state is None or invalid:
            self._save_after_workspace_resize = invalid
            self._workspace_resize_timer.start(0)
        if invalid:
            self.statusBar().showMessage(
                "Workspace layout was reset because its saved state "
                "could not be restored.",
                5000,
            )

    def _apply_default_dock_layout(self) -> None:
        self.setCorner(
            Qt.Corner.TopLeftCorner,
            Qt.DockWidgetArea.LeftDockWidgetArea,
        )
        self.setCorner(
            Qt.Corner.TopRightCorner,
            Qt.DockWidgetArea.RightDockWidgetArea,
        )
        self.setCorner(
            Qt.Corner.BottomLeftCorner,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.setCorner(
            Qt.Corner.BottomRightCorner,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        for dock, area in self._default_dock_areas.items():
            dock.showNormal()
            dock.hide()
            dock.setFloating(False)
            self.removeDockWidget(dock)
            self.addDockWidget(area, dock)

    def _redock_fixed_workspace_panel(self, key: str) -> None:
        """Return one non-player panel to its only supported location."""
        if key == "player":
            return
        dock = self._workspace_docks.get(key)
        if dock is None:
            return
        area = self._default_dock_areas[dock]
        if not dock.isFloating() and self.dockWidgetArea(dock) == area:
            return
        old_guard = self._workspace_state_guard
        self._workspace_state_guard = True
        try:
            dock.showNormal()
            dock.setFloating(False)
            if self.dockWidgetArea(dock) != area:
                self.removeDockWidget(dock)
                self.addDockWidget(area, dock)
            if self.stack.currentWidget() is self.workspace:
                dock.show()
        finally:
            self._workspace_state_guard = old_guard

    def _redock_fixed_workspace_panels(self) -> None:
        """Normalize all utility surfaces after native state restoration."""
        for key in ("clips", "play_details"):
            self._redock_fixed_workspace_panel(key)

    def _resize_default_docks(self) -> None:
        docks = getattr(self, "_workspace_docks", {})
        player = docks.get("player")
        clips = docks.get("clips")
        details = docks.get("play_details")
        control_center = getattr(self, "_control_center_dock", None)
        if player is not None and not player.isFloating() \
                and control_center is not None:
            # Dock V2 leaves an empty placeholder, so the player takes the
            # whole column. The legacy rollback keeps its original fixed
            # bottom-dock height.
            if control_center.widget() is None:
                # With no visible bottom dock there is no second vertical
                # surface to reserve room for. Ask Qt for the full available
                # dock lane; the old ``height - 120`` request stopped all
                # three columns about 43px above the status bar at 1708x920.
                masthead_height = self._centered_menu_shell.height()
                status_height = (
                    self.statusBar().height()
                    if self.statusBar().isVisible() else 0)
                player_height = max(
                    620, self.height() - masthead_height - status_height)
                player.setMinimumHeight(player_height)
                # resizeDocks ignores the requested ratio when its comparison
                # dock is a hidden zero-height placeholder. Resize the one
                # visible surface directly so it consumes the central column.
                self.resizeDocks(
                    [player], [player_height], Qt.Orientation.Vertical)
            else:
                player_height = 620
                control_height = control_center.minimumHeight()
                self.resizeDocks(
                    [player, control_center],
                    [player_height, control_height],
                    Qt.Orientation.Vertical)
        if clips is not None and details is not None:
            self.resizeDocks(
                [clips, details],
                [self.REVIEW_LEDGER_WIDTH, self.REVIEW_INSPECTOR_WIDTH],
                Qt.Orientation.Horizontal)
        if self._save_after_workspace_resize:
            self._save_after_workspace_resize = False
            self._save_workspace_now()

    def _reset_workspace(self, _checked: bool = False) -> None:
        self._exit_player_fullscreen(restore_previous=False)
        self._workspace_state_guard = True
        try:
            self._apply_default_dock_layout()
            self._workspace_maximized_docks.clear()
            self._restore_floating_states_pending = False
            self._dock_visibility = {
                "player": True,
                "clips": True,
                "play_details": True,
            }
            self._workspace_stage = "review"
            self.workflow_ribbon.set_active("review")
            self.player.timeline_follow_playhead.setChecked(True)
        finally:
            self._workspace_state_guard = False
        self._apply_workspace_visibility()
        self._save_after_workspace_resize = True
        self._workspace_resize_timer.start(0)
        self.statusBar().showMessage("Workspace layout reset", 3000)

    def _recover_missing_monitor_docks(self) -> None:
        if self._workspace_state_guard:
            return
        player = self._workspace_docks.get("player")
        if player is None:
            return
        user_defaults = {
            player: self._default_dock_areas[player],
        }
        self._workspace_state_guard = True
        try:
            recovered = redock_offscreen_floating_docks(
                self, user_defaults)
        finally:
            self._workspace_state_guard = False
        if recovered:
            recovered_names = set(recovered)
            if player.objectName() in recovered_names:
                self._workspace_maximized_docks.discard("player")
                self._player_fullscreen = False
                self._player_pre_fullscreen_maximized = False
                self._sync_player_dock_controls(False)
            self._save_workspace_now()
            noun = "panel" if len(recovered) == 1 else "panels"
            self.statusBar().showMessage(
                f"Recovered {len(recovered)} {noun} from a disconnected "
                "display.",
                5000,
            )

    def eventFilter(self, watched, event) -> bool:
        docks = getattr(self, "_workspace_docks", {})
        player_dock = docks.get("player")
        if watched is player_dock:
            if event.type() == QEvent.Type.Close \
                    and watched.isFloating() \
                    and not self._app_closing:
                # The floating player's native X means "Dock Back", never
                # "lose the player somewhere in saved workspace state."
                self._toggle_player_floating()
                return True
            if event.type() in {
                    QEvent.Type.Move, QEvent.Type.Resize
            } and watched.isFloating() \
                    and not watched.isMinimized() \
                    and not watched.isMaximized() \
                    and not watched.isFullScreen():
                self._schedule_workspace_save()
            elif event.type() == QEvent.Type.WindowStateChange:
                key = next(
                    (name for name, dock in docks.items()
                     if dock is watched),
                    None,
                )
                if key is not None:
                    QTimer.singleShot(
                        0,
                        self,
                        lambda dock_key=key:
                        self._workspace_dock_window_state_changed(dock_key),
                    )
        return super().eventFilter(watched, event)

    def _transport_auxiliary_windows(self) -> tuple[QWidget, ...]:
        dock = getattr(self, "_workspace_docks", {}).get("player")
        return (
            (dock,)
            if dock is not None and dock.isVisible() and dock.isFloating()
            else ()
        )

    def start_temporal_review(self) -> None:
        """Open the saved review cursor in the authoritative player."""
        panel = getattr(self, "temporal_review_panel", None)
        if panel is None:
            return
        if self.temporal_review_session.current is None:
            self.statusBar().showMessage(
                "Temporal Review has no flagged clips.", 5000)
            return
        panel.activate_current()

    def start_snap_calibration(self) -> None:
        """Open the saved exact-snap cursor in the authoritative player."""
        panel = getattr(self, "snap_calibration_panel", None)
        if panel is None:
            return
        if self.snap_calibration_session.current is None:
            self.statusBar().showMessage(
                "Snap Calibration has no clips.", 5000)
            return
        panel.activate_current()

    def _snap_calibration_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        QMessageBox.critical(self, "Snap Calibration", message)

    def _snap_calibration_project_path(self, item) -> Path:
        expected_name = str(item.project_file_name).casefold()
        candidates = [
            *self.snap_calibration_project_paths,
            Path(str(item.project_path)),
        ]
        for candidate in candidates:
            if (
                candidate.name.casefold() == expected_name
                and candidate.is_file()
            ):
                return candidate.resolve()
        raise FileNotFoundError(
            f"Could not find {item.project_file_name}. "
            "Recreate the Snap Calibration desktop shortcut after moving "
            "the research projects."
        )

    def _ensure_snap_calibration_item(self, item) -> bool:
        calibration = self.snap_calibration_session
        if calibration is None:
            return False
        try:
            project_path = self._snap_calibration_project_path(item)
            needs_open = (
                self.session is None
                or not getattr(self.session, "read_only", False)
                or not self._same_project_path(
                    self.session.db_path, project_path)
            )
            if needs_open:
                session = ProjectSession.open_read_only(project_path)
                if not self._activate_session(session):
                    # Refused, so self.session is still the previous one and
                    # the assert below would fire on the wrong project.
                    return False
            assert self.session is not None
            matched = calibration.bind_project(
                project_path,
                self.session.clips,
                current_project_path_candidates=(item.project_path,),
            )
            if not any(match.item_id == item.item_id for match in matched):
                raise ValueError(
                    "The selected calibration clip does not belong to the "
                    "validated project."
                )
        except Exception as exc:
            self._snap_calibration_error(str(exc))
            return False
        return True

    def _show_snap_calibration_moment(
            self, item, target_ms: int) -> bool:
        if not self.select_clip(
            item.clip_id,
            seek=True,
            seek_ms=target_ms,
            focus_player=True,
            review_autoplay=False,
        ):
            self._snap_calibration_error(
                f"Clip {item.clip_number} could not be selected.")
            return False
        marker_start = max(
            item.start_ms, min(item.end_ms - 1, target_ms))
        marker_end = min(item.end_ms, marker_start + 1)
        self.player.focus_source_range(
            marker_start,
            marker_end,
            context_ms=5_000,
            seek_center=False,
        )
        self.player.shuttle_stop()
        self.player.clear_clip_range()
        self.player.seek_to(target_ms)
        return True

    def _snap_calibration_item_requested(self, item) -> None:
        if not self._ensure_snap_calibration_item(item):
            return
        target_ms = (
            item.angles[0].proposed_onset_ms
            if item.angles else item.start_ms
        )
        if self._show_snap_calibration_moment(item, target_ms):
            self.statusBar().showMessage(
                f"Snap Calibration | Clip {item.clip_number} | "
                "pause, use Left/Right to reach the true snap, then mark "
                "the current frame",
                7000,
            )

    def _snap_calibration_inspect_requested(
            self, item, angle_number: int, play_around: bool) -> None:
        calibration = self.snap_calibration_session
        if (
            calibration is None
            or not self._ensure_snap_calibration_item(item)
        ):
            return
        target_ms = calibration.seek_target_ms(item.item_id, angle_number)
        if not self._show_snap_calibration_moment(item, target_ms):
            return
        if play_around:
            start_ms, end_ms = calibration.preview_range_ms(
                item.item_id, angle_number)
            if end_ms > start_ms:
                self.player.focus_source_range(
                    start_ms,
                    end_ms,
                    context_ms=1_000,
                    seek_center=False,
                )
                self.player.play_clip_range(start_ms, end_ms, False)
        action = "Playing around" if play_around else "Showing"
        self.statusBar().showMessage(
            f"{action} Angle {angle_number} proposed snap", 3000)

    def _snap_calibration_exact_requested(
            self, item, angle_number: int) -> None:
        """Persist the authoritative player's current frame as truth."""
        calibration = self.snap_calibration_session
        if (
            calibration is None
            or not self._ensure_snap_calibration_item(item)
        ):
            return
        if self._selected_clip_id != item.clip_id:
            self._snap_calibration_error(
                "Show this angle's proposed moment before marking its "
                "exact snap.")
            return
        # Freeze playback before sampling the authoritative source position,
        # so a click made during playback cannot drift by another frame.
        self.player.shuttle_stop()
        source_ms = int(self.player.position_ms())
        try:
            calibrated = calibration.mark_actual_snap(
                item.item_id, angle_number, source_ms)
        except (KeyError, ValueError) as exc:
            self._snap_calibration_error(str(exc))
            return
        panel = getattr(self, "snap_calibration_panel", None)
        if panel is not None:
            panel.refresh()
        measured = calibrated.angle(angle_number)
        delta = measured.snap_delta_ms
        delta_text = (
            f"{delta:+d} ms from proposal"
            if delta is not None else "saved"
        )
        self.statusBar().showMessage(
            f"Angle {angle_number} exact snap saved at "
            f"{source_ms / 1000:.3f}s ({delta_text})",
            5000,
        )

    def _temporal_review_error(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        QMessageBox.critical(self, "Temporal Review", message)

    def _temporal_review_project_path(self, item) -> Path:
        expected_name = str(item.project_file_name).casefold()
        candidates = [
            *self.temporal_review_project_paths,
            Path(str(item.project_path)),
        ]
        for candidate in candidates:
            if (
                candidate.name.casefold() == expected_name
                and candidate.is_file()
            ):
                return candidate.resolve()
        raise FileNotFoundError(
            f"Could not find {item.project_file_name}. "
            "Recreate the Temporal Review desktop shortcut after moving "
            "the research projects."
        )

    @staticmethod
    def _same_project_path(left: Path | str, right: Path | str) -> bool:
        return str(Path(left).resolve()).casefold() == \
            str(Path(right).resolve()).casefold()

    def _ensure_temporal_review_item(self, item) -> bool:
        review = self.temporal_review_session
        if review is None:
            return False
        try:
            project_path = self._temporal_review_project_path(item)
            needs_open = (
                self.session is None
                or not getattr(self.session, "read_only", False)
                or not self._same_project_path(
                    self.session.db_path, project_path)
            )
            if needs_open:
                session = ProjectSession.open_read_only(project_path)
                if not self._activate_session(session):
                    # Refused, so self.session is still the previous one and
                    # the assert below would fire on the wrong project.
                    return False
            assert self.session is not None
            matched = review.bind_project(
                project_path,
                self.session.clips,
                current_project_path_candidates=(item.project_path,),
            )
            if not any(match.item_id == item.item_id for match in matched):
                raise ValueError(
                    "The selected review clip does not belong to the "
                    "validated project."
                )
        except Exception as exc:
            self._temporal_review_error(str(exc))
            return False
        return True

    def _show_temporal_review_moment(self, item, target_ms: int) -> bool:
        if not self.select_clip(
            item.clip_id,
            seek=True,
            seek_ms=target_ms,
            focus_player=True,
            review_autoplay=False,
        ):
            self._temporal_review_error(
                f"Clip {item.clip_number} could not be selected.")
            return False
        marker_start = max(
            item.start_ms, min(item.end_ms - 1, target_ms))
        marker_end = min(item.end_ms, marker_start + 1)
        self.player.focus_source_range(
            marker_start,
            marker_end,
            context_ms=5_000,
            seek_center=False,
        )
        self.player.shuttle_stop()
        self.player.clear_clip_range()
        self.player.seek_to(target_ms)
        return True

    def _temporal_review_item_requested(self, item) -> None:
        if not self._ensure_temporal_review_item(item):
            return
        target_ms = (
            item.angles[0].proposed_onset_ms
            if item.angles else item.start_ms
        )
        if self._show_temporal_review_moment(item, target_ms):
            self.statusBar().showMessage(
                f"Temporal Review | Clip {item.clip_number} | "
                "check both proposed snap moments",
                5000,
            )

    def _temporal_review_inspect_requested(
            self, item, angle_number: int, play_around: bool) -> None:
        review = self.temporal_review_session
        if review is None or not self._ensure_temporal_review_item(item):
            return
        target_ms = review.seek_target_ms(item.item_id, angle_number)
        if not self._show_temporal_review_moment(item, target_ms):
            return
        if play_around:
            start_ms, end_ms = review.preview_range_ms(
                item.item_id, angle_number)
            if end_ms > start_ms:
                self.player.focus_source_range(
                    start_ms,
                    end_ms,
                    context_ms=1_000,
                    seek_center=False,
                )
                self.player.play_clip_range(start_ms, end_ms, False)
        action = "Playing around" if play_around else "Showing"
        self.statusBar().showMessage(
            f"{action} Angle {angle_number} proposed snap", 3000)
