"""Immutable Voiceover presentation events on the audio-frame clock.

These records capture inputs, not pixels.  Export can replay source timestamps
against variable-frame-rate footage and film-coordinate telestration at any
output resolution.  The audio frame is the event clock; wall time is never
used and therefore cannot drift away from narration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
from typing import Any

from tapesift.models.telestration import INK, MarkKind


class PresentationEventKind(str, Enum):
    SOURCE_POSITION = "source_position"
    TELESTRATION_SNAPSHOT = "telestration_snapshot"


class SourcePositionTransition(str, Enum):
    """How a newly presented source frame relates to the prior frame.

    Voiceover records the frame that was actually painted, not a requested
    QMediaPlayer position. Export holds every delivered PTS until the next one;
    this transition is retained as provenance rather than interpolation input.
    ANCHOR identifies the already-presented frame at audio frame zero.
    """

    ANCHOR = "anchor"
    CONTINUOUS = "continuous"
    HARD_SEEK = "hard_seek"


_TRACK_KEYS = frozenset({"version", "sample_rate", "events"})
_EVENT_KEYS = frozenset({"audio_frame", "sequence", "kind", "payload"})
_SOURCE_POSITION_KEYS_LEGACY = frozenset({"source_position_ms"})
_SOURCE_POSITION_KEYS = frozenset({"source_position_ms", "transition"})
_TELESTRATION_KEYS = frozenset({"marks"})
_MARK_KEYS = frozenset({"id", "kind", "ink", "width", "points"})


def _require_exact_keys(
        value: dict[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has unknown or missing fields")


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def validate_telestration_snapshot(marks: Any) -> None:
    """Validate the exact shape emitted by ``marks_to_json`` without Qt.

    The accepted point-count contract deliberately matches TapeSift's
    persisted mark model: legacy one-sample freehand marks remain valid, and
    geometric marks may retain more than two historical samples even though
    the renderer uses their first and last points.  Recording an edit must
    never fail merely because another accepted legacy mark is still present.
    """
    if not isinstance(marks, list):
        raise ValueError("Telestration event needs a list of mark objects")
    for mark in marks:
        if not isinstance(mark, dict):
            raise ValueError(
                "Telestration event needs a list of mark objects")
        _require_exact_keys(mark, _MARK_KEYS, "Telestration mark")
        if not isinstance(mark.get("id"), str) or not mark["id"].strip():
            raise ValueError("Telestration mark needs a non-empty id")
        try:
            kind = MarkKind(mark.get("kind"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Telestration mark kind is invalid") from exc
        if mark.get("ink") not in INK:
            raise ValueError("Telestration mark ink is invalid")
        width = mark.get("width")
        numeric_width = _finite_number(width)
        if numeric_width is None or numeric_width <= 0.0:
            raise ValueError("Telestration mark width is invalid")
        points = mark.get("points")
        minimum = 1 if kind == MarkKind.FREEHAND else 2
        if not isinstance(points, list) or len(points) < minimum:
            raise ValueError(
                "Telestration mark has the wrong number of points")
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError(
                    "Telestration mark point must contain x and y")
            for coordinate in point:
                numeric_coordinate = _finite_number(coordinate)
                if numeric_coordinate is None \
                        or not 0.0 <= numeric_coordinate <= 1.0:
                    raise ValueError(
                        "Telestration coordinates must be finite film "
                        "fractions")


def _canonical_payload(payload: Any) -> str:
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Presentation event payload must be finite JSON data") from exc
    # Require an object so every event can grow named fields without changing
    # the surrounding track schema.
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError("Presentation event payload must be a JSON object")
    return encoded


@dataclass(frozen=True)
class PresentationEvent:
    """One ordered input event stamped at a captured audio frame."""

    audio_frame: int
    sequence: int
    kind: PresentationEventKind
    payload_json: str

    def __post_init__(self) -> None:
        if isinstance(self.audio_frame, bool) \
                or not isinstance(self.audio_frame, int) \
                or self.audio_frame < 0:
            raise ValueError(
                "Presentation audio frame must be a non-negative integer")
        if isinstance(self.sequence, bool) \
                or not isinstance(self.sequence, int) or self.sequence < 0:
            raise ValueError(
                "Presentation event sequence must be a non-negative integer")
        try:
            kind = PresentationEventKind(self.kind)
        except (TypeError, ValueError) as exc:
            raise ValueError("Unknown presentation event kind") from exc
        try:
            payload = json.loads(self.payload_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "Presentation event payload must be valid JSON") from exc
        canonical = _canonical_payload(payload)
        if kind == PresentationEventKind.SOURCE_POSITION:
            if "source_frame" in payload:
                raise ValueError(
                    "Source-position events cannot use source_frame")
            # Tracks recorded before delivered-frame timing carried only the
            # millisecond timestamp.  Keep reading those immutable projects;
            # all newly recorded events carry an explicit transition.
            if set(payload) not in (
                    _SOURCE_POSITION_KEYS_LEGACY, _SOURCE_POSITION_KEYS):
                raise ValueError(
                    "Source-position event has unknown or missing fields")
            position = payload.get("source_position_ms")
            if isinstance(position, bool) or not isinstance(position, int) \
                    or position < 0:
                raise ValueError(
                    "Source-position event needs a non-negative millisecond "
                    "timestamp")
            if set(payload) == _SOURCE_POSITION_KEYS:
                try:
                    transition = SourcePositionTransition(
                        payload.get("transition"))
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "Source-position transition is invalid") from exc
                if transition is SourcePositionTransition.ANCHOR \
                        and self.audio_frame != 0:
                    raise ValueError(
                        "Source-position anchor must be at audio frame zero")
        elif kind == PresentationEventKind.TELESTRATION_SNAPSHOT:
            _require_exact_keys(
                payload, _TELESTRATION_KEYS, "Telestration event")
            validate_telestration_snapshot(payload.get("marks"))
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload_json", canonical)

    @classmethod
    def create(
            cls, *, audio_frame: int, sequence: int,
            kind: PresentationEventKind, payload: dict[str, Any]
    ) -> "PresentationEvent":
        return cls(
            audio_frame=audio_frame,
            sequence=sequence,
            kind=PresentationEventKind(kind),
            payload_json=_canonical_payload(payload),
        )

    @property
    def payload(self) -> dict[str, Any]:
        # A new object preserves the event's immutability even if a caller
        # mutates the returned dictionary or one of its nested values.
        return json.loads(self.payload_json)

    def to_dict(self) -> dict[str, Any]:
        return {
            "audio_frame": self.audio_frame,
            "sequence": self.sequence,
            "kind": self.kind.value,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PresentationEvent":
        if not isinstance(value, dict):
            raise ValueError("Presentation event must be a JSON object")
        _require_exact_keys(value, _EVENT_KEYS, "Presentation event")
        return cls.create(
            audio_frame=value["audio_frame"],
            sequence=value["sequence"],
            kind=PresentationEventKind(value["kind"]),
            payload=value["payload"],
        )


@dataclass(frozen=True)
class PresentationEventTrack:
    """A deterministic, serializable event sequence for one narration take."""

    sample_rate: int
    events: tuple[PresentationEvent, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.sample_rate, bool) \
                or not isinstance(self.sample_rate, int) \
                or self.sample_rate <= 0:
            raise ValueError(
                "Presentation track sample rate must be a positive integer")
        if any(not isinstance(event, PresentationEvent)
               for event in self.events):
            raise ValueError(
                "Presentation track events must be PresentationEvent values")
        ordered = tuple(sorted(
            self.events,
            key=lambda event: (event.audio_frame, event.sequence),
        ))
        if len({event.sequence for event in ordered}) != len(ordered):
            raise ValueError("Presentation event sequences must be unique")
        object.__setattr__(self, "events", ordered)

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": 1,
                "sample_rate": self.sample_rate,
                "events": [event.to_dict() for event in self.events],
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str) -> "PresentationEventTrack":
        try:
            decoded = json.loads(value)
            if not isinstance(decoded, dict):
                raise ValueError("Invalid presentation event track")
            _require_exact_keys(
                decoded, _TRACK_KEYS, "Presentation event track")
            version = decoded.get("version")
            if isinstance(version, bool) or not isinstance(version, int) \
                    or version != 1:
                raise ValueError("Unsupported presentation track version")
            raw_events = decoded.get("events", ())
            if not isinstance(raw_events, list):
                raise ValueError("Invalid presentation event track")
            events = tuple(PresentationEvent.from_dict(event)
                           for event in raw_events)
            return cls(sample_rate=decoded["sample_rate"], events=events)
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid presentation event track") from exc
