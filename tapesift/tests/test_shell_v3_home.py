"""Locked Home states and intake behavior for TapeSift Shell V3."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import (  # noqa: E402
    QEvent, QMimeData, QPoint, QPointF, QSize, QUrl,
)
from PySide6.QtGui import QFontDatabase, QTextDocumentFragment  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog, QPushButton  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.services.project_service import ProjectSession  # noqa: E402
from tapesift.ui_v2.fonts import load_v2_fonts  # noqa: E402
from tapesift.ui_v3.main_window import MainWindowV3  # noqa: E402
from tapesift.ui_v3.start_screen import (  # noqa: E402
    NewProjectDialog,
    ProjectCardV3, ResumeCardV3,
    StartScreenV3,
)
from tapesift.ui_v3.theme import stylesheet  # noqa: E402


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    load_v2_fonts()
    for font_path in (
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\seguisb.ttf",
        r"C:\Windows\Fonts\segmdl2.ttf",
        r"C:\Windows\Fonts\consola.ttf",
    ):
        QFontDatabase.addApplicationFont(font_path)
    previous_style = app.styleSheet()
    app.setStyleSheet(stylesheet())
    try:
        yield app
    finally:
        app.setStyleSheet(previous_style)


def _settings(tmp_path: Path, *, recent_projects=None) -> AppSettings:
    settings = AppSettings(
        onboarding_seen=True,
        recent_projects=list(recent_projects or []),
    )
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "exports")
    settings.save = lambda *args, **kwargs: None
    return settings


def _settle(qapp: QApplication) -> None:
    for _ in range(5):
        qapp.processEvents()


def test_first_run_keeps_intake_actions_and_full_field_drop_target(qapp, tmp_path):
    screen = StartScreenV3(_settings(tmp_path))
    screen.resize(1708, 830)
    screen.show()
    _settle(qapp)
    assert screen.drop_zone.isVisibleTo(screen)
    assert screen.drop_zone.acceptDrops()
    assert screen.drop_zone.minimumHeight() >= 330
    assert QTextDocumentFragment.fromHtml(screen._drop_heading.text()).toPlainText() == "Start with\nyour film."
    assert not screen.drop_zone.logo.isNull()
    assert not screen.drop_zone.field.isNull()
    assert not screen._recent_col.isVisibleTo(screen)
    for name in ("V3HomeNewProject", "V3HomeOpenExisting"):
        button = screen.findChild(QPushButton, name)
        assert button.isVisibleTo(screen) and button.isEnabled()
    assert screen._drop_project_hint.isVisibleTo(screen.drop_zone)
    screen.begin_loading("film.mp4")
    assert screen._drop_status.isVisibleTo(screen)
    screen.show_load_error("film.mp4", "Unreadable file")
    assert screen._retry_btn.isVisibleTo(screen)
    assert screen._logs_btn.isVisibleTo(screen)
    screen.clear_load_state()
    assert not screen._drop_status.isVisibleTo(screen)
    screen.hide()


def test_visible_empty_home_actions_are_keyboard_accessible(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog
    screen = StartScreenV3(_settings(tmp_path))
    screen.resize(1360, 760)
    screen.show()
    calls = []
    monkeypatch.setattr(NewProjectDialog, "exec", lambda dialog: calls.append("new") or QDialog.DialogCode.Rejected)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (calls.append("open") or "", ""))
    _settle(qapp)
    for button in (screen._drop_new_button, screen._empty_open_button):
        assert button.isVisibleTo(screen) and button.isEnabled()
        assert button.focusPolicy() != Qt.FocusPolicy.NoFocus
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
    assert calls == ["new", "open"]
    assert not list(tmp_path.glob("*.tapesift"))
    screen.close()


def test_returning_home_restores_resume_first_layout(qapp, tmp_path):
    session = ProjectSession.create("EXAMPLE HOME O VS. AWAY D", tmp_path, tmp_path / "exports")
    session.save()
    screen = StartScreenV3(_settings(tmp_path, recent_projects=[str(session.db_path)]))
    screen.resize(1360, 760)
    screen.show()
    _settle(qapp)
    cards = screen.findChildren(ResumeCardV3)
    assert len(cards) == 1 and cards[0].featured
    assert cards[0]._title_label.text() == session.project.name
    opened = []
    screen.open_project_requested.connect(opened.append)
    cards[0]._resume_button.click()
    assert opened == [str(session.db_path)]
    assert not screen.drop_zone.isVisibleTo(screen)
    screen.begin_loading("new-film.mp4")
    assert screen._drop_status.isVisibleTo(screen)
    screen.hide()
    session.close()


def test_removing_the_last_recent_restores_the_visible_film_drop_target(qapp, tmp_path):
    session = ProjectSession.create("Last recent", tmp_path, tmp_path / "exports")
    project_path = session.db_path
    session.save()
    session.close()
    settings = _settings(tmp_path, recent_projects=[str(project_path)])
    screen = StartScreenV3(settings)
    screen.resize(1360, 760)
    screen.show()
    _settle(qapp)
    assert not screen.drop_zone.isVisibleTo(screen)

    settings.recent_projects.clear()
    screen.refresh_recent()
    _settle(qapp)
    assert not screen._recent_col.isVisibleTo(screen)
    assert screen.drop_zone.isVisibleTo(screen)
    assert screen._drop_heading.isVisibleTo(screen)
    assert screen.drop_zone.acceptDrops()
    assert screen.findChild(QPushButton, "V3HomeNewProject").isVisibleTo(screen)
    screen.close()


def test_returning_home_shows_featured_project_and_scrolls_to_every_project(qapp, tmp_path):
    sessions = [ProjectSession.create(f"GAME {i+1}", tmp_path, tmp_path / "exports") for i in range(5)]
    for session in sessions:
        session.save()
    screen = StartScreenV3(_settings(tmp_path, recent_projects=[str(s.db_path) for s in sessions]))
    screen.resize(1696, 824)
    screen.show()
    _settle(qapp)
    try:
        cards = screen.findChildren(ResumeCardV3)
        assert len(cards) == 5 and sum(card.featured for card in cards) == 1
        for width, height in ((1696, 824), (1248, 640)):
            screen.resize(width, height)
            _settle(qapp)
            for card in cards:
                screen._home_scroll.ensureWidgetVisible(card)
                _settle(qapp)
                assert card._resume_button.isVisibleTo(screen)
                assert card._overflow_button.isVisibleTo(screen)
                assert card._title_label.height() >= card._title_label.fontMetrics().height()
                assert card._counts.text() == "0 of 0 plays logged"
                assert card.rect().contains(card._resume_button.geometry())
            assert screen._home_scroll.verticalScrollBar().maximum() > 0
    finally:
        screen.hide()
        for session in sessions:
            session.close()


def test_home_action_rail_and_featured_footer_fit_with_two_recent_projects(qapp, tmp_path):
    sessions = [ProjectSession.create(f"MIAMI O VS. STANFORD D {2025+i}", tmp_path, tmp_path / "exports") for i in range(3)]
    for session in sessions:
        session.save()
    screen = StartScreenV3(_settings(tmp_path, recent_projects=[str(s.db_path) for s in sessions]))
    screen.show()
    try:
        for size in ((1690, 852), (1280, 712)):
            screen.resize(*size)
            _settle(qapp)
            cards = screen.findChildren(ResumeCardV3)
            featured = next(card for card in cards if card.featured)
            button = featured._resume_button
            assert featured.rect().contains(button.geometry())
            assert button.x() > featured.width() // 2
            recent = [card for card in cards if not card.featured]
            assert abs(recent[0].width() - recent[1].width()) <= 2
            viewport = screen._home_scroll.viewport()
            for card in cards:
                screen._home_scroll.ensureWidgetVisible(card._resume_button)
                _settle(qapp)
                rect = card._resume_button.rect().translated(card._resume_button.mapTo(viewport, QPoint()))
                assert viewport.rect().contains(rect)
            assert screen._recent_col.isAncestorOf(screen._view_all_projects)
            assert screen._view_all_projects.isVisibleTo(screen)
    finally:
        screen.close()
        for session in sessions:
            session.close()


def test_drag_over_matches_locked_release_state(qapp, tmp_path):
    screen = StartScreenV3(_settings(tmp_path))
    screen.resize(1708, 830)
    screen.show()
    screen.set_drag_active(True, "EXAMPLE HOME O VS. AWAY D.mp4")
    _settle(qapp)

    assert screen._load_state == "dragover"
    assert screen.drop_zone.property("dragover") == "true"
    assert screen._drop_heading.text() == "RELEASE TO START A NEW PROJECT"
    assert screen._accepted_file_name.text() == \
        "EXAMPLE HOME O VS. AWAY D.mp4"
    assert screen._accepted_file.isVisibleTo(screen.drop_zone)
    assert not screen._drop_new_button.isVisibleTo(screen.drop_zone)
    assert screen._drop_heading.property("dragover") == "true"
    assert not screen._drop_project_hint.isVisibleTo(screen.drop_zone)

    screen.set_drag_active(False)
    _settle(qapp)
    assert screen._load_state == "idle"
    assert QTextDocumentFragment.fromHtml(screen._drop_heading.text()).toPlainText() == "Start with\nyour film."
    assert not screen._accepted_file.isVisibleTo(screen.drop_zone)
    assert screen._drop_project_hint.isVisibleTo(screen.drop_zone)
    screen.hide()


class _DropEvent:
    def __init__(self, event_type: QEvent.Type, path: Path) -> None:
        self._type = event_type
        self._mime = QMimeData()
        self._mime.setUrls([QUrl.fromLocalFile(str(path))])
        self.accepted = False

    def type(self):
        return self._type

    def mimeData(self):  # noqa: N802
        return self._mime

    def acceptProposedAction(self):  # noqa: N802
        self.accepted = True

    def accept(self):
        self.accepted = True


def test_tapesift_drop_opens_existing_project(qapp, tmp_path):
    project = tmp_path / "Existing.tapesift"
    project.touch()
    screen = StartScreenV3(_settings(tmp_path))
    opened: list[str] = []
    screen.open_project_requested.connect(opened.append)

    event = _DropEvent(QEvent.Type.Drop, project)
    assert screen.eventFilter(screen.drop_zone, event)
    _settle(qapp)
    assert event.accepted
    assert opened == [str(project)]


def test_video_drop_prefills_new_project_dialog(
        qapp, tmp_path, monkeypatch):
    video = tmp_path / "Game Film.mp4"
    video.touch()
    screen = StartScreenV3(_settings(tmp_path))
    created: list[tuple[str, str, str, str]] = []
    screen.new_project_with_video_requested.connect(
        lambda *values: created.append(tuple(values)))

    class _AcceptedDialog:
        def __init__(self, settings, parent=None) -> None:
            self.video_path = None
            self.project_name = "Game Film"
            self.project_folder = str(tmp_path)
            self.output_folder = str(tmp_path / "exports" / "Game Film")

        def set_video_path(self, value) -> None:
            self.video_path = Path(value)

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        "tapesift.ui_v3.start_screen.NewProjectDialog",
        _AcceptedDialog,
    )
    event = _DropEvent(QEvent.Type.Drop, video)
    assert screen.eventFilter(screen.drop_zone, event)
    _settle(qapp)
    assert event.accepted
    assert created == [(
        "Game Film",
        str(tmp_path),
        str(tmp_path / "exports" / "Game Film"),
        str(video),
    )]


def test_populated_home_accepts_video_drop_across_the_home_surface(
        qapp, tmp_path):
    session = ProjectSession.create(
        "Existing", tmp_path, tmp_path / "exports")
    session.save()
    video = tmp_path / "New Game.mp4"
    video.touch()
    window = MainWindowV3(
        _settings(
            tmp_path,
            recent_projects=[str(tmp_path / "Existing.tapesift")],
        ),
        workspace_state_path=tmp_path / "shell-v3.json",
    )
    window._screen_fit_done = True
    window.resize(1280, 760)
    window.show()
    window.stack.setCurrentWidget(window.start_screen)
    _settle(qapp)

    class _WindowDropEvent(_DropEvent):
        def position(self):
            return QPointF(
                window.start_screen.mapTo(window, QPoint(640, 320)))

    event = _WindowDropEvent(QEvent.Type.Drop, video)
    try:
        assert not window.start_screen.drop_zone.isVisibleTo(window)
        assert window._v3_home_drop_path(event) == video
    finally:
        window._app_closing = True
        window.player.unload()
        window.hide()
        session.close()


def test_loading_state_cannot_be_replaced_by_drag_feedback(qapp, tmp_path):
    screen = StartScreenV3(_settings(tmp_path))
    screen.begin_loading("Game Film.mp4")
    screen.set_drag_active(True, "Another.mp4")
    assert screen._load_state == "loading"
    assert "Game Film.mp4" in screen._status_title.text()


def test_main_window_installs_v3_home_and_page_specific_height_floor(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(
        MainWindowV3,
        "_check_recovery",
        lambda self: None,
    )
    window = MainWindowV3(
        _settings(tmp_path),
        workspace_state_path=tmp_path / "shell-v3.json",
    )
    window._screen_fit_done = True
    window.resize(1479, 640)
    window.show()
    window.stack.setCurrentWidget(window.start_screen)
    _settle(qapp)

    assert isinstance(window.start_screen, StartScreenV3)
    assert window.minimumHeight() == 640
    assert window.height() == 640
    assert not window.statusBar().isVisible()
    assert window._centered_menu_shell._mode == "home"
    assert not hasattr(window._centered_menu_shell, "runtime_metrics")
    home_host = window._centered_menu_shell._left_hosts["home"]
    assert home_host.x() == 38

    window.stack.setCurrentWidget(window.workspace)
    _settle(qapp)
    assert window.minimumWidth() == 1248
    assert window.minimumHeight() == 608
    assert window.height() >= 608
    assert not hasattr(window._centered_menu_shell, "runtime_metrics")

    window.stack.setCurrentWidget(window.start_screen)
    _settle(qapp)
    assert window.minimumWidth() == 1120
    assert window.minimumHeight() == 640

    window._app_closing = True
    window.player.unload()
    window.hide()


def test_first_run_brand_header_leaves_intake_inside_scroll_view(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowV3, "_check_recovery", lambda self: None)
    window = MainWindowV3(_settings(tmp_path), workspace_state_path=tmp_path / "shell-v3.json")
    window._screen_fit_done = True
    window.resize(1708, 881)
    window.show()
    _settle(qapp)
    try:
        screen = window.start_screen
        assert window._centered_menu_shell.height() == 88
        assert screen.mapTo(window, QPoint()) == QPoint(0, 88)
        assert screen.drop_zone.isVisibleTo(window)
        assert screen._home_scroll.isAncestorOf(screen.drop_zone)
        assert not screen.home_brand_lockup.pixmap().isNull()
        assert not screen.home_navigation_chip.icon().isNull()
        assert window.contentsMargins().left() == 0
        assert not window._v3_window_bezel.isVisible()
        small_title_heights = []
        for size in ((1120, 640), (1708, 881), (1120, 640)):
            window.resize(*size)
            _settle(qapp)
            viewport = screen._home_scroll.viewport()
            for widget in (screen._drop_heading, screen._drop_new_button,
                           screen._empty_open_button, screen._drop_project_hint):
                bounds = widget.rect().translated(widget.mapTo(viewport, QPoint()))
                assert viewport.rect().contains(bounds)
            if size[0] == 1120:
                small_title_heights.append(screen._drop_heading.height())
        assert small_title_heights[0] == small_title_heights[1]
    finally:
        window._app_closing = True
        window.player.unload()
        window.hide()


def test_all_home_new_project_actions_use_v3_and_cancel_emits_nothing(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    screen = StartScreenV3(_settings(tmp_path))
    screen.resize(1360, 760)
    screen.show()
    emitted = []
    opened = []
    screen.new_project_with_video_requested.connect(lambda *values: emitted.append(values))
    def cancel(dialog):
        opened.append(type(dialog))
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(NewProjectDialog, "exec", cancel)
    QTest.mouseClick(screen.findChild(QPushButton, "V3HomeNewProject"), Qt.MouseButton.LeftButton)
    QTest.mouseClick(screen._drop_new_button, Qt.MouseButton.LeftButton)
    screen._build_add_project_row().click()
    screen.queue_dropped_path(tmp_path / "cancelled.mp4")
    _settle(qapp)
    assert opened == [NewProjectDialog] * 4
    assert emitted == []
    assert not list(tmp_path.glob("*.tapesift"))
    screen.hide()


def test_v3_intake_preserves_validation_custom_fields_and_browse_cancel(qapp, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    from PySide6.QtTest import QTest
    dialog = NewProjectDialog(_settings(tmp_path))
    dialog.show()
    _settle(qapp)
    assert not dialog.create_button.isEnabled()
    first, second = tmp_path / "film-one.mp4", tmp_path / "film-two.mp4"
    first.touch()
    second.touch()
    dialog.set_video_path(first)
    assert dialog.project_name == "film-one"
    assert dialog.output_folder == str(tmp_path / "exports" / "film-one")
    assert dialog.create_button.isEnabled()
    dialog.project_name_edit.selectAll()
    QTest.keyClicks(dialog.project_name_edit, "Custom game")
    dialog.output_folder_edit.selectAll()
    QTest.keyClicks(dialog.output_folder_edit, str(tmp_path / "custom-export"))
    dialog.set_video_path(second)
    assert dialog.project_name == "Custom game"
    assert dialog.output_folder == str(tmp_path / "custom-export")
    assert dialog._film_heading.text() == second.name
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: "")
    dialog.drop_zone.browse_button.click()
    dialog.project_folder_browse.click()
    dialog.output_folder_browse.click()
    assert dialog.video_path == second
    assert dialog.project_folder == str(tmp_path)
    assert dialog.output_folder == str(tmp_path / "custom-export")
    dialog.project_folder_edit.clear()
    assert not dialog.create_button.isEnabled()
    dialog.project_folder_edit.setText(str(tmp_path))
    dialog.set_video_path(tmp_path / "missing.mp4")
    assert not dialog.create_button.isEnabled()
    assert dialog.video_path is None
    assert not list(tmp_path.glob("*.tapesift"))
    dialog.reject()


def test_home_library_navigation_keeps_existing_route(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    monkeypatch.setattr(MainWindowV3, "_check_recovery", lambda self: None)
    window = MainWindowV3(_settings(tmp_path), workspace_state_path=tmp_path / "shell.json")
    window.show()
    _settle(qapp)
    button = window.start_screen.library_button
    assert button.text() == "Library"
    assert not button.icon().isNull()
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    _settle(qapp)
    assert window._centered_menu_shell._mode == "library"
    window._app_closing = True
    window.player.unload()
    window.hide()


def test_home_masthead_actions_work_in_empty_and_returning_states_at_minimum_size(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QFileDialog

    session = ProjectSession.create("Recent game", tmp_path, tmp_path / "exports")
    session.save()
    project_path = str(session.db_path)
    session.close()
    calls = []
    monkeypatch.setattr(MainWindowV3, "_check_recovery", lambda self: None)
    monkeypatch.setattr(MainWindowV3, "_open_project", lambda self, path: calls.append(("open", path)))
    monkeypatch.setattr(MainWindowV3, "_open_settings", lambda self: calls.append(("settings", None)))
    monkeypatch.setattr(NewProjectDialog, "exec", lambda dialog: calls.append(("new", None)) or QDialog.DialogCode.Rejected)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (project_path, ""))
    settings = _settings(tmp_path)
    window = MainWindowV3(settings, workspace_state_path=tmp_path / "shell.json")
    window._screen_fit_done = True
    window.resize(1120, 640)
    window.show()
    try:
        for recent_paths in ([], [project_path]):
            settings.recent_projects = recent_paths
            window.start_screen.refresh_recent()
            _settle(qapp)
            bar = window._centered_menu_shell
            nav = bar._film_navigation
            new = window.findChild(QPushButton, "V3HomeNewProject")
            existing = window.findChild(QPushButton, "V3HomeOpenExisting")
            for button in (new, existing, window.start_screen.library_button, window.start_screen.settings_button):
                assert button.isVisibleTo(window) and button.isEnabled()
                bounds = button.rect().translated(button.mapTo(bar, QPoint()))
                assert bar.rect().contains(bounds)
            assert not nav.geometry().intersects(bar.right_host.geometry())
            assert not nav.geometry().intersects(bar._left_hosts["home"].geometry())
            QTest.mouseClick(new, Qt.MouseButton.LeftButton)
            QTest.mouseClick(existing, Qt.MouseButton.LeftButton)
            QTest.mouseClick(window.start_screen.settings_button, Qt.MouseButton.LeftButton)
        assert calls == [("new", None), ("open", project_path), ("settings", None)] * 2
    finally:
        window._app_closing = True
        window.player.unload()
        window.hide()


def test_refresh_and_dispose_cancel_pending_hero_decode(qapp, tmp_path, monkeypatch):
    import sys
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QProcess
    from tapesift.models.clip import Clip

    source = tmp_path / "source.mp4"
    source.write_bytes(b"unchanged source")
    session = ProjectSession.create("Pending preview", tmp_path, tmp_path / "exports")
    session.project.source_video_path = str(source)
    session.add_clip(Clip(clip_title="Play", start_ms=0, end_ms=1000))
    session.save()
    project_path = session.db_path
    session.close()
    original = (project_path.read_bytes(), source.read_bytes())
    monkeypatch.setattr("tapesift.ui_v3.home_preview.ffmpeg_service.find_executable", lambda *_: sys.executable)
    monkeypatch.setattr("tapesift.ui_v3.home_preview.ffmpeg_service.build_thumbnail_command",
                        lambda *a, **k: [sys.executable, "-c", "import time; time.sleep(20)", "-update", "1", "pipe:1"])
    screen = StartScreenV3(_settings(tmp_path, recent_projects=[str(project_path)]))
    first = screen._film_cards[0]._preview_loader
    assert first._process.waitForStarted(3000)
    delivered = []
    first.ready.connect(delivered.append)
    screen.refresh_recent()
    assert first._process.state() == QProcess.ProcessState.NotRunning
    second = screen._film_cards[0]._preview_loader
    assert second._process.waitForStarted(3000)
    second.ready.connect(delivered.append)
    states = []
    second._process.stateChanged.connect(states.append)
    screen.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not shiboken6.isValid(screen)
    assert states[-1] == QProcess.ProcessState.NotRunning
    assert delivered == []
    assert (project_path.read_bytes(), source.read_bytes()) == original
