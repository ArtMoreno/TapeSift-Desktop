"""SQLite connection management. One database file per project."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tapesift.database.migrations import assert_supported, run_migrations

PROJECT_FILE_EXTENSION = ".tapesift"
SUPPORTED_PROJECT_FILE_EXTENSIONS = (PROJECT_FILE_EXTENSION,)


def open_project_db(db_path: Path) -> sqlite3.Connection:
    """Open (or create) a project database and bring the schema up to date."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # Before journal_mode, which rewrites the file header: a project from
        # a newer build is one we refuse, and refusing should not have
        # already modified it.
        assert_supported(conn)
        conn.execute("PRAGMA journal_mode = WAL")
        run_migrations(conn)
    except BaseException:
        conn.close()
        raise
    return conn
