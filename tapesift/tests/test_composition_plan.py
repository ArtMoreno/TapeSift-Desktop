"""Fixed geometry contracts shared by Signature preview and final render."""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QColor, QImage

from tapesift.models.composition_plan import (
    EncoderTransformPolicy,
    CompositionLayerKind,
    CompositionPlan,
    CompositionPlanValidationError,
    IdentityAssetRole,
    ImmutableAssetReference,
    LayerFitPolicy,
    LayerMaskPolicy,
    LayerTextSource,
    LockedTemplateIdentity,
    PixelRect,
    PixelSize,
    SourceFitPolicy,
    build_composition_plan,
)
from tapesift.models.export_package import (
    CompositorInput,
    ExportPackageSnapshot,
    ExportPackageValidationError,
    ExportStyle,
    ExportTemplateSnapshot,
)
from tapesift.models.export_settings import (
    SOCIAL_1080P,
    SOURCE_QUALITY,
)
from tapesift.models.signature_template import (
    ImageAssetSnapshot,
    SignatureIdentity,
    SignatureTemplate,
)


def _locked_identity(
    *,
    show_result: bool = True,
    include_border: bool = False,
    include_profile: bool = True,
    include_wordmark_logo: bool = True,
    wordmark_text: str = "TAPESIFT",
    username_text: str = "@cane_films",
) -> tuple[
    ExportTemplateSnapshot,
    LockedTemplateIdentity,
]:
    identity = SignatureIdentity(
        display_text="Cane Films",
        show_result=show_result,
        wordmark_text=wordmark_text,
        username_text=username_text,
        border=(
            ImageAssetSnapshot.capture(_valid_png("#111111"))
            if include_border
            else None
        ),
        profile_photo=(
            ImageAssetSnapshot.capture(_valid_png("#884422"))
            if include_profile
            else None
        ),
        wordmark_logo=(
            ImageAssetSnapshot.capture(_valid_png("#39E07A"))
            if include_wordmark_logo
            else None
        ),
    )
    template = SignatureTemplate(
        template_id="cane-films",
        name="Cane Films",
        identity=identity,
    )
    snapshot = ExportTemplateSnapshot(
        template_id="cane-films",
        revision=4,
        payload_json=template.to_json(),
    )
    return snapshot, LockedTemplateIdentity.capture(snapshot, template)


def _inputs(
    *,
    ink: bool = True,
    voiceover: bool = True,
    play_call: bool = True,
    show_result: bool = True,
) -> tuple[CompositorInput, ...]:
    values = [CompositorInput("source_video", "D:/film/game.mp4")]
    if ink:
        values.append(CompositorInput("ink_event_track", "ink-track-17"))
    if voiceover:
        values.append(CompositorInput("voiceover_audio", "voiceover-take-3"))
        values.append(CompositorInput(
            "presentation_event_track", "voiceover-take-3-events"
        ))
    if play_call:
        values.extend((
            CompositorInput("play_call_situation", "1st & 10 / own 34"),
            CompositorInput("play_call_concept", "Duo / Backside Cut"),
        ))
        if show_result:
            values.append(CompositorInput("play_call_result", "+7"))
    return tuple(values)


def _package(
    style: ExportStyle,
    *,
    snapshot: ExportTemplateSnapshot,
    ink: bool = True,
    voiceover: bool = True,
    play_call: bool = True,
    show_result: bool = True,
    slate: bool = False,
) -> ExportPackageSnapshot:
    return ExportPackageSnapshot(
        style=style,
        technical_preset=SOCIAL_1080P.name,
        template=snapshot,
        compositor_inputs=_inputs(
            ink=ink,
            voiceover=voiceover,
            play_call=play_call,
            show_result=show_result,
        ),
        include_ink=ink,
        include_voiceover=voiceover,
        include_play_call=play_call,
        include_slate=slate,
    )


def _plan(style: ExportStyle, *, show_result: bool = True) -> CompositionPlan:
    snapshot, identity = _locked_identity(show_result=show_result)
    return build_composition_plan(
        _package(style, snapshot=snapshot, show_result=show_result),
        PixelSize(1920, 1080),
        template_identity=identity,
    )


def _kinds(plan: CompositionPlan) -> tuple[CompositionLayerKind, ...]:
    return tuple(layer.kind for layer in plan.layers)


def _valid_png(color: str) -> bytes:
    image = QImage(12, 12, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    payload = QByteArray()
    buffer = QBuffer(payload)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return bytes(payload)


def test_clean_plan_is_source_only_and_retains_legacy_technical_preset_axis():
    package = ExportPackageSnapshot(
        style=ExportStyle.CLEAN,
        technical_preset=SOURCE_QUALITY.name,
    )

    plan = build_composition_plan(package, PixelSize(2560, 1440))

    assert plan.canvas == PixelSize(2560, 1440)
    assert plan.safe_area == PixelRect(0, 0, 2560, 1440)
    assert _kinds(plan) == (CompositionLayerKind.FILM,)
    assert plan.template_identity is None
    assert plan.source_film.fit_policy is SourceFitPolicy.SOURCE_PASSTHROUGH
    assert plan.source_film.source_crop_rect == PixelRect(0, 0, 2560, 1440)
    assert plan.encoder_transform_policy is EncoderTransformPolicy.TECHNICAL_PRESET


def test_one_locked_identity_drives_both_fixed_output_treatments():
    snapshot, identity = _locked_identity()
    source = PixelSize(1920, 1080)

    signature = build_composition_plan(
        _package(ExportStyle.SIGNATURE, snapshot=snapshot),
        source,
        template_identity=identity,
    )
    vertical = build_composition_plan(
        _package(ExportStyle.VERTICAL, snapshot=snapshot),
        source,
        template_identity=identity,
    )

    assert signature.template_identity is identity
    assert vertical.template_identity is identity
    assert signature.canvas == PixelSize(1920, 1080)
    assert vertical.canvas == PixelSize(1080, 1920)
    assert signature.source_film.fit_policy is SourceFitPolicy.CONTAIN_LETTERBOX
    assert vertical.source_film.fit_policy is SourceFitPolicy.CONTAIN_LETTERBOX
    assert signature.source_film.source_crop_rect == PixelRect(0, 0, 1920, 1080)
    assert vertical.source_film.source_crop_rect == PixelRect(0, 0, 1920, 1080)
    assert signature.encoder_transform_policy is (
        EncoderTransformPolicy.PRESERVE_COMPOSITOR_CANVAS
    )
    assert vertical.encoder_transform_policy is (
        EncoderTransformPolicy.PRESERVE_COMPOSITOR_CANVAS
    )


def test_signature_hierarchy_is_top_rail_film_then_nonoverlapping_lower_band():
    plan = _plan(ExportStyle.SIGNATURE)
    film = plan.source_film.slot_rect
    wordmark = plan.layer(CompositionLayerKind.WORDMARK).rect
    profile = plan.layer(CompositionLayerKind.PROFILE_PHOTO).rect
    display = plan.layer(CompositionLayerKind.IDENTITY_DISPLAY).rect
    username = plan.layer(CompositionLayerKind.IDENTITY_USERNAME).rect
    situation = plan.layer(CompositionLayerKind.SITUATION).rect
    play_call = plan.layer(CompositionLayerKind.PLAY_CALL).rect
    result = plan.layer(CompositionLayerKind.RESULT).rect
    waveform = plan.layer(CompositionLayerKind.WAVEFORM).rect

    assert wordmark.bottom < film.y
    assert film.bottom < min(profile.y, situation.y, play_call.y, result.y)
    assert waveform.y >= max(display.y, play_call.bottom, result.bottom)
    assert not profile.overlaps(waveform)
    assert not display.overlaps(waveform)
    assert not username.overlaps(waveform)


def test_vertical_hierarchy_keeps_call_above_safe_middle_film_and_identity_below():
    plan = _plan(ExportStyle.VERTICAL)
    film = plan.source_film.slot_rect
    situation = plan.layer(CompositionLayerKind.SITUATION).rect
    play_call = plan.layer(CompositionLayerKind.PLAY_CALL).rect
    result = plan.layer(CompositionLayerKind.RESULT).rect
    profile = plan.layer(CompositionLayerKind.PROFILE_PHOTO).rect
    display = plan.layer(CompositionLayerKind.IDENTITY_DISPLAY).rect
    username = plan.layer(CompositionLayerKind.IDENTITY_USERNAME).rect
    waveform = plan.layer(CompositionLayerKind.WAVEFORM).rect

    assert max(situation.bottom, play_call.bottom, result.bottom) < film.y
    assert film.bottom < min(profile.y, waveform.y)
    assert film.bottom < min(display.y, username.y)
    assert waveform.width > play_call.width
    assert film.x >= 0 and film.right <= plan.canvas.width


@pytest.mark.parametrize("style", [ExportStyle.SIGNATURE, ExportStyle.VERTICAL])
def test_fixed_graphic_regions_stay_in_safe_area_without_overlap(style):
    plan = _plan(style)
    graphic_kinds = {
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
    graphics = [layer for layer in plan.layers if layer.kind in graphic_kinds]

    assert all(plan.safe_area.contains(layer.rect) for layer in graphics)
    for index, first in enumerate(graphics):
        for second in graphics[index + 1:]:
            assert not first.rect.overlaps(second.rect), (
                first.kind,
                second.kind,
            )


def test_locked_paint_order_keeps_ink_above_film_and_chrome_above_ink():
    plan = _plan(ExportStyle.SIGNATURE)

    assert _kinds(plan) == (
        CompositionLayerKind.BACKGROUND,
        CompositionLayerKind.FILM,
        CompositionLayerKind.INK,
        CompositionLayerKind.WORDMARK,
        CompositionLayerKind.PROFILE_PHOTO,
        CompositionLayerKind.IDENTITY_DISPLAY,
        CompositionLayerKind.IDENTITY_USERNAME,
        CompositionLayerKind.SITUATION,
        CompositionLayerKind.PLAY_CALL,
        CompositionLayerKind.RESULT,
        CompositionLayerKind.WAVEFORM,
        CompositionLayerKind.PROGRESS,
    )
    assert plan.layer(CompositionLayerKind.INK).rect == plan.source_film.content_rect


def test_identity_assets_are_content_addressed_references_not_paths_or_bytes():
    _, identity = _locked_identity()

    assert identity.display_text == "Cane Films"
    assert identity.username_text == "@cane_films"
    assert identity.wordmark_text == "TAPESIFT"
    assert identity.accent_color == "#39E07A"
    assert identity.show_result is True
    assert identity.border is None
    for asset in (identity.profile_photo, identity.wordmark_logo):
        assert asset is not None
        assert len(asset.sha256) == 64
        assert not hasattr(asset, "data")
        assert not hasattr(asset, "path")


def test_real_signature_template_and_valid_png_assets_drive_both_formats():
    profile = ImageAssetSnapshot.capture(_valid_png("#884422"))
    logo = ImageAssetSnapshot.capture(_valid_png("#39E07A"))
    template = SignatureTemplate(
        template_id="real-analyst",
        name="Real Analyst",
        identity=SignatureIdentity(
            display_text="Nick Marshall",
            username_text="@MMarshall_FB",
            wordmark_text="TapeSift",
            wordmark_logo=logo,
            profile_photo=profile,
            accent_color="#39E07A",
            show_result=True,
        ),
    )
    snapshot = ExportTemplateSnapshot(
        template_id=template.template_id,
        revision=7,
        payload_json=template.to_json(),
    )

    identity = LockedTemplateIdentity.capture(snapshot, template)

    assert identity.matches(snapshot)
    assert identity.profile_photo.sha256 == profile.sha256
    assert identity.wordmark_logo.sha256 == logo.sha256
    for style in (ExportStyle.SIGNATURE, ExportStyle.VERTICAL):
        plan = build_composition_plan(
            _package(style, snapshot=snapshot),
            PixelSize(1920, 1080),
            template_identity=identity,
        )
        assert plan.layer(CompositionLayerKind.PROFILE_PHOTO).asset.sha256 == (
            profile.sha256
        )
        assert plan.layer(CompositionLayerKind.WORDMARK).asset.sha256 == logo.sha256


def test_real_current_template_with_no_optional_identity_assets_does_not_invent_them():
    template = SignatureTemplate(
        template_id="minimal-analyst",
        name="Minimal Analyst",
        identity=SignatureIdentity(display_text="Analyst"),
    )
    snapshot = ExportTemplateSnapshot(
        template_id=template.template_id,
        revision=1,
        payload_json=template.to_json(),
    )
    identity = LockedTemplateIdentity.capture(snapshot, template)
    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=SOCIAL_1080P.name,
        template=snapshot,
        compositor_inputs=(CompositorInput("source_video", "source-1"),),
    )

    plan = build_composition_plan(
        package,
        PixelSize(1920, 1080),
        template_identity=identity,
    )

    assert plan.layer(CompositionLayerKind.PROFILE_PHOTO) is None
    assert plan.layer(CompositionLayerKind.IDENTITY_USERNAME) is None
    assert plan.layer(CompositionLayerKind.WORDMARK) is None
    assert plan.layer(CompositionLayerKind.IDENTITY_DISPLAY) is not None


def test_structural_template_cannot_decouple_identity_from_snapshot_json():
    template = SignatureTemplate(
        template_id="authoritative-template",
        name="Authoritative Template",
        identity=SignatureIdentity(
            display_text="Authoritative Analyst",
            wordmark_text="AUTH",
        ),
    )
    snapshot = ExportTemplateSnapshot(
        template_id=template.template_id,
        revision=2,
        payload_json=template.to_json(),
    )

    class _MismatchedTemplate:
        template_id = template.template_id
        identity = SignatureIdentity(
            display_text="Different Analyst",
            wordmark_text="DIFFERENT",
        )

        @staticmethod
        def to_json() -> str:
            return snapshot.payload_json

    with pytest.raises(CompositionPlanValidationError, match="differs from its queued"):
        LockedTemplateIdentity.capture(snapshot, _MismatchedTemplate())


def test_optional_layers_follow_approved_toggles_and_result_identity_setting():
    snapshot, identity = _locked_identity(show_result=False)
    plan = build_composition_plan(
        _package(
            ExportStyle.VERTICAL,
            snapshot=snapshot,
            ink=False,
            voiceover=False,
            play_call=True,
            show_result=False,
        ),
        PixelSize(1920, 1080),
        template_identity=identity,
    )

    assert plan.layer(CompositionLayerKind.INK) is None
    assert plan.layer(CompositionLayerKind.WAVEFORM) is None
    assert plan.layer(CompositionLayerKind.RESULT) is None
    assert plan.layer(CompositionLayerKind.PLAY_CALL) is not None


@pytest.mark.parametrize("style", [ExportStyle.SIGNATURE, ExportStyle.VERTICAL])
@pytest.mark.parametrize("ink", [False, True])
@pytest.mark.parametrize("voiceover", [False, True])
@pytest.mark.parametrize("play_call", [False, True])
def test_toggle_input_and_layer_presence_stay_in_lockstep(
    style,
    ink,
    voiceover,
    play_call,
):
    snapshot, identity = _locked_identity(show_result=True)
    plan = build_composition_plan(
        _package(
            style,
            snapshot=snapshot,
            ink=ink,
            voiceover=voiceover,
            play_call=play_call,
            show_result=True,
        ),
        PixelSize(1920, 1080),
        template_identity=identity,
    )

    assert (plan.layer(CompositionLayerKind.INK) is not None) is ink
    assert (plan.layer(CompositionLayerKind.WAVEFORM) is not None) is voiceover
    assert bool(plan.presentation_event_input_name) is voiceover
    assert (plan.layer(CompositionLayerKind.SITUATION) is not None) is play_call
    assert (plan.layer(CompositionLayerKind.PLAY_CALL) is not None) is play_call
    assert (plan.layer(CompositionLayerKind.RESULT) is not None) is play_call


def test_result_input_must_match_locked_template_visibility():
    hidden_snapshot, hidden_identity = _locked_identity(show_result=False)
    with pytest.raises(
        CompositionPlanValidationError,
        match="do not match the locked template",
    ):
        build_composition_plan(
            _package(
                ExportStyle.SIGNATURE,
                snapshot=hidden_snapshot,
                show_result=True,
            ),
            PixelSize(1920, 1080),
            template_identity=hidden_identity,
        )

    shown_snapshot, shown_identity = _locked_identity(show_result=True)
    with pytest.raises(CompositionPlanValidationError, match="play_call_result"):
        build_composition_plan(
            _package(
                ExportStyle.SIGNATURE,
                snapshot=shown_snapshot,
                show_result=False,
            ),
            PixelSize(1920, 1080),
            template_identity=shown_identity,
        )


def test_optional_border_is_omitted_and_wordmark_text_is_the_logo_fallback():
    snapshot, identity = _locked_identity(
        include_border=False,
        include_wordmark_logo=False,
        wordmark_text="CANE FILMS",
    )
    plan = build_composition_plan(
        _package(ExportStyle.SIGNATURE, snapshot=snapshot),
        PixelSize(1920, 1080),
        template_identity=identity,
    )

    assert plan.layer(CompositionLayerKind.BORDER) is None
    wordmark = plan.layer(CompositionLayerKind.WORDMARK)
    assert wordmark is not None
    assert wordmark.asset is None
    assert wordmark.text_source is LayerTextSource.TEMPLATE_WORDMARK
    assert wordmark.fit_policy is LayerFitPolicy.NONE
    assert identity.wordmark_text == "CANE FILMS"


def test_empty_wordmark_without_a_logo_is_omitted_without_implicit_branding():
    snapshot, identity = _locked_identity(
        include_wordmark_logo=False,
        wordmark_text="",
    )

    plan = build_composition_plan(
        _package(ExportStyle.SIGNATURE, snapshot=snapshot),
        PixelSize(1920, 1080),
        template_identity=identity,
    )

    assert plan.layer(CompositionLayerKind.WORDMARK) is None


def test_optional_photo_and_username_layers_are_omitted_not_fabricated():
    snapshot, identity = _locked_identity(
        include_profile=False,
        username_text="",
    )

    plan = build_composition_plan(
        _package(ExportStyle.VERTICAL, snapshot=snapshot),
        PixelSize(1920, 1080),
        template_identity=identity,
    )

    assert plan.layer(CompositionLayerKind.PROFILE_PHOTO) is None
    assert plan.layer(CompositionLayerKind.IDENTITY_USERNAME) is None
    assert plan.layer(CompositionLayerKind.IDENTITY_DISPLAY) is not None


def test_profile_and_logo_have_locked_fit_mask_and_text_semantics():
    plan = _plan(ExportStyle.SIGNATURE)

    profile = plan.layer(CompositionLayerKind.PROFILE_PHOTO)
    assert profile.fit_policy is LayerFitPolicy.COVER
    assert profile.mask_policy is LayerMaskPolicy.CIRCLE
    assert profile.text_source is LayerTextSource.NONE

    display = plan.layer(CompositionLayerKind.IDENTITY_DISPLAY)
    assert display.text_source is LayerTextSource.TEMPLATE_DISPLAY

    username = plan.layer(CompositionLayerKind.IDENTITY_USERNAME)
    assert username.text_source is LayerTextSource.TEMPLATE_USERNAME

    wordmark = plan.layer(CompositionLayerKind.WORDMARK)
    assert wordmark.fit_policy is LayerFitPolicy.CONTAIN
    assert wordmark.mask_policy is LayerMaskPolicy.NONE
    assert wordmark.text_source is LayerTextSource.NONE


@pytest.mark.parametrize("style", [ExportStyle.SIGNATURE, ExportStyle.VERTICAL])
def test_one_border_asset_hard_fails_until_cross_aspect_strategy_is_defined(style):
    snapshot, identity = _locked_identity(include_border=True)

    with pytest.raises(CompositionPlanValidationError, match="aspect strategy"):
        build_composition_plan(
            _package(style, snapshot=snapshot),
            PixelSize(1920, 1080),
            template_identity=identity,
        )


@pytest.mark.parametrize("mime_type", ["image/svg+xml", [], None])
def test_identity_asset_reference_rejects_unapproved_mime_and_invalid_role(
    mime_type,
):
    with pytest.raises(CompositionPlanValidationError, match="PNG, JPEG, or WebP"):
        ImmutableAssetReference(
            role=IdentityAssetRole.PROFILE_PHOTO,
            sha256="0" * 64,
            mime_type=mime_type,
            width=1,
            height=1,
        )

    with pytest.raises(CompositionPlanValidationError, match="role is invalid"):
        ImmutableAssetReference.capture("invented", None)


def test_missing_event_or_audio_input_fails_before_rendering():
    snapshot, identity = _locked_identity()
    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=SOCIAL_1080P.name,
        template=snapshot,
        compositor_inputs=(CompositorInput("source_video", "D:/film/game.mp4"),),
        include_ink=True,
        include_voiceover=True,
    )

    with pytest.raises(ExportPackageValidationError) as caught:
        build_composition_plan(
            package,
            PixelSize(1920, 1080),
            template_identity=identity,
        )

    assert "ink_event_track" in str(caught.value)
    assert "voiceover_audio" in str(caught.value)
    assert "presentation_event_track" in str(caught.value)


def test_voiceover_plan_retains_the_audio_clock_event_input_for_replay():
    plan = _plan(ExportStyle.SIGNATURE)

    assert plan.presentation_event_input_name == "presentation_event_track"


def test_non_voiceover_plan_has_no_presentation_event_input():
    snapshot, identity = _locked_identity()
    plan = build_composition_plan(
        _package(
            ExportStyle.SIGNATURE,
            snapshot=snapshot,
            voiceover=False,
        ),
        PixelSize(1920, 1080),
        template_identity=identity,
    )

    assert plan.presentation_event_input_name == ""


def test_slate_is_not_silently_invented_or_ignored():
    snapshot, identity = _locked_identity()
    package = _package(
        ExportStyle.SIGNATURE,
        snapshot=snapshot,
        slate=True,
    )

    with pytest.raises(ExportPackageValidationError, match="cannot be queued"):
        build_composition_plan(
            package,
            PixelSize(1920, 1080),
            template_identity=identity,
        )


def test_plan_serialization_is_canonical_stable_and_round_trips():
    first = _plan(ExportStyle.VERTICAL)
    second = _plan(ExportStyle.VERTICAL)

    serialized = first.to_json()

    assert serialized == second.to_json()
    assert CompositionPlan.from_json(serialized) == first
    assert "callout" not in serialized
    assert '"title"' not in serialized
    assert "D:/film/game.mp4" not in serialized


def test_serialized_plan_rejects_locked_identity_and_asset_reference_tampering():
    payload = json.loads(_plan(ExportStyle.SIGNATURE).to_json())
    payload["plan"]["template_identity"]["display_text"] = "FORGED"

    with pytest.raises(CompositionPlanValidationError, match="integrity"):
        CompositionPlan.from_json(json.dumps(payload))

    payload = json.loads(_plan(ExportStyle.SIGNATURE).to_json())
    payload["plan"]["template_identity"]["profile_photo"]["sha256"] = "0" * 64
    profile = next(
        layer
        for layer in payload["plan"]["layers"]
        if layer["kind"] == "profile_photo"
    )
    profile["asset"]["sha256"] = "0" * 64

    with pytest.raises(CompositionPlanValidationError, match="integrity"):
        CompositionPlan.from_json(json.dumps(payload))


def test_serialized_plan_rejects_reordered_layers_and_source_crop():
    plan = _plan(ExportStyle.SIGNATURE)
    payload = json.loads(plan.to_json())
    layers = payload["plan"]["layers"]
    layers[1], layers[2] = layers[2], layers[1]

    with pytest.raises(CompositionPlanValidationError, match="paint order"):
        CompositionPlan.from_json(json.dumps(payload))

    payload = json.loads(plan.to_json())
    payload["plan"]["source_film"]["source_crop_rect"]["x"] = 1
    payload["plan"]["source_film"]["source_crop_rect"]["width"] -= 1
    with pytest.raises(CompositionPlanValidationError, match="crop is forbidden"):
        CompositionPlan.from_json(json.dumps(payload))


def test_serialized_plan_cannot_turn_locked_geometry_into_layout_knobs():
    payload = json.loads(_plan(ExportStyle.VERTICAL).to_json())
    play_call = next(
        layer
        for layer in payload["plan"]["layers"]
        if layer["kind"] == "play_call"
    )
    play_call["rect"]["y"] += 12

    with pytest.raises(CompositionPlanValidationError, match="locked geometry"):
        CompositionPlan.from_json(json.dumps(payload))

    payload = json.loads(_plan(ExportStyle.VERTICAL).to_json())
    payload["plan"]["safe_area"]["height"] -= 1
    with pytest.raises(CompositionPlanValidationError, match="safe area"):
        CompositionPlan.from_json(json.dumps(payload))


def test_serialized_plan_enforces_atomic_play_call_and_result_visibility():
    payload = json.loads(_plan(ExportStyle.SIGNATURE).to_json())
    payload["plan"]["layers"] = [
        layer
        for layer in payload["plan"]["layers"]
        if layer["kind"] != "situation"
    ]

    with pytest.raises(CompositionPlanValidationError, match="must appear together"):
        CompositionPlan.from_json(json.dumps(payload))

    payload = json.loads(_plan(ExportStyle.SIGNATURE).to_json())
    payload["plan"]["layers"] = [
        layer
        for layer in payload["plan"]["layers"]
        if layer["kind"] != "result"
    ]
    with pytest.raises(
        CompositionPlanValidationError,
        match="locked template visibility",
    ):
        CompositionPlan.from_json(json.dumps(payload))


def test_serialized_plan_rejects_wrong_role_assets_and_layer_semantics():
    payload = json.loads(_plan(ExportStyle.SIGNATURE).to_json())
    profile_asset = next(
        layer["asset"]
        for layer in payload["plan"]["layers"]
        if layer["kind"] == "profile_photo"
    )
    film = next(
        layer
        for layer in payload["plan"]["layers"]
        if layer["kind"] == "film"
    )
    film["asset"] = profile_asset

    with pytest.raises(CompositionPlanValidationError, match="locked semantics"):
        CompositionPlan.from_json(json.dumps(payload))

    payload = json.loads(_plan(ExportStyle.SIGNATURE).to_json())
    profile = next(
        layer
        for layer in payload["plan"]["layers"]
        if layer["kind"] == "profile_photo"
    )
    profile["fit_policy"] = "contain"
    with pytest.raises(CompositionPlanValidationError, match="fit policy"):
        CompositionPlan.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("field_path", "bad_value"),
    [
        (("plan", "style"), "invented"),
        (("plan", "encoder_transform_policy"), "invented"),
    ],
)
def test_codec_wraps_invalid_enum_values(field_path, bad_value):
    payload = json.loads(_plan(ExportStyle.SIGNATURE).to_json())
    payload[field_path[0]][field_path[1]] = bad_value

    with pytest.raises(CompositionPlanValidationError):
        CompositionPlan.from_json(json.dumps(payload))


def test_plan_builder_wraps_wrong_package_type_at_the_public_boundary():
    with pytest.raises(CompositionPlanValidationError, match="package snapshot"):
        build_composition_plan(None, PixelSize(1920, 1080))


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("canvas", None, "canvas"),
        ("safe_area", None, "safe area"),
        ("source_film", None, "source film"),
        ("layers", None, "layers"),
    ],
)
def test_direct_plan_boundary_wraps_arbitrary_bad_field_types(
    field,
    bad_value,
    message,
):
    plan = _plan(ExportStyle.SIGNATURE)
    values = {
        "style": plan.style,
        "canvas": plan.canvas,
        "safe_area": plan.safe_area,
        "source_film": plan.source_film,
        "layers": plan.layers,
        "presentation_event_input_name": plan.presentation_event_input_name,
        "encoder_transform_policy": plan.encoder_transform_policy,
        "template_identity": plan.template_identity,
    }
    values[field] = bad_value

    with pytest.raises(CompositionPlanValidationError, match=message):
        CompositionPlan(**values)


def test_serialized_clean_plan_cannot_reposition_or_resize_source_film():
    clean = build_composition_plan(
        ExportPackageSnapshot(style=ExportStyle.CLEAN),
        PixelSize(1920, 1080),
    )
    payload = json.loads(clean.to_json())
    payload["plan"]["safe_area"]["height"] -= 1

    with pytest.raises(CompositionPlanValidationError, match="complete source frame"):
        CompositionPlan.from_json(json.dumps(payload))


@pytest.mark.parametrize(
    "source_size",
    [
        PixelSize(640, 480),
        PixelSize(3840, 1080),
        PixelSize(1080, 1920),
        PixelSize(1, 1000),
        PixelSize(1000, 1),
    ],
)
@pytest.mark.parametrize("style", [ExportStyle.SIGNATURE, ExportStyle.VERTICAL])
def test_contain_policy_never_crops_extreme_source_aspects(source_size, style):
    snapshot, identity = _locked_identity()
    plan = build_composition_plan(
        _package(style, snapshot=snapshot),
        source_size,
        template_identity=identity,
    )

    assert plan.source_film.source_crop_rect == PixelRect(
        0,
        0,
        source_size.width,
        source_size.height,
    )
    assert plan.source_film.slot_rect.contains(plan.source_film.content_rect)


def test_layout_constants_are_private_not_template_configuration():
    for style in (ExportStyle.SIGNATURE, ExportStyle.VERTICAL):
        plan = _plan(style)
        canvas = PixelRect(0, 0, plan.canvas.width, plan.canvas.height)
        assert canvas.contains(plan.safe_area)
        assert canvas.contains(plan.source_film.slot_rect)


def test_two_and_a_half_pixel_concept_rule_rounds_to_three_integer_raster_pixels():
    assert _plan(ExportStyle.SIGNATURE).layer(
        CompositionLayerKind.PROGRESS
    ).rect.height == 3
    assert _plan(ExportStyle.VERTICAL).layer(
        CompositionLayerKind.PROGRESS
    ).rect.height == 3

