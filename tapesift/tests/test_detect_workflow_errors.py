"""Detection failures stay visible in the desktop app, including before its dialog."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tapesift.core.exceptions import DatabaseError
from tapesift.services.play_detect_service import DetectionResult
from tapesift.ui_core import main_window_workflow as workflow


@pytest.mark.parametrize("failure_at", ["read", "create"])
def test_detection_database_error_shows_recovery_advice(tmp_path, monkeypatch, failure_at):
    source = tmp_path / "film.mp4"
    source.touch()
    failure = DatabaseError("Project database is locked.", "Close its other editor.")
    session = SimpleNamespace(
        db_path=tmp_path / "game.tapesift",
        project=SimpleNamespace(
            has_source=True, source_video_path=str(source),
            output_folder=str(tmp_path), source_duration_ms=60_000),
        active_autodetect_review_batch=Mock(return_value=None),
        add_detected_clips=Mock(side_effect=failure),
    )
    if failure_at == "read":
        session.active_autodetect_review_batch.side_effect = failure
    dialog = Mock()
    dialog.exec.return_value = workflow.PlayDetectDialog.DialogCode.Accepted
    dialog.result = DetectionResult(
        plays=[], signal="none", spans_found=0, separators_found=0,
        duration_ms=60_000)
    dialog.play_candidates.return_value = []
    dialog.capture_parameters.return_value = {}
    dialog.runtime_seconds = 0.0
    dialog.wide_only_check.isChecked.return_value = False
    factory = Mock(return_value=dialog)
    factory.DialogCode = workflow.PlayDetectDialog.DialogCode
    monkeypatch.setattr(workflow, "PlayDetectDialog", factory)
    monkeypatch.setattr(workflow.proxy_service, "find_ready_proxy", lambda *_: None)
    monkeypatch.setattr(workflow.ffmpeg_service, "get_version", lambda *_: "test")
    warning = Mock()
    monkeypatch.setattr(workflow.QMessageBox, "warning", warning)
    window = object.__new__(workflow.MainWindowWorkflow)
    window.session = session
    window.settings = SimpleNamespace(ffmpeg_path="ffmpeg")
    window._require_session = lambda: True
    window._clip_defaults = lambda: SimpleNamespace()

    window._detect_plays()

    warning.assert_called_once()
    assert warning.call_args.args[1] == "Could not complete detection"
    assert failure.user_text() in warning.call_args.args[2]
    assert "try again" in warning.call_args.args[2]
    assert session.add_detected_clips.call_count == (failure_at == "create")


def test_detection_worker_keeps_actionable_error_message(tmp_path, monkeypatch):
    from tapesift.core.exceptions import FFmpegNotFoundError
    from tapesift.workers.play_detect_worker import PlayDetectWorker

    error = FFmpegNotFoundError("FFmpeg is unavailable.", "Choose FFmpeg in Settings.")
    monkeypatch.setattr(workflow.play_detect_service, "detect_plays", Mock(side_effect=error))
    worker = PlayDetectWorker("missing", tmp_path / "film.mp4", 60_000, 3, 4, 60)
    messages = []
    worker.failed.connect(messages.append)
    worker.run()
    assert messages == [error.user_text()]
