"""Film Room start screen: drag / loading / error state machine (Phase 2)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.ui_v2.start_screen import StartScreenV2  # noqa: E402


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def screen(qapp):
    settings = AppSettings(onboarding_seen=True, recent_projects=[])
    settings.save = lambda *a, **k: None
    return StartScreenV2(settings)


def test_starts_idle_with_the_default_drop_content(screen):
    assert screen._load_state == "idle"
    assert screen._drop_default.isVisible() or not screen._drop_status.isVisible()
    assert screen._drop_heading.text() == "DROP A GAME FILM HERE"


def test_drag_over_switches_to_release_prompt(screen):
    screen.set_drag_active(True)
    assert screen._load_state == "dragover"
    assert screen._drop_heading.text() == "RELEASE TO START A PROJECT"
    assert screen.drop_zone.property("dragover") == "true"

    screen.set_drag_active(False)
    assert screen._load_state == "idle"
    assert screen._drop_heading.text() == "DROP A GAME FILM HERE"


def test_loading_shows_the_filename_and_a_busy_bar_no_actions(screen):
    screen.begin_loading("Game_Film.mp4")
    assert screen._load_state == "loading"
    assert "Game_Film.mp4" in screen._status_title.text()
    assert screen._status_bar.maximum() == 0            # indeterminate
    assert screen._status_bar.isVisibleTo(screen._drop_status)
    assert not screen._retry_btn.isVisibleTo(screen._drop_status)
    assert not screen._logs_btn.isVisibleTo(screen._drop_status)


def test_error_shows_message_and_recovery_actions(screen):
    screen.show_load_error("Game_Film.mov", "The file could not be read.")
    assert screen._load_state == "error"
    assert "Game_Film.mov" in screen._status_title.text()
    assert "could not be read" in screen._status_detail.text()
    assert screen._retry_btn.isVisibleTo(screen._drop_status)
    assert screen._logs_btn.isVisibleTo(screen._drop_status)
    assert not screen._status_bar.isVisibleTo(screen._drop_status)


def test_clear_returns_to_the_default_content(screen):
    screen.begin_loading("x.mp4")
    screen.clear_load_state()
    assert screen._load_state == "idle"
    assert screen._drop_heading.text() == "DROP A GAME FILM HERE"


def test_recovery_buttons_emit_signals(qapp, screen):
    retried = []
    logs = []
    screen.retry_video_requested.connect(lambda: retried.append(1))
    screen.open_logs_requested.connect(lambda: logs.append(1))
    screen.show_load_error("x.mov", "nope")
    screen._retry_btn.click()
    screen._logs_btn.click()
    assert retried == [1]
    assert logs == [1]


def test_drag_active_is_ignored_while_loading(screen):
    """A drag entering during a probe must not clobber the loading UI."""
    screen.begin_loading("x.mp4")
    screen.set_drag_active(True)
    assert screen._load_state == "loading"
    assert "x.mp4" in screen._status_title.text()


def test_bad_video_ends_in_error_state_no_project(qapp, tmp_path, monkeypatch):
    """End-to-end: dropping an unreadable file shows the error and creates
    no project (probe-first). Exercises the real MetadataWorker + wiring."""
    from tapesift.core.config import AppSettings
    from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
    from tapesift.ui_v2.main_window import MainWindowV2
    from pathlib import Path

    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False,
                           recent_projects=[])
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "out")
    settings.save = lambda *a, **k: None
    window = MainWindowV2(settings)

    window._start_project_from_video(Path(tmp_path / "does_not_exist.mp4"))
    # Let the probe worker run and emit.
    for _ in range(200):
        qapp.processEvents()
        if window.start_screen._load_state == "error":
            break
        window._probe_worker.wait(20)

    assert window.start_screen._load_state == "error"
    assert window.session is None          # no orphan project
    window.hide()


def test_configured_new_project_also_probes_before_creation(
        qapp, tmp_path, monkeypatch):
    """The Iteration 4 dialog path keeps the same no-orphan safety rule."""
    from pathlib import Path

    from tapesift.core.config import AppSettings
    from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
    from tapesift.ui_v2.main_window import MainWindowV2

    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False,
                           recent_projects=[])
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "out")
    settings.save = lambda *a, **k: None
    window = MainWindowV2(settings)

    missing = Path(tmp_path / "does_not_exist.mp4")
    window._start_configured_project(
        "Configured Game",
        str(tmp_path),
        str(tmp_path / "out" / "Configured Game"),
        str(missing),
    )
    for _ in range(200):
        qapp.processEvents()
        if window.start_screen._load_state == "error":
            break
        window._probe_worker.wait(20)

    assert window.start_screen._load_state == "error"
    assert window.session is None
    assert not (tmp_path / "Configured Game.tapesift").exists()
    window.hide()
