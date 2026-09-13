"""Signature templates are durable identity, not mutable file references."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from tapesift.models.signature_template import (
    LINKED_SIGNATURE_OUTPUTS,
    SIGNATURE_TEMPLATE_SCHEMA,
    SIGNATURE_TEMPLATE_VERSION,
    ImageAssetSnapshot,
    SignatureIdentity,
    SignatureOutput,
    SignatureTemplate,
    SignatureTemplateValidationError,
    signature_template_from_dict,
    signature_template_from_json,
    signature_template_to_dict,
    signature_template_to_json,
)
from tapesift.services.signature_template_repository import (
    DuplicateSignatureTemplateNameError,
    InMemorySignatureTemplateRepository,
    SignatureTemplateRepository,
    SignatureTemplateRepositoryError,
)


def _png_bytes(
    width: int = 3,
    height: int = 2,
    color: str = "#39E07A",
) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGBA8888)
    image.fill(QColor(color))
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


def _asset(color: str = "#39E07A") -> ImageAssetSnapshot:
    return ImageAssetSnapshot.capture(_png_bytes(color=color))


def _template(
    template_id: str = "sideline",
    name: str = "Sideline Analyst",
    *,
    with_assets: bool = True,
) -> SignatureTemplate:
    asset = _asset() if with_assets else None
    return SignatureTemplate(
        template_id=template_id,
        name=name,
        identity=SignatureIdentity(
            display_text="Nick Marshall",
            username_text="@NMarshall_FB",
            wordmark_text="TapeSift",
            wordmark_logo=asset,
            profile_photo=asset,
            border=asset,
            accent_color="#39e07a",
            show_result=True,
        ),
    )


def test_image_capture_copies_bytes_and_derives_trusted_metadata():
    source = bytearray(_png_bytes(width=5, height=7))
    captured = ImageAssetSnapshot.capture(source)
    original_data = captured.data

    source[:] = b"changed after selection"

    assert captured.data == original_data
    assert captured.mime_type == "image/png"
    assert (captured.width, captured.height) == (5, 7)
    assert len(captured.sha256) == 64


def test_file_capture_survives_original_being_replaced(tmp_path):
    source = tmp_path / "analyst-photo.png"
    source.write_bytes(_png_bytes(color="#FF0000"))
    captured = ImageAssetSnapshot.capture_file(source)

    source.write_bytes(_png_bytes(color="#0000FF"))

    assert captured.data != source.read_bytes()
    assert not hasattr(captured, "path")
    assert str(source) not in signature_template_to_json(
        SignatureTemplate(
            "portable",
            "Portable",
            SignatureIdentity("Analyst", profile_photo=captured),
        )
    )


def test_file_extension_cannot_override_the_captured_image_content(tmp_path):
    misleading = tmp_path / "looks-like-a-jpeg.jpg"
    misleading.write_bytes(_png_bytes())
    assert ImageAssetSnapshot.capture_file(misleading).mime_type == "image/png"


@pytest.mark.parametrize(
    ("image_format", "mime_type"),
    [("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_capture_accepts_supported_photo_formats(image_format, mime_type):
    image = QImage(4, 6, QImage.Format.Format_RGB888)
    image.fill(QColor("#336699"))
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, image_format)

    captured = ImageAssetSnapshot.capture(bytes(buffer.data()))

    assert captured.mime_type == mime_type
    assert (captured.width, captured.height) == (4, 6)


@pytest.mark.parametrize(
    "payload",
    [b"", b"not an image", b'<svg xmlns="http://www.w3.org/2000/svg"/>'],
)
def test_image_capture_rejects_empty_corrupt_and_unsupported_payloads(payload):
    with pytest.raises(SignatureTemplateValidationError):
        ImageAssetSnapshot.capture(payload)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"sha256": "0" * 64}, "sha256 does not match"),
        ({"mime_type": "image/jpeg"}, "MIME type does not match"),
        ({"width": 99}, "dimensions do not match"),
        ({"height": True}, "dimensions must be integers"),
    ],
)
def test_asset_constructor_revalidates_serialized_claims(change, message):
    asset = _asset()
    values = {
        "data": asset.data,
        "sha256": asset.sha256,
        "mime_type": asset.mime_type,
        "width": asset.width,
        "height": asset.height,
    }
    values.update(change)
    with pytest.raises(SignatureTemplateValidationError, match=message):
        ImageAssetSnapshot(**values)


def test_models_are_frozen_all_the_way_to_asset_bytes():
    template = _template()
    with pytest.raises(FrozenInstanceError):
        template.name = "Changed"
    with pytest.raises(FrozenInstanceError):
        template.identity.show_result = False
    with pytest.raises(FrozenInstanceError):
        template.identity.profile_photo.width = 42
    assert isinstance(template.identity.profile_photo.data, bytes)


def test_one_identity_drives_only_signature_and_vertical_outputs():
    template = _template(with_assets=False)
    signature = template.bind(SignatureOutput.SIGNATURE_16_9)
    vertical = template.bind("vertical_9_16")

    assert template.outputs == LINKED_SIGNATURE_OUTPUTS
    assert set(template.outputs) == {
        SignatureOutput.SIGNATURE_16_9,
        SignatureOutput.VERTICAL_9_16,
    }
    assert signature.identity is template.identity
    assert vertical.identity is template.identity
    assert signature.identity is vertical.identity
    assert all("clean" not in output.value for output in template.outputs)


def test_unknown_output_cannot_manufacture_a_third_template_variant():
    with pytest.raises(SignatureTemplateValidationError, match="Signature 16:9"):
        _template(with_assets=False).bind("clean")


def test_identity_preserves_approved_scope_without_layout_controls():
    fields = set(SignatureIdentity.__dataclass_fields__)
    assert fields == {
        "display_text",
        "username_text",
        "wordmark_text",
        "wordmark_logo",
        "profile_photo",
        "border",
        "accent_color",
        "show_result",
    }
    assert not fields.intersection(
        {
            "font",
            "font_size",
            "layout",
            "ink_palette",
            "safe_area",
            "waveform",
            "title",
            "callout",
        }
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"display_text": ""},
        {"display_text": " Analyst"},
        {"display_text": "Analyst\nTwo"},
        {"display_text": "Analyst", "username_text": "handle\r"},
        {"display_text": "Analyst", "wordmark_text": "T" * 81},
        {"display_text": "Analyst", "accent_color": "green"},
        {"display_text": "Analyst", "accent_color": "#12345678"},
        {"display_text": "Analyst", "show_result": 1},
        {"display_text": "Analyst", "profile_photo": b"not a snapshot"},
    ],
)
def test_identity_rejects_ambiguous_or_malformed_values(kwargs):
    with pytest.raises(SignatureTemplateValidationError):
        SignatureIdentity(**kwargs)


def test_accent_color_is_canonicalized_for_deterministic_storage():
    identity = SignatureIdentity("Analyst", accent_color="#a0b1c2")
    assert identity.accent_color == "#A0B1C2"


@pytest.mark.parametrize(
    ("template_id", "name"),
    [
        ("", "Name"),
        ("has spaces", "Name"),
        ("x" * 65, "Name"),
        ("valid", ""),
        ("valid", " Name"),
        ("valid", "N" * 101),
    ],
)
def test_template_rejects_unstable_ids_and_ambiguous_names(template_id, name):
    with pytest.raises(SignatureTemplateValidationError):
        SignatureTemplate(template_id, name, SignatureIdentity("Analyst"))


def test_template_json_is_canonical_versioned_and_round_trips_assets():
    template = _template()

    first = template.to_json()
    second = signature_template_to_json(template)
    restored = SignatureTemplate.from_json(first)

    assert first == second
    assert "\n" not in first
    assert ": " not in first
    assert restored == template
    decoded = json.loads(first)
    assert decoded["schema"] == SIGNATURE_TEMPLATE_SCHEMA
    assert decoded["version"] == SIGNATURE_TEMPLATE_VERSION
    assert decoded["template"]["identity"]["profile_photo"]["data_base64"]


def test_unicode_identity_is_deterministic_and_not_ascii_escaped():
    template = SignatureTemplate(
        "unicode",
        "Coaching — Español",
        SignatureIdentity("José Muñoz", username_text="@José"),
    )
    serialized = template.to_json()
    assert "José Muñoz" in serialized
    assert "\\u00e9" not in serialized
    assert signature_template_from_json(serialized) == template


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(version=999),
        lambda data: data.update(schema="some.other.schema"),
        lambda data: data.update(extra="silent drift"),
        lambda data: data["template"]["identity"].update(font="invented"),
    ],
)
def test_deserializer_rejects_unknown_versions_schemas_and_fields(mutation):
    data = signature_template_to_dict(_template(with_assets=False))
    mutation(data)
    with pytest.raises(SignatureTemplateValidationError):
        signature_template_from_dict(data)


def test_deserializer_rejects_tampered_embedded_asset():
    data = signature_template_to_dict(_template())
    photo = data["template"]["identity"]["profile_photo"]
    photo["sha256"] = "0" * 64
    with pytest.raises(SignatureTemplateValidationError, match="sha256 does not match"):
        signature_template_from_dict(data)


def test_deserializer_rejects_invalid_asset_base64():
    data = signature_template_to_dict(_template())
    data["template"]["identity"]["border"]["data_base64"] = "not base64!"
    with pytest.raises(SignatureTemplateValidationError, match="base64 is invalid"):
        signature_template_from_dict(data)


def test_deserializer_rejects_missing_identity_fields():
    data = signature_template_to_dict(_template(with_assets=False))
    del data["template"]["identity"]["show_result"]
    with pytest.raises(SignatureTemplateValidationError, match="missing show_result"):
        signature_template_from_dict(data)


@pytest.mark.parametrize("payload", ["", "[]", "{", 42, None])
def test_deserializer_reports_invalid_json_shapes(payload):
    with pytest.raises(SignatureTemplateValidationError):
        signature_template_from_json(payload)


def test_repository_contract_supports_multiple_named_templates():
    beta = _template("beta", "Beta", with_assets=False)
    alpha = _template("alpha", "Alpha", with_assets=False)
    repository = InMemorySignatureTemplateRepository([beta, alpha])

    assert isinstance(repository, SignatureTemplateRepository)
    assert repository.list_all() == (alpha, beta)
    assert repository.get("beta") is beta
    assert repository.get_by_name("ALPHA") is alpha
    assert repository.get("missing") is None


def test_repository_replaces_by_stable_id_without_duplicating():
    repository = InMemorySignatureTemplateRepository()
    original = _template("one", "Original", with_assets=False)
    replacement = _template("one", "Renamed", with_assets=False)

    repository.save(original)
    assert repository.save(replacement) is replacement

    assert repository.list_all() == (replacement,)
    assert repository.get("one") is replacement


def test_repository_rejects_case_insensitive_duplicate_names_atomically():
    existing = _template("one", "Broadcast", with_assets=False)
    conflict = _template("two", "broadcast", with_assets=False)
    repository = InMemorySignatureTemplateRepository([existing])

    with pytest.raises(DuplicateSignatureTemplateNameError):
        repository.save(conflict)

    assert repository.list_all() == (existing,)
    assert repository.get("two") is None


def test_repository_rejects_a_non_template():
    repository = InMemorySignatureTemplateRepository()
    with pytest.raises(SignatureTemplateRepositoryError):
        repository.save("not a template")


def test_repository_delete_is_explicit_and_idempotent():
    template = _template(with_assets=False)
    repository = InMemorySignatureTemplateRepository([template])

    assert repository.delete(template.template_id)
    assert not repository.delete(template.template_id)
    assert repository.list_all() == ()
