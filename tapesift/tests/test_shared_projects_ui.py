"""Personal shared versions preserve local edits and report the active project."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMainWindow

from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.services.project_sync_service import SyncStore
from tapesift.ui_v3.clip_details import ClipDetailsV3
from tapesift.ui_v3.shared_projects import SharedProjects, local_key


@pytest.fixture
def shared(tmp_path):
    app = QApplication.instance() or QApplication([])
    folder = tmp_path / "shared"
    folder.mkdir()
    local = tmp_path / "local"
    local.mkdir()
    window = QMainWindow()
    window.settings = AppSettings(shared_projects_folder=str(folder),
        default_project_folder=str(local), default_output_folder=str(tmp_path / "exports"))
    window.session = ProjectSession.create("First game", local, tmp_path / "exports")
    window.session.add_clip(Clip(0, 5000, details={"timing_snap_ms": "1250", "timing_snap_confirmed": "1"}))
    window.clip_editor = ClipDetailsV3(window.settings)
    window.clip_editor.set_analyst_mode(True)
    window.clip_editor.set_clip(window.session.clips[0])
    window.clip_editor.clip_edited.connect(lambda _: window.session.commit())
    def save():
        window.session.save()
        return True
    window._try_save = save
    controller = SharedProjects(window)
    controller.timer.stop()
    yield controller
    controller.wait()
    if controller.edit_lock:
        controller.edit_lock.unlock()
    if window.session:
        window.session.conn.close()
    window.clip_editor.close()
    window.close()


def publish(controller, **kwargs):
    assert controller.publish(**kwargs)
    assert controller.wait()
    return controller.binding(controller.window.session.db_path)


def test_publish_draft_autoshare_and_remote_update_preserve_local_work(shared, tmp_path):
    window = shared.window
    window.clip_editor.notes_edit.setPlainText("Saved from this computer")
    link = publish(shared, save_draft=True)
    assert not shared.error
    store = SyncStore(link["folder"])
    copy = store.checkout(link["project_id"], link["revision"], tmp_path / "other-computer")
    other = ProjectSession.open(copy)
    try:
        assert other.clips[0].notes == "Saved from this computer"
        assert other.clips[0].details["timing_snap_ms"] == "1250"
        window.session.clips[0].notes = "Automatic saved change"
        window.session.commit()
        shared.poll()
        assert shared.worker is None
        shared.poll()
        assert shared.wait() and not shared.error
        current = shared.binding(window.session.db_path)
        assert current["revision"] != link["revision"]
        other.clips[0].notes = "Work saved on the other computer"
        other.commit()
        remote = store.publish(other.db_path, project_id=current["project_id"], parent_revision=current["revision"])
        window.session.clips[0].notes = "Keep my local work too"
        window.session.commit()
        publish(shared)
        assert shared.error and "newer shared version" in shared.message
        assert shared.binding(window.session.db_path)["revision"] == current["revision"]
        assert store.status(current["project_id"]).latest == remote
        assert window.session.clips[0].notes == "Keep my local work too"
        shared.show()
        assert shared.dialog.versions.topLevelItemCount() == 1
        assert shared.dialog.versions.topLevelItem(0).childCount() == 3
    finally:
        other.conn.close()


def test_renamed_link_keeps_identity_and_failed_queue_does_not_block_another_project(shared, tmp_path):
    link = publish(shared, save_draft=True)
    old = shared.window.session.db_path
    shared.window.session.conn.close()
    shared.window.session = None
    moved = old.with_name("Renamed.tapesift")
    old.rename(moved)
    shared.moved(str(old), str(moved))
    assert shared.binding(old) is None
    assert shared.binding(moved)["project_id"] == link["project_id"]
    shared.poll()
    assert shared.wait() and not shared.error
    other = ProjectSession.create("Other game", tmp_path / "local", tmp_path / "exports")
    try:
        other.add_clip(Clip(0, 1000))
        root = shared.window.settings.shared_projects_folder
        first = SyncStore(root).publish(other.db_path)
        shared.remember(local_key(other.db_path), dict(folder=root, project_id=first.project_id, revision=first.revision_id, pending="1"))
        missing = local_key(tmp_path / "000-missing.tapesift")
        shared.remember(missing, dict(link, pending="1"))
        shared.poll()
        assert shared.worker.path == missing
        shared.wait()
        assert shared.error
        shared.poll()
        assert shared.worker.path == local_key(other.db_path)
        shared.wait()
        assert not shared.error
        assert shared.binding(other.db_path)["pending"] == ""
    finally:
        other.conn.close()


def test_status_lock_folder_change_and_same_file_reopen(shared, tmp_path):
    from PySide6.QtCore import QLockFile
    from tapesift.ui_v3.main_window import MainWindowV3
    publish(shared, save_draft=True)
    window = shared.window
    contender = QLockFile(str(window.session.db_path) + ".editing.lock")
    assert not contender.tryLock(0)
    window.clip_editor.notes_edit.setPlainText("Unsaved draft")
    shown = []
    owner = SimpleNamespace(session=window.session, workspace=object(),
        stack=SimpleNamespace(setCurrentWidget=shown.append))
    MainWindowV3._open_project(owner, str(window.session.db_path))
    assert shown == [owner.workspace]
    assert window.clip_editor.notes_edit.toPlainText() == "Unsaved draft"
    assert not contender.tryLock(0)
    shared.show()
    elsewhere = tmp_path / "different-shared-folder"
    elsewhere.mkdir()
    window.settings.shared_projects_folder = str(elsewhere)
    shared.dialog.refresh()
    assert not shared.dialog.save_button.isEnabled()
    assert shared.dialog.new_button.isEnabled()
    old = window.session
    window.session = ProjectSession.create("Unshared game", tmp_path / "local", tmp_path / "exports")
    shared.activated()
    assert shared.message == "This project is local only"
    old.conn.close()


def test_settings_write_failure_does_not_advance_revision_anchor(shared, monkeypatch):
    link = publish(shared, save_draft=True)
    shared.window.session.clips[0].notes = "Still preserved"
    shared.window.session.commit()
    def fail():
        raise OSError("Settings unavailable")
    monkeypatch.setattr(shared.settings, "save", fail)
    publish(shared)
    assert shared.error
    assert shared.binding(shared.window.session.db_path) == link
    assert SyncStore(link["folder"]).status(link["project_id"], link["revision"]).has_updates


def test_closed_library_edit_respects_other_window_lock_and_queues_a_save(shared):
    from PySide6.QtCore import QLockFile
    publish(shared, save_draft=True)
    window = shared.window
    session = window.session
    window.session = None
    called = []
    def apply():
        called.append(True)
        session.clips[0].notes = "From Library"
        session.commit()
        return True, ""
    try:
        assert not shared.library_edit(session.db_path, apply)[0]
        assert not called
        shared.edit_lock.unlock()
        shared.edit_lock = None
        assert shared.library_edit(session.db_path, apply) == (True, "")
        assert shared.binding(session.db_path)["pending"] == "1"
        shared.poll()
        assert shared.wait() and not shared.error
        assert shared.binding(session.db_path)["pending"] == ""
    finally:
        window.session = session


def test_async_completion_and_edit_queued_during_publish_remain_durable(shared):
    from PySide6.QtCore import QEventLoop, QTimer
    loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    shared.changed.connect(lambda: loop.quit() if shared.worker is None else None)
    assert shared.publish(save_draft=True)
    timeout.start(10000)
    loop.exec()
    timeout.stop()
    assert shared.worker is None and not shared.error
    path = shared.window.session.db_path
    shared.queue_saved_file(path)
    shared.poll()
    assert shared.worker.wait(10000)
    # The saved file changed after the worker captured its snapshot, before
    # the GUI receives completion. Closing now must retain the second upload.
    shared.window.session.clips[0].notes = "A second Library save"
    shared.window.session.commit()
    shared.queue_saved_file(path)
    shared._finished(shared.worker)
    assert shared.binding(path)["pending"] == "1"
    session = shared.window.session
    shared.window.session = None
    try:
        assert shared.before_close()
        assert shared.binding(path)["pending"] == ""
        link = shared.binding(path)
        copy = SyncStore(link["folder"]).checkout(link["project_id"], link["revision"], path.parent / "verify")
        reopened = ProjectSession.open_read_only(copy)
        try:
            assert reopened.clips[0].notes == "A second Library save"
        finally:
            reopened.close()
    finally:
        shared.window.session = session


def test_failed_import_releases_the_unactivated_database(shared, monkeypatch):
    import sqlite3
    link = publish(shared, save_draft=True)
    revision = SyncStore(link["folder"]).status(link["project_id"]).latest
    shared.window._close_project = lambda: True
    opened = []
    original_open = ProjectSession.open
    def open_copy(path):
        session = original_open(path)
        opened.append(session)
        return session
    def fail(*_args):
        raise OSError("Settings unavailable")
    monkeypatch.setattr(ProjectSession, "open", open_copy)
    monkeypatch.setattr(shared, "remember", fail)
    with pytest.raises(OSError):
        shared.open_revision(link["folder"], revision)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].conn.execute("SELECT 1")


def test_v3_rename_reopens_shared_project_when_preferences_cannot_be_written(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    from tapesift.ui_v3.main_window import MainWindowV3
    app = QApplication.instance() or QApplication([])
    folder = tmp_path / "shared"
    folder.mkdir()
    local = tmp_path / "local"
    settings = AppSettings(shared_projects_folder=str(folder),
        default_project_folder=str(local), default_output_folder=str(tmp_path / "exports"))
    window = MainWindowV3(settings, workspace_state_path=tmp_path / "workspace.json")
    window.shared_projects.timer.stop()
    session = ProjectSession.create("Rename me", local, tmp_path / "exports")
    session.add_clip(Clip(0, 5000))
    try:
        assert window._activate_session(session)
        link = publish(window.shared_projects, save_draft=True)
        window.clip_editor.notes_edit.setPlainText("Draft kept through rename")
        monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Renamed", True))
        def fail():
            raise OSError("Preferences are read-only")
        with monkeypatch.context() as patch:
            patch.setattr(settings, "save", fail)
            window._rename_open_project()
        assert window.session is not None
        assert window.session.db_path.name == "Renamed.tapesift"
        assert window.session.clips[0].notes == "Draft kept through rename"
        assert window.shared_projects.binding(window.session.db_path)["project_id"] == link["project_id"]
        assert window.clip_editor._clip is window.session.clips[0]
    finally:
        window.shared_projects.wait()
        if window.shared_projects.edit_lock:
            window.shared_projects.edit_lock.unlock()
            window.shared_projects.edit_lock = None
        window.shared_projects.settings.shared_project_links = {}
        window._app_closing = True
        window.close()
