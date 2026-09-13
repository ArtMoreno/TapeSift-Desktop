"""Transactional global storage for reusable Signature identity templates.

The storage path is supplied by the application and is expected to be TapeSift's
AppData directory. Raster assets are persisted as SQLite BLOBs and revalidated
through the immutable domain model on every read.
"""

from __future__ import annotations

import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from tapesift.models.signature_template import (
    ImageAssetSnapshot,
    SignatureIdentity,
    SignatureTemplate,
    SignatureTemplateValidationError,
)
from tapesift.services.signature_template_repository import (
    DuplicateSignatureTemplateNameError,
    SignatureTemplateRepositoryError,
)


SIGNATURE_TEMPLATE_LIBRARY_DB_NAME = "signature_templates.sqlite3"
SIGNATURE_TEMPLATE_LIBRARY_SCHEMA_VERSION = 2

_ASSET_ROLES = ("wordmark_logo", "profile_photo", "border")

_TEMPLATE_SCHEMA_V1_SQL = (
    "CREATE TABLE signature_templates ("
    "template_id TEXT PRIMARY KEY NOT NULL,"
    "canonical_name TEXT UNIQUE NOT NULL,"
    "name TEXT NOT NULL,"
    "display_text TEXT NOT NULL,"
    "username_text TEXT NOT NULL,"
    "wordmark_text TEXT NOT NULL,"
    "accent_color TEXT NOT NULL,"
    "show_result INTEGER NOT NULL CHECK (show_result IN (0, 1))"
    ")"
)
_TEMPLATE_SCHEMA_V2_SQL = (
    "CREATE TABLE signature_templates ("
    "template_id TEXT PRIMARY KEY NOT NULL,"
    "canonical_name TEXT UNIQUE NOT NULL,"
    "name TEXT NOT NULL,"
    "display_text TEXT NOT NULL,"
    "username_text TEXT NOT NULL,"
    "wordmark_text TEXT NOT NULL,"
    "accent_color TEXT NOT NULL,"
    "show_result INTEGER NOT NULL CHECK (show_result IN (0, 1)), "
    "revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1)"
    ")"
)
_ASSET_SCHEMA_SQL = (
    "CREATE TABLE signature_assets ("
    "template_id TEXT NOT NULL REFERENCES "
    "signature_templates(template_id) ON DELETE CASCADE,"
    "role TEXT NOT NULL CHECK "
    "(role IN ('wordmark_logo', 'profile_photo', 'border')),"
    "data BLOB NOT NULL,"
    "sha256 TEXT NOT NULL,"
    "mime_type TEXT NOT NULL,"
    "width INTEGER NOT NULL,"
    "height INTEGER NOT NULL,"
    "PRIMARY KEY (template_id, role)"
    ")"
)
_STATE_SCHEMA_SQL = (
    "CREATE TABLE signature_library_state ("
    "singleton INTEGER PRIMARY KEY CHECK (singleton = 1),"
    "selected_template_id TEXT NULL REFERENCES "
    "signature_templates(template_id) ON DELETE SET NULL"
    ")"
)
_EXPECTED_SCHEMA_SQL = {
    "signature_templates": _TEMPLATE_SCHEMA_V2_SQL,
    "signature_assets": _ASSET_SCHEMA_SQL,
    "signature_library_state": _STATE_SCHEMA_SQL,
}


class SignatureTemplateStorageError(SignatureTemplateRepositoryError):
    """The file-backed library could not complete a storage operation."""


class SignatureTemplateCorruptionError(SignatureTemplateStorageError):
    """Stored template data failed schema or domain validation."""


class SignatureTemplateAlreadyExistsError(SignatureTemplateRepositoryError):
    """A create operation reused an existing stable template id."""


class SignatureTemplateNotFoundError(SignatureTemplateRepositoryError):
    """An update or selection referenced an unknown stable template id."""


class SignatureTemplateRevisionConflictError(SignatureTemplateRepositoryError):
    """An update was based on an out-of-date template revision."""

    def __init__(self, template_id: str, expected: int, actual: int) -> None:
        self.template_id = template_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"template {template_id!r} is revision {actual}, not {expected}"
        )


@dataclass(frozen=True, slots=True)
class SignatureTemplateRecord:
    """One stored immutable template plus concurrency and selection metadata."""

    template: SignatureTemplate
    revision: int
    is_selected: bool

    def __post_init__(self) -> None:
        if not isinstance(self.template, SignatureTemplate):
            raise SignatureTemplateRepositoryError(
                "record template must be a SignatureTemplate"
            )
        if type(self.revision) is not int or self.revision < 1:
            raise SignatureTemplateRepositoryError(
                "record revision must be a positive integer"
            )
        if type(self.is_selected) is not bool:
            raise SignatureTemplateRepositoryError(
                "record selection state must be true or false"
            )

    @property
    def template_id(self) -> str:
        return self.template.template_id

    @property
    def name(self) -> str:
        return self.template.name


def canonical_signature_template_name(name: str) -> str:
    """Return the locale-independent key used for global name uniqueness."""

    if not isinstance(name, str):
        raise SignatureTemplateRepositoryError("template name must be text")
    return unicodedata.normalize("NFKC", name).casefold()


class SQLiteSignatureTemplateRepository:
    """Revisioned, process-persistent Signature template library.

    The path passed to the constructor is a directory, not a database filename.
    This keeps AppData ownership explicit and gives the application one stable
    place to back up or migrate the complete global template library.
    """

    def __init__(self, app_data_path: str | Path) -> None:
        if not isinstance(app_data_path, (str, Path)):
            raise SignatureTemplateStorageError("AppData path must be a path")
        self._app_data_path = Path(app_data_path)
        self._database_path = (
            self._app_data_path / SIGNATURE_TEMPLATE_LIBRARY_DB_NAME
        )
        self._connection: sqlite3.Connection | None = None
        try:
            self._app_data_path.mkdir(parents=True, exist_ok=True)
            if not self._app_data_path.is_dir():
                raise OSError("AppData path is not a directory")
            connection = sqlite3.connect(
                self._database_path,
                timeout=5.0,
                isolation_level=None,
            )
            self._connection = connection
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            self._migrate_schema()
            self._validate_library_state()
        except SignatureTemplateRepositoryError:
            self.close()
            raise
        except (OSError, sqlite3.Error) as exc:
            self.close()
            raise SignatureTemplateStorageError(
                f"Signature template library could not be opened: {exc}"
            ) from exc

    @property
    def app_data_path(self) -> Path:
        return self._app_data_path

    @property
    def database_path(self) -> Path:
        return self._database_path

    def __enter__(self) -> "SQLiteSignatureTemplateRepository":
        self._require_connection()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise SignatureTemplateStorageError(
                "Signature template library is closed"
            )
        return self._connection

    @contextmanager
    def _read_transaction(self) -> Iterator[sqlite3.Connection]:
        """Hold one SQLite snapshot across template, asset, and state reads."""

        connection = self._require_connection()
        try:
            connection.execute("BEGIN")
        except sqlite3.Error as exc:
            raise SignatureTemplateStorageError(
                f"Signature template read could not begin: {exc}"
            ) from exc
        try:
            yield connection
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            if connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error as rollback_exc:
                    raise SignatureTemplateStorageError(
                        "Signature template read could not roll back"
                    ) from rollback_exc
            raise SignatureTemplateStorageError(
                f"Signature template read could not complete: {exc}"
            ) from exc
        except Exception:
            if connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except sqlite3.Error as exc:
                    raise SignatureTemplateStorageError(
                        f"Signature template read could not roll back: {exc}"
                    ) from exc
            raise

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def _migrate_schema(self) -> None:
        try:
            # The version and table inventory must be read only after acquiring
            # the write lock.  Otherwise two first-open constructors can both
            # observe v0 and race to create the same tables.
            with self._write_transaction() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                existing_tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
                if type(version) is not int or version < 0:
                    raise SignatureTemplateCorruptionError(
                        "Signature template library has an invalid schema version"
                    )
                if version > SIGNATURE_TEMPLATE_LIBRARY_SCHEMA_VERSION:
                    raise SignatureTemplateStorageError(
                        "Signature template library was created by a newer TapeSift"
                    )
                if version == 0:
                    if existing_tables:
                        raise SignatureTemplateCorruptionError(
                            "Unversioned Signature template library contains tables"
                        )
                    self._create_schema_v1(connection)
                    connection.execute("PRAGMA user_version = 1")
                    version = 1
                if version == 1:
                    connection.execute(
                        "ALTER TABLE signature_templates "
                        "ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 "
                        "CHECK (revision >= 1)"
                    )
                    connection.execute(_STATE_SCHEMA_SQL)
                    selected = connection.execute(
                        "SELECT template_id FROM signature_templates "
                        "ORDER BY canonical_name, name, template_id LIMIT 1"
                    ).fetchone()
                    connection.execute(
                        "INSERT INTO signature_library_state "
                        "(singleton, selected_template_id) VALUES (1, ?)",
                        (selected[0] if selected is not None else None,),
                    )
                    connection.execute("PRAGMA user_version = 2")
        except SignatureTemplateRepositoryError:
            raise
        except sqlite3.Error as exc:
            raise SignatureTemplateCorruptionError(
                f"Signature template schema migration failed: {exc}"
            ) from exc

    @staticmethod
    def _create_schema_v1(connection: sqlite3.Connection) -> None:
        connection.execute(_TEMPLATE_SCHEMA_V1_SQL)
        connection.execute(_ASSET_SCHEMA_SQL)

    def _validate_library_state(self) -> None:
        with self._read_transaction() as connection:
            self._validate_library_state_in_snapshot(connection)

    def _validate_library_state_in_snapshot(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != SIGNATURE_TEMPLATE_LIBRARY_SCHEMA_VERSION:
                raise SignatureTemplateCorruptionError(
                    "Signature template library schema did not reach current version"
                )
            schema_rows = connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            schema_sql = {row["name"]: row["sql"] for row in schema_rows}
            if set(schema_sql) != set(_EXPECTED_SCHEMA_SQL):
                raise SignatureTemplateCorruptionError(
                    "Signature template library has unexpected tables"
                )
            for table, expected_sql in _EXPECTED_SCHEMA_SQL.items():
                if schema_sql[table] != expected_sql:
                    raise SignatureTemplateCorruptionError(
                        f"Signature template library table {table!r} "
                        "has an invalid definition"
                    )
            unexpected_executable_schema = connection.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE type IN ('trigger', 'view') LIMIT 1"
            ).fetchone()
            if unexpected_executable_schema is not None:
                raise SignatureTemplateCorruptionError(
                    "Signature template library contains an unexpected "
                    f"{unexpected_executable_schema['type']}"
                )
            required_columns = {
                "signature_templates": {
                    "template_id",
                    "canonical_name",
                    "name",
                    "display_text",
                    "username_text",
                    "wordmark_text",
                    "accent_color",
                    "show_result",
                    "revision",
                },
                "signature_assets": {
                    "template_id",
                    "role",
                    "data",
                    "sha256",
                    "mime_type",
                    "width",
                    "height",
                },
                "signature_library_state": {"singleton", "selected_template_id"},
            }
            for table, expected in required_columns.items():
                actual = {
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if actual != expected:
                    raise SignatureTemplateCorruptionError(
                        f"Signature template library table {table!r} is invalid"
                    )
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise SignatureTemplateCorruptionError(
                    "Signature template library has broken row relationships"
                )
            state_rows = connection.execute(
                "SELECT singleton, selected_template_id "
                "FROM signature_library_state"
            ).fetchall()
            if len(state_rows) != 1 or state_rows[0]["singleton"] != 1:
                raise SignatureTemplateCorruptionError(
                    "Signature template library selection state is invalid"
                )
            count = connection.execute(
                "SELECT COUNT(*) FROM signature_templates"
            ).fetchone()[0]
            selected = state_rows[0]["selected_template_id"]
            if count == 0 and selected is not None:
                raise SignatureTemplateCorruptionError(
                    "Empty Signature template library has a selected template"
                )
            if count > 0:
                if not isinstance(selected, str):
                    raise SignatureTemplateCorruptionError(
                        "Signature template library has no selected template"
                    )
                selected_count = connection.execute(
                    "SELECT COUNT(*) FROM signature_templates "
                    "WHERE template_id = ?",
                    (selected,),
                ).fetchone()[0]
                if selected_count != 1:
                    raise SignatureTemplateCorruptionError(
                        "Selected Signature template does not exist"
                    )
        except SignatureTemplateRepositoryError:
            raise
        except sqlite3.Error as exc:
            raise SignatureTemplateCorruptionError(
                f"Signature template library could not be validated: {exc}"
            ) from exc

    @staticmethod
    def _validate_template(template: SignatureTemplate) -> None:
        if not isinstance(template, SignatureTemplate):
            raise SignatureTemplateRepositoryError(
                "repository accepts only SignatureTemplate instances"
            )

    @staticmethod
    def _validate_revision(expected_revision: int) -> None:
        if type(expected_revision) is not int or expected_revision < 1:
            raise SignatureTemplateRepositoryError(
                "expected_revision must be a positive integer"
            )

    @staticmethod
    def _template_values(template: SignatureTemplate) -> tuple[Any, ...]:
        identity = template.identity
        return (
            template.template_id,
            canonical_signature_template_name(template.name),
            template.name,
            identity.display_text,
            identity.username_text,
            identity.wordmark_text,
            identity.accent_color,
            int(identity.show_result),
        )

    @staticmethod
    def _write_assets(
        connection: sqlite3.Connection,
        template: SignatureTemplate,
    ) -> None:
        for role in _ASSET_ROLES:
            asset = getattr(template.identity, role)
            if asset is None:
                continue
            connection.execute(
                "INSERT INTO signature_assets "
                "(template_id, role, data, sha256, mime_type, width, height) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    template.template_id,
                    role,
                    sqlite3.Binary(asset.data),
                    asset.sha256,
                    asset.mime_type,
                    asset.width,
                    asset.height,
                ),
            )

    @staticmethod
    def _raise_integrity_error(
        exc: sqlite3.IntegrityError,
        template: SignatureTemplate,
    ) -> None:
        message = str(exc).lower()
        if "signature_templates.canonical_name" in message:
            raise DuplicateSignatureTemplateNameError(
                f"a template named {template.name!r} already exists"
            ) from exc
        if "signature_templates.template_id" in message:
            raise SignatureTemplateAlreadyExistsError(
                f"a template with id {template.template_id!r} already exists"
            ) from exc
        raise SignatureTemplateStorageError(
            f"Signature template could not be stored: {exc}"
        ) from exc

    def create(
        self,
        template: SignatureTemplate,
        *,
        make_selected: bool = False,
    ) -> SignatureTemplateRecord:
        self._validate_template(template)
        if type(make_selected) is not bool:
            raise SignatureTemplateRepositoryError(
                "make_selected must be true or false"
            )
        try:
            with self._write_transaction() as connection:
                existing = connection.execute(
                    "SELECT 1 FROM signature_templates WHERE template_id = ?",
                    (template.template_id,),
                ).fetchone()
                if existing is not None:
                    raise SignatureTemplateAlreadyExistsError(
                        f"a template with id {template.template_id!r} already exists"
                    )
                try:
                    connection.execute(
                        "INSERT INTO signature_templates "
                        "(template_id, canonical_name, name, display_text, "
                        "username_text, wordmark_text, accent_color, show_result, "
                        "revision) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                        self._template_values(template),
                    )
                    self._write_assets(connection, template)
                except sqlite3.IntegrityError as exc:
                    self._raise_integrity_error(exc, template)
                state = connection.execute(
                    "SELECT selected_template_id FROM signature_library_state "
                    "WHERE singleton = 1"
                ).fetchone()
                if state is None:
                    raise SignatureTemplateCorruptionError(
                        "Signature template library selection state is missing"
                    )
                selected = state[0]
                if selected is None:
                    template_count = connection.execute(
                        "SELECT COUNT(*) FROM signature_templates"
                    ).fetchone()[0]
                    if template_count != 1:
                        raise SignatureTemplateCorruptionError(
                            "Signature template library lost its selected template"
                        )
                if make_selected or selected is None:
                    connection.execute(
                        "UPDATE signature_library_state "
                        "SET selected_template_id = ? WHERE singleton = 1",
                        (template.template_id,),
                    )
                    selected = template.template_id
            return SignatureTemplateRecord(
                template=template,
                revision=1,
                is_selected=selected == template.template_id,
            )
        except SignatureTemplateRepositoryError:
            raise
        except sqlite3.Error as exc:
            raise SignatureTemplateStorageError(
                f"Signature template could not be created: {exc}"
            ) from exc

    def update(
        self,
        template: SignatureTemplate,
        *,
        expected_revision: int,
        make_selected: bool = False,
    ) -> SignatureTemplateRecord:
        self._validate_template(template)
        self._validate_revision(expected_revision)
        if type(make_selected) is not bool:
            raise SignatureTemplateRepositoryError(
                "make_selected must be true or false"
            )
        try:
            with self._write_transaction() as connection:
                current = connection.execute(
                    "SELECT revision FROM signature_templates WHERE template_id = ?",
                    (template.template_id,),
                ).fetchone()
                if current is None:
                    raise SignatureTemplateNotFoundError(
                        f"template {template.template_id!r} does not exist"
                    )
                actual_revision = current[0]
                if type(actual_revision) is not int or actual_revision < 1:
                    raise SignatureTemplateCorruptionError(
                        f"template {template.template_id!r} has an invalid revision"
                    )
                if actual_revision != expected_revision:
                    raise SignatureTemplateRevisionConflictError(
                        template.template_id,
                        expected_revision,
                        actual_revision,
                    )
                values = self._template_values(template)
                try:
                    changed = connection.execute(
                        "UPDATE signature_templates SET "
                        "canonical_name = ?, name = ?, display_text = ?, "
                        "username_text = ?, wordmark_text = ?, accent_color = ?, "
                        "show_result = ?, revision = revision + 1 "
                        "WHERE template_id = ? AND revision = ?",
                        (
                            values[1],
                            values[2],
                            values[3],
                            values[4],
                            values[5],
                            values[6],
                            values[7],
                            template.template_id,
                            expected_revision,
                        ),
                    )
                    if changed.rowcount != 1:
                        refreshed = connection.execute(
                            "SELECT revision FROM signature_templates "
                            "WHERE template_id = ?",
                            (template.template_id,),
                        ).fetchone()
                        if refreshed is None:
                            raise SignatureTemplateNotFoundError(
                                f"template {template.template_id!r} does not exist"
                            )
                        raise SignatureTemplateRevisionConflictError(
                            template.template_id,
                            expected_revision,
                            refreshed[0],
                        )
                    connection.execute(
                        "DELETE FROM signature_assets WHERE template_id = ?",
                        (template.template_id,),
                    )
                    self._write_assets(connection, template)
                except sqlite3.IntegrityError as exc:
                    self._raise_integrity_error(exc, template)
                if make_selected:
                    connection.execute(
                        "UPDATE signature_library_state "
                        "SET selected_template_id = ? WHERE singleton = 1",
                        (template.template_id,),
                    )
                selected = connection.execute(
                    "SELECT selected_template_id FROM signature_library_state "
                    "WHERE singleton = 1"
                ).fetchone()
                if selected is None:
                    raise SignatureTemplateCorruptionError(
                        "Signature template library selection state is missing"
                    )
                next_revision = expected_revision + 1
            return SignatureTemplateRecord(
                template=template,
                revision=next_revision,
                is_selected=selected[0] == template.template_id,
            )
        except SignatureTemplateRepositoryError:
            raise
        except sqlite3.Error as exc:
            raise SignatureTemplateStorageError(
                f"Signature template could not be updated: {exc}"
            ) from exc

    def save(self, template: SignatureTemplate) -> SignatureTemplate:
        """Compatibility upsert; UI editors should use revisioned create/update."""

        self._validate_template(template)
        current = self.get_record(template.template_id)
        if current is None:
            return self.create(template).template
        return self.update(
            template,
            expected_revision=current.revision,
        ).template

    def _selected_template_id(self) -> str | None:
        try:
            row = self._require_connection().execute(
                "SELECT selected_template_id FROM signature_library_state "
                "WHERE singleton = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise SignatureTemplateCorruptionError(
                f"Signature template selection could not be read: {exc}"
            ) from exc
        if row is None:
            raise SignatureTemplateCorruptionError(
                "Signature template library selection state is missing"
            )
        selected = row[0]
        if selected is not None and not isinstance(selected, str):
            raise SignatureTemplateCorruptionError(
                "Signature template selection has an invalid id"
            )
        return selected

    @staticmethod
    def _asset_from_row(row: sqlite3.Row) -> ImageAssetSnapshot:
        data = row["data"]
        if not isinstance(data, bytes):
            raise SignatureTemplateCorruptionError(
                "Stored Signature image data is not bytes"
            )
        try:
            return ImageAssetSnapshot(
                data=data,
                sha256=row["sha256"],
                mime_type=row["mime_type"],
                width=row["width"],
                height=row["height"],
            )
        except (SignatureTemplateValidationError, TypeError) as exc:
            raise SignatureTemplateCorruptionError(
                f"Stored Signature image is invalid: {exc}"
            ) from exc

    def _record_from_row(
        self,
        row: sqlite3.Row,
        *,
        selected_template_id: str | None,
    ) -> SignatureTemplateRecord:
        values = dict(row)
        template_id = values.get("template_id")
        name = values.get("name")
        canonical_name = values.get("canonical_name")
        revision = values.get("revision")
        show_result = values.get("show_result")
        if not isinstance(name, str) or canonical_name != (
            canonical_signature_template_name(name)
        ):
            raise SignatureTemplateCorruptionError(
                f"Stored template {template_id!r} has an invalid canonical name"
            )
        if type(revision) is not int or revision < 1:
            raise SignatureTemplateCorruptionError(
                f"Stored template {template_id!r} has an invalid revision"
            )
        if type(show_result) is not int or show_result not in (0, 1):
            raise SignatureTemplateCorruptionError(
                f"Stored template {template_id!r} has an invalid result setting"
            )
        try:
            asset_rows = self._require_connection().execute(
                "SELECT role, data, sha256, mime_type, width, height "
                "FROM signature_assets WHERE template_id = ? ORDER BY role",
                (template_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise SignatureTemplateCorruptionError(
                f"Stored Signature assets could not be read: {exc}"
            ) from exc
        assets: dict[str, ImageAssetSnapshot] = {}
        for asset_row in asset_rows:
            role = asset_row["role"]
            if role not in _ASSET_ROLES or role in assets:
                raise SignatureTemplateCorruptionError(
                    f"Stored template {template_id!r} has an invalid asset role"
                )
            assets[role] = self._asset_from_row(asset_row)
        try:
            template = SignatureTemplate(
                template_id=template_id,
                name=name,
                identity=SignatureIdentity(
                    display_text=values.get("display_text"),
                    username_text=values.get("username_text"),
                    wordmark_text=values.get("wordmark_text"),
                    wordmark_logo=assets.get("wordmark_logo"),
                    profile_photo=assets.get("profile_photo"),
                    border=assets.get("border"),
                    accent_color=values.get("accent_color"),
                    show_result=bool(show_result),
                ),
            )
        except (SignatureTemplateValidationError, TypeError) as exc:
            raise SignatureTemplateCorruptionError(
                f"Stored template {template_id!r} is invalid: {exc}"
            ) from exc
        return SignatureTemplateRecord(
            template=template,
            revision=revision,
            is_selected=template_id == selected_template_id,
        )

    def _get_record_in_snapshot(
        self,
        template_id: str,
    ) -> SignatureTemplateRecord | None:
        try:
            row = self._require_connection().execute(
                "SELECT template_id, canonical_name, name, display_text, "
                "username_text, wordmark_text, accent_color, show_result, revision "
                "FROM signature_templates WHERE template_id = ?",
                (template_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise SignatureTemplateCorruptionError(
                f"Stored Signature template could not be read: {exc}"
            ) from exc
        if row is None:
            return None
        return self._record_from_row(
            row,
            selected_template_id=self._selected_template_id(),
        )

    def get_record(self, template_id: str) -> SignatureTemplateRecord | None:
        if not isinstance(template_id, str):
            return None
        with self._read_transaction():
            return self._get_record_in_snapshot(template_id)

    def get(self, template_id: str) -> SignatureTemplate | None:
        record = self.get_record(template_id)
        return record.template if record is not None else None

    def get_by_name(self, name: str) -> SignatureTemplate | None:
        if not isinstance(name, str):
            return None
        with self._read_transaction():
            try:
                row = self._require_connection().execute(
                    "SELECT template_id FROM signature_templates "
                    "WHERE canonical_name = ?",
                    (canonical_signature_template_name(name),),
                ).fetchone()
            except sqlite3.Error as exc:
                raise SignatureTemplateCorruptionError(
                    f"Stored Signature template name could not be read: {exc}"
                ) from exc
            record = (
                self._get_record_in_snapshot(row[0]) if row is not None else None
            )
            return record.template if record is not None else None

    def list_records(self) -> tuple[SignatureTemplateRecord, ...]:
        with self._read_transaction():
            try:
                rows = self._require_connection().execute(
                    "SELECT template_id, canonical_name, name, display_text, "
                    "username_text, wordmark_text, accent_color, show_result, revision "
                    "FROM signature_templates "
                    "ORDER BY canonical_name, name, template_id"
                ).fetchall()
            except sqlite3.Error as exc:
                raise SignatureTemplateCorruptionError(
                    f"Stored Signature templates could not be listed: {exc}"
                ) from exc
            selected = self._selected_template_id()
            records = tuple(
                self._record_from_row(row, selected_template_id=selected)
                for row in rows
            )
            if records and sum(record.is_selected for record in records) != 1:
                raise SignatureTemplateCorruptionError(
                    "Signature template library must have exactly one selected template"
                )
            return records

    def list_all(self) -> tuple[SignatureTemplate, ...]:
        return tuple(record.template for record in self.list_records())

    def get_selected_record(self) -> SignatureTemplateRecord | None:
        with self._read_transaction():
            selected = self._selected_template_id()
            if selected is None:
                try:
                    count = self._require_connection().execute(
                        "SELECT COUNT(*) FROM signature_templates"
                    ).fetchone()[0]
                except sqlite3.Error as exc:
                    raise SignatureTemplateCorruptionError(
                        f"Signature template selection could not be checked: {exc}"
                    ) from exc
                if count:
                    raise SignatureTemplateCorruptionError(
                        "Signature template library has no selected template"
                    )
                return None
            record = self._get_record_in_snapshot(selected)
            if record is None:
                raise SignatureTemplateCorruptionError(
                    "Selected Signature template does not exist"
                )
            return record

    def get_selected(self) -> SignatureTemplate | None:
        record = self.get_selected_record()
        return record.template if record is not None else None

    def set_selected(self, template_id: str) -> SignatureTemplateRecord:
        if not isinstance(template_id, str):
            raise SignatureTemplateNotFoundError("template id must be text")
        try:
            with self._write_transaction() as connection:
                target = self._get_record_in_snapshot(template_id)
                if target is None:
                    raise SignatureTemplateNotFoundError(
                        f"template {template_id!r} does not exist"
                    )
                changed = connection.execute(
                    "UPDATE signature_library_state SET selected_template_id = ? "
                    "WHERE singleton = 1",
                    (template_id,),
                )
                if changed.rowcount != 1:
                    raise SignatureTemplateCorruptionError(
                        "Signature template library selection state is missing"
                    )
                selected = SignatureTemplateRecord(
                    template=target.template,
                    revision=target.revision,
                    is_selected=True,
                )
        except SignatureTemplateRepositoryError:
            raise
        except sqlite3.Error as exc:
            raise SignatureTemplateStorageError(
                f"Signature template could not be selected: {exc}"
            ) from exc
        return selected

    # Default and selected are intentionally the same global state.  These
    # aliases keep terminology flexible without creating two competing truths.
    get_default = get_selected
    set_default = set_selected

    def delete(
        self,
        template_id: str,
        *,
        expected_revision: int | None = None,
    ) -> bool:
        if expected_revision is not None:
            self._validate_revision(expected_revision)
        if not isinstance(template_id, str):
            return False
        try:
            with self._write_transaction() as connection:
                row = connection.execute(
                    "SELECT revision FROM signature_templates WHERE template_id = ?",
                    (template_id,),
                ).fetchone()
                if row is None:
                    return False
                actual_revision = row[0]
                if type(actual_revision) is not int or actual_revision < 1:
                    raise SignatureTemplateCorruptionError(
                        f"template {template_id!r} has an invalid revision"
                    )
                if (
                    expected_revision is not None
                    and expected_revision != actual_revision
                ):
                    raise SignatureTemplateRevisionConflictError(
                        template_id,
                        expected_revision,
                        actual_revision,
                    )
                state = connection.execute(
                    "SELECT selected_template_id FROM signature_library_state "
                    "WHERE singleton = 1"
                ).fetchone()
                if state is None:
                    raise SignatureTemplateCorruptionError(
                        "Signature template library selection state is missing"
                    )
                was_selected = state[0] == template_id
                connection.execute(
                    "DELETE FROM signature_templates WHERE template_id = ?",
                    (template_id,),
                )
                if was_selected:
                    replacement = connection.execute(
                        "SELECT template_id FROM signature_templates "
                        "ORDER BY canonical_name, name, template_id LIMIT 1"
                    ).fetchone()
                    replacement_id = None
                    if replacement is not None:
                        replacement_record = self._get_record_in_snapshot(
                            replacement[0]
                        )
                        if replacement_record is None:
                            raise SignatureTemplateCorruptionError(
                                "Replacement Signature template disappeared"
                            )
                        replacement_id = replacement_record.template_id
                    connection.execute(
                        "UPDATE signature_library_state "
                        "SET selected_template_id = ? WHERE singleton = 1",
                        (replacement_id,),
                    )
            return True
        except SignatureTemplateRepositoryError:
            raise
        except sqlite3.Error as exc:
            raise SignatureTemplateStorageError(
                f"Signature template could not be deleted: {exc}"
            ) from exc
