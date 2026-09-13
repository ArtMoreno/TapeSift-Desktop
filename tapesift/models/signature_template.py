"""Reusable identity templates for composited Signature exports.

A template owns one identity snapshot that is shared by the linked 16:9 and
9:16 output treatments.  It intentionally does not describe layout,
typography, telestration ink, waveforms, or export encoding: those belong to
the compositor and technical export preset respectively.

User images are stored as validated bytes, never as source paths.  That makes
a saved template stable when the original file is moved, replaced, or deleted.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImageReader


SIGNATURE_TEMPLATE_SCHEMA = "tapesift.signature-template"
SIGNATURE_TEMPLATE_VERSION = 1

_MAX_ASSET_BYTES = 25 * 1024 * 1024
_MAX_ASSET_EDGE = 16_384
_MAX_ASSET_PIXELS = 100_000_000
_MAX_TEMPLATE_NAME = 100
_MAX_DISPLAY_TEXT = 120
_MAX_USERNAME_TEXT = 80
_MAX_WORDMARK_TEXT = 80
_TEMPLATE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ACCENT_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_MIME_BY_QT_FORMAT = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "webp": "image/webp",
}


class SignatureTemplateValidationError(ValueError):
    """A template or embedded asset cannot be used safely."""


class SignatureOutput(str, Enum):
    """The two linked presentation treatments driven by one template."""

    SIGNATURE_16_9 = "signature_16_9"
    VERTICAL_9_16 = "vertical_9_16"


LINKED_SIGNATURE_OUTPUTS = (
    SignatureOutput.SIGNATURE_16_9,
    SignatureOutput.VERTICAL_9_16,
)


def _require_string(
    value: object,
    field_name: str,
    *,
    required: bool,
    maximum: int,
) -> str:
    if not isinstance(value, str):
        raise SignatureTemplateValidationError(f"{field_name} must be text")
    if value != value.strip():
        raise SignatureTemplateValidationError(
            f"{field_name} cannot begin or end with whitespace"
        )
    if required and not value:
        raise SignatureTemplateValidationError(f"{field_name} is required")
    if len(value) > maximum:
        raise SignatureTemplateValidationError(
            f"{field_name} cannot exceed {maximum} characters"
        )
    if any(not character.isprintable() for character in value):
        raise SignatureTemplateValidationError(
            f"{field_name} must be a single line of printable text"
        )
    return value


def _inspect_image(data: bytes) -> tuple[str, int, int]:
    if not data:
        raise SignatureTemplateValidationError("image asset is empty")
    if len(data) > _MAX_ASSET_BYTES:
        raise SignatureTemplateValidationError(
            f"image asset exceeds {_MAX_ASSET_BYTES // (1024 * 1024)} MiB"
        )

    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
        raise SignatureTemplateValidationError("image asset could not be read")

    reader = QImageReader(buffer)
    # A filename extension is not evidence of content.  Always determine the
    # format from the captured bytes themselves.
    reader.setDecideFormatFromContent(True)
    image_format = bytes(reader.format()).decode("ascii", errors="ignore").lower()
    mime_type = _MIME_BY_QT_FORMAT.get(image_format)
    if mime_type is None:
        raise SignatureTemplateValidationError(
            "image asset must be PNG, JPEG, or WebP"
        )
    if reader.supportsAnimation() or reader.imageCount() > 1:
        raise SignatureTemplateValidationError(
            "animated image assets are not supported"
        )

    size = reader.size()
    width = size.width()
    height = size.height()
    if width <= 0 or height <= 0:
        raise SignatureTemplateValidationError("image asset has invalid dimensions")
    if width > _MAX_ASSET_EDGE or height > _MAX_ASSET_EDGE:
        raise SignatureTemplateValidationError(
            f"image asset dimensions cannot exceed {_MAX_ASSET_EDGE} pixels"
        )
    if width * height > _MAX_ASSET_PIXELS:
        raise SignatureTemplateValidationError("image asset contains too many pixels")

    image = reader.read()
    if image.isNull() or image.width() != width or image.height() != height:
        raise SignatureTemplateValidationError("image asset could not be decoded")
    return mime_type, width, height


@dataclass(frozen=True, slots=True)
class ImageAssetSnapshot:
    """A self-contained, content-addressed raster image."""

    data: bytes
    sha256: str
    mime_type: str
    width: int
    height: int

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise SignatureTemplateValidationError("image data must be immutable bytes")
        if not isinstance(self.sha256, str) or not _SHA256_PATTERN.fullmatch(
            self.sha256
        ):
            raise SignatureTemplateValidationError(
                "image sha256 must be 64 lowercase hexadecimal characters"
            )
        actual_digest = hashlib.sha256(self.data).hexdigest()
        if self.sha256 != actual_digest:
            raise SignatureTemplateValidationError("image sha256 does not match its data")

        actual_mime, actual_width, actual_height = _inspect_image(self.data)
        if self.mime_type != actual_mime:
            raise SignatureTemplateValidationError(
                "image MIME type does not match its data"
            )
        if type(self.width) is not int or type(self.height) is not int:
            raise SignatureTemplateValidationError("image dimensions must be integers")
        if (self.width, self.height) != (actual_width, actual_height):
            raise SignatureTemplateValidationError(
                "image dimensions do not match its data"
            )

    @classmethod
    def capture(cls, data: bytes | bytearray | memoryview) -> "ImageAssetSnapshot":
        """Copy and validate image bytes at the moment the user selects them."""

        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise SignatureTemplateValidationError("image data must be bytes")
        captured = bytes(data)
        mime_type, width, height = _inspect_image(captured)
        return cls(
            data=captured,
            sha256=hashlib.sha256(captured).hexdigest(),
            mime_type=mime_type,
            width=width,
            height=height,
        )

    @classmethod
    def capture_file(cls, path: str | Path) -> "ImageAssetSnapshot":
        """Read a file once; the returned model retains no mutable path."""

        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            raise SignatureTemplateValidationError(
                f"image asset could not be read: {exc}"
            ) from exc
        return cls.capture(data)


@dataclass(frozen=True, slots=True)
class SignatureIdentity:
    """Identity configured once and reused by both output treatments."""

    display_text: str
    username_text: str = ""
    wordmark_text: str = ""
    wordmark_logo: ImageAssetSnapshot | None = None
    profile_photo: ImageAssetSnapshot | None = None
    border: ImageAssetSnapshot | None = None
    accent_color: str = "#39E07A"
    show_result: bool = True

    def __post_init__(self) -> None:
        _require_string(
            self.display_text,
            "display_text",
            required=True,
            maximum=_MAX_DISPLAY_TEXT,
        )
        _require_string(
            self.username_text,
            "username_text",
            required=False,
            maximum=_MAX_USERNAME_TEXT,
        )
        _require_string(
            self.wordmark_text,
            "wordmark_text",
            required=False,
            maximum=_MAX_WORDMARK_TEXT,
        )
        for field_name in ("wordmark_logo", "profile_photo", "border"):
            asset = getattr(self, field_name)
            if asset is not None and not isinstance(asset, ImageAssetSnapshot):
                raise SignatureTemplateValidationError(
                    f"{field_name} must be an image asset snapshot"
                )
        if not isinstance(self.accent_color, str) or not _ACCENT_PATTERN.fullmatch(
            self.accent_color
        ):
            raise SignatureTemplateValidationError(
                "accent_color must use #RRGGBB hexadecimal notation"
            )
        object.__setattr__(self, "accent_color", self.accent_color.upper())
        if type(self.show_result) is not bool:
            raise SignatureTemplateValidationError("show_result must be true or false")


@dataclass(frozen=True, slots=True)
class SignatureTemplateBinding:
    """A template resolved for one of its two fixed output treatments."""

    template_id: str
    template_name: str
    output: SignatureOutput
    identity: SignatureIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.template_id, str) or not _TEMPLATE_ID_PATTERN.fullmatch(
            self.template_id
        ):
            raise SignatureTemplateValidationError("binding has an invalid template_id")
        _require_string(
            self.template_name,
            "template_name",
            required=True,
            maximum=_MAX_TEMPLATE_NAME,
        )
        if not isinstance(self.output, SignatureOutput):
            raise SignatureTemplateValidationError(
                "binding output must be a SignatureOutput"
            )
        if not isinstance(self.identity, SignatureIdentity):
            raise SignatureTemplateValidationError(
                "binding identity must be a SignatureIdentity"
            )


@dataclass(frozen=True, slots=True)
class SignatureTemplate:
    """One named identity template linked to both standard output shapes."""

    template_id: str
    name: str
    identity: SignatureIdentity

    def __post_init__(self) -> None:
        if not isinstance(self.template_id, str) or not _TEMPLATE_ID_PATTERN.fullmatch(
            self.template_id
        ):
            raise SignatureTemplateValidationError(
                "template_id must be 1-64 letters, numbers, dots, dashes, or underscores"
            )
        _require_string(
            self.name,
            "name",
            required=True,
            maximum=_MAX_TEMPLATE_NAME,
        )
        if not isinstance(self.identity, SignatureIdentity):
            raise SignatureTemplateValidationError(
                "identity must be a SignatureIdentity"
            )

    @property
    def outputs(self) -> tuple[SignatureOutput, SignatureOutput]:
        return LINKED_SIGNATURE_OUTPUTS

    def bind(self, output: SignatureOutput | str) -> SignatureTemplateBinding:
        try:
            resolved = (
                output
                if isinstance(output, SignatureOutput)
                else SignatureOutput(output)
            )
        except (TypeError, ValueError) as exc:
            raise SignatureTemplateValidationError(
                "template output must be Signature 16:9 or Vertical 9:16"
            ) from exc
        return SignatureTemplateBinding(
            template_id=self.template_id,
            template_name=self.name,
            output=resolved,
            identity=self.identity,
        )

    def to_json(self) -> str:
        return signature_template_to_json(self)

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> "SignatureTemplate":
        return signature_template_from_json(payload)


def _asset_to_dict(asset: ImageAssetSnapshot | None) -> dict[str, object] | None:
    if asset is None:
        return None
    return {
        "data_base64": base64.b64encode(asset.data).decode("ascii"),
        "height": asset.height,
        "mime_type": asset.mime_type,
        "sha256": asset.sha256,
        "width": asset.width,
    }


def _expect_mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SignatureTemplateValidationError(f"{field_name} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    field_name: str,
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise SignatureTemplateValidationError(
            f"{field_name} has invalid fields: {'; '.join(details)}"
        )


def _asset_from_dict(
    value: object,
    field_name: str,
) -> ImageAssetSnapshot | None:
    if value is None:
        return None
    data = _expect_mapping(value, field_name)
    _require_exact_keys(
        data,
        {"data_base64", "height", "mime_type", "sha256", "width"},
        field_name,
    )
    encoded = data["data_base64"]
    if not isinstance(encoded, str):
        raise SignatureTemplateValidationError(f"{field_name}.data_base64 must be text")
    try:
        captured = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise SignatureTemplateValidationError(
            f"{field_name}.data_base64 is invalid"
        ) from exc
    return ImageAssetSnapshot(
        data=captured,
        sha256=data["sha256"],
        mime_type=data["mime_type"],
        width=data["width"],
        height=data["height"],
    )


def signature_template_to_dict(template: SignatureTemplate) -> dict[str, object]:
    if not isinstance(template, SignatureTemplate):
        raise SignatureTemplateValidationError(
            "only SignatureTemplate instances can be serialized"
        )
    identity = template.identity
    return {
        "schema": SIGNATURE_TEMPLATE_SCHEMA,
        "template": {
            "identity": {
                "accent_color": identity.accent_color,
                "border": _asset_to_dict(identity.border),
                "display_text": identity.display_text,
                "profile_photo": _asset_to_dict(identity.profile_photo),
                "show_result": identity.show_result,
                "username_text": identity.username_text,
                "wordmark_logo": _asset_to_dict(identity.wordmark_logo),
                "wordmark_text": identity.wordmark_text,
            },
            "name": template.name,
            "template_id": template.template_id,
        },
        "version": SIGNATURE_TEMPLATE_VERSION,
    }


def signature_template_to_json(template: SignatureTemplate) -> str:
    """Return canonical UTF-8-safe JSON for hashing and persistence."""

    return json.dumps(
        signature_template_to_dict(template),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def signature_template_from_dict(payload: Mapping[str, Any]) -> SignatureTemplate:
    root = _expect_mapping(payload, "template document")
    _require_exact_keys(root, {"schema", "template", "version"}, "template document")
    if root["schema"] != SIGNATURE_TEMPLATE_SCHEMA:
        raise SignatureTemplateValidationError("unknown template schema")
    if type(root["version"]) is not int or root["version"] != SIGNATURE_TEMPLATE_VERSION:
        raise SignatureTemplateValidationError(
            f"unsupported template version: {root['version']!r}"
        )

    template_data = _expect_mapping(root["template"], "template")
    _require_exact_keys(
        template_data,
        {"identity", "name", "template_id"},
        "template",
    )
    identity_data = _expect_mapping(template_data["identity"], "identity")
    _require_exact_keys(
        identity_data,
        {
            "accent_color",
            "border",
            "display_text",
            "profile_photo",
            "show_result",
            "username_text",
            "wordmark_logo",
            "wordmark_text",
        },
        "identity",
    )
    return SignatureTemplate(
        template_id=template_data["template_id"],
        name=template_data["name"],
        identity=SignatureIdentity(
            display_text=identity_data["display_text"],
            username_text=identity_data["username_text"],
            wordmark_text=identity_data["wordmark_text"],
            wordmark_logo=_asset_from_dict(
                identity_data["wordmark_logo"], "identity.wordmark_logo"
            ),
            profile_photo=_asset_from_dict(
                identity_data["profile_photo"], "identity.profile_photo"
            ),
            border=_asset_from_dict(identity_data["border"], "identity.border"),
            accent_color=identity_data["accent_color"],
            show_result=identity_data["show_result"],
        ),
    )


def signature_template_from_json(
    payload: str | bytes | bytearray,
) -> SignatureTemplate:
    if not isinstance(payload, (str, bytes, bytearray)):
        raise SignatureTemplateValidationError("template JSON must be text or bytes")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SignatureTemplateValidationError("template JSON is invalid") from exc
    return signature_template_from_dict(decoded)
