from pathlib import Path

import pytest

from tapesift.models.clip import Clip
from tapesift.services import library_service
from tapesift.services.project_service import ProjectSession


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """Point the library catalog at an isolated temp database."""
    db = tmp_path / "library.db"
    monkeypatch.setattr(library_service, "catalog_path", lambda: db)
    return db


def _clip(title, tags=None, **details):
    return Clip(start_ms=1000, end_ms=6000, clip_title=title,
                tags=tags or [], details=details)


def _index(project_path, name, source, clips, opponent=""):
    rows = library_service.build_index_payload(
        project_path, name, source, clips, opponent=opponent)
    library_service.write_project_index(project_path, rows)


def test_game_year_persists_and_distinguishes_same_named_games(catalog, tmp_path):
    import sqlite3
    from tapesift.database.migrations import run_migrations
    from tapesift.models.project import normalize_game_year
    sessions = []
    try:
        for year in ("2024", "2025"):
            session = ProjectSession.create("Miami vs Stanford", tmp_path / year, tmp_path / "out")
            sessions.append(session)
            session.project.game_year = year
            session.add_clip(_clip("Opening play", quarter="Q1"))
            session.save()
            library_service.reindex_project_file(str(session.db_path))
            reopened = ProjectSession.open(session.db_path)
            assert reopened.project.game_year == year
            reopened.close()
        assert len(library_service.search()) == 2
        assert library_service.game_years() == ["2025", "2024"]
        assert [r.game_label for r in library_service.search(game_year="2025")] == ["2025 · Miami vs Stanford"]
        assert len(library_service.search("2024 Miami")) == 1
        assert {name for name, _ in library_service.projects()} == {"2024 · Miami vs Stanford", "2025 · Miami vs Stanford"}
        # Upgrade a pre-year project and catalog, retaining every existing clip.
        old = sessions[0]
        before = [tuple(row) for row in old.conn.execute("SELECT * FROM clips")]
        old.conn.execute("ALTER TABLE projects DROP COLUMN game_year")
        old.conn.execute("PRAGMA user_version=18")
        old.conn.commit()
        run_migrations(old.conn)
        assert old.conn.execute("SELECT game_year FROM projects").fetchone()[0] == ""
        assert [tuple(row) for row in old.conn.execute("SELECT * FROM clips")] == before
        with sqlite3.connect(catalog) as conn:
            conn.execute("ALTER TABLE library_clips DROP COLUMN game_year")
        rows = library_service.search()
        assert len(rows) == 2 and all(row.game_year == "" for row in rows)
        for invalid in ("25", "2025/26", "２０２５", "2200"):
            with pytest.raises(ValueError):
                normalize_game_year(invalid)
    finally:
        for session in sessions:
            session.conn.close()


def test_year_opponent_and_game_filters_intersect_and_include_unset_year(catalog):
    for path, year, opponent in (("a", "2025", "Central"), ("b", "2024", "Central"),
                                 ("c", "2025", "North"), ("d", "", "Central")):
        payload = library_service.build_index_payload(
            path, "Week 4", "film.mp4", [_clip("Pass")], opponent=opponent, game_year=year)
        library_service.write_project_index(path, payload)
    assert len(library_service.search(opponent="Central")) == 3
    assert library_service.projects(game_year="2025", opponent="Central") == [("2025 · Week 4", "a")]
    assert [r.project_path for r in library_service.search(game_year="2025", opponent="Central", project_path="a")] == ["a"]
    assert not library_service.search(game_year="2025", project_path="b")
    assert [r.project_path for r in library_service.search(game_year=library_service.UNSET_GAME_YEAR)] == ["d"]
    assert library_service.projects(game_year=library_service.UNSET_GAME_YEAR) == [("Week 4", "d")]


class TestSearch:
    def test_quarterback_is_searchable_involvement_without_becoming_primary(self, catalog):
        clips = [
            _clip("Receiver focus", player_name="Malik", other_players="Francis, Mark",
                  quarterback="10 Cam Ward"),
            _clip("Quarterback focus", player_name="10 Cam Ward", quarterback="10 CAM WARD"),
            _clip("Substitution", player_name="Malik", quarterback="11 Emory Williams"),
        ]
        _index("C:/proj/qb.clipforge", "Miami", "C:/vid/qb.mp4", clips)
        assert {r.clip_title for r in library_service.search(player="10 cam ward")} == {
            "Receiver focus", "Quarterback focus"}
        assert [r.clip_title for r in library_service.search(
            player="10 Cam Ward", player_highlighted_only=True)] == ["Quarterback focus"]
        assert [r.clip_title for r in library_service.search(player="Mark")] == ["Receiver focus"]
        names = library_service.players()
        assert "10 Cam Ward" in names and "11 Emory Williams" in names
        assert sum(name.casefold() == "10 cam ward" for name in names) == 1
        # Reads do not overwrite the stored roles or assign QB as primary.
        assert clips[0].details["player_name"] == "Malik"
        assert library_service.search(player="10 Cam Ward")[0].details["quarterback"] in {
            "10 Cam Ward", "10 CAM WARD"}

    def test_free_text_matches_title_and_details(self, catalog):
        _index("C:/proj/a.clipforge", "Game A", "C:/vid/a.mp4", [
            _clip("Big catch", tags=["Catch"], player_name="Damon Wilson"),
            _clip("Sack", tags=["Sack"], player_name="Joe Blow"),
        ])
        assert {r.clip_title for r in library_service.search("catch")} == {"Big catch"}
        assert {r.clip_title for r in library_service.search("damon")} == {"Big catch"}

    def test_free_text_terms_are_anded(self, catalog):
        _index("C:/proj/a.clipforge", "Game A", "C:/vid/a.mp4", [
            _clip("Pass TD", tags=["Pass TD"], quarter="Q3"),
            _clip("Pass incomplete", tags=["Incompletion"], quarter="Q1"),
        ])
        assert len(library_service.search("pass td")) == 1

    def test_free_text_matches_opponent_even_with_stale_search_blob(self, catalog):
        _index("C:/proj/a.clipforge", "Sample Game", "C:/vid/a.mp4",
               [_clip("Opening drive")], opponent="Notre Dame")
        conn = library_service._connect()
        with conn:
            conn.execute("UPDATE library_clips SET search_blob = ''")
        conn.close()

        rows = library_service.search("notre dame")

        assert [row.clip_title for row in rows] == ["Opening drive"]
        assert rows[0].opponent == "Notre Dame"

    def test_tag_filter_matches_whole_tag_only(self, catalog):
        _index("C:/proj/a.clipforge", "Game A", "C:/vid/a.mp4", [
            _clip("A", tags=["TD"]),
            _clip("B", tags=["Rushing TD"]),
        ])
        # Filtering by exact tag "TD" must not also match "Rushing TD".
        titles = {r.clip_title for r in library_service.search(tags=["TD"])}
        assert titles == {"A"}

    def test_tags_are_anded(self, catalog):
        _index("C:/proj/a.clipforge", "Game A", "C:/vid/a.mp4", [
            _clip("A", tags=["3rd Down", "Catch"]),
            _clip("B", tags=["3rd Down"]),
        ])
        assert {r.clip_title for r in
                library_service.search(tags=["3rd Down", "Catch"])} == {"A"}

    def test_search_spans_projects(self, catalog):
        _index("C:/proj/a.clipforge", "Game A", "C:/vid/a.mp4",
               [_clip("A td", tags=["Pass TD"])])
        _index("C:/proj/b.clipforge", "Game B", "C:/vid/b.mp4",
               [_clip("B td", tags=["Pass TD"])])
        assert len(library_service.search(tags=["Pass TD"])) == 2

    def test_reindex_replaces_project_rows(self, catalog):
        _index("C:/proj/a.clipforge", "Game A", "C:/vid/a.mp4",
               [_clip("Old", tags=["Catch"])])
        _index("C:/proj/a.clipforge", "Game A", "C:/vid/a.mp4",
               [_clip("New", tags=["Sack"])])
        assert {r.clip_title for r in library_service.search()} == {"New"}

    def test_remove_project(self, catalog):
        _index("C:/proj/a.clipforge", "Game A", "C:/vid/a.mp4",
               [_clip("A", tags=["Catch"])])
        library_service.remove_project("C:/proj/a.clipforge")
        assert library_service.search() == []


class TestRankingAndFilters:
    def _seed(self):
        _index("C:/p/a.clipforge", "Home vs Away", "C:/v/a.mp4", [
            _clip("Fletcher run", tags=["Run"]),
            _clip("Big play", tags=["Fletcher"]),
            _clip("Fletcher", tags=["Catch"]),
            _clip("Deep shot", tags=["Pass"], player_name="Fletcher"),
        ])
        _index("C:/p/b.clipforge", "Home vs Visitors", "C:/v/b.mp4", [
            _clip("Kick return", tags=["Special Teams"]),
        ])

    def test_exact_title_ranks_first(self, catalog):
        self._seed()
        rows = library_service.rank_results(
            library_service.search("fletcher"), "fletcher")
        assert rows[0].clip_title == "Fletcher"          # exact title
        titles = [r.clip_title for r in rows]
        assert "Kick return" not in titles

    def test_project_filter(self, catalog):
        self._seed()
        rows = library_service.search(project_path="C:/p/b.clipforge")
        assert [r.clip_title for r in rows] == ["Kick return"]

    def test_projects_listing(self, catalog):
        self._seed()
        names = [n for n, _ in library_service.projects()]
        assert names == ["Home vs Away", "Home vs Visitors"]

    def test_notes_are_searchable(self, catalog):
        clip = _clip("A play", tags=["Run"])
        clip.notes = "great second-level block"
        _index("C:/p/a.clipforge", "Game", "C:/v/a.mp4", [clip])
        rows = library_service.search("second-level")
        assert len(rows) == 1
        assert rows[0].notes == "great second-level block"


class TestTagsAndStats:
    def test_players_are_one_case_insensitive_library_value(self, catalog):
        _index("C:/proj/a.clipforge", "Game A", "C:/vid/a.mp4", [
            _clip("A", player_name="Jordan Davis"),
            _clip("B", player_name="jordan davis"),
            _clip("C", other_players="JORDAN DAVIS, Marcus Lee"),
        ])

        players = library_service.players()

        assert players.count("Jordan Davis") == 1
        assert sum(value.casefold() == "jordan davis" for value in players) == 1
        assert "Marcus Lee" in players

    def test_all_tags_sorted_by_frequency(self, catalog):
        _index("C:/proj/a.clipforge", "Game A", "C:/vid/a.mp4", [
            _clip("A", tags=["Catch", "Pass TD"]),
            _clip("B", tags=["Catch"]),
        ])
        tags = library_service.all_tags()
        assert tags[0] == "Catch"  # most common first
        assert set(tags) == {"Catch", "Pass TD"}

    def test_stats(self, catalog):
        _index("C:/proj/a.clipforge", "A", "C:/vid/a.mp4", [_clip("x", tags=["t"])])
        _index("C:/proj/b.clipforge", "B", "C:/vid/b.mp4", [_clip("y", tags=["t"])])
        assert library_service.stats() == (2, 2)


class TestDiscovery:
    def test_finds_projects_regardless_of_recents(self, catalog, tmp_path):
        """A wiped recents list must not hide projects from a rebuild."""
        folder = tmp_path / "projects"
        folder.mkdir()
        for name in ("Game A", "Game B"):
            s = ProjectSession.create(name, folder, folder / "out")
            s.save()
            s.conn.close()
        found = library_service.discover_project_files(
            [str(folder)], recent_projects=[])       # empty recents!
        assert len(found) == 2

    def test_scans_folders_of_recents_and_index(self, catalog, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        s = ProjectSession.create("Stray", elsewhere, elsewhere / "out")
        s.save()
        sibling = ProjectSession.create("Sibling", elsewhere, elsewhere / "out")
        sibling.save()
        stray_path = str(s.db_path)
        s.conn.close()
        sibling.conn.close()
        # Only the stray is in recents; its SIBLING is found via the folder.
        found = library_service.discover_project_files(
            [], recent_projects=[stray_path])
        assert len(found) == 2

    def test_ignores_the_retired_clipforge_extension(self, catalog, tmp_path):
        """Every project was converted to .tapesift, so a stray .clipforge
        file is someone else's, not an un-indexed project of ours."""
        legacy = tmp_path / "Archived game.clipforge"
        legacy.write_bytes(b"legacy project placeholder")
        found = library_service.discover_project_files(
            [str(tmp_path)], recent_projects=[])
        assert str(legacy) not in found

    def test_missing_folders_are_skipped(self, catalog):
        found = library_service.discover_project_files(
            ["C:/definitely/not/here"], recent_projects=["C:/nope/x.clipforge"])
        assert found == []


class TestReindexFromProjectFile:
    def test_round_trip_from_real_project(self, catalog, tmp_path):
        session = ProjectSession.create("Game", tmp_path, tmp_path / "out")
        session.project.source_video_path = "C:/vid/game.mp4"
        session.add_clip(_clip("Interception", tags=["Interception"],
                               player_name="Smith"))
        session.save()
        path = str(session.db_path)
        session.conn.close()

        count = library_service.reindex_project_file(path)
        assert count == 1
        results = library_service.search("interception")
        assert len(results) == 1
        assert results[0].player_name == "Smith"
        assert results[0].source_video_path == "C:/vid/game.mp4"


class TestCatalogIntegrity:
    def test_catalog_uses_wal(self, catalog):
        """#5: the shared catalog must be WAL so an interrupted write
        (force-quit / crash / power loss) self-heals instead of corrupting
        library.db - which every project depends on for search."""
        conn = library_service._connect()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode.lower() == "wal"

    def test_corrupt_catalog_rebuilds_on_connect(self, catalog):
        """#5: if a prior crash left library.db unreadable, _connect must
        drop and rebuild it rather than let every search fail silently."""
        library_service._connect().close()
        catalog.write_bytes(b"this is not a valid sqlite database\x00\x01\x02")
        # _connect should detect the corruption and reset, not raise.
        conn = library_service._connect()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='library_clips'").fetchall()
        conn.close()
        assert rows, "corrupt catalog was not rebuilt"
