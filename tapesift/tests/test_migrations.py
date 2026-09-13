"""Schema migrations: all-or-nothing, and never silently downgrading.

Both properties protect the project file itself, which is the one thing in
TapeSift that cannot be regenerated from anything else.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tapesift.core.exceptions import DatabaseError
from tapesift.database import migrations
from tapesift.database.connection import open_project_db
from tapesift.database.migrations import MIGRATIONS, run_migrations


def _at_version(path: Path, version: int) -> None:
    """Build a project file stopped partway up the migration ladder."""
    conn = sqlite3.connect(path)
    try:
        for index, script in enumerate(MIGRATIONS[:version], start=1):
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {index}")
        conn.commit()
    finally:
        conn.close()


def _user_version(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


class TestAMigrationIsAllOrNothing:
    """The schema change and its version bump land together, or not at all.

    executescript runs DDL in autocommit. With the bump as a separate
    statement, dying in between left the tables created and the version
    unchanged - and the next open replayed the same migration onto tables
    that already existed, so the project stopped opening at all.
    """

    def test_a_failing_migration_leaves_no_trace(self, tmp_path, monkeypatch):
        project = tmp_path / "Half.tapesift"
        _at_version(project, len(MIGRATIONS))
        before = _user_version(project)

        # A migration whose second statement cannot apply: the first would
        # have been committed on its own under the old runner.
        monkeypatch.setattr(migrations, "MIGRATIONS", list(MIGRATIONS) + [
            "CREATE TABLE half_applied (a INTEGER);\n"
            "CREATE TABLE clips (this_table_already_exists INTEGER);"
        ])

        conn = sqlite3.connect(project)
        try:
            with pytest.raises(sqlite3.Error):
                run_migrations(conn)
        finally:
            conn.close()

        assert _user_version(project) == before
        conn = sqlite3.connect(project)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert "half_applied" not in tables

    def test_a_project_stopped_mid_ladder_still_opens(self, tmp_path):
        project = tmp_path / "Legacy.tapesift"
        _at_version(project, 6)

        conn = open_project_db(project)
        try:
            assert conn.execute(
                "PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        finally:
            conn.close()

    def test_reopening_a_current_project_changes_nothing(self, tmp_path):
        project = tmp_path / "Current.tapesift"
        open_project_db(project).close()
        assert _user_version(project) == len(MIGRATIONS)

        open_project_db(project).close()
        assert _user_version(project) == len(MIGRATIONS)

    def test_v13_project_missing_review_tables_is_repaired(self, tmp_path):
        project = tmp_path / "Incomplete-v13.tapesift"
        _at_version(project, 13)
        conn = sqlite3.connect(project)
        conn.execute("DROP TABLE autodetect_recoveries")
        conn.execute("DROP TABLE autodetect_review_batches")
        conn.commit()
        conn.close()

        migrated = open_project_db(project)
        try:
            tables = {row[0] for row in migrated.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            assert {
                "autodetect_review_batches",
                "autodetect_recoveries",
            } <= tables
            assert migrated.execute(
                "PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        finally:
            migrated.close()


class TestANewerProjectIsRefused:
    """Opening a project from a newer build would quietly delete its data.

    ClipRepository reads and writes a fixed column list and
    ProjectSession._write replaces every clip row on save, so anything a
    newer schema added survives exactly until the first autosave. Refusing
    is the only honest option for a file format described as portable.
    """

    def test_a_future_schema_raises_rather_than_opening(self, tmp_path):
        project = tmp_path / "FromTheFuture.tapesift"
        _at_version(project, len(MIGRATIONS))
        conn = sqlite3.connect(project)
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 3}")
        conn.commit()
        conn.close()

        with pytest.raises(DatabaseError) as caught:
            open_project_db(project)

        message = str(caught.value)
        assert "newer version" in message
        assert str(len(MIGRATIONS) + 3) in message

    def test_the_refusal_leaves_the_file_untouched(self, tmp_path):
        project = tmp_path / "FromTheFuture.tapesift"
        _at_version(project, len(MIGRATIONS))
        conn = sqlite3.connect(project)
        conn.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
        conn.commit()
        conn.close()
        before = project.read_bytes()

        with pytest.raises(DatabaseError):
            open_project_db(project)

        assert project.read_bytes() == before

    def test_the_current_version_is_not_refused(self, tmp_path):
        project = tmp_path / "Fine.tapesift"
        _at_version(project, len(MIGRATIONS))

        conn = open_project_db(project)
        conn.close()
