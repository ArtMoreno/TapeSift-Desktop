"""Synthetic pixel tests for the shared Signature/Vertical compositor."""

from __future__ import annotations

from io import BytesIO
import json
import os
import threading
import wave

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRect  # noqa: E402
from PySide6.QtGui import QColor, QFont, QFontInfo, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.models.composition_plan import (  # noqa: E402
    CompositionLayerKind,
    IdentityAssetRole,
    LockedTemplateIdentity,
    PixelSize,
    build_composition_plan,
)
from tapesift.models.export_package import (  # noqa: E402
    CompositorInput,
    ExportPackageSnapshot,
    ExportStyle,
    ExportTemplateSnapshot,
)
from tapesift.models.export_settings import SOCIAL_1080P, SOURCE_QUALITY  # noqa: E402
from tapesift.models.presentation_track import (  # noqa: E402
    PresentationEvent,
    PresentationEventKind,
    PresentationEventTrack,
)
from tapesift.models.signature_template import (  # noqa: E402
    ImageAssetSnapshot,
    SignatureIdentity,
    SignatureTemplate,
)
from tapesift.services.preview_compositor import (  # noqa: E402
    CompositionAssetPayload,
    CompositionFrameCompositor,
    CompositionFramePayloads,
    PreviewCompositorError,
)
import tapesift.services.preview_compositor as preview_module  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _encode_png(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def _solid_png(width: int, height: int, color: str) -> bytes:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    return _encode_png(image)


def _quadrant_png(width: int = 100, height: int = 100) -> bytes:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    painter.fillRect(QRect(0, 0, width // 2, height // 2), QColor("#E53935"))
    painter.fillRect(
        QRect(width // 2, 0, width - width // 2, height // 2),
        QColor("#43A047"),
    )
    painter.fillRect(
        QRect(0, height // 2, width // 2, height - height // 2),
        QColor("#1E88E5"),
    )
    painter.fillRect(
        QRect(
            width // 2,
            height // 2,
            width - width // 2,
            height - height // 2,
        ),
        QColor("#FDD835"),
    )
    painter.end()
    return _encode_png(image)


def _wav_bytes(*, sample_rate: int = 48_000, frame_count: int = 960) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        samples = bytearray()
        for index in range(frame_count):
            value = 22_000 if (index // 24) % 2 == 0 else -12_000
            samples.extend(int(value).to_bytes(2, "little", signed=True))
        writer.writeframes(bytes(samples))
    return output.getvalue()


def _track_bytes(
    *,
    sample_rate: int = 48_000,
    final_frame: int = 0,
) -> bytes:
    events = ()
    if final_frame:
        events = (
            PresentationEvent.create(
                audio_frame=final_frame,
                sequence=0,
                kind=PresentationEventKind.SOURCE_POSITION,
                payload={"source_position_ms": 1200},
            ),
        )
    return PresentationEventTrack(sample_rate, events).to_json().encode("utf-8")


def _composition(
    style: ExportStyle,
    *,
    source_frame: bytes,
    source_size: PixelSize,
    include_ink: bool = False,
    include_voiceover: bool = False,
    include_play_call: bool = False,
    show_result: bool = True,
    profile: bytes | None = None,
    logo: bytes | None = None,
    wordmark_text: str = "TapeSift",
    username_text: str = "@coach_view",
) -> tuple[object, CompositionFramePayloads]:
    profile_asset = ImageAssetSnapshot.capture(profile) if profile else None
    logo_asset = ImageAssetSnapshot.capture(logo) if logo else None
    template = SignatureTemplate(
        template_id="preview-test",
        name="Preview Test",
        identity=SignatureIdentity(
            display_text="Nick Marshall",
            username_text=username_text,
            wordmark_text=wordmark_text,
            profile_photo=profile_asset,
            wordmark_logo=logo_asset,
            accent_color="#39E07A",
            show_result=show_result,
        ),
    )
    snapshot = ExportTemplateSnapshot(
        template_id=template.template_id,
        revision=3,
        payload_json=template.to_json(),
    )
    names = [CompositorInput("source_video", "source-frame")]
    if include_ink:
        names.append(CompositorInput("ink_event_track", "current-marks"))
    if include_voiceover:
        names.extend((
            CompositorInput("voiceover_audio", "take-7"),
            CompositorInput("presentation_event_track", "take-7-events"),
        ))
    if include_play_call:
        names.extend((
            CompositorInput("play_call_situation", "3rd & 7 / FSU 38"),
            CompositorInput("play_call_concept", "BECK FLEA FLICKER"),
        ))
        if show_result:
            names.append(CompositorInput("play_call_result", "TOUCHDOWN"))
    package = ExportPackageSnapshot(
        style=style,
        technical_preset=SOCIAL_1080P.name,
        template=snapshot,
        compositor_inputs=tuple(names),
        include_ink=include_ink,
        include_voiceover=include_voiceover,
        include_play_call=include_play_call,
    )
    plan = build_composition_plan(
        package,
        source_size,
        template_identity=LockedTemplateIdentity.capture(snapshot, template),
    )

    assets = []
    if profile is not None:
        assets.append(CompositionAssetPayload(IdentityAssetRole.PROFILE_PHOTO, profile))
    if logo is not None:
        assets.append(CompositionAssetPayload(IdentityAssetRole.WORDMARK_LOGO, logo))
    marks = None
    if include_ink:
        marks = json.dumps([
            {
                "id": "arrow-1",
                "kind": "arrow",
                "ink": "gold",
                "width": 1.0,
                "points": [[0.2, 0.5], [0.8, 0.5]],
            },
            {
                "id": "circle-1",
                "kind": "circle",
                "ink": "cyan",
                "width": 1.0,
                "points": [[0.1, 0.1], [0.3, 0.3]],
            },
            {
                "id": "line-1",
                "kind": "line",
                "ink": "red",
                "width": 1.0,
                "points": [[0.7, 0.2], [0.9, 0.3]],
            },
            {
                "id": "freehand-1",
                "kind": "freehand",
                "ink": "gold",
                "width": 0.8,
                "points": [[0.35, 0.75], [0.5, 0.65], [0.65, 0.75]],
            },
        ], separators=(",", ":"), sort_keys=True).encode("utf-8")
    payloads = CompositionFramePayloads(
        source_video=source_frame,
        assets=tuple(assets),
        ink_event_track=marks,
        voiceover_audio=_wav_bytes() if include_voiceover else None,
        presentation_event_track=(
            _track_bytes(final_frame=900) if include_voiceover else None
        ),
        play_call_situation=(
            b"3rd & 7 / FSU 38" if include_play_call else None
        ),
        play_call_concept=(b"BECK FLEA FLICKER" if include_play_call else None),
        play_call_result=(
            b"TOUCHDOWN" if include_play_call and show_result else None
        ),
        timeline_position=1,
        timeline_duration=2,
    )
    return plan, payloads


def _color(image: QImage, x: int, y: int) -> QColor:
    return QColor.fromRgba(image.pixel(x, y))


def _same_rgb(actual: QColor, expected: str, tolerance: int = 0) -> bool:
    target = QColor(expected)
    return all(
        abs(left - right) <= tolerance
        for left, right in zip(
            (actual.red(), actual.green(), actual.blue()),
            (target.red(), target.green(), target.blue()),
        )
    )


def _region_has_non_background(image: QImage, rect) -> bool:
    background = QColor("#090C0A")
    # Thin waveform bars and three-pixel progress rules can legitimately sit
    # between coarse sample columns.  Scan the locked region exactly rather
    # than making a visual-presence assertion depend on sampling luck.
    for y in range(rect.y, rect.bottom):
        for x in range(rect.x, rect.right):
            if _color(image, x, y).rgb() != background.rgb():
                return True
    return False


def test_clean_is_the_unchanged_source_only_canvas():
    source = _quadrant_png(20, 10)
    plan = build_composition_plan(
        ExportPackageSnapshot(
            style=ExportStyle.CLEAN,
            technical_preset=SOURCE_QUALITY.name,
        ),
        PixelSize(20, 10),
    )

    rendered = CompositionFrameCompositor().render(
        plan,
        CompositionFramePayloads(source_video=source),
    )

    assert (rendered.width(), rendered.height()) == (20, 10)
    assert _same_rgb(_color(rendered, 2, 2), "#E53935")
    assert _same_rgb(_color(rendered, 17, 2), "#43A047")
    assert _same_rgb(_color(rendered, 2, 8), "#1E88E5")
    assert _same_rgb(_color(rendered, 17, 8), "#FDD835")


@pytest.mark.parametrize(
    ("style", "expected_size"),
    ((ExportStyle.SIGNATURE, (1920, 1080)), (ExportStyle.VERTICAL, (1080, 1920))),
)
def test_fixed_canvas_contains_every_source_corner_without_cropping(
    style,
    expected_size,
):
    source = _quadrant_png()
    plan, payloads = _composition(
        style,
        source_frame=source,
        source_size=PixelSize(100, 100),
        wordmark_text="",
        username_text="",
    )

    rendered = CompositionFrameCompositor().render(plan, payloads)
    content = plan.source_film.content_rect
    slot = plan.source_film.slot_rect

    assert (rendered.width(), rendered.height()) == expected_size
    assert _same_rgb(_color(rendered, content.x + 8, content.y + 8), "#E53935")
    assert _same_rgb(_color(rendered, content.right - 9, content.y + 8), "#43A047")
    assert _same_rgb(_color(rendered, content.x + 8, content.bottom - 9), "#1E88E5")
    assert _same_rgb(
        _color(rendered, content.right - 9, content.bottom - 9), "#FDD835"
    )
    assert plan.source_film.source_crop_rect.width == 100
    assert plan.source_film.source_crop_rect.height == 100
    # A square frame leaves genuine black contain bars inside the film slot.
    assert _same_rgb(_color(rendered, slot.x + 2, slot.y + 2), "#000000")


@pytest.mark.parametrize("style", (ExportStyle.SIGNATURE, ExportStyle.VERTICAL))
def test_full_treatment_paints_assets_identity_call_ink_waveform_and_progress(style):
    source = _solid_png(320, 180, "#214C2F")
    profile = _solid_png(80, 60, "#D64B6A")
    logo = _solid_png(120, 30, "#246BCE")
    plan, payloads = _composition(
        style,
        source_frame=source,
        source_size=PixelSize(320, 180),
        include_ink=True,
        include_voiceover=True,
        include_play_call=True,
        profile=profile,
        logo=logo,
    )

    rendered = CompositionFrameCompositor().render(plan, payloads)
    film = plan.source_film.content_rect
    profile_rect = plan.layer(CompositionLayerKind.PROFILE_PHOTO).rect
    logo_rect = plan.layer(CompositionLayerKind.WORDMARK).rect
    display_rect = plan.layer(CompositionLayerKind.IDENTITY_DISPLAY).rect
    call_rect = plan.layer(CompositionLayerKind.PLAY_CALL).rect
    result_rect = plan.layer(CompositionLayerKind.RESULT).rect
    waveform_rect = plan.layer(CompositionLayerKind.WAVEFORM).rect
    progress_rect = plan.layer(CompositionLayerKind.PROGRESS).rect

    # Ink uses source-film fractions and sits above the contained film.
    assert _same_rgb(
        _color(
            rendered,
            int(film.x + film.width * 0.5),
            int(film.y + film.height * 0.5),
        ),
        "#FFD24A",
        tolerance=2,
    )
    # Cover + circular mask keeps the photo center and clips its corners.
    assert _same_rgb(
        _color(
            rendered,
            profile_rect.x + profile_rect.width // 2,
            profile_rect.y + profile_rect.height // 2,
        ),
        "#D64B6A",
    )
    assert _same_rgb(
        _color(rendered, profile_rect.x, profile_rect.y),
        "#090C0A",
        tolerance=2,
    )
    assert _same_rgb(
        _color(
            rendered,
            logo_rect.x + logo_rect.width // 2,
            logo_rect.y + logo_rect.height // 2,
        ),
        "#246BCE",
    )
    assert _region_has_non_background(rendered, display_rect)
    assert _region_has_non_background(rendered, call_rect)
    assert _region_has_non_background(rendered, result_rect)
    assert _region_has_non_background(rendered, waveform_rect)
    assert _same_rgb(
        _color(
            rendered,
            progress_rect.x + progress_rect.width // 4,
            progress_rect.y + 1,
        ),
        "#39E07A",
    )
    assert _same_rgb(
        _color(
            rendered,
            progress_rect.x + (progress_rect.width * 3) // 4,
            progress_rect.y + 1,
        ),
        "#263029",
    )


def test_no_photo_and_no_logo_use_only_the_locked_text_fallback_layers():
    source = _solid_png(160, 90, "#214C2F")
    plan, payloads = _composition(
        ExportStyle.SIGNATURE,
        source_frame=source,
        source_size=PixelSize(160, 90),
        profile=None,
        logo=None,
        wordmark_text="TAPESIFT",
    )

    rendered = CompositionFrameCompositor().render(plan, payloads)

    assert plan.layer(CompositionLayerKind.PROFILE_PHOTO) is None
    assert _region_has_non_background(
        rendered, plan.layer(CompositionLayerKind.IDENTITY_DISPLAY).rect
    )
    assert _region_has_non_background(
        rendered, plan.layer(CompositionLayerKind.WORDMARK).rect
    )
    assert QFontInfo(QFont("Rajdhani")).exactMatch()


def test_repeated_render_is_byte_for_byte_deterministic():
    plan, payloads = _composition(
        ExportStyle.VERTICAL,
        source_frame=_solid_png(160, 90, "#214C2F"),
        source_size=PixelSize(160, 90),
        include_ink=True,
        include_voiceover=True,
        include_play_call=True,
        profile=_solid_png(40, 40, "#D64B6A"),
        logo=_solid_png(80, 20, "#246BCE"),
    )
    compositor = CompositionFrameCompositor()

    assert compositor.render_png(plan, payloads) == compositor.render_png(
        plan, payloads
    )


def test_missing_and_hash_mismatched_assets_are_controlled_errors():
    source = _solid_png(160, 90, "#214C2F")
    profile = _solid_png(40, 40, "#D64B6A")
    plan, payloads = _composition(
        ExportStyle.SIGNATURE,
        source_frame=source,
        source_size=PixelSize(160, 90),
        profile=profile,
        logo=None,
        wordmark_text="",
    )
    compositor = CompositionFrameCompositor()

    with pytest.raises(
        PreviewCompositorError,
        match="bytes are missing: profile_photo",
    ):
        compositor.render(
            plan,
            CompositionFramePayloads(source_video=source),
        )

    mismatched = CompositionFramePayloads(
        source_video=source,
        assets=(CompositionAssetPayload(
            IdentityAssetRole.PROFILE_PHOTO,
            _solid_png(40, 40, "#0000FF"),
        ),),
    )
    with pytest.raises(PreviewCompositorError, match="MIME/hash validation"):
        compositor.render(plan, mismatched)

    assert payloads.assets[0].data == profile


def test_unused_border_bytes_are_rejected_instead_of_inventing_a_strategy():
    source = _solid_png(160, 90, "#214C2F")
    plan, _ = _composition(
        ExportStyle.VERTICAL,
        source_frame=source,
        source_size=PixelSize(160, 90),
        wordmark_text="",
        username_text="",
    )
    payloads = CompositionFramePayloads(
        source_video=source,
        assets=(CompositionAssetPayload(
            IdentityAssetRole.BORDER,
            _solid_png(20, 20, "#FFFFFF"),
        ),),
    )

    with pytest.raises(PreviewCompositorError, match="not used by the plan: border"):
        CompositionFrameCompositor().render(plan, payloads)


def test_wrong_source_size_and_stale_optional_payloads_fail_before_painting():
    source = _solid_png(160, 90, "#214C2F")
    plan, _ = _composition(
        ExportStyle.SIGNATURE,
        source_frame=source,
        source_size=PixelSize(160, 90),
        wordmark_text="",
        username_text="",
    )
    compositor = CompositionFrameCompositor()

    with pytest.raises(PreviewCompositorError, match="dimensions do not match"):
        compositor.render(
            plan,
            CompositionFramePayloads(
                source_video=_solid_png(80, 45, "#214C2F"),
            ),
        )
    with pytest.raises(PreviewCompositorError, match="Unexpected ink_event_track"):
        compositor.render(
            plan,
            CompositionFramePayloads(source_video=source, ink_event_track=b"[]"),
        )
    with pytest.raises(PreviewCompositorError, match="Unexpected play_call_result"):
        compositor.render(
            plan,
            CompositionFramePayloads(source_video=source, play_call_result=b"TD"),
        )


def test_audio_and_event_track_must_share_the_audio_frame_clock():
    source = _solid_png(160, 90, "#214C2F")
    plan, payloads = _composition(
        ExportStyle.SIGNATURE,
        source_frame=source,
        source_size=PixelSize(160, 90),
        include_voiceover=True,
        wordmark_text="",
        username_text="",
    )
    bad_rate = CompositionFramePayloads(
        source_video=source,
        voiceover_audio=payloads.voiceover_audio,
        presentation_event_track=_track_bytes(sample_rate=44_100),
    )
    with pytest.raises(PreviewCompositorError, match="sample rate differs"):
        CompositionFrameCompositor().render(plan, bad_rate)

    past_audio = CompositionFramePayloads(
        source_video=source,
        voiceover_audio=payloads.voiceover_audio,
        presentation_event_track=_track_bytes(final_frame=961),
    )
    with pytest.raises(PreviewCompositorError, match="extends beyond"):
        CompositionFrameCompositor().render(plan, past_audio)


def test_all_supported_film_coordinate_mark_kinds_paint_inside_the_film_only():
    source = _solid_png(320, 180, "#214C2F")
    plan, payloads = _composition(
        ExportStyle.VERTICAL,
        source_frame=source,
        source_size=PixelSize(320, 180),
        include_ink=True,
        wordmark_text="",
        username_text="",
    )

    rendered = CompositionFrameCompositor().render(plan, payloads)
    film = plan.source_film.content_rect
    slot = plan.source_film.slot_rect

    # Arrow, circle, line, and multi-sample freehand each leave their ink.
    expected = (
        (0.5, 0.5, "#FFD24A"),
        (0.2, 0.1, "#5AD6F0"),
        (0.8, 0.25, "#FF6B5E"),
        (0.5, 0.65, "#FFD24A"),
    )
    for fx, fy, color in expected:
        assert _same_rgb(
            _color(
                rendered,
                int(film.x + film.width * fx),
                int(film.y + film.height * fy),
            ),
            color,
            tolerance=5,
        )
    if slot.x < film.x:
        assert _same_rgb(_color(rendered, slot.x + 1, slot.y + 1), "#000000")


def test_excessive_mark_width_is_rejected_before_qpainter_sees_it():
    source = _solid_png(160, 90, "#214C2F")
    plan, payloads = _composition(
        ExportStyle.SIGNATURE,
        source_frame=source,
        source_size=PixelSize(160, 90),
        include_ink=True,
        wordmark_text="",
        username_text="",
    )
    marks = json.loads(payloads.ink_event_track)
    marks[0]["width"] = 17.0
    too_wide = CompositionFramePayloads(
        source_video=source,
        ink_event_track=json.dumps(marks).encode("utf-8"),
    )

    with pytest.raises(PreviewCompositorError, match="width exceeds 16"):
        CompositionFrameCompositor().render(plan, too_wide)


def test_long_voiceover_waveform_is_decoded_once_per_export_compositor(
        monkeypatch):
    source = _solid_png(32, 18, "#203040")
    plan, payloads = _composition(
        ExportStyle.SIGNATURE,
        source_frame=source,
        source_size=PixelSize(32, 18),
        include_voiceover=True,
    )
    original = preview_module._decode_waveform
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(preview_module, "_decode_waveform", counted)
    compositor = CompositionFrameCompositor()
    compositor.render(plan, payloads)
    compositor.render(plan, payloads)
    assert calls == 1

    compositor.clear_cache()
    compositor.render(plan, payloads)
    assert calls == 2


def test_long_voiceover_waveform_reads_pcm_in_bounded_chunks(monkeypatch):
    source = _solid_png(32, 18, "#203040")
    plan, payloads = _composition(
        ExportStyle.SIGNATURE,
        source_frame=source,
        source_size=PixelSize(32, 18),
        include_voiceover=True,
    )
    long_wav = _wav_bytes(frame_count=20_000)
    bounded = CompositionFramePayloads(
        source_video=payloads.source_video,
        voiceover_audio=long_wav,
        presentation_event_track=_track_bytes(final_frame=19_999),
        timeline_position=1,
        timeline_duration=2,
    )
    original = wave.Wave_read.readframes
    requests: list[int] = []

    def observed(reader, count):
        requests.append(count)
        return original(reader, count)

    monkeypatch.setattr(wave.Wave_read, "readframes", observed)
    CompositionFrameCompositor().render(plan, bounded)
    assert requests
    assert max(requests) <= preview_module._WAVEFORM_READ_FRAMES
    assert requests.count(preview_module._WAVEFORM_READ_FRAMES) >= 2


def test_real_persisted_waveform_can_preview_without_loading_full_wav_blob():
    source = _solid_png(32, 18, "#203040")
    plan, payloads = _composition(
        ExportStyle.SIGNATURE,
        source_frame=source,
        source_size=PixelSize(32, 18),
        include_voiceover=True,
    )
    preview = CompositionFramePayloads(
        source_video=payloads.source_video,
        voiceover_waveform=tuple(
            (index % 17) / 16.0 for index in range(1_000)
        ),
        timeline_position=1,
        timeline_duration=2,
    )
    rendered = CompositionFrameCompositor().render(plan, preview)
    assert not rendered.isNull()
    assert len(preview_module._downsample_preview_waveform(
        preview.voiceover_waveform)) == 96


def test_preview_waveform_boundary_rejects_mutable_invalid_and_ambiguous_data():
    with pytest.raises(PreviewCompositorError, match="immutable tuple"):
        CompositionFramePayloads(  # type: ignore[arg-type]
            source_video=b"frame", voiceover_waveform=[0.2]
        )
    with pytest.raises(PreviewCompositorError, match="finite"):
        CompositionFramePayloads(
            source_video=b"frame", voiceover_waveform=(float("nan"),)
        )

    source = _solid_png(32, 18, "#203040")
    plan, payloads = _composition(
        ExportStyle.SIGNATURE,
        source_frame=source,
        source_size=PixelSize(32, 18),
        include_voiceover=True,
    )
    ambiguous = CompositionFramePayloads(
        source_video=source,
        voiceover_audio=payloads.voiceover_audio,
        presentation_event_track=payloads.presentation_event_track,
        voiceover_waveform=(0.2, 0.4),
    )
    with pytest.raises(PreviewCompositorError, match="cannot be combined"):
        CompositionFrameCompositor().render(plan, ambiguous)


def test_static_assets_and_repeated_mark_snapshots_decode_once_per_job(
        monkeypatch):
    plan, payloads = _composition(
        ExportStyle.SIGNATURE,
        source_frame=_solid_png(32, 18, "#203040"),
        source_size=PixelSize(32, 18),
        include_ink=True,
        include_voiceover=True,
    )
    asset_resolver = preview_module._resolve_assets
    mark_decoder = preview_module._decode_marks
    calls = {"assets": 0, "marks": 0}

    def counted_assets(*args, **kwargs):
        calls["assets"] += 1
        return asset_resolver(*args, **kwargs)

    def counted_marks(*args, **kwargs):
        calls["marks"] += 1
        return mark_decoder(*args, **kwargs)

    monkeypatch.setattr(preview_module, "_resolve_assets", counted_assets)
    monkeypatch.setattr(preview_module, "_decode_marks", counted_marks)
    compositor = CompositionFrameCompositor()
    compositor.render(plan, payloads)
    compositor.render(plan, payloads)
    assert calls == {"assets": 1, "marks": 1}
    compositor.clear_cache()
    compositor.render(plan, payloads)
    assert calls == {"assets": 2, "marks": 2}


def test_fonts_must_be_registered_on_gui_thread_then_are_worker_safe(
        monkeypatch):
    monkeypatch.setattr(preview_module, "_FONT_FAMILY", None)
    failures: list[BaseException] = []

    def register_in_worker() -> None:
        try:
            preview_module.register_compositor_fonts()
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=register_in_worker)
    thread.start()
    thread.join(2)
    assert len(failures) == 1
    assert "GUI thread" in str(failures[0])

    family = preview_module.register_compositor_fonts()
    failures.clear()
    thread = threading.Thread(target=register_in_worker)
    thread.start()
    thread.join(2)
    assert not failures
    assert preview_module._FONT_FAMILY == family
