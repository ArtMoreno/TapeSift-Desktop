"""The menu route to the phone companion."""

from __future__ import annotations

import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.ui_v2.companion_dialog import CompanionDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def qtbot(qapp):
    """Just enough of pytest-qt's fixture for these tests.

    The project does not depend on pytest-qt and this file is not a reason
    to add one; all these tests need is somewhere to register a widget so
    it is destroyed rather than leaked into the next test. Leaked widgets
    under a module-scoped QApplication are the documented cause of this
    suite's access violations.
    """
    class _Bot:
        def __init__(self):
            self.widgets = []

        def addWidget(self, widget):  # noqa: N802 - matches pytest-qt
            self.widgets.append(widget)

    bot = _Bot()
    yield bot
    for widget in bot.widgets:
        widget.close()
        widget.deleteLater()
    qapp.processEvents()


@pytest.fixture
def folder(tmp_path):
    """Two projects, one of them without a film on disk."""
    for name, film in (("Game A", str(tmp_path / "a.mp4")),
                       ("Game B", "Z:/gone.mp4")):
        path = tmp_path / f"{name}.tapesift"
        db = sqlite3.connect(path)
        db.executescript(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT,"
            " source_video_path TEXT);"
            "CREATE TABLE clips (id TEXT PRIMARY KEY, project_id INTEGER,"
            " clip_number INTEGER, order_index INTEGER, start_ms INTEGER,"
            " end_ms INTEGER, clip_title TEXT, tags_json TEXT,"
            " details_json TEXT, enabled INTEGER DEFAULT 1);")
        db.execute("INSERT INTO projects VALUES (1, ?, ?)", (name, film))
        db.execute(
            "INSERT INTO clips VALUES ('c1', 1, 1, 0, 0, 900, 'P1', '[]',"
            " '{}', 1)")
        db.commit()
        db.close()
    (tmp_path / "a.mp4").write_bytes(b"\x00" * 32)
    return tmp_path


class TestSummary:
    def test_it_counts_every_project_not_just_one(self, qtbot, folder):
        dialog = CompanionDialog(folder)
        qtbot.addWidget(dialog)
        assert "2 projects" in dialog.summary.text()

    def test_it_says_which_projects_have_no_film(self, qtbot, folder):
        dialog = CompanionDialog(folder)
        qtbot.addWidget(dialog)
        assert "without their source film" in dialog.summary.text()

    def test_an_empty_folder_cannot_be_started(self, qtbot, tmp_path):
        dialog = CompanionDialog(tmp_path)
        qtbot.addWidget(dialog)
        assert not dialog.start_button.isEnabled()
        assert "No projects" in dialog.summary.text()


class TestRunning:
    def test_start_serves_and_shows_an_address(self, qtbot, folder):
        dialog = CompanionDialog(folder)
        qtbot.addWidget(dialog)
        dialog._start()
        try:
            assert dialog._server is not None
            assert dialog.url_label.isVisible() or dialog.url_label.text()
            assert dialog.url_label.text().startswith("http://")
            assert dialog.start_button.text() == "Stop"
        finally:
            dialog._stop()

    def test_stopping_releases_the_server(self, qtbot, folder):
        dialog = CompanionDialog(folder)
        qtbot.addWidget(dialog)
        dialog._start()
        dialog._stop()
        assert dialog._server is None
        assert dialog.start_button.text() == "Start"

    def test_tailscale_address_is_shown_and_preferred(
            self, qtbot, folder, monkeypatch):
        remote = "http://pc.example.ts.net:8733/?t=phone-token"
        monkeypatch.setattr(
            "tapesift.ui_v2.companion_dialog.tailscale_urls",
            lambda port, token: [remote])
        dialog = CompanionDialog(folder)
        qtbot.addWidget(dialog)
        dialog._start()
        try:
            assert remote in dialog.tailscale_label.text()
            assert dialog._copy_target == remote
            assert dialog.copy_button.text() == "Copy Tailscale Address"
        finally:
            dialog._stop()

    def test_closing_the_window_stops_the_server(self, qtbot, folder):
        """A server outliving its dialog would hold the port with no way back."""
        dialog = CompanionDialog(folder)
        qtbot.addWidget(dialog)
        dialog._start()
        dialog.reject()
        assert dialog._server is None

    def test_writes_are_refused_while_a_project_is_open(self, qtbot, folder):
        """The check that stops a sync being erased by the next desktop save."""
        target = sorted(folder.glob("*.tapesift"))[0]
        held = sqlite3.connect(target)
        held.execute("PRAGMA journal_mode = WAL")
        held.execute("SELECT 1 FROM projects").fetchone()
        try:
            dialog = CompanionDialog(folder)
            qtbot.addWidget(dialog)
            dialog.allow_writes.setChecked(True)
            dialog._start()
            assert dialog._server is None, "started despite an open project"
            assert "open somewhere" in dialog.note.text()
            assert target.stem in dialog.note.text()
        finally:
            held.close()

    def test_read_only_starts_even_with_a_project_open(self, qtbot, folder):
        """Tagging queues on the phone, so browsing never needs the file closed."""
        target = sorted(folder.glob("*.tapesift"))[0]
        held = sqlite3.connect(target)
        held.execute("PRAGMA journal_mode = WAL")
        held.execute("SELECT 1 FROM projects").fetchone()
        try:
            dialog = CompanionDialog(folder)
            qtbot.addWidget(dialog)
            dialog._start()
            assert dialog._server is not None
            dialog._stop()
        finally:
            held.close()
