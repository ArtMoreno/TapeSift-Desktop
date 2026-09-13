from pathlib import Path

import pytest

from tapesift.core.exceptions import DatabaseError
from tapesift.models.clip import Clip, ExportStatus
from tapesift.services.clip_factory import ClipDefaults, clip_from_timestamp
from tapesift.services.project_service import ProjectSession


@pytest.fixture
def session(tmp_path: Path):
    s = ProjectSession.create("Test Project", tmp_path, tmp_path / "out")
    yield s
    s.conn.close()


class TestProjectPersistence:
    def test_create_and_reopen(self, session, tmp_path):
        session.project.source_video_path = "C:/videos/source.mp4"
        session.project.source_duration_ms = 3_600_000
        session.project.quarter_markers_ms = [
            900_000, 1_800_000, 2_700_000, 3_300_000]
        session.project.tag_styles = {
            "pressure": {
                "color": "#f59e0b", "category": "Disruption",
                "primary": True, "show_on_timeline": True,
            }
        }
        clip = Clip(start_ms=1000, end_ms=5000, clip_title="First clip",
                    output_filename_base="First-clip", tags=["a", "b"],
                    analysis={"snap_prediction": {"source_ms": 2_500}})
        session.add_clip(clip)
        session.save()
        db_path = session.db_path
        session.conn.close()

        reopened = ProjectSession.open(db_path)
        assert reopened.project.name == "Test Project"
        assert reopened.project.source_video_path == "C:/videos/source.mp4"
        assert reopened.project.quarter_markers_ms == [
            900_000, 1_800_000, 2_700_000, 3_300_000]
        assert reopened.project.tag_styles == {
            "pressure": {
                "color": "#f59e0b", "category": "Disruption",
                "primary": True, "show_on_timeline": True,
            }
        }
        assert len(reopened.clips) == 1
        loaded = reopened.clips[0]
        assert loaded.clip_title == "First clip"
        assert loaded.output_filename_base == "First-clip"
        assert loaded.tags == ["a", "b"]
        assert loaded.analysis == {
            "snap_prediction": {"source_ms": 2_500}}
        assert loaded.export_status == ExportStatus.NOT_EXPORTED
        reopened.conn.close()

    def test_clip_ordering_survives_save(self, session):
        for i in range(3):
            session.add_clip(Clip(start_ms=i * 1000, end_ms=i * 1000 + 500,
                                  clip_title=f"clip{i}"))
        session.move_clip(2, 0)
        session.save()
        titles = [c.clip_title for c in session.clip_repo.list_for_project(session.project.id)]
        assert titles == ["clip2", "clip0", "clip1"]

    def test_clip_survives_reopen_without_explicit_save(self, session, tmp_path):
        """Regression: an added clip and an applied edit must hit disk
        immediately, so a crash/force-close between autosaves loses nothing.
        """
        clip = session.add_clip(Clip(start_ms=1000, end_ms=5000,
                                      clip_title="Unsaved clip", tags=["bomb"]))
        # No session.save() call here.
        db_path = session.db_path
        session.conn.close()

        reopened = ProjectSession.open(db_path)
        assert len(reopened.clips) == 1
        assert reopened.clips[0].clip_title == "Unsaved clip"
        assert reopened.clips[0].tags == ["bomb"]
        reopened.conn.close()

    def test_edit_persists_without_explicit_save(self, session, tmp_path):
        """Regression: a clip edit (title/notes/tags) is committed on apply,
        not held in memory until the next autosave/close."""
        clip = session.add_clip(Clip(start_ms=0, end_ms=1000, clip_title="Orig"))
        clip.clip_title = "Edited"
        clip.notes = "game-winning drive"
        clip.tags = ["explosive"]
        clip.touch()
        session.commit()
        db_path = session.db_path
        session.conn.close()

        reopened = ProjectSession.open(db_path)
        loaded = reopened.clips[0]
        assert loaded.clip_title == "Edited"
        assert loaded.notes == "game-winning drive"
        assert loaded.tags == ["explosive"]
        reopened.conn.close()

    def test_duplicate_project_file_rejected(self, session, tmp_path):
        from tapesift.core.exceptions import DatabaseError
        with pytest.raises(DatabaseError):
            ProjectSession.create("Test Project", tmp_path, tmp_path / "out")


class TestClipOperations:
    def test_clip_numbers_increment(self, session):
        a = session.add_clip(Clip(start_ms=0, end_ms=1000))
        b = session.add_clip(Clip(start_ms=0, end_ms=1000))
        assert (a.clip_number, b.clip_number) == (1, 2)

    def test_duplicate_clip(self, session):
        original = session.add_clip(Clip(start_ms=0, end_ms=1000, clip_title="X"))
        dup = session.duplicate_clip(original.id)
        assert dup is not None
        assert dup.id != original.id
        assert dup.clip_title == "X v2"
        assert len(session.clips) == 2

    def test_duplicate_versions_increment(self, session):
        original = session.add_clip(Clip(start_ms=0, end_ms=1000,
                                         clip_title="Touchdown"))
        v2 = session.duplicate_clip(original.id)
        v3 = session.duplicate_clip(original.id)
        v4 = session.duplicate_clip(v3.id)  # duplicating a version continues the series
        assert [v2.clip_title, v3.clip_title, v4.clip_title] == \
            ["Touchdown v2", "Touchdown v3", "Touchdown v4"]

    def test_duplicate_versions_export_distinct_names(self, session):
        from tapesift.services.filename_service import render_template
        original = session.add_clip(Clip(start_ms=0, end_ms=1000, clip_title="Play",
                                         output_filename_base="Play"))
        dup = session.duplicate_clip(original.id)
        a = render_template("{clip_name}", original)
        b = render_template("{clip_name}", dup)
        assert a != b

    def test_remove_clips(self, session):
        a = session.add_clip(Clip(start_ms=0, end_ms=1000))
        session.add_clip(Clip(start_ms=0, end_ms=1000))
        session.remove_clips([a.id])
        assert len(session.clips) == 1
        assert session.clips[0].order_index == 0


class TestUndoRedo:
    def test_undo_add(self, session):
        session.add_clip(Clip(start_ms=0, end_ms=1000))
        assert session.can_undo()
        session.undo()
        assert session.clips == []
        session.redo()
        assert len(session.clips) == 1

    def test_undo_delete(self, session):
        clip = session.add_clip(Clip(start_ms=0, end_ms=1000, clip_title="Keep me"))
        session.remove_clips([clip.id])
        assert session.clips == []
        session.undo()
        assert session.clips[0].clip_title == "Keep me"

    def test_undo_reorder(self, session):
        session.add_clip(Clip(start_ms=0, end_ms=1000, clip_title="A"))
        session.add_clip(Clip(start_ms=0, end_ms=1000, clip_title="B"))
        session.move_clip(1, 0)
        assert session.clips[0].clip_title == "B"
        session.undo()
        assert session.clips[0].clip_title == "A"

    def test_trim_boundary_is_committed_and_undoable(self, session):
        clip = session.add_clip(Clip(start_ms=1_000, end_ms=6_000))

        session.trim_clip_boundary(clip.id, "start", 2_000)

        assert clip.start_ms == 2_000
        assert session.undo() == "trim clip"
        assert session.clips[0].start_ms == 1_000

    def test_trim_boundary_enforces_minimum_duration(self, session):
        clip = session.add_clip(Clip(start_ms=1_000, end_ms=6_000))

        session.trim_clip_boundary(clip.id, "end", 1_050)

        assert clip.end_ms == 1_250


class TestDurations:
    def test_duration_property(self):
        assert Clip(start_ms=1_120_000, end_ms=1_135_000).duration_ms == 15_000

    def test_pre_post_roll_spec_example(self):
        # 18:45 with 5s pre / 8s post → 18:40 – 18:53
        clip, clamped = clip_from_timestamp(
            1_125_000, "Opening sequence", 3_600_000, ClipDefaults())
        assert clip.start_ms == 1_120_000
        assert clip.end_ms == 1_133_000
        assert not clamped
        assert clip.clip_title == "Opening sequence"
        assert clip.output_filename_base == "Opening-sequence"

    def test_clamp_at_zero(self):
        clip, clamped = clip_from_timestamp(2_000, "Early", 3_600_000, ClipDefaults())
        assert clip.start_ms == 0
        assert clamped

    def test_clamp_at_duration(self):
        clip, clamped = clip_from_timestamp(3_598_000, "Late", 3_600_000, ClipDefaults())
        assert clip.end_ms == 3_600_000
        assert clamped


class TestSplitAndOrdering:
    def test_split_creates_two_clips_in_place(self, session):
        session.add_clip(Clip(start_ms=0, end_ms=30_000, clip_title="Long play",
                              label="offense", tags=["Run"], notes="source",
                              details={"quarter": "Q1", "run_pass": "Run"},
                              export_preset="social"))
        session.add_clip(Clip(start_ms=40_000, end_ms=50_000,
                              clip_title="Following play"))
        source = session.clips[0]
        tail = session.split_clip(source.id, 12_000)
        assert len(session.clips) == 3
        assert (session.clips[0].start_ms, session.clips[0].end_ms) == (0, 12_000)
        assert (tail.start_ms, tail.end_ms) == (12_000, 30_000)
        assert session.clips[1] is tail            # sits right after, not last
        assert [clip.clip_number for clip in session.clips] == [1, 2, 3]
        assert tail.details == {"quarter": "Q1"}
        assert tail.clip_title == ""
        assert tail.label == ""
        assert tail.tags == []
        assert tail.notes == ""
        assert tail.export_preset == "social"

    def test_split_outside_the_clip_is_refused(self, session):
        session.add_clip(Clip(start_ms=5_000, end_ms=10_000))
        clip_id = session.clips[0].id
        with pytest.raises(DatabaseError):
            session.split_clip(clip_id, 20_000)
        assert len(session.clips) == 1

    def test_split_is_undoable(self, session):
        session.add_clip(Clip(start_ms=0, end_ms=30_000))
        session.split_clip(session.clips[0].id, 10_000)
        session.undo()
        assert len(session.clips) == 1
        assert session.clips[0].end_ms == 30_000

    def test_new_clips_slot_in_chronologically(self, session):
        session.add_clip(Clip(start_ms=30_000, end_ms=35_000, clip_title="C"))
        session.add_clip(Clip(start_ms=10_000, end_ms=15_000, clip_title="A"))
        session.add_clip(Clip(start_ms=20_000, end_ms=25_000, clip_title="B"))
        assert [c.clip_title for c in session.clips] == ["A", "B", "C"]
        assert [c.order_index for c in session.clips] == [0, 1, 2]

    def test_bulk_add_is_sorted_once(self, session):
        session.add_clips([
            Clip(start_ms=20_000, end_ms=25_000, clip_title="B"),
            Clip(start_ms=5_000, end_ms=9_000, clip_title="A"),
        ])
        assert [c.clip_title for c in session.clips] == ["A", "B"]


class TestSortByTime:
    def test_number_prefix_keeps_names_metadata_and_is_persisted_and_undoable(self, session):
        from dataclasses import asdict
        from tapesift.services.filename_service import render_template

        self._messy(session)
        session.project.naming_template = "{clip_name}_{clip_number}"
        session.clips[0].output_filename_base = "My custom name"
        session.clips[1].details = {"quarter": "Q2", "result": "Completion; First Down"}
        session.clips[2].enabled = False
        before = {clip.id: asdict(clip) for clip in session.clips}
        order = [clip.id for clip in session.clips]
        session.sort_clips_by_time(number_prefix=True)
        assert session.project.naming_template == "{clip_number}_{clip_name}"
        assert [clip.clip_number for clip in session.clips] == [1, 2, 3]
        assert [render_template(session.project.naming_template, clip) for clip in session.clips] == [
            "001_at-10s", "002_at-20s", "003_My-custom-name"]
        for clip in session.clips:
            for key, value in asdict(clip).items():
                if key not in {"clip_number", "order_index", "updated_at"}:
                    assert value == before[clip.id][key]
        session.undo()
        assert [clip.id for clip in session.clips] == order
        assert session.project.naming_template == "{clip_name}_{clip_number}"
        assert [clip.clip_number for clip in session.clips] == [before[key]["clip_number"] for key in order]
        session.redo()
        session.sort_clips_by_time(number_prefix=True)
        assert session.project.naming_template.count("{clip_number}") == 1
        reopened = ProjectSession.open(session.db_path)
        try:
            assert reopened.project.naming_template == "{clip_number}_{clip_name}"
            assert [clip.clip_number for clip in reopened.clips] == [1, 2, 3]
            assert reopened.clips[-1].output_filename_base == "My custom name"
        finally:
            reopened.conn.close()

    def _messy(self, session):
        # Out of time order AND with numbers that don't match position,
        # which is what a list built before chronological insert looks like.
        for start in (30_000, 10_000, 20_000):
            session.add_clip(Clip(start_ms=start, end_ms=start + 5_000,
                                  clip_title=f"at {start//1000}s"),
                             chronological=False)
        return session

    def test_sorts_into_film_order(self, session):
        self._messy(session)
        session.sort_clips_by_time()
        assert [c.start_ms for c in session.clips] == [10_000, 20_000, 30_000]
        assert [c.order_index for c in session.clips] == [0, 1, 2]

    def test_renumbering_is_opt_in(self, session):
        self._messy(session)
        before = [c.clip_number for c in session.clips]
        session.sort_clips_by_time(renumber=False)
        # Same numbers, just carried to their new positions.
        assert sorted(c.clip_number for c in session.clips) == sorted(before)
        session.sort_clips_by_time(renumber=True)
        assert [c.clip_number for c in session.clips] == [1, 2, 3]

    def test_is_undoable(self, session):
        self._messy(session)
        original = [c.start_ms for c in session.clips]
        session.sort_clips_by_time(renumber=True)
        session.undo()
        assert [c.start_ms for c in session.clips] == original

    def test_already_sorted_is_harmless(self, session):
        session.add_clip(Clip(start_ms=1_000, end_ms=2_000))
        session.add_clip(Clip(start_ms=5_000, end_ms=6_000))
        session.sort_clips_by_time(renumber=True)
        assert [c.start_ms for c in session.clips] == [1_000, 5_000]
        assert [c.clip_number for c in session.clips] == [1, 2]


class TestMergeClips:
    """Merging is the inverse of splitting: one play, one clip, described once."""

    def test_merge_fuses_the_span_and_starts_unlogged(self, session):
        first = session.add_clip(Clip(
            start_ms=1_000, end_ms=4_000, clip_title="Angle A",
            tags=["Run"], notes="wide", details={"play_type": "Run"}))
        second = session.add_clip(Clip(
            start_ms=4_500, end_ms=9_000, clip_title="Angle B",
            tags=["Pass"], notes="tight", details={"play_type": "Pass"}))
        third = session.add_clip(Clip(
            start_ms=20_000, end_ms=24_000, clip_title="Untouched",
            tags=["TD"]))

        merged = session.merge_clips([second.id, first.id])

        assert merged is not None
        assert merged.id == first.id           # the earliest keeps identity
        # The gap between the halves is the same play, so it is included.
        assert (merged.start_ms, merged.end_ms) == (1_000, 9_000)
        assert merged.clip_title == ""
        assert merged.tags == []
        assert merged.notes == ""
        assert merged.details == {}
        assert len(session.clips) == 2
        assert session.get_clip(second.id) is None
        assert session.get_clip(third.id).tags == ["TD"]
        # Ordering is re-derived; clip_number is a stable identity and keeps
        # its gap, exactly as it does after a delete.
        assert [clip.order_index for clip in session.clips] == [0, 1]
        assert [clip.clip_number for clip in session.clips] == [1, 3]

    def test_merge_survives_undo_and_redo(self, session):
        first = session.add_clip(Clip(
            start_ms=1_000, end_ms=4_000, clip_title="Angle A",
            details={"play_type": "Run"}))
        second = session.add_clip(Clip(
            start_ms=4_500, end_ms=9_000, clip_title="Angle B",
            details={"play_type": "Pass"}))

        session.merge_clips([first.id, second.id])
        session.undo()

        restored = {clip.id: clip for clip in session.clips}
        assert len(restored) == 2
        assert restored[first.id].clip_title == "Angle A"
        assert restored[first.id].end_ms == 4_000
        assert restored[second.id].details == {"play_type": "Pass"}

        session.redo()
        assert len(session.clips) == 1
        assert session.clips[0].start_ms == 1_000
        assert session.clips[0].end_ms == 9_000

    def test_merge_needs_two_distinct_clips(self, session):
        only = session.add_clip(Clip(start_ms=0, end_ms=2_000, tags=["Run"]))

        assert session.merge_clips([]) is None
        assert session.merge_clips([only.id]) is None
        assert session.merge_clips([only.id, only.id]) is None
        # A rejected merge leaves the clip and the undo stack alone.
        assert only.tags == ["Run"]
        assert len(session.clips) == 1

    def test_merge_reopens_from_disk(self, session, tmp_path):
        first = session.add_clip(Clip(start_ms=1_000, end_ms=4_000))
        second = session.add_clip(Clip(start_ms=4_500, end_ms=9_000))
        session.merge_clips([first.id, second.id])
        session.commit()
        session.conn.close()

        reopened = ProjectSession.open(tmp_path / "Test Project.tapesift")
        try:
            assert len(reopened.clips) == 1
            assert (reopened.clips[0].start_ms,
                    reopened.clips[0].end_ms) == (1_000, 9_000)
        finally:
            reopened.conn.close()
