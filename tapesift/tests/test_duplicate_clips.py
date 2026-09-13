"""Duplicate detection must never lose the copy carrying the logging."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.models.clip import Clip  # noqa: E402
from tapesift.services.duplicate_service import (  # noqa: E402
    find_duplicate_groups, metadata_score, removable_clip_ids,
)
from tapesift.services.project_service import ProjectSession  # noqa: E402
from tapesift.ui.duplicate_clips_dialog import DuplicateClipsDialog  # noqa: E402
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
from tapesift.ui_v2.main_window import MainWindowV2  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def make(start, end, **kwargs):
    return Clip(start_ms=start, end_ms=end, **kwargs)


class TestGrouping:
    def test_distinct_ranges_are_never_grouped(self):
        clips = [make(0, 1_000), make(2_000, 3_000), make(4_000, 5_000)]
        assert find_duplicate_groups(clips) == []

    def test_identical_ranges_group_together(self):
        clips = [make(0, 1_000), make(0, 1_000), make(0, 1_000)]
        groups = find_duplicate_groups(clips)

        assert len(groups) == 1
        assert len(groups[0].clips) == 3
        assert len(groups[0].remove) == 2

    def test_overlapping_but_different_ranges_are_left_alone(self):
        """An overlap is a conflict to review, not a duplicate to delete."""
        clips = [make(0, 10_000), make(5_000, 15_000)]

        assert find_duplicate_groups(clips) == []

    def test_tolerance_matches_nudged_copies_on_both_edges(self):
        clips = [make(0, 10_000), make(120, 10_100)]

        assert find_duplicate_groups(clips) == []
        assert len(find_duplicate_groups(clips, tolerance_ms=200)) == 1

    def test_tolerance_does_not_chain_across_a_drifting_run(self):
        """Anchoring on the first member stops a chain collapsing into one."""
        clips = [make(0, 10_000), make(150, 10_150), make(300, 10_300)]

        groups = find_duplicate_groups(clips, tolerance_ms=200)

        assert len(groups) == 1
        assert len(groups[0].clips) == 2


class TestWhichCopySurvives:
    def test_the_logged_copy_is_kept(self):
        bare = make(0, 1_000, clip_number=1)
        logged = make(0, 1_000, clip_number=2,
                      details={"quarter": "1", "result": "First Down"},
                      notes="great block")
        groups = find_duplicate_groups([bare, logged])

        assert groups[0].keep.id == logged.id
        assert removable_clip_ids(groups) == [bare.id]

    def test_notes_and_tags_outweigh_a_bare_title(self):
        titled = make(0, 1_000, clip_title="Play 001")
        tagged = make(0, 1_000, tags=["Explosive", "RedZone"])

        assert metadata_score(tagged) > metadata_score(titled)

    def test_equal_copies_keep_the_earliest(self):
        first = make(0, 1_000, clip_number=1, order_index=0)
        second = make(0, 1_000, clip_number=2, order_index=1)

        assert find_duplicate_groups([first, second]).pop().keep.id == first.id

    def test_whitespace_is_not_metadata(self):
        assert metadata_score(make(0, 1_000, clip_title="   ",
                                   notes="\n", tags=["  "])) == 0


class TestDialog:
    def test_dialog_reports_ids_without_touching_clips(self, qapp):
        clips = [make(0, 1_000), make(0, 1_000), make(5_000, 6_000)]
        dialog = DuplicateClipsDialog(clips)

        assert dialog.table.rowCount() == 1
        assert len(dialog.clip_ids_to_remove()) == 1
        assert dialog.remove_button.isEnabled()
        assert len(clips) == 3

    def test_a_clean_project_offers_nothing_to_remove(self, qapp):
        dialog = DuplicateClipsDialog([make(0, 1_000), make(2_000, 3_000)])

        assert dialog.table.rowCount() == 0
        assert dialog.clip_ids_to_remove() == []
        assert not dialog.remove_button.isEnabled()


def test_removal_is_one_undoable_edit(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowWorkflow, "_check_recovery", lambda self: None)
    settings = AppSettings(onboarding_seen=True, review_mode=False)
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV2(settings)
    session = ProjectSession.create("Dupes", tmp_path, tmp_path / "out")
    keep = session.add_clip(Clip(start_ms=0, end_ms=10_000,
                                 details={"quarter": "1"}))
    session.add_clip(Clip(start_ms=0, end_ms=10_000))
    session.add_clip(Clip(start_ms=0, end_ms=10_000))
    session.add_clip(Clip(start_ms=20_000, end_ms=30_000))
    session.project.source_duration_ms = 60_000
    window.session = session
    window._refresh_clip_list()
    monkeypatch.setattr(window, "_start_thumbnails", lambda _clips=None: None)
    monkeypatch.setattr(window, "_index_current_project", lambda: None)
    monkeypatch.setattr(
        DuplicateClipsDialog, "exec",
        lambda self: QDialog.DialogCode.Accepted)

    window._find_duplicate_clips()

    assert len(session.clips) == 2
    assert keep.id in {clip.id for clip in session.clips}
    assert session.undo() == "delete clips"
    assert len(session.clips) == 4

    session.conn.close()
    window.hide()
