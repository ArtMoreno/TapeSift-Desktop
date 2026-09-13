from __future__ import annotations

from io import BytesIO
import json
import os
import wave

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.models.composition_plan import (  # noqa: E402
    CompositionLayerKind,
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
from tapesift.models.export_settings import SOCIAL_1080P  # noqa: E402
from tapesift.models.presentation_track import (  # noqa: E402
    PresentationEvent,
    PresentationEventKind,
    PresentationEventTrack,
)
from tapesift.models.signature_template import (  # noqa: E402
    SignatureIdentity,
    SignatureTemplate,
)
from tapesift.services.presentation_sequence_renderer import (  # noqa: E402
    CompositionSequenceInputs,
    CompositionSequenceRenderer,
    PresentationSequenceCancelled,
    PresentationSequenceError,
)


RATE = 48_000


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _png(color: str, width: int = 32, height: int = 18) -> bytes:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(color))
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    buffer.close()
    return bytes(encoded)


def _wav(frame_count: int) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(b"\x00\x00" * frame_count)
    return output.getvalue()


def _source(frame: int, sequence: int, position: int) -> PresentationEvent:
    return PresentationEvent.create(
        audio_frame=frame,
        sequence=sequence,
        kind=PresentationEventKind.SOURCE_POSITION,
        payload={"source_position_ms": position},
    )


def _marks(frame: int, sequence: int, marks: list[dict]) -> PresentationEvent:
    return PresentationEvent.create(
        audio_frame=frame,
        sequence=sequence,
        kind=PresentationEventKind.TELESTRATION_SNAPSHOT,
        payload={"marks": marks},
    )


def _arrow() -> dict:
    return {
        "id": "a",
        "kind": "arrow",
        "ink": "gold",
        "width": 1.0,
        "points": [[0.2, 0.5], [0.8, 0.5]],
    }


def _fixture(
    *,
    frame_count: int = 4_800,
    style: ExportStyle = ExportStyle.SIGNATURE,
):
    template = SignatureTemplate(
        template_id="sequence",
        name="Sequence",
        identity=SignatureIdentity(
            display_text="Coach",
            username_text="@coach",
            wordmark_text="TapeSift",
            accent_color="#39E07A",
            show_result=False,
        ),
    )
    snapshot = ExportTemplateSnapshot(
        template_id=template.template_id,
        revision=1,
        payload_json=template.to_json(),
    )
    package = ExportPackageSnapshot(
        style=style,
        technical_preset=SOCIAL_1080P.name,
        template=snapshot,
        compositor_inputs=(
            CompositorInput("source_video", "source"),
            CompositorInput("ink_event_track", "events"),
            CompositorInput("voiceover_audio", "take"),
            CompositorInput("presentation_event_track", "track"),
        ),
        include_ink=True,
        include_voiceover=True,
    )
    plan = build_composition_plan(
        package,
        PixelSize(32, 18),
        template_identity=LockedTemplateIdentity.capture(snapshot, template),
    )
    track = PresentationEventTrack(
        sample_rate=RATE,
        events=(
            _source(0, 0, 100),
            _marks(0, 1, []),
            _source(1_600, 2, 200),
            _marks(1_600, 3, [_arrow()]),
        ),
    )
    inputs = CompositionSequenceInputs(
        voiceover_audio=_wav(frame_count),
        presentation_event_track=track.to_json().encode("utf-8"),
        voiceover_frame_count=frame_count,
        voiceover_waveform=tuple(
            ((index * 3) % 11) / 10.0
            for index in range(max(1, (frame_count * 100 + RATE - 1) // RATE))
        ),
    )
    return plan, inputs


def _no_voiceover_fixture():
    template = SignatureTemplate(
        template_id="sequence-no-vo",
        name="Sequence No VO",
        identity=SignatureIdentity(
            display_text="Coach",
            username_text="@coach",
            wordmark_text="TapeSift",
            accent_color="#39E07A",
            show_result=False,
        ),
    )
    snapshot = ExportTemplateSnapshot(
        template_id=template.template_id,
        revision=1,
        payload_json=template.to_json(),
    )
    package = ExportPackageSnapshot(
        style=ExportStyle.SIGNATURE,
        technical_preset=SOCIAL_1080P.name,
        template=snapshot,
        compositor_inputs=(
            CompositorInput("source_video", "source"),
            CompositorInput("ink_event_track", "clip-ink"),
        ),
        include_ink=True,
        include_voiceover=False,
    )
    plan = build_composition_plan(
        package,
        PixelSize(32, 18),
        template_identity=LockedTemplateIdentity.capture(snapshot, template),
    )
    inputs = CompositionSequenceInputs(
        clip_start_ms=1_000,
        clip_end_ms=1_100,
        clip_ink_event_track=json.dumps([_arrow()]).encode("utf-8"),
    )
    return plan, inputs


def _rgb(image: QImage, x: int, y: int) -> tuple[int, int, int]:
    color = QColor.fromRgba(image.pixel(x, y))
    return color.red(), color.green(), color.blue()


def test_sequence_resolves_source_time_marks_progress_and_locked_canvas():
    plan, inputs = _fixture()
    calls: list[int] = []
    progress: list[tuple[int, int]] = []

    def source(position: int) -> bytes:
        calls.append(position)
        return _png("#112233" if position < 200 else "#334455")

    renderer = CompositionSequenceRenderer(plan, inputs, source)
    frames = list(renderer.frames(on_progress=lambda done, total: progress.append(
        (done, total)
    )))
    assert renderer.frame_count == 3
    assert calls == [100, 200, 200]
    assert progress == [(1, 3), (2, 3), (3, 3)]
    assert all(
        (frame.image.width(), frame.image.height()) == (1920, 1080)
        for frame in frames
    )
    film = plan.layer(CompositionLayerKind.FILM).rect
    center = (film.x + film.width // 2, film.y + film.height // 2)
    assert _rgb(frames[0].image, *center) == (17, 34, 51)
    # The second source is different and its accepted arrow is painted over
    # the film centre at the same audio-frame boundary.
    assert _rgb(frames[1].image, *center) != (17, 34, 51)


def test_no_voiceover_sequence_uses_frozen_clip_clock_and_static_ink():
    plan, inputs = _no_voiceover_fixture()
    calls: list[int] = []
    renderer = CompositionSequenceRenderer(
        plan,
        inputs,
        lambda position: calls.append(position) or _png("#112233"),
    )
    frames = list(renderer.frames())
    assert renderer.frame_count == 3
    assert renderer.source_positions() == (1_000, 1_033, 1_066)
    assert calls == [1_000, 1_033, 1_066]
    assert [frame.audio_frame for frame in frames] == [0, 33, 66]
    film = plan.layer(CompositionLayerKind.FILM).rect
    center = (film.x + film.width // 2, film.y + film.height // 2)
    assert all(_rgb(frame.image, *center) != (17, 34, 51) for frame in frames)


def test_stored_100hz_waveform_moves_with_audio_clock():
    plan, inputs = _fixture(frame_count=9_600)
    renderer = CompositionSequenceRenderer(
        plan, inputs, lambda _position: _png("#112233")
    )
    iterator = renderer.frames()
    first = next(iterator).image
    second = next(iterator).image
    rect = plan.layer(CompositionLayerKind.WAVEFORM).rect
    first_pixels = first.copy(rect.x, rect.y, rect.width, rect.height) \
        .constBits().tobytes()
    second_pixels = second.copy(rect.x, rect.y, rect.width, rect.height) \
        .constBits().tobytes()
    assert first_pixels != second_pixels


def test_sequence_cancel_is_checked_before_loading_next_source_frame():
    plan, inputs = _fixture()
    calls = 0

    def source(_position: int) -> bytes:
        nonlocal calls
        calls += 1
        return _png("#112233")

    renderer = CompositionSequenceRenderer(plan, inputs, source)
    iterator = renderer.frames(is_cancelled=lambda: calls >= 1)
    first = next(iterator)
    assert first.index == 0
    with pytest.raises(PresentationSequenceCancelled):
        next(iterator)
    assert calls == 1


def test_sequence_wraps_source_and_compositor_failures_with_frame_context():
    plan, inputs = _fixture()

    def broken(_position: int) -> bytes:
        raise OSError("decoder stopped")

    renderer = CompositionSequenceRenderer(plan, inputs, broken)
    with pytest.raises(PresentationSequenceError, match="output frame 0"):
        next(renderer.frames())

    wrong_size = CompositionSequenceRenderer(
        plan, inputs, lambda _position: _png("#112233", 16, 9)
    )
    with pytest.raises(PresentationSequenceError, match="Output frame 0"):
        next(wrong_size.frames())


def test_sequence_rejects_plan_mode_mismatch_non_bytes_and_track_without_frame_zero():
    plan, inputs = _fixture()
    clean_package = ExportPackageSnapshot(style=ExportStyle.CLEAN)
    clean_plan = build_composition_plan(clean_package, PixelSize(32, 18))
    with pytest.raises(PresentationSequenceError, match="do not match"):
        CompositionSequenceRenderer(clean_plan, inputs, lambda _p: _png("#000"))

    renderer = CompositionSequenceRenderer(
        plan, inputs, lambda _position: "mutable"  # type: ignore[return-value]
    )
    with pytest.raises(PresentationSequenceError, match="immutable"):
        next(renderer.frames())

    bad_track = PresentationEventTrack(
        sample_rate=RATE,
        events=(_source(1, 0, 100),),
    )
    bad_inputs = CompositionSequenceInputs(
        voiceover_audio=_wav(4_800),
        presentation_event_track=bad_track.to_json().encode("utf-8"),
        voiceover_frame_count=4_800,
    )
    with pytest.raises(PresentationSequenceError, match="audio frame 0"):
        CompositionSequenceRenderer(plan, bad_inputs, lambda _p: _png("#000"))


def test_sequence_input_boundary_freezes_assets_and_rejects_mutable_bytes():
    with pytest.raises(PresentationSequenceError, match="immutable"):
        CompositionSequenceInputs(  # type: ignore[arg-type]
            voiceover_audio=bytearray(b"wav"),
            presentation_event_track=b"{}",
            voiceover_frame_count=1,
        )
    with pytest.raises(PresentationSequenceError, match="positive"):
        CompositionSequenceInputs(
            voiceover_audio=b"wav",
            presentation_event_track=b"{}",
            voiceover_frame_count=0,
        )


def test_sequence_preflight_rejects_wav_count_mismatch_and_track_overrun():
    track = PresentationEventTrack(
        sample_rate=RATE,
        events=(_source(0, 0, 100),),
    )
    with pytest.raises(PresentationSequenceError, match="frame count differs"):
        CompositionSequenceInputs(
            voiceover_audio=_wav(4_801),
            presentation_event_track=track.to_json().encode("utf-8"),
            voiceover_frame_count=9_600,
        )

    overrun = PresentationEventTrack(
        sample_rate=RATE,
        events=(
            _source(0, 0, 100),
            _source(4_801, 1, 200),
        ),
    )
    with pytest.raises(PresentationSequenceError, match="extends beyond"):
        CompositionSequenceInputs(
            voiceover_audio=_wav(4_800),
            presentation_event_track=overrun.to_json().encode("utf-8"),
            voiceover_frame_count=4_800,
        )

    with pytest.raises(PresentationSequenceError, match="waveform length"):
        CompositionSequenceInputs(
            voiceover_audio=_wav(4_800),
            presentation_event_track=track.to_json().encode("utf-8"),
            voiceover_frame_count=4_800,
            voiceover_waveform=(0.2,),
        )
