"""Renderer-neutral geometry for TapeSift Signature exports.

The composition plan is the single contract shared by export preview and the
event-driven final compositor.  It describes *what goes where*; it does not
draw pixels, invoke FFmpeg, or expose layout knobs.

One locked identity snapshot drives both standard treatments:

* Signature: 1920 x 1080, branded rail, letterboxed film, lower information band.
* Vertical: 1080 x 1920, call above a safe middle film band, identity and
  waveform below.

Football film is always contained.  It is never center-cropped to make a
social aspect ratio, because losing players at the edge of the frame would
change the analysis rather than merely restyle it.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from tapesift.models.export_package import (
    ExportPackageSnapshot,
    ExportStyle,
    ExportTemplateSnapshot,
)


__all__ = (
    "COMPOSITION_PLAN_SCHEMA",
    "COMPOSITION_PLAN_VERSION",
    "CompositionLayer",
    "CompositionLayerKind",
    "CompositionPlan",
    "CompositionPlanValidationError",
    "EncoderTransformPolicy",
    "IdentityAssetRole",
    "ImmutableAssetReference",
    "LayerFitPolicy",
    "LayerMaskPolicy",
    "LayerTextSource",
    "LockedTemplateIdentity",
    "PixelRect",
    "PixelSize",
    "SourceFilmPlacement",
    "SourceFitPolicy",
    "build_composition_plan",
)


COMPOSITION_PLAN_SCHEMA = "tapesift.composition-plan"
COMPOSITION_PLAN_VERSION = 2
# The standard concept calls for a 2.5 px progress rule. PixelRect is an
# integer-raster contract, so the shared preview/final plan rounds that half
# pixel up to three output pixels deterministically.
_PROGRESS_THICKNESS_PX = 3
_NORMALIZED_SCALE = 10_000
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SUPPORTED_IMAGE_MIME_TYPES = frozenset({
    "image/jpeg",
    "image/png",
    "image/webp",
})


class CompositionPlanValidationError(ValueError):
    """The requested composition violates the shared reference treatment."""

    def __init__(self, errors: tuple[str, ...] | list[str] | str) -> None:
        if isinstance(errors, str):
            errors = (errors,)
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


@dataclass(frozen=True, slots=True)
class PixelSize:
    width: int
    height: int

    def __post_init__(self) -> None:
        if type(self.width) is not int or type(self.height) is not int:
            raise CompositionPlanValidationError("Pixel dimensions must be integers.")
        if self.width <= 0 or self.height <= 0:
            raise CompositionPlanValidationError("Pixel dimensions must be positive.")


@dataclass(frozen=True, slots=True)
class PixelRect:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in (
            self.x, self.y, self.width, self.height
        )):
            raise CompositionPlanValidationError("Pixel rectangles require integers.")
        if self.x < 0 or self.y < 0:
            raise CompositionPlanValidationError(
                "Pixel rectangle origins cannot be negative."
            )
        if self.width <= 0 or self.height <= 0:
            raise CompositionPlanValidationError(
                "Pixel rectangle dimensions must be positive."
            )

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    def contains(self, other: "PixelRect") -> bool:
        return (
            self.x <= other.x
            and self.y <= other.y
            and self.right >= other.right
            and self.bottom >= other.bottom
        )

    def overlaps(self, other: "PixelRect") -> bool:
        return not (
            self.right <= other.x
            or other.right <= self.x
            or self.bottom <= other.y
            or other.bottom <= self.y
        )


@dataclass(frozen=True, slots=True)
class _NormalizedRect:
    """A locked rectangle in ten-thousandths of an output canvas."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if any(type(value) is not int for value in values):
            raise CompositionPlanValidationError(
                "Normalized rectangles require integer ten-thousandths."
            )
        if self.x < 0 or self.y < 0 or self.width <= 0 or self.height <= 0:
            raise CompositionPlanValidationError("Normalized rectangle is invalid.")
        if self.x + self.width > _NORMALIZED_SCALE:
            raise CompositionPlanValidationError(
                "Normalized rectangle exceeds the canvas width."
            )
        if self.y + self.height > _NORMALIZED_SCALE:
            raise CompositionPlanValidationError(
                "Normalized rectangle exceeds the canvas height."
            )

    def resolve(self, canvas: PixelSize) -> PixelRect:
        def edge(value: int, extent: int) -> int:
            # Integer half-up rounding is deterministic across Python/Qt/FFmpeg.
            return (value * extent + (_NORMALIZED_SCALE // 2)) // _NORMALIZED_SCALE

        left = edge(self.x, canvas.width)
        top = edge(self.y, canvas.height)
        right = edge(self.x + self.width, canvas.width)
        bottom = edge(self.y + self.height, canvas.height)
        return PixelRect(left, top, right - left, bottom - top)


class SourceFitPolicy(str, Enum):
    SOURCE_PASSTHROUGH = "source_passthrough"
    CONTAIN_LETTERBOX = "contain_letterbox"


class EncoderTransformPolicy(str, Enum):
    """Whether a later encoder may change the plan's pixel canvas."""

    TECHNICAL_PRESET = "technical_preset"
    PRESERVE_COMPOSITOR_CANVAS = "preserve_compositor_canvas"


@dataclass(frozen=True, slots=True)
class SourceFilmPlacement:
    source_size: PixelSize
    slot_rect: PixelRect
    content_rect: PixelRect
    source_crop_rect: PixelRect
    fit_policy: SourceFitPolicy
    letterbox_color: str = "#000000"

    def __post_init__(self) -> None:
        if not isinstance(self.source_size, PixelSize):
            raise CompositionPlanValidationError(
                "Source-film source size must be a PixelSize."
            )
        for field_name in ("slot_rect", "content_rect", "source_crop_rect"):
            if not isinstance(getattr(self, field_name), PixelRect):
                raise CompositionPlanValidationError(
                    f"Source-film {field_name} must be a PixelRect."
                )
        if not isinstance(self.letterbox_color, str):
            raise CompositionPlanValidationError(
                "Letterbox color must use #RRGGBB notation."
            )
        try:
            object.__setattr__(self, "fit_policy", SourceFitPolicy(self.fit_policy))
        except (TypeError, ValueError) as exc:
            raise CompositionPlanValidationError(
                "Source-film fit policy is invalid."
            ) from exc

    def validation_errors(self, canvas: PixelSize) -> tuple[str, ...]:
        errors: list[str] = []
        canvas_rect = PixelRect(0, 0, canvas.width, canvas.height)
        if not canvas_rect.contains(self.slot_rect):
            errors.append("Source film slot must remain inside the output canvas.")
        if not self.slot_rect.contains(self.content_rect):
            errors.append("Contained source film must remain inside its slot.")
        full_source = PixelRect(
            0, 0, self.source_size.width, self.source_size.height
        )
        if self.source_crop_rect != full_source:
            errors.append("Football film must use the complete source frame; crop is forbidden.")
        if self.fit_policy is SourceFitPolicy.SOURCE_PASSTHROUGH:
            if self.slot_rect != self.content_rect:
                errors.append("Source-only output cannot letterbox or reposition the film.")
        elif self.fit_policy is SourceFitPolicy.CONTAIN_LETTERBOX:
            expected = _contain_rect(self.source_size, self.slot_rect)
            if self.content_rect != expected:
                errors.append("Letterboxed film content does not match contain geometry.")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.letterbox_color):
            errors.append("Letterbox color must use #RRGGBB notation.")
        return tuple(errors)


class IdentityAssetRole(str, Enum):
    BORDER = "border"
    PROFILE_PHOTO = "profile_photo"
    WORDMARK_LOGO = "wordmark_logo"


class LayerFitPolicy(str, Enum):
    NONE = "none"
    CONTAIN = "contain"
    COVER = "cover"


class LayerMaskPolicy(str, Enum):
    NONE = "none"
    CIRCLE = "circle"


class LayerTextSource(str, Enum):
    NONE = "none"
    TEMPLATE_DISPLAY = "template_display"
    TEMPLATE_USERNAME = "template_username"
    TEMPLATE_WORDMARK = "template_wordmark"
    PLAY_CALL_SITUATION = "play_call_situation"
    PLAY_CALL_CONCEPT = "play_call_concept"
    PLAY_CALL_RESULT = "play_call_result"


@dataclass(frozen=True, slots=True)
class ImmutableAssetReference:
    """A content-addressed reference into an embedded template snapshot."""

    role: IdentityAssetRole
    sha256: str
    mime_type: str
    width: int
    height: int

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "role", IdentityAssetRole(self.role))
        except (TypeError, ValueError) as exc:
            raise CompositionPlanValidationError(
                "Identity asset role is invalid."
            ) from exc
        if not isinstance(self.sha256, str) or not _SHA256_PATTERN.fullmatch(
            self.sha256
        ):
            raise CompositionPlanValidationError(
                "Identity asset sha256 must be 64 lowercase hexadecimal characters."
            )
        if (
            not isinstance(self.mime_type, str)
            or self.mime_type not in _SUPPORTED_IMAGE_MIME_TYPES
        ):
            raise CompositionPlanValidationError(
                "Identity asset MIME type must be PNG, JPEG, or WebP."
            )
        if type(self.width) is not int or type(self.height) is not int:
            raise CompositionPlanValidationError(
                "Identity asset dimensions must be integers."
            )
        if self.width <= 0 or self.height <= 0:
            raise CompositionPlanValidationError(
                "Identity asset dimensions must be positive."
            )

    @classmethod
    def capture(
        cls,
        role: IdentityAssetRole,
        asset_snapshot: object | None,
    ) -> "ImmutableAssetReference | None":
        try:
            resolved_role = IdentityAssetRole(role)
        except (TypeError, ValueError) as exc:
            raise CompositionPlanValidationError(
                "Identity asset role is invalid."
            ) from exc
        if asset_snapshot is None:
            return None
        try:
            data = asset_snapshot.data
            digest = asset_snapshot.sha256
            mime_type = asset_snapshot.mime_type
            width = asset_snapshot.width
            height = asset_snapshot.height
        except AttributeError as exc:
            raise CompositionPlanValidationError(
                f"Template {resolved_role.value} is not an image asset snapshot."
            ) from exc
        if not isinstance(data, bytes):
            raise CompositionPlanValidationError(
                f"Template {resolved_role.value} bytes are not immutable."
            )
        if hashlib.sha256(data).hexdigest() != digest:
            raise CompositionPlanValidationError(
                f"Template {resolved_role.value} digest does not match its bytes."
            )
        return cls(resolved_role, digest, mime_type, width, height)


def _identity_asset_lock_dict(
    asset: ImmutableAssetReference | None,
) -> dict[str, object] | None:
    if asset is None:
        return None
    return {
        "height": asset.height,
        "mime_type": asset.mime_type,
        "role": asset.role.value,
        "sha256": asset.sha256,
        "width": asset.width,
    }


def _identity_integrity_digest(
    *,
    template_id: str,
    revision: int,
    snapshot_sha256: str,
    display_text: str,
    username_text: str,
    wordmark_text: str,
    accent_color: str,
    show_result: bool,
    border: ImmutableAssetReference | None,
    profile_photo: ImmutableAssetReference | None,
    wordmark_logo: ImmutableAssetReference | None,
) -> str:
    payload = {
        "accent_color": accent_color,
        "border": _identity_asset_lock_dict(border),
        "display_text": display_text,
        "profile_photo": _identity_asset_lock_dict(profile_photo),
        "revision": revision,
        "show_result": show_result,
        "snapshot_sha256": snapshot_sha256,
        "template_id": template_id,
        "username_text": username_text,
        "wordmark_logo": _identity_asset_lock_dict(wordmark_logo),
        "wordmark_text": wordmark_text,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LockedTemplateIdentity:
    """Identity-only template state shared by both composition treatments.

    Layout, typography, ink palette, waveform treatment, and safe areas do not
    live here and therefore cannot drift from one export to the next.
    """

    template_id: str
    revision: int
    snapshot_sha256: str
    integrity_sha256: str
    display_text: str
    username_text: str
    wordmark_text: str
    accent_color: str
    show_result: bool
    border: ImmutableAssetReference | None = None
    profile_photo: ImmutableAssetReference | None = None
    wordmark_logo: ImmutableAssetReference | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.template_id, str) or not self.template_id.strip():
            raise CompositionPlanValidationError("Locked template id is required.")
        if type(self.revision) is not int or self.revision < 1:
            raise CompositionPlanValidationError(
                "Locked template revision must be at least 1."
            )
        if not isinstance(self.snapshot_sha256, str) or not _SHA256_PATTERN.fullmatch(
            self.snapshot_sha256
        ):
            raise CompositionPlanValidationError(
                "Locked template snapshot requires a lowercase SHA-256 digest."
            )
        if not isinstance(self.integrity_sha256, str) or not _SHA256_PATTERN.fullmatch(
            self.integrity_sha256
        ):
            raise CompositionPlanValidationError(
                "Locked template integrity requires a lowercase SHA-256 digest."
            )
        if not isinstance(self.display_text, str) or not self.display_text.strip():
            raise CompositionPlanValidationError("Template display text is required.")
        if not isinstance(self.username_text, str):
            raise CompositionPlanValidationError("Template username must be text.")
        if not isinstance(self.wordmark_text, str):
            raise CompositionPlanValidationError("Template wordmark must be text.")
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", self.accent_color):
            raise CompositionPlanValidationError(
                "Template accent color must use #RRGGBB notation."
            )
        object.__setattr__(self, "accent_color", self.accent_color.upper())
        if type(self.show_result) is not bool:
            raise CompositionPlanValidationError(
                "Template result visibility must be true or false."
            )
        expected_roles = {
            "border": IdentityAssetRole.BORDER,
            "profile_photo": IdentityAssetRole.PROFILE_PHOTO,
            "wordmark_logo": IdentityAssetRole.WORDMARK_LOGO,
        }
        for field_name, expected_role in expected_roles.items():
            asset = getattr(self, field_name)
            if asset is not None and not isinstance(
                asset, ImmutableAssetReference
            ):
                raise CompositionPlanValidationError(
                    f"Template {field_name} must be an immutable asset reference."
                )
            if asset is not None and asset.role is not expected_role:
                raise CompositionPlanValidationError(
                    f"Template {field_name} has the wrong asset role."
                )
        expected_integrity = _identity_integrity_digest(
            template_id=self.template_id,
            revision=self.revision,
            snapshot_sha256=self.snapshot_sha256,
            display_text=self.display_text,
            username_text=self.username_text,
            wordmark_text=self.wordmark_text,
            accent_color=self.accent_color,
            show_result=self.show_result,
            border=self.border,
            profile_photo=self.profile_photo,
            wordmark_logo=self.wordmark_logo,
        )
        if self.integrity_sha256 != expected_integrity:
            raise CompositionPlanValidationError(
                "Locked template identity failed its integrity check."
            )

    @classmethod
    def capture(
        cls,
        snapshot: ExportTemplateSnapshot,
        signature_template: object,
    ) -> "LockedTemplateIdentity":
        """Lock a structural ``SignatureTemplate`` without importing Qt types.

        The template model owns and validates the embedded image bytes.  This
        plan retains only content-addressed references, while the queued
        template snapshot remains the authoritative byte store.
        """

        if not isinstance(snapshot, ExportTemplateSnapshot):
            raise CompositionPlanValidationError(
                "An immutable export template snapshot is required."
            )
        template_errors = snapshot.validation_errors()
        if template_errors:
            raise CompositionPlanValidationError(list(template_errors))
        try:
            template_id = signature_template.template_id
            identity = signature_template.identity
            template_json = signature_template.to_json()
        except AttributeError as exc:
            raise CompositionPlanValidationError(
                "A structural SignatureTemplate is required to lock identity."
            ) from exc
        if template_id != snapshot.template_id:
            raise CompositionPlanValidationError(
                "Template snapshot id does not match the resolved signature template."
            )
        try:
            snapshot_data = json.loads(snapshot.payload_json)
            resolved_data = json.loads(template_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise CompositionPlanValidationError(
                "Signature template JSON cannot be resolved."
            ) from exc
        if snapshot_data != resolved_data:
            raise CompositionPlanValidationError(
                "Resolved signature template does not match the queued snapshot."
            )
        canonical = json.dumps(
            snapshot_data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        try:
            snapshot_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            border = ImmutableAssetReference.capture(
                IdentityAssetRole.BORDER, identity.border
            )
            profile_photo = ImmutableAssetReference.capture(
                IdentityAssetRole.PROFILE_PHOTO, identity.profile_photo
            )
            wordmark_logo = ImmutableAssetReference.capture(
                IdentityAssetRole.WORDMARK_LOGO, identity.wordmark_logo
            )
            lock_values = {
                "template_id": snapshot.template_id,
                "revision": snapshot.revision,
                "snapshot_sha256": snapshot_sha256,
                "display_text": identity.display_text,
                "username_text": identity.username_text,
                "wordmark_text": identity.wordmark_text,
                "accent_color": identity.accent_color,
                "show_result": identity.show_result,
                "border": border,
                "profile_photo": profile_photo,
                "wordmark_logo": wordmark_logo,
            }
            locked = cls(
                **lock_values,
                integrity_sha256=_identity_integrity_digest(**lock_values),
            )
        except AttributeError as exc:
            raise CompositionPlanValidationError(
                "Resolved signature template has an invalid identity."
            ) from exc
        if not _identity_matches_template_payload(locked, snapshot_data):
            raise CompositionPlanValidationError(
                "Resolved template identity differs from its queued JSON snapshot."
            )
        return locked

    def matches(self, snapshot: ExportTemplateSnapshot) -> bool:
        if not isinstance(snapshot, ExportTemplateSnapshot):
            return False
        if snapshot.validation_errors():
            return False
        try:
            payload = json.loads(snapshot.payload_json)
        except (TypeError, json.JSONDecodeError):
            return False
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return (
            self.template_id == snapshot.template_id
            and self.revision == snapshot.revision
            and self.snapshot_sha256
            == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            and _identity_matches_template_payload(self, payload)
        )


def _payload_asset_matches(
    reference: ImmutableAssetReference | None,
    value: object,
) -> bool:
    if reference is None:
        return value is None
    if not isinstance(value, Mapping):
        return False
    if set(value) != {
        "data_base64",
        "height",
        "mime_type",
        "sha256",
        "width",
    }:
        return False
    encoded = value.get("data_base64")
    if not isinstance(encoded, str):
        return False
    try:
        raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error):
        return False
    return (
        isinstance(value.get("sha256"), str)
        and isinstance(value.get("mime_type"), str)
        and type(value.get("width")) is int
        and type(value.get("height")) is int
        and hashlib.sha256(raw).hexdigest() == reference.sha256
        and value.get("sha256") == reference.sha256
        and value.get("mime_type") == reference.mime_type
        and value.get("width") == reference.width
        and value.get("height") == reference.height
    )


def _identity_matches_template_payload(
    identity: LockedTemplateIdentity,
    payload: object,
) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if set(payload) != {"schema", "template", "version"}:
        return False
    if payload.get("schema") != "tapesift.signature-template":
        return False
    if type(payload.get("version")) is not int or payload.get("version") != 1:
        return False
    template = payload.get("template")
    if not isinstance(template, Mapping):
        return False
    if set(template) != {"identity", "name", "template_id"}:
        return False
    if not isinstance(template.get("name"), str) or not template["name"].strip():
        return False
    if template.get("template_id") != identity.template_id:
        return False
    raw_identity = template.get("identity")
    if not isinstance(raw_identity, Mapping):
        return False
    if set(raw_identity) != {
        "accent_color",
        "border",
        "display_text",
        "profile_photo",
        "show_result",
        "username_text",
        "wordmark_logo",
        "wordmark_text",
    }:
        return False
    scalar_values = {
        "accent_color": identity.accent_color,
        "display_text": identity.display_text,
        "show_result": identity.show_result,
        "username_text": identity.username_text,
        "wordmark_text": identity.wordmark_text,
    }
    if (
        not isinstance(raw_identity.get("accent_color"), str)
        or not isinstance(raw_identity.get("display_text"), str)
        or type(raw_identity.get("show_result")) is not bool
        or not isinstance(raw_identity.get("username_text"), str)
        or not isinstance(raw_identity.get("wordmark_text"), str)
    ):
        return False
    if any(raw_identity.get(key) != value for key, value in scalar_values.items()):
        return False
    return (
        _payload_asset_matches(identity.border, raw_identity.get("border"))
        and _payload_asset_matches(
            identity.profile_photo, raw_identity.get("profile_photo")
        )
        and _payload_asset_matches(
            identity.wordmark_logo, raw_identity.get("wordmark_logo")
        )
    )


class CompositionLayerKind(str, Enum):
    BACKGROUND = "background"
    FILM = "film"
    INK = "ink"
    BORDER = "border"
    PROFILE_PHOTO = "profile_photo"
    IDENTITY_DISPLAY = "identity_display"
    IDENTITY_USERNAME = "identity_username"
    WORDMARK = "wordmark"
    SITUATION = "situation"
    PLAY_CALL = "play_call"
    RESULT = "result"
    WAVEFORM = "waveform"
    PROGRESS = "progress"


_LAYER_RANK = {
    kind: rank
    for rank, kind in enumerate((
        CompositionLayerKind.BACKGROUND,
        CompositionLayerKind.FILM,
        CompositionLayerKind.INK,
        CompositionLayerKind.BORDER,
        CompositionLayerKind.WORDMARK,
        CompositionLayerKind.PROFILE_PHOTO,
        CompositionLayerKind.IDENTITY_DISPLAY,
        CompositionLayerKind.IDENTITY_USERNAME,
        CompositionLayerKind.SITUATION,
        CompositionLayerKind.PLAY_CALL,
        CompositionLayerKind.RESULT,
        CompositionLayerKind.WAVEFORM,
        CompositionLayerKind.PROGRESS,
    ))
}


@dataclass(frozen=True, slots=True)
class CompositionLayer:
    kind: CompositionLayerKind
    rect: PixelRect
    input_name: str = ""
    asset: ImmutableAssetReference | None = None
    fit_policy: LayerFitPolicy = LayerFitPolicy.NONE
    mask_policy: LayerMaskPolicy = LayerMaskPolicy.NONE
    text_source: LayerTextSource = LayerTextSource.NONE

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "kind", CompositionLayerKind(self.kind))
            object.__setattr__(
                self, "fit_policy", LayerFitPolicy(self.fit_policy)
            )
            object.__setattr__(
                self, "mask_policy", LayerMaskPolicy(self.mask_policy)
            )
            object.__setattr__(
                self, "text_source", LayerTextSource(self.text_source)
            )
        except (TypeError, ValueError) as exc:
            raise CompositionPlanValidationError(
                "Composition layer semantics are invalid."
            ) from exc
        if not isinstance(self.input_name, str):
            raise CompositionPlanValidationError("Layer input name must be text.")
        if not isinstance(self.rect, PixelRect):
            raise CompositionPlanValidationError("Layer rectangle must be a PixelRect.")
        if self.asset is not None and not isinstance(
            self.asset, ImmutableAssetReference
        ):
            raise CompositionPlanValidationError(
                "Layer asset must be an immutable asset reference."
            )


@dataclass(frozen=True, slots=True)
class _LayoutContract:
    canvas: PixelSize
    safe_area: _NormalizedRect
    film_slot: _NormalizedRect
    wordmark: _NormalizedRect
    profile_photo: _NormalizedRect
    identity_display: _NormalizedRect
    identity_username: _NormalizedRect
    situation: _NormalizedRect
    play_call: _NormalizedRect
    result: _NormalizedRect
    waveform: _NormalizedRect
    progress: _NormalizedRect


# Central, unexposed reference-treatment constants. They describe the standard
# hierarchy in normalized coordinates so preview and final export resolve the
# same geometry. Exact pixel placement remains provisional until both formats
# pass the rendered owner-review gate; these values must not be exposed as
# user settings or described as a final pixel-perfect specification.
_SIGNATURE_LAYOUT = _LayoutContract(
    canvas=PixelSize(1920, 1080),
    safe_area=_NormalizedRect(500, 500, 9000, 9000),
    film_slot=_NormalizedRect(500, 1220, 9000, 6670),
    wordmark=_NormalizedRect(500, 500, 2000, 560),
    profile_photo=_NormalizedRect(500, 8889, 281, 500),
    identity_display=_NormalizedRect(854, 8907, 1302, 204),
    identity_username=_NormalizedRect(854, 9167, 1302, 167),
    situation=_NormalizedRect(1290, 8055, 5400, 220),
    play_call=_NormalizedRect(1290, 8315, 5400, 480),
    result=_NormalizedRect(8000, 8055, 1500, 740),
    waveform=_NormalizedRect(2292, 8907, 7208, 444),
    progress=_NormalizedRect(500, 9470, 9000, 30),
)

_VERTICAL_LAYOUT = _LayoutContract(
    canvas=PixelSize(1080, 1920),
    safe_area=_NormalizedRect(667, 625, 8666, 7750),
    film_slot=_NormalizedRect(555, 2750, 8890, 2815),
    wordmark=_NormalizedRect(667, 625, 3333, 375),
    profile_photo=_NormalizedRect(667, 5989, 926, 521),
    identity_display=_NormalizedRect(1815, 6000, 4167, 198),
    identity_username=_NormalizedRect(1815, 6250, 4167, 167),
    situation=_NormalizedRect(667, 1250, 6555, 220),
    play_call=_NormalizedRect(667, 1530, 6555, 845),
    result=_NormalizedRect(7555, 1250, 1778, 1125),
    waveform=_NormalizedRect(1667, 6771, 6666, 333),
    progress=_NormalizedRect(667, 7875, 8666, 25),
)


@dataclass(frozen=True, slots=True)
class CompositionPlan:
    style: ExportStyle
    canvas: PixelSize
    safe_area: PixelRect
    source_film: SourceFilmPlacement
    layers: tuple[CompositionLayer, ...]
    presentation_event_input_name: str
    encoder_transform_policy: EncoderTransformPolicy
    template_identity: LockedTemplateIdentity | None = None

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "style", ExportStyle(self.style))
            object.__setattr__(
                self,
                "encoder_transform_policy",
                EncoderTransformPolicy(self.encoder_transform_policy),
            )
        except (TypeError, ValueError) as exc:
            raise CompositionPlanValidationError(
                "Composition plan style or encoder policy is invalid."
            ) from exc
        if not isinstance(self.canvas, PixelSize):
            raise CompositionPlanValidationError(
                "Composition canvas must be a PixelSize."
            )
        if not isinstance(self.safe_area, PixelRect):
            raise CompositionPlanValidationError(
                "Composition safe area must be a PixelRect."
            )
        if not isinstance(self.source_film, SourceFilmPlacement):
            raise CompositionPlanValidationError(
                "Composition source film must be a SourceFilmPlacement."
            )
        try:
            frozen_layers = tuple(self.layers)
        except TypeError as exc:
            raise CompositionPlanValidationError(
                "Composition layers must be an iterable of immutable layers."
            ) from exc
        object.__setattr__(self, "layers", frozen_layers)
        if not isinstance(self.presentation_event_input_name, str):
            raise CompositionPlanValidationError(
                "Presentation event input name must be text."
            )
        errors = self.validation_errors()
        if errors:
            raise CompositionPlanValidationError(errors)

    @property
    def is_composited(self) -> bool:
        return self.style in {ExportStyle.SIGNATURE, ExportStyle.VERTICAL}

    def layer(self, kind: CompositionLayerKind) -> CompositionLayer | None:
        resolved = CompositionLayerKind(kind)
        return next((layer for layer in self.layers if layer.kind is resolved), None)

    def validation_errors(self) -> tuple[str, ...]:
        errors = list(self.source_film.validation_errors(self.canvas))
        canvas_rect = PixelRect(0, 0, self.canvas.width, self.canvas.height)
        if not canvas_rect.contains(self.safe_area):
            errors.append("Safe area must remain inside the output canvas.")
        seen_kinds: set[CompositionLayerKind] = set()
        previous_rank = -1
        for layer in self.layers:
            if not isinstance(layer, CompositionLayer):
                errors.append("Composition layers must be immutable layer snapshots.")
                continue
            if layer.kind in seen_kinds:
                errors.append(f"Composition layer is duplicated: {layer.kind.value}.")
            seen_kinds.add(layer.kind)
            rank = _LAYER_RANK[layer.kind]
            if rank <= previous_rank:
                errors.append("Composition layers are not in the locked paint order.")
            previous_rank = rank
            if not canvas_rect.contains(layer.rect):
                errors.append(
                    f"Composition layer leaves the output canvas: {layer.kind.value}."
                )

        if self.style is ExportStyle.CLEAN:
            full_canvas = PixelRect(0, 0, self.canvas.width, self.canvas.height)
            if self.template_identity is not None:
                errors.append("Clean composition cannot retain template identity.")
            if self.presentation_event_input_name:
                errors.append("Clean composition cannot retain a presentation event input.")
            if self.canvas != self.source_film.source_size:
                errors.append("Clean composition canvas must match the source dimensions.")
            if self.safe_area != full_canvas:
                errors.append("Clean composition safe area must be the complete source frame.")
            if (
                self.source_film.slot_rect != full_canvas
                or self.source_film.content_rect != full_canvas
            ):
                errors.append("Clean composition cannot reposition the source film.")
            if self.layers != (
                CompositionLayer(
                    CompositionLayerKind.FILM,
                    self.source_film.content_rect,
                    "source_video",
                ),
            ):
                errors.append("Clean composition must contain source film only.")
            if self.encoder_transform_policy is not EncoderTransformPolicy.TECHNICAL_PRESET:
                errors.append("Clean composition must retain the technical preset policy.")
        else:
            if self.template_identity is None:
                errors.append("Composited output requires one locked template identity.")
            contract = (
                _SIGNATURE_LAYOUT
                if self.style is ExportStyle.SIGNATURE
                else _VERTICAL_LAYOUT
            )
            expected_canvas = contract.canvas
            if self.canvas != expected_canvas:
                errors.append("Composited output canvas does not match its fixed treatment.")
            expected_safe_area = contract.safe_area.resolve(expected_canvas)
            if self.safe_area != expected_safe_area:
                errors.append("Composited output safe area does not match its fixed treatment.")
            expected_slot = contract.film_slot.resolve(expected_canvas)
            if self.source_film.slot_rect != expected_slot:
                errors.append("Source-film slot does not match the locked treatment geometry.")
            expected_content = _contain_rect(self.source_film.source_size, expected_slot)
            if self.source_film.content_rect != expected_content:
                errors.append("Source-film content does not match the locked contain policy.")
            if (
                self.encoder_transform_policy
                is not EncoderTransformPolicy.PRESERVE_COMPOSITOR_CANVAS
            ):
                errors.append(
                    "Composited frames must bypass technical scaling and cropping."
                )
            required = {
                CompositionLayerKind.BACKGROUND,
                CompositionLayerKind.FILM,
                CompositionLayerKind.IDENTITY_DISPLAY,
                CompositionLayerKind.PROGRESS,
            }
            if self.template_identity is not None:
                identity = self.template_identity
                if identity.profile_photo is not None:
                    required.add(CompositionLayerKind.PROFILE_PHOTO)
                if identity.username_text.strip():
                    required.add(CompositionLayerKind.IDENTITY_USERNAME)
                if identity.wordmark_logo is not None or identity.wordmark_text.strip():
                    required.add(CompositionLayerKind.WORDMARK)
            missing = required - seen_kinds
            if missing:
                errors.append(
                    "Composited output is missing locked layers: "
                    + ", ".join(sorted(kind.value for kind in missing))
                    + "."
                )
            expected_rects = {
                CompositionLayerKind.BACKGROUND: PixelRect(
                    0, 0, expected_canvas.width, expected_canvas.height
                ),
                CompositionLayerKind.FILM: expected_content,
                CompositionLayerKind.INK: expected_content,
                CompositionLayerKind.BORDER: PixelRect(
                    0, 0, expected_canvas.width, expected_canvas.height
                ),
                CompositionLayerKind.PROFILE_PHOTO: contract.profile_photo.resolve(
                    expected_canvas
                ),
                CompositionLayerKind.IDENTITY_DISPLAY: contract.identity_display.resolve(
                    expected_canvas
                ),
                CompositionLayerKind.IDENTITY_USERNAME: contract.identity_username.resolve(
                    expected_canvas
                ),
                CompositionLayerKind.WORDMARK: contract.wordmark.resolve(expected_canvas),
                CompositionLayerKind.SITUATION: contract.situation.resolve(expected_canvas),
                CompositionLayerKind.PLAY_CALL: contract.play_call.resolve(expected_canvas),
                CompositionLayerKind.RESULT: contract.result.resolve(expected_canvas),
                CompositionLayerKind.WAVEFORM: contract.waveform.resolve(expected_canvas),
                CompositionLayerKind.PROGRESS: _progress_rect(
                    contract, expected_canvas
                ),
            }
            expected_inputs = {
                CompositionLayerKind.BACKGROUND: "",
                CompositionLayerKind.FILM: "source_video",
                CompositionLayerKind.INK: "ink_event_track",
                CompositionLayerKind.BORDER: "template.border",
                CompositionLayerKind.PROFILE_PHOTO: "template.profile_photo",
                CompositionLayerKind.IDENTITY_DISPLAY: "template.display_text",
                CompositionLayerKind.IDENTITY_USERNAME: "template.username_text",
                CompositionLayerKind.WORDMARK: "template.wordmark_text",
                CompositionLayerKind.SITUATION: "play_call_situation",
                CompositionLayerKind.PLAY_CALL: "play_call_concept",
                CompositionLayerKind.RESULT: "play_call_result",
                CompositionLayerKind.WAVEFORM: "voiceover_audio",
                CompositionLayerKind.PROGRESS: "timeline_progress",
            }
            expected_fit = {
                kind: LayerFitPolicy.NONE for kind in CompositionLayerKind
            }
            expected_fit[CompositionLayerKind.PROFILE_PHOTO] = LayerFitPolicy.COVER
            expected_mask = {
                kind: LayerMaskPolicy.NONE for kind in CompositionLayerKind
            }
            expected_mask[CompositionLayerKind.PROFILE_PHOTO] = LayerMaskPolicy.CIRCLE
            expected_text = {
                kind: LayerTextSource.NONE for kind in CompositionLayerKind
            }
            expected_text.update({
                CompositionLayerKind.IDENTITY_DISPLAY:
                    LayerTextSource.TEMPLATE_DISPLAY,
                CompositionLayerKind.IDENTITY_USERNAME:
                    LayerTextSource.TEMPLATE_USERNAME,
                CompositionLayerKind.WORDMARK:
                    LayerTextSource.TEMPLATE_WORDMARK,
                CompositionLayerKind.SITUATION:
                    LayerTextSource.PLAY_CALL_SITUATION,
                CompositionLayerKind.PLAY_CALL:
                    LayerTextSource.PLAY_CALL_CONCEPT,
                CompositionLayerKind.RESULT:
                    LayerTextSource.PLAY_CALL_RESULT,
            })
            expected_assets: dict[
                CompositionLayerKind, ImmutableAssetReference | None
            ] = {kind: None for kind in CompositionLayerKind}
            if self.template_identity is not None:
                identity = self.template_identity
                expected_assets[CompositionLayerKind.PROFILE_PHOTO] = (
                    identity.profile_photo
                )
                if identity.wordmark_logo is not None:
                    expected_assets[CompositionLayerKind.WORDMARK] = (
                        identity.wordmark_logo
                    )
                    expected_inputs[CompositionLayerKind.WORDMARK] = (
                        "template.wordmark_logo"
                    )
                    expected_fit[CompositionLayerKind.WORDMARK] = (
                        LayerFitPolicy.CONTAIN
                    )
                    expected_text[CompositionLayerKind.WORDMARK] = (
                        LayerTextSource.NONE
                    )
            for layer in self.layers:
                if not isinstance(layer, CompositionLayer):
                    continue
                if layer.rect != expected_rects[layer.kind]:
                    errors.append(
                        f"Composition layer moved outside locked geometry: "
                        f"{layer.kind.value}."
                    )
                if layer.input_name != expected_inputs[layer.kind]:
                    errors.append(
                        f"Composition layer uses an unexpected input: "
                        f"{layer.kind.value}."
                    )
                if layer.fit_policy is not expected_fit[layer.kind]:
                    errors.append(
                        f"Composition layer uses an unexpected fit policy: "
                        f"{layer.kind.value}."
                    )
                if layer.mask_policy is not expected_mask[layer.kind]:
                    errors.append(
                        f"Composition layer uses an unexpected mask policy: "
                        f"{layer.kind.value}."
                    )
                if layer.text_source is not expected_text[layer.kind]:
                    errors.append(
                        f"Composition layer uses an unexpected text source: "
                        f"{layer.kind.value}."
                    )
                if layer.asset != expected_assets[layer.kind]:
                    errors.append(
                        f"Composition layer asset differs from locked semantics: "
                        f"{layer.kind.value}."
                    )
            if self.template_identity is not None:
                identity = self.template_identity
                if identity.border is not None or CompositionLayerKind.BORDER in seen_kinds:
                    errors.append(
                        "Border rendering across linked 16:9 and 9:16 treatments "
                        "is undefined."
                    )
                presence_contract = {
                    CompositionLayerKind.PROFILE_PHOTO:
                        identity.profile_photo is not None,
                    CompositionLayerKind.IDENTITY_USERNAME:
                        bool(identity.username_text.strip()),
                    CompositionLayerKind.WORDMARK:
                        identity.wordmark_logo is not None
                        or bool(identity.wordmark_text.strip()),
                }
                for kind, expected_presence in presence_contract.items():
                    if (kind in seen_kinds) is not expected_presence:
                        errors.append(
                            f"Composition layer presence differs from locked identity: "
                            f"{kind.value}."
                        )
            ink = self.layer(CompositionLayerKind.INK)
            if ink is not None and ink.rect != self.source_film.content_rect:
                errors.append("Ink must be clipped to the visible source-film rectangle.")
            has_situation = CompositionLayerKind.SITUATION in seen_kinds
            has_play_call = CompositionLayerKind.PLAY_CALL in seen_kinds
            has_result = CompositionLayerKind.RESULT in seen_kinds
            has_voiceover = CompositionLayerKind.WAVEFORM in seen_kinds
            expected_event_input = (
                "presentation_event_track" if has_voiceover else ""
            )
            if self.presentation_event_input_name != expected_event_input:
                errors.append(
                    "Presentation event input does not match voiceover layer presence."
                )
            if has_situation is not has_play_call:
                errors.append(
                    "Situation and play-call layers must appear together."
                )
            if has_result and not (has_situation and has_play_call):
                errors.append("Result cannot appear without the play-call treatment.")
            if self.template_identity is not None and has_situation and has_play_call:
                if has_result is not self.template_identity.show_result:
                    errors.append(
                        "Result layer presence differs from locked template visibility."
                    )

            safe_kinds = {
                CompositionLayerKind.PROFILE_PHOTO,
                CompositionLayerKind.IDENTITY_DISPLAY,
                CompositionLayerKind.IDENTITY_USERNAME,
                CompositionLayerKind.WORDMARK,
                CompositionLayerKind.SITUATION,
                CompositionLayerKind.PLAY_CALL,
                CompositionLayerKind.RESULT,
                CompositionLayerKind.WAVEFORM,
                CompositionLayerKind.PROGRESS,
            }
            safe_layers = [layer for layer in self.layers if layer.kind in safe_kinds]
            for layer in safe_layers:
                if not self.safe_area.contains(layer.rect):
                    errors.append(
                        f"Composition layer leaves the treatment safe area: "
                        f"{layer.kind.value}."
                    )
            for index, first in enumerate(safe_layers):
                for second in safe_layers[index + 1:]:
                    if first.rect.overlaps(second.rect):
                        errors.append(
                            "Locked information regions overlap: "
                            f"{first.kind.value} and {second.kind.value}."
                        )
        return tuple(errors)

    def to_json(self) -> str:
        return json.dumps(
            _plan_to_dict(self),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, payload: str | bytes | bytearray) -> "CompositionPlan":
        if not isinstance(payload, (str, bytes, bytearray)):
            raise CompositionPlanValidationError(
                "Composition plan JSON must be text or bytes."
            )
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CompositionPlanValidationError(
                "Composition plan JSON is invalid."
            ) from exc
        try:
            return _plan_from_dict(decoded)
        except CompositionPlanValidationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise CompositionPlanValidationError(
                "Composition plan contains an invalid field value."
            ) from exc


def _contain_rect(source: PixelSize, slot: PixelRect) -> PixelRect:
    if source.width * slot.height >= source.height * slot.width:
        width = slot.width
        height = max(1, (source.height * slot.width) // source.width)
    else:
        height = slot.height
        width = max(1, (source.width * slot.height) // source.height)
    return PixelRect(
        slot.x + ((slot.width - width) // 2),
        slot.y + ((slot.height - height) // 2),
        width,
        height,
    )


def _progress_rect(contract: _LayoutContract, canvas: PixelSize) -> PixelRect:
    normalized = contract.progress.resolve(canvas)
    return PixelRect(
        normalized.x,
        normalized.y,
        normalized.width,
        _PROGRESS_THICKNESS_PX,
    )


def _input_names(package: ExportPackageSnapshot) -> frozenset[str]:
    return frozenset(
        compositor_input.name
        for compositor_input in package.compositor_inputs
    )


def build_composition_plan(
    package: ExportPackageSnapshot,
    source_size: PixelSize,
    *,
    template_identity: LockedTemplateIdentity | None = None,
) -> CompositionPlan:
    """Resolve one package into the plan used by both preview and final render."""

    if not isinstance(package, ExportPackageSnapshot):
        raise CompositionPlanValidationError(
            "An immutable export package snapshot is required."
        )
    package.validate()
    if not isinstance(source_size, PixelSize):
        raise CompositionPlanValidationError("Source size must be a PixelSize.")

    if package.style is ExportStyle.CLEAN:
        if template_identity is not None:
            raise CompositionPlanValidationError(
                "Clean source-only output cannot receive template identity."
            )
        full = PixelRect(0, 0, source_size.width, source_size.height)
        placement = SourceFilmPlacement(
            source_size=source_size,
            slot_rect=full,
            content_rect=full,
            source_crop_rect=full,
            fit_policy=SourceFitPolicy.SOURCE_PASSTHROUGH,
        )
        return CompositionPlan(
            style=package.style,
            canvas=source_size,
            safe_area=full,
            source_film=placement,
            layers=(
                CompositionLayer(
                    CompositionLayerKind.FILM, full, "source_video"
                ),
            ),
            presentation_event_input_name="",
            encoder_transform_policy=EncoderTransformPolicy.TECHNICAL_PRESET,
        )

    if package.template is None or template_identity is None:
        raise CompositionPlanValidationError(
            "Composited output requires one locked template identity."
        )
    if not template_identity.matches(package.template):
        raise CompositionPlanValidationError(
            "Locked identity does not match the export package template snapshot."
        )
    if template_identity.border is not None:
        raise CompositionPlanValidationError(
            "Border rendering across linked 16:9 and 9:16 treatments is "
            "undefined; choose an aspect strategy before using this asset."
        )

    names = _input_names(package)
    required_inputs = {"source_video"}
    if package.include_ink:
        required_inputs.add("ink_event_track")
    if package.include_voiceover:
        required_inputs.update({
            "voiceover_audio",
            "presentation_event_track",
        })
    if package.include_play_call:
        required_inputs.update({"play_call_situation", "play_call_concept"})
        if template_identity.show_result:
            required_inputs.add("play_call_result")
    missing_inputs = sorted(required_inputs - names)
    unexpected_inputs = sorted(names - required_inputs)
    input_errors: list[str] = []
    if missing_inputs:
        input_errors.append(
            "Composition inputs are missing: " + ", ".join(missing_inputs) + "."
        )
    if unexpected_inputs:
        input_errors.append(
            "Composition inputs do not match the locked template: "
            + ", ".join(unexpected_inputs)
            + "."
        )
    if input_errors:
        raise CompositionPlanValidationError(input_errors)

    contract = (
        _SIGNATURE_LAYOUT
        if package.style is ExportStyle.SIGNATURE
        else _VERTICAL_LAYOUT
    )
    canvas = contract.canvas
    safe_area = contract.safe_area.resolve(canvas)
    film_slot = contract.film_slot.resolve(canvas)
    content_rect = _contain_rect(source_size, film_slot)
    placement = SourceFilmPlacement(
        source_size=source_size,
        slot_rect=film_slot,
        content_rect=content_rect,
        source_crop_rect=PixelRect(0, 0, source_size.width, source_size.height),
        fit_policy=SourceFitPolicy.CONTAIN_LETTERBOX,
    )

    layers = [
        CompositionLayer(
            CompositionLayerKind.BACKGROUND,
            PixelRect(0, 0, canvas.width, canvas.height),
        ),
        CompositionLayer(
            CompositionLayerKind.FILM,
            content_rect,
            "source_video",
        ),
    ]
    if package.include_ink:
        layers.append(CompositionLayer(
            CompositionLayerKind.INK,
            content_rect,
            "ink_event_track",
        ))
    if template_identity.wordmark_logo is not None:
        layers.append(CompositionLayer(
            CompositionLayerKind.WORDMARK,
            contract.wordmark.resolve(canvas),
            "template.wordmark_logo",
            template_identity.wordmark_logo,
            fit_policy=LayerFitPolicy.CONTAIN,
        ))
    elif template_identity.wordmark_text.strip():
        layers.append(CompositionLayer(
            CompositionLayerKind.WORDMARK,
            contract.wordmark.resolve(canvas),
            "template.wordmark_text",
            text_source=LayerTextSource.TEMPLATE_WORDMARK,
        ))
    if template_identity.profile_photo is not None:
        layers.append(CompositionLayer(
            CompositionLayerKind.PROFILE_PHOTO,
            contract.profile_photo.resolve(canvas),
            "template.profile_photo",
            template_identity.profile_photo,
            fit_policy=LayerFitPolicy.COVER,
            mask_policy=LayerMaskPolicy.CIRCLE,
        ))
    layers.append(CompositionLayer(
        CompositionLayerKind.IDENTITY_DISPLAY,
        contract.identity_display.resolve(canvas),
        "template.display_text",
        text_source=LayerTextSource.TEMPLATE_DISPLAY,
    ))
    if template_identity.username_text.strip():
        layers.append(CompositionLayer(
            CompositionLayerKind.IDENTITY_USERNAME,
            contract.identity_username.resolve(canvas),
            "template.username_text",
            text_source=LayerTextSource.TEMPLATE_USERNAME,
        ))
    if package.include_play_call:
        layers.extend((
            CompositionLayer(
                CompositionLayerKind.SITUATION,
                contract.situation.resolve(canvas),
                "play_call_situation",
                text_source=LayerTextSource.PLAY_CALL_SITUATION,
            ),
            CompositionLayer(
                CompositionLayerKind.PLAY_CALL,
                contract.play_call.resolve(canvas),
                "play_call_concept",
                text_source=LayerTextSource.PLAY_CALL_CONCEPT,
            ),
        ))
        if template_identity.show_result:
            layers.append(CompositionLayer(
                CompositionLayerKind.RESULT,
                contract.result.resolve(canvas),
                "play_call_result",
                text_source=LayerTextSource.PLAY_CALL_RESULT,
            ))
    if package.include_voiceover:
        layers.append(CompositionLayer(
            CompositionLayerKind.WAVEFORM,
            contract.waveform.resolve(canvas),
            "voiceover_audio",
        ))
    layers.append(CompositionLayer(
        CompositionLayerKind.PROGRESS,
        _progress_rect(contract, canvas),
        "timeline_progress",
    ))

    return CompositionPlan(
        style=package.style,
        canvas=canvas,
        safe_area=safe_area,
        source_film=placement,
        layers=tuple(layers),
        presentation_event_input_name=(
            "presentation_event_track" if package.include_voiceover else ""
        ),
        encoder_transform_policy=EncoderTransformPolicy.PRESERVE_COMPOSITOR_CANVAS,
        template_identity=template_identity,
    )


def _size_to_dict(size: PixelSize) -> dict[str, int]:
    return {"height": size.height, "width": size.width}


def _rect_to_dict(rect: PixelRect) -> dict[str, int]:
    return {
        "height": rect.height,
        "width": rect.width,
        "x": rect.x,
        "y": rect.y,
    }


def _asset_to_dict(asset: ImmutableAssetReference | None) -> dict[str, object] | None:
    if asset is None:
        return None
    return {
        "height": asset.height,
        "mime_type": asset.mime_type,
        "role": asset.role.value,
        "sha256": asset.sha256,
        "width": asset.width,
    }


def _identity_to_dict(identity: LockedTemplateIdentity) -> dict[str, object]:
    return {
        "accent_color": identity.accent_color,
        "border": _asset_to_dict(identity.border),
        "display_text": identity.display_text,
        "profile_photo": _asset_to_dict(identity.profile_photo),
        "integrity_sha256": identity.integrity_sha256,
        "revision": identity.revision,
        "show_result": identity.show_result,
        "snapshot_sha256": identity.snapshot_sha256,
        "template_id": identity.template_id,
        "username_text": identity.username_text,
        "wordmark_logo": _asset_to_dict(identity.wordmark_logo),
        "wordmark_text": identity.wordmark_text,
    }


def _plan_to_dict(plan: CompositionPlan) -> dict[str, object]:
    film = plan.source_film
    return {
        "plan": {
            "canvas": _size_to_dict(plan.canvas),
            "encoder_transform_policy": plan.encoder_transform_policy.value,
            "layers": [
                {
                    "asset": _asset_to_dict(layer.asset),
                    "fit_policy": layer.fit_policy.value,
                    "input_name": layer.input_name,
                    "kind": layer.kind.value,
                    "mask_policy": layer.mask_policy.value,
                    "rect": _rect_to_dict(layer.rect),
                    "text_source": layer.text_source.value,
                }
                for layer in plan.layers
            ],
            "presentation_event_input_name": plan.presentation_event_input_name,
            "safe_area": _rect_to_dict(plan.safe_area),
            "source_film": {
                "content_rect": _rect_to_dict(film.content_rect),
                "fit_policy": film.fit_policy.value,
                "letterbox_color": film.letterbox_color,
                "slot_rect": _rect_to_dict(film.slot_rect),
                "source_crop_rect": _rect_to_dict(film.source_crop_rect),
                "source_size": _size_to_dict(film.source_size),
            },
            "style": plan.style.value,
            "template_identity": (
                _identity_to_dict(plan.template_identity)
                if plan.template_identity is not None
                else None
            ),
        },
        "schema": COMPOSITION_PLAN_SCHEMA,
        "version": COMPOSITION_PLAN_VERSION,
    }


def _expect_mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CompositionPlanValidationError(f"{name} must be an object.")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise CompositionPlanValidationError(f"{name} has invalid fields.")


def _size_from_dict(value: object, name: str) -> PixelSize:
    data = _expect_mapping(value, name)
    _exact_keys(data, {"height", "width"}, name)
    return PixelSize(data["width"], data["height"])


def _rect_from_dict(value: object, name: str) -> PixelRect:
    data = _expect_mapping(value, name)
    _exact_keys(data, {"height", "width", "x", "y"}, name)
    return PixelRect(data["x"], data["y"], data["width"], data["height"])


def _asset_from_dict(
    value: object,
    name: str,
) -> ImmutableAssetReference | None:
    if value is None:
        return None
    data = _expect_mapping(value, name)
    _exact_keys(data, {"height", "mime_type", "role", "sha256", "width"}, name)
    return ImmutableAssetReference(
        role=data["role"],
        sha256=data["sha256"],
        mime_type=data["mime_type"],
        width=data["width"],
        height=data["height"],
    )


def _identity_from_dict(value: object) -> LockedTemplateIdentity:
    data = _expect_mapping(value, "template_identity")
    _exact_keys(data, {
        "accent_color",
        "border",
        "display_text",
        "integrity_sha256",
        "profile_photo",
        "revision",
        "show_result",
        "snapshot_sha256",
        "template_id",
        "username_text",
        "wordmark_logo",
        "wordmark_text",
    }, "template_identity")
    return LockedTemplateIdentity(
        template_id=data["template_id"],
        revision=data["revision"],
        snapshot_sha256=data["snapshot_sha256"],
        integrity_sha256=data["integrity_sha256"],
        display_text=data["display_text"],
        username_text=data["username_text"],
        wordmark_text=data["wordmark_text"],
        accent_color=data["accent_color"],
        show_result=data["show_result"],
        border=_asset_from_dict(data["border"], "template_identity.border"),
        profile_photo=_asset_from_dict(
            data["profile_photo"], "template_identity.profile_photo"
        ),
        wordmark_logo=_asset_from_dict(
            data["wordmark_logo"], "template_identity.wordmark_logo"
        ),
    )


def _plan_from_dict(value: object) -> CompositionPlan:
    root = _expect_mapping(value, "composition plan document")
    _exact_keys(root, {"plan", "schema", "version"}, "composition plan document")
    if root["schema"] != COMPOSITION_PLAN_SCHEMA:
        raise CompositionPlanValidationError("Unknown composition plan schema.")
    if type(root["version"]) is not int or root["version"] != COMPOSITION_PLAN_VERSION:
        raise CompositionPlanValidationError(
            f"Unsupported composition plan version: {root['version']!r}."
        )
    data = _expect_mapping(root["plan"], "plan")
    _exact_keys(data, {
        "canvas",
        "encoder_transform_policy",
        "layers",
        "presentation_event_input_name",
        "safe_area",
        "source_film",
        "style",
        "template_identity",
    }, "plan")
    film_data = _expect_mapping(data["source_film"], "source_film")
    _exact_keys(film_data, {
        "content_rect",
        "fit_policy",
        "letterbox_color",
        "slot_rect",
        "source_crop_rect",
        "source_size",
    }, "source_film")
    raw_layers = data["layers"]
    if not isinstance(raw_layers, list):
        raise CompositionPlanValidationError("Plan layers must be an array.")
    layers: list[CompositionLayer] = []
    for index, raw_layer in enumerate(raw_layers):
        layer_data = _expect_mapping(raw_layer, f"layers[{index}]")
        _exact_keys(
            layer_data,
            {
                "asset",
                "fit_policy",
                "input_name",
                "kind",
                "mask_policy",
                "rect",
                "text_source",
            },
            f"layers[{index}]",
        )
        layers.append(CompositionLayer(
            kind=layer_data["kind"],
            rect=_rect_from_dict(layer_data["rect"], f"layers[{index}].rect"),
            input_name=layer_data["input_name"],
            asset=_asset_from_dict(layer_data["asset"], f"layers[{index}].asset"),
            fit_policy=layer_data["fit_policy"],
            mask_policy=layer_data["mask_policy"],
            text_source=layer_data["text_source"],
        ))
    identity_data = data["template_identity"]
    return CompositionPlan(
        style=data["style"],
        canvas=_size_from_dict(data["canvas"], "canvas"),
        safe_area=_rect_from_dict(data["safe_area"], "safe_area"),
        source_film=SourceFilmPlacement(
            source_size=_size_from_dict(film_data["source_size"], "source_size"),
            slot_rect=_rect_from_dict(film_data["slot_rect"], "slot_rect"),
            content_rect=_rect_from_dict(
                film_data["content_rect"], "content_rect"
            ),
            source_crop_rect=_rect_from_dict(
                film_data["source_crop_rect"], "source_crop_rect"
            ),
            fit_policy=film_data["fit_policy"],
            letterbox_color=film_data["letterbox_color"],
        ),
        layers=tuple(layers),
        presentation_event_input_name=data["presentation_event_input_name"],
        encoder_transform_policy=data["encoder_transform_policy"],
        template_identity=(
            _identity_from_dict(identity_data)
            if identity_data is not None
            else None
        ),
    )

