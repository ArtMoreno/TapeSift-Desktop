"""Immutable project snapshots in a user-selected, already-mounted shared folder.

Only committed local data is published. A shared folder is eventually consistent:
parents describe history, not a lock, and concurrent branches are never merged or
overwritten. Media and device-local path mapping remain the caller's responsibility.
"""
from __future__ import annotations

from collections import deque
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache, wraps
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import time
from uuid import UUID, uuid4

from tapesift.core.exceptions import TapeSiftError
from tapesift.database.migrations import MIGRATIONS


class SyncError(TapeSiftError):
    """The selected store or requested operation is unavailable."""


class IncompleteTransferError(SyncError):
    """Revision files or their ancestors have not completely arrived."""


class InvalidSnapshotError(SyncError):
    """A snapshot, checksum or manifest failed validation."""


def _io_errors(method):
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except OSError as exc:
            raise SyncError(f"Could not access project sync files: {exc}") from exc
    return call


@dataclass(frozen=True)
class SyncRevision:
    project_id: str
    revision_id: str
    parents: tuple[str, ...]
    sha256: str
    name: str
    created_at: str
    schema_version: int


@dataclass(frozen=True)
class SyncProject:
    project_id: str
    name: str
    heads: tuple[SyncRevision, ...]
    latest: SyncRevision | None
    problem: str = ""


@dataclass(frozen=True)
class SyncStatus:
    project_id: str
    heads: tuple[SyncRevision, ...]
    latest: SyncRevision | None
    has_updates: bool
    diverged: bool


def _uuid(value: str) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidSnapshotError("Invalid project or revision identifier.") from exc
    return value


def _uuid_name(value: str) -> bool:
    try:
        _uuid(value)
        return True
    except SyncError:
        return False


def _schema(conn: sqlite3.Connection) -> dict:
    objects = conn.execute("SELECT type, name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'").fetchall()
    if any(kind not in ("table", "index") for kind, _ in objects):
        raise InvalidSnapshotError("The snapshot contains an unexpected database schema.")
    return {
        name: (tuple(conn.execute("SELECT * FROM pragma_table_info(?)", (name,))),
               tuple(conn.execute("SELECT * FROM pragma_foreign_key_list(?)", (name,))))
        for kind, name in objects if kind == "table"
    }


@lru_cache(maxsize=len(MIGRATIONS))
def _expected_schema(version: int) -> dict:
    with closing(sqlite3.connect(":memory:")) as conn:
        for script in MIGRATIONS[:version]:
            conn.executescript(script)
        return _schema(conn)


def _validate_database(path: Path) -> tuple[str, int]:
    try:
        # Immutable snapshots must never acquire journal or lock files in the store.
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
            conn.execute("PRAGMA trusted_schema = OFF")
            conn.execute("PRAGMA query_only = ON")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if not 1 <= version <= len(MIGRATIONS):
                raise InvalidSnapshotError("Unsupported TapeSift project schema; update the app if this is a newer project.")
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise InvalidSnapshotError("The project snapshot failed SQLite integrity checks.")
            if _schema(conn) != _expected_schema(version):
                raise InvalidSnapshotError("The snapshot does not match its TapeSift project schema.")
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise InvalidSnapshotError("The snapshot contains broken project references.")
            rows = conn.execute("SELECT name FROM projects").fetchall()
            if len(rows) != 1 or not isinstance(rows[0][0], str) or not rows[0][0].strip():
                raise InvalidSnapshotError("A snapshot must contain exactly one named TapeSift project.")
            return rows[0][0], version
    except sqlite3.Error as exc:
        raise InvalidSnapshotError(f"Could not validate the project snapshot: {exc}") from exc


def _snapshot(local_db: Path, destination: Path) -> None:
    deadline = time.monotonic() + 30

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise SyncError("The local project is busy; finish saving and try again.")

    try:
        with closing(sqlite3.connect(local_db.as_uri() + "?mode=ro", uri=True, timeout=1)) as source:
            with closing(sqlite3.connect(destination)) as target:
                source.backup(target, pages=128, progress=progress, sleep=.05)
                target.execute("PRAGMA journal_mode = DELETE")
    except sqlite3.Error as exc:
        raise InvalidSnapshotError(f"Could not snapshot the local project: {exc}") from exc


def _copy_exclusive(source: Path, destination: Path) -> None:
    created = False
    try:
        with destination.open("xb") as target:
            created = True
            with source.open("rb") as stream:
                shutil.copyfileobj(stream, target)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def save_project_copy(local_db: Path | str, folder: Path | str) -> Path:
    """Write a standalone copy of committed data without replacing another file."""
    source, destination = Path(local_db).resolve(), Path(folder).resolve()
    if not source.is_file() or not destination.is_dir():
        raise SyncError("The project or destination folder is unavailable.")
    with tempfile.TemporaryDirectory(prefix=".tapesift-copy-", dir=destination) as temporary:
        snapshot = Path(temporary) / "copy.tapesift"
        _snapshot(source, snapshot)
        _validate_database(snapshot)
        number = 1
        while True:
            suffix = "" if number == 1 else f" ({number})"
            target = destination / f"{source.stem}{suffix}.tapesift"
            number += 1
            if any(Path(str(target) + extension).exists() for extension in ("-wal", "-shm", "-journal")):
                continue
            try:
                _copy_exclusive(snapshot, target)
            except FileExistsError:
                continue
            return target


class SyncStore:
    @_io_errors
    def __init__(self, shared_folder: Path | str):
        self.root = Path(shared_folder).expanduser().resolve()
        self._available()

    def _available(self) -> None:
        if not self.root.is_dir():
            raise SyncError("The shared folder is unavailable. Mount or reconnect it, then try again.")

    def _project_dir(self, project_id: str, *, create: bool = False) -> Path:
        self._available()
        path = self.root / _uuid(project_id)
        if path.is_symlink() or path.resolve().parent != self.root:
            raise InvalidSnapshotError("Shared project folders must not redirect outside the store.")
        if create:
            path.mkdir(exist_ok=True)
        if path.exists() and not path.is_dir():
            raise InvalidSnapshotError("The shared project location is not a folder.")
        return path

    def _local(self, path: Path | str) -> Path:
        self._available()
        result = Path(path).expanduser().resolve()
        if result.is_relative_to(self.root):
            raise SyncError("Keep editable projects outside the shared folder; use a local project copy.")
        return result

    def _read_revision(self, project_id: str, revision_id: str) -> tuple[SyncRevision, Path, int]:
        folder = self._project_dir(project_id)
        _uuid(revision_id)
        manifest, snapshot = folder / f"{revision_id}.json", folder / f"{revision_id}.tapesift"
        for path in (manifest, snapshot):
            if path.is_symlink() or path.resolve().parent != folder:
                raise InvalidSnapshotError("Revision files must stay within their shared project folder.")
            if not path.is_file():
                raise IncompleteTransferError("A project revision is still downloading or is missing a file.")
        try:
            if manifest.stat().st_size > 131072:
                raise InvalidSnapshotError("The project revision manifest is invalid.")
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise IncompleteTransferError("A revision manifest is incomplete or damaged; wait for the drive to finish syncing.") from exc
        try:
            if type(data["format_version"]) is not int or data["format_version"] != 1:
                raise ValueError("unsupported manifest version")
            if data["project_id"] != project_id or data["revision_id"] != revision_id:
                raise ValueError("revision identity mismatch")
            parents = data["parents"]
            if not isinstance(parents, list) or len(parents) > 32:
                raise ValueError("invalid parents")
            parents = tuple(_uuid(parent) for parent in parents)
            if len(set(parents)) != len(parents) or revision_id in parents:
                raise ValueError("invalid ancestry")
            digest = data["sha256"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("invalid checksum")
            size, version, name = data["size_bytes"], data["schema_version"], data["name"]
            if type(size) is not int or size <= 0 or type(version) is not int or not 1 <= version <= len(MIGRATIONS):
                raise ValueError("invalid snapshot size or unsupported project schema")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("missing project name")
            if datetime.fromisoformat(data["created_at"]).tzinfo is None:
                raise ValueError("missing date timezone")
            revision = SyncRevision(project_id, revision_id, parents, digest, name, data["created_at"], version)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise InvalidSnapshotError(f"Invalid project revision manifest: {exc}") from exc
        if snapshot.stat().st_size != size:
            raise IncompleteTransferError("A project snapshot has not completely transferred or is damaged.")
        return revision, snapshot, size

    @_io_errors
    def revisions(self, project_id: str, *, complete_only: bool = False) -> list[SyncRevision]:
        """Read metadata/availability only; checkout verifies the chosen content."""
        folder = self._project_dir(project_id)
        if not folder.exists():
            return []
        manifests = {path.stem for path in folder.glob("*.json") if _uuid_name(path.stem)}
        snapshots = {path.stem for path in folder.glob("*.tapesift") if _uuid_name(path.stem)}
        if manifests != snapshots and not complete_only:
            raise IncompleteTransferError("Project revisions are still transferring; wait for the shared drive to finish syncing.")
        revisions = []
        for revision in manifests & snapshots:
            try:
                revisions.append(self._read_revision(project_id, revision)[0])
            except SyncError:
                if not complete_only:
                    raise
        known = {revision.revision_id for revision in revisions}
        children = {revision: [] for revision in known}
        pending = {}
        for revision in revisions:
            if not set(revision.parents).issubset(known) and not complete_only:
                raise IncompleteTransferError("An earlier project revision has not arrived yet.")
            pending[revision.revision_id] = len(revision.parents)
            for parent in revision.parents:
                if parent in children:
                    children[parent].append(revision.revision_id)
        ready = deque(key for key, count in pending.items() if count == 0)
        visited = set()
        while ready:
            key = ready.popleft()
            visited.add(key)
            for child in children[key]:
                pending[child] -= 1
                if pending[child] == 0:
                    ready.append(child)
        if len(visited) != len(known) and not complete_only:
            raise InvalidSnapshotError("Project revision history contains a cycle.")
        return sorted((revision for revision in revisions if revision.revision_id in visited),
                      key=lambda revision: (revision.created_at, revision.revision_id))

    @_io_errors
    def status(self, project_id: str, base_revision: str | None = None) -> SyncStatus:
        revisions = self.revisions(project_id)
        if base_revision is not None:
            _uuid(base_revision)
            if not any(revision.revision_id == base_revision for revision in revisions):
                raise IncompleteTransferError("This computer's saved revision has not arrived in the shared folder.")
        parents = {parent for revision in revisions for parent in revision.parents}
        heads = tuple(revision for revision in revisions if revision.revision_id not in parents)
        latest = heads[0] if len(heads) == 1 else None
        return SyncStatus(project_id, heads, latest,
                          bool(heads and (latest is None or latest.revision_id != base_revision)), len(heads) > 1)

    @_io_errors
    def list_projects(self) -> list[SyncProject]:
        self._available()
        projects = []
        for folder in sorted(self.root.iterdir()):
            if not _uuid_name(folder.name):
                continue
            try:
                status = self.status(folder.name)
            except SyncError as exc:
                projects.append(SyncProject(folder.name, folder.name, (), None, str(exc)))
                continue
            if status.heads:
                name = (status.latest or status.heads[-1]).name
                projects.append(SyncProject(folder.name, name, status.heads, status.latest))
        return projects

    @_io_errors
    def publish(self, local_db: Path | str, *, project_id: str | None = None,
                parent_revision: str | None = None) -> SyncRevision:
        """Back up committed data; a stale parent creates a preserved branch."""
        source = self._local(local_db)
        if not source.is_file():
            raise SyncError("The local project file is unavailable.")
        project_id = _uuid(project_id) if project_id is not None else str(uuid4())
        status = self.status(project_id, parent_revision)
        # ponytail: full snapshots retain easy recovery; add retention if history grows too large.
        with tempfile.TemporaryDirectory(prefix=".tapesift-sync-", dir=source.parent) as temporary:
            snapshot = Path(temporary) / "snapshot.tapesift"
            _snapshot(source, snapshot)
            name, schema = _validate_database(snapshot)
            with snapshot.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if status.latest and status.latest.revision_id == parent_revision and status.latest.sha256 == digest:
                self.validate_revision(project_id, parent_revision)
                return status.latest
            revision = SyncRevision(project_id, str(uuid4()),
                (parent_revision,) if parent_revision else (), digest, name,
                datetime.now(timezone.utc).isoformat(), schema)
            manifest = Path(temporary) / "manifest.json"
            manifest.write_text(json.dumps(dict(asdict(revision), format_version=1,
                                               size_bytes=snapshot.stat().st_size), ensure_ascii=False), encoding="utf-8")
            folder = self._project_dir(project_id, create=True)
            target = folder / f"{revision.revision_id}.tapesift"
            _copy_exclusive(snapshot, target)
            try:
                # No mutable latest pointer: independent writers retain both heads.
                _copy_exclusive(manifest, folder / f"{revision.revision_id}.json")
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            return revision

    def _verified_copy(self, project_id: str, revision_id: str, target: Path) -> SyncRevision:
        revision, source, expected_size = self._read_revision(project_id, revision_id)
        digest = hashlib.sha256()
        with source.open("rb") as stream, target.open("wb") as destination:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                destination.write(chunk)
        if target.stat().st_size != expected_size or digest.hexdigest() != revision.sha256:
            raise InvalidSnapshotError("Project snapshot checksum mismatch. The transfer is incomplete or the snapshot is damaged.")
        if _validate_database(target) != (revision.name, revision.schema_version):
            raise InvalidSnapshotError("The project snapshot does not match its revision manifest.")
        return revision

    @_io_errors
    def validate_revision(self, project_id: str, revision_id: str) -> SyncRevision:
        """Explicitly verify content, without opening an editable shared database."""
        with tempfile.TemporaryDirectory(prefix="tapesift-sync-") as temporary:
            return self._verified_copy(project_id, revision_id, Path(temporary) / "snapshot.tapesift")

    @_io_errors
    def checkout(self, project_id: str, revision_id: str, local_projects_folder: Path | str) -> Path:
        """Verify and create a new local file; remote names never form paths."""
        folder = self._local(local_projects_folder)
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".tapesift-sync-", dir=folder) as temporary:
            snapshot = Path(temporary) / "snapshot.tapesift"
            self._verified_copy(project_id, revision_id, snapshot)
            target = folder / f"{_uuid(project_id)}-{uuid4()}.tapesift"
            _copy_exclusive(snapshot, target)
            return target
