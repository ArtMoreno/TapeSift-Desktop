"""The global Signature template library survives projects and restarts."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from threading import Barrier

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from tapesift.models.signature_template import (
    ImageAssetSnapshot,
    SignatureIdentity,
    SignatureTemplate,
)
from tapesift.services.signature_template_repository import (
    DuplicateSignatureTemplateNameError,
    SignatureTemplateRepository,
)
from tapesift.services.signature_template_store import (
    SIGNATURE_TEMPLATE_LIBRARY_DB_NAME,
    SIGNATURE_TEMPLATE_LIBRARY_SCHEMA_VERSION,
    _ASSET_SCHEMA_SQL,
    _STATE_SCHEMA_SQL,
    _TEMPLATE_SCHEMA_V2_SQL,
    SQLiteSignatureTemplateRepository,
    SignatureTemplateAlreadyExistsError,
    SignatureTemplateCorruptionError,
    SignatureTemplateRevisionConflictError,
    SignatureTemplateStorageError,
    canonical_signature_template_name,
)


def _image_bytes(
    image_format: str = "PNG",
    *,
    width: int = 5,
    height: int = 3,
    color: str = "#39E07A",
) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(color))
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, image_format)
    return bytes(buffer.data())


def _template(
    template_id: str,
    name: str,
    *,
    asset: ImageAssetSnapshot | None = None,
    display_text: str = "Nick Marshall",
) -> SignatureTemplate:
    return SignatureTemplate(
        template_id=template_id,
        name=name,
        identity=SignatureIdentity(
            display_text=display_text,
            username_text="@NMarshall_FB",
            wordmark_text="TapeSift",
            wordmark_logo=asset,
            profile_photo=asset,
            border=asset,
            accent_color="#39E07A",
            show_result=True,
        ),
    )


def test_library_persists_multiple_templates_selection_and_revisions(tmp_path):
    app_data = tmp_path / "TapeSift"
    beta = _template("beta", "Beta")
    alpha = _template("alpha", "Alpha")

    with SQLiteSignatureTemplateRepository(app_data) as repository:
        assert isinstance(repository, SignatureTemplateRepository)
        beta_record = repository.create(beta)
        alpha_record = repository.create(alpha, make_selected=True)

        assert beta_record.revision == 1
        assert beta_record.is_selected
        assert alpha_record.revision == 1
        assert alpha_record.is_selected
        assert repository.list_all() == (alpha, beta)
        assert [record.is_selected for record in repository.list_records()] == [
            True,
            False,
        ]
        assert repository.get_by_name("ALPHA") == alpha
        assert repository.get_default() == alpha
        assert repository.database_path == app_data / SIGNATURE_TEMPLATE_LIBRARY_DB_NAME

    with SQLiteSignatureTemplateRepository(app_data) as reopened:
        assert reopened.list_all() == (alpha, beta)
        assert reopened.get_selected() == alpha
        assert reopened.get_record("alpha").revision == 1


def test_captured_png_survives_source_file_deletion_and_restart(tmp_path):
    source = tmp_path / "identity-source.png"
    source.write_bytes(_image_bytes("PNG", width=17, height=9))
    captured = ImageAssetSnapshot.capture_file(source)
    template = _template("portable", "Portable", asset=captured)
    app_data = tmp_path / "AppData"

    with SQLiteSignatureTemplateRepository(app_data) as repository:
        repository.create(template)
    source.unlink()

    with SQLiteSignatureTemplateRepository(app_data) as reopened:
        restored = reopened.get("portable")

    assert restored == template
    assert restored.identity.profile_photo.data == captured.data
    assert restored.identity.profile_photo.sha256 == captured.sha256
    assert restored.identity.profile_photo.mime_type == "image/png"
    assert (
        restored.identity.profile_photo.width,
        restored.identity.profile_photo.height,
    ) == (17, 9)
    assert not source.exists()


@pytest.mark.parametrize(
    ("image_format", "expected_mime"),
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_library_round_trips_every_approved_embedded_raster_format(
    tmp_path,
    image_format,
    expected_mime,
):
    captured = ImageAssetSnapshot.capture(_image_bytes(image_format))
    template = _template(image_format.lower(), image_format, asset=captured)
    app_data = tmp_path / image_format

    with SQLiteSignatureTemplateRepository(app_data) as repository:
        repository.create(template)
    with SQLiteSignatureTemplateRepository(app_data) as reopened:
        restored = reopened.get(template.template_id)

    assert restored == template
    assert restored.identity.border.mime_type == expected_mime
    assert restored.identity.border.data == captured.data


def test_create_rejects_stable_id_and_canonical_name_collisions_atomically(tmp_path):
    app_data = tmp_path / "TapeSift"
    original = _template("one", "Studio")
    duplicate_id = _template("one", "Another")
    # NFKC converts these full-width letters to the same canonical key.
    duplicate_name = _template("two", "ＳＴＵＤＩＯ")

    with SQLiteSignatureTemplateRepository(app_data) as repository:
        repository.create(original)
        with pytest.raises(SignatureTemplateAlreadyExistsError):
            repository.create(duplicate_id)
        with pytest.raises(DuplicateSignatureTemplateNameError):
            repository.create(duplicate_name)

        assert repository.list_all() == (original,)
        assert repository.get_selected() == original
        assert canonical_signature_template_name("Studio") == (
            canonical_signature_template_name("ＳＴＵＤＩＯ")
        )


def test_update_uses_optimistic_revision_and_stale_write_is_atomic(tmp_path):
    original_asset = ImageAssetSnapshot.capture(_image_bytes(color="#FF0000"))
    replacement_asset = ImageAssetSnapshot.capture(_image_bytes(color="#0000FF"))
    original = _template("analyst", "Analyst", asset=original_asset)
    changed = _template(
        "analyst",
        "Lead Analyst",
        asset=replacement_asset,
        display_text="Taylor Reed",
    )
    stale = _template("analyst", "Stale Name", display_text="Stale")

    with SQLiteSignatureTemplateRepository(tmp_path / "TapeSift") as repository:
        created = repository.create(original)
        updated = repository.update(changed, expected_revision=created.revision)

        assert updated.revision == 2
        assert updated.is_selected
        with pytest.raises(SignatureTemplateRevisionConflictError) as error:
            repository.update(stale, expected_revision=1)

        assert error.value.template_id == "analyst"
        assert error.value.expected == 1
        assert error.value.actual == 2
        assert repository.get_record("analyst") == updated
        assert repository.get("analyst").identity.border.data == replacement_asset.data


def test_revision_conflict_is_detected_across_two_open_library_instances(tmp_path):
    app_data = tmp_path / "TapeSift"
    original = _template("shared", "Shared")
    first_change = replace(original, name="First Change")
    stale_change = replace(original, name="Stale Change")

    with SQLiteSignatureTemplateRepository(app_data) as first:
        first.create(original)
        with SQLiteSignatureTemplateRepository(app_data) as second:
            assert second.get_record("shared").revision == 1
            first.update(first_change, expected_revision=1)

            with pytest.raises(SignatureTemplateRevisionConflictError) as error:
                second.update(stale_change, expected_revision=1)

            assert error.value.actual == 2
            assert second.get("shared") == first_change


def test_duplicate_name_update_rolls_back_fields_assets_and_revision(tmp_path):
    original_asset = ImageAssetSnapshot.capture(_image_bytes(color="#FF0000"))
    replacement_asset = ImageAssetSnapshot.capture(_image_bytes(color="#0000FF"))
    alpha = _template("alpha", "Alpha", asset=original_asset)
    beta = _template("beta", "Beta")
    conflicting = _template("alpha", "BETA", asset=replacement_asset)

    with SQLiteSignatureTemplateRepository(tmp_path / "TapeSift") as repository:
        alpha_record = repository.create(alpha)
        repository.create(beta)

        with pytest.raises(DuplicateSignatureTemplateNameError):
            repository.update(
                conflicting,
                expected_revision=alpha_record.revision,
            )

        stored = repository.get_record("alpha")
        assert stored.revision == 1
        assert stored.template == alpha
        assert stored.template.identity.border.data == original_asset.data
        assert stored.is_selected


def test_selected_template_survives_update_and_delete_selects_replacement(tmp_path):
    alpha = _template("alpha", "Alpha")
    beta = _template("beta", "Beta")
    renamed_alpha = replace(alpha, name="Alpha Updated")

    with SQLiteSignatureTemplateRepository(tmp_path / "TapeSift") as repository:
        repository.create(alpha)
        repository.create(beta)
        updated = repository.update(renamed_alpha, expected_revision=1)

        assert updated.is_selected
        assert repository.get_selected() == renamed_alpha
        assert repository.delete("alpha", expected_revision=2)
        assert repository.get_selected() == beta
        assert repository.get_record("beta").is_selected
        assert repository.delete("beta", expected_revision=1)
        assert repository.get_selected() is None
        assert repository.list_all() == ()

    with SQLiteSignatureTemplateRepository(tmp_path / "TapeSift") as reopened:
        assert reopened.get_selected() is None


def test_selection_is_one_global_default_and_can_be_changed(tmp_path):
    alpha = _template("alpha", "Alpha")
    beta = _template("beta", "Beta")

    with SQLiteSignatureTemplateRepository(tmp_path / "TapeSift") as repository:
        repository.create(alpha)
        repository.create(beta)
        selected = repository.set_default("beta")

        assert selected.template == beta
        assert selected.is_selected
        assert repository.get_default() == beta
        assert sum(record.is_selected for record in repository.list_records()) == 1


def test_legacy_repository_upsert_api_remains_available(tmp_path):
    original = _template("compat", "Compatible")
    replacement = replace(original, name="Compatible Renamed")

    with SQLiteSignatureTemplateRepository(tmp_path / "TapeSift") as repository:
        assert repository.save(original) == original
        assert repository.save(replacement) == replacement

        record = repository.get_record("compat")
        assert record.template == replacement
        assert record.revision == 2
        assert record.is_selected
        assert repository.delete("missing") is False


def test_asset_digest_corruption_raises_controlled_error(tmp_path):
    app_data = tmp_path / "TapeSift"
    template = _template(
        "with-asset",
        "With Asset",
        asset=ImageAssetSnapshot.capture(_image_bytes()),
    )
    with SQLiteSignatureTemplateRepository(app_data) as repository:
        repository.create(template)
        database_path = repository.database_path

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE signature_assets SET sha256 = ? "
            "WHERE template_id = ? AND role = ?",
            ("0" * 64, "with-asset", "profile_photo"),
        )

    with SQLiteSignatureTemplateRepository(app_data) as reopened:
        with pytest.raises(SignatureTemplateCorruptionError, match="sha256"):
            reopened.get("with-asset")


def test_invalid_selection_state_fails_as_controlled_corruption_on_reopen(tmp_path):
    app_data = tmp_path / "TapeSift"
    with SQLiteSignatureTemplateRepository(app_data) as repository:
        repository.create(_template("one", "One"))
        database_path = repository.database_path

    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(
        "UPDATE signature_library_state SET selected_template_id = 'missing' "
        "WHERE singleton = 1"
    )
    connection.commit()
    connection.close()

    with pytest.raises(SignatureTemplateCorruptionError, match="relationships"):
        SQLiteSignatureTemplateRepository(app_data)


def test_v2_schema_with_matching_columns_but_no_constraints_is_rejected(tmp_path):
    app_data = tmp_path / "TapeSift"
    app_data.mkdir()
    database_path = app_data / SIGNATURE_TEMPLATE_LIBRARY_DB_NAME
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE signature_templates ("
            "template_id TEXT, canonical_name TEXT, name TEXT, display_text TEXT, "
            "username_text TEXT, wordmark_text TEXT, accent_color TEXT, "
            "show_result INTEGER, revision INTEGER)"
        )
        connection.execute(
            "CREATE TABLE signature_assets ("
            "template_id TEXT, role TEXT, data BLOB, sha256 TEXT, mime_type TEXT, "
            "width INTEGER, height INTEGER)"
        )
        connection.execute(
            "CREATE TABLE signature_library_state ("
            "singleton INTEGER, selected_template_id TEXT)"
        )
        connection.execute(
            "INSERT INTO signature_library_state "
            "(singleton, selected_template_id) VALUES (1, NULL)"
        )
        connection.execute(
            f"PRAGMA user_version = {SIGNATURE_TEMPLATE_LIBRARY_SCHEMA_VERSION}"
        )

    with pytest.raises(SignatureTemplateCorruptionError, match="definition"):
        SQLiteSignatureTemplateRepository(app_data)


@pytest.mark.parametrize(
    ("template_sql", "asset_sql"),
    [
        (
            _TEMPLATE_SCHEMA_V2_SQL.replace(
                "canonical_name TEXT UNIQUE",
                "canonical_name TEXTUNIQUE",
            ),
            _ASSET_SCHEMA_SQL,
        ),
        (
            _TEMPLATE_SCHEMA_V2_SQL,
            _ASSET_SCHEMA_SQL.replace("'wordmark_logo'", "'WORDMARK_LOGO'"),
        ),
    ],
)
def test_sql_fingerprint_preserves_token_and_literal_boundaries(
    tmp_path,
    template_sql,
    asset_sql,
):
    app_data = tmp_path / "TapeSift"
    app_data.mkdir()
    database_path = app_data / SIGNATURE_TEMPLATE_LIBRARY_DB_NAME
    with sqlite3.connect(database_path) as connection:
        connection.execute(template_sql)
        connection.execute(asset_sql)
        connection.execute(_STATE_SCHEMA_SQL)
        connection.execute(
            "INSERT INTO signature_library_state "
            "(singleton, selected_template_id) VALUES (1, NULL)"
        )
        connection.execute(
            f"PRAGMA user_version = {SIGNATURE_TEMPLATE_LIBRARY_SCHEMA_VERSION}"
        )

    with pytest.raises(SignatureTemplateCorruptionError, match="definition"):
        SQLiteSignatureTemplateRepository(app_data)


def test_selecting_corrupt_template_rolls_back_previous_selection(tmp_path):
    app_data = tmp_path / "TapeSift"
    good = _template("good", "Good")
    bad = _template(
        "bad",
        "Bad",
        asset=ImageAssetSnapshot.capture(_image_bytes()),
    )
    with SQLiteSignatureTemplateRepository(app_data) as repository:
        repository.create(good)
        repository.create(bad)
        database_path = repository.database_path

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE signature_assets SET sha256 = ? "
            "WHERE template_id = ? AND role = ?",
            ("0" * 64, "bad", "profile_photo"),
        )

    with SQLiteSignatureTemplateRepository(app_data) as reopened:
        with pytest.raises(SignatureTemplateCorruptionError, match="sha256"):
            reopened.set_selected("bad")
        assert reopened.get_selected() == good
        with sqlite3.connect(database_path) as connection:
            selected = connection.execute(
                "SELECT selected_template_id FROM signature_library_state "
                "WHERE singleton = 1"
            ).fetchone()[0]
        assert selected == "good"


def test_deleting_selected_template_does_not_promote_corrupt_replacement(tmp_path):
    app_data = tmp_path / "TapeSift"
    alpha = _template("alpha", "Alpha")
    beta = _template(
        "beta",
        "Beta",
        asset=ImageAssetSnapshot.capture(_image_bytes()),
    )
    with SQLiteSignatureTemplateRepository(app_data) as repository:
        repository.create(alpha)
        repository.create(beta)
        database_path = repository.database_path

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE signature_assets SET sha256 = ? "
            "WHERE template_id = ? AND role = ?",
            ("0" * 64, "beta", "profile_photo"),
        )

    with SQLiteSignatureTemplateRepository(app_data) as reopened:
        with pytest.raises(SignatureTemplateCorruptionError, match="sha256"):
            reopened.delete("alpha")
        assert reopened.get("alpha") == alpha
        assert reopened.get_selected() == alpha


def test_two_first_openers_serialize_schema_creation_and_migration(tmp_path):
    app_data = tmp_path / "TapeSift"
    first_write = Barrier(2)

    class CoordinatedRepository(SQLiteSignatureTemplateRepository):
        def __init__(self, path):
            self._wait_for_first_write = True
            super().__init__(path)

        @contextmanager
        def _write_transaction(self):
            if self._wait_for_first_write:
                self._wait_for_first_write = False
                first_write.wait(timeout=5)
            with super()._write_transaction() as connection:
                yield connection

    def open_and_close():
        with CoordinatedRepository(app_data):
            return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: open_and_close(), range(2)))

    assert results == [True, True]
    with SQLiteSignatureTemplateRepository(app_data) as reopened:
        assert reopened.list_all() == ()
        with sqlite3.connect(reopened.database_path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == (
                SIGNATURE_TEMPLATE_LIBRARY_SCHEMA_VERSION
            )


def test_v1_library_migrates_reopens_and_preserves_asset_bytes(tmp_path):
    app_data = tmp_path / "TapeSift"
    app_data.mkdir()
    database_path = app_data / SIGNATURE_TEMPLATE_LIBRARY_DB_NAME
    captured = ImageAssetSnapshot.capture(_image_bytes(width=11, height=7))
    template = _template("legacy", "Legacy", asset=captured)
    identity = template.identity

    with sqlite3.connect(database_path) as connection:
        SQLiteSignatureTemplateRepository._create_schema_v1(connection)
        connection.execute(
            "INSERT INTO signature_templates "
            "(template_id, canonical_name, name, display_text, username_text, "
            "wordmark_text, accent_color, show_result) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                template.template_id,
                canonical_signature_template_name(template.name),
                template.name,
                identity.display_text,
                identity.username_text,
                identity.wordmark_text,
                identity.accent_color,
                int(identity.show_result),
            ),
        )
        for role in ("wordmark_logo", "profile_photo", "border"):
            connection.execute(
                "INSERT INTO signature_assets "
                "(template_id, role, data, sha256, mime_type, width, height) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    template.template_id,
                    role,
                    captured.data,
                    captured.sha256,
                    captured.mime_type,
                    captured.width,
                    captured.height,
                ),
            )
        connection.execute("PRAGMA user_version = 1")

    with SQLiteSignatureTemplateRepository(app_data) as migrated:
        record = migrated.get_record("legacy")
        assert record.template == template
        assert record.revision == 1
        assert record.is_selected
        assert record.template.identity.border.data == captured.data
        changed = replace(template, name="Migrated")
        assert migrated.update(changed, expected_revision=1).revision == 2

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            SIGNATURE_TEMPLATE_LIBRARY_SCHEMA_VERSION
        )
    with SQLiteSignatureTemplateRepository(app_data) as reopened:
        assert reopened.get_record("legacy").revision == 2
        assert reopened.get_selected().name == "Migrated"


def test_schema_contains_identity_only_and_no_layout_or_export_toggles(tmp_path):
    with SQLiteSignatureTemplateRepository(tmp_path / "TapeSift") as repository:
        with sqlite3.connect(repository.database_path) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(signature_templates)"
                ).fetchall()
            }

    assert not columns.intersection(
        {
            "layout",
            "output_type",
            "font",
            "safe_area",
            "ink_palette",
            "waveform",
            "include_ink",
            "include_voiceover",
            "include_play_call",
            "include_slate",
        }
    )


def test_newer_library_version_and_closed_use_are_controlled_errors(tmp_path):
    app_data = tmp_path / "TapeSift"
    app_data.mkdir()
    database_path = app_data / SIGNATURE_TEMPLATE_LIBRARY_DB_NAME
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            f"PRAGMA user_version = {SIGNATURE_TEMPLATE_LIBRARY_SCHEMA_VERSION + 1}"
        )

    with pytest.raises(SignatureTemplateStorageError, match="newer TapeSift"):
        SQLiteSignatureTemplateRepository(app_data)

    clean = SQLiteSignatureTemplateRepository(tmp_path / "Clean")
    clean.close()
    with pytest.raises(SignatureTemplateStorageError, match="closed"):
        clean.list_all()
