"""Immutable presentation settings for one export package.

Presentation style is deliberately separate from :class:`ExportPreset`.
``ExportPreset`` describes encoding (codec, quality, scale, and stream copy),
while the types here describe what is composed into the exported frame.
Keeping those axes independent prevents a style such as ``VERTICAL`` from
being mistaken for the legacy ``vertical_9_16`` encoder preset.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from tapesift.models.export_settings import (
    BUILTIN_PRESETS,
    FAST_COPY,
    SOURCE_QUALITY,
    VERTICAL_9_16,
)


class ExportStyle(str, Enum):
    """The visible composition applied to an export."""

    CLEAN = "clean"
    SIGNATURE = "signature"
    VERTICAL = "vertical"


EXPORT_PACKAGE_SCHEMA = "tapesift.export-package"
EXPORT_PACKAGE_VERSION = 1
CANONICAL_COMPOSITOR_INPUT_NAMES = frozenset({
    "source_video",
    "ink_event_track",
    "voiceover_audio",
    "presentation_event_track",
    "play_call_situation",
    "play_call_concept",
    "play_call_result",
})
_TEMPLATE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ExportPackageValidationError(ValueError):
    """Raised when a package cannot be rendered as described."""

    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


@dataclass(frozen=True)
class ExportTemplateSnapshot:
    """A stable template revision embedded into an export job.

    The payload remains JSON rather than a mutable mapping so changing the
    authoring model after a job is queued cannot change that job's output.
    The package boundary verifies that the snapshot is identifiable and
    structurally readable. The composition-plan lock then matches it against
    the current SignatureTemplate schema and embedded asset bytes.
    """

    template_id: str
    revision: int
    payload_json: str

    def validation_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not isinstance(self.template_id, str) or not self.template_id.strip():
            errors.append("Template id is required.")
        elif self.template_id != self.template_id.strip():
            errors.append(
                "Template id must be canonical without surrounding whitespace."
            )
        elif not _TEMPLATE_ID_PATTERN.fullmatch(self.template_id):
            errors.append(
                "Template id must be 1-64 letters, numbers, dots, dashes, "
                "or underscores."
            )
        if type(self.revision) is not int or self.revision < 1:
            errors.append("Template revision must be at least 1.")
        if not isinstance(self.payload_json, str):
            errors.append("Template snapshot must contain valid JSON.")
        else:
            try:
                payload = json.loads(self.payload_json)
            except json.JSONDecodeError:
                errors.append("Template snapshot must contain valid JSON.")
            else:
                if not isinstance(payload, dict):
                    errors.append("Template snapshot JSON must be an object.")
        return tuple(errors)


@dataclass(frozen=True)
class CompositorInput:
    """One immutable named input required by the composition plan."""

    name: str
    value: str

    def validation_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append("Compositor input name is required.")
        elif self.name != self.name.strip():
            errors.append(
                f"Compositor input name must be canonical without surrounding "
                f"whitespace: {self.name!r}."
            )
        elif self.name not in CANONICAL_COMPOSITOR_INPUT_NAMES:
            errors.append(f"Unknown compositor input name: {self.name}.")
        if not isinstance(self.value, str) or not self.value.strip():
            label = self.name.strip() if isinstance(self.name, str) else ""
            errors.append(
                f"Compositor input {label or '<unnamed>'} "
                "requires a value."
            )
        elif self.value != self.value.strip():
            errors.append(
                f"Compositor input {self.name} value must not contain "
                "surrounding whitespace."
            )
        return tuple(errors)


@dataclass(frozen=True)
class ExportPackageSnapshot:
    """The complete immutable presentation choice for one queued export.

    This is intentionally renderer-neutral. A later compositor can consume
    the named inputs, while the existing Clean export path can continue to
    consume only ``technical_preset`` and ``accurate_cut``.
    """

    style: ExportStyle
    technical_preset: str = SOURCE_QUALITY.name
    accurate_cut: bool = True
    template: ExportTemplateSnapshot | None = None
    compositor_inputs: tuple[CompositorInput, ...] = ()
    include_ink: bool = False
    include_voiceover: bool = False
    include_play_call: bool = False
    include_slate: bool = False

    def __post_init__(self) -> None:
        # Persistence code can pass enum values and JSON-decoded lists. Freeze
        # them at the boundary so callers cannot mutate a queued snapshot.
        try:
            resolved_style = ExportStyle(self.style)
        except (TypeError, ValueError) as exc:
            raise ExportPackageValidationError((
                f"Unknown export style: {self.style!r}.",
            )) from exc
        object.__setattr__(self, "style", resolved_style)
        try:
            frozen_inputs = tuple(self.compositor_inputs)
        except TypeError as exc:
            raise ExportPackageValidationError((
                "Compositor inputs must be an iterable of immutable snapshots.",
            )) from exc
        object.__setattr__(
            self, "compositor_inputs", frozen_inputs)

    @property
    def is_composited(self) -> bool:
        return self.style in {ExportStyle.SIGNATURE, ExportStyle.VERTICAL}

    def validation_errors(self) -> tuple[str, ...]:
        """Return every actionable incompatibility in one pass."""

        errors: list[str] = []
        if not isinstance(self.technical_preset, str):
            preset_name = ""
            errors.append("Technical export preset must be text.")
        else:
            preset_name = self.technical_preset.strip()
        if isinstance(self.technical_preset, str) and not preset_name:
            errors.append("Technical export preset is required.")
        elif (
            isinstance(self.technical_preset, str)
            and self.technical_preset != preset_name
        ):
            errors.append(
                "Technical export preset name must not contain surrounding "
                "whitespace."
            )
        elif preset_name and preset_name not in BUILTIN_PRESETS:
            errors.append(f"Unknown technical export preset: {preset_name}.")

        if type(self.accurate_cut) is not bool:
            errors.append("Accurate-cut setting must be true or false.")
        for field_name in (
            "include_ink",
            "include_voiceover",
            "include_play_call",
            "include_slate",
        ):
            if type(getattr(self, field_name)) is not bool:
                errors.append(
                    f"{field_name} setting must be true or false."
                )

        if self.include_slate is True:
            errors.append(
                "Slate composition is undefined and cannot be queued."
            )

        if self.style is ExportStyle.CLEAN:
            if self.template is not None:
                errors.append("Clean exports cannot include a template.")
            if self.compositor_inputs:
                errors.append("Clean exports cannot include compositor inputs.")
            if any((
                self.include_ink,
                self.include_voiceover,
                self.include_play_call,
                self.include_slate,
            )):
                errors.append("Clean exports cannot include presentation layers.")
        else:
            if preset_name == FAST_COPY.name:
                errors.append(
                    "Fast Copy is available only for Clean exports; "
                    "Signature and Vertical exports require re-encoding."
                )
            if preset_name == VERTICAL_9_16.name:
                errors.append(
                    "The legacy Vertical 9:16 encoder preset cannot be used "
                    "with composited frames; the compositor already owns the "
                    "output canvas."
                )
            if self.template is None:
                errors.append(
                    f"{self.style.value.title()} exports require a template snapshot."
                )
            elif not isinstance(self.template, ExportTemplateSnapshot):
                errors.append(
                    "Export template must be an immutable template snapshot."
                )
            else:
                errors.extend(self.template.validation_errors())
            if not self.compositor_inputs:
                errors.append(
                    f"{self.style.value.title()} exports require compositor inputs."
                )
            else:
                input_names: set[str] = set()
                normalized_names: set[str] = set()
                for compositor_input in self.compositor_inputs:
                    if not isinstance(compositor_input, CompositorInput):
                        errors.append(
                            "Compositor inputs must be CompositorInput snapshots."
                        )
                        continue
                    errors.extend(compositor_input.validation_errors())
                    if not isinstance(compositor_input.name, str):
                        continue
                    normalized_name = compositor_input.name.strip().casefold()
                    if normalized_name:
                        if normalized_name in normalized_names:
                            errors.append(
                                "Compositor input names must be unique; "
                                f"duplicate name: {compositor_input.name.strip()}."
                            )
                        else:
                            normalized_names.add(normalized_name)
                    if compositor_input.name in CANONICAL_COMPOSITOR_INPUT_NAMES:
                        input_names.add(compositor_input.name)

                required_names = {"source_video"}
                permitted_names = {"source_video"}
                if self.include_ink is True:
                    required_names.add("ink_event_track")
                    permitted_names.add("ink_event_track")
                if self.include_voiceover is True:
                    required_names.update({
                        "voiceover_audio",
                        "presentation_event_track",
                    })
                    permitted_names.update({
                        "voiceover_audio",
                        "presentation_event_track",
                    })
                if self.include_play_call is True:
                    required_names.update({
                        "play_call_situation",
                        "play_call_concept",
                    })
                    permitted_names.update({
                        "play_call_situation",
                        "play_call_concept",
                        # Result presence is resolved against the locked
                        # template's show_result setting by the plan builder.
                        "play_call_result",
                    })
                missing_names = sorted(required_names - input_names)
                if missing_names:
                    errors.append(
                        "Compositor inputs are missing: "
                        + ", ".join(missing_names)
                        + "."
                    )
                stale_names = sorted(input_names - permitted_names)
                if stale_names:
                    errors.append(
                        "Compositor inputs do not match enabled layers: "
                        + ", ".join(stale_names)
                        + "."
                    )

        return tuple(errors)

    def validate(self) -> "ExportPackageSnapshot":
        """Raise with all validation errors, otherwise return this snapshot."""

        errors = self.validation_errors()
        if errors:
            raise ExportPackageValidationError(errors)
        return self

    def to_json(self) -> str:
        """Return the canonical, immutable package document for a queue job."""

        self.validate()
        template: dict[str, object] | None = None
        if self.template is not None:
            template = {
                "payload": json.loads(self.template.payload_json),
                "revision": self.template.revision,
                "template_id": self.template.template_id,
            }
        payload = {
            "package": {
                "accurate_cut": self.accurate_cut,
                "compositor_inputs": [
                    {"name": item.name, "value": item.value}
                    for item in sorted(
                        self.compositor_inputs,
                        key=lambda item: (item.name.casefold(), item.name),
                    )
                ],
                "include_ink": self.include_ink,
                "include_play_call": self.include_play_call,
                "include_slate": self.include_slate,
                "include_voiceover": self.include_voiceover,
                "style": self.style.value,
                "technical_preset": self.technical_preset,
                "template": template,
            },
            "schema": EXPORT_PACKAGE_SCHEMA,
            "version": EXPORT_PACKAGE_VERSION,
        }
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(
            cls, payload: str | bytes | bytearray
    ) -> "ExportPackageSnapshot":
        """Read only the exact versioned package shape emitted by ``to_json``."""

        if not isinstance(payload, (str, bytes, bytearray)):
            raise ExportPackageValidationError((
                "Export package JSON must be text or bytes.",
            ))
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExportPackageValidationError((
                "Export package JSON is invalid.",
            )) from exc
        try:
            root = _expect_mapping(decoded, "Export package document")
            _require_exact_keys(
                root,
                {"package", "schema", "version"},
                "Export package document",
            )
            if root["schema"] != EXPORT_PACKAGE_SCHEMA:
                raise ExportPackageValidationError((
                    "Unknown export package schema.",
                ))
            if type(root["version"]) is not int \
                    or root["version"] != EXPORT_PACKAGE_VERSION:
                raise ExportPackageValidationError((
                    f"Unsupported export package version: {root['version']!r}.",
                ))
            data = _expect_mapping(root["package"], "Export package")
            _require_exact_keys(
                data,
                {
                    "accurate_cut",
                    "compositor_inputs",
                    "include_ink",
                    "include_play_call",
                    "include_slate",
                    "include_voiceover",
                    "style",
                    "technical_preset",
                    "template",
                },
                "Export package",
            )
            raw_inputs = data["compositor_inputs"]
            if not isinstance(raw_inputs, list):
                raise ExportPackageValidationError((
                    "Export package compositor inputs must be an array.",
                ))
            inputs: list[CompositorInput] = []
            for index, raw_input in enumerate(raw_inputs):
                item = _expect_mapping(
                    raw_input, f"Export package compositor input {index}")
                _require_exact_keys(
                    item,
                    {"name", "value"},
                    f"Export package compositor input {index}",
                )
                inputs.append(CompositorInput(item["name"], item["value"]))

            raw_template = data["template"]
            template = None
            if raw_template is not None:
                template_data = _expect_mapping(
                    raw_template, "Export package template")
                _require_exact_keys(
                    template_data,
                    {"payload", "revision", "template_id"},
                    "Export package template",
                )
                template = ExportTemplateSnapshot(
                    template_id=template_data["template_id"],
                    revision=template_data["revision"],
                    payload_json=json.dumps(
                        template_data["payload"],
                        ensure_ascii=False,
                        allow_nan=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            package = cls(
                style=data["style"],
                technical_preset=data["technical_preset"],
                accurate_cut=data["accurate_cut"],
                template=template,
                compositor_inputs=tuple(inputs),
                include_ink=data["include_ink"],
                include_voiceover=data["include_voiceover"],
                include_play_call=data["include_play_call"],
                include_slate=data["include_slate"],
            )
            return package.validate()
        except ExportPackageValidationError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise ExportPackageValidationError((
                "Export package contains an invalid field value.",
            )) from exc


def _expect_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExportPackageValidationError((f"{label} must be an object.",))
    return value


def _require_exact_keys(
        value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ExportPackageValidationError((f"{label} has invalid fields.",))
