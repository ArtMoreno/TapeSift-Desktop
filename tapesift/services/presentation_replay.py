"""Deterministic replay of recorded Voiceover presentation inputs.

The Voiceover event track is stamped on the canonical audio-frame clock.  This
module resolves that sparse event stream into one immutable state per output
video frame without touching playback, Qt, FFmpeg, or the live project model.

Source positions are the PTS values of images the live widget actually painted.
Replay is therefore stepwise: each delivered source frame is held until the
next delivered event.  It never interpolates between samples, because doing so
would invent film the analyst did not see even when the average delta happens
to resemble a protected J/K/L rate.  Legacy tracks use the same safe rule.
Telestration snapshots are never interpolated; the latest accepted full
snapshot at the output audio time is authoritative.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction
import json
from typing import Iterator

from tapesift.models.presentation_track import (
    PresentationEventKind,
    PresentationEventTrack,
    validate_telestration_snapshot,
)


__all__ = (
    "PresentationReplay",
    "PresentationReplayError",
    "PresentationReplayFrame",
)


_CANONICAL_SAMPLE_RATE = 48_000


class PresentationReplayError(ValueError):
    """The immutable recording inputs cannot produce a faithful replay."""


@dataclass(frozen=True, slots=True)
class PresentationReplayFrame:
    """Resolved inputs for one output frame on the narration clock."""

    index: int
    audio_frame: int
    source_position_ms: int
    marks_json: bytes

    def __post_init__(self) -> None:
        for name in ("index", "audio_frame", "source_position_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise PresentationReplayError(
                    f"Replay {name} must be a non-negative integer."
                )
        if not isinstance(self.marks_json, bytes):
            raise PresentationReplayError(
                "Replay telestration must be immutable canonical bytes."
            )


@dataclass(frozen=True, slots=True)
class _PositionSample:
    audio_frame: int
    source_position_ms: int


@dataclass(frozen=True, slots=True)
class _MarksSample:
    audio_frame: int
    sequence: int
    canonical_json: bytes


class PresentationReplay:
    """Resolve a frozen PresentationEventTrack at a fixed output frame rate."""

    def __init__(
        self,
        track: PresentationEventTrack,
        *,
        voiceover_frame_count: int,
        fps: int | Fraction = 30,
    ) -> None:
        if not isinstance(track, PresentationEventTrack):
            raise PresentationReplayError(
                "A validated PresentationEventTrack is required."
            )
        if track.sample_rate != _CANONICAL_SAMPLE_RATE:
            raise PresentationReplayError(
                "Presentation replay requires the canonical 48 kHz clock."
            )
        if isinstance(voiceover_frame_count, bool) \
                or not isinstance(voiceover_frame_count, int) \
                or voiceover_frame_count <= 0:
            raise PresentationReplayError(
                "Voiceover frame count must be a positive integer."
            )
        rate = _coerce_fps(fps)
        if track.events and track.events[-1].audio_frame > voiceover_frame_count:
            raise PresentationReplayError(
                "Presentation event track extends beyond the Voiceover audio."
            )

        positions = _position_samples(track)
        if not positions or positions[0].audio_frame != 0:
            raise PresentationReplayError(
                "Presentation replay needs a source-position event at audio frame 0."
            )
        marks = _marks_samples(track)

        self._track = track
        self._voiceover_frame_count = voiceover_frame_count
        self._fps = rate
        self._positions = positions
        self._position_frames = tuple(sample.audio_frame for sample in positions)
        self._marks = marks
        self._marks_frames = tuple(sample.audio_frame for sample in marks)
        self._empty_marks = b"[]"

    @classmethod
    def from_json_bytes(
        cls,
        value: bytes,
        *,
        voiceover_frame_count: int,
        fps: int | Fraction = 30,
    ) -> "PresentationReplay":
        if not isinstance(value, bytes):
            raise PresentationReplayError(
                "Presentation event track must be immutable UTF-8 bytes."
            )
        try:
            track = PresentationEventTrack.from_json(value.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise PresentationReplayError(
                f"Presentation event track is invalid: {exc}."
            ) from exc
        return cls(
            track,
            voiceover_frame_count=voiceover_frame_count,
            fps=fps,
        )

    @property
    def sample_rate(self) -> int:
        return self._track.sample_rate

    @property
    def voiceover_frame_count(self) -> int:
        return self._voiceover_frame_count

    @property
    def fps(self) -> Fraction:
        return self._fps

    @property
    def output_frame_count(self) -> int:
        numerator = self._voiceover_frame_count * self._fps.numerator
        denominator = self.sample_rate * self._fps.denominator
        return (numerator + denominator - 1) // denominator

    def audio_frame_for_index(self, index: int) -> int:
        if isinstance(index, bool) or not isinstance(index, int) \
                or index < 0 or index >= self.output_frame_count:
            raise IndexError("Replay frame index is outside the output duration.")
        numerator = index * self.sample_rate * self._fps.denominator
        return min(
            self._voiceover_frame_count,
            numerator // self._fps.numerator,
        )

    def frame(self, index: int) -> PresentationReplayFrame:
        audio_frame = self.audio_frame_for_index(index)
        return PresentationReplayFrame(
            index=index,
            audio_frame=audio_frame,
            source_position_ms=self.source_position_at(audio_frame),
            marks_json=self.marks_at(audio_frame),
        )

    def frames(self) -> Iterator[PresentationReplayFrame]:
        for index in range(self.output_frame_count):
            yield self.frame(index)

    def source_position_at(self, audio_frame: int) -> int:
        _validate_audio_frame(audio_frame, self._voiceover_frame_count)
        index = bisect_right(self._position_frames, audio_frame) - 1
        return self._positions[max(0, index)].source_position_ms

    def marks_at(self, audio_frame: int) -> bytes:
        _validate_audio_frame(audio_frame, self._voiceover_frame_count)
        index = bisect_right(self._marks_frames, audio_frame) - 1
        if index < 0:
            return self._empty_marks
        return self._marks[index].canonical_json

def _coerce_fps(value: int | Fraction) -> Fraction:
    if isinstance(value, bool):
        raise PresentationReplayError("Output frame rate must be positive.")
    try:
        rate = value if isinstance(value, Fraction) else Fraction(value)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise PresentationReplayError(
            "Output frame rate must be a positive rational value."
        ) from exc
    if rate <= 0:
        raise PresentationReplayError("Output frame rate must be positive.")
    return rate


def _position_samples(
    track: PresentationEventTrack,
) -> tuple[_PositionSample, ...]:
    # Multiple source updates can legitimately share one drained audio frame.
    # Event sequence is deterministic; the last update in that frame wins.
    by_frame: dict[int, _PositionSample] = {}
    for event in track.events:
        if event.kind is not PresentationEventKind.SOURCE_POSITION:
            continue
        payload = event.payload
        by_frame[event.audio_frame] = _PositionSample(
            audio_frame=event.audio_frame,
            source_position_ms=int(payload["source_position_ms"]),
        )
    return tuple(by_frame[key] for key in sorted(by_frame))


def _marks_samples(track: PresentationEventTrack) -> tuple[_MarksSample, ...]:
    samples: list[_MarksSample] = []
    for event in track.events:
        if event.kind is not PresentationEventKind.TELESTRATION_SNAPSHOT:
            continue
        marks = event.payload["marks"]
        validate_telestration_snapshot(marks)
        canonical = json.dumps(
            marks,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        samples.append(_MarksSample(
            audio_frame=event.audio_frame,
            sequence=event.sequence,
            canonical_json=canonical,
        ))
    return tuple(samples)


def _validate_audio_frame(audio_frame: int, frame_count: int) -> None:
    if isinstance(audio_frame, bool) or not isinstance(audio_frame, int) \
            or audio_frame < 0 or audio_frame > frame_count:
        raise PresentationReplayError(
            "Replay audio frame is outside the Voiceover duration."
        )
