"""Clip-scoped wiring between the Review selection and film annotations."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.models.clip import Clip  # noqa: E402
from tapesift.models.telestration import (  # noqa: E402
    Mark,
    MarkKind,
    marks_from_json,
    marks_to_json,
)
from tapesift.services.project_service import ProjectSession  # noqa: E402
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow  # noqa: E402
from tapesift.ui_v2.main_window import MainWindowV2  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, recent_projects=[])
    settings.default_project_folder = str(tmp_path)
    settings.default_output_folder = str(tmp_path / "exports")
    settings.confirm_before_delete = False
    settings.save = lambda *args, **kwargs: None
    value = MainWindowV2(settings)
    yield value
    if value.session is not None:
        value.session.close()
        value.session = None
    value.hide()
    value.deleteLater()
    qapp.processEvents()


def _attach_session(window, tmp_path, clips):
    session = ProjectSession.create(
        "Telestration clips", tmp_path, tmp_path / "exports")
    for clip in clips:
        session.add_clip(clip)
    window.session = session
    window._refresh_clip_list()
    return session


def _select(window, clip_id):
    assert window.select_clip(
        clip_id,
        seek=False,
        focus_player=False,
        review_autoplay=False,
    )


def _mouse_event(kind, pos):
    point = QPointF(*pos)
    return QMouseEvent(
        kind,
        point,
        point,
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def _drag(surface, start=(100, 100), end=(500, 300)):
    surface.mousePressEvent(
        _mouse_event(QEvent.Type.MouseButtonPress, start))
    surface.mouseMoveEvent(_mouse_event(QEvent.Type.MouseMove, end))
    surface.mouseReleaseEvent(
        _mouse_event(QEvent.Type.MouseButtonRelease, end))


def _mark(kind=MarkKind.ARROW, *, ink="gold", offset=0.0):
    return Mark(
        kind,
        [(0.10 + offset, 0.20), (0.60 + offset, 0.70)],
        ink=ink,
    )


def test_switching_a_to_b_to_a_loads_only_that_clips_marks(
        window, tmp_path):
    clip_a = Clip(
        start_ms=0,
        end_ms=4_000,
        overlays=marks_to_json([_mark(MarkKind.ARROW)]),
    )
    clip_b = Clip(
        start_ms=5_000,
        end_ms=9_000,
        overlays=marks_to_json([_mark(MarkKind.CIRCLE, ink="cyan")]),
    )
    session = _attach_session(window, tmp_path, [clip_a, clip_b])
    edits = []
    window.player.telestration_marks_changed.connect(
        lambda: edits.append(True))

    _select(window, clip_a.id)
    assert [mark.kind for mark in window.player.telestration_marks()] == [
        MarkKind.ARROW]
    _select(window, clip_b.id)
    assert [mark.kind for mark in window.player.telestration_marks()] == [
        MarkKind.CIRCLE]
    _select(window, clip_a.id)
    assert [mark.kind for mark in window.player.telestration_marks()] == [
        MarkKind.ARROW]

    assert edits == [], "selection loads are presentation, not edits"
    assert session.dirty is False


def test_draw_undo_and_clear_update_only_the_selected_clip(
        window, tmp_path):
    original = [_mark(), _mark(MarkKind.LINE, offset=0.05)]
    clip_a = Clip(
        start_ms=0, end_ms=4_000, overlays=marks_to_json(original))
    clip_b = Clip(
        start_ms=5_000,
        end_ms=9_000,
        overlays=marks_to_json([_mark(MarkKind.CIRCLE, ink="red")]),
    )
    session = _attach_session(window, tmp_path, [clip_a, clip_b])
    _select(window, clip_a.id)

    assert window.player.video_widget.undo_mark() is True
    assert len(marks_from_json(clip_a.overlays)) == 1
    assert len(marks_from_json(clip_b.overlays)) == 1
    assert session.dirty is True

    session.dirty = False
    assert window.player.video_widget.clear_marks() is True
    assert clip_a.overlays == []
    assert len(marks_from_json(clip_b.overlays)) == 1
    assert session.dirty is True

    session.dirty = False
    surface = window.player.video_widget
    surface._frame_rect = QRectF(0, 0, 960, 540)
    surface.set_tool("arrow")
    _drag(surface)
    restored = marks_from_json(clip_a.overlays)
    assert len(restored) == 1
    assert restored[0].kind is MarkKind.ARROW
    assert session.dirty is True


def test_surface_edit_survives_project_close_and_reopen(
        window, tmp_path):
    clip = Clip(start_ms=0, end_ms=4_000)
    session = _attach_session(window, tmp_path, [clip])
    db_path = session.db_path
    _select(window, clip.id)

    surface = window.player.video_widget
    surface._frame_rect = QRectF(0, 0, 960, 540)
    surface.set_tool("circle")
    _drag(surface, (150, 100), (450, 350))
    assert session.dirty is True

    session.close()
    window.session = None
    reopened = ProjectSession.open(db_path)
    window.session = reopened
    window._refresh_clip_list()
    _select(window, clip.id)

    stored = marks_from_json(reopened.get_clip(clip.id).overlays)
    assert len(stored) == 1
    assert stored[0].kind is MarkKind.CIRCLE
    assert window.player.telestration_marks()[0].kind is MarkKind.CIRCLE
    assert reopened.dirty is False


def test_clearing_selection_empties_surface_and_rejects_unowned_edits(
        window, tmp_path):
    stored = marks_to_json([_mark(MarkKind.LINE)])
    clip = Clip(start_ms=0, end_ms=4_000, overlays=stored)
    session = _attach_session(window, tmp_path, [clip])
    _select(window, clip.id)
    assert window.player.telestration_marks()

    window.clip_list.table.clearSelection()
    assert window._selected_clip_id is None
    assert window.player.telestration_marks() == []
    assert session.dirty is False

    surface = window.player.video_widget
    surface._frame_rect = QRectF(0, 0, 960, 540)
    surface.set_tool("arrow")
    _drag(surface)

    assert window.player.telestration_marks() == []
    assert clip.overlays == stored
    assert session.dirty is False


def test_malformed_overlays_are_ignored_without_dirtying_selection(
        window, tmp_path):
    clip = Clip(
        start_ms=0,
        end_ms=4_000,
        overlays=[
            None,
            "not a mark",
            {},
            {"kind": "circle", "points": [["bad", 0.2], [0.5, 0.6]]},
            {"kind": "line", "points": [[0.1]]},
        ],
    )
    session = _attach_session(window, tmp_path, [clip])

    _select(window, clip.id)

    assert window.player.telestration_marks() == []
    assert session.dirty is False


def test_deleting_the_selected_clip_clears_live_selection_and_surface(
        window, tmp_path):
    clip = Clip(
        start_ms=0,
        end_ms=4_000,
        overlays=marks_to_json([_mark(MarkKind.CIRCLE)]),
    )
    session = _attach_session(window, tmp_path, [clip])
    _select(window, clip.id)
    assert window.player.telestration_marks()

    window._delete_clips([clip.id])

    assert session.get_clip(clip.id) is None
    assert window._selected_clip_id is None
    assert window.player._selected_timeline_clip_id is None
    assert window.player.telestration_marks() == []


def test_undo_and_redo_reload_overlays_from_replacement_clip_objects(
        window, tmp_path):
    old = marks_to_json([_mark(MarkKind.ARROW)])
    new = marks_to_json([_mark(MarkKind.CIRCLE, ink="red")])
    clip = Clip(start_ms=0, end_ms=4_000, overlays=old)
    session = _attach_session(window, tmp_path, [clip])
    _select(window, clip.id)

    session.checkpoint("replace overlays")
    clip.overlays = new
    session.commit()
    window._load_clip_telestration(clip)
    before_undo = session.get_clip(clip.id)

    window._undo()
    after_undo = session.get_clip(clip.id)
    assert after_undo is not before_undo
    assert after_undo.overlays == old
    assert [mark.kind for mark in window.player.telestration_marks()] == [
        MarkKind.ARROW]

    window._redo()
    after_redo = session.get_clip(clip.id)
    assert after_redo is not after_undo
    assert after_redo.overlays == new
    assert [mark.kind for mark in window.player.telestration_marks()] == [
        MarkKind.CIRCLE]


def test_switching_clips_mid_drag_cancels_the_unowned_release(
        window, tmp_path):
    clip_a = Clip(start_ms=0, end_ms=4_000)
    b_overlays = marks_to_json([_mark(MarkKind.LINE)])
    clip_b = Clip(start_ms=5_000, end_ms=9_000, overlays=b_overlays)
    session = _attach_session(window, tmp_path, [clip_a, clip_b])
    _select(window, clip_a.id)

    accepted = []
    window.telestration_edit_accepted.connect(
        lambda clip_id, snapshot: accepted.append((clip_id, snapshot)))
    surface = window.player.video_widget
    surface._frame_rect = QRectF(0, 0, 960, 540)
    surface.set_tool("arrow")
    surface.set_ink("cyan")
    surface.mousePressEvent(
        _mouse_event(QEvent.Type.MouseButtonPress, (100, 100)))
    surface.mouseMoveEvent(
        _mouse_event(QEvent.Type.MouseMove, (500, 300)))
    assert surface._drawing is not None

    _select(window, clip_b.id)
    assert surface._drawing is None
    assert surface._drag_to is None
    assert surface.tool() == "arrow"
    assert surface.ink() == "cyan"
    surface.mouseReleaseEvent(
        _mouse_event(QEvent.Type.MouseButtonRelease, (500, 300)))

    assert clip_a.overlays == []
    assert clip_b.overlays == b_overlays
    assert session.dirty is False
    assert accepted == []


def test_accepted_edit_event_excludes_programmatic_rejected_and_read_only_edits(
        window, tmp_path):
    clip = Clip(start_ms=0, end_ms=4_000)
    session = _attach_session(window, tmp_path, [clip])
    accepted = []
    raw = []
    window.telestration_edit_accepted.connect(
        lambda clip_id, snapshot: accepted.append((clip_id, snapshot)))
    window.player.telestration_marks_changed.connect(
        lambda: raw.append(True))

    _select(window, clip.id)
    assert accepted == []
    surface = window.player.video_widget
    surface._frame_rect = QRectF(0, 0, 960, 540)
    surface.set_tool("arrow")
    _drag(surface)
    assert len(raw) == 1
    assert accepted == [(clip.id, clip.overlays)]

    # The event carries a snapshot, not the list owned by the Clip.
    accepted[0][1][0]["points"][0][0] = 999
    assert clip.overlays[0]["points"][0][0] != 999

    window.clip_list.table.clearSelection()
    _drag(surface, (150, 100), (550, 300))
    assert len(raw) == 2
    assert len(accepted) == 1

    _select(window, clip.id)
    session.read_only = True
    _drag(surface, (200, 100), (600, 300))
    assert len(raw) == 3
    assert len(accepted) == 1
    assert marks_from_json(clip.overlays)
