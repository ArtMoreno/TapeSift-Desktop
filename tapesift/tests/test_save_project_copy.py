"""Portable folder saves include WAL data and preserve the open original."""
import sqlite3
from pathlib import Path

from PySide6.QtWidgets import QApplication

from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.services.project_sync_service import save_project_copy
from tapesift.ui_v3 import main_window as ui


def test_copy_is_standalone_and_preserves_existing_files(tmp_path):
    session = ProjectSession.create("Game", tmp_path / "projects", tmp_path / "exports")
    session.add_clip(Clip(0, 5000, notes="Saved in WAL", details={"quarterback": "QB One", "timing_snap_ms": "1250"}))
    session.save()
    destination = tmp_path / "chosen folder"
    destination.mkdir()
    existing = destination / session.db_path.name
    existing.write_bytes(b"Do not replace")
    orphan = destination / "Game (2).tapesift-wal"
    orphan.write_bytes(b"Do not reuse this filename")
    try:
        copied = save_project_copy(session.db_path, destination)
        assert copied.name == "Game (3).tapesift"
        assert existing.read_bytes() == b"Do not replace"
        assert orphan.read_bytes() == b"Do not reuse this filename"
        assert not copied.with_name(copied.name + "-wal").exists()
        with sqlite3.connect(copied) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        reopened = ProjectSession.open(copied)
        try:
            assert reopened.clips[0].notes == "Saved in WAL"
            assert reopened.clips[0].details["timing_snap_ms"] == "1250"
            assert reopened.clips[0].details["quarterback"] == "QB One"
        finally:
            reopened.conn.close()
        assert session.conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        session.conn.close()


def test_menu_saves_drafts_opens_folder_and_handles_cancel_and_failure(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    settings = AppSettings(default_project_folder=str(tmp_path / "projects"),
                           default_output_folder=str(tmp_path / "exports"))
    window = ui.MainWindowV3(settings, workspace_state_path=tmp_path / "workspace.json")
    session = ProjectSession.create("Portable Game", tmp_path / "projects", tmp_path / "exports")
    session.add_clip(Clip(0, 5000, details={"timing_snap_ms": "1500", "timing_snap_confirmed": "1"}))
    destination = tmp_path / "transfer"
    destination.mkdir()
    notices, folders, warnings = [], [], []
    monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *_: str(destination))
    monkeypatch.setattr(ui.QMessageBox, "exec", lambda box: notices.append(box.text()))
    monkeypatch.setattr(ui.QMessageBox, "clickedButton", lambda box: next(button for button in box.buttons() if button.text() == "Open Folder"))
    monkeypatch.setattr(ui.QDesktopServices, "openUrl", lambda url: folders.append(url.toLocalFile()))
    monkeypatch.setattr(ui.QMessageBox, "warning", lambda *args: warnings.append(args[-1]))
    try:
        assert not window._v3_save_menu.isEnabled()
        assert window._activate_session(session)
        window._sync_v3_menu_availability()
        assert window.save_to_folder_action.isEnabled()
        assert window.save_to_folder_action.shortcut().toString() == "Ctrl+Shift+S"
        assert window._v3_menu_actions["Save Project"].shortcut().toString() == "Ctrl+S"
        window.clip_editor.notes_edit.setPlainText("Draft included in the portable copy")
        original = session.db_path
        window.save_to_folder_action.trigger()
        copied = destination / original.name
        assert copied.is_file() and str(copied) in notices[0]
        assert [Path(folder) for folder in folders] == [destination]
        assert window.session is session and session.db_path == original
        reopened = ProjectSession.open(copied)
        try:
            assert reopened.clips[0].notes == "Draft included in the portable copy"
            assert reopened.clips[0].details["timing_snap_ms"] == "1500"
        finally:
            reopened.conn.close()
        window.clip_editor.notes_edit.setPlainText("Cancelled copy stays a draft")
        monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *_: "")
        window.save_to_folder_action.trigger()
        assert len(notices) == 1
        assert session.clips[0].notes == "Draft included in the portable copy"
        monkeypatch.setattr(ui.QFileDialog, "getExistingDirectory", lambda *_: str(destination))
        def fail(*_):
            raise OSError("Drive unavailable")
        monkeypatch.setattr(ui, "save_project_copy", fail)
        window.save_to_folder_action.trigger()
        assert warnings == ["Drive unavailable"] and len(notices) == 1
        assert session.clips[0].notes == "Cancelled copy stays a draft"
        assert session.db_path == original
    finally:
        window.close()
