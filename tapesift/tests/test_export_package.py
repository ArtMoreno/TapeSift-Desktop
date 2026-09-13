"""Pure domain contracts for Export Package presentation snapshots."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tapesift.models.export_package import (
    CompositorInput,
    ExportPackageSnapshot,
    ExportPackageValidationError,
    ExportStyle,
    ExportTemplateSnapshot,
)
from tapesift.models.export_settings import FAST_COPY, SOCIAL_1080P, VERTICAL_9_16


def _template() -> ExportTemplateSnapshot:
    return ExportTemplateSnapshot(
        template_id="analyst-green",
        revision=3,
        payload_json='{"name": "Analyst Green"}',
    )


def _inputs() -> tuple[CompositorInput, ...]:
    return (CompositorInput("source_video", "D:/film/game.mp4"),)


def test_presentation_styles_are_not_technical_preset_names():
    assert {style.value for style in ExportStyle} == {
        "clean", "signature", "vertical"}
    assert ExportStyle.VERTICAL.value != "vertical_9_16"

    package = ExportPackageSnapshot(
        style=ExportStyle.CLEAN,
        technical_preset=SOCIAL_1080P.name,
    )

    assert package.style is ExportStyle.CLEAN
    assert package.technical_preset == SOCIAL_1080P.name
    assert package.validate() is package


def test_snapshots_are_frozen_and_compositor_inputs_are_copied_to_a_tuple():
    source_inputs = [CompositorInput("source_video", "D:/film/game.mp4")]
    package = ExportPackageSnapshot(
        style="signature",
        technical_preset=SOCIAL_1080P.name,
        template=_template(),
        compositor_inputs=source_inputs,
    )
    source_inputs.append(CompositorInput("border", "D:/brand/border.png"))

    assert package.style is ExportStyle.SIGNATURE
    assert package.compositor_inputs == (_inputs()[0],)
    with pytest.raises(FrozenInstanceError):
        package.technical_preset = FAST_COPY.name
    with pytest.raises(FrozenInstanceError):
        package.template.revision = 4


def test_fast_copy_is_valid_for_clean_only():
    assert ExportPackageSnapshot(
        style=ExportStyle.CLEAN,
        technical_preset=FAST_COPY.name,
    ).validation_errors() == ()

    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=FAST_COPY.name,
        template=_template(),
        compositor_inputs=_inputs(),
    )

    with pytest.raises(ExportPackageValidationError) as caught:
        package.validate()
    assert "Fast Copy is available only for Clean exports" in str(caught.value)


@pytest.mark.parametrize("style", [ExportStyle.SIGNATURE, ExportStyle.VERTICAL])
def test_composited_styles_require_template_and_compositor_inputs(style):
    package = ExportPackageSnapshot(
        style=style,
        technical_preset=SOCIAL_1080P.name,
    )

    errors = package.validation_errors()

    assert any("require a template snapshot" in error for error in errors)
    assert any("require compositor inputs" in error for error in errors)


@pytest.mark.parametrize("style", [ExportStyle.SIGNATURE, ExportStyle.VERTICAL])
def test_composited_styles_accept_valid_immutable_inputs(style):
    package = ExportPackageSnapshot(
        style=style,
        technical_preset=SOCIAL_1080P.name,
        template=_template(),
        compositor_inputs=(
            CompositorInput("source_video", "D:/film/game.mp4"),
            CompositorInput("ink_event_track", "ink-track-1"),
            CompositorInput("play_call_situation", "1st & 10"),
            CompositorInput("play_call_concept", "Duo"),
        ),
        include_ink=True,
        include_play_call=True,
    )

    assert package.is_composited is True
    assert package.validate() is package


def test_clean_rejects_stale_template_compositor_and_layer_state():
    package = ExportPackageSnapshot(
        style=ExportStyle.CLEAN,
        template=_template(),
        compositor_inputs=_inputs(),
        include_voiceover=True,
    )

    errors = package.validation_errors()

    assert "Clean exports cannot include a template." in errors
    assert "Clean exports cannot include compositor inputs." in errors
    assert "Clean exports cannot include presentation layers." in errors


@pytest.mark.parametrize("payload", ["not json", "[]", "null"])
def test_template_snapshot_must_be_a_json_object(payload):
    package = ExportPackageSnapshot(
        style=ExportStyle.VERTICAL,
        technical_preset=SOCIAL_1080P.name,
        template=ExportTemplateSnapshot("vertical", 1, payload),
        compositor_inputs=_inputs(),
    )

    with pytest.raises(ExportPackageValidationError):
        package.validate()


@pytest.mark.parametrize(
    "template_id",
    [" template ", "bad/id", "x" * 65, 42],
)
def test_template_snapshot_id_uses_the_same_canonical_contract_as_templates(
    template_id,
):
    snapshot = ExportTemplateSnapshot(template_id, 1, "{}")

    assert snapshot.validation_errors()


def test_unknown_technical_preset_is_rejected_instead_of_falling_back():
    package = ExportPackageSnapshot(
        style=ExportStyle.CLEAN,
        technical_preset="looks-real-but-is-not-registered",
    )

    assert package.validation_errors() == (
        "Unknown technical export preset: looks-real-but-is-not-registered.",
    )

    padded = ExportPackageSnapshot(
        style=ExportStyle.CLEAN,
        technical_preset=" source_quality ",
    )
    assert padded.validation_errors() == (
        "Technical export preset name must not contain surrounding whitespace.",
    )


def test_invalid_compositor_input_reports_both_missing_fields():
    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=SOCIAL_1080P.name,
        template=_template(),
        compositor_inputs=(CompositorInput("", ""),),
    )

    errors = package.validation_errors()

    assert "Compositor input name is required." in errors
    assert "Compositor input <unnamed> requires a value." in errors


def test_compositor_input_value_rejects_invisible_surrounding_whitespace():
    errors = CompositorInput("source_video", " D:/film/game.mp4 ").validation_errors()

    assert errors == (
        "Compositor input source_video value must not contain surrounding "
        "whitespace.",
    )


def test_compositor_input_names_are_unique_after_trim_and_casefold():
    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=SOCIAL_1080P.name,
        template=_template(),
        compositor_inputs=(
            CompositorInput("source_video", "D:/film/game.mp4"),
            CompositorInput(" Source_Video ", "D:/film/other.mp4"),
        ),
    )

    assert any(
        "duplicate name: Source_Video" in error
        for error in package.validation_errors()
    )
    assert any(
        "canonical without surrounding whitespace" in error
        for error in package.validation_errors()
    )


@pytest.mark.parametrize("style", [ExportStyle.SIGNATURE, ExportStyle.VERTICAL])
def test_legacy_vertical_encoder_transform_is_rejected_for_composited_frames(style):
    package = ExportPackageSnapshot(
        style=style,
        technical_preset=VERTICAL_9_16.name,
        template=_template(),
        compositor_inputs=_inputs(),
    )

    assert any(
        "compositor already owns the output canvas" in error
        for error in package.validation_errors()
    )


def test_unknown_or_disabled_layer_inputs_are_rejected():
    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=SOCIAL_1080P.name,
        template=_template(),
        compositor_inputs=(
            CompositorInput("source_video", "D:/film/game.mp4"),
            CompositorInput("voiceover_audio", "take-4"),
            CompositorInput("presentation_event_track", "take-4-events"),
            CompositorInput("title", "invented title"),
        ),
    )

    errors = package.validation_errors()

    assert "Unknown compositor input name: title." in errors
    stale_error = next(
        error for error in errors if "do not match enabled layers" in error
    )
    assert "voiceover_audio" in stale_error
    assert "presentation_event_track" in stale_error


def test_enabled_layers_require_their_canonical_inputs():
    package = ExportPackageSnapshot(
        style=ExportStyle.VERTICAL,
        technical_preset=SOCIAL_1080P.name,
        template=_template(),
        compositor_inputs=_inputs(),
        include_ink=True,
        include_voiceover=True,
        include_play_call=True,
    )

    error = "; ".join(package.validation_errors())

    assert "ink_event_track" in error
    assert "voiceover_audio" in error
    assert "presentation_event_track" in error
    assert "play_call_situation" in error
    assert "play_call_concept" in error


def test_voiceover_audio_and_audio_clock_event_track_are_atomic_inputs():
    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=SOCIAL_1080P.name,
        template=_template(),
        compositor_inputs=(
            CompositorInput("source_video", "D:/film/game.mp4"),
            CompositorInput("voiceover_audio", "take-4-audio"),
        ),
        include_voiceover=True,
    )

    assert any(
        "presentation_event_track" in error
        for error in package.validation_errors()
    )


@pytest.mark.parametrize(
    "style",
    [ExportStyle.CLEAN, ExportStyle.SIGNATURE, ExportStyle.VERTICAL],
)
def test_undefined_slate_is_rejected_before_a_job_can_be_queued(style):
    package = ExportPackageSnapshot(
        style=style,
        technical_preset=SOCIAL_1080P.name,
        template=_template(),
        compositor_inputs=_inputs(),
        include_slate=True,
    )

    assert "Slate composition is undefined and cannot be queued." in (
        package.validation_errors()
    )


def test_snapshot_and_package_field_types_fail_validation_without_raw_type_errors():
    snapshot = ExportTemplateSnapshot(
        template_id="template",
        revision=True,
        payload_json="{}",
    )
    assert snapshot.validation_errors() == (
        "Template revision must be at least 1.",
    )

    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=42,
        accurate_cut="yes",
        template=snapshot,
        compositor_inputs=_inputs(),
        include_ink="yes",
    )
    errors = package.validation_errors()
    assert "Technical export preset must be text." in errors
    assert "Accurate-cut setting must be true or false." in errors
    assert "include_ink setting must be true or false." in errors


def test_invalid_style_is_wrapped_as_package_validation_error():
    with pytest.raises(ExportPackageValidationError, match="Unknown export style"):
        ExportPackageSnapshot(style="invented")
