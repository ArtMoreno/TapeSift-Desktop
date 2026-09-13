"""Renaming the project you are working in, from File > Rename Project.

Renaming touches the file on disk, so the session must be saved and closed
around it and then reopened. That plumbing is exactly where clips get lost,
the library index ends up pointing at a path that no longer exists, or the
user lands back on the home screen wondering what happened. These pin the
whole round trip.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.database.connection import PROJECT_FILE_EXTENSION  # noqa: E402
from tapesift.models.clip import Clip  # noqa: E402
from tapesift.services import library_service, recovery_service  # noqa: E402
from tapesift.services.project_service import ProjectSession  # noqa: E402
from tapesift.ui_v2.main_window import MainWindowV2  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def no_modal_dialogs(monkeypatch):
    """A modal box has no one to dismiss it offscreen, so it hangs forever."""
    for name in ("warning", "information", "critical", "question"):
        monkeypatch.setattr(QMessageBox, name,
                            staticmethod(lambda *a, **k:
                                         QMessageBox.StandardButton.Yes))


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    settings = AppSettings()
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "out")
    monkeypatch.setattr(settings, "save", lambda *a, **k: None)
    recovery_service.mark_closed()
    win = MainWindowV2(settings)

    session = ProjectSession.create("Vs Duke", tmp_path, tmp_path / "out")
    session.add_clip(Clip(start_ms=1000, end_ms=6000, clip_title="Play 001"))
    session.add_clip(Clip(start_ms=9000, end_ms=15000, clip_title="Play 002"))
    session.save()
    win._activate_session(session)
    yield win, settings, tmp_path
    win._close_project()


def answer(monkeypatch, text: str, ok: bool = True):
    monkeypatch.setattr(QInputDialog, "getText",
                        staticmethod(lambda *a, **k: (text, ok)))


class TestRenameFromTheFileMenu:
    def test_the_action_is_disabled_until_a_project_is_open(self, qapp,
                                                            tmp_path,
                                                            monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata2"))
        recovery_service.mark_closed()
        win = MainWindowV2(AppSettings())
        assert not win.rename_action.isEnabled()

    def test_renames_the_file_and_keeps_the_project_open(self, window,
                                                         monkeypatch):
        win, _, tmp_path = window
        answer(monkeypatch, "Vs Duke 2025")
        win._rename_open_project()

        assert (tmp_path / f"Vs Duke 2025{PROJECT_FILE_EXTENSION}").is_file()
        assert not (tmp_path / f"Vs Duke{PROJECT_FILE_EXTENSION}").exists()
        assert win.session is not None, "the project must stay open"
        assert win.session.project.name == "Vs Duke 2025"

    def test_clips_survive_the_round_trip(self, window, monkeypatch):
        win, _, _ = window
        answer(monkeypatch, "Vs Duke 2025")
        win._rename_open_project()
        assert [c.clip_title for c in win.session.clips] == ["Play 001",
                                                             "Play 002"]

    def test_recent_list_follows_the_new_path(self, window, monkeypatch):
        win, settings, tmp_path = window
        answer(monkeypatch, "Vs Duke 2025")
        win._rename_open_project()
        new_path = str(tmp_path / f"Vs Duke 2025{PROJECT_FILE_EXTENSION}")
        assert new_path in settings.recent_projects
        assert not any("Vs Duke." in p for p in settings.recent_projects)

    def test_library_index_points_at_the_new_path(self, window, monkeypatch):
        """The catalog is shared app-wide state, so look only at this test's
        own folder - other tests index projects of their own."""
        win, _, tmp_path = window
        answer(monkeypatch, "Vs Duke 2025")
        win._rename_open_project()
        mine = [p for _, p in library_service.projects()
                if p.startswith(str(tmp_path))]
        assert not any(p.endswith(f"Vs Duke{PROJECT_FILE_EXTENSION}")
                       for p in mine), mine

    def test_cancelling_changes_nothing(self, window, monkeypatch):
        win, _, tmp_path = window
        answer(monkeypatch, "Whatever", ok=False)
        win._rename_open_project()
        assert win.session.project.name == "Vs Duke"
        assert (tmp_path / f"Vs Duke{PROJECT_FILE_EXTENSION}").is_file()

    def test_an_unchanged_name_is_not_a_rename(self, window, monkeypatch):
        win, _, tmp_path = window
        answer(monkeypatch, "Vs Duke")
        win._rename_open_project()
        assert (tmp_path / f"Vs Duke{PROJECT_FILE_EXTENSION}").is_file()

    def test_an_illegal_name_leaves_the_project_open(self, window, monkeypatch):
        """The rename fails after the session was closed - the user must not
        be dumped on the home screen with their work apparently gone."""
        win, _, tmp_path = window
        answer(monkeypatch, 'Vs Duke: "2025"')
        win._rename_open_project()
        assert win.session is not None
        assert win.session.project.name == "Vs Duke"
        assert [c.clip_title for c in win.session.clips] == ["Play 001",
                                                             "Play 002"]
