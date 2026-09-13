"""Rebuilding the Library index without losing clips.

The catalog is the only record of which folders to scan: discovery looks
in the default folder, the folders of recent projects, and the folders of
projects already indexed. So purging a project does not just drop its
clips — it forgets where that project lived, and a later rebuild will not
look there again. A wrong purge is therefore permanent in practice.

That makes the distinction between "deleted" and "can't be reached right
now" a data-safety question, not a cosmetic one. Film lives on external
drives; rebuilding with D: unplugged must not quietly empty the Library.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tapesift.models.clip import Clip
from tapesift.services import library_service as L
from tapesift.services.project_service import ProjectSession


@pytest.fixture(autouse=True)
def catalog(tmp_path, monkeypatch):
    """Point the catalog at a throwaway file, never the user's own."""
    monkeypatch.setattr(L, "catalog_path", lambda: tmp_path / "library.db")
    yield


def make_project(folder: Path, name: str, clips: int = 3) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    session = ProjectSession.create(name, folder, folder / "out")
    for i in range(clips):
        session.add_clip(Clip(start_ms=i * 10_000, end_ms=i * 10_000 + 5_000,
                              clip_title=f"Play {i}"))
    session.save()
    session.conn.close()
    return Path(session.db_path)


class TestUnreachableLocations:
    def test_unplugged_drive_keeps_its_clips(self, tmp_path):
        """The failure this guards: rebuild with the film drive unplugged.

        The catalog row is written directly with a path on a drive that
        does not exist, because that is precisely the state an unmounted
        external drive leaves behind and it cannot be produced by creating
        a real file.
        """
        external = r"Z:\Film\Indiana.tapesift"
        L.write_project_index(external, [
            dict(clip_uid=f"{external}#{i}", project_path=external,
                 project_name="Indiana", game_year="", source_video_path=r"Z:\Film\IU.mp4",
                 clip_id=str(i), clip_number=i, clip_title=f"Play {i}",
                 start_ms=i * 1000, end_ms=i * 1000 + 500, tags_text="",
                 tags_json="[]", details_json="{}", player_name="",
                 play_type="", quarter="", down_distance="", result="",
                 action="", other_players="", opponent="Indiana",
                 notes="", search_blob=f"play {i} indiana",
                 thumbnail_path="", updated_at=0)
            for i in range(3)])
        assert L.stats()[0] == 3

        clips, indexed, skipped = L.rebuild_from_projects([])
        assert skipped == 1
        assert L.stats()[0] == 3, "clips were purged for an unreachable path"
        assert "Indiana" in {n for n, _ in L.projects()}

    def test_unreachable_is_distinguished_from_deleted(self, tmp_path):
        assert L.is_unreachable(r"Z:\NotMounted\game.tapesift")
        existing_folder = tmp_path / "here" / "gone.tapesift"
        (tmp_path / "here").mkdir()
        assert not L.is_unreachable(str(existing_folder))

    def test_catalog_entry_on_missing_drive_survives_a_rescan(self, tmp_path):
        """A rebuild that scans other folders must not evict it."""
        gone = make_project(tmp_path / "ext", "OnExternal")
        L.rebuild_from_projects([str(gone)])
        rows_before = L.stats()[0]

        # The project's whole folder disappears, and we rebuild from a
        # different folder entirely.
        for f in (tmp_path / "ext").iterdir():
            f.unlink()
        (tmp_path / "ext" / "out").rmdir() if (tmp_path / "ext" / "out").is_dir() else None
        (tmp_path / "ext").rmdir()
        other = make_project(tmp_path / "local", "Pittsburgh")
        clips, projects, skipped = L.rebuild_from_projects([str(other)])

        assert skipped == 1
        names = {n for n, _ in L.projects()}
        assert "OnExternal" in names, "unreachable project was evicted"
        assert "Pittsburgh" in names
        assert L.stats()[0] == rows_before + 3


class TestDeletedProjects:
    def test_deleted_project_is_purged(self, tmp_path):
        """The other half: a file deleted from a folder we CAN read must
        not leave orphan rows behind, as a deleted project once did."""
        project = make_project(tmp_path / "projects", "Scratch")
        L.rebuild_from_projects([str(project)])
        assert L.stats()[1] == 1

        project.unlink()                       # folder still exists
        other = make_project(tmp_path / "projects", "Keeper")
        clips, projects, skipped = L.rebuild_from_projects([str(other)])

        names = {n for n, _ in L.projects()}
        assert "Scratch" not in names, "orphan rows survived"
        assert names == {"Keeper"}
        assert skipped == 0

    def test_rebuild_reconciles_projects_the_scan_missed(self, tmp_path):
        """Discovery only returns files that exist, so a deleted project is
        never in the scan list — the catalog itself has to be consulted."""
        project = make_project(tmp_path / "projects", "Ghost")
        L.rebuild_from_projects([str(project)])
        project.unlink()

        L.rebuild_from_projects([])            # nothing discovered at all
        assert L.projects() == []
