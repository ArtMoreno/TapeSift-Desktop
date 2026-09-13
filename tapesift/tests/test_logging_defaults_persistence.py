"""A video remembers logging defaults without rewriting previously logged plays."""

import json
import sqlite3

from tapesift.database.connection import open_project_db
from tapesift.database.migrations import MIGRATIONS
from tapesift.database.repositories import ClipRepository
from tapesift.models.clip import Clip
from tapesift.models.project import Project
from tapesift.services.project_service import ProjectSession
from tapesift.services.detail_service import quarterback_at, with_quarterback_change


def test_substitutions_follow_film_order_survive_reopen_and_undo_together(tmp_path):
    initial = {"quarter": "Q1", "quarterback": "Starter"}
    changes = with_quarterback_change(initial, 30_000, "Backup")
    changes = with_quarterback_change(changes, 60_000, "Third QB")
    changes = with_quarterback_change(changes, 30_000, "Corrected backup")
    assert initial == {"quarter": "Q1", "quarterback": "Starter"}
    assert [quarterback_at(changes, ms) for ms in (0, 29_999, 30_000, 59_999, 60_000)] == [
        "Starter", "Starter", "Corrected backup", "Corrected backup", "Third QB"]
    assert quarterback_at(with_quarterback_change(changes, 70_000, ""), 80_000) == ""
    session = ProjectSession.create("QB sequence", tmp_path / "projects", tmp_path / "exports")
    try:
        clip = session.add_clip(Clip(30_000, 35_000, details={"quarterback": "Original"}))
        session.project.logging_defaults = initial
        session.checkpoint("QB substitution", include_logging_defaults=True)
        clip.details["quarterback"] = "Corrected backup"
        session.project.logging_defaults = changes
        session.commit()
        session.undo()
        assert session.project.logging_defaults == initial
        assert session.get_clip(clip.id).details["quarterback"] == "Original"
        session.redo()
        assert session.project.logging_defaults == changes
        assert session.get_clip(clip.id).details["quarterback"] == "Corrected backup"
        reopened = ProjectSession.open(session.db_path)
        try:
            assert reopened.project.logging_defaults == changes
            assert quarterback_at(reopened.project.logging_defaults, 60_000) == "Third QB"
        finally:
            reopened.conn.close()
    finally:
        session.conn.close()


def test_v17_upgrade_preserves_saved_clip_and_defaults_survive_reopen(tmp_path):
    path = tmp_path / "existing-v17.tapesift"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for version, migration in enumerate(MIGRATIONS[:17], start=1):
        conn.executescript(migration)
        conn.execute(f"PRAGMA user_version = {version}")
    conn.execute(
        "INSERT INTO projects (id,name,created_at,updated_at,game_team_ids_json) VALUES (1,?,?,?,?)",
        ("Existing film", "2026-09-06", "2026-09-06", json.dumps(["cfbd:team:2390", "cfbd:team:24"])))
    old = Clip(1000, 5000, project_id=1, clip_title="My manual clip title",
               output_filename_base="my-custom-name", notes="Analyst note",
               details={"quarter": "Q1", "quarterback": "#10 Original QB",
                        "other_players": "#1 Receiver, #4 Runner", "result": "Completion",
                        "ball_on": "OWN 25", "yards": "18", "yac": "11"},
               tags=["custom tag"], source_photo={"timestamp_ms": 1000},
               source_photo_png=b"existing-photo-payload")
    ClipRepository(conn).save(old)
    before = dict(conn.execute("SELECT * FROM clips WHERE id=?", (old.id,)).fetchone())
    conn.close()

    migrated = open_project_db(path)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    assert dict(migrated.execute("SELECT * FROM clips WHERE id=?", (old.id,)).fetchone()) == before
    assert migrated.execute("SELECT logging_defaults_json FROM projects").fetchone()[0] == "{}"
    migrated.close()

    session = ProjectSession.open(path)
    assert session.project.logging_defaults == {}
    session.project.logging_defaults = {"quarter": "OT", "quarterback": "#12 Substitute"}
    session.save()
    session.conn.close()
    reopened = ProjectSession.open(path)
    try:
        assert reopened.project.logging_defaults == {"quarter": "OT", "quarterback": "#12 Substitute"}
        assert reopened.project.game_team_ids == ["cfbd:team:2390", "cfbd:team:24"]
        loaded = reopened.clips[0]
        assert loaded.details == old.details
        assert loaded.clip_title == old.clip_title
        assert loaded.output_filename_base == old.output_filename_base
        assert loaded.notes == old.notes
        assert loaded.tags == old.tags
        assert loaded.source_photo == old.source_photo
        assert loaded.source_photo_png == old.source_photo_png
        assert (loaded.start_ms, loaded.end_ms) == (1000, 5000)
        assert Project("Another video").logging_defaults == {}
    finally:
        reopened.conn.close()
