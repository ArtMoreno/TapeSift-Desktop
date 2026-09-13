"""Automatic preview scheduling and the real persisted V3 settings toggle."""
from unittest.mock import Mock

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QMessageBox

from tapesift.core.config import AppSettings
from tapesift.services.project_service import ProjectSession
from tapesift.ui_core import main_window_workflow as workflow
from tapesift.ui_v3.dialog_surface import SettingsDialogV3
from tapesift.tests.test_shell_v3_review import _window, _close_test_window


def test_auto_preview_queue_reuse_and_settings_toggle(tmp_path, monkeypatch):
    window = _window(tmp_path)
    window.settings.ffmpeg_path = "ffmpeg"
    session = ProjectSession.create("Auto preview", tmp_path / "projects", tmp_path / "exports")
    source = tmp_path / "film.mp4"
    source.write_bytes(b"test scheduling only")
    session.project.source_video_path = str(source)
    window.session = session
    workers = []
    def make_worker(*args):
        worker = Mock()
        worker.source = args[1]
        worker.isRunning.return_value = True
        workers.append(worker)
        return worker
    busy = [True]
    ready = [None]
    monkeypatch.setattr(workflow, "ProxyWorker", make_worker)
    monkeypatch.setattr(workflow.background_service, "is_busy", lambda: busy[0])
    monkeypatch.setattr(workflow.ffmpeg_service, "detect_hw_encoders", lambda _: [])
    monkeypatch.setattr(workflow.proxy_service, "find_ready_proxy", lambda *_: ready[0])
    monkeypatch.setattr(QMessageBox, "information", lambda *_: (_ for _ in ()).throw(AssertionError("Unexpected prompt")))
    load, swap = Mock(), Mock()
    monkeypatch.setattr(window.player, "load", load)
    monkeypatch.setattr(window.player, "swap_source", swap)
    monkeypatch.setattr(window.player.player, "source", lambda: QUrl.fromLocalFile(str(source)))

    window._load_preview_source(source)
    load.assert_called_once_with(source, 30.0)
    assert window._proxy_retry_timer.isActive() and not workers
    busy[0] = False
    window._proxy_retry_timer.timeout.emit()
    assert len(workers) == 1
    workers[0].start.assert_called_once()
    assert window.proxy_banner_widget.isHidden()
    window._setup_preview_proxy(source)
    assert len(workers) == 1  # No duplicate worker for the same film.

    desired = [False]
    def save_toggle(dialog):
        assert dialog.proxy_check.text() == "Automatically build smooth-scrub previews"
        dialog.proxy_check.setChecked(desired[0])
        dialog._save()
        return dialog.result()
    monkeypatch.setattr(SettingsDialogV3, "exec", save_toggle)
    window._open_settings()
    assert not window.settings.scrub_proxy_enabled
    assert not AppSettings.load().scrub_proxy_enabled
    workers[0].cancel.assert_called_once()
    workers[0].isRunning.return_value = False
    window._proxy_worker_finished(workers[0])
    assert "original" in window.preview_source_label.text()
    window._setup_preview_proxy(source)
    assert len(workers) == 1 and not window._proxy_retry_timer.isActive()
    assert window.proxy_banner_widget.isHidden()

    desired[0] = True
    window._open_settings()
    assert AppSettings.load().scrub_proxy_enabled and len(workers) == 2
    workers[1].isRunning.return_value = False
    window._proxy_worker_finished(workers[1])
    ready[0] = tmp_path / "ready.preview.mp4"
    window.settings.scrub_proxy_enabled = False
    window._load_preview_source(source)
    assert load.call_args.args[0] == ready[0] and len(workers) == 2

    # Explicit Build must rebuild even when a cache exists and auto-build is off.
    window._setup_preview_proxy(source, force=True)
    assert len(workers) == 3
    swap.assert_called_with(source)
    workers[2].start.assert_called_once()
    window._proxy_failed(workers[2], "video samples missing")
    assert "video samples missing" in window.statusBar().currentMessage()
    window._proxy_failed(workers[1], "stale worker")
    assert "stale worker" not in window.statusBar().currentMessage()
    workers[2].isRunning.return_value = False
    window._proxy_worker_finished(workers[2])
    assert "original" in window.preview_source_label.text()

    # A queued retry resolves the current source, never the previous video.
    ready[0] = None
    busy[0] = True
    window.settings.scrub_proxy_enabled = True
    window._setup_preview_proxy(source)
    window.session = None
    window._proxy_retry_timer.timeout.emit()
    assert len(workers) == 3
    window._proxy_retry_timer.stop()
    session.conn.close()
