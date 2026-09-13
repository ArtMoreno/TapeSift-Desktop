"""Renaming and deleting projects."""

from pathlib import Path

import pytest

from tapesift.core.exceptions import DatabaseError
from tapesift.database.connection import PROJECT_FILE_EXTENSION
from tapesift.models.clip import Clip
from tapesift.services.project_service import (
    ProjectSession, delete_project, rename_project,
)


def _make(tmp_path: Path, name: str = "Game One") -> Path:
    session = ProjectSession.create(name, tmp_path, tmp_path / "out")
    session.add_clip(Clip(start_ms=0, end_ms=5000, clip_title="A play"))
    session.save()
    path = session.db_path
    session.conn.close()
    return path


class TestRename:
    def test_renames_file_and_stored_name(self, tmp_path: Path):
        path = _make(tmp_path)
        new_path = rename_project(path, "Home vs Away")
        assert new_path.name == f"Home vs Away{PROJECT_FILE_EXTENSION}"
        assert new_path.is_file() and not path.exists()
        reopened = ProjectSession.open(new_path)
        assert reopened.project.name == "Home vs Away"
        assert len(reopened.clips) == 1          # content preserved
        reopened.conn.close()

    def test_rejects_empty_name(self, tmp_path: Path):
        path = _make(tmp_path)
        with pytest.raises(DatabaseError):
            rename_project(path, "   ")

    def test_rejects_invalid_filename_characters(self, tmp_path: Path):
        path = _make(tmp_path)
        with pytest.raises(DatabaseError):
            rename_project(path, "Miami / FSU")

    def test_rejects_collision(self, tmp_path: Path):
        first = _make(tmp_path, "Game One")
        _make(tmp_path, "Game Two")
        with pytest.raises(DatabaseError):
            rename_project(first, "Game Two")
        assert first.is_file()                   # untouched after refusal

    def test_missing_project(self, tmp_path: Path):
        with pytest.raises(DatabaseError):
            rename_project(tmp_path / f"nope{PROJECT_FILE_EXTENSION}", "X")


class TestEditClipMetadata:
    def test_round_trip(self, tmp_path: Path):
        from tapesift.services.project_service import edit_clip_metadata
        path = _make(tmp_path)
        session = ProjectSession.open(path)
        clip_id = session.clips[0].id
        session.conn.close()

        edit_clip_metadata(path, clip_id, clip_title="Fixed name",
                           tags=["Run", " Pressure "], notes="better note",
                           details={"quarter": "Q3", "result": "  "})
        session = ProjectSession.open(path)
        clip = session.clips[0]
        assert clip.clip_title == "Fixed name"
        assert clip.tags == ["Run", "Pressure"]          # trimmed
        assert clip.notes == "better note"
        assert clip.details == {"quarter": "Q3"}         # empties dropped
        # untouched fields survive
        assert (clip.start_ms, clip.end_ms) == (0, 5000)
        session.conn.close()

    def test_unknown_clip_raises(self, tmp_path: Path):
        from tapesift.services.project_service import edit_clip_metadata
        path = _make(tmp_path)
        with pytest.raises(DatabaseError):
            edit_clip_metadata(path, "nope", clip_title="x", tags=[],
                               notes="", details={})


class TestDuplicate:
    def test_copy_gets_new_name_and_keeps_clips(self, tmp_path: Path):
        from tapesift.services.project_service import duplicate_project
        original = _make(tmp_path, "Game One")
        copy_path = duplicate_project(original)
        assert copy_path.name == f"Game One copy{PROJECT_FILE_EXTENSION}"
        assert original.is_file()                       # source untouched
        session = ProjectSession.open(copy_path)
        assert session.project.name == "Game One copy"
        assert len(session.clips) == 1
        session.conn.close()

    def test_second_copy_numbers_up(self, tmp_path: Path):
        from tapesift.services.project_service import duplicate_project
        original = _make(tmp_path, "Game One")
        duplicate_project(original)
        second = duplicate_project(original)
        assert second.name == f"Game One copy 2{PROJECT_FILE_EXTENSION}"

    def test_missing_project(self, tmp_path: Path):
        from tapesift.services.project_service import duplicate_project
        with pytest.raises(DatabaseError):
            duplicate_project(tmp_path / f"nope{PROJECT_FILE_EXTENSION}")


class TestDelete:
    def test_deletes_file_only(self, tmp_path: Path):
        path = _make(tmp_path)
        video = tmp_path / "source.mp4"
        video.write_bytes(b"fake video")
        exports = tmp_path / "out"
        exports.mkdir(exist_ok=True)
        (exports / "clip.mp4").write_bytes(b"exported")

        delete_project(path)

        assert not path.exists()
        assert video.exists()                    # source never touched
        assert (exports / "clip.mp4").exists()   # exports never touched

    def test_missing_project(self, tmp_path: Path):
        with pytest.raises(DatabaseError):
            delete_project(tmp_path / f"nope{PROJECT_FILE_EXTENSION}")
