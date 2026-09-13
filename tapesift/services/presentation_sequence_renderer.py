"""Frame-by-frame Signature/Vertical composition from recorded inputs.

This is the renderer-facing half of final export.  It deliberately owns no
video decoder and no encoder: a source-frame loader resolves each VFR-safe
source timestamp, and the caller consumes rendered QImages.  Keeping those I/O
boundaries explicit lets the existing export worker supply FFmpeg processes
without coupling the immutable replay contract to subprocess lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
import struct
from typing import Callable, Iterator

from PySide6.QtGui import QImage

from tapesift.models.composition_plan import (
    CompositionLayerKind,
    CompositionPlan,
)
from tapesift.services.presentation_replay import (
    PresentationReplay,
    PresentationReplayError,
    PresentationReplayFrame,
)
from tapesift.models.presentation_track import PresentationEventTrack
from tapesift.services.preview_compositor import (
    CompositionAssetPayload,
    CompositionFrameCompositor,
    CompositionFramePayloads,
    DecodedSourceFrame,
    PreviewCompositorError,
)


__all__ = (
    "CompositionSequenceInputs",
    "CompositionSequenceRenderer",
    "PresentationSequenceCancelled",
    "PresentationSequenceError",
    "RenderedPresentationFrame",
)


SourceFrameLoader = Callable[[int], bytes | DecodedSourceFrame]
ProgressCallback = Callable[[int, int], None]
CancellationCheck = Callable[[], bool]
_CANONICAL_SAMPLE_RATE = 48_000
_WAVEFORM_SAMPLE_RATE = 100


class PresentationSequenceError(ValueError):
    """A recorded sequence could not be resolved or composed."""


class PresentationSequenceCancelled(RuntimeError):
    """The caller cancelled sequence rendering between output frames."""


@dataclass(frozen=True, slots=True)
class CompositionSequenceInputs:
    """Immutable inputs shared by every frame in one composited job."""

    voiceover_audio: bytes | None = None
    presentation_event_track: bytes | None = None
    voiceover_frame_count: int | None = None
    voiceover_waveform: tuple[float, ...] | None = None
    clip_start_ms: int | None = None
    clip_end_ms: int | None = None
    clip_ink_event_track: bytes | None = None
    assets: tuple[CompositionAssetPayload, ...] = ()
    play_call_situation: bytes | None = None
    play_call_concept: bytes | None = None
    play_call_result: bytes | None = None

    def __post_init__(self) -> None:
        voiceover_values = (
            self.voiceover_audio,
            self.presentation_event_track,
            self.voiceover_frame_count,
        )
        has_voiceover = any(value is not None for value in voiceover_values)
        if has_voiceover:
            if not isinstance(self.voiceover_audio, bytes) \
                    or not isinstance(self.presentation_event_track, bytes):
                raise PresentationSequenceError(
                    "Voiceover audio and event track must be immutable bytes."
                )
            if isinstance(self.voiceover_frame_count, bool) \
                    or not isinstance(self.voiceover_frame_count, int) \
                    or self.voiceover_frame_count <= 0:
                raise PresentationSequenceError(
                    "Voiceover frame count must be a positive integer."
                )
            if any(value is not None for value in (
                    self.clip_start_ms,
                    self.clip_end_ms,
                    self.clip_ink_event_track,
            )):
                raise PresentationSequenceError(
                    "Voiceover replay cannot be combined with frozen clip inputs."
                )
            _validate_voiceover_payload(
                self.voiceover_audio,
                self.presentation_event_track,
                self.voiceover_frame_count,
            )
        else:
            _validate_clip_range(self.clip_start_ms, self.clip_end_ms)
            if self.clip_ink_event_track is not None \
                    and not isinstance(self.clip_ink_event_track, bytes):
                raise PresentationSequenceError(
                    "Frozen clip ink must be immutable bytes when supplied."
                )
        if self.voiceover_waveform is not None:
            if not has_voiceover:
                raise PresentationSequenceError(
                    "A stored Voiceover waveform requires Voiceover audio."
                )
            if not isinstance(self.voiceover_waveform, tuple):
                raise PresentationSequenceError(
                    "Voiceover waveform must be an immutable tuple."
                )
            for value in self.voiceover_waveform:
                if isinstance(value, bool) \
                        or not isinstance(value, (int, float)) \
                        or not math.isfinite(float(value)) \
                        or not 0.0 <= float(value) <= 1.0:
                    raise PresentationSequenceError(
                        "Voiceover waveform values must be finite from 0 to 1."
                    )
            assert self.voiceover_frame_count is not None
            expected_buckets = (
                self.voiceover_frame_count + (_CANONICAL_SAMPLE_RATE // 100) - 1
            ) // (_CANONICAL_SAMPLE_RATE // 100)
            if len(self.voiceover_waveform) != expected_buckets:
                raise PresentationSequenceError(
                    "Stored Voiceover waveform length differs from the "
                    "immutable 100 Hz take envelope."
                )
        try:
            assets = tuple(self.assets)
        except TypeError as exc:
            raise PresentationSequenceError(
                "Composition assets must be an immutable sequence."
            ) from exc
        if any(not isinstance(asset, CompositionAssetPayload)
               for asset in assets):
            raise PresentationSequenceError(
                "Composition assets must be immutable asset payloads."
            )
        object.__setattr__(self, "assets", assets)
        for field_name in (
            "play_call_situation",
            "play_call_concept",
            "play_call_result",
        ):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, bytes):
                raise PresentationSequenceError(
                    f"{field_name} must be immutable bytes when supplied."
                )

    @property
    def has_voiceover(self) -> bool:
        return self.voiceover_audio is not None


@dataclass(frozen=True, slots=True)
class RenderedPresentationFrame:
    index: int
    audio_frame: int
    source_position_ms: int
    image: QImage

    def __post_init__(self) -> None:
        if self.image.isNull():
            raise PresentationSequenceError(
                "Compositor returned a null output frame."
            )


class CompositionSequenceRenderer:
    """Resolve recorded events and compose output frames in audio order."""

    def __init__(
        self,
        plan: CompositionPlan,
        inputs: CompositionSequenceInputs,
        source_frame_loader: SourceFrameLoader,
        *,
        fps: int = 30,
        compositor: CompositionFrameCompositor | None = None,
    ) -> None:
        if not isinstance(plan, CompositionPlan):
            raise PresentationSequenceError(
                "A validated CompositionPlan is required."
            )
        errors = plan.validation_errors()
        if errors:
            raise PresentationSequenceError(
                "Composition plan is invalid: " + "; ".join(errors)
            )
        if not isinstance(inputs, CompositionSequenceInputs):
            raise PresentationSequenceError(
                "CompositionSequenceInputs are required."
            )
        if not callable(source_frame_loader):
            raise PresentationSequenceError(
                "A VFR-safe source-frame loader is required."
            )
        has_waveform = plan.layer(CompositionLayerKind.WAVEFORM) is not None
        if has_waveform is not inputs.has_voiceover:
            raise PresentationSequenceError(
                "Composition plan Voiceover layers do not match sequence inputs."
            )
        try:
            replay = (
                PresentationReplay.from_json_bytes(
                    inputs.presentation_event_track,
                    voiceover_frame_count=inputs.voiceover_frame_count,
                    fps=fps,
                )
                if inputs.has_voiceover
                else None
            )
        except PresentationReplayError as exc:
            raise PresentationSequenceError(str(exc)) from exc

        rate = Fraction(fps)
        if rate <= 0:
            raise PresentationSequenceError(
                "Output frame rate must be positive."
            )
        has_ink = plan.layer(CompositionLayerKind.INK) is not None
        if not inputs.has_voiceover:
            if has_ink and inputs.clip_ink_event_track is None:
                raise PresentationSequenceError(
                    "Frozen clip ink is required by the composition plan."
                )
            if not has_ink and inputs.clip_ink_event_track is not None:
                raise PresentationSequenceError(
                    "Frozen clip ink was supplied for a plan without ink."
                )

        self.plan = plan
        self.inputs = inputs
        self.source_frame_loader = source_frame_loader
        self.replay = replay
        self._fps = rate
        self.compositor = compositor or CompositionFrameCompositor()
        self._has_ink = has_ink

    @property
    def frame_count(self) -> int:
        if self.replay is not None:
            return self.replay.output_frame_count
        assert self.inputs.clip_start_ms is not None
        assert self.inputs.clip_end_ms is not None
        duration_ms = self.inputs.clip_end_ms - self.inputs.clip_start_ms
        numerator = duration_ms * self._fps.numerator
        denominator = 1_000 * self._fps.denominator
        return (numerator + denominator - 1) // denominator

    def source_positions(self) -> tuple[int, ...]:
        """Return the immutable request order used to batch exact PTS decode."""

        if self.replay is not None:
            return tuple(
                state.source_position_ms for state in self.replay.frames()
            )
        assert self.inputs.clip_start_ms is not None
        assert self.inputs.clip_end_ms is not None
        return tuple(
            min(
                self.inputs.clip_end_ms - 1,
                self.inputs.clip_start_ms
                + (index * 1_000 * self._fps.denominator)
                // self._fps.numerator,
            )
            for index in range(self.frame_count)
        )

    def frames(
        self,
        *,
        on_progress: ProgressCallback | None = None,
        is_cancelled: CancellationCheck | None = None,
    ) -> Iterator[RenderedPresentationFrame]:
        total = self.frame_count
        try:
            for state in self._states():
                if is_cancelled is not None and is_cancelled():
                    raise PresentationSequenceCancelled(
                        "Presentation export was cancelled."
                    )
                try:
                    source_frame = self.source_frame_loader(
                        state.source_position_ms
                    )
                except Exception as exc:
                    raise PresentationSequenceError(
                        "Source frame could not be loaded at "
                        f"{state.source_position_ms} ms for output frame "
                        f"{state.index}: {exc}"
                    ) from exc
                if not isinstance(source_frame, (bytes, DecodedSourceFrame)):
                    raise PresentationSequenceError(
                        "Source-frame loader must return immutable encoded "
                        "bytes or decoded RGBA."
                    )

                payloads = CompositionFramePayloads(
                    source_video=source_frame,
                    assets=self.inputs.assets,
                    ink_event_track=(
                        state.marks_json if self._has_ink else None
                    ),
                    voiceover_audio=(
                        self.inputs.voiceover_audio
                        if self.inputs.voiceover_waveform is None else None
                    ),
                    presentation_event_track=(
                        self.inputs.presentation_event_track
                        if self.inputs.voiceover_waveform is None else None
                    ),
                    voiceover_waveform=self.inputs.voiceover_waveform,
                    voiceover_waveform_rate_hz=(
                        _WAVEFORM_SAMPLE_RATE
                        if self.inputs.voiceover_waveform is not None else None
                    ),
                    play_call_situation=self.inputs.play_call_situation,
                    play_call_concept=self.inputs.play_call_concept,
                    play_call_result=self.inputs.play_call_result,
                    timeline_position=state.audio_frame,
                    timeline_duration=(
                        self.inputs.voiceover_frame_count
                        if self.inputs.has_voiceover
                        else self.inputs.clip_end_ms - self.inputs.clip_start_ms
                    ),
                )
                try:
                    image = self.compositor.render(self.plan, payloads)
                except PreviewCompositorError as exc:
                    raise PresentationSequenceError(
                        f"Output frame {state.index} could not be composed: {exc}"
                    ) from exc
                yield RenderedPresentationFrame(
                    index=state.index,
                    audio_frame=state.audio_frame,
                    source_position_ms=state.source_position_ms,
                    image=image,
                )
                if on_progress is not None:
                    on_progress(state.index + 1, total)
        finally:
            self.compositor.clear_cache()

    def _states(self) -> Iterator[PresentationReplayFrame]:
        if self.replay is not None:
            yield from self.replay.frames()
            return
        assert self.inputs.clip_start_ms is not None
        assert self.inputs.clip_end_ms is not None
        marks = self.inputs.clip_ink_event_track or b"[]"
        for index, position in enumerate(self.source_positions()):
            elapsed_ms = position - self.inputs.clip_start_ms
            yield PresentationReplayFrame(
                index=index,
                # No-Voiceover rendering has no audio clock.  Keep the public
                # field deterministic by carrying elapsed clip milliseconds.
                audio_frame=elapsed_ms,
                source_position_ms=position,
                marks_json=marks,
            )


def _validate_clip_range(start_ms: int | None, end_ms: int | None) -> None:
    for label, value in (("start", start_ms), ("end", end_ms)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PresentationSequenceError(
                f"Frozen clip {label} must be a non-negative integer."
            )
    assert start_ms is not None and end_ms is not None
    if end_ms <= start_ms:
        raise PresentationSequenceError(
            "Frozen clip end must be after its start."
        )


def _validate_voiceover_payload(
    wav_data: bytes,
    event_track_data: bytes,
    declared_frame_count: int,
) -> None:
    """Validate canonical take metadata without scanning/copying PCM."""

    actual_frame_count = _canonical_wav_frame_count(wav_data)
    if actual_frame_count != declared_frame_count:
        raise PresentationSequenceError(
            "Voiceover WAV frame count differs from the immutable take metadata."
        )
    try:
        track = PresentationEventTrack.from_json(
            event_track_data.decode("utf-8")
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise PresentationSequenceError(
            f"Presentation event track is invalid: {exc}."
        ) from exc
    if track.sample_rate != _CANONICAL_SAMPLE_RATE:
        raise PresentationSequenceError(
            "Presentation event track must use the canonical 48 kHz clock."
        )
    if track.events and track.events[-1].audio_frame > actual_frame_count:
        raise PresentationSequenceError(
            "Presentation event track extends beyond the Voiceover audio."
        )


def _canonical_wav_frame_count(data: bytes) -> int:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise PresentationSequenceError(
            "Voiceover audio is not a valid RIFF/WAVE payload."
        )
    declared_size = struct.unpack_from("<I", data, 4)[0] + 8
    if declared_size != len(data):
        raise PresentationSequenceError(
            "Voiceover WAV is truncated or contains undeclared trailing bytes."
        )
    offset = 12
    format_values: tuple[int, int, int, int, int, int] | None = None
    data_size: int | None = None
    while offset < len(data):
        if offset + 8 > len(data):
            raise PresentationSequenceError("Voiceover WAV chunk header is truncated.")
        chunk_id = data[offset:offset + 4]
        chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        if payload_end > len(data):
            raise PresentationSequenceError("Voiceover WAV chunk is truncated.")
        if chunk_id == b"fmt ":
            if format_values is not None or chunk_size < 16:
                raise PresentationSequenceError("Voiceover WAV format chunk is invalid.")
            format_values = struct.unpack_from("<HHIIHH", data, payload_start)
        elif chunk_id == b"data":
            if data_size is not None:
                raise PresentationSequenceError("Voiceover WAV has duplicate data chunks.")
            data_size = chunk_size
        offset = payload_end + (chunk_size & 1)
    if offset != len(data) or format_values is None or data_size is None:
        raise PresentationSequenceError("Voiceover WAV is missing canonical chunks.")
    audio_format, channels, rate, byte_rate, block_align, bits = format_values
    if (
        audio_format != 1
        or channels != 1
        or rate != _CANONICAL_SAMPLE_RATE
        or byte_rate != _CANONICAL_SAMPLE_RATE * 2
        or block_align != 2
        or bits != 16
        or data_size % block_align
    ):
        raise PresentationSequenceError(
            "Voiceover audio must be 48 kHz mono uncompressed PCM16 WAV."
        )
    frame_count = data_size // block_align
    if frame_count <= 0:
        raise PresentationSequenceError("Voiceover WAV must contain audio frames.")
    return frame_count
