"""Human snap corrections survive persistence and outrank machine estimates."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from tapesift.core.exceptions import DatabaseError
from tapesift.models.clip import Clip
from tapesift.services import snap_prediction_service as snaps
from tapesift.services.project_service import ProjectSession


def test_corrected_snap_survives_reopen_undo_redo_and_failed_write(tmp_path, monkeypatch):
    session = ProjectSession.create("Snap", tmp_path, tmp_path / "exports")
    try:
        clip = session.add_clip(Clip(1000, 6000, notes="Saved note", details={
            "timing_enabled": "1", "timing_release_ms": "4000", "formation": "Shotgun"}))
        prediction = {"predictor_id": snaps.PREDICTOR_ID,
            "predictor_version": snaps.PREDICTOR_VERSION,
            "clip_start_ms": 1000, "clip_end_ms": 6000, "source_ms": 2000}
        session.cache_snap_prediction(clip.id, prediction)
        before = deepcopy(clip.details)
        session.correct_snap_point(clip.id, 2500)
        assert snaps.cached_prediction(clip) == prediction
        assert snaps.snap_marker(clip) == {"source_ms": 2500, "confirmed": True}
        assert clip.details == dict(before, timing_snap_ms="2500", timing_snap_confirmed="1")
        assert clip.notes == "Saved note"
        stored = ProjectSession.open_read_only(session.db_path)
        try:
            assert stored.get_clip(clip.id).details == clip.details
            with pytest.raises(DatabaseError, match="read-only"):
                stored.correct_snap_point(clip.id, 3000)
        finally:
            stored.close()
        undo_size = len(session._undo_stack)
        session.correct_snap_point(clip.id, 2500)
        assert len(session._undo_stack) == undo_size
        for invalid in (None, True, 999, 6000, "2500"):
            with pytest.raises(DatabaseError, match="within"):
                session.correct_snap_point(clip.id, invalid)
        with pytest.raises(DatabaseError, match="no longer"):
            session.correct_snap_point("missing", 2500)
        def fail(*_args, **_kwargs):
            raise OSError("disk failure")
        with monkeypatch.context() as patch:
            patch.setattr(session.clip_repo, "save_many", fail)
            with pytest.raises(OSError, match="disk failure"):
                session.correct_snap_point(clip.id, 3000)
        assert len(session._undo_stack) == undo_size
        assert snaps.snap_marker(session.get_clip(clip.id))["source_ms"] == 2500
        assert session.undo() == "correct snap point"
        assert session.get_clip(clip.id).details == before
        assert snaps.snap_marker(session.get_clip(clip.id)) == prediction
        assert session.redo() == "correct snap point"
        assert snaps.snap_marker(session.get_clip(clip.id))["source_ms"] == 2500
    finally:
        session.close()


def test_navigation_prefers_human_snap_even_when_prediction_finishes_later():
    from tapesift.ui_core.main_window_workflow import MainWindowWorkflow

    clip = Clip(0, 6000, details={"timing_snap_ms": "0", "timing_snap_confirmed": "1"})
    calls, states, messages = [], [], []
    player = SimpleNamespace(shuttle_stop=lambda: None, seek_to=calls.append,
        set_predicted_snap_state=lambda *args: states.append(args))
    owner = SimpleNamespace(session=SimpleNamespace(get_clip=lambda _: clip),
        _selected_clip_id=clip.id, player=player, statusBar=lambda: SimpleNamespace(
            showMessage=lambda text, *_: messages.append(text)))
    owner._jump_to_predicted_snap = lambda *args: MainWindowWorkflow._jump_to_predicted_snap(owner, *args)
    MainWindowWorkflow._sync_predicted_snap_action(owner)
    MainWindowWorkflow._predicted_snap_requested(owner)
    MainWindowWorkflow._jump_to_predicted_snap(owner, clip, {"source_ms": 2000})
    assert states == [("confirmed", {"source_ms": 0, "confirmed": True})]
    assert calls == [0, 0] and all("Confirmed snap" in text for text in messages)
    for invalid in ("bad", "-1", "6000", None):
        clip.details["timing_snap_ms"] = invalid
        assert snaps.snap_marker(clip) is None


def test_stale_video_context_cannot_correct_another_selection():
    from tapesift.ui_v3.main_window import MainWindowV3

    messages = []
    owner = SimpleNamespace(_v3_snap_context=("old selection", 2500),
        _source_photo_context=lambda: "new selection",
        statusBar=lambda: SimpleNamespace(showMessage=lambda text, *_: messages.append(text)))
    MainWindowV3._correct_snap_point(owner)
    assert owner._v3_snap_context is None
    assert messages and "right-click again" in messages[0]


def test_ui_undo_redo_preserves_unsaved_inspector_fields(tmp_path):
    from PySide6.QtWidgets import QApplication
    from tapesift.core.config import AppSettings
    from tapesift.ui_v3.clip_details import ClipDetailsV3
    from tapesift.ui_v3.main_window import MainWindowV3

    app = QApplication.instance() or QApplication([])
    editor = ClipDetailsV3(AppSettings())
    editor.set_analyst_mode(True)
    session = ProjectSession.create("Undo snap", tmp_path, tmp_path / "out")
    try:
        clip = session.add_clip(Clip(0, 6000))
        session.correct_snap_point(clip.id, 2500)
        editor.set_clip(clip)
        editor.notes_edit.setPlainText("Keep my unsaved note")
        editor.detail_edits["quarter"].setText("Q2")
        editor._stage_timing_details({"timing_enabled": "1"})
        def rebind():
            editor._clip = session.get_clip(clip.id)
        owner = SimpleNamespace(session=session, clip_editor=editor,
            _refresh_source_photo_binding=rebind,
            clip_list=SimpleNamespace(set_clips=lambda *_: None), settings=AppSettings(),
            statusBar=lambda: SimpleNamespace(showMessage=lambda *_: None),
            _index_current_project=lambda: None, _decorate_v3_ledger_rows=lambda: None,
            _refresh_timeline_presentation=lambda: None, _sync_predicted_snap_action=lambda: None)
        owner._restore_snap_correction = lambda direction: MainWindowV3._restore_snap_correction(owner, direction)
        MainWindowV3._undo(owner)
        assert "timing_snap_ms" not in editor.play_timing.details
        MainWindowV3._redo(owner)
        assert editor.play_timing.details["timing_snap_ms"] == "2500"
        assert editor.play_timing.details["timing_enabled"] == "1"
        assert editor.notes_edit.toPlainText() == "Keep my unsaved note"
        assert editor.detail_edits["quarter"].text() == "Q2"
        assert session.get_clip(clip.id).notes == ""
        assert "quarter" not in session.get_clip(clip.id).details
        editor.clip_edited.connect(lambda _: session.commit())
        assert editor._apply()
        saved = session.get_clip(clip.id)
        assert saved.notes == "Keep my unsaved note"
        assert saved.details["quarter"] == "Q2"
        assert saved.details["timing_snap_ms"] == "2500"
    finally:
        session.close()
        editor.close()
