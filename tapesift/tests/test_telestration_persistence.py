"""Marks survive a round trip through a real project file.

The column they use, ``overlays_json``, has existed since an early
migration and was never read or written by anything. These tests are what
makes it real, and they guard the case that matters most: a project written
before telestration existed must still open.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tapesift.database.repositories import ClipRepository
from tapesift.models.clip import Clip
from tapesift.models.telestration import (
    Mark, MarkKind, marks_from_json, marks_to_json)
from tapesift.services.project_service import ProjectSession


def _session(tmp_path: Path) -> ProjectSession:
    return ProjectSession.create("Telestration", tmp_path, tmp_path / "out")


def test_marks_survive_close_and_reopen(tmp_path):
    session = _session(tmp_path)
    db_path = session.db_path

    clip = Clip(start_ms=1_000, end_ms=6_000, clip_title="Duo")
    clip.overlays = marks_to_json([
        Mark(MarkKind.ARROW, [(0.30, 0.62), (0.78, 0.26)], ink="gold"),
        Mark(MarkKind.CIRCLE, [(0.20, 0.50), (0.28, 0.58)], ink="cyan"),
    ])
    session.add_clip(clip)
    session.save()
    session.close()

    reopened = ProjectSession.open(db_path)
    try:
        stored = reopened.clips[0]
        marks = marks_from_json(stored.overlays)
        assert len(marks) == 2
        assert marks[0].kind is MarkKind.ARROW
        assert marks[0].points[0] == (0.30, 0.62)
        assert marks[1].ink == "cyan"
    finally:
        reopened.close()


def test_a_clip_with_no_marks_round_trips_as_empty(tmp_path):
    session = _session(tmp_path)
    db_path = session.db_path
    session.add_clip(Clip(start_ms=0, end_ms=2_000))
    session.save()
    session.close()

    reopened = ProjectSession.open(db_path)
    try:
        assert reopened.clips[0].overlays == []
        assert marks_from_json(reopened.clips[0].overlays) == []
    finally:
        reopened.close()


def test_marks_can_be_edited_and_resaved(tmp_path):
    """Erasing back to nothing must persist as nothing."""
    session = _session(tmp_path)
    db_path = session.db_path
    clip = Clip(start_ms=0, end_ms=3_000)
    clip.overlays = marks_to_json(
        [Mark(MarkKind.ARROW, [(0.1, 0.1), (0.5, 0.5)])])
    session.add_clip(clip)
    session.save()
    session.close()

    reopened = ProjectSession.open(db_path)
    reopened.clips[0].overlays = []
    reopened.save()
    reopened.close()

    again = ProjectSession.open(db_path)
    try:
        assert again.clips[0].overlays == []
    finally:
        again.close()


def test_precision_is_enough_to_place_a_mark_on_a_player(tmp_path):
    """Six decimals is a tenth of a pixel on an 8K frame.

    Rounding is deliberate - full float repr bloats every project file with
    noise - so this checks the rounding kept enough to be exact in practice.
    """
    session = _session(tmp_path)
    db_path = session.db_path
    clip = Clip(start_ms=0, end_ms=1_000)
    original = (0.123456, 0.987654)
    clip.overlays = marks_to_json(
        [Mark(MarkKind.ARROW, [original, (0.5, 0.5)])])
    session.add_clip(clip)
    session.save()
    session.close()

    reopened = ProjectSession.open(db_path)
    try:
        point = marks_from_json(reopened.clips[0].overlays)[0].points[0]
        # On a 1920-wide frame this is well under a thousandth of a pixel.
        assert abs(point[0] - original[0]) < 1e-6
        assert abs(point[1] - original[1]) < 1e-6
    finally:
        reopened.close()


@pytest.mark.parametrize("corrupt", [
    "{broken",
    "",
    "{}",
    "null",
    '"not a list"',
])
def test_corrupt_overlay_json_cannot_prevent_project_reopen(
        tmp_path, corrupt):
    session = _session(tmp_path)
    db_path = session.db_path
    clip = session.add_clip(Clip(start_ms=0, end_ms=1_000))
    session.close()

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE clips SET overlays_json=? WHERE id=?",
            (corrupt, clip.id),
        )

    reopened = ProjectSession.open(db_path)
    try:
        assert reopened.get_clip(clip.id).overlays == []
    finally:
        reopened.close()


def test_overlay_recovery_does_not_mask_corrupt_clip_metadata(tmp_path):
    session = _session(tmp_path)
    db_path = session.db_path
    clip = session.add_clip(Clip(start_ms=0, end_ms=1_000))
    session.close()

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "UPDATE clips SET tags_json=? WHERE id=?",
            ("{broken", clip.id),
        )
        row = conn.execute(
            "SELECT * FROM clips WHERE id=?", (clip.id,)).fetchone()
        with pytest.raises(json.JSONDecodeError):
            ClipRepository._to_clip(row)
