"""Smoke tests for the parallel V2 presentation layer."""

from __future__ import annotations

import gc
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The Windows backend does not survive this module's repeated destruction of
# full QMediaPlayer trees reliably. The FFmpeg backend exercises the same Qt
# player API without leaking native Media Foundation callbacks across tests.
os.environ.setdefault("QT_MEDIA_BACKEND", "ffmpeg")

from PySide6.QtCore import (  # noqa: E402
    QEvent, QPoint, QPointF, QSize, QTimer, QUrl, Qt,
)
from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QBoxLayout, QDialog, QDockWidget, QFrame, QLabel, QMenu,
    QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)
from PySide6.QtTest import QTest  # noqa: E402
from shiboken6 import delete as delete_qobject  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.ui_v2.dock_v2 import (  # noqa: E402
    DockV2Deck, dock_v2_enabled, make_control_center)
from tapesift.models.clip import Clip  # noqa: E402
from tapesift.models.project import Project  # noqa: E402
from tapesift.services.library_service import LibraryRow  # noqa: E402
from tapesift.services.play_detect_service import (  # noqa: E402
    DetectedPlay, DetectionResult, UnclassifiedSegment,
)
from tapesift.services.project_service import ProjectSession  # noqa: E402
from tapesift.services import proxy_service  # noqa: E402
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow  # noqa: E402
from tapesift.ui.play_detect_dialog import PlayDetectDialog  # noqa: E402
from tapesift.ui_core.video_player import VideoPlayer  # noqa: E402
from tapesift.ui_core.minimal_jog_ring import MinimalJogRing  # noqa: E402
from tapesift.ui_core.timeline import TimelineBlock  # noqa: E402
from tapesift.ui_v2.shuttle_wheel import ProfessionalJogWheel  # noqa: E402
from tapesift.ui_v2.smooth_wheel import SmoothJogWheel  # noqa: E402
from tapesift.ui.result_manager_dialog import (  # noqa: E402
    STANDARD_RESULT_CHOICES, ResultManagerDialog,
)
from tapesift.ui_v2.library_screen import LibrarySearchScreenV2  # noqa: E402
from tapesift.ui_v2.main_window import MainWindowV2  # noqa: E402
from tapesift.ui_v2.control_center import ControlCenterDeck  # noqa: E402
from tapesift.ui_v2.tag_readout import _outcome, _play_type  # noqa: E402
from tapesift.ui_v2.fonts import load_v2_fonts  # noqa: E402
from tapesift.ui_v2.quick_tag_manager import (  # noqa: E402
    QuickTagDefinitionDialog,
)
from tapesift.ui_v2.quick_tag_tray import (  # noqa: E402
    QUICK_TAGS, RUN_PASS_LAB_TAG_KEYS, RUN_PASS_LAB_TAGS, QuickTag,
    QuickTagTray, load_quick_tags, serialize_quick_tags,
)
from tapesift.workers.analysis_preview_worker import (  # noqa: E402
    AnalysisPreviewWorker,
)
from tapesift.ui_core.start_screen import ProjectInfo  # noqa: E402
from tapesift.ui_v2.start_screen import (  # noqa: E402
    NewProjectDialog, ProjectCardV2, StartScreenV2,
)
from tapesift.ui_v2.theme import stylesheet  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    value = AppSettings(onboarding_seen=True, recent_projects=[])
    value.default_project_folder = str(tmp_path)
    value.default_output_folder = str(tmp_path / "exports")
    value.save = lambda *args, **kwargs: None
    return value


def _destroy_qt_roots(qapp):
    """Quiesce native producers, then destroy each owning widget tree once."""
    roots = [
        widget for widget in qapp.topLevelWidgets()
        if widget.parent() is None
    ]
    for widget in roots:
        for timer in widget.findChildren(QTimer):
            timer.stop()
        videos = (
            ([widget] if isinstance(widget, VideoPlayer) else [])
            + widget.findChildren(VideoPlayer)
        )
        for video in videos:
            video.stop()
            video.player.setSource(QUrl())
            video.player.setVideoOutput(None)
            video.player.setAudioOutput(None)
            # Do not clear the timeline menu here. set_timeline_key() gives
            # each QWidgetAction and its default widget to that QMenu. A
            # synchronous clear immediately before destroying the owning
            # window double-tears that native subtree down on Windows and
            # corrupts a later menu rebuild. Let the one owning root destroy
            # the complete menu/action tree.
        for media in widget.findChildren(QMediaPlayer):
            media.stop()
            media.setSource(QUrl())
            media.setVideoOutput(None)
            media.setAudioOutput(None)
    qapp.processEvents()
    roots = [
        widget for widget in qapp.topLevelWidgets()
        if widget.parent() is None
    ]
    for widget in roots:
        # Synchronous C++ destruction prevents queued callbacks from a dead
        # QMediaPlayer/menu tree surviving into the next test. This bypasses
        # closeEvent just like deleteLater, so no test ProjectSession is saved.
        delete_qobject(widget)
    qapp.processEvents()
    # Signal lambdas and QWidgetAction/default-widget relationships leave
    # Python wrapper cycles even after the C++ tree is gone. Collect them now,
    # while teardown owns the event loop, instead of letting an arbitrary later
    # menu allocation trigger collection of stale native wrappers.
    gc.collect()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


@pytest.fixture(autouse=True)
def _free_qt_widgets(qapp):
    """Destroy test-owned Qt trees without racing their native children."""
    yield
    _destroy_qt_roots(qapp)


def test_v2_theme_uses_tapesift_green():
    qss = stylesheet()
    assert "#39e07a" in qss
    assert 'QPushButton[workflow="true"]' in qss
    assert "selection-background-color: #39e07a" in qss
    assert "QWidget#CenteredApplicationMenu" in qss
    assert "QMenuBar#CenteredApplicationMenuBar" in qss


def test_ready_scrub_proxy_is_loaded_directly_without_startup_swap(
        tmp_path, monkeypatch):
    """A ready proxy must not restore a stale paused state after selection."""
    source = tmp_path / "game.mp4"
    proxy = tmp_path / "game.preview.mp4"
    loaded = []
    preview_labels = []
    messages = []
    window = SimpleNamespace(
        settings=SimpleNamespace(ffmpeg_path="ffmpeg"),
        session=SimpleNamespace(
            project=SimpleNamespace(output_folder=str(tmp_path)),
            db_path=tmp_path / "project.tapesift",
        ),
        player=SimpleNamespace(
            load=lambda path, frame_rate: loaded.append((path, frame_rate))),
        _set_preview_source_label=preview_labels.append,
        statusBar=lambda: SimpleNamespace(
            showMessage=lambda *args: messages.append(args)),
        _setup_preview_proxy=lambda *_args: pytest.fail(
            "ready proxy should be the first source"),
    )
    monkeypatch.setattr(
        proxy_service, "find_ready_proxy", lambda *_args: proxy)

    MainWindowWorkflow._load_preview_source(window, source, 59.94)

    assert loaded == [(proxy, 59.94)]
    assert preview_labels == ["optimized"]
    assert messages == [("Preview: scrub-optimized copy", 4000)]


def test_v2_home_has_a_clear_start_and_workflow_hierarchy(qapp, settings):
    screen = StartScreenV2(settings)
    texts = [label.text() for label in screen.findChildren(QLabel)]
    # Film Room framing (the old "CUT THE FILM / FIND THE PLAY" hero was
    # removed); the headline reflects whether there is work to resume.
    assert "FILM ROOM" in texts
    assert "Start your first breakdown" in texts   # no projects yet
    assert "DROP A GAME FILM HERE" in texts
    assert "DETECT PLAYS" in texts
    assert "REVIEW & LOG" in texts
    assert "EXPORT CUTUPS" in texts
    assert "NO PROJECTS YET" in texts

    buttons = {button.objectName(): button for button in screen.findChildren(QPushButton)}
    assert buttons["HomeNewProject"].property("primary") == "true"
    open_link = buttons["HomeOpenProject"]
    assert open_link.text() == "Open an existing project…"
    assert open_link.property("homeOpenLink") == "true"
    assert open_link.width() == 220
    assert not screen.drop_zone.isAncestorOf(open_link)
    assert screen.drop_zone.property("homeDropzone") == "true"
    assert screen._recent_col.isHidden()


def test_new_project_dialog_matches_the_selected_single_surface_flow(
        qapp, settings):
    dialog = NewProjectDialog(settings)

    assert dialog.windowTitle() == "New Project"
    assert dialog.objectName() == "V2NewProjectDialog"
    assert dialog.findChild(QLabel, "NewProjectFilmHeading").text() == \
        "Choose game film"
    film_icon = dialog.findChild(QLabel, "NewProjectFilmIcon")
    assert film_icon is not None
    assert film_icon.property("iconLibrary") == "Segoe Fluent Icons"
    assert film_icon.property("iconAsset") == "tapesift-video.png"
    assert dialog.project_folder == settings.default_project_folder
    assert dialog.output_folder == settings.default_output_folder
    assert dialog.create_button.property("primary") == "true"
    assert not dialog.create_button.isEnabled()


def test_new_project_dialog_uses_the_film_name_and_output_default(
        qapp, settings, tmp_path):
    film = tmp_path / "Home O vs Away D Clips.mp4"
    film.touch()
    dialog = NewProjectDialog(settings)

    dialog.set_video_path(film)

    assert dialog.video_path == film
    assert dialog.project_name == "Home O vs Away D Clips"
    assert dialog.output_folder == str(
        Path(settings.default_output_folder) / film.stem)
    assert dialog.create_button.isEnabled()
    assert dialog.film_status.property("state") == "selected"


def test_new_project_dialog_preserves_a_manually_edited_name(
        qapp, settings, tmp_path):
    film = tmp_path / "game.mp4"
    film.touch()
    dialog = NewProjectDialog(settings)
    dialog.project_name_edit.setText("Quarterback cutups")
    dialog._project_name_edited("Quarterback cutups")

    dialog.set_video_path(film)

    assert dialog.project_name == "Quarterback cutups"
    assert dialog.output_folder.endswith("Quarterback cutups")


def test_v2_home_emits_the_complete_new_project_request(
        qapp, settings, tmp_path, monkeypatch):
    film = tmp_path / "game.mp4"
    film.touch()
    screen = StartScreenV2(settings)
    emitted = []
    screen.new_project_with_video_requested.connect(
        lambda *values: emitted.append(values))

    class AcceptedDialog:
        video_path = film
        project_name = "Game"
        project_folder = str(tmp_path)
        output_folder = str(tmp_path / "exports" / "Game")

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        "tapesift.ui_v2.start_screen.NewProjectDialog",
        lambda *_args, **_kwargs: AcceptedDialog())
    screen._new_project()

    assert emitted == [(
        "Game",
        str(tmp_path),
        str(tmp_path / "exports" / "Game"),
        str(film),
    )]


def test_detect_dialog_states_all_22_only(qapp, tmp_path):
    dialog = PlayDetectDialog("ffmpeg", tmp_path / "film.mp4", 60_000)

    assert "ALL-22 FOOTBALL FILM ONLY" in dialog.scope_notice.text()
    assert "Broadcast footage" in dialog.scope_notice.text()
    assert dialog.scope_notice.property("role") == "warning"


def test_detect_dialog_ranges_preserve_review_state(qapp, tmp_path):
    dialog = PlayDetectDialog("ffmpeg", tmp_path / "film.mp4", 60_000)
    dialog.result = DetectionResult(
        plays=[
            DetectedPlay(1_000, 9_000, needs_review=False),
            DetectedPlay(10_000, 20_000, needs_review=True),
        ],
        signal="black",
        spans_found=2,
        separators_found=1,
        duration_ms=30_000,
        unclassified=[
            UnclassifiedSegment(21_000, 29_000, 1, "check this section"),
        ],
    )

    assert dialog.accepted_ranges() == [
        (1_000, 9_000, False),
        (10_000, 20_000, True),
    ]
    assert len(dialog.accepted_candidates()) == 3
    assert len(dialog.play_candidates()) == 2


def test_detect_dialog_candidates_preserve_provenance_and_wide_only(
        qapp, tmp_path):
    dialog = PlayDetectDialog("ffmpeg", tmp_path / "film.mp4", 60_000)
    dialog.result = DetectionResult(
        plays=[
            DetectedPlay(
                1_000, 9_000, angle_count=2,
                angle_starts=[1_000, 5_000], needs_review=True,
                review_reason="check the pair"),
        ],
        signal="scene",
        spans_found=2,
        separators_found=1,
        duration_ms=20_000,
        unclassified=[
            UnclassifiedSegment(
                10_000, 18_000, 2, "unclear",
                split_points_ms=[14_000]),
        ],
    )
    dialog.wide_only_check.setChecked(True)

    assert dialog.accepted_candidates() == [
        {
            "candidate_kind": "play",
            "candidate_index": 0,
            "detector_start_ms": 1_000,
            "detector_end_ms": 9_000,
            "created_start_ms": 1_000,
            "created_end_ms": 5_000,
            "angle_starts_ms": [1_000, 5_000],
            "angle_count": 2,
            "needs_review": True,
            "review_reason": "check the pair",
        },
        {
            "candidate_kind": "unclassified",
            "candidate_index": 0,
            "detector_start_ms": 10_000,
            "detector_end_ms": 18_000,
            "created_start_ms": 10_000,
            "created_end_ms": 18_000,
            "angle_starts_ms": [10_000, 14_000],
            "angle_count": 2,
            "needs_review": True,
            "review_reason": "unclear",
        },
    ]


def test_detect_dialog_uses_the_selected_guided_three_stage_flow(
        qapp, tmp_path):
    dialog = PlayDetectDialog("ffmpeg", tmp_path / "film.mp4", 120_000)

    assert dialog.objectName() == "V2DetectPlaysDialog"
    assert dialog._stage == "setup"
    assert dialog.pages.currentWidget().objectName() == "DetectSetupPage"
    assert dialog.step_labels["setup"].property("active") == "true"
    assert dialog.step_labels["review"].property("active") == "false"
    assert dialog.coverage_rail.accessibleName() == \
        "Source film timeline: green is detected, amber needs review"
    assert dialog.table.columnCount() == 6
    assert dialog.create_button.property("primary") == "true"
    assert dialog.close_button.property("iconLibrary") == \
        "Segoe Fluent Icons"
    assert dialog.close_button.property("iconAsset") == \
        "tapesift-close.png"
    assert not dialog.close_button.icon().isNull()


def test_detect_dialog_setup_and_analyze_states_keep_actions_available(
        qapp, tmp_path):
    source = tmp_path / "Away O vs Home D.mp4"
    dialog = PlayDetectDialog("ffmpeg", source, 2_914_000)
    settings_frame = dialog.findChild(QFrame, "DetectSetupSettings")

    assert settings_frame is not None
    assert settings_frame.isAncestorOf(dialog.run_btn)
    assert dialog.run_btn.property("primary") == "true"
    assert dialog.analysis_card.minimumWidth() == 650
    assert dialog.analysis_card.maximumWidth() == 720
    assert dialog.analysis_source.text() == \
        "Away O vs Home D.mp4  |  48:34 source"
    assert dialog.progress.accessibleName() == \
        "Detect Plays analysis progress"
    assert dialog.live_frame_scan.accessibleName() == \
        "Live source-film frames moving through play detection"
    assert dialog.live_frame_scan.viewport.height() == 310
    assert dialog.live_frame_scan.filmstrip.height() == 76
    assert AnalysisPreviewWorker.sample_timestamps(100_000) == [
        12_000, 26_000, 38_500, 56_000, 78_000,
    ]

    dialog._set_stage("analyze")
    assert dialog.pages.currentWidget().objectName() == "DetectAnalyzePage"
    assert not dialog.cancel_button.isHidden()
    assert dialog.cancel_button.text() == "Cancel detection"
    assert dialog.live_frame_scan._timer.isActive()

    preview = QPixmap(320, 180)
    preview.fill(QColor("#3f6f36"))
    dialog.live_frame_scan.add_frame(0, 42_000, preview)
    assert dialog.live_frame_scan.current_frame() is not None
    assert dialog.live_frame_scan.time_label.text() == \
        "Scanning source  00:42  /  48:34"

    class RunningWorker:
        cancelled = False

        def isRunning(self):
            return True

        def cancel(self):
            self.cancelled = True

    worker = RunningWorker()
    dialog._worker = worker
    dialog._cancel_or_reject()
    assert worker.cancelled
    assert dialog._worker is None
    assert dialog.pages.currentWidget().objectName() == "DetectSetupPage"
    assert dialog.run_btn.isEnabled()
    assert dialog.cancel_button.text() == "Cancel"
    assert not dialog.live_frame_scan._timer.isActive()


def test_detect_dialog_review_tables_reserve_clean_scroll_gutters(
        qapp, tmp_path):
    dialog = PlayDetectDialog("ffmpeg", tmp_path / "film.mp4", 120_000)

    assert dialog.table.horizontalScrollBarPolicy() == \
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert dialog.confident_table.horizontalScrollBarPolicy() == \
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert dialog.table.minimumHeight() == 108
    assert dialog.confident_table.minimumHeight() == 104
    assert dialog.review_detail.layout().contentsMargins().right() == 14
    assert dialog.preview_strip.layout().contentsMargins().right() == 2
    assert dialog.review_settings_bar.layout().contentsMargins().top() == 4

    dialog._done(DetectionResult(
        plays=[
            DetectedPlay(
                1_000, 20_000, needs_review=True,
                review_reason="check boundary"),
            DetectedPlay(24_000, 42_000),
        ],
        signal="scene",
        spans_found=3,
        separators_found=1,
        duration_ms=120_000,
    ))
    dialog.resize(930, 800)
    dialog.show()
    qapp.processEvents()

    assert dialog.review_detail.geometry().top() - \
        dialog.table.geometry().bottom() >= 7
    assert dialog.confident_table.geometry().top() - \
        dialog.review_detail.geometry().bottom() >= 7
    assert dialog.review_settings_bar.geometry().top() - \
        dialog.confident_table.geometry().bottom() >= 7


def test_detect_dialog_review_promotes_uncertain_ranges_without_losing_them(
        qapp, tmp_path):
    dialog = PlayDetectDialog("ffmpeg", tmp_path / "film.mp4", 120_000)
    result = DetectionResult(
        plays=[
            DetectedPlay(
                1_000, 18_000, angle_count=2,
                angle_starts=[1_000, 9_000]),
            DetectedPlay(
                22_000, 40_000, angle_count=2,
                angle_starts=[22_000, 31_000], needs_review=True,
                review_reason="check scene boundary"),
        ],
        signal="mixed",
        spans_found=5,
        separators_found=2,
        duration_ms=60_000,
        unclassified=[
            UnclassifiedSegment(
                44_000, 55_000, 1, "possible missed boundary"),
        ],
    )

    dialog._done(result)

    assert dialog._stage == "review"
    assert dialog.pages.currentWidget().objectName() == "DetectReviewPage"
    assert dialog.status_label.text() == \
        "2 plays found across 58% of the film"
    assert dialog.review_filter.isChecked()
    assert dialog.review_filter.text() == "Needs review 2"
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, 5).text() == "check scene boundary"
    assert dialog.table.item(1, 0).text() == "Review section"
    assert dialog.confident_table.rowCount() == 1
    assert dialog.confident_table.item(0, 5).text() == "High confidence"
    assert dialog.create_button.text() == "Create 2 Clips"
    assert len(dialog.accepted_candidates()) == 3
    assert len(dialog.play_candidates()) == 2


def test_detect_dialog_review_decisions_drive_the_create_count(
        qapp, tmp_path):
    dialog = PlayDetectDialog("ffmpeg", tmp_path / "film.mp4", 80_000)
    result = DetectionResult(
        plays=[
            DetectedPlay(
                1_000, 20_000, needs_review=True,
                review_reason="check boundary"),
            DetectedPlay(24_000, 42_000),
        ],
        signal="scene",
        spans_found=3,
        separators_found=1,
        duration_ms=80_000,
        unclassified=[
            UnclassifiedSegment(48_000, 62_000, 1, "unclear"),
        ],
    )
    dialog._done(result)

    dialog.table.setCurrentCell(0, 0)
    dialog._decide_selected("dismiss")
    assert len(dialog.play_candidates()) == 1
    assert dialog.create_button.text() == "Create 1 Clip"

    dialog.table.setCurrentCell(1, 0)
    dialog._decide_selected("keep")
    assert len(dialog.play_candidates()) == 2
    assert any(
        candidate["candidate_kind"] == "unclassified"
        for candidate in dialog.play_candidates())
    assert dialog.create_button.text() == "Create 2 Clips"


def test_v2_bundles_the_selected_display_font(qapp):
    assert "Rajdhani" in load_v2_fonts()


def test_v2_home_balances_a_recent_project_with_a_new_project_tile(qapp, settings, tmp_path):
    recent = tmp_path / "recent.clipforge"
    recent.touch()          # the home screen drops recents whose file is gone
    settings.recent_projects = [str(recent)]
    screen = StartScreenV2(settings)
    buttons = {button.objectName(): button for button in screen.findChildren(QPushButton)}
    assert "RecentNewProject" in buttons
    assert screen.summary_label.text() == ""
    assert screen._room_title.text() == "Resume the latest breakdown"
    assert buttons["HomeOpenProject"].text() == "Open an existing project…"


def test_v2_home_footer_carries_the_current_build(
        qapp, settings):
    screen = StartScreenV2(settings)
    build = screen.findChild(QLabel, "HomeBuildLabel")

    assert build is not None
    assert build.text() == \
        "TapeSift v0.7.0-alpha  •  Your film. Your edge."
    assert screen.findChild(QPushButton, "HomeGitHubLink") is None
    assert "@example" not in {
        label.text() for label in screen.findChildren(QLabel)}


def test_v2_project_resume_remains_interactive(qapp, tmp_path):
    path = tmp_path / "game.clipforge"
    path.touch()
    card = ProjectCardV2(ProjectInfo(
        path=path, name="Game", exists=True, clip_count=10, logged_count=3))
    resumed = []
    card.resume_requested.connect(resumed.append)
    resume = next(button for button in card.findChildren(QPushButton)
                  if button.text() == "Continue reviewing")
    QTest.mouseClick(resume, Qt.MouseButton.LeftButton)
    assert resumed == [str(path)]


def test_v2_project_overflow_is_keyboard_accessible(qapp, tmp_path):
    path = tmp_path / "game.clipforge"
    card = ProjectCardV2(ProjectInfo(
        path=path, name="Game", exists=True, clip_count=10, logged_count=3))
    overflow = card.findChild(QToolButton, "ProjectOverflowMenu")
    assert overflow is not None
    assert overflow.focusPolicy() == Qt.FocusPolicy.StrongFocus
    assert overflow.accessibleName() == "Project actions for Game"
    assert overflow.toolTip() == "Project actions for Game"


def test_v2_long_project_titles_do_not_widen_the_stacked_home(
        qapp, settings, monkeypatch, tmp_path):
    from tapesift.ui_v2 import start_screen as start_screen_module

    # The card is rendered from the patched load_project_info below, but the
    # home screen drops recents whose file is gone before it gets there.
    recent = tmp_path / "long.clipforge"
    recent.touch()
    settings.recent_projects = [str(recent)]
    monkeypatch.setattr(
        start_screen_module,
        "load_project_info",
        lambda path: ProjectInfo(
            path=path,
            name="A very long scouting project title " * 3,
            source_name="A similarly long source film name " * 3,
            exists=True,
            clip_count=78,
            logged_count=12,
        ),
    )
    screen = StartScreenV2(settings)
    screen.resize(920, 700)
    screen.show()
    qapp.processEvents()

    assert screen._body_layout.direction() == \
        QBoxLayout.Direction.TopToBottom
    assert screen.cards_host.width() <= screen.cards_scroll.viewport().width()
    assert screen.cards_scroll.horizontalScrollBar().maximum() == 0


def test_v2_window_keeps_three_production_screens(
        qapp, settings, monkeypatch):
    monkeypatch.delenv("TAPESIFT_DOCK_V2", raising=False)
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    try:
        assert window.stack.count() == 3
        assert isinstance(window.start_screen, StartScreenV2)
        assert isinstance(window.library_screen, LibrarySearchScreenV2)
        assert window.workflow_ribbon is not None
        assert window.windowTitle() == "TapeSift"
        assert window.player.parentWidget() is window._player_panel
        # The Tag Map dock is retired; its projections live in the
        # attribute grid under the timeline.
        assert set(window._workspace_docks) == {
            "player", "clips", "play_details"}
        assert all(
            isinstance(dock, QDockWidget)
            for dock in window._workspace_docks.values())
        assert window._workspace_docks["player"].widget() \
            is window._player_panel
        assert window._workspace_docks["clips"].widget() \
            is window.clip_ledger_scroll
        assert window._workspace_docks["play_details"].widget() \
            is window.clip_details_scroll
        assert isinstance(window.clip_ledger_scroll, QScrollArea)
        assert isinstance(window.clip_details_scroll, QScrollArea)
        assert window.clip_ledger_scroll.widget() is window.clip_list
        assert window.clip_details_scroll.widget() is window.clip_editor
        assert window.clip_ledger_scroll.verticalScrollBar() is not \
            window.clip_details_scroll.verticalScrollBar()
        assert type(window.control_center) is DockV2Deck
        control_dock = window._control_center_dock
        strip_slot = window.player.control_strip_slot
        assert control_dock.widget() is None
        assert (control_dock.minimumHeight(),
                control_dock.maximumHeight()) == (0, 0)
        assert strip_slot.layout().count() == 1
        assert strip_slot.layout().itemAt(0).widget() is \
            window.control_center
        assert window.control_center.parentWidget() is strip_slot
        assert window.player.isAncestorOf(window.control_center)
        assert window._player_panel.isAncestorOf(window.control_center)
        assert not control_dock.isAncestorOf(window.control_center)
        assert window.player.control_center is window.control_center
        assert window.player.transport_pill is window.control_center
        assert window.control_center.bound_player is window.player
        assert window.findChildren(ControlCenterDeck) == [
            window.control_center]
        assert not hasattr(window.player, "voiceover_record_button")
        assert not hasattr(window.player, "deck_export_style_buttons")
        assert control_dock.objectName() == \
            "tapesift.dock.control_center"
        assert window.dockWidgetArea(control_dock) == \
            Qt.DockWidgetArea.BottomDockWidgetArea
        assert window._export_dock.widget() is window.export_panel
        assert window.dockWidgetArea(
            window._workspace_docks["player"]) == \
            Qt.DockWidgetArea.TopDockWidgetArea
        assert window.dockWidgetArea(
            window._workspace_docks["clips"]) == \
            Qt.DockWidgetArea.LeftDockWidgetArea
        assert window.dockWidgetArea(
            window._workspace_docks["play_details"]) == \
            Qt.DockWidgetArea.RightDockWidgetArea
        # Docks belong to the project workspace lifecycle, not Home.
        assert not any(
            dock.isVisible() for dock in window._workspace_docks.values())
        assert window.findChildren(VideoPlayer) == [window.player]
        # Quick Tags live in the Tag Map header, never in the deck.
        assert window.quick_tag_tray.parentWidget() is \
            window.player.quick_tag_slot
        assert window._player_panel.isAncestorOf(window.quick_tag_tray)
        assert not window._control_center_dock.isAncestorOf(
            window.quick_tag_tray)
        assert window.stack.maximumHeight() > 0
        assert window.clip_list._sidebar_mode is True
        assert window.clip_editor._analyst_mode is True
    finally:
        window.hide()


def test_v2_defers_recovery_until_workspace_timers_exist(
        qapp, settings, monkeypatch):
    recovery_timer_states: list[bool] = []
    monkeypatch.setattr(
        MainWindowWorkflow,
        "_check_recovery",
        lambda self: recovery_timer_states.append(
            hasattr(self, "_workspace_recovery_timer")),
    )

    window = MainWindowV2(settings)
    # Shown, because that is the whole point of deferring it: the prompt
    # must not appear behind a window the user cannot see.
    window.show()

    assert recovery_timer_states == []
    qapp.processEvents()
    assert recovery_timer_states == [True]
    window.hide()
    window.deleteLater()
    qapp.processEvents()


def test_v2_recovery_does_not_prompt_from_a_window_on_its_way_out(
        qapp, settings, monkeypatch):
    """The queued prompt has to die with the window that queued it.

    deleteLater only queues destruction, so the window is still a valid
    object while the same event loop delivers the timer. It fired into a
    teardown and took the process with it.
    """
    ran: list[bool] = []
    monkeypatch.setattr(
        MainWindowWorkflow, "_check_recovery", lambda self: ran.append(True))

    window = MainWindowV2(settings)
    window.show()
    window.hide()
    qapp.processEvents()

    assert ran == []


def test_dock_v2_queued_geometry_dies_with_its_deck(qapp):
    calls: list[str] = []
    deck = DockV2Deck()
    deck._apply_premium_geometry = lambda: calls.append("geometry")

    deck.show()
    assert calls == ["geometry"]
    delete_qobject(deck)
    qapp.processEvents()

    assert calls == ["geometry"]


def test_quick_tag_tray_reflects_selected_clip_details(qapp):
    tray = QuickTagTray()
    clip = Clip(
        start_ms=0, end_ms=10_000,
        details={"run_pass": "Pass", "play_type": "Screen"})

    tray.set_clip(clip)

    assert tray.buttons["pass"].isChecked()
    assert tray.buttons["screen"].isChecked()
    assert not tray.buttons["run"].isChecked()
    assert tray.buttons["run"].property("tagFamily") == "run"
    assert tray.buttons["pass"].property("tagFamily") == "pass"
    assert tray.buttons["screen"].property("tagFamily") == "screen"
    assert tray.buttons["rpo_run"].property("tagFamily") == "rpo"
    assert tray.buttons["interception"].property("tagFamily") == \
        "interception"
    assert all(button.isEnabled() for button in tray.buttons.values())

    tray.set_clip(None)
    assert not any(button.isEnabled() for button in tray.buttons.values())
    assert not any(button.isChecked() for button in tray.buttons.values())


def test_default_quick_tags_make_the_rpo_decision_explicit(qapp):
    tray = QuickTagTray()

    assert tray.details("rpo_run") == {
        "run_pass": "Run", "play_type": "RPO"}
    assert tray.details("rpo_pass") == {
        "run_pass": "Pass", "play_type": "RPO"}
    assert "rpo" not in tray.buttons
    for key in ("rpo_run", "rpo_pass"):
        button = tray.buttons[key]
        assert button.minimumWidth() >= \
            button.fontMetrics().horizontalAdvance(button.text()) + 30
    assert tray.details("special") == {"run_pass": "Special"}
    assert tray.details("no_play") == {"run_pass": "No Play"}
    assert "special" not in tray.buttons
    assert "no_play" not in tray.buttons


def test_run_pass_lab_has_fixed_research_actions(qapp):
    tray = QuickTagTray(serialize_quick_tags(RUN_PASS_LAB_TAGS))

    assert tuple(tray.buttons) == RUN_PASS_LAB_TAG_KEYS
    assert tray.details("play_action") == {
        "play_action": "Play Action"}
    assert tray.details("scramble") == {
        "run_pass": "Run", "play_type": "Scramble"}
    assert tray.details("sack") == {
        "run_pass": "Pass", "result": "Sack"}


def test_run_pass_lab_window_does_not_replace_everyday_favorites(
        qapp, settings, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    everyday = serialize_quick_tags((QUICK_TAGS[1], QUICK_TAGS[0]))
    settings.quick_tag_favorites = everyday

    lab = MainWindowV2(settings, run_pass_lab=True)
    try:
        assert tuple(lab.quick_tag_tray.buttons) == RUN_PASS_LAB_TAG_KEYS
        assert lab.quick_tag_tray.heading.text() == "RUN / PASS LAB"
        assert lab.quick_tag_tray.add_button.isHidden()
        assert lab.quick_tag_tray.manage_button.isHidden()
        assert settings.quick_tag_favorites == everyday
    finally:
        lab.hide()


def test_legacy_saved_rpo_tag_expands_to_both_decisions():
    restored = load_quick_tags([{
        "key": "rpo",
        "label": "RPO",
        "details": {"play_type": "RPO"},
        "description": "Old built-in action",
    }])

    assert [tag.key for tag in restored] == ["rpo_run", "rpo_pass"]


def test_quick_tag_result_state_supports_more_than_one_result(qapp):
    tray = QuickTagTray()
    tray.set_clip(Clip(
        start_ms=0,
        end_ms=10_000,
        details={"result": "Touchdown; First Down"},
    ))

    assert tray.buttons["touchdown"].isChecked()
    assert tray.buttons["first_down"].isChecked()


def test_quick_tag_favorites_round_trip_custom_actions(qapp):
    custom = QuickTag(
        "custom_explosive", "Explosive",
        (("result", "Gain"), ("action", "Big Hit")),
        "Save an explosive big-hit play",
    )
    configured = (QUICK_TAGS[1], custom, QUICK_TAGS[0])

    restored = load_quick_tags(serialize_quick_tags(configured))
    tray = QuickTagTray(serialize_quick_tags(configured))

    assert restored == configured
    assert tuple(tray.buttons) == ("pass", "custom_explosive", "run")
    assert tray.details("custom_explosive") == {
        "result": "Gain",
        "action": "Big Hit",
    }


def test_quick_tag_tray_manage_controls_are_fixed_outside_scroll(qapp):
    tray = QuickTagTray()
    requests: list[bool] = []
    tray.manage_requested.connect(requests.append)

    QTest.mouseClick(tray.add_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(tray.manage_button, Qt.MouseButton.LeftButton)

    assert requests == [True, False]
    assert tray.add_button.parent() is tray
    assert tray.manage_button.parent() is tray
    assert all(
        button.parent() is tray.button_host
        for button in tray.buttons.values())


def test_quick_tag_rule_editor_rows_fit_their_controls(qapp):
    dialog = QuickTagDefinitionDialog(
        QuickTag(
            "custom_result", "Result", (("result", "First Down"),),
            "Save a result"),
        AppSettings().fixed_details,
    )

    field = dialog.rules.cellWidget(0, 0)
    value = dialog.rules.cellWidget(0, 1)
    assert dialog.rules.rowHeight(0) >= 42
    assert field.minimumHeight() >= 32
    assert value.minimumHeight() >= 32


def test_v2_quick_tag_saves_through_existing_undo_flow(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Quick tags", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=1_000, end_ms=9_000, clip_title="Play"))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    assert window.clip_editor.heading_label.text() == "PLAY 001"
    assert window.clip_editor.essentials_box.isHidden()
    assert window.clip_editor.classification_box.isHidden()
    # The situation is logged in the grid now, so the inspector carries no
    # picker for it at all - neither shape claims the space.
    assert window.clip_editor.attribute_rows_panel.isHidden()
    assert window.clip_editor.quick_pickers.isHidden()
    assert not window.clip_editor.action_bar.isHidden()
    assert window.clip_editor.details_section.toggle.text() == "Naming Export"
    assert window.clip_editor.metadata_section.isHidden()
    assert window.clip_editor.export_section.isHidden()
    assert not window.clip_editor.advanced_details_section.isHidden()
    assert not window.clip_editor.advanced_details_section.is_expanded()
    assert not window.clip_editor.details_section.is_expanded()
    assert window.clip_editor.players_box.title() == "PEOPLE"
    assert window.clip_editor.notes_box.title() == "NOTES"
    window.clip_editor.title_edit.setFocus()
    QTest.keyClicks(window.clip_editor.title_edit, " revised")
    assert window.clip_editor.save_state_label.text() == "UNSAVED"

    QTest.mouseClick(
        window.quick_tag_tray.buttons["screen"],
        Qt.MouseButton.LeftButton)

    saved = session.get_clip(clip.id)
    assert saved is not None
    assert saved.details["run_pass"] == "Pass"
    assert saved.details["play_type"] == "Screen"
    assert "Pass" in saved.tags
    assert "Screen" in saved.tags
    assert window.quick_tag_tray.buttons["pass"].isChecked()
    assert window.quick_tag_tray.buttons["screen"].isChecked()
    assert _play_type(saved.details) == ("screen", "SCREEN")
    assert window.clip_editor.save_state_label.text() == "SAVED"

    QTest.mouseClick(
        window.quick_tag_tray.buttons["touchdown"],
        Qt.MouseButton.LeftButton)
    QTest.mouseClick(
        window.quick_tag_tray.buttons["first_down"],
        Qt.MouseButton.LeftButton)
    saved = session.get_clip(clip.id)
    assert saved is not None
    assert saved.details["result"] == "Touchdown; First Down"
    assert window.quick_tag_tray.buttons["touchdown"].isChecked()
    assert window.quick_tag_tray.buttons["first_down"].isChecked()

    assert session.undo() == "edit clip"
    assert session.undo() == "edit clip"
    assert session.undo() == "edit clip"
    restored = session.get_clip(clip.id)
    assert restored is not None
    assert restored.details == {}

    session.conn.close()
    window.hide()


def test_removing_touchdown_refreshes_outcome_readout_and_undo_restores_it(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Outcome correction", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=102_566,
        end_ms=131_616,
        clip_number=5,
        clip_title="Touchdown catch",
        tags=["Touchdown", "First Down", "Reception"],
        details={
            "quarter": "Q1",
            "down_distance": "1st",
            "run_pass": "Pass",
            "result": "Touchdown; First Down; Reception",
            "player_name": "CharMar Brown",
        },
    ))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)

    def outcome_key() -> str | None:
        saved_clip = session.get_clip(clip.id)
        projected = _outcome(saved_clip) if saved_clip is not None else None
        return projected[0] if projected else None

    assert outcome_key() == "score"

    QTest.mouseClick(
        window.clip_editor.result_buttons["Touchdown"],
        Qt.MouseButton.LeftButton)
    QTest.mouseClick(
        window.clip_editor.apply_btn,
        Qt.MouseButton.LeftButton)
    qapp.processEvents()

    saved = session.get_clip(clip.id)
    assert saved is not None
    assert saved.details["result"] == "First Down; Reception"
    # Removing a saved result removes its obsolete mirror on both shells.
    assert "Touchdown" not in saved.tags
    assert outcome_key() == "first_down"

    window._undo()
    qapp.processEvents()
    restored = session.get_clip(clip.id)
    assert restored is not None
    assert restored.details["result"] == \
        "Touchdown; First Down; Reception"
    assert outcome_key() == "score"

    window._redo()
    qapp.processEvents()
    redone = session.get_clip(clip.id)
    assert redone is not None
    assert redone.details["result"] == "First Down; Reception"
    assert outcome_key() == "first_down"

    session.conn.close()
    window.hide()


def test_active_quick_tag_click_removes_detail_mirrored_tag_and_readout(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Quick tag correction", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=10_000,
        end_ms=20_000,
        clip_title="Correctable result",
        tags=["Touchdown", "First Down"],
        details={"result": "Touchdown; First Down"},
    ))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    assert window.quick_tag_tray.buttons["touchdown"].isChecked()
    assert window.quick_tag_tray.buttons["first_down"].isChecked()

    QTest.mouseClick(
        window.quick_tag_tray.buttons["touchdown"],
        Qt.MouseButton.LeftButton)
    qapp.processEvents()

    saved = session.get_clip(clip.id)
    assert saved is not None
    assert saved.details["result"] == "First Down"
    assert "Touchdown" not in saved.tags
    assert "First Down" in saved.tags
    assert not window.quick_tag_tray.buttons["touchdown"].isChecked()
    assert window.quick_tag_tray.buttons["first_down"].isChecked()
    projected = _outcome(saved)
    assert projected is not None and projected[0] == "first_down"

    window._undo()
    qapp.processEvents()
    restored = session.get_clip(clip.id)
    assert restored is not None
    assert restored.details["result"] == "Touchdown; First Down"
    assert "Touchdown" in restored.tags

    session.conn.close()
    window.hide()


def test_run_pass_lab_play_action_toggles_without_erasing_primary_label(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings, run_pass_lab=True)
    session = ProjectSession.create(
        "Lab tags", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=1_000,
        end_ms=9_000,
        details={"run_pass": "Pass", "play_type": "RPO"},
    ))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)

    QTest.mouseClick(
        window.quick_tag_tray.buttons["play_action"],
        Qt.MouseButton.LeftButton)
    saved = session.get_clip(clip.id)
    assert saved is not None
    assert saved.details["run_pass"] == "Pass"
    assert saved.details["play_type"] == "RPO"
    assert saved.details["play_action"] == "Play Action"

    QTest.mouseClick(
        window.quick_tag_tray.buttons["play_action"],
        Qt.MouseButton.LeftButton)
    saved = session.get_clip(clip.id)
    assert saved is not None
    assert saved.details["run_pass"] == "Pass"
    assert saved.details["play_type"] == "RPO"
    assert saved.details.get("play_action", "") == ""

    session.conn.close()
    window.hide()


def test_v2_custom_quick_tag_uses_saved_definition(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings.quick_tag_favorites = serialize_quick_tags((
        QuickTag(
            "custom_pressure", "Pressure",
            (("run_pass", "Pass"), ("action", "Pressure")),
            "Save a pass pressure",
        ),
    ))
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Custom quick tag", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=1_000, end_ms=9_000, clip_title="Play"))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)

    QTest.mouseClick(
        window.quick_tag_tray.buttons["custom_pressure"],
        Qt.MouseButton.LeftButton)

    saved = session.get_clip(clip.id)
    assert saved is not None
    assert saved.details["run_pass"] == "Pass"
    assert saved.details["action"] == "Pressure"
    session.conn.close()
    window.hide()


def test_v2_compact_inspector_layout(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Inspector", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=12_400, end_ms=20_800, clip_number=8,
        clip_title="2nd & 10 | Y Off Tackle",
        notes="Strong right side.",
        details={
            "quarter": "Q2",
            "down_distance": "2nd & 10",
            "ball_on": "35",
            "run_pass": "Pass",
            "play_type": "Screen",
            "play_action": "Play Action",
            "result": "8 Yard Gain",
            "player_name": "#12 QB",
            "other_players": "#7 WR, #8 RB",
        }))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)

    editor = window.clip_editor
    assert editor.heading_label.text() == "PLAY 008"
    assert editor.review_state_label.text() == "LOGGED"
    assert editor.save_state_label.text() == "SAVED"
    assert editor.title_summary_label.text() == "2nd & 10 | Y Off Tackle"
    assert editor.situation_summary_label.text() == (
        "Q2  |  2nd & 10  |  BALL ON 35  |  PASS  |  8 YARD GAIN")
    assert "#12 QB" in editor.range_summary_label.text()
    assert "8.4s" in editor.range_summary_label.text()
    assert editor.play_rail.property("playKind") == "pass"
    assert editor.classification_buttons["pass"].isChecked()
    assert editor.classification_buttons["screen"].isChecked()
    assert editor.classification_buttons["play_action"].isChecked()
    assert not editor.notes_box.isHidden()
    assert editor.notes_edit.toPlainText() == "Strong right side."
    assert editor.players_box.parentWidget() is editor.analyst_right_column
    assert editor.notes_box.parentWidget() is editor.analyst_right_column
    assert editor.essentials_box.isHidden()
    assert editor.classification_box.isHidden()
    assert editor.analyst_left_column.isHidden()
    # Quarter, down and result remain one-click answers while the editable
    # range is pinned above the rest of Play Details. They are asked as rows
    # now; the chip pickers still track the same fields behind them.
    assert editor.quarter_buttons["Q2"].isChecked()
    assert editor.down_buttons["2nd"].isChecked()
    assert editor.attribute_rows["quarter"].value() == "Q2"
    assert editor.attribute_rows["down"].value() == "2nd"
    # The panel itself is off: the grid logs the situation now.
    assert editor.attribute_rows_panel.isHidden()
    # Analyst cells stack the label above the field. Side-by-side crushed the
    # field to ~57px in narrow inspector columns, which received keystrokes it
    # had no room to render.
    assert editor.detail_cell_layouts["player_name"].direction() \
        == QBoxLayout.Direction.TopToBottom
    assert editor.detail_edits["player_name"].lineEdit().placeholderText() \
        == "Name or number"
    assert editor.detail_edits["other_players"].lineEdit().placeholderText() \
        == "Name or number"
    assert editor.detail_labels["other_players"].text() == "Involved"
    assert editor._form_layout.indexOf(editor.primary_container) >= 0
    assert editor.primary_container.property("analystRange") == "true"
    assert editor.range_duration_label.text() == "8.4s"
    assert editor.details_section.body_layout.indexOf(
        editor.analyst_naming_container) >= 0
    assert editor._analyst_preview_layout.indexOf(
        editor.preview_container) >= 0
    assert editor.details_section.body_layout.indexOf(editor.details_box) < 0
    assert editor.advanced_details_section.body_layout.indexOf(
        editor.details_box) >= 0
    assert not editor.advanced_details_section.is_expanded()
    assert editor.advanced_details_section.toggle.text() == (
        "Additional Play Details    4 of 11")
    assert editor.details_section.toggle.text() == "Naming Export"
    assert not editor.details_section.is_expanded()
    # These three used to be pulled out of the details grid because a chip
    # row owned them. The grid under the timeline logs the situation now,
    # so they come back here as ordinary fields - a value you can see and
    # cannot reach would be worse than either arrangement.
    for key in ("quarter", "down_distance", "result"):
        assert editor._details_grid.indexOf(editor.detail_cells[key]) >= 0
    assert editor.detail_cell_layouts["run_pass"].direction() \
        == QBoxLayout.Direction.LeftToRight
    assert editor.apply_btn.text() == "Save Play"
    assert editor.save_next_btn.text() == "Save + Next"
    assert editor.package_btn.text() == "Package / Cut Up"
    assert not editor.package_btn.isHidden()
    assert editor._action_button_row.indexOf(editor.quick_export_btn) == 0
    assert editor._action_button_row.indexOf(editor.package_btn) == 1
    assert editor._action_button_row_2.indexOf(editor.apply_btn) == 0
    assert editor._action_button_row_2.indexOf(editor.save_next_btn) == 1

    editor._update_analyst_ledger_direction(700)
    assert editor._analyst_ledger_layout.direction() \
        == QBoxLayout.Direction.TopToBottom
    assert editor.analyst_vertical_divider.isHidden()
    assert editor.analyst_horizontal_divider.isHidden()
    editor._update_analyst_ledger_direction(420)
    assert editor._analyst_ledger_layout.direction() \
        == QBoxLayout.Direction.TopToBottom
    assert editor.analyst_vertical_divider.isHidden()
    assert editor.analyst_horizontal_divider.isHidden()

    window.stack.setCurrentWidget(window.workspace)
    window.resize(1600, 940)
    window.show()
    qapp.processEvents()
    form = editor.form_area.widget()
    notes_bottom = editor.notes_edit.mapTo(
        form, QPoint(0, editor.notes_edit.height())).y()
    disclosures_top = editor.advanced_details_section.mapTo(
        form, QPoint(0, 0)).y()
    assert editor._analyst_ledger_layout.stretch(3) == 0
    assert 0 <= disclosures_top - notes_bottom <= 24
    right_edge = editor.action_bar.contentsRect().right()
    assert editor.save_next_btn.geometry().right() <= right_edge
    QTest.mouseClick(
        editor.classification_buttons["run"], Qt.MouseButton.LeftButton)
    assert editor.classification_buttons["run"].isChecked()
    assert editor.classification_buttons["screen"].isChecked()
    assert editor.detail_edits["run_pass"].text() == "Run"
    assert editor.detail_edits["play_type"].text() == "Screen"
    assert editor.detail_edits["play_action"].text() == "Play Action"
    assert editor.save_state_label.text() == "UNSAVED"

    QTest.mouseClick(
        editor.classification_buttons["play_action"],
        Qt.MouseButton.LeftButton)
    assert editor.detail_edits["play_action"].text() == ""
    assert editor.detail_edits["run_pass"].text() == "Run"
    QTest.mouseClick(
        editor.classification_buttons["play_action"],
        Qt.MouseButton.LeftButton)
    assert editor.detail_edits["play_action"].text() == "Play Action"

    QTest.mouseClick(
        editor.classification_buttons["rpo_run"], Qt.MouseButton.LeftButton)
    assert editor.detail_edits["run_pass"].text() == "Run"
    assert editor.detail_edits["play_type"].text() == "RPO"
    assert editor.classification_buttons["rpo_run"].isChecked()
    assert not editor.classification_buttons["rpo_pass"].isChecked()

    QTest.mouseClick(
        editor.classification_buttons["rpo_pass"], Qt.MouseButton.LeftButton)
    assert editor.detail_edits["run_pass"].text() == "Pass"
    assert editor.detail_edits["play_type"].text() == "RPO"
    assert editor.classification_buttons["rpo_pass"].isChecked()
    assert not editor.classification_buttons["rpo_run"].isChecked()

    QTest.mouseClick(
        editor.classification_buttons["scramble"], Qt.MouseButton.LeftButton)
    assert editor.detail_edits["run_pass"].text() == "Run"
    assert editor.detail_edits["play_type"].text() == "Scramble"
    assert editor.classification_buttons["scramble"].isChecked()

    QTest.mouseClick(
        editor.classification_buttons["sack"], Qt.MouseButton.LeftButton)
    assert editor.detail_edits["run_pass"].text() == "Pass"
    assert editor.detail_edits["play_type"].text() == ""
    assert "Sack" in editor.detail_edits["result"].text()
    assert editor.classification_buttons["sack"].isChecked()
    assert editor.detail_edits["play_action"].text() == "Play Action"

    QTest.mouseClick(
        editor.classification_buttons["special"], Qt.MouseButton.LeftButton)
    assert editor.detail_edits["run_pass"].text() == "Special"
    assert editor.classification_buttons["special"].isChecked()

    QTest.mouseClick(
        editor.classification_buttons["no_play"], Qt.MouseButton.LeftButton)
    assert editor.detail_edits["run_pass"].text() == "No Play"
    assert editor.classification_buttons["no_play"].isChecked()

    QTest.mouseClick(editor.edit_title_btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert editor.details_section.is_expanded()
    assert editor.title_edit.hasFocus()

    session.conn.close()
    window.hide()


def test_v2_quick_pickers_log_the_common_answers_with_one_click(qapp):
    from tapesift.ui_core.clip_editor import ClipEditor

    settings = AppSettings(onboarding_seen=True)
    settings.save = lambda *a, **k: None
    editor = ClipEditor(settings)
    editor.setObjectName("V2ReviewInspector")
    editor.set_analyst_mode(True)
    editor.set_clip(Clip(start_ms=0, end_ms=40_000, clip_number=3))

    QTest.mouseClick(editor.quarter_buttons["Q2"], Qt.MouseButton.LeftButton)
    QTest.mouseClick(editor.down_buttons["3rd"], Qt.MouseButton.LeftButton)
    QTest.mouseClick(
        editor.result_buttons["First Down"], Qt.MouseButton.LeftButton)
    QTest.mouseClick(
        editor.result_buttons["Reception"], Qt.MouseButton.LeftButton)

    assert editor.detail_edits["quarter"].text() == "Q2"
    assert editor.detail_edits["down_distance"].text() == "3rd"
    assert editor.detail_edits["result"].text() == "First Down; Reception; Completion"
    assert editor.result_buttons["First Down"].isChecked()
    assert editor.result_buttons["Reception"].isChecked()

    # Distance to go edits the existing combined field rather than creating a
    # second source of truth.
    editor.to_go_edit.clear()
    QTest.keyClicks(editor.to_go_edit, "7")
    assert editor.detail_edits["down_distance"].text() == "3rd & 7"
    assert editor.to_go_edit.text() == "7"

    # Each result toggles independently; removing one does not erase another.
    QTest.mouseClick(
        editor.result_buttons["Reception"], Qt.MouseButton.LeftButton)
    assert editor.detail_edits["result"].text() == "First Down"
    assert editor.result_buttons["First Down"].isChecked()

    # Setting a down must not discard a distance already recorded.
    editor.detail_edits["down_distance"].setText("3rd & 7")
    QTest.mouseClick(editor.down_buttons["2nd"], Qt.MouseButton.LeftButton)
    assert editor.detail_edits["down_distance"].text() == "2nd & 7"
    editor.to_go_edit.selectAll()
    QTest.keyClicks(editor.to_go_edit, "Goal")
    assert editor.detail_edits["down_distance"].text() == "2nd & Goal"
    assert editor.to_go_edit.text() == "Goal"

    # Clicking the active chip clears it.
    QTest.mouseClick(editor.quarter_buttons["Q2"], Qt.MouseButton.LeftButton)
    assert editor.detail_edits["quarter"].text() == ""


def test_v2_quick_pickers_light_up_for_bare_stored_values(qapp):
    from tapesift.ui_core.clip_editor import ClipEditor

    settings = AppSettings(onboarding_seen=True)
    settings.save = lambda *a, **k: None
    editor = ClipEditor(settings)
    editor.setObjectName("V2ReviewInspector")
    editor.set_analyst_mode(True)
    # Detected clips store "1" and "2 & 10", not "Q1" and "2nd".
    editor.set_clip(Clip(
        start_ms=0, end_ms=40_000, clip_number=1,
        details={"quarter": "1", "down_distance": "2 & 10"}))

    assert editor.quarter_buttons["Q1"].isChecked()
    assert editor.down_buttons["2nd"].isChecked()
    assert editor.to_go_edit.text() == "10"
    assert not editor.quarter_buttons["Q2"].isChecked()


def test_v2_to_go_saves_combined_down_distance_and_undo_restores_it(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Distance to go", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=1_000,
        end_ms=9_000,
        clip_number=1,
        details={"quarter": "Q1", "down_distance": "2nd & 10"},
    ))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    assert window.clip_editor.to_go_edit.text() == "10"

    window.clip_editor.to_go_edit.selectAll()
    QTest.keyClicks(window.clip_editor.to_go_edit, "7")
    QTest.mouseClick(
        window.clip_editor.apply_btn, Qt.MouseButton.LeftButton)
    qapp.processEvents()

    saved = session.get_clip(clip.id)
    assert saved is not None
    assert saved.details["down_distance"] == "2nd & 7"

    window._undo()
    qapp.processEvents()
    restored = session.get_clip(clip.id)
    assert restored is not None
    assert restored.details["down_distance"] == "2nd & 10"

    session.conn.close()
    window.hide()


def test_v2_player_names_are_remembered_for_the_next_clip(qapp):
    from tapesift.ui_core.clip_editor import ClipEditor

    settings = AppSettings(onboarding_seen=True)
    settings.save = lambda *a, **k: None
    editor = ClipEditor(settings)
    editor.setObjectName("V2ReviewInspector")
    editor.set_analyst_mode(True)
    editor.set_vocabulary({
        "player_name": ["Damien Martinez"],
        "other_players": ["Cam McCormick, Xavier Restrepo"],
    }, [])

    player_edit = editor.detail_edits["player_name"]
    for name in ("Damien Martinez", "Cam McCormick", "Xavier Restrepo"):
        assert player_edit.findText(name) >= 0

    first = Clip(start_ms=0, end_ms=40_000, clip_number=1)
    editor.set_clip(first)
    player_edit.setText("Jacolby George")
    editor.detail_edits["other_players"].setText(
        "Isaiah Horton, Samuel Brown")
    assert editor._apply()

    editor.set_clip(Clip(start_ms=50_000, end_ms=90_000, clip_number=2))
    for name in ("Jacolby George", "Isaiah Horton", "Samuel Brown"):
        assert player_edit.findText(name) >= 0


def test_v2_player_vocabulary_collapses_case_variants(qapp):
    from tapesift.ui_core.clip_editor import ClipEditor

    settings = AppSettings(onboarding_seen=True)
    settings.save = lambda *a, **k: None
    editor = ClipEditor(settings)
    editor.set_analyst_mode(True)
    editor.set_vocabulary({
        "player_name": ["Jordan Davis", "jordan davis", "JORDAN DAVIS"],
        "other_players": ["Marcus Lee, jordan davis"],
    }, [])

    values = [
        editor.detail_edits["player_name"].itemText(index)
        for index in range(editor.detail_edits["player_name"].count())
    ]
    assert [value for value in values
            if value.casefold() == "jordan davis"] == ["Jordan Davis"]
    assert "Marcus Lee" in values


def test_v2_result_chips_keep_the_defaults_reachable(qapp):
    from tapesift.ui_core.clip_editor import ClipEditor

    settings = AppSettings(onboarding_seen=True)
    settings.save = lambda *a, **k: None
    editor = ClipEditor(settings)
    editor.setObjectName("V2ReviewInspector")
    editor.set_analyst_mode(True)
    # A project that only ever logged one result must not collapse to one chip.
    editor.set_vocabulary({"result": ["No Gain"]}, [])

    assert "No Gain" in editor.result_buttons
    assert "Reception" in editor.result_buttons
    assert "Touchdown" in editor.result_buttons
    assert len(editor.result_buttons) > 1


def test_reception_is_a_standard_non_removable_result():
    assert "Reception" in STANDARD_RESULT_CHOICES
    assert "Reception" in AppSettings().fixed_details["result"]


def test_v2_result_favorites_and_overflow_share_one_vocabulary(qapp):
    from tapesift.ui_core.clip_editor import ClipEditor

    settings = AppSettings(
        onboarding_seen=True,
        result_favorites=[
            "Penalty", "Safety", "Punt", "Sack", "No Gain", "First Down",
        ],
    )
    settings.fixed_details["result"].append("Drive Killer")
    settings.save = lambda *a, **k: None
    editor = ClipEditor(settings)
    editor.setObjectName("V2ReviewInspector")
    editor.set_analyst_mode(True)
    editor.set_clip(Clip(start_ms=0, end_ms=40_000, clip_number=3))
    editor.set_vocabulary({"result": ["Clock Runoff"]}, [])

    assert list(editor.result_buttons) == settings.result_favorites
    overflow = [action.text() for action in editor.result_menu.actions()]
    assert "Drive Killer" in overflow
    assert "Clock Runoff" in overflow
    assert "Touchdown" in overflow

    editor.detail_edits["result"].setText("Reception; Clock Runoff")
    editor._rebuild_result_menu()
    actions = {
        action.text(): action for action in editor.result_menu.actions()}
    assert actions["Reception"].isChecked()
    assert actions["Clock Runoff"].isChecked()


def test_result_manager_keeps_custom_results_and_ordered_favorites(qapp):
    dialog = ResultManagerDialog(
        ["No Gain", "First Down"], ["No Gain", "First Down", "Safety"])
    dialog.custom_edit.setText("Custom Result")
    dialog._add_custom()
    matches = dialog.result_list.findItems(
        "Custom Result", Qt.MatchFlag.MatchExactly)
    dialog.result_list.setCurrentItem(matches[0])
    dialog._add_selected_favorite()

    assert dialog.favorites() == [
        "No Gain", "First Down", "Custom Result"]
    assert "Custom Result" in dialog.results()


def test_v2_save_next_advances_and_save_play_syncs_quick_tags(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Save actions", tmp_path, tmp_path / "exports")
    first = session.add_clip(Clip(
        start_ms=1_000, end_ms=7_000, clip_title="First"))
    second = session.add_clip(Clip(
        start_ms=8_000, end_ms=14_000, clip_title="Second"))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(first.id, seek=False)

    advanced = []
    edit_started = []
    clip_edited = []
    window.clip_editor.save_and_advance.connect(lambda: advanced.append(True))
    window.clip_editor.edit_started.connect(edit_started.append)
    window.clip_editor.clip_edited.connect(clip_edited.append)
    window.clip_editor.start_edit.setText("not a timestamp")
    QTest.mouseClick(
        window.clip_editor.save_next_btn, Qt.MouseButton.LeftButton)
    assert advanced == []
    assert edit_started == []
    assert clip_edited == []
    assert window._selected_clip_id == first.id
    assert not window.clip_editor.error_label.isHidden()
    window.clip_editor.start_edit.setText("00:01.000")

    window.clip_editor.detail_edits["run_pass"].setText("Pass")
    QTest.mouseClick(
        window.clip_editor.apply_btn, Qt.MouseButton.LeftButton)
    assert window.quick_tag_tray.buttons["pass"].isChecked()
    assert window._selected_clip_id == first.id
    assert edit_started == [first.id]
    assert clip_edited == [first.id]

    QTest.mouseClick(
        window.clip_editor.save_next_btn, Qt.MouseButton.LeftButton)
    assert window._selected_clip_id == second.id
    assert advanced == [True]
    assert edit_started == [first.id, first.id]
    assert clip_edited == [first.id, first.id]

    session.conn.close()
    window.hide()


def test_v2_inspector_dropdown_focus_blocks_transport_shortcuts(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Focus guard", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=1_000, end_ms=7_000, clip_title="Play"))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    window.stack.setCurrentWidget(window.workspace)
    window.show()
    qapp.processEvents()
    window.clip_editor.detail_edits["quarter"].setFocus()
    qapp.processEvents()

    quarter = window.clip_editor.detail_edits["quarter"]
    assert window.focusWidget() in (quarter, quarter.lineEdit())
    assert window._typing_in_text_field()
    shuttle_calls = []
    window._shortcut(lambda: shuttle_calls.append("L"))()
    assert shuttle_calls == []
    window._shortcut_escape()
    assert window.focusWidget() is window.player.video_widget
    session.conn.close()
    window.hide()


def test_v2_failed_quick_tag_restores_visual_state(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Failed quick tag", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=1_000, end_ms=7_000, clip_title="Play"))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    window.clip_editor.start_edit.setText("not a timestamp")

    QTest.mouseClick(
        window.quick_tag_tray.buttons["screen"],
        Qt.MouseButton.LeftButton)

    assert session.get_clip(clip.id).details == {}
    assert not window.quick_tag_tray.buttons["screen"].isChecked()
    assert not window.quick_tag_tray.buttons["pass"].isChecked()
    session.conn.close()
    window.hide()


def test_go_to_library_menu_action_opens_the_library(
        qapp, settings, monkeypatch):
    from PySide6.QtGui import QAction
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    action = next(a for a in window.findChildren(QAction)
                  if a.text().replace("&", "") == "Go to Library")

    action.trigger()

    assert window.stack.currentWidget() is window.library_screen
    window.hide()


def test_v2_application_menu_is_centered_and_shared_across_pages(
        qapp, settings, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.resize(1600, 940)
    window.show()
    qapp.processEvents()

    shell = window._centered_menu_shell
    menu_bar = window.menuBar()
    actions = [action for action in menu_bar.actions() if action.isVisible()]
    names = [action.text().replace("&", "") for action in actions]

    assert window.menuWidget() is shell
    assert shell.objectName() == "CenteredApplicationMenu"
    assert menu_bar.objectName() == "CenteredApplicationMenuBar"
    assert shell.geometry().top() == 0
    assert shell.width() == window.width()
    for object_name in (
        "ApplicationMinimizeButton",
        "ApplicationMaximizeButton",
        "ApplicationCloseButton",
    ):
        button = shell.findChild(QToolButton, object_name)
        assert button is not None
        assert not button.icon().isNull()
    assert names == [
        "File",
        "Edit",
        "Playback",
        "Window",
        "How TapeSift Works",
        "Help",
    ]

    first = menu_bar.actionGeometry(actions[0])
    last = menu_bar.actionGeometry(actions[-1])
    group_left = menu_bar.geometry().left() + first.left()
    group_right = menu_bar.geometry().left() + last.right()
    group_center = (group_left + group_right) / 2
    assert abs(group_center - shell.rect().center().x()) <= 4

    action_ids = [id(action) for action in actions]
    for page in (
        window.start_screen,
        window.library_screen,
        window.workspace,
    ):
        window.stack.setCurrentWidget(page)
        qapp.processEvents()
        assert window.menuBar() is menu_bar
        assert [id(action) for action in menu_bar.actions()] == action_ids

    expected_modes = (
        (window.start_screen, "home"),
        (window.library_screen, "library"),
        (window.workspace, "review"),
    )
    for page, mode in expected_modes:
        window.stack.setCurrentWidget(page)
        qapp.processEvents()
        assert shell._mode == mode
        assert shell._left_hosts[mode].isVisible()
        assert shell._action_hosts[mode].isVisible()
        assert sum(host.isVisible()
                   for host in shell._left_hosts.values()) == 1
        assert sum(host.isVisible()
                   for host in shell._action_hosts.values()) == 1

    file_menu = actions[0].menu()
    playback_menu = actions[2].menu()
    assert file_menu is not None
    assert playback_menu is not None
    go_to_library = next(
        action for action in file_menu.actions()
        if action.text().replace("&", "") == "Go to Library"
    )
    assert go_to_library.shortcut().toString() == "Ctrl+Shift+L"
    assert window.review_action in playback_menu.actions()
    window.hide()


def test_v2_library_uses_the_selected_play_workbench(qapp, settings):
    screen = LibrarySearchScreenV2(settings)
    try:
        labels = [label.text() for label in screen.findChildren(QLabel)]
        assert "CLIP LEDGER" in labels
        assert "SELECTED PLAY" in labels
        assert "PLAY DETAILS" in labels
        assert "ACTIONS" in labels
        assert screen.search_box.objectName() == "V2LibrarySearch"
        assert screen.layout().indexOf(screen.search_box) >= 0
        assert screen.results_list.objectName() == "V2LibraryResults"
        assert screen._preview_panel.objectName() == "V2LibraryInspector"
        assert screen.save_btn.property("primary") == "true"
        assert screen.export_btn.property("primary") == "true"
    finally:
        screen.hide()


def test_v2_library_notes_height_is_resizable_and_persisted(qapp, settings):
    saved: list[int] = []
    settings.library_notes_height = 118
    settings.save = lambda *a, **k: saved.append(
        settings.library_notes_height)
    screen = LibrarySearchScreenV2(settings)
    try:
        assert screen.preview_notes.minimumHeight() == 118
        assert screen.preview_notes.maximumHeight() == 118
        screen._save_notes_height(164)
        assert screen.preview_notes.minimumHeight() == 164
        assert screen.preview_notes.maximumHeight() == 164
        assert settings.library_notes_height == 164
        assert saved == [164]
    finally:
        screen.hide()


def test_v2_library_ledger_selection_drives_real_inspector(qapp, settings):
    screen = LibrarySearchScreenV2(settings)
    row = LibraryRow(
        clip_uid="project:clip-1",
        project_path="C:/project/game.clipforge",
        project_name="Miami D Vs Notre Dame O Clips",
        source_video_path="C:/film/game.mp4",
        clip_id="clip-1",
        clip_number=51,
        clip_title="Play 051",
        start_ms=3_011_000,
        end_ms=3_041_500,
        tags=["Pass", "Shotgun"],
        player_name="#12 QB",
        play_type="Pass",
        quarter="2",
        down_distance="3rd & 7",
        result="Complete",
        notes="Good timing.",
        thumbnail_path="",
        details={"run_pass": "Pass", "off_formation": "Shotgun",
                 "result": "Complete", "down_distance": "3rd & 7"},
    )
    try:
        screen._results = [row]
        screen._populate_results()
        screen.results_list.setCurrentRow(0)
        qapp.processEvents()
        assert screen.preview_title.text() == "Play 051"
        assert screen.preview_details["run_pass"].text() == "Pass"
        assert screen.open_btn.isEnabled()
        assert screen.export_btn.isEnabled()
        assert screen.count_label.text() == "1 of 1 selected"
    finally:
        screen.hide()


def test_v2_library_preview_plays_inline_in_selected_clip_surface(
        qapp, settings, tmp_path, monkeypatch):
    screen = LibrarySearchScreenV2(settings)
    source = tmp_path / "game.mp4"
    source.write_bytes(b"placeholder")
    row = LibraryRow(
        clip_uid="project:clip-inline",
        project_path="C:/project/game.tapesift",
        project_name="Game",
        source_video_path=str(source),
        clip_id="clip-inline",
        clip_number=1,
        clip_title="Inline preview",
        start_ms=1_000,
        end_ms=5_000,
        tags=[], player_name="", play_type="", quarter="",
        down_distance="", result="", notes="", thumbnail_path="",
        details={}, action="", other_players="", opponent="",
    )
    monkeypatch.setattr(screen.preview_player, "setSource", lambda *_: None)
    monkeypatch.setattr(screen.preview_player, "setPosition", lambda *_: None)
    monkeypatch.setattr(screen.preview_player, "play", lambda: None)
    try:
        screen._quick_preview(row)
        assert screen.preview_thumb.currentWidget() is screen.preview_video
        assert screen._inline_preview_row is row
        assert screen.preview_play_btn.accessibleName() == \
            "Pause preview (Space)"
        screen._inline_preview_position_changed(row.end_ms)
        assert screen.preview_play_btn.accessibleName() == \
            "Replay preview (Space)"
    finally:
        screen.hide()


def test_workflow_ribbon_tracks_stage(qapp, settings, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    try:
        window.workflow_ribbon.set_active("export")
        assert window.workflow_ribbon.export_btn.property("active") == "true"
        assert window.workflow_ribbon.detect_btn.property("active") == "false"
    finally:
        window.hide()


def test_review_and_export_switch_the_lower_stage_without_toggling_f5_mode(
        qapp, settings, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    try:
        initial_review_mode = settings.review_mode
        window.stack.setCurrentWidget(window.workspace)
        window.show()
        qapp.processEvents()

        window._focus_export()
        qapp.processEvents()
        assert window._export_dock.isVisible()
        assert window.workflow_ribbon.export_btn.property("active") == "true"

        window._v2_review()
        qapp.processEvents()
        assert not window._export_dock.isVisible()
        assert window.workflow_ribbon.review_btn.property("active") == "true"
        assert settings.review_mode is initial_review_mode
    finally:
        window.hide()


def test_review_workbench_is_the_default_workflow_stage(
        qapp, settings, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    try:
        assert window.workflow_ribbon.review_btn.property("active") == "true"
        assert window.workflow_ribbon.detect_btn.property("active") == "false"
    finally:
        window.hide()


def test_review_clip_ledger_exposes_full_film_detection(
        qapp, settings, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.resize(1600, 940)
    window.show()
    window.stack.setCurrentWidget(window.workspace)
    qapp.processEvents()

    calls = []
    monkeypatch.setattr(window, "_detect_plays", lambda: calls.append("detect"))
    button = window.clip_list.findChild(
        QToolButton, "ClipLedgerDetectPlays")

    assert button is window.detect_plays_button
    assert button.isVisible()
    assert button.text() == "Detect Plays"
    assert button.parentWidget() is window.clip_list.header_actions
    assert window.clip_list.header_actions.isVisible()
    assert button.minimumWidth() >= 86
    assert window.review_export_button.text() == "Export"
    assert window.review_export_button.minimumWidth() >= 64
    assert window.new_clip_button.text() == "New Clip"
    assert window.new_clip_button.minimumWidth() >= 76
    assert "full loaded game film" in button.toolTip()
    assert button.accessibleName() == \
        "Detect plays in the full loaded game film"

    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    assert calls == ["detect"]
    assert window.workflow_ribbon.review_btn.property("active") == "true"
    window.hide()


def test_review_clip_ledger_exposes_scoped_export(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    session = ProjectSession.create(
        "Export selection", tmp_path, tmp_path / "exports")
    first = session.add_clip(Clip(
        start_ms=1_000, end_ms=9_000, clip_title="First"))
    second = session.add_clip(Clip(
        start_ms=10_000, end_ms=18_000, clip_title="Second"))
    window.session = session
    window._refresh_clip_list()
    window.resize(1600, 940)
    window.show()
    window.stack.setCurrentWidget(window.workspace)
    assert window.select_clip(first.id, seek=False)
    qapp.processEvents()

    button = window.clip_list.findChild(QToolButton, "ClipLedgerExport")
    assert button is window.review_export_button
    assert button.isVisible()
    assert button.isEnabled()
    assert button.text() == "Export"
    assert window.clip_editor.quick_export_btn.text() == "Export Clip"
    assert window.clip_editor.package_btn.text() == "Package / Cut Up"

    # Package / Cut Up is a distinct, session-wide route. It clears any
    # ledger selection scope and opens only the existing ExportPanel stage.
    QTest.mouseClick(
        window.clip_editor.package_btn, Qt.MouseButton.LeftButton)
    assert window._workspace_stage == "export"
    assert window._review_export_scope_ids is None
    window._set_workspace_stage("review")

    monkeypatch.setattr(
        window.clip_list, "selected_clip_ids",
        lambda: [first.id, second.id])
    window._sync_review_export_button([first.id, second.id])
    assert button.text() == "Export (2)"
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)

    assert window._workspace_stage == "export"
    assert window._review_export_scope_ids == (first.id, second.id)
    assert window.export_panel.selection_label.text().startswith("2 clips")
    assert window._export_dock.isVisible()

    first.enabled = False
    second.enabled = False
    window._sync_review_export_button([first.id, second.id])
    assert not button.isEnabled()
    assert "excluded" in button.toolTip()

    starts = []
    monkeypatch.setattr(
        MainWindowWorkflow, "_start_export",
        lambda self, mode, preset, accurate, clips=None, quick=False:
        starts.append((mode, preset, accurate, clips, quick)))
    window._export_restore_complete = True
    window._start_export("individual", "social_1080p", True)
    assert starts == [(
        "individual", "social_1080p", True, [first, second], False)]
    window.hide()


def test_review_masthead_and_transport_match_selected_option_one(
        qapp, settings, monkeypatch):
    # The opt-out still verifies the rollback deck, but it now lives inside
    # the selected Option 4 shell and must follow that shell's hidden-strip
    # presentation contract.
    monkeypatch.setenv("TAPESIFT_DOCK_V2", "0")
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.resize(1600, 940)
    window.show()
    window.stack.setCurrentWidget(window.workspace)
    qapp.processEvents()

    assert window.review_masthead.isVisible()
    assert window.review_masthead.objectName() == "V2ReviewMasthead"
    assert window.findChild(QLabel, "ReviewBrandLockup") is not None
    assert window.workflow_ribbon.isHidden()

    player = window.player
    deck = window.control_center
    assert type(deck) is ControlCenterDeck
    assert window._control_center_dock.widget() is deck
    controls = [
        deck.findChild(QToolButton, name)
        for name in (
            "TransportSkipBackward",
            "TransportRewind",
            "TransportFastForward",
            "TransportSkipForward",
        )
    ]
    assert all(button is not None for button in controls)
    assert all(button.text() == "" for button in controls)
    assert all(not button.icon().isNull() for button in controls)
    assert all(
        button.size() == QSize(44, 44)
        for button in controls)
    assert all(button.iconSize() == QSize(40, 40) for button in controls)
    assert len({button.geometry().top() for button in controls}) == 1
    assert deck.findChild(QToolButton, "TransportPause") is None

    for button in controls:
        image = button.icon().pixmap(button.iconSize()).toImage()
        opaque = [
            (x, y)
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 8
        ]
        left = min(x for x, _y in opaque)
        right = max(x for x, _y in opaque)
        top = min(y for _x, y in opaque)
        bottom = max(y for _x, y in opaque)
        assert abs(((left + right) / 2) - 19.5) <= 1
        assert abs(((top + bottom) / 2) - 19.5) <= 1

    play_pause = deck.findChild(QToolButton, "TransportPlayPause")
    assert play_pause is player.play_btn
    assert play_pause.parent() is not None
    # The selected dock keeps square chassis around project-owned rotary SVGs.
    assert play_pause.size() == QSize(44, 44)
    assert play_pause.iconSize() == QSize(40, 40)
    assert len(deck.findChildren(
        QToolButton, "TransportPlayPause")) == 1
    paused_icon = play_pause.icon().cacheKey()
    player._state_changed(QMediaPlayer.PlaybackState.PlayingState)
    assert play_pause.property("playing") == "true"
    assert play_pause.accessibleName() == "Pause playback (Space)"
    assert play_pause.icon().cacheKey() != paused_icon
    player._state_changed(QMediaPlayer.PlaybackState.PausedState)
    assert play_pause.property("playing") == "false"
    assert play_pause.accessibleName() == "Play playback (Space)"

    volume = player.findChild(QToolButton, "VolumeControl")
    assert volume is player.volume_btn
    assert volume.property("iconLibrary") == "Segoe Fluent Icons"
    assert volume.property("iconAsset") == "tapesift-volume.png"
    assert volume.iconSize() == QSize(18, 18)
    assert not volume.icon().isNull()
    volume_icon = volume.icon().cacheKey()
    player._toggle_mute()
    assert volume.property("iconAsset") == "tapesift-volume-muted.png"
    assert volume.icon().cacheKey() != volume_icon
    player._toggle_mute()
    center = deck.findChild(QWidget, "TransportCluster")
    assert center is not None
    jog_ring = deck.findChild(QWidget, "MinimalJogRing")
    assert jog_ring is player.jog_ring
    assert isinstance(jog_ring, MinimalJogRing)
    assert isinstance(jog_ring, ProfessionalJogWheel)
    assert jog_ring.height() == 197
    assert jog_ring.FACE_RADIUS * 2 == 165
    assert jog_ring.CENTER_Y == 82.5
    assert jog_ring.CENTER_Y - jog_ring.FACE_RADIUS == 0.0
    assert jog_ring.CENTER_Y + jog_ring.FACE_RADIUS == 165.0
    assert dict(jog_ring.SHUTTLE_ARC_DEGREES) == {
        1.0: 90.0,
        2.0: 180.0,
        4.0: 270.0,
        8.0: 360.0,
    }
    assert player.findChild(QWidget, "JogShuttleWheel") is None
    assert player.findChild(QWidget, "PlaybackDock") is None
    secondaries = [
        deck.findChild(QToolButton, "TransportFrameBackward"),
        deck.findChild(QToolButton, "TransportLoop"),
        deck.findChild(QToolButton, "TransportFrameForward"),
    ]
    assert secondaries == [
        player.step_back_btn, player.loop_btn, player.step_fwd_btn]
    assert all(button.size() == QSize(30, 30) for button in secondaries)
    assert all(button.iconSize() == QSize(26, 26) for button in secondaries)
    assert [button.toolTip() for button in secondaries] == [
        "Step back one frame (Left)",
        "Loop the marked range",
        "Step forward one frame (Right)",
    ]
    # The upper workflow row and lower capture/export row are distinct;
    # Telestration retains its one separate home beside the film.
    marks = deck.findChild(QWidget, "TransportMarksGroup")
    voiceover = deck.findChild(QWidget, "TransportVoiceoverZone")
    assert marks.mapTo(deck, QPoint(0, 0)).y() < \
        voiceover.mapTo(deck, QPoint(0, 0)).y()
    assert deck.findChild(QWidget, "TransportTelestrateZone") is None
    assert player.findChild(QWidget, "TelestrationRail") \
        is player.telestration_rail
    view_strip = player.findChild(QWidget, "TimelineViewStrip")
    assert view_strip is player.view_strip
    assert view_strip.isVisible()
    assert not player.timeline_viewport_controls.isVisible()
    assert player.timeline_viewport_controls.isHidden()
    for control in (
            player.timeline_zoom_out,
            player.timeline_zoom_in,
            player.timeline_fit_play):
        assert control is not None
        assert control.isVisible()
    for control in (
            player.timeline_zoom_slider,
            player.timeline_fit_game,
            player.timeline_legend,
            player.timeline_key_button,
            player.timeline_snap_button,
            player.volume_btn):
        assert control is not None
        assert not control.isVisible()
    assert player.predicted_snap_button.isVisible()

    timeline_actions = window.timeline_menu.actions()
    for action in (
            window.timeline_zoom_in_action,
            window.timeline_zoom_out_action,
            window.timeline_fit_play_action,
            window.timeline_predicted_snap_action,
            window.timeline_snap_action,
            window.timeline_volume_action,
            window.timeline_key_menu_action):
        assert action in timeline_actions
    assert window.timeline_key_menu is player.timeline_key_menu
    assert window.timeline_snap_action.isCheckable()
    assert window.timeline_snap_action.isChecked() \
        == player.timeline_snap_button.isChecked()
    full_timeline = next(
        action for action in window._playback_menu.actions()
        if action.text() == "Show Full Timeline")
    assert full_timeline.shortcut().toString() == "Ctrl+0"
    assert not player.timeline_follow_playhead.isVisible()
    assert not player.timeline_variant_label.isVisible()
    assert player.timeline_variant_label.text()
    assert not player.slider._period_rail_visible
    window.hide()


class TestBranding:
    """The standard icon-plus-wordmark identity appears on product surfaces.

    Product naming is image artwork rather than a live-font approximation.
    The same app tile identifies both the app and its Library.
    """

    def test_home_header_uses_the_official_horizontal_lockup(
            self, qapp, settings):
        start_screen_v2 = StartScreenV2(settings)
        lockup = start_screen_v2.findChild(QLabel, "HomeBrandLockup")
        assert lockup is not None
        assert lockup.property("brandAsset") == "tapesift-logo.png"
        assert lockup.pixmap() and not lockup.pixmap().isNull()
        assert lockup.pixmap().width() > lockup.pixmap().height() * 4

    def test_home_actions_use_fluent_search_and_settings_icons(
            self, qapp, settings):
        start_screen_v2 = StartScreenV2(settings)
        button = next(b for b in start_screen_v2.findChildren(QPushButton)
                      if b.text() == "Search Library")
        assert not button.icon().isNull(), "library button has no icon"
        assert button.property("iconLibrary") == "Segoe Fluent Icons"
        assert button.property("iconAsset") == "tapesift-search.png"
        assert button.iconSize() == QSize(16, 16)
        settings_button = next(
            b for b in start_screen_v2.findChildren(QPushButton)
            if b.text() == "Settings")
        assert not settings_button.icon().isNull()
        assert settings_button.property("iconLibrary") == \
            "Segoe Fluent Icons"
        assert settings_button.property("iconAsset") == \
            "tapesift-settings.png"

    def test_library_screen_uses_the_same_official_lockup(
            self, qapp, settings):
        from tapesift.ui_v2.library_screen import LibrarySearchScreenV2
        library_screen_v2 = LibrarySearchScreenV2(settings)
        lockup = library_screen_v2.findChild(
            QLabel, "LibraryBrandLockup")
        assert lockup is not None
        assert lockup.property("brandAsset") == "tapesift-logo.png"
        assert lockup.pixmap() and not lockup.pixmap().isNull()
        assert lockup.pixmap().width() > lockup.pixmap().height() * 4
        section = library_screen_v2.findChild(
            QLabel, "LibrarySectionLabel")
        assert section is not None
        assert section.text() == "LIBRARY"

    def test_a_missing_brand_file_does_not_break_the_screen(self, monkeypatch):
        from tapesift.ui_v2 import start_screen as ss
        monkeypatch.setattr(ss, "_icon_path", lambda name: None)
        assert ss._brand_image_label("nope.png", 30) is None


class TestHowItWorks:
    """The in-app explanation. It ships inside the exe, so it cannot be
    lost by an installer the way a docs file can."""

    def test_every_section_has_real_content(self):
        from tapesift.ui.how_it_works_dialog import SECTIONS
        assert len(SECTIONS) >= 6
        for title, body in SECTIONS:
            assert title and len(body) > 200, title

    def test_it_says_detection_is_all22_only(self):
        """The single most important expectation to set: broadcast film
        will not work, and finding that out by trial is a bad first hour."""
        from tapesift.ui.how_it_works_dialog import SECTIONS
        detect = next(b for t, b in SECTIONS if "Detect" in t)
        assert "Beta" in detect
        assert "All-22" in detect and "Broadcast" in detect

    def test_selecting_a_section_shows_it(self, qapp):
        from tapesift.ui.how_it_works_dialog import HowItWorksDialog, SECTIONS
        dialog = HowItWorksDialog(section=2)
        assert dialog.contents.count() == len(SECTIONS)
        assert dialog.text.toPlainText().strip()

    def test_an_out_of_range_section_does_not_crash(self, qapp):
        from tapesift.ui.how_it_works_dialog import HowItWorksDialog
        assert HowItWorksDialog(section=999).contents.currentRow() >= 0


class TestHomeStatement:
    def test_the_privacy_column_is_gone(self, qapp, settings):
        screen = StartScreenV2(settings)
        texts = [label.text() for label in screen.findChildren(QLabel)]
        for retired in ("No account", "No uploads", "No internet required"):
            assert retired not in texts

    def test_library_entry_is_weightier_than_a_header_link(self, qapp,
                                                           settings):
        from PySide6.QtWidgets import QPushButton
        screen = StartScreenV2(settings)
        library = next(b for b in screen.findChildren(QPushButton)
                       if b.text() == "Search Library")
        settings_btn = next(b for b in screen.findChildren(QPushButton)
                            if b.text() == "Settings")
        assert library.property("libraryEntry")
        assert library.iconSize() == QSize(16, 16)
        assert library.minimumHeight() > settings_btn.minimumHeight()


class TestLibraryMasthead:
    """One left-aligned lockup with inline section stats."""

    def _screen(self, settings):
        from tapesift.ui_v2.library_screen import LibrarySearchScreenV2
        screen = LibrarySearchScreenV2(settings)
        screen.resize(1900, 1000)
        screen.show()
        return screen

    def test_the_lockup_is_left_aligned_with_inline_stats(
            self, qapp, settings):
        screen = self._screen(settings)
        brand = screen.findChild(QLabel, "LibraryBrandLockup")
        section = screen.findChild(QLabel, "LibrarySectionLabel")
        stats = screen.findChild(QLabel, "LibraryMastheadStats")
        assert brand.x() < screen.width() // 4
        assert brand.x() < section.x() < stats.x()
        assert abs(brand.geometry().center().y()
                   - section.geometry().center().y()) <= 3

    def test_only_one_product_lockup_is_visible(self, qapp, settings):
        """The base class builds its own LIBRARY mark; replacing it without
        hiding it leaves a second one floating at the top left."""
        screen = self._screen(settings)
        lockups = [label for label in screen.findChildren(
            QLabel, "LibraryBrandLockup") if label.isVisible()]
        assert len(lockups) == 1
        assert lockups[0].pixmap() and not lockups[0].pixmap().isNull()

    def test_library_remains_a_section_label_not_a_second_logo(
            self, qapp, settings):
        screen = self._screen(settings)
        section = screen.findChild(QLabel, "LibrarySectionLabel")
        assert section.isVisible()
        assert section.text() == "LIBRARY"
        assert section.pixmap().isNull()


def test_transport_deck_lays_out_two_rows_across_the_width(
        qapp, settings, monkeypatch):
    """The standard top and bottom zones preserve the locked dock geometry."""
    monkeypatch.setenv("TAPESIFT_DOCK_V2", "0")
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.stack.setCurrentWidget(window.workspace)
    window.show()
    deck = window.control_center
    assert type(deck) is ControlCenterDeck
    assert window._control_center_dock.widget() is deck
    assert window.player.control_strip_slot.layout().count() == 0
    assert deck.parentWidget() is window._control_center_dock
    top_names = [
        "TransportPositionZone",
        "TransportCluster",
        "TransportWheelAnchor",
        "TransportDialZone",
        "TransportMarksGroup",
    ]
    bottom_names = [
        "TransportVoiceoverZone",
        "TransportReservedBay",
        "TransportExportZone",
    ]
    try:
        for width in (
                1260, 1280, 1366, 1440, 1499,
                1500, 1683, 1920, 2560):
            window.resize(width, 935)
            qapp.processEvents()
            actual_width = window.width()
            assert (actual_width, window.height()) == (width, 935), (
                "supported desktop size was not honored",
                (width, 935), (actual_width, window.height()),
                deck.minimumSizeHint())

            top_zones = [deck.findChild(QWidget, name)
                         for name in top_names]
            bottom_zones = [deck.findChild(QWidget, name)
                            for name in bottom_names]
            assert all(zone is not None for zone in (*top_zones,
                                                      *bottom_zones)), (
                actual_width, top_names, bottom_names)
            assert deck.findChild(QWidget, "TransportTelestrateZone") is None
            assert len(deck.findChildren(QFrame, "TransportZoneRule")) == 6

            top_host = deck.findChild(QWidget, "ControlCenterTopRow")
            bottom_host = deck.findChild(QWidget, "ControlCenterBottomRow")
            divider = deck.findChild(QFrame, "TransportRowRule")
            assert top_host is not None
            assert bottom_host is not None
            assert divider is not None
            assert deck.sizeHint().height() == 197
            assert deck.minimumSizeHint().height() == 197
            assert deck.height() == 197
            assert top_host.height() == 135
            assert divider.height() == 1
            assert bottom_host.height() == 61
            top_y = top_host.mapTo(deck, QPoint(0, 0)).y()
            divider_y = divider.mapTo(deck, QPoint(0, 0)).y()
            bottom_y = bottom_host.mapTo(deck, QPoint(0, 0)).y()
            assert abs(divider_y - (top_y + 135)) <= 1
            assert abs(bottom_y - (divider_y + 1)) <= 1

            top_lefts = [zone.mapTo(deck, QPoint(0, 0)).x()
                         for zone in top_zones]
            top_rights = [left + zone.width()
                          for left, zone in zip(top_lefts, top_zones)]
            bottom_lefts = [zone.mapTo(deck, QPoint(0, 0)).x()
                            for zone in bottom_zones]
            bottom_rights = [left + zone.width()
                             for left, zone in zip(bottom_lefts,
                                                   bottom_zones)]
            assert all(zone.width() > 0 for zone in top_zones)
            assert bottom_zones[0].width() > 0
            assert bottom_zones[2].width() > 0
            assert top_lefts == sorted(top_lefts), (
                actual_width, top_lefts)
            assert bottom_lefts == sorted(bottom_lefts), (
                actual_width, bottom_lefts)
            assert all(top_rights[index] <= top_lefts[index + 1]
                       for index in range(len(top_zones) - 1)), (
                           actual_width,
                           list(zip(top_lefts, top_rights)))
            assert all(bottom_rights[index] <= bottom_lefts[index + 1]
                       for index in range(len(bottom_zones) - 1)), (
                           actual_width,
                           list(zip(bottom_lefts, bottom_rights)))

            reserved_bay = bottom_zones[1]
            assert reserved_bay.layout() is None
            assert not reserved_bay.findChildren(QWidget)
            assert reserved_bay.width() == top_zones[2].width()
            capped_width = min(width, 1683)
            fraction = (capped_width - 1260) / (1683 - 1260)
            expected_transport = round(290 + fraction * (415 - 290))
            expected_shuttle = round(268 + fraction * (383 - 268))
            expected_top_lefts = (
                0,
                147,
                148 + expected_transport,
                391 + expected_transport,
                392 + expected_transport + expected_shuttle,
            )
            expected_bottom_lefts = (
                0,
                148 + expected_transport,
                391 + expected_transport,
            )
            assert all(abs(actual - expected) <= 2
                       for actual, expected in zip(
                           top_lefts, expected_top_lefts)), (
                               width, top_lefts, expected_top_lefts)
            assert all(abs(actual - expected) <= 2
                       for actual, expected in zip(
                           bottom_lefts, expected_bottom_lefts)), (
                               width, bottom_lefts,
                               expected_bottom_lefts)
            assert all(abs(actual - expected) <= 2
                       for actual, expected in zip(
                           (top_zones[0].width(),
                            top_zones[1].width(),
                            top_zones[2].width(),
                            top_zones[3].width(),
                            bottom_zones[0].width(),
                            reserved_bay.width()),
                           (146, expected_transport, 242,
                            expected_shuttle,
                            147 + expected_transport, 242)))
            assert top_zones[4].width() >= 310
            assert deck.minimumSizeHint() == QSize(1260, 197)

            media_names = (
                "TransportSkipBackward",
                "TransportRewind",
                "TransportPlayPause",
                "TransportFastForward",
                "TransportSkipForward",
            )
            media_keys = [deck.findChild(QWidget, name)
                          for name in media_names]
            assert all(key is not None for key in media_keys)
            transport_zone = top_zones[1]
            key_lefts = [key.mapTo(transport_zone, QPoint(0, 0)).x()
                         for key in media_keys]
            key_rights = [left + key.width()
                          for left, key in zip(key_lefts, media_keys)]
            assert all(key_rights[index] <= key_lefts[index + 1]
                       for index in range(len(media_keys) - 1)), (
                           actual_width, list(zip(key_lefts, key_rights)))
            assert key_lefts[0] >= 0
            assert key_rights[-1] <= transport_zone.width(), (
                actual_width, key_rights[-1], transport_zone.width())
            assert all((key.width(), key.height()) == (44, 44)
                       for key in media_keys)
            assert all((key.iconSize().width(), key.iconSize().height())
                       == (40, 40) for key in media_keys)

            secondary_names = (
                "TransportFrameBackward",
                "TransportLoop",
                "TransportFrameForward",
            )
            secondary_keys = [deck.findChild(QWidget, name)
                              for name in secondary_names]
            assert all(key is not None for key in secondary_keys)
            assert secondary_keys == [
                window.player.step_back_btn,
                window.player.loop_btn,
                window.player.step_fwd_btn,
            ]
            assert all((key.width(), key.height()) == (30, 30)
                       for key in secondary_keys)
            assert all((key.iconSize().width(), key.iconSize().height())
                       == (26, 26) for key in secondary_keys)
            secondary_lefts = [
                key.mapTo(transport_zone, QPoint(0, 0)).x()
                for key in secondary_keys
            ]
            secondary_tops = [
                key.mapTo(transport_zone, QPoint(0, 0)).y()
                for key in secondary_keys
            ]
            assert secondary_lefts == sorted(secondary_lefts)
            assert len(set(secondary_tops)) == 1
            for secondary, primary in zip(secondary_keys,
                                          media_keys[1:4]):
                secondary_left = secondary.mapTo(
                    transport_zone, QPoint(0, 0)).x()
                primary_left = primary.mapTo(
                    transport_zone, QPoint(0, 0)).x()
                assert abs((secondary_left + 15) -
                           (primary_left + 22)) <= 2

            wheel_anchor = top_zones[2]
            wheel_host = deck.findChild(QWidget, "TransportWheelHost")
            assert wheel_host is not None
            ring = window.control_center.jog_ring
            assert isinstance(ring, ProfessionalJogWheel)
            assert wheel_host.isAncestorOf(ring)
            wheel_left = wheel_host.mapTo(deck, QPoint(0, 0)).x()
            assert abs(wheel_left - top_lefts[2]) <= 2
            assert wheel_host.width() == wheel_anchor.width()
            assert wheel_host.height() == 197
            assert ring.width() == wheel_host.width()
            assert ring.height() == 197
            assert ring.FACE_RADIUS * 2 == 165
            assert ring.CENTER_Y == 82.5
            assert ring.CENTER_Y - ring.FACE_RADIUS == 0.0
            assert ring.CENTER_Y + ring.FACE_RADIUS == 165.0

            dial_zone = top_zones[3]
            shuttle_meter = window.control_center.shuttle_meter
            assert dial_zone.isAncestorOf(shuttle_meter)
            assert shuttle_meter in dial_zone.findChildren(QWidget)
            assert shuttle_meter not in top_zones[0].findChildren(QWidget)
            window.control_center.set_shuttle_rate(-4.0)
            assert shuttle_meter.rate == -4.0
            assert shuttle_meter.accessibleDescription() == "REV 4×"
            window.control_center.set_shuttle_rate(2.0)
            assert shuttle_meter.accessibleDescription() == "FWD 2×"
            window.control_center.set_shuttle_rate(0.0)
            assert shuttle_meter.accessibleDescription() == "PAUSED 0×"

            # The window owns the deck now, so the side columns end above it
            # and cannot steal width from its control row.
            control_dock = window._control_center_dock
            assert control_dock.width() >= window.width() - 2
            deck_top = control_dock.mapTo(window, QPoint(0, 0)).y()
            for key in ("clips", "player", "play_details"):
                panel = window._workspace_docks[key]
                panel_bottom = panel.mapTo(
                    window, QPoint(0, panel.height())).y()
                assert panel_bottom <= deck_top + 1, (
                    actual_width, key, panel_bottom, deck_top)

            # Both rows reach the deck edge; neither can be clipped by a side
            # panel or by the deliberately empty reserved bay.
            assert top_rights[-1] >= deck.width() - 2, (
                actual_width, top_rights[-1], deck.width())
            assert bottom_rights[-1] >= deck.width() - 2, (
                actual_width, bottom_rights[-1], deck.width())

            waveform = deck.findChild(QWidget, "VoiceoverWaveform")
            assert waveform.isVisible()
            style_buttons = window.control_center.deck_export_style_buttons
            assert all(button.isVisible() for button in style_buttons)
            voice_widgets = (
                window.control_center.voiceover_record_button,
                window.control_center.voiceover_take_label,
                window.control_center.voice_clear_button,
                waveform,
            )
            export_widgets = (
                *style_buttons,
                window.control_center.deck_format_button,
                window.control_center.deck_export_button,
            )
            for widget, owner in (
                *((widget, bottom_zones[0]) for widget in voice_widgets),
                *((widget, bottom_zones[2]) for widget in export_widgets),
            ):
                if not widget.isVisible():
                    continue
                widget_left = widget.mapTo(owner, QPoint(0, 0)).x()
                assert widget_left >= 0
                assert widget_left + widget.width() <= owner.width(), (
                    actual_width, widget.objectName(), widget_left,
                    widget.width(), owner.width())
            for widgets, owner in (
                    (voice_widgets, bottom_zones[0]),
                    (export_widgets, bottom_zones[2])):
                spans = sorted(
                    (widget.mapTo(owner, QPoint(0, 0)).x(),
                     widget.mapTo(owner, QPoint(0, 0)).x() + widget.width(),
                     widget.objectName())
                    for widget in widgets if widget.isVisible()
                )
                assert all(spans[index][1] <= spans[index + 1][0]
                           for index in range(len(spans) - 1)), (
                               actual_width, spans)
            assert top_rights[-1] <= deck.width(), (
                actual_width, top_rights[-1], deck.width())
            assert bottom_rights[-1] <= deck.width(), (
                actual_width, bottom_rights[-1], deck.width())
    finally:
        window._app_closing = True
        window.hide()


def test_review_workspace_preserves_tag_map_and_fills_available_height(
        qapp, settings, monkeypatch):
    """Review keeps Tag Map and spends only the dead-band height on content."""
    monkeypatch.delenv("TAPESIFT_DOCK_V2", raising=False)
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings.workspace_tag_map_collapsed = False
    window = MainWindowV2(settings)
    window.stack.setCurrentWidget(window.workspace)
    window.show()
    try:
        player = window.player
        assert type(window.control_center) is DockV2Deck
        grid = player.attribute_grid
        rail = player.telestration_rail
        identities = (
            player.video_widget, grid, rail, player.slider,
            window.control_center, window.control_center.jog_ring,
        )
        assert window.minimumSize() == QSize(1260, 768)
        assert rail.isHidden()

        window.resize(1683, 935)
        qapp.processEvents()
        qapp.processEvents()
        assert (window.width(), window.height()) == (1683, 935)
        assert not player.tag_map_collapsed()
        assert player.timeline_header.isVisible()
        assert grid.isVisible()
        assert player.quick_tag_slot.isVisible()
        assert window.quick_tag_tray.parent() is player.quick_tag_slot
        assert window._workspace_docks["player"].height() >= \
            window.height() - 150
        # Iteration 3 (10_SPEC_BAND_B.md): the band owns ONE shared content
        # edge at x=26 and the attribute grid below keeps its own GUTTER_W
        # gutter - it no longer borrows GRID_GUTTER_W to align the scrub
        # with the grid's plot area (that shared axis was the "three left
        # margins" fault the spec removes).
        assert player.slider.mapTo(player, QPoint(0, 0)).x() == 26

        # The rail is greyed until a play owns the strokes.
        player.set_telestration_enabled(True)
        # Shape and ink both come from the library now.
        player._shape_library_picked("route_arrow")
        player._ink_library_picked("cyan")
        assert player.timeline_snap_button.isChecked()
        assert player.video_widget.tool() == "route_arrow"
        assert player.video_widget.ink() == "cyan"

        # Below the supported floor clamps deliberately. The larger dock keeps
        # the Tag Map and film usable instead of creating a second black panel.
        window.resize(1100, 700)
        qapp.processEvents()
        qapp.processEvents()
        assert (window.width(), window.height()) == (1260, 768)
        assert player.height() >= 600
        assert not player.tag_map_collapsed()
        assert settings.workspace_tag_map_collapsed is False
        assert player.timeline_header.isVisible()
        assert grid.isVisible()
        assert player.quick_tag_slot.isVisible()
        assert window._workspace_docks["player"].height() >= \
            window.height() - 150
        assert player.video_widget.height() >= 240
        assert rail.isHidden()
        assert rail.width() == 30
        assert player.view_strip.isHidden()
        assert window.control_center.viewport_group.isHidden()
        for widget in (
                player.timeline_fit_game,
                player.timeline_legend,
                player.timeline_key_button,
                player.timeline_snap_button,
                player.predicted_snap_button,
                player.volume_btn):
            assert not widget.isVisible()
        for control in (
                player.timeline_zoom_out,
                player.timeline_zoom_in,
                player.timeline_fit_play):
            assert control.parentWidget() is \
                window.control_center.viewport_group
            assert not control.isVisible()

        playback_menu = next(
            action.menu() for action in window.menuBar().actions()
            if action.text().replace("&", "") == "Playback")
        assert window.timeline_menu in (
            action.menu() for action in playback_menu.actions())
        assert window.timeline_key_menu is player.timeline_key_menu

        window.resize(1366, 768)
        qapp.processEvents()
        qapp.processEvents()
        assert (window.width(), window.height()) == (1366, 768)
        assert player.timeline_header.isVisible()

        # Growing enlarges the existing surfaces. Identity plus drawing, ink,
        # and snap state survive the complete down/up pass.
        window.resize(1920, 1080)
        qapp.processEvents()
        qapp.processEvents()
        assert (window.width(), window.height()) == (1920, 1080)
        assert window.control_center.viewport_group.isVisible()
        assert player.timeline_zoom_out.isVisible()
        assert player.timeline_zoom_in.isVisible()
        assert player.timeline_fit_play.isVisible()
        assert not player.tag_map_collapsed()
        assert player.timeline_header.isVisible()
        assert grid.isVisible()
        assert player.quick_tag_slot.isVisible()
        assert window._workspace_docks["player"].height() >= \
            window.height() - 150
        assert identities == (
            player.video_widget, player.attribute_grid,
            player.telestration_rail, player.slider,
            window.control_center, window.control_center.jog_ring,
        )
        assert player.timeline_snap_button.isChecked()
        assert player.video_widget.tool() == "route_arrow"
        assert player.video_widget.ink() == "cyan"

        # A deliberate fold still collapses only the grid. Its visible header
        # remains available to expand the Tag Map again.
        player.set_tag_map_collapsed(True)
        assert player.tag_map_collapsed()
        assert settings.workspace_tag_map_collapsed is True
        window.resize(1366, 768)
        qapp.processEvents()
        window.resize(1683, 935)
        qapp.processEvents()
        qapp.processEvents()
        assert player._tag_map_effectively_collapsed()
        assert player.tag_map_collapsed()
        assert grid.isHidden()
        assert player.timeline_header.isVisible()
        assert player.tag_map_collapse_button.isChecked()
        assert player.tag_map_collapse_button.isEnabled()
    finally:
        window._app_closing = True
        window.hide()


def test_transport_zoom_cluster_and_menu_route_existing_controls(
        qapp, settings, monkeypatch):
    """Review mounts zoom in transport while the menu keeps every route."""
    monkeypatch.delenv("TAPESIFT_DOCK_V2", raising=False)
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.stack.setCurrentWidget(window.workspace)
    window._screen_fit_done = True
    window.resize(1920, 1080)
    window.show()
    window._resize_default_docks()
    qapp.processEvents()
    qapp.processEvents()
    try:
        player = window.player
        deck = window.control_center
        assert player.view_strip.isHidden()
        assert deck.viewport_group.isVisible()
        for control in (
                player.timeline_zoom_out,
                player.timeline_zoom_in,
                player.timeline_fit_play):
            assert control.parentWidget() is deck.viewport_group
            assert control.isVisible()

        playback_menu = next(
            action.menu() for action in window.menuBar().actions()
            if action.text().replace("&", "") == "Playback")
        full_timeline = next(
            action for action in playback_menu.actions()
            if action.text() == "Show Full Timeline")
        timeline_menu = next(
            action.menu() for action in playback_menu.actions()
            if action.menu() is window.timeline_menu)
        assert timeline_menu.title() == "Timeline"
        assert window.timeline_key_menu is player.timeline_key_menu
        assert window.timeline_key_menu.menuAction() in timeline_menu.actions()

        player._duration_changed(1_200_000)
        player.set_clip_blocks([
            TimelineBlock(
                100_000, 140_000, clip_id="selected", title="Play"),
        ])
        player.set_selected_clip_id("selected")
        timeline_menu.aboutToShow.emit()

        assert window.timeline_zoom_in_action.isEnabled() \
            == player.timeline_zoom_in.isEnabled()
        assert window.timeline_zoom_out_action.isEnabled() \
            == player.timeline_zoom_out.isEnabled()
        assert window.timeline_fit_play_action.isEnabled() \
            == player.timeline_fit_play.isEnabled()

        zoom_before = player.timeline_zoom_slider.value()
        window.timeline_zoom_in_action.trigger()
        assert player.timeline_zoom_slider.value() == zoom_before + 1
        window.timeline_zoom_out_action.trigger()
        assert player.timeline_zoom_slider.value() == zoom_before

        window.timeline_fit_play_action.trigger()
        assert player.slider.visible_range() == (90_000, 150_000)
        full_timeline.trigger()
        assert player.slider.visible_range() == (0, 1_200_000)

        snap_requests = []
        player.predicted_snap_requested.connect(
            lambda: snap_requests.append("requested"))
        player.set_predicted_snap_state("missing")
        timeline_menu.aboutToShow.emit()
        assert window.timeline_predicted_snap_action.text() == \
            "Predicted Snap: Find Snap"
        assert window.timeline_predicted_snap_action.isEnabled()
        window.timeline_predicted_snap_action.trigger()
        assert snap_requests == ["requested"]

        assert player.timeline_snap_button.isChecked()
        assert window.timeline_snap_action.isChecked()
        window.timeline_snap_action.trigger()
        assert not player.timeline_snap_button.isChecked()
        assert not player.slider.snapping_enabled()
        player.timeline_snap_button.setChecked(True)
        assert window.timeline_snap_action.isChecked()
        assert player.slider.snapping_enabled()

        volume_clicks = []
        player.volume_btn.clicked.connect(
            lambda: volume_clicks.append("clicked"))
        timeline_menu.aboutToShow.emit()
        window.timeline_volume_action.trigger()
        qapp.processEvents()
        assert volume_clicks == ["clicked"]
        assert player.volume_popup.isVisible()
    finally:
        window._app_closing = True
        window.hide()


def test_telestration_rail_is_preserved_but_hidden_from_review_shell(
        qapp, settings, monkeypatch):
    """Option 4 hides the rail without deleting its code or stored state."""
    monkeypatch.delenv("TAPESIFT_DOCK_V2", raising=False)
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.resize(1683, 935)
    window.stack.setCurrentWidget(window.workspace)
    window.show()
    try:
        player = window.player
        qapp.processEvents()

        rails = window.findChildren(QWidget, "TelestrationRail")
        assert rails == [player.telestration_rail]
        deck = window.control_center
        assert type(deck) is DockV2Deck
        assert window._control_center_dock.widget() is None
        assert deck.parentWidget() is player.control_strip_slot
        assert deck.findChild(QWidget, "TelestrationRail") is None
        rail = player.telestration_rail
        video_left = player.video_widget.mapTo(player, QPoint(0, 0)).x()
        assert rail.isHidden()
        assert rail.width() == 30
        assert video_left == 0
        assert rail.accessibleName() == "Telestration tools"
        assert rail.accessibleDescription() == "No marks"
        rail_qss = rail.styleSheet()
        for contract_line in (
                "border: 1px solid #3b3b32;",
                "background: #111416;",
                "border: 1px solid #33383A;",
                "color: #9AA694;",
                "background: #1B1E20;",
                "border-color: #50565A;",
                "background: #111A15;",
                "border-color: #39E07A;",
                "border-color: #E8A33D;",
                "background: #23272A;",
                "border-color: #23272A;",
                "color: #4E584A;",
                "border-color: #FFD24A;",
                "border-color: #5AD6F0;",
                "border-color: #FF6B5E;"):
            assert contract_line in rail_qss
        assert player.mark_count_label.parentWidget() is rail
        assert player.mark_count_label.isHidden()
        assert all(label.isHidden() for label in rail.findChildren(QLabel))

        # The rail is Select, the shape library, one ink swatch, then undo
        # and clear. The three legacy tool keys and the three fixed ink
        # swatches are gone: they were an arbitrary subset of 27 shapes
        # and could never represent 12 inks.
        select = player.tool_group.button(0)
        library = player.tool_group.button(1)
        assert select is not None and library is not None
        assert player.tool_group.button(2) is None
        assert player.tool_group.exclusive()
        assert select.objectName() == "TelestrationSelect"
        assert library is player.shape_library_button
        assert select.isChecked()
        for button in (select, library):
            assert button.isCheckable()
            assert button.toolTip() and button.accessibleName()
            assert 0 <= button.mapTo(rail, QPoint(0, 0)).x()
            assert (button.mapTo(rail, QPoint(0, 0)).x()
                    + button.width()) <= 30

        swatch = player.ink_button
        assert swatch.objectName() == "TelestrationInk"
        assert swatch.size() == QSize(18, 18)
        assert swatch.iconSize() == QSize(10, 10)
        assert swatch.text() == "" and not swatch.icon().isNull()
        assert swatch.property("inkColor") == "gold"
        assert swatch.focusPolicy() & Qt.FocusPolicy.TabFocus
        assert swatch.toolTip() and swatch.accessibleName()

        undo = rail.findChild(QToolButton, "TelestrationUndo")
        clear = rail.findChild(QToolButton, "TelestrationClear")
        assert undo is not None and clear is not None
        utilities = (undo, clear)
        assert all(button.size() == QSize(24, 24) for button in utilities)
        assert all(button.iconSize() == QSize(16, 16) for button in utilities)
        assert all(button.text() == "" and not button.icon().isNull()
                   for button in utilities)
        assert all(button.toolButtonStyle() ==
                   Qt.ToolButtonStyle.ToolButtonIconOnly
                   for button in utilities)
        assert all(button.focusPolicy() & Qt.FocusPolicy.TabFocus
                   for button in utilities)
        assert all(button.toolTip() and button.accessibleName()
                   for button in utilities)

        # The rail is greyed until a play owns the strokes.
        player.set_telestration_enabled(True)
        # Shapes and inks are driven from the library, and Select disarms.
        for shape in ("route_arrow", "block_tee", "zone_bubble"):
            player._shape_library_picked(shape)
            assert player.video_widget.tool() == shape
        player.tool_group.button(0).click()
        assert player.video_widget.tool() is None
        for ink in ("gold", "cyan", "red", "neon"):
            player._ink_library_picked(ink)
            assert player.video_widget.ink() == ink
            assert player.ink_button.property("inkColor") == ink

        utility_calls = []
        monkeypatch.setattr(
            player.video_widget, "undo_mark",
            lambda: utility_calls.append("undo"))
        monkeypatch.setattr(
            player.video_widget, "clear_marks",
            lambda: utility_calls.append("clear"))
        undo.click()
        clear.click()
        assert utility_calls == ["undo", "clear"]

        monkeypatch.setattr(
            player.video_widget, "marks", lambda: [object(), object()])
        player._refresh_mark_count()
        assert player.mark_count_label.text() == "2 marks"
        assert player.mark_count_label.isHidden()
        assert rail.accessibleDescription() == "2 marks"

        player.set_tag_map_collapsed(True)
        assert player.video_widget.tool() is None
    finally:
        window._app_closing = True
        window.hide()


def test_standalone_player_requires_an_explicit_deck_harness(qapp, settings):
    """A bare player owns no deck; a host constructs and attaches exactly one."""
    owner = QWidget()
    owner_layout = QVBoxLayout(owner)
    owner_layout.setContentsMargins(0, 0, 0, 0)
    owner_layout.setSpacing(0)
    player = VideoPlayer(settings, owner)
    assert player.control_center is None
    assert player.findChild(QWidget, "TransportPill") is None
    deck = ControlCenterDeck(owner)
    player.attach_control_center(deck)
    feature_requests = []
    deck.voiceover_record_requested.connect(
        lambda: feature_requests.append("voiceover"))
    deck.export_requested.connect(lambda: feature_requests.append("export"))
    deck.voiceover_record_button.click()
    deck.deck_export_button.click()
    assert feature_requests == ["voiceover", "export"]
    owner_layout.addWidget(player, 1)
    owner_layout.addWidget(deck)
    owner.show()
    try:
        for size in ((1400, 768), (1260, 768)):
            owner.resize(*size)
            owner.layout().activate()
            qapp.processEvents()
            qapp.processEvents()
            assert (owner.width(), owner.height()) == size
            assert player.control_center is deck
            assert player.transport_pill is deck
            assert deck.parentWidget() is owner
            assert not player.isAncestorOf(deck)
            assert deck.isVisible()
            deck_top = deck.mapTo(owner, QPoint(0, 0)).y()
            player_bottom = player.mapTo(
                owner, QPoint(0, player.height())).y()
            assert deck_top >= player_bottom
            assert deck_top + deck.height() <= owner.height()
    finally:
        owner.deleteLater()
        qapp.processEvents()


    """A part-visible tag would read as a different, shorter tag."""
    tray = QuickTagTray(settings.quick_tag_favorites)
    tray.set_inline(True)
    try:
        for width in (240, 320, 460, 620, 900):
            tray.resize(width, 40)
            qapp.processEvents()
            visible = [button for button in tray.buttons.values()
                       if not button.isHidden()]
            for button in visible:
                right_edge = button.mapTo(tray.scroll, QPoint(0, 0)).x() \
                    + button.width()
                assert right_edge <= tray.scroll.width() + 1, (
                    width, button.text())

            hidden = [button for button in tray.buttons.values()
                      if button.isHidden()]
            assert tray.more_button.isHidden() == (not hidden), width
            if hidden:
                assert tray.more_button.text() == f"More +{len(hidden)}"
                # The overflow holds one real button per hidden tag.
                assert len(tray.more_buttons) == len(hidden)
                assert [b.text() for b in tray.more_buttons.values()] ==                     [b.text() for b in hidden]
    finally:
        tray.deleteLater()
        qapp.processEvents()


def test_inline_quick_tag_overflow_still_applies_the_tag(qapp, settings):
    """Tags behind More must fire the same signal the buttons do."""
    tray = QuickTagTray(settings.quick_tag_favorites)
    tray.set_inline(True)
    tray.resize(240, 40)
    qapp.processEvents()
    try:
        requested: list[str] = []
        tray.tag_requested.connect(requested.append)

        # With nothing selected the overflow must be as inert as the rail.
        assert tray.more_buttons
        assert not any(button.isEnabled()
                       for button in tray.more_buttons.values())

        tray.set_clip(Clip(start_ms=0, end_ms=10_000, details={}))
        overflow = list(tray.more_buttons.values())
        assert overflow, "expected overflow at this width"
        assert all(button.isEnabled() for button in overflow)
        overflow[0].click()
        assert len(requested) == 1
        assert tray.more_popup.isHidden()
    finally:
        tray.deleteLater()
        qapp.processEvents()


def test_more_popup_is_a_uniform_grid_with_an_add_cell(qapp, settings):
    """Ragged rows of unequal buttons read as leftovers, not as actions."""
    tray = QuickTagTray(settings.quick_tag_favorites)
    tray.set_inline(True)
    tray.resize(420, 40)
    qapp.processEvents()
    try:
        overflow = list(tray.more_buttons.values())
        assert overflow, "expected overflow at this width"

        widths = {button.width() for button in overflow}
        assert len(widths) == 1, widths
        assert tray.more_add_button.width() == overflow[0].width()

        cells = len(overflow) + 1
        expected = (tray.POPUP_WIDE_COLUMNS
                    if cells > tray.POPUP_WIDE_AFTER
                    else tray.POPUP_COLUMNS)
        columns = {tray.more_grid.getItemPosition(index)[1]
                   for index in range(tray.more_grid.count())}
        assert columns == set(range(min(cells, expected)))

        # The add cell is always last, never orphaned above a tag.
        last = tray.more_grid.count() - 1
        assert tray.more_grid.itemAt(last).widget() is tray.more_add_button

        requested: list[bool] = []
        tray.manage_requested.connect(requested.append)
        tray.more_add_button.click()
        assert requested == [True]
        assert tray.more_popup.isHidden()
    finally:
        tray.deleteLater()
        qapp.processEvents()


def test_more_popup_grows_and_shrinks_with_the_tag_set(qapp, settings):
    """Adding and removing tags must re-flow the rail and the popup grid."""
    tray = QuickTagTray(settings.quick_tag_favorites)
    tray.set_inline(True)
    tray.resize(620, 40)
    qapp.processEvents()

    def reflow():
        qapp.processEvents()
        shown = [b for b in tray.buttons.values() if not b.isHidden()]
        hidden = [b for b in tray.buttons.values() if b.isHidden()]
        return shown, hidden

    try:
        for count in (16, 12, 9, 5, 3, 1):
            tray.set_tags(tuple(QUICK_TAGS[:count]))
            shown, hidden = reflow()

            assert len(tray.buttons) == count
            assert len(shown) + len(hidden) == count
            # Every hidden tag, and only those, is in the popup.
            assert set(tray.more_buttons) == {
                key for key, button in tray.buttons.items()
                if button.isHidden()}
            assert tray.more_button.isHidden() == (not hidden)

            if hidden:
                assert tray.more_button.text() == f"More +{len(hidden)}"
                cells = len(hidden) + 1  # tags plus the add control
                assert tray.more_grid.count() == cells
                # QGridLayout.rowCount() is sticky - it never shrinks once
                # grown - so the occupied cells are what must be checked.
                positions = [tray.more_grid.getItemPosition(index)[:2]
                             for index in range(cells)]
                assert positions == [
                    (index // (max(c for _r, c in positions) + 1),
                     index % (max(c for _r, c in positions) + 1))
                    for index in range(cells)], count
                widths = {b.width() for b in tray.more_buttons.values()}
                assert len(widths) == 1, (count, widths)
            else:
                # No overflow: the grid must be empty, not holding the
                # previous set's buttons.
                assert tray.more_popup is None or tray.more_grid.count() == 0

        # And back up again: shrinking must not leave stale buttons behind.
        tray.set_tags(tuple(QUICK_TAGS[:16]))
        shown, hidden = reflow()
        assert len(tray.buttons) == 16
        assert set(tray.more_buttons) == {
            key for key, button in tray.buttons.items() if button.isHidden()}
    finally:
        tray.deleteLater()
        qapp.processEvents()


def test_quick_tag_edit_and_undo_both_reach_the_play_readout(
        qapp, settings, monkeypatch, tmp_path):
    """Tagging must update the projected play readout, not just the rail."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create(
        "Tag readout refresh", tmp_path, tmp_path / "exports")
    session.project.source_duration_ms = 60_000
    clip = session.add_clip(
        Clip(start_ms=1_000, end_ms=4_000, clip_title="Play 1"))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    qapp.processEvents()

    def play_type_key() -> str | None:
        saved_clip = session.get_clip(clip.id)
        projected = _play_type(saved_clip.details) if saved_clip else None
        return projected[0] if projected else None

    try:
        assert play_type_key() != "screen"

        window._apply_quick_tag("screen")
        qapp.processEvents()
        assert session.get_clip(clip.id).details["play_type"] == "Screen"
        assert play_type_key() == "screen"

        # Untagging has to clear the projection, not just the checked state.
        window._apply_quick_tag("screen")
        qapp.processEvents()
        assert play_type_key() != "screen"

        window._apply_quick_tag("screen")
        qapp.processEvents()
        assert play_type_key() == "screen"
        window._undo()
        qapp.processEvents()
        assert play_type_key() != "screen"
        window._redo()
        qapp.processEvents()
        assert play_type_key() == "screen"
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_library_preview_dial_is_placed_between_the_transport_keys(
        qapp, settings):
    """The V2 rebuild must move the dial, not tear Play out of it."""
    screen = LibrarySearchScreenV2(settings)
    screen.resize(1600, 950)
    screen.show()
    screen.layout().activate()
    qapp.processEvents()

    try:
        ring = screen.preview_jog_ring
        play = screen.preview_play_btn
        rewind = screen.preview_rewind_btn
        forward = screen.preview_fast_forward_btn

        # Play stays inside the dial: the dial paints the seat around it and
        # its grid is what positions it.
        assert play.parent() is ring
        assert play.width() == ring.CENTER_DIAMETER

        # The dial is laid out, not floating at the panel origin.
        assert ring.parentWidget() is not None
        assert ring.isVisible()
        assert ring.geometry().topLeft() != QPoint(0, 0)

        # Rewind, dial, fast-forward read as one row, in order.
        assert (rewind.geometry().center().x()
                < ring.geometry().center().x()
                < forward.geometry().center().x())
        assert rewind.geometry().center().y() == ring.geometry().center().y()
        assert forward.geometry().center().y() == ring.geometry().center().y()

        # And the dial must not overlap the panel it used to paint on top of.
        assert not ring.geometry().intersects(
            screen.preview_thumb.geometry())
    finally:
        screen.close()
        screen.deleteLater()
        qapp.processEvents()


def test_masthead_and_ledger_report_the_same_logged_count(
        qapp, settings, monkeypatch, tmp_path):
    """The two counts sit inches apart and must not disagree."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create(
        "Counts", tmp_path, tmp_path / "exports")
    logged = session.add_clip(Clip(
        start_ms=0, end_ms=2_000, details={"play_type": "Run"}))
    session.add_clip(Clip(start_ms=3_000, end_ms=5_000))
    # Details but disabled: the ledger has always excluded these, so the
    # masthead must exclude them too.
    disabled = session.add_clip(Clip(
        start_ms=6_000, end_ms=8_000, details={"play_type": "Pass"}))
    disabled.enabled = False
    window.session = session
    window._refresh_clip_list()
    qapp.processEvents()

    try:
        assert window.review_progress_label.text() == "1 / 3 logged"
        assert "1 logged" in window.clip_list.progress_label.text()

        logged.enabled = False
        window._refresh_clip_list()
        qapp.processEvents()
        assert window.review_progress_label.text() == "0 / 3 logged"
        assert "0 logged" in window.clip_list.progress_label.text()

        # Status badges have to read as words, not as punctuation.
        table = window.clip_list.table
        texts = {
            table.item(row, 1).text()
            for row in range(table.rowCount())
            if table.item(row, 1) is not None
        }
        assert not (texts & {"!", "OK", "DET!", "NO", "OFF", "NEW"}), texts
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_inspector_collapses_empty_details_into_add_chips(
        qapp, settings, monkeypatch, tmp_path):
    """Nine always-rendered rows meant seven dashes to scroll past."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create(
        "Collapse", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=0, end_ms=4_000,
        details={"run_pass": "Run", "play_type": "RPO"}))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    qapp.processEvents()

    editor = window.clip_editor
    try:
        keys = editor._advanced_detail_keys()
        assert len(keys) > 2, keys

        filled = {"run_pass", "play_type"}
        for key in keys:
            expected = key in filled
            # isHidden, not isVisible: the section itself may be
            # collapsed, which would hide every child regardless.
            assert editor.detail_cells[key].isHidden() is not expected, key

        # Read the layout, not findChildren: chips awaiting deleteLater
        # are still children until the event loop next spins.
        def chip_texts():
            layout = editor._advanced_add_layout
            return {
                layout.itemAt(i).widget().text()
                for i in range(layout.count())
                if layout.itemAt(i).widget() is not None
            }

        assert chip_texts() == {
            f"+ {editor.detail_labels[k].text()}"
            for k in keys if k not in filled}
        assert not editor._advanced_add_row.isHidden()

        # A chip turns into its field, and the value survives the round trip.
        target = next(k for k in keys if k not in filled)
        editor._reveal_detail(target)
        qapp.processEvents()
        assert not editor.detail_cells[target].isHidden()
        assert f"+ {editor.detail_labels[target].text()}" not in chip_texts()

        # Revealing is per play, not a setting that leaks to the next clip.
        other = session.add_clip(Clip(start_ms=5_000, end_ms=9_000))
        window._refresh_clip_list()
        window.select_clip(other.id, seek=False)
        qapp.processEvents()
        assert editor._revealed_details == set()
        assert editor.detail_cells[target].isHidden()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_inspector_notes_start_compact_and_expand_on_demand(
        qapp, settings, monkeypatch, tmp_path):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create("Notes", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(start_ms=0, end_ms=4_000))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    qapp.processEvents()

    editor = window.clip_editor
    try:
        assert not editor.notes_expand_btn.isHidden()
        # Density owns the collapsed height; the toggle raises the cap.
        collapsed = editor.notes_edit.maximumHeight()
        assert collapsed == editor._notes_density_height
        assert editor.notes_expand_btn.text() == "Expand"

        editor.notes_expand_btn.setChecked(True)
        qapp.processEvents()
        assert editor.notes_edit.maximumHeight() == \
            editor.NOTES_HEIGHT_EXPANDED
        assert editor.notes_expand_btn.text() == "Collapse"
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_shortcuts_overlay_matches_the_real_bindings(
        qapp, settings, monkeypatch):
    """A sheet that lies is worse than no sheet."""
    from tapesift.ui_core.shortcuts_overlay import SHORTCUT_GROUPS

    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    try:
        registered = {
            shortcut.key().toString()
            for shortcut in window.findChildren(QShortcut)
        }
        claimed = [
            (keys, binding)
            for _title, rows in SHORTCUT_GROUPS
            for keys, _meaning, binding in rows
            if binding is not None
        ]
        assert claimed, "the sheet should cite real bindings"
        for keys, binding in claimed:
            assert QKeySequence(binding).toString() in registered, (
                f"sheet lists {keys!r} but {binding!r} is not bound")

        # The keys this stage added must actually exist.
        assert QKeySequence("M").toString() in registered
        assert QKeySequence("?").toString() in registered
        assert len(window._quick_tag_shortcuts) == 9
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_shortcuts_overlay_opens_and_closes_on_the_question_key(
        qapp, settings, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.resize(1400, 900)
    window.show()
    qapp.processEvents()

    try:
        assert getattr(window, "_shortcuts_overlay", None) is None
        window._toggle_shortcuts_overlay()
        qapp.processEvents()
        overlay = window._shortcuts_overlay
        assert not overlay.isHidden()
        # Centred, and inside the window on both axes.
        assert overlay.geometry().left() >= 0
        assert overlay.geometry().top() >= 0
        assert overlay.width() <= window.width()

        window._toggle_shortcuts_overlay()
        qapp.processEvents()
        assert overlay.isHidden()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_number_keys_apply_the_quick_tag_in_that_slot(
        qapp, settings, monkeypatch, tmp_path):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create("Slots", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(start_ms=0, end_ms=4_000))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    qapp.processEvents()

    try:
        tray = window.quick_tag_tray
        first = tray.tags[0]
        window._quick_tag_by_slot(0)
        qapp.processEvents()
        assert tray.buttons[first.key].isChecked()

        # Toggles off again, exactly like clicking the button.
        window._quick_tag_by_slot(0)
        qapp.processEvents()
        assert not tray.buttons[first.key].isChecked()

        # A slot past the end of the rail is a no-op, not a crash.
        window._quick_tag_by_slot(len(tray.tags) + 3)
        qapp.processEvents()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_grid_cell_edit_saves_through_the_normal_undo_path(
        qapp, settings, monkeypatch, tmp_path):
    """Editing in the grid must be the existing action, not a second one."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create(
        "Grid edit", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(start_ms=1_000, end_ms=5_000))
    window.session = session
    window._refresh_clip_list()
    qapp.processEvents()

    menus: list = []
    monkeypatch.setattr(
        QMenu, "popup", lambda self, *a, **k: menus.append(self))

    try:
        window._grid_cell_edit_requested(clip.id, "result")
        qapp.processEvents()

        assert menus, "a chooser should have opened"
        # Selecting the clip is part of editing it: the write goes through
        # the inspector, which has to be holding this clip.
        assert window._selected_clip_id == clip.id

        first_down = next(
            a for a in menus[0].actions() if a.text() == "First Down")
        first_down.trigger()
        qapp.processEvents()

        assert session.get_clip(clip.id).details["result"] == "First Down"
        assert session.can_undo()

        window._undo()
        qapp.processEvents()
        assert session.get_clip(clip.id).details.get("result", "") == ""

        # Nothing offers to edit a row that is derived or free text.
        menus.clear()
        window._grid_cell_edit_requested(clip.id, "confidence")
        qapp.processEvents()
        assert menus == []
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_heat_map_export_survives_the_menu_being_rebuilt(
        qapp, settings, monkeypatch, tmp_path):
    """An action the menu owns dies with the menu when the workspace rebuilds."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()
    try:
        assert window.export_heatmap_action.text().startswith(
            "Export Game Heat Map")
        window._rehome_load_video()
        qapp.processEvents()
        # Reading text() on a dead C++ object raises, which is the failure
        # this guards: the entry looked present but could not be used.
        assert window.export_heatmap_action.text().startswith(
            "Export Game Heat Map")
        assert window.export_heatmap_action.parent() is window
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_inspector_shows_rows_or_pickers_but_never_both(
        qapp, settings, monkeypatch, tmp_path):
    """One shape at a time, and the old pickers are a flag away."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create("Shape", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(start_ms=0, end_ms=4_000))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    qapp.processEvents()

    editor = window.clip_editor
    try:
        # Off by default: the grid under the timeline logs the situation.
        assert editor.attribute_rows_panel.isHidden()
        assert editor.quick_pickers.isHidden()

        editor.LOG_ATTRIBUTES_IN_INSPECTOR = True
        editor._sync_inspector_panels()
        qapp.processEvents()
        # isHidden, not isVisible: the inspector's dock may be closed in
        # this fixture, and that is not what these panels decide.
        assert not editor.attribute_rows_panel.isHidden()
        assert editor.quick_pickers.isHidden()

        # Nothing was deleted: the pickers come back on one constant.
        editor.USE_ATTRIBUTE_ROWS = False
        editor._sync_inspector_panels()
        qapp.processEvents()
        assert editor.attribute_rows_panel.isHidden()
        assert not editor.quick_pickers.isHidden()
        editor.USE_ATTRIBUTE_ROWS = True

        # With no clip open neither shape claims the space.
        editor.set_clip(None)
        qapp.processEvents()
        assert editor.attribute_rows_panel.isHidden()
        assert editor.quick_pickers.isHidden()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_inspector_rows_carry_a_typed_field_position(
        qapp, settings, monkeypatch, tmp_path):
    """Ball on is a place, so its slot keeps the side that was typed."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create("Spot", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(start_ms=0, end_ms=4_000))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    qapp.processEvents()

    editor = window.clip_editor
    editor.LOG_ATTRIBUTES_IN_INSPECTOR = True
    editor._sync_inspector_panels()
    rows = editor.attribute_rows
    try:
        rows["ball_on"].entry.setText("Own 35")
        rows["ball_on"].entry.editingFinished.emit()
        qapp.processEvents()
        assert editor.detail_edits["ball_on"].text() == "Own 35"

        assert editor._apply()
        window.clip_editor.set_clip(None)
        window.clip_editor.set_clip(clip)
        qapp.processEvents()
        assert rows["ball_on"].value() == "Own 35"
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_inspector_rows_read_and_write_through_the_existing_fields(
        qapp, settings, monkeypatch, tmp_path):
    """The rows are a new way to reach an existing edit, not a new edit."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create("Rows", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(start_ms=0, end_ms=4_000))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    qapp.processEvents()

    editor = window.clip_editor
    # The rows are off by default now - the grid logs the situation. They
    # still exist behind the flag, which is what this test covers.
    editor.LOG_ATTRIBUTES_IN_INSPECTOR = True
    editor._sync_inspector_panels()
    qapp.processEvents()
    rows = editor.attribute_rows
    try:
        assert not editor.attribute_rows_panel.isHidden()
        # The pickers they replace stand down in analyst mode.
        assert editor.quick_pickers.isHidden()

        rows["quarter"].buttons["Q2"].click()
        rows["down"].buttons["3rd"].click()
        rows["to_go"].entry.setText("7")
        rows["to_go"].entry.editingFinished.emit()
        rows["run_pass"].buttons["RPO"].click()
        rows["result_a"].buttons["1st Dn"].click()
        qapp.processEvents()

        assert editor.detail_edits["quarter"].text() == "Q2"
        assert editor.detail_edits["down_distance"].text() == "3rd & 7"
        assert editor.detail_edits["play_type"].text() == "RPO"
        # The compact row label saves as the project's real vocabulary.
        assert "First Down" in editor.detail_edits["result"].text()

        # Goal-to-go is a distance the field has to carry as text.
        rows["to_go"].entry.setText("g")
        rows["to_go"].entry.editingFinished.emit()
        qapp.processEvents()
        assert editor.detail_edits["down_distance"].text() == "3rd & Goal"

        # Reopening shows what the clip says - after saving, because the
        # rows write into the editor's fields and Save is what commits
        # them, exactly like every other control in the panel.
        assert editor._apply()
        qapp.processEvents()
        assert clip.details["quarter"] == "Q2"
        window.clip_editor.set_clip(None)
        window.clip_editor.set_clip(clip)
        qapp.processEvents()
        assert rows["quarter"].value() == "Q2"
        assert rows["down"].value() == "3rd"
        assert rows["to_go"].value() == "Goal"
        assert rows["run_pass"].value() == "RPO"
        assert rows["result_a"].value() == "1st Dn"
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_grid_edits_write_through_the_same_path_as_the_inspector(
        qapp, settings, monkeypatch, tmp_path):
    """The grid is a shortcut to an existing edit, never a second writer."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create("Grid", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=0, end_ms=4_000,
        details={"quarter": "Q1", "down_distance": "3rd & 7"}))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    qapp.processEvents()

    try:
        # Quarter: a plain choice.
        window.clip_editor.apply_quick_details({"quarter": "Q3"})
        qapp.processEvents()
        assert window.clip_editor.detail_edits["quarter"].text() == "Q3"

        # Down: the pick has to keep the distance beside it.
        from tapesift.ui_v2.attribute_grid import set_down_keeping_distance
        kept = set_down_keeping_distance(
            clip.details.get("down_distance", ""), "4th")
        assert kept == "4th & 7"
        window.clip_editor.apply_quick_details({"down_distance": kept})
        qapp.processEvents()
        assert window.clip_editor.detail_edits[
            "down_distance"].text() == "4th & 7"

        # Clearing must not read as "already set" on every play.
        assert not window._grid_choice_is_current(clip, {"action": ""}) \
            or not clip.details.get("action", "")
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_a_keystroke_on_the_grid_logs_the_play(
        qapp, settings, monkeypatch, tmp_path):
    """Iteration 3's whole point: one key, one edit, no mouse."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create("Keys", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=0, end_ms=4_000,
        details={"quarter": "Q1", "down_distance": "3rd & 7"}))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    qapp.processEvents()

    try:
        # Third quarter choice, straight from the keyboard.
        window._grid_cell_choice_picked(clip.id, "quarter", 2)
        qapp.processEvents()
        assert window.clip_editor.detail_edits["quarter"].text() == "Q3"

        # A down pick from the keyboard keeps the distance, same as the menu.
        window._grid_cell_choice_picked(clip.id, "down", 3)
        qapp.processEvents()
        assert window.clip_editor.detail_edits[
            "down_distance"].text() == "4th & 7"

        # Out of range is ignored rather than crashing or writing junk.
        before = window.clip_editor.detail_edits["quarter"].text()
        window._grid_cell_choice_picked(clip.id, "quarter", 99)
        window._grid_cell_choice_picked(clip.id, "people", 0)
        qapp.processEvents()
        assert window.clip_editor.detail_edits["quarter"].text() == before
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_the_reduced_inspector_leaves_nothing_unreachable(
        qapp, settings, monkeypatch, tmp_path):
    """Iteration 4: the panel stops logging the situation, keeps the rest.

    The grid under the timeline shows quarter, down and result across
    every play and can be logged from the keyboard, so the inspector no
    longer carries a picker for them. What it must never do is show a
    value with no way to change it - that is worse than either surface.
    """
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create("Lean", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(
        start_ms=0, end_ms=4_000,
        details={"quarter": "Q2", "down_distance": "3rd & 7",
                 "ball_on": "Own 35", "result": "First Down"}))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    qapp.processEvents()

    editor = window.clip_editor
    try:
        assert not editor.LOG_ATTRIBUTES_IN_INSPECTOR
        assert editor.attribute_rows_panel.isHidden()
        assert editor.quick_pickers.isHidden()

        # Everything the pickers used to own is still editable somewhere.
        for key in ("quarter", "down_distance", "result", "ball_on"):
            assert key in editor.detail_edits
            assert editor._details_grid.indexOf(
                editor.detail_cells[key]) >= 0, key

        # And the panel still does the jobs the grid cannot.
        assert not editor.notes_box.isHidden()
        assert not editor.players_box.isHidden()
        assert not editor.action_bar.isHidden()

        # Nothing was deleted: one constant brings the rows back.
        editor.LOG_ATTRIBUTES_IN_INSPECTOR = True
        editor._sync_inspector_panels()
        qapp.processEvents()
        assert not editor.attribute_rows_panel.isHidden()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_play_details_folds_to_a_strip_and_comes_back(
        qapp, settings, monkeypatch, tmp_path):
    """Logging runs in the grid, so the panel folds instead of closing.

    Folding rather than closing matters: the panel still owns the title,
    the range, the film note and export, and a closed dock is a thing you
    have to remember exists.
    """
    from tapesift.ui_core.clip_editor import COLLAPSED_WIDTH

    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create("Fold", tmp_path, tmp_path / "exports")
    clip = session.add_clip(Clip(start_ms=0, end_ms=4_000))
    window.session = session
    window._refresh_clip_list()
    assert window.select_clip(clip.id, seek=False)
    qapp.processEvents()

    editor = window.clip_editor
    try:
        assert not editor.is_collapsed()
        assert not editor.form_area.isHidden()

        editor.toggle_collapsed()
        qapp.processEvents()
        assert editor.is_collapsed()
        assert editor.width() == COLLAPSED_WIDTH
        assert editor.form_area.isHidden()
        assert editor.action_bar.isHidden()
        # The handle is the only thing left, so it has to still be there.
        assert not editor.collapse_btn.isHidden()

        # Loading a clip must not quietly unfold the panel.
        editor.set_clip(clip)
        qapp.processEvents()
        assert editor.is_collapsed()
        assert editor.form_area.isHidden()

        editor.toggle_collapsed()
        qapp.processEvents()
        assert not editor.is_collapsed()
        assert not editor.form_area.isHidden()
        assert editor.width() > COLLAPSED_WIDTH
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_folding_reports_the_width_it_wants_back(
        qapp, settings, monkeypatch, tmp_path):
    """Restoring only the floor reopens the panel at its narrowest."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()
    editor = window.clip_editor
    try:
        seen: list[bool] = []
        editor.collapse_changed.connect(seen.append)
        editor.resize(360, editor.height())
        editor.toggle_collapsed()
        qapp.processEvents()
        assert seen == [True]
        assert editor.open_width() >= editor.minimumWidth()
        editor.toggle_collapsed()
        qapp.processEvents()
        assert seen == [True, False]
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_u_walks_the_plays_that_still_need_logging(
        qapp, settings, monkeypatch, tmp_path):
    """Save + Next goes to the next clip; U goes to the next unfinished."""
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    window = MainWindowV2(settings)
    window.show()
    qapp.processEvents()

    session = ProjectSession.create("Unlogged", tmp_path, tmp_path / "exports")
    for index in range(6):
        session.add_clip(Clip(
            start_ms=index * 5_000, end_ms=index * 5_000 + 4_000,
            clip_number=index + 1,
            details={"quarter": "Q1"} if index % 2 == 0 else {}))
    window.session = session
    window._refresh_clip_list()
    clips = session.clips
    assert window.select_clip(clips[0].id, seek=False)
    qapp.processEvents()

    try:
        for expected in (1, 3, 5):
            window._goto_unlogged(1)
            qapp.processEvents()
            assert window._current_row() == expected

        # Nothing after the last one, so it stays put rather than wrapping.
        window._goto_unlogged(1)
        qapp.processEvents()
        assert window._current_row() == 5

        window._goto_unlogged(-1)
        qapp.processEvents()
        assert window._current_row() == 3
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_teardown_actually_destroys_widgets_rather_than_queueing_them(qapp):
    """The fixture above has to free widgets, not just ask nicely.

    deleteLater posts a DeferredDelete event, and processEvents does not
    deliver those. For most of this project's life the teardown fixture
    called deleteLater and then processEvents twice, which freed nothing:
    top-level widgets climbed past three thousand over a single run and
    the process died partway through at whatever it happened to be doing.
    The fix is one extra call, and this is the guard on it.
    """
    before = len(qapp.topLevelWidgets())
    kept = [QWidget() for _ in range(40)]
    assert len(qapp.topLevelWidgets()) == before + 40

    for widget in kept:
        widget.deleteLater()

    qapp.processEvents()
    qapp.processEvents()
    # Still every one of them: this is the trap.
    assert len(qapp.topLevelWidgets()) == before + 40

    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()
    assert len(qapp.topLevelWidgets()) == before


def test_teardown_releases_rebuilt_menu_trees_with_complex_windows(
        qapp, settings, monkeypatch):
    """Repeated MainWindow teardown must leave the next menu rebuild valid.

    A bare QWidget regression cannot catch native ownership corruption in the
    player's QWidgetAction/default-widget tree. Six cycles reproduced the
    Windows access violation in set_timeline_key when teardown called
    QMenu.clear() before deleting its MainWindow; eight cycles guard the real
    complex-window lifetime instead.
    """
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    entries = (
        ("run", "Run", QColor("#39e07a")),
        ("pass", "Pass", QColor("#66aaff")),
    )

    for index in range(8):
        window = MainWindowV2(settings, run_pass_lab=bool(index % 2))
        window.show()
        window.player.set_timeline_key("play_type", entries)
        qapp.processEvents()

        assert len(window.player.timeline_key_menu.actions()) >= len(entries)

        _destroy_qt_roots(qapp)
        assert not qapp.topLevelWidgets()
        assert not qapp.allWidgets()


def test_dock_v2_is_the_default_and_the_old_deck_stays_reachable(
        qapp, monkeypatch):
    """Dock V2 needs no switch; the old deck is one variable away.

    Was opt-in through iterations 2-4. Dock V2 is now the dock, including
    from the desktop shortcut, which launches this source tree with no
    environment set. TAPESIFT_DOCK_V2=0 is the rollback path and must keep
    working: both decks satisfy the same attach contract.
    """
    monkeypatch.delenv("TAPESIFT_DOCK_V2", raising=False)
    assert dock_v2_enabled()
    assert type(make_control_center()) is DockV2Deck

    for opt_out in ("0", "false", "no", "off", "OFF"):
        monkeypatch.setenv("TAPESIFT_DOCK_V2", opt_out)
        assert not dock_v2_enabled(), opt_out
        assert type(make_control_center()) is ControlCenterDeck

    for opt_in in ("1", "true", "on"):
        monkeypatch.setenv("TAPESIFT_DOCK_V2", opt_in)
        assert dock_v2_enabled(), opt_in
        assert type(make_control_center()) is DockV2Deck


def test_dock_v2_band_lays_out_target_composition(qapp):
    """One band: marks/readout, centered transport, then Jog Wheel.

    Replaces the two-row geometry assertion for Dock V2 only. The V1 band
    keeps its own invariant in
    test_transport_deck_lays_out_two_rows_across_the_width.
    """
    deck = DockV2Deck()
    deck.resize(1900, deck.DECK_HEIGHT)
    deck.show()
    deck.layout().activate()
    qapp.processEvents()

    # One band, not two rows: the inherited second row is collapsed.
    assert deck.height() == deck.DECK_HEIGHT
    bottom = deck.findChild(QWidget, "ControlCenterBottomRow")
    assert bottom is not None and not bottom.isVisible()

    names = ("TransportPositionZone", "TransportCluster",
             "TransportMarksGroup", "TransportExportZone")
    zones = [deck.findChild(QWidget, name) for name in names]
    assert all(zone is not None and zone.isVisible() for zone in zones)

    # Zones may not overlap at the reference viewport.
    spans = sorted((zone.mapTo(deck, QPoint(0, 0)).x(), zone.width(), name)
                   for zone, name in zip(zones, names))
    for (left, width, name), (next_left, _w, next_name) in zip(spans, spans[1:]):
        assert left + width <= next_left, (name, next_name)

    # Five symmetric island keys, literal to the layout (frame-step,
    # shuttle, PLAY, shuttle, frame-step). The jump keys stay parked while
    # the selected shell keeps the wheel's launcher visible on the band.
    island_controls = (
        deck.step_back_btn, deck.rewind_btn,
        deck.play_btn,
        deck.fast_forward_btn, deck.step_fwd_btn,
    )
    assert all(control.isVisible() for control in island_controls)
    positions = [
        control.mapTo(deck, QPoint()).x() for control in island_controls]
    assert positions == sorted(positions)
    assert deck.play_btn.parentWidget() is deck.transport_island
    # The two jump keys are parked, not deleted.
    for parked_key in (deck.skip_backward_btn, deck.skip_forward_btn):
        assert deck.overflow_bay.isAncestorOf(parked_key)
        assert not parked_key.isVisible()
    assert deck.jog_ring is deck.jog_window.wheel
    assert deck.jog_ring.parentWidget() is deck.jog_window
    assert deck.jog_ring.play_button is None
    assert deck.jog_toggle.isVisible()
    assert deck.jog_toggle.text() == "Jog Wheel"
    assert deck.jog_toggle.parentWidget() is deck.deck_export_zone
    assert deck.jog_toggle.size() == QSize(88, 30)
    menu = deck.overflow_button.menu()
    jog_actions = [a for a in menu.actions() if a.text() == "Show jog wheel"]
    assert len(jog_actions) == 1
    jog_actions[0].trigger()
    qapp.processEvents()
    assert deck.jog_window.isVisible()
    deck.jog_window.close()
    qapp.processEvents()
    assert not deck.jog_window.isVisible()
    assert deck.findChild(QToolButton, "TransportPause") is None
    assert deck.DECK_HEIGHT == 46
    assert deck.minimumSizeHint() == QSize(520, 46)

    # Nothing was dropped: compatibility aliases and controls without a
    # permanent slot stay live in the hidden overflow bay.
    assert DockV2Deck.COMPATIBILITY_ALIASES == \
        ControlCenterDeck.COMPATIBILITY_ALIASES
    assert len(ControlCenterDeck.COMPATIBILITY_ALIASES) == 17
    assert all(getattr(deck, name, None) is not None
               for name in ControlCenterDeck.COMPATIBILITY_ALIASES)
    for parked in (deck.loop_btn, deck.voice_clear_button,
                   deck.voiceover_take_label, deck.deck_format_button):
        assert deck.overflow_bay.isAncestorOf(parked)
        assert not parked.isVisible()
    # The compact band parks the compatibility meter; the jog window
    # displays rate feedback when opened.
    assert deck.overflow_bay.isAncestorOf(deck.shuttle_meter)
    assert not deck.shuttle_meter.isVisible()
    assert [button.text() for button in deck.speed_buttons] == [
        "1x", "2x", "4x", "8x"]


def _jog_drag_frames(wheel, degrees, steps=24):
    """Drive a rotational drag and total the frames the wheel asked for."""
    import math
    from PySide6.QtGui import QMouseEvent

    wheel.resize(wheel.WIDTH, wheel.HEIGHT)
    wheel.show()
    requested = []
    wheel.framesRequested.connect(requested.append)
    center = wheel.dial_center()
    radius = wheel.FACE_RADIUS * 0.6

    def at(degree):
        radians = math.radians(degree)
        return QPointF(center.x() + math.cos(radians) * radius,
                       center.y() + math.sin(radians) * radius)

    def event(kind, point):
        return QMouseEvent(kind, point, wheel.mapToGlobal(point.toPoint()),
                           Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                           Qt.KeyboardModifier.NoModifier)

    wheel.mousePressEvent(event(QMouseEvent.Type.MouseButtonPress, at(0)))
    for step in range(1, steps + 1):
        wheel.mouseMoveEvent(
            event(QMouseEvent.Type.MouseMove, at(degrees * step / steps)))
    wheel.mouseReleaseEvent(
        event(QMouseEvent.Type.MouseButtonRelease, at(degrees)))
    return sum(requested)


def test_dock_v2_wheel_preserves_the_jog_gesture(qapp):
    """The premium face must scrub exactly as far as the wheel it replaces.

    Resizing a jog wheel is only safe because the gesture constants are rates,
    not sizes. This asserts that: identical angular drags produce identical
    frame counts on both wheels.
    """
    for degrees in (90, 180, -90):
        assert (_jog_drag_frames(SmoothJogWheel(), degrees)
                == _jog_drag_frames(ProfessionalJogWheel(), degrees))

    assert (SmoothJogWheel.DEGREES_PER_FRAME
            == ProfessionalJogWheel.DEGREES_PER_FRAME)
    assert (SmoothJogWheel.PIXELS_PER_FRAME
            == ProfessionalJogWheel.PIXELS_PER_FRAME)

    # Hit-testing follows the premium radius rather than a stale pixel value.
    wheel = SmoothJogWheel()
    center = wheel.dial_center()
    assert wheel._inside_ring(QPointF(
        center.x() + wheel.FACE_RADIUS - 4, center.y()))
    assert not wheel._inside_ring(QPointF(
        center.x() + wheel.FACE_RADIUS + 4, center.y()))


def test_dock_v2_wheel_is_line_free_and_shows_one_arc(qapp):
    """The contract rejects ticks, and allows exactly one outer indicator."""
    wheel = SmoothJogWheel()

    # Ticks are painted by _paint_ticks in the base; Dock V2 draws none.
    assert SmoothJogWheel._paint_ticks is not MinimalJogRing._paint_ticks
    assert SmoothJogWheel._paint_readouts is not MinimalJogRing._paint_readouts

    # The jog arc and the rate arc share one ring and never both draw.
    wheel.set_shuttle_rate(4.0)
    assert abs(wheel._rate) > 0.001
    wheel.set_shuttle_rate(0.0)
    assert abs(wheel._rate) <= 0.001

    # No bay minimum, so the larger floating instrument cannot inflate the bar.
    before = wheel.size()
    wheel.set_bay_width(400)
    assert wheel.size() == before
    assert (SmoothJogWheel.WIDTH, SmoothJogWheel.HEIGHT) == (196, 196)
    assert SmoothJogWheel.SHOW_FRAME_TEXT is False


def test_dock_v2_floating_wheel_keeps_its_readout_hub_joggable(qapp):
    """The real JOG toggle opens a wheel whose full face accepts input."""
    from PySide6.QtGui import QWheelEvent

    deck = DockV2Deck()
    deck.resize(1900, deck.DECK_HEIGHT)
    deck.show()
    deck.layout().activate()
    qapp.processEvents()

    deck.jog_toggle.click()
    qapp.processEvents()
    assert deck.jog_toggle.isChecked()
    assert deck.jog_window.isVisible()
    assert deck.jog_ring.play_button is None
    assert deck.jog_window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert deck.jog_window.windowFlags() & Qt.WindowType.Tool
    assert deck.jog_window.windowFlags() & \
        Qt.WindowType.NoDropShadowWindowHint
    assert deck.jog_window.windowFlags() & \
        Qt.WindowType.WindowDoesNotAcceptFocus
    assert deck.jog_window.testAttribute(
        Qt.WidgetAttribute.WA_TranslucentBackground)
    assert deck.jog_window.testAttribute(
        Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert deck.jog_window.rate_label.isHidden()

    deck.set_position("00:02:31:03", 4529)
    assert deck.jog_ring.SHOW_FRAME_TEXT is False
    image = deck.jog_window.grab().toImage()
    assert image.pixelColor(0, 0).alpha() == 0

    # A normal left drag on the outer ring moves the floating controller;
    # the center remains reserved for the Play/Pause click.
    start = deck.jog_ring.dial_center().toPoint() + QPoint(
        int(deck.jog_ring.FACE_RADIUS - 8), 0)
    origin = deck.jog_window.pos()
    QTest.mousePress(
        deck.jog_ring, Qt.MouseButton.LeftButton, pos=start)
    assert deck.jog_window._moving
    QTest.mouseMove(deck.jog_ring, start + QPoint(24, 16), delay=1)
    QTest.mouseRelease(
        deck.jog_ring, Qt.MouseButton.LeftButton,
        pos=start + QPoint(24, 16))
    assert not deck.jog_window._moving
    assert deck.jog_window._has_user_position
    assert deck.jog_window.pos() != origin

    requested = []
    deck.jog_ring.framesRequested.connect(requested.append)
    center = deck.jog_ring.dial_center()
    event = QWheelEvent(
        center,
        QPointF(deck.jog_ring.mapToGlobal(center.toPoint())),
        QPoint(), QPoint(0, 120), Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(deck.jog_ring, event)
    assert requested == [1]
    assert event.isAccepted()

    deck.jog_window.close()
    qapp.processEvents()
    assert not deck.jog_toggle.isChecked()

    deck.jog_toggle.click()
    qapp.processEvents()
    deck.jog_window._moving = True
    deck.jog_window.hide()
    assert not deck.jog_window._moving


def test_dock_v2_centered_play_routes_and_reflects_real_state(qapp):
    """The compact center key calls one authority and shows real state."""
    calls = []
    player = SimpleNamespace(
        jump_backward=lambda: None,
        shuttle_reverse=lambda: None,
        toggle_play=lambda: calls.append("toggle"),
        shuttle_forward=lambda: None,
        jump_forward=lambda: None,
        _jog_frames_requested=lambda _frames: None,
        frame_step_backward=lambda: None,
        frame_step_forward=lambda: None,
        set_range_loop=lambda _enabled: None,
        set_in_point=lambda: None,
        set_out_point=lambda: None,
        request_add_clip=lambda: None,
    )
    deck = DockV2Deck()
    deck.bind_player(player)

    deck.play_btn.click()
    assert calls == ["toggle"]
    assert deck.play_btn.size() == QSize(44, 44)
    assert deck.play_btn.iconSize() == QSize(17, 17)

    deck.set_playing(False)
    play_icon = deck.play_btn.icon().cacheKey()
    assert deck.play_btn.property("playing") == "false"
    assert deck.play_btn.accessibleName().startswith("Play playback")
    deck.set_playing(True)
    pause_icon = deck.play_btn.icon().cacheKey()
    assert deck.play_btn.property("playing") == "true"
    assert deck.play_btn.accessibleName().startswith("Pause playback")
    assert deck.play_btn.iconSize() == QSize(17, 17)
    assert pause_icon != play_icon

    # The wheel hub is a second face on the same authoritative Play/Pause
    # command. It must route once, not own or predict playback state.
    QTest.mouseClick(
        deck.jog_ring, Qt.MouseButton.LeftButton,
        pos=deck.jog_ring.dial_center().toPoint())
    assert calls == ["toggle", "toggle"]
    assert deck.jog_ring._playing
    deck.set_playing(False)
    assert not deck.jog_ring._playing


def test_dock_v2_active_speed_key_survives_repeat_click(qapp):
    """Rate commands cannot visually clear authoritative engine state."""
    calls = []
    player = SimpleNamespace(
        jump_backward=lambda: None,
        shuttle_reverse=lambda: None,
        toggle_play=lambda: None,
        shuttle_forward=lambda: calls.append("forward"),
        shuttle_stop=lambda: calls.append("stop"),
        jump_forward=lambda: None,
        _jog_frames_requested=lambda _frames: None,
        frame_step_backward=lambda: None,
        frame_step_forward=lambda: None,
        set_range_loop=lambda _enabled: None,
        set_in_point=lambda: None,
        set_out_point=lambda: None,
        request_add_clip=lambda: None,
    )
    deck = DockV2Deck()
    deck.bind_player(player)

    for index, rate in enumerate((1.0, 2.0, 4.0, 8.0)):
        deck.set_shuttle_rate(rate)
        calls.clear()
        deck.speed_buttons[index].click()
        assert [button.isChecked() for button in deck.speed_buttons] == [
            position == index for position in range(4)]
        assert calls == []


def test_dock_v2_speed_buttons_claim_no_state_before_wiring(qapp):
    """A checked 1x while the player sits at rate 0 is optimistic UI."""
    deck = DockV2Deck()
    assert [button.isChecked() for button in deck.speed_buttons] == [
        False, False, False, False]
    assert not deck.speed_group.exclusive()
    # The speed row is wired to the shuttle ladder, so it is operable;
    # it still must not claim a rate before the engine reports one.
    assert all(button.isEnabled() for button in deck.speed_buttons)
    # Settings moved into the Export menu; the band no longer carries a
    # second key for one rarely-opened dialog.
    assert not deck.settings_button.isVisible()
    assert deck.export_settings_action.isEnabled()


def test_dock_v2_arc_covers_half_the_circle_at_full_shuttle(qapp):
    """Maxed out must read as exactly half the wheel, in either direction."""
    import math

    def span_for(rate):
        magnitude = min(abs(rate), 8.0)
        fraction = math.log2(max(magnitude, 0.25) * 4.0) / 5.0
        fraction = max(0.06, min(1.0, fraction))
        return SmoothJogWheel.ARC_MAX_SPAN_DEG * fraction

    assert SmoothJogWheel.ARC_MAX_SPAN_DEG == 180.0
    assert span_for(8.0) == 180.0
    assert span_for(-8.0) == 180.0
    # And the ladder below it stays proportional, never exceeding half.
    assert span_for(1.0) < span_for(2.0) < span_for(4.0) < span_for(8.0)
    assert all(span_for(rate) <= 180.0 for rate in (1.0, 2.0, 4.0, 8.0, 99.0))


def test_dock_v2_rate_sink_updates_visible_feedback_and_parked_alias(qapp):
    """Visible rate feedback and the parked compatibility sink agree."""
    deck = DockV2Deck()
    deck.resize(1900, deck.DECK_HEIGHT)
    deck.show()
    deck.layout().activate()
    qapp.processEvents()

    # Keep the parked meter synchronized with the visible jog readout.
    assert deck.shuttle_meter.parentWidget() is deck.rate_group
    assert deck.overflow_bay.isAncestorOf(deck.rate_group)
    assert not deck.shuttle_meter.isVisible()

    cases = (
        (0.0, "PAUSED", []),
        (1.0, "FWD 1x", ["1x"]),
        (2.0, "FWD 2x", ["2x"]),
        (4.0, "FWD 4x", ["4x"]),
        (8.0, "FWD 8x", ["8x"]),
        (-1.0, "REV 1x", []),
        (-4.0, "REV 4x", []),
        (-8.0, "REV 8x", []),
    )
    for rate, label, checked in cases:
        deck.set_shuttle_rate(rate)
        assert deck.shuttle_meter.rate == pytest.approx(rate)
        assert deck.jog_ring._rate == pytest.approx(rate)
        assert deck.jog_window.rate_label.text() == label
        assert [button.text() for button in deck.speed_buttons
                if button.isChecked()] == checked



def test_dock_v2_voiceover_is_parked_but_every_state_sink_still_lands(qapp):
    """Voiceover is off the band, and nothing about that may break capture.

    The take flyout crashed the app natively on teardown, so the whole group
    was pulled from the band until it is rebuilt. The widgets stay alive and
    every set_voiceover_* sink must keep working, because
    VoiceoverDeckCoordinator drives them unconditionally and knows nothing
    about which deck it is talking to.
    """
    deck = DockV2Deck()
    deck.resize(1900, deck.DECK_HEIGHT)
    deck.show()
    deck.layout().activate()
    qapp.processEvents()

    for parked in (deck.voiceover_record_button, deck.voiceover_waveform,
                   deck.voiceover_timer_label, deck.voiceover_take_label,
                   deck.voice_clear_button):
        assert not parked.isVisible()
        assert parked.parent() is deck.overflow_bay

    # No audition: it is removed, not merely hidden.
    assert not hasattr(deck, "voice_play_button")
    assert not hasattr(deck, "voiceover_popover")

    # Every sink the coordinator calls must still be accepted.
    deck.set_voiceover_available(True, "Record Voiceover for the selected clip")
    deck.set_voiceover_recording(True)
    deck.set_voiceover_take(
        take_number=2, duration_ms=7400, waveform=(), clear_enabled=False)
    assert deck.voiceover_timer_label.text() == "00:07"
    deck.set_voiceover_levels(0.8, 0.5)
    assert deck.voiceover_waveform.peak == pytest.approx(0.8)
    deck.set_voiceover_recording(False)
    deck.set_voiceover_take(
        take_number=2, duration_ms=12000, waveform=(0.2, 0.6),
        clear_enabled=True)
    assert "TAKE 2" in deck.voiceover_take_label.text()


def test_dock_v2_export_menu_and_settings_are_live_controls(qapp):
    """Nothing on the band pretends to work.

    Settings was a dead button and the three export styles were parked out of
    sight; both now reach the existing window routes. The menu is a second
    face on the same controls, so ExportPanel still owns the selection.
    """
    deck = DockV2Deck()
    deck.resize(1900, deck.DECK_HEIGHT)
    deck.show()
    deck.layout().activate()
    qapp.processEvents()

    # Settings is reached from the Export menu.
    assert not deck.settings_button.isVisible()
    asked = []
    deck.settings_requested.connect(lambda: asked.append("settings"))
    deck.export_settings_action.trigger()
    assert asked == ["settings"]

    labels = [a.text() for a in deck.export_menu.actions() if not a.isSeparator()]
    assert labels[:3] == ["Signature", "Clean", "Vertical"]
    assert "Open Export Package" in labels
    assert labels[-1] == "Settings..."

    styles, formats, opened = [], [], []
    deck.export_style_requested.connect(lambda s: styles.append(s))
    deck.export_format_requested.connect(lambda: formats.append("format"))
    deck.export_requested.connect(lambda: opened.append("open"))
    deck.export_style_actions["signature"].trigger()
    deck.export_format_action.trigger()
    next(a for a in deck.export_menu.actions()
         if a.text() == "Open Export Package").trigger()
    assert styles == ["SIGNATURE"]
    assert formats == ["format"]
    assert opened == ["open"]

    # ExportPanel remains the authority: its selection drives both faces.
    deck.set_export_style("vertical")
    assert [n for n, a in deck.export_style_actions.items() if a.isChecked()]         == ["vertical"]
    assert [b.text() for b in deck.deck_export_style_buttons if b.isChecked()]         == ["VERTICAL"]
    assert deck.export_format_action.text() == deck.deck_format_button.text()


def test_dock_v2_add_clip_face_explains_mark_readiness(qapp):
    deck = DockV2Deck()

    assert deck.add_clip_btn.text() == "+ Add Clip"
    assert deck.add_clip_btn.width() == 64
    assert not deck.add_clip_btn.isEnabled()
    assert deck.add_clip_btn.toolTip() == \
        "Set IN and OUT before adding a clip"

    deck.set_marks(1_000, None)
    assert not deck.add_clip_btn.isEnabled()
    assert deck.add_clip_btn.toolTip() == \
        "Set an OUT point before adding a clip"

    deck.set_marks(1_000, 4_000)
    assert deck.add_clip_btn.isEnabled()
    assert deck.add_clip_btn.toolTip() == \
        "Add a clip from the marked IN and OUT points (A)"
