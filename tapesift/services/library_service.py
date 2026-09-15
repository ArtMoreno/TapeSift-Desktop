"""Library catalog: cross-project clip index for tag/metadata search.

Every TapeSift project is its own SQLite file. To find clips *across* games
("all Pass TDs this season") we maintain one central catalog database in the
app-data folder. Each project's clips are upserted into the catalog whenever
the project saves; the Library Search screen queries only the catalog, so it
is fast and works even when projects are closed.

The catalog is a derived index, never the source of truth - it can be deleted
and rebuilt from the projects at any time. Nothing here runs on the playback
path; indexing happens on save (on a worker thread) and search happens only
while the Library screen is open.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from tapesift.core import paths
from tapesift.services import tag_service
from tapesift.services.detail_service import DETAIL_KEYS
from tapesift.database.connection import SUPPORTED_PROJECT_FILE_EXTENSIONS

log = logging.getLogger(__name__)

TAG_DELIM = "|"  # wraps each tag so LIKE '%|tag|%' matches whole tags only

# Detail fields promoted to their own columns for structured filtering.
PROMOTED_DETAILS = ["player_name", "play_type", "quarter", "down_distance", "result"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_clips (
    clip_uid          TEXT PRIMARY KEY,
    project_path      TEXT NOT NULL,
    project_name      TEXT NOT NULL,
    game_year         TEXT NOT NULL DEFAULT '',
    source_video_path TEXT NOT NULL,
    clip_id           TEXT NOT NULL,
    clip_number       INTEGER,
    clip_title        TEXT,
    start_ms          INTEGER,
    end_ms            INTEGER,
    tags_text         TEXT,
    tags_json         TEXT,
    details_json      TEXT,
    player_name       TEXT,
    play_type         TEXT,
    quarter           TEXT,
    down_distance     TEXT,
    result            TEXT,
    action            TEXT,
    other_players     TEXT,
    opponent          TEXT,
    notes             TEXT,
    search_blob       TEXT,
    thumbnail_path    TEXT,
    updated_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_lib_project ON library_clips(project_path);
CREATE INDEX IF NOT EXISTS idx_lib_player  ON library_clips(player_name);
CREATE INDEX IF NOT EXISTS idx_lib_playtype ON library_clips(play_type);
"""


def catalog_path() -> Path:
    return paths.app_data_dir() / "library.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(catalog_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        # WAL keeps the shared catalog self-healing: an interrupted write (a
        # forced quit, a power loss, or a crash) is reconciled from the
        # -wal/-shm files on the next open instead of corrupting library.db.
        # Every project shares this one file, so its integrity matters more
        # than a single project's .tapesift db.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.executescript(_SCHEMA)
        # The catalog is derived data: if the schema gained columns since this
        # file was written, drop and let a rebuild repopulate it.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(library_clips)")}
        if not {"notes", "opponent", "action", "other_players"} <= cols:
            conn.executescript("DROP TABLE IF EXISTS library_clips;")
            conn.executescript(_SCHEMA)
            log.info("Library catalog schema upgraded; index needs a rebuild")
            return conn
        if "game_year" not in cols:
            conn.execute("ALTER TABLE library_clips ADD COLUMN game_year TEXT NOT NULL DEFAULT ''")
            conn.commit()
        # If a prior crash left the catalog unreadable, drop and rebuild from
        # projects rather than letting every search fail silently.
        conn.execute("PRAGMA integrity_check(1)")
    except sqlite3.DatabaseError:
        # The file is junk (not a valid SQLite db, or a half-written WAL
        # remnant). Reset and reconnect cleanly.
        log.warning("Library catalog unreadable; rebuilding from projects")
        conn.close()
        _reset_catalog()
        return _connect()
    return conn


def _reset_catalog() -> None:
    """Delete library.db and its WAL companions so the next connect starts clean."""
    for suffix in ("", "-wal", "-shm"):
        p = catalog_path().with_suffix(".db" + suffix) if suffix else catalog_path()
        try:
            p.unlink()
        except FileNotFoundError:
            pass


@dataclass
class LibraryRow:
    clip_uid: str
    project_path: str
    project_name: str
    source_video_path: str
    clip_id: str
    clip_number: int
    clip_title: str
    start_ms: int
    end_ms: int
    tags: list[str]
    player_name: str
    play_type: str
    quarter: str
    down_distance: str
    result: str
    notes: str
    thumbnail_path: str
    updated_at: str = ""
    details: dict = None  # full details dict, for editing from the library
    action: str = ""
    other_players: str = ""
    opponent: str = ""
    game_year: str = ""

    @property
    def game_label(self) -> str:
        return f"{self.game_year} · {self.project_name}" if self.game_year else self.project_name

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


def build_index_payload(project_path: str, project_name: str,
                        source_video_path: str, clips,
                        opponent: str = "", game_year: str = "") -> list[dict]:
    """Snapshot clips into plain dicts on the calling (UI) thread.

    Returns rows ready for `write_project_index`, which may run on a worker
    thread - so we never touch mutable Clip objects off the UI thread.
    """
    rows: list[dict] = []
    for clip in clips:
        details = dict(clip.details or {})
        # Index on canonical keys so "Run"/"run" and "CJ Daniels"/"Cj Daniels"
        # match each other. The clip's own tags are left exactly as typed.
        tags = tag_service.dedupe_tags(list(clip.tags or []))
        keys = [tag_service.tag_key(t) for t in tags]
        tags_text = TAG_DELIM + TAG_DELIM.join(k for k in keys if k) + TAG_DELIM \
            if keys else ""
        blob_parts = [clip.clip_title or ""]
        blob_parts.extend(tags)
        blob_parts.extend(details.get(k, "") for k in DETAIL_KEYS)
        blob_parts.append(project_name)
        blob_parts.append(clip.notes or "")
        blob_parts.append(opponent)      # searchable by who you played
        blob_parts.append(game_year)
        search_blob = " ".join(p for p in blob_parts if p).lower()
        rows.append({
            "clip_uid": f"{project_path}::{clip.id}",
            "project_path": project_path,
            "project_name": project_name,
            "game_year": game_year,
            "source_video_path": source_video_path,
            "clip_id": clip.id,
            "clip_number": clip.clip_number,
            "clip_title": clip.clip_title or "",
            "start_ms": clip.start_ms,
            "end_ms": clip.end_ms,
            "tags_text": tags_text,
            "tags_json": json.dumps(tags),
            "details_json": json.dumps(details),
            "player_name": details.get("player_name", ""),
            "play_type": details.get("play_type", ""),
            "quarter": details.get("quarter", ""),
            "down_distance": details.get("down_distance", ""),
            "result": details.get("result", ""),
            "action": details.get("action", ""),
            "other_players": details.get("other_players", ""),
            "opponent": opponent,
            "notes": clip.notes or "",
            "search_blob": search_blob,
            "thumbnail_path": clip.thumbnail_path or "",
            "updated_at": clip.updated_at,
        })
    return rows


_COLUMNS = [
    "clip_uid", "project_path", "project_name", "game_year", "source_video_path", "clip_id",
    "clip_number", "clip_title", "start_ms", "end_ms", "tags_text", "tags_json",
    "details_json", "player_name", "play_type", "quarter", "down_distance",
    "result", "action", "other_players", "opponent", "notes",
    "search_blob", "thumbnail_path", "updated_at",
]


def write_project_index(project_path: str, rows: list[dict]) -> None:
    """Replace all catalog rows for one project. Safe to call off-thread."""
    placeholders = ",".join("?" for _ in _COLUMNS)
    try:
        # closing(), because `with conn:` is a transaction block and not a
        # close: the old code only reached conn.close() on the happy path, so
        # every failed write leaked a connection - and this runs on a daemon
        # thread after every edit. A live connection keeps library.db's -shm
        # file alive, so the leak is not just memory.
        with contextlib.closing(_connect()) as conn:
            with conn:
                conn.execute("DELETE FROM library_clips WHERE project_path=?",
                             (project_path,))
                conn.executemany(
                    f"INSERT INTO library_clips ({','.join(_COLUMNS)}) "
                    f"VALUES ({placeholders})",
                    [tuple(r[c] for c in _COLUMNS) for r in rows])
        log.debug("Indexed %d clips for %s", len(rows), project_path)
    except sqlite3.DatabaseError:
        log.exception("Library index write failed for %s", project_path)


def remove_project(project_path: str) -> None:
    try:
        conn = _connect()
        with conn:
            conn.execute("DELETE FROM library_clips WHERE project_path=?",
                         (project_path,))
        conn.close()
    except sqlite3.DatabaseError:
        log.exception("Library index removal failed for %s", project_path)


def _row_to_dataclass(row: sqlite3.Row) -> LibraryRow:
    return LibraryRow(
        clip_uid=row["clip_uid"], project_path=row["project_path"],
        project_name=row["project_name"],
        game_year=row["game_year"] or "",
        source_video_path=row["source_video_path"], clip_id=row["clip_id"],
        clip_number=row["clip_number"] or 0, clip_title=row["clip_title"] or "",
        start_ms=row["start_ms"] or 0, end_ms=row["end_ms"] or 0,
        tags=json.loads(row["tags_json"] or "[]"),
        player_name=row["player_name"] or "", play_type=row["play_type"] or "",
        quarter=row["quarter"] or "", down_distance=row["down_distance"] or "",
        result=row["result"] or "", notes=row["notes"] or "",
        thumbnail_path=row["thumbnail_path"] or "",
        updated_at=row["updated_at"] or "",
        details=json.loads(row["details_json"] or "{}"),
        action=row["action"] or "",
        other_players=row["other_players"] or "",
        opponent=row["opponent"] or "")


UNSET_GAME_YEAR = "__unset__"


def search(text: str = "", tags: list[str] | None = None,
           project_path: str = "", opponent: str = "", player: str = "",
           player_highlighted_only: bool = False,
           limit: int = 500, game_year: str = "") -> list[LibraryRow]:
    """Find catalog clips matching free-text terms (AND) and required tags (AND).

    Free-text terms match anywhere (title, tags, details, game name, opponent);
    each tag must be present as a whole tag on the clip.
    """
    clauses: list[str] = []
    params: list[str] = []
    if game_year:
        clauses.append("game_year = ?")
        params.append("" if game_year == UNSET_GAME_YEAR else game_year)
    if project_path:
        clauses.append("project_path = ?")
        params.append(project_path)
    if opponent:
        clauses.append("opponent = ?")
        params.append(opponent)
    if player:
        if player_highlighted_only:
            # Only clips this player is the subject of.
            clauses.append("LOWER(player_name) = ?")
            params.append(player.strip().lower())
        else:
            # QB is an involvement role, not necessarily the clip's subject.
            clauses.append("(LOWER(player_name) = ? OR "
                           "LOWER(other_players) LIKE ? OR "
                           "LOWER(json_extract(details_json, '$.quarterback')) = ?)")
            params.append(player.strip().lower())
            params.append(f"%{player.strip().lower()}%")
            params.append(player.strip().lower())
    for term in text.lower().split():
        # Match the dedicated opponent column as well as the cached blob. This
        # keeps opponent search reliable for catalogs built before opponents
        # were included in search_blob.
        clauses.append("(search_blob LIKE ? OR LOWER(opponent) LIKE ?)")
        params.append(f"%{term}%")
        params.append(f"%{term}%")
    for tag in tags or []:
        key = tag_service.tag_key(tag)
        if not key:
            continue
        clauses.append("tags_text LIKE ?")
        params.append(f"%{TAG_DELIM}{key}{TAG_DELIM}%")
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (f"SELECT * FROM library_clips{where} "
           f"ORDER BY project_name, clip_number LIMIT ?")
    params.append(limit)
    try:
        conn = _connect()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
    except sqlite3.DatabaseError:
        log.exception("Library search failed")
        return []
    return [_row_to_dataclass(r) for r in rows]


def projects(*, game_year: str = "", opponent: str = "") -> list[tuple[str, str]]:
    """Indexed games, optionally narrowed by year and opponent."""
    clauses, params = [], []
    if game_year:
        clauses.append("game_year = ?")
        params.append("" if game_year == UNSET_GAME_YEAR else game_year)
    if opponent:
        clauses.append("opponent = ?")
        params.append(opponent)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT DISTINCT project_name, project_path, game_year FROM library_clips"
            + where + " ORDER BY game_year DESC, project_name", params).fetchall()
        conn.close()
        return [(f"{r['game_year']} · {r['project_name']}" if r['game_year'] else r['project_name'], r["project_path"]) for r in rows]
    except sqlite3.DatabaseError:
        return []


def opponents() -> list[str]:
    """Every opponent present in the catalog."""
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT DISTINCT opponent FROM library_clips "
            "WHERE opponent != '' ORDER BY opponent").fetchall()
        conn.close()
        return [r["opponent"] for r in rows]
    except sqlite3.DatabaseError:
        return []


def game_years() -> list[str]:
    with contextlib.closing(_connect()) as conn:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT game_year FROM library_clips WHERE game_year != '' ORDER BY game_year DESC")]


def players() -> list[str]:
    """Every player named in the catalog, highlighted or otherwise."""
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT player_name, other_players, "
            "json_extract(details_json, '$.quarterback') AS quarterback "
            "FROM library_clips").fetchall()
        conn.close()
    except sqlite3.DatabaseError:
        return []
    from tapesift.services.detail_service import split_players
    variants: list[str] = []
    for row in rows:
        found = [row["player_name"] or "", row["quarterback"] or ""] + split_players(row["other_players"] or "")
        variants.extend(found)
    return sorted(
        tag_service.merge_player_variants(variants).values(),
        key=str.casefold,
    )


def reindex_project(session) -> None:
    """Index an open session's project (carries its opponent through)."""
    rows = build_index_payload(
        str(session.db_path), session.project.name,
        session.project.source_video_path, session.clips,
        opponent=session.project.opponent, game_year=session.project.game_year)
    write_project_index(str(session.db_path), rows)


def rank_results(rows: list[LibraryRow], text: str) -> list[LibraryRow]:
    """Order rows by how well they match the query.

    Exact title > exact tag > title prefix > word hits in title > tag hits >
    project/notes hits. Without a query, most recently updated first.
    """
    query = (text or "").strip().lower()
    if not query:
        return sorted(rows, key=lambda r: r.updated_at, reverse=True)
    terms = query.split()
    query_key = tag_service.tag_key(query)

    def score(row: LibraryRow) -> int:
        title = (row.clip_title or "").lower()
        keys = {tag_service.tag_key(t) for t in row.tags}
        points = 0
        if title == query:
            points = 120
        elif query_key and query_key in keys:
            points = 100
        elif title.startswith(query):
            points = 90
        points += sum(10 for term in terms if term in title)
        points += sum(6 for term in terms
                      if any(term in key for key in keys))
        if query in (row.project_name or "").lower():
            points += 4
        if query in (row.opponent or "").lower():
            points += 8
        if query in (row.notes or "").lower():
            points += 3
        return points

    return sorted(rows, key=lambda r: (-score(r), r.project_name,
                                       r.clip_number))


def all_tags() -> list[str]:
    """Distinct tags across the catalog, most-common first."""
    try:
        conn = _connect()
        rows = conn.execute(
            "SELECT tags_json FROM library_clips WHERE tags_json != '[]'").fetchall()
        conn.close()
    except sqlite3.DatabaseError:
        return []
    # Group equivalent variants so the chip list shows each tag once, using
    # the spelling the user typed most often.
    counts: dict[str, int] = {}
    variants: dict[str, list[str]] = {}
    for row in rows:
        for tag in json.loads(row["tags_json"] or "[]"):
            key = tag_service.tag_key(tag)
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            variants.setdefault(key, []).append(tag)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [tag_service.canonical_display(variants[key]) for key, _ in ordered]


def discover_project_files(project_folders: list[str],
                           recent_projects: list[str]) -> list[str]:
    """Every project file worth indexing, regardless of the recents list.

    The recents list is fragile (it was once wiped by a corrupted settings
    file), so rebuilds scan real locations instead: the default project
    folder, the folder of every recent project, and the folder of every
    project already in the catalog.
    """
    folders: set[Path] = set()
    for folder in project_folders:
        if folder and Path(folder).is_dir():
            folders.add(Path(folder))
    for recent in recent_projects:
        parent = Path(recent).parent
        if parent.is_dir():
            folders.add(parent)
    for _, project_path in projects():
        parent = Path(project_path).parent
        if parent.is_dir():
            folders.add(parent)

    found: set[str] = set(p for p in recent_projects if Path(p).is_file())
    for folder in folders:
        try:
            for extension in SUPPORTED_PROJECT_FILE_EXTENSIONS:
                for path in folder.rglob(f"*{extension}"):
                    found.add(str(path))
        except OSError:
            continue
    return sorted(found)


def reindex_project_file(db_path: str) -> int:
    """Open a project file read-only-ish and (re)index its clips. Returns count."""
    from tapesift.services.project_service import ProjectSession
    session = ProjectSession.open(Path(db_path))
    try:
        rows = build_index_payload(
            str(session.db_path), session.project.name,
            session.project.source_video_path, session.clips,
            opponent=session.project.opponent, game_year=session.project.game_year)
        write_project_index(str(session.db_path), rows)
        return len(rows)
    finally:
        session.conn.close()


def is_unreachable(path: str) -> bool:
    """True when a project's location can't be checked at all right now.

    An unplugged external drive, a disconnected network share, or a folder
    we lack permission to read are all "don't know", not "gone". The
    distinction matters because the catalog is the only record of which
    folders to scan: purging a project also forgets where it lived, so a
    wrongly-purged project cannot come back on its own even after the drive
    is plugged in again.
    """
    # A catalog copied from Windows can reference drives Linux cannot resolve.
    if os.name != "nt" and PureWindowsPath(path).drive:
        return True
    parent = Path(path).parent
    try:
        return not parent.is_dir()
    except OSError:
        return True


def rebuild_from_projects(db_paths: list[str]) -> tuple[int, int, int]:
    """Reindex a set of project files.

    Returns (clips indexed, projects indexed, projects left alone because
    their location was unreachable).
    """
    # Projects already in the catalog that this scan did not turn up. They
    # are the interesting case: either the file was deleted (stale rows to
    # clear) or its drive is absent (rows to keep). discover_project_files
    # only returns files that exist, so without this they would linger for
    # ever, exactly like the 68 rows left behind by a deleted project.
    scanned = {str(Path(p)) for p in db_paths}
    db_paths = list(db_paths) + [p for _, p in projects()
                                 if str(Path(p)) not in scanned]

    total_clips = 0
    indexed = 0
    skipped = 0
    for path in db_paths:
        if not Path(path).is_file():
            if is_unreachable(path):
                # Keep the rows. They are still searchable, and they are
                # what tells a later rebuild to look in this folder again.
                log.info("Location unreachable, keeping index for %s", path)
                skipped += 1
            else:
                remove_project(path)
            continue
        try:
            total_clips += reindex_project_file(path)
            indexed += 1
        except Exception:
            log.exception("Could not index project %s", path)
            skipped += 1
    return total_clips, indexed, skipped


def stats() -> tuple[int, int]:
    """(clip count, project count) in the catalog."""
    try:
        conn = _connect()
        clips = conn.execute("SELECT COUNT(*) FROM library_clips").fetchone()[0]
        projects = conn.execute(
            "SELECT COUNT(DISTINCT project_path) FROM library_clips").fetchone()[0]
        conn.close()
        return clips, projects
    except sqlite3.DatabaseError:
        return 0, 0
