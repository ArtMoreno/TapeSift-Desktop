"""Home screen as a work queue.

The card is only useful if "logged" means what the logging workflow means:
a clip with play details on it. These pin that down, plus the coordinate-free
bits of the All Projects filter, so a schema change can't quietly turn every
project into "0 of N logged".
"""

from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tapesift.database.connection import PROJECT_FILE_EXTENSION  # noqa: E402
from tapesift.models.clip import Clip  # noqa: E402
from tapesift.services.project_service import ProjectSession  # noqa: E402
from tapesift.ui_core.start_screen import ProjectInfo, load_project_info  # noqa: E402
from tapesift.app import _project_from_args  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def qapp_settings(qapp, tmp_path):
    from tapesift.core.config import AppSettings
    settings = AppSettings()
    settings.recent_projects = []
    settings.save(tmp_path / "settings.json")
    return settings


@pytest.fixture
def project(tmp_path):
    session = ProjectSession.create("Home vs Away", tmp_path,
                                    tmp_path / "exports")
    path = tmp_path / f"Home vs Away{PROJECT_FILE_EXTENSION}"
    for index in range(4):
        session.add_clip(Clip(clip_title=f"Play {index}",
                              start_ms=index * 30_000,
                              end_ms=index * 30_000 + 12_000))
    session.save()          # the card reads the file, not the live session
    return session, path


class TestLoggedCount:
    def test_clips_without_details_are_unlogged(self, project):
        _, path = project
        info = load_project_info(path)
        assert info.clip_count == 4
        assert info.logged_count == 0
        assert info.unlogged == 4
        assert info.progress_pct == 0

    def test_details_make_a_clip_logged(self, project):
        session, path = project
        clip = session.clips[0]
        clip.details = {"player_name": "Damon Wilson"}
        session.save()
        info = load_project_info(path)
        assert info.logged_count == 1
        assert info.unlogged == 3
        assert info.progress_pct == 25

    def test_empty_details_json_does_not_count(self, project):
        """'{}' is what an untouched clip stores - it is not progress."""
        _, path = project
        conn = sqlite3.connect(path)
        conn.execute("UPDATE clips SET details_json = '{}'")
        conn.commit()
        conn.close()
        assert load_project_info(path).logged_count == 0

    def test_card_prefers_an_informative_thumbnail_over_a_black_separator(
            self, project, tmp_path):
        session, path = project
        black = tmp_path / "black.jpg"
        field = tmp_path / "field.jpg"
        black.write_bytes(b"tiny")
        field.write_bytes(b"detailed-field-frame" * 100)
        session.clips[0].thumbnail_path = str(black)
        session.clips[1].thumbnail_path = str(field)
        session.save()
        assert load_project_info(path).thumbnail == str(field)

    def test_missing_project_reports_gracefully(self, tmp_path):
        info = load_project_info(tmp_path / f"gone{PROJECT_FILE_EXTENSION}")
        assert not info.exists
        assert info.unlogged == 0
        assert info.progress_pct == 0


class TestProgressMath:
    def test_no_clips_is_not_a_division_error(self):
        assert ProjectInfo(path=None, clip_count=0).progress_pct == 0

    def test_fully_logged_reads_complete(self):
        info = ProjectInfo(path=None, clip_count=10, logged_count=10)
        assert info.unlogged == 0
        assert info.progress_pct == 100


class TestProjectLaunchCompatibility:
    def test_accepts_current_tapesift_projects(self, tmp_path):
        project = tmp_path / "Game.tapesift"
        project.touch()
        assert _project_from_args([str(project)]) == project

    def test_rejects_the_retired_clipforge_extension(self, tmp_path):
        """All projects were converted, so .clipforge is not ours any more."""
        project = tmp_path / "Game.clipforge"
        project.touch()
        assert _project_from_args([str(project)]) is None


class TestAttribution:
    """One source of truth for the author's name.

    It appears on the home screen, in About, in the .exe's file properties
    and in the installer; defining it four times is how three of them end
    up saying something slightly different.
    """

    def test_lines_are_built_from_the_names(self):
        import tapesift
        assert tapesift.AUTHOR in tapesift.AUTHOR_LINE
        assert tapesift.AUTHOR_LEGAL in tapesift.COPYRIGHT_LINE
        assert tapesift.COPYRIGHT_YEAR in tapesift.COPYRIGHT_LINE

    def test_home_screen_shows_the_byline_and_copyright(self, qapp_settings):
        from tapesift import AUTHOR_LINE, COPYRIGHT_LINE
        from tapesift.ui_core.start_screen import StartScreen
        from PySide6.QtWidgets import QLabel
        screen = StartScreen(qapp_settings)
        texts = [w.text() for w in screen.findChildren(QLabel) if w.text()]
        assert AUTHOR_LINE in texts
        assert COPYRIGHT_LINE in texts
