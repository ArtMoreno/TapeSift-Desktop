"""Production seams for the standalone Signature identity manager."""

from __future__ import annotations

from dataclasses import replace
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QPoint  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from tapesift.models.export_package import ExportStyle  # noqa: E402
from tapesift.models.signature_template import (  # noqa: E402
    ImageAssetSnapshot,
    SignatureIdentity,
    SignatureTemplate,
    SignatureTemplateValidationError,
)
from tapesift.services.signature_template_repository import (  # noqa: E402
    DuplicateSignatureTemplateNameError,
)
from tapesift.services.signature_template_store import (  # noqa: E402
    SQLiteSignatureTemplateRepository,
    SignatureTemplateStorageError,
)
from tapesift.ui_v2.signature_template_manager import (  # noqa: E402
    NO_AUDIO_PREVIEW_MESSAGE,
    SignatureTemplateManagerDialog,
    UnsavedTemplateChangesError,
)


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _image_bytes(
    image_format: str = "PNG",
    *,
    width: int = 28,
    height: int = 18,
    color: str = "#39E07A",
) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(color))
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, image_format)
    return bytes(buffer.data())


def _write_image(path, image_format: str, color: str) -> bytes:
    data = _image_bytes(image_format, color=color)
    path.write_bytes(data)
    return data


def test_crud_revisions_and_authoring_selection_do_not_change_default(tmp_path):
    dialog = SignatureTemplateManagerDialog(tmp_path / "AppData")

    alpha = dialog.create_template("Alpha")
    assert not dialog.default_button.isEnabled()
    beta = dialog.create_template("Beta")

    assert alpha.revision == 1
    assert beta.revision == 1
    assert dialog.current_record.template_id == beta.template_id
    assert dialog.repository.get_default().template_id == alpha.template_id
    assert not dialog.current_record.is_selected
    assert dialog.default_button.isEnabled()

    chosen = dialog.set_default_template()
    assert chosen.template_id == beta.template_id
    assert dialog.repository.get_default().template_id == beta.template_id
    assert not dialog.default_button.isEnabled()

    renamed = dialog.rename_template("Lead Analyst")
    assert renamed.revision == 2
    assert renamed.is_selected
    dialog.display_text_edit.setText("Taylor Reed")
    saved = dialog.save_current_template()
    assert saved.revision == 3
    assert saved.template.identity.display_text == "Taylor Reed"

    duplicate = dialog.duplicate_template("Lead Analyst Copy")
    assert duplicate.revision == 1
    assert duplicate.template.identity == saved.template.identity
    assert dialog.repository.get_default().template_id == saved.template_id
    assert dialog.delete_template()
    assert dialog.repository.get(duplicate.template_id) is None
    assert dialog.repository.get_default().template_id == saved.template_id

    dialog.close()


def test_embedded_logo_and_photo_survive_source_deletion_and_restart(tmp_path):
    app_data = tmp_path / "AppData"
    logo_path = tmp_path / "wordmark.png"
    photo_path = tmp_path / "profile.jpg"
    logo_bytes = _write_image(logo_path, "PNG", "#246BCE")
    photo_bytes = _write_image(photo_path, "JPEG", "#D64B6A")

    dialog = SignatureTemplateManagerDialog(app_data)
    record = dialog.create_template("Portable Identity")
    logo = dialog.set_asset_from_path("wordmark_logo", logo_path)
    photo = dialog.set_asset_from_path("profile_photo", photo_path)
    saved = dialog.save_current_template()

    assert saved.revision == 2
    assert logo.mime_type == "image/png"
    assert photo.mime_type == "image/jpeg"
    assert not dialog.preview_image(ExportStyle.SIGNATURE).isNull()
    assert not dialog.preview_image(ExportStyle.VERTICAL).isNull()
    dialog.close()

    logo_path.unlink()
    photo_path.unlink()
    with SQLiteSignatureTemplateRepository(app_data) as reopened:
        restored = reopened.get(record.template_id)

    assert restored.identity.wordmark_logo.data == logo_bytes
    assert restored.identity.profile_photo.data == photo_bytes


def test_both_linked_previews_refresh_from_one_draft_without_fake_audio(tmp_path):
    dialog = SignatureTemplateManagerDialog(tmp_path / "AppData")
    dialog.create_template("Preview Identity")

    signature_before = dialog.preview_image(ExportStyle.SIGNATURE)
    vertical_before = dialog.preview_image(ExportStyle.VERTICAL)
    render_count = dialog.preview_render_count

    assert signature_before.size().toTuple() == (1920, 1080)
    assert vertical_before.size().toTuple() == (1080, 1920)
    seam = dialog.findChild(QLabel, "SignatureNoAudioPreviewSeam")
    assert seam.text() == NO_AUDIO_PREVIEW_MESSAGE
    assert "silent" in dialog.preview_status_label.text().casefold()

    dialog.display_text_edit.setText("Jordan Ellis")
    dialog.username_text_edit.setText("@jellis")
    dialog.accent_color_edit.setText("#E7B858")
    dialog.show_result_check.setChecked(False)
    dialog.refresh_previews()

    assert dialog.preview_render_count == render_count + 1
    assert dialog.preview_image(ExportStyle.SIGNATURE) != signature_before
    assert dialog.preview_image(ExportStyle.VERTICAL) != vertical_before
    assert dialog.is_dirty

    dialog.discard_current_changes()
    dialog.close()


def test_existing_border_is_preserved_but_has_no_editor_or_fallback_preview(tmp_path):
    dialog = SignatureTemplateManagerDialog(tmp_path / "AppData")
    border = ImageAssetSnapshot.capture(_image_bytes("PNG", width=40, height=24))
    legacy = SignatureTemplate(
        template_id="legacy-border",
        name="Legacy Border",
        identity=SignatureIdentity(
            display_text="Casey Morgan",
            username_text="@casey",
            wordmark_text="TapeSift",
            border=border,
        ),
    )
    dialog.repository.create(legacy)
    dialog.refresh_library(legacy.template_id)

    assert not dialog.border_notice.isHidden()
    assert "unavailable" in dialog.border_policy_note.text().casefold()
    assert dialog.border_policy_note.accessibleName() == "Border image editing status"
    assert dialog.preview_image(ExportStyle.SIGNATURE).isNull()
    assert dialog.preview_image(ExportStyle.VERTICAL).isNull()
    assert "border" in dialog.signature_preview.text().casefold()
    border_buttons = [
        button
        for button in dialog.findChildren(QPushButton)
        if "border" in button.objectName().casefold()
        or "border" in button.accessibleName().casefold()
    ]
    assert border_buttons == []

    dialog.display_text_edit.setText("Casey Morgan Updated")
    saved = dialog.save_current_template()
    assert saved.template.identity.border == border

    duplicate = dialog.duplicate_template("Legacy Border Copy")
    assert duplicate.template.identity.border == border
    assert dialog.repository.get(duplicate.template_id).identity.border.data == border.data

    dialog.close()


def test_duplicate_names_invalid_images_and_stale_revisions_fail_without_data_loss(
    tmp_path,
):
    app_data = tmp_path / "AppData"
    dialog = SignatureTemplateManagerDialog(app_data)
    alpha = dialog.create_template("Alpha")
    dialog.create_template("Beta")
    dialog.select_template(alpha.template_id)

    with pytest.raises(DuplicateSignatureTemplateNameError):
        dialog.rename_template("BETA")
    assert dialog.repository.get_record(alpha.template_id).revision == 1

    invalid = tmp_path / "not-an-image.png"
    invalid.write_bytes(b"not an image")
    with pytest.raises(SignatureTemplateValidationError):
        dialog.set_asset_from_path("profile_photo", invalid)
    assert dialog.repository.get(alpha.template_id).identity.profile_photo is None
    assert not dialog.is_dirty

    with SQLiteSignatureTemplateRepository(app_data) as concurrent:
        external = concurrent.get_record(alpha.template_id)
        changed_identity = replace(
            external.template.identity,
            display_text="External edit",
        )
        concurrent.update(
            replace(external.template, identity=changed_identity),
            expected_revision=external.revision,
        )

    dialog.display_text_edit.setText("Stale local edit")
    dialog.save_button.click()
    QApplication.processEvents()

    assert "revision" in dialog.status_label.text().casefold()
    assert dialog.is_dirty
    assert dialog.repository.get(alpha.template_id).identity.display_text == "External edit"

    dialog.discard_current_changes()
    assert dialog.display_text_edit.text() == "External edit"
    dialog.close()


def test_unsaved_draft_must_be_explicitly_discarded_before_selection(tmp_path):
    dialog = SignatureTemplateManagerDialog(tmp_path / "AppData")
    alpha = dialog.create_template("Alpha")
    beta = dialog.create_template("Beta")
    dialog.select_template(alpha.template_id)
    dialog.display_text_edit.setText("Unsaved")

    with pytest.raises(UnsavedTemplateChangesError):
        dialog.select_template(beta.template_id)

    selected = dialog.select_template(beta.template_id, discard_unsaved=True)
    assert selected.template_id == beta.template_id
    assert dialog.display_text_edit.text() == "Beta"
    assert dialog.repository.get(alpha.template_id).identity.display_text == "Alpha"
    dialog.close()


def test_controls_and_linked_previews_have_specific_accessible_names(tmp_path):
    dialog = SignatureTemplateManagerDialog(tmp_path / "AppData")
    dialog.create_template("Accessible")

    required = (
        dialog.library_list,
        dialog.new_button,
        dialog.duplicate_button,
        dialog.rename_button,
        dialog.delete_button,
        dialog.default_button,
        dialog.display_text_edit,
        dialog.username_text_edit,
        dialog.wordmark_text_edit,
        dialog.accent_color_edit,
        dialog.show_result_check,
        dialog.wordmark_asset_editor.choose_button,
        dialog.profile_asset_editor.choose_button,
        dialog.save_button,
        dialog.signature_preview,
        dialog.vertical_preview,
        dialog.status_label,
    )
    assert dialog.accessibleName() == "Signature template manager"
    assert all(widget.accessibleName().strip() for widget in required)
    assert dialog.signature_preview.accessibleName() != dialog.vertical_preview.accessibleName()
    assert dialog.library_list.currentItem().data(11).endswith("export default")

    dialog.close()


def test_asset_editors_use_wide_row_and_restack_at_dialog_minimum(
        qapp, tmp_path):
    dialog = SignatureTemplateManagerDialog(tmp_path / "AppData")
    dialog.create_template("Responsive Identity")

    dialog.resize(1400, 900)
    dialog.show()
    qapp.processEvents()
    assert dialog._asset_editors_layout.direction() \
        == QBoxLayout.Direction.LeftToRight
    assert dialog.wordmark_asset_editor.geometry().top() \
        == dialog.profile_asset_editor.geometry().top()
    assert dialog.wordmark_asset_editor.geometry().right() \
        < dialog.profile_asset_editor.geometry().left()
    preview_panel = dialog.findChild(QWidget, "SignatureLinkedPreviewsPanel")
    editor_scroll = dialog.findChild(QWidget, "SignatureTemplateEditorScroll")
    preview_top = preview_panel.mapTo(
        editor_scroll.viewport(), QPoint(0, 0)).y()
    assert preview_top < editor_scroll.viewport().height()

    dialog.resize(1060, 720)
    qapp.processEvents()
    assert dialog._asset_editors_layout.direction() \
        == QBoxLayout.Direction.TopToBottom
    assert dialog.wordmark_asset_editor.geometry().bottom() \
        < dialog.profile_asset_editor.geometry().top()
    dialog.close()


def test_escape_reject_path_closes_the_owned_sqlite_repository(tmp_path):
    dialog = SignatureTemplateManagerDialog(tmp_path / "AppData")
    dialog.create_template("Lifecycle")
    repository = dialog.repository

    dialog.reject()

    with pytest.raises(SignatureTemplateStorageError):
        repository.list_records()
