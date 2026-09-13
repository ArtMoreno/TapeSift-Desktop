"""Immutable inputs captured when an export job enters the queue.

The live project, selected template, selected Voiceover take, and export panel
can all change after a job is queued.  This module is the boundary that keeps
retry deterministic: it captures the source ranges, canonical Export Package,
locked CompositionPlan, identity bytes, ink, and narration input bytes once.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tapesift.core.exceptions import ExportCancelledError
from tapesift.models.clip import Clip
from tapesift.models.composition_plan import (
    CompositionPlan,
    IdentityAssetRole,
    ImmutableAssetReference,
    build_composition_plan,
)
from tapesift.models.export_package import ExportPackageSnapshot
from tapesift.models.export_settings import ReelSettings
from tapesift.models.presentation_track import (
    PresentationEventKind,
    PresentationEventTrack,
    validate_telestration_snapshot,
)
from tapesift.models.signature_template import (
    ImageAssetSnapshot,
    SignatureTemplate,
)
from tapesift.models.telestration import marks_from_json, marks_to_json
from tapesift.models.voiceover import VoiceoverTake


EXPORT_JOB_SNAPSHOT_SCHEMA = "tapesift.export-job-snapshot"
EXPORT_JOB_SNAPSHOT_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ENCODER_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ExportJobSnapshotValidationError(ValueError):
    """A queued job snapshot is incomplete, mutable, or internally inconsistent."""


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ExportJobSnapshotValidationError(
            "Queued export data must be finite JSON."
        ) from exc


def _expect_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ExportJobSnapshotValidationError(f"{label} must be an object.")
    return value


def _exact_keys(
        value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ExportJobSnapshotValidationError(f"{label} has invalid fields.")


def _input_value(package: ExportPackageSnapshot, name: str) -> str:
    for item in package.compositor_inputs:
        if item.name == name:
            return item.value
    return ""


def _take_id_from_input(value: str, *, suffix: str, label: str) -> str:
    tagged_suffix = f":{suffix}"
    if value.startswith("take:") and value.endswith(tagged_suffix):
        take_id = value[5:-len(tagged_suffix)]
    elif ":" not in value:
        take_id = value
    else:
        take_id = ""
    if not take_id or take_id != take_id.strip() or ":" in take_id:
        raise ExportJobSnapshotValidationError(
            f"Voiceover {label} input does not identify one frozen take."
        )
    return take_id


def frozen_voiceover_take_id(package: ExportPackageSnapshot) -> str:
    """Resolve one exact take from every enabled Voiceover logical input."""

    if not isinstance(package, ExportPackageSnapshot) \
            or not package.include_voiceover:
        raise ExportJobSnapshotValidationError(
            "A Voiceover package is required to resolve its frozen take."
        )
    audio_id = _take_id_from_input(
        _input_value(package, "voiceover_audio"),
        suffix="audio",
        label="audio",
    )
    event_id = _take_id_from_input(
        _input_value(package, "presentation_event_track"),
        suffix="events",
        label="presentation-event",
    )
    if audio_id != event_id:
        raise ExportJobSnapshotValidationError(
            "Voiceover audio and presentation-event inputs must identify "
            "the same frozen take."
        )
    if package.include_ink:
        ink_id = _take_id_from_input(
            _input_value(package, "ink_event_track"),
            suffix="ink",
            label="ink",
        )
        if ink_id != audio_id:
            raise ExportJobSnapshotValidationError(
                "Voiceover ink input must identify the same frozen take as "
                "audio and events."
            )
    return audio_id


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ExportCancelledError("Source identity capture was cancelled.")


def _source_file_identity(
        source_video_path: str,
        *,
        cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    """Read one stable source revision for deterministic retry validation."""

    path = Path(source_video_path)
    try:
        _raise_if_cancelled(cancel_event)
        before = path.stat()
        if not path.is_file():
            raise OSError("source is not a regular file")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while True:
                _raise_if_cancelled(cancel_event)
                chunk = stream.read(8 * 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        _raise_if_cancelled(cancel_event)
        after = path.stat()
    except OSError as exc:
        raise ExportJobSnapshotValidationError(
            f"Queued source video cannot be read: {path}."
        ) from exc
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise ExportJobSnapshotValidationError(
            "Queued source video changed while its identity was captured."
        )
    return after.st_size, digest.hexdigest()


@dataclass(frozen=True, slots=True)
class SourceMediaIdentity:
    """Reusable full-file identity for every job in one enqueue batch."""

    source_video_path: str
    file_size: int
    sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_video_path, str) \
                or not self.source_video_path.strip() \
                or self.source_video_path != self.source_video_path.strip() \
                or not Path(self.source_video_path).is_absolute():
            raise ExportJobSnapshotValidationError(
                "Source media identity requires one canonical absolute path."
            )
        if isinstance(self.file_size, bool) or not isinstance(
                self.file_size, int) or self.file_size <= 0:
            raise ExportJobSnapshotValidationError(
                "Source media identity has an invalid file size."
            )
        if not isinstance(self.sha256, str) \
                or not _SHA256_PATTERN.fullmatch(self.sha256):
            raise ExportJobSnapshotValidationError(
                "Source media identity has an invalid digest."
            )

    @classmethod
    def capture(
            cls,
            source_video_path: str | Path,
            *,
            cancel_event: threading.Event | None = None,
    ) -> "SourceMediaIdentity":
        canonical_path = str(source_video_path)
        file_size, sha256 = _source_file_identity(
            canonical_path, cancel_event=cancel_event)
        return cls(canonical_path, file_size, sha256)

    def verify(
            self, *, cancel_event: threading.Event | None = None) -> None:
        file_size, sha256 = _source_file_identity(
            self.source_video_path, cancel_event=cancel_event)
        if file_size != self.file_size or sha256 != self.sha256:
            raise ExportJobSnapshotValidationError(
                "Queued source video changed after this export entered the queue."
            )


@dataclass(frozen=True, slots=True)
class ExportClipSnapshot:
    """The only clip state the renderer is allowed to use on retry."""

    clip_id: str
    start_ms: int
    end_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.clip_id, str) or not self.clip_id.strip():
            raise ExportJobSnapshotValidationError(
                "Export clip snapshot requires a clip id."
            )
        if self.clip_id != self.clip_id.strip():
            raise ExportJobSnapshotValidationError(
                "Export clip id cannot contain surrounding whitespace."
            )
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (self.start_ms, self.end_ms)
        ):
            raise ExportJobSnapshotValidationError(
                "Export clip timestamps must be integers."
            )
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ExportJobSnapshotValidationError(
                "Export clip snapshot requires a positive source range."
            )

    @classmethod
    def capture(cls, clip: Clip) -> "ExportClipSnapshot":
        if not isinstance(clip, Clip):
            raise ExportJobSnapshotValidationError(
                "Only a TapeSift clip can be captured for export."
            )
        return cls(clip.id, clip.start_ms, clip.end_ms)

    def to_clip(self) -> Clip:
        """Materialize an isolated range for the existing technical exporter."""

        return Clip(start_ms=self.start_ms, end_ms=self.end_ms, id=self.clip_id)


@dataclass(frozen=True, slots=True)
class ExportReelSettingsSnapshot:
    """Frozen form of the legacy technical reel controls."""

    fade_in: bool = False
    fade_out: bool = False
    fade_duration: float = 0.5
    gap_seconds: float = 0.0
    title_card_text: str = ""
    title_card_seconds: float = 3.0
    show_clip_labels: bool = False

    def __post_init__(self) -> None:
        for name in ("fade_in", "fade_out", "show_clip_labels"):
            if type(getattr(self, name)) is not bool:
                raise ExportJobSnapshotValidationError(
                    f"{name} reel setting must be true or false."
                )
        for name in ("fade_duration", "gap_seconds", "title_card_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(float(value)) or float(value) < 0.0:
                raise ExportJobSnapshotValidationError(
                    f"{name} reel setting must be a finite non-negative number."
                )
        if not isinstance(self.title_card_text, str):
            raise ExportJobSnapshotValidationError(
                "title_card_text reel setting must be text."
            )

    @classmethod
    def capture(
            cls, settings: ReelSettings | None
    ) -> "ExportReelSettingsSnapshot":
        settings = settings or ReelSettings()
        return cls(
            fade_in=settings.fade_in,
            fade_out=settings.fade_out,
            fade_duration=settings.fade_duration,
            gap_seconds=settings.gap_seconds,
            title_card_text=settings.title_card_text,
            title_card_seconds=settings.title_card_seconds,
            show_clip_labels=settings.show_clip_labels,
        )

    def to_reel_settings(self) -> ReelSettings:
        return ReelSettings(
            fade_in=self.fade_in,
            fade_out=self.fade_out,
            fade_duration=float(self.fade_duration),
            gap_seconds=float(self.gap_seconds),
            title_card_text=self.title_card_text,
            title_card_seconds=float(self.title_card_seconds),
            show_clip_labels=self.show_clip_labels,
        )


@dataclass(frozen=True, slots=True)
class StagedIdentityAsset:
    """One copied identity image, addressed by the digest of its bytes."""

    role: IdentityAssetRole
    sha256: str
    mime_type: str
    width: int
    height: int
    data: bytes

    def __post_init__(self) -> None:
        try:
            role = IdentityAssetRole(self.role)
        except (TypeError, ValueError) as exc:
            raise ExportJobSnapshotValidationError(
                "Staged identity asset role is invalid."
            ) from exc
        object.__setattr__(self, "role", role)
        if not isinstance(self.data, bytes):
            raise ExportJobSnapshotValidationError(
                "Staged identity image bytes must be immutable."
            )
        try:
            captured = ImageAssetSnapshot(
                data=self.data,
                sha256=self.sha256,
                mime_type=self.mime_type,
                width=self.width,
                height=self.height,
            )
        except ValueError as exc:
            raise ExportJobSnapshotValidationError(
                f"Staged {role.value} image is corrupt."
            ) from exc
        if captured.sha256 != hashlib.sha256(self.data).hexdigest():
            raise ExportJobSnapshotValidationError(
                f"Staged {role.value} digest does not match its bytes."
            )

    @classmethod
    def capture(
            cls, role: IdentityAssetRole,
            asset: ImageAssetSnapshot,
    ) -> "StagedIdentityAsset":
        if not isinstance(asset, ImageAssetSnapshot):
            raise ExportJobSnapshotValidationError(
                "Only validated identity images can be staged."
            )
        return cls(
            role=role,
            sha256=asset.sha256,
            mime_type=asset.mime_type,
            width=asset.width,
            height=asset.height,
            data=bytes(asset.data),
        )

    @property
    def reference(self) -> ImmutableAssetReference:
        return ImmutableAssetReference(
            self.role, self.sha256, self.mime_type, self.width, self.height
        )


class _BytesViewReader:
    """Seekable WAV header reader that never duplicates the full audio BLOB."""

    def __init__(self, value: bytes) -> None:
        self._value = memoryview(value)
        self._position = 0

    def read(self, size: int = -1) -> bytes:
        stop = len(self._value) if size is None or size < 0 else min(
            len(self._value), self._position + size)
        value = self._value[self._position:stop].tobytes()
        self._position = stop
        return value

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            position = offset
        elif whence == io.SEEK_CUR:
            position = self._position + offset
        elif whence == io.SEEK_END:
            position = len(self._value) + offset
        else:
            raise ValueError("Invalid WAV seek mode")
        if position < 0:
            raise ValueError("Cannot seek before WAV start")
        self._position = min(position, len(self._value))
        return self._position

    def tell(self) -> int:
        return self._position


def _canonical_wav_data_size(value: bytes) -> int:
    view = memoryview(value)
    if len(view) < 12 or bytes(view[:4]) != b"RIFF" \
            or bytes(view[8:12]) != b"WAVE":
        raise ExportJobSnapshotValidationError(
            "Staged Voiceover audio is not a WAV file."
        )
    riff_end = int.from_bytes(view[4:8], "little") + 8
    if riff_end != len(view):
        raise ExportJobSnapshotValidationError(
            "Staged Voiceover WAV has an invalid RIFF length."
        )
    offset = 12
    data_sizes: list[int] = []
    while offset < riff_end:
        if offset + 8 > riff_end:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover WAV has a truncated chunk header."
            )
        chunk_id = bytes(view[offset:offset + 4])
        chunk_size = int.from_bytes(view[offset + 4:offset + 8], "little")
        chunk_end = offset + 8 + chunk_size
        next_offset = chunk_end + (chunk_size & 1)
        if chunk_end > riff_end or next_offset > riff_end:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover WAV has a truncated chunk."
            )
        if chunk_id == b"data":
            data_sizes.append(chunk_size)
        offset = next_offset
    if offset != riff_end or len(data_sizes) != 1:
        raise ExportJobSnapshotValidationError(
            "Staged Voiceover WAV needs exactly one data chunk."
        )
    return data_sizes[0]


@dataclass(frozen=True, slots=True)
class StagedVoiceoverPayload:
    """A selected take copied into the job so delete/re-record cannot alter retry."""

    take_id: str
    project_id: int
    clip_id: str
    audio_input_value: str
    presentation_input_value: str
    ink_input_value: str
    source_anchor_ms: int
    sample_rate: int
    channels: int
    frame_count: int
    duration_ms: int
    audio_sha256: str
    presentation_sha256: str
    audio_wav: bytes
    presentation_track_json: str
    waveform: tuple[float, ...]

    def __post_init__(self) -> None:
        for name in (
            "take_id", "clip_id", "audio_input_value",
            "presentation_input_value",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() \
                    or value != value.strip():
                raise ExportJobSnapshotValidationError(
                    f"Staged Voiceover {name} is required and canonical."
                )
        if not isinstance(self.ink_input_value, str) or (
            self.ink_input_value
            and self.ink_input_value != self.ink_input_value.strip()
        ):
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover ink input must be canonical text."
            )
        if isinstance(self.project_id, bool) or not isinstance(
                self.project_id, int) or self.project_id <= 0:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover project id is invalid."
            )
        for name in ("audio_sha256", "presentation_sha256"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
                raise ExportJobSnapshotValidationError(
                    f"Staged Voiceover {name} is invalid."
                )
        if not isinstance(self.audio_wav, bytes):
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover audio bytes must be immutable."
            )
        if hashlib.sha256(self.audio_wav).hexdigest() != self.audio_sha256:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover audio failed its integrity check."
            )
        if not isinstance(self.presentation_track_json, str):
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover presentation track must be JSON text."
            )
        if hashlib.sha256(
            self.presentation_track_json.encode("utf-8")
        ).hexdigest() != self.presentation_sha256:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover presentation track failed its integrity check."
            )
        try:
            waveform = tuple(self.waveform)
        except TypeError as exc:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover waveform must be immutable samples."
            ) from exc
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= 1.0
            for value in waveform
        ):
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover waveform values must be finite from 0 to 1."
            )
        object.__setattr__(
            self, "waveform", tuple(float(value) for value in waveform)
        )
        try:
            track = PresentationEventTrack.from_json(
                self.presentation_track_json)
        except ValueError as exc:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover presentation track is corrupt."
            ) from exc
        for name in (
            "source_anchor_ms", "sample_rate", "channels",
            "frame_count", "duration_ms",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ExportJobSnapshotValidationError(
                    f"Staged Voiceover {name} must be an integer."
                )
        if self.source_anchor_ms < 0 or self.sample_rate != 48_000 \
                or self.channels != 1 or self.frame_count <= 0 \
                or self.duration_ms < 0:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover must be non-empty canonical 48 kHz mono audio."
            )
        expected_waveform_count = (self.frame_count + 479) // 480
        if len(self.waveform) != expected_waveform_count:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover waveform must contain one 100 Hz sample "
                "for every complete or partial 480-frame bucket."
            )
        calculated_duration = (
            self.frame_count * 1000 + self.sample_rate // 2
        ) // self.sample_rate
        if abs(calculated_duration - self.duration_ms) > 1:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover duration differs from its audio clock."
            )
        if track.sample_rate != self.sample_rate or any(
            event.audio_frame > self.frame_count for event in track.events
        ):
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover events differ from the audio clock."
            )
        frame_zero_positions = tuple(
            event for event in track.events
            if event.audio_frame == 0
            and event.kind is PresentationEventKind.SOURCE_POSITION
        )
        if not frame_zero_positions:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover needs a source-position event at audio frame 0."
            )
        # PresentationReplay applies same-frame events in sequence order; the
        # last source update at frame zero is therefore the effective anchor.
        effective_anchor = frame_zero_positions[-1].payload[
            "source_position_ms"]
        if effective_anchor != self.source_anchor_ms:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover frame-zero source anchor differs from its take."
            )
        expected_data_size = self.frame_count * self.channels * 2
        if _canonical_wav_data_size(self.audio_wav) != expected_data_size:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover WAV length differs from its frame count."
            )
        try:
            with wave.open(_BytesViewReader(self.audio_wav), "rb") as reader:
                if reader.getcomptype() != "NONE" or reader.getsampwidth() != 2 \
                        or reader.getframerate() != self.sample_rate \
                        or reader.getnchannels() != self.channels \
                        or reader.getnframes() != self.frame_count:
                    raise ExportJobSnapshotValidationError(
                        "Staged Voiceover WAV format differs from its metadata."
                    )
        except (EOFError, wave.Error) as exc:
            raise ExportJobSnapshotValidationError(
                "Staged Voiceover audio is corrupt."
            ) from exc

    @classmethod
    def capture(
            cls,
            take: VoiceoverTake,
            *,
            package: ExportPackageSnapshot,
    ) -> "StagedVoiceoverPayload":
        if not isinstance(take, VoiceoverTake):
            raise ExportJobSnapshotValidationError(
                "Voiceover staging requires a saved take."
            )
        if take.presentation_track is None:
            raise ExportJobSnapshotValidationError(
                "Voiceover export requires its audio-clock presentation track."
            )
        frozen_take_id = frozen_voiceover_take_id(package)
        if take.id != frozen_take_id:
            raise ExportJobSnapshotValidationError(
                "The selected take differs from the frozen Voiceover take inputs."
            )
        track_json = take.presentation_track.to_json()
        audio = bytes(take.audio_wav)
        return cls(
            take_id=take.id,
            project_id=take.project_id,
            clip_id=take.clip_id,
            audio_input_value=_input_value(package, "voiceover_audio"),
            presentation_input_value=_input_value(
                package, "presentation_event_track"),
            ink_input_value=(
                _input_value(package, "ink_event_track")
                if package.include_ink else ""
            ),
            source_anchor_ms=take.source_anchor_ms,
            sample_rate=take.sample_rate,
            channels=take.channels,
            frame_count=take.frame_count,
            duration_ms=take.duration_ms,
            audio_sha256=hashlib.sha256(audio).hexdigest(),
            presentation_sha256=hashlib.sha256(
                track_json.encode("utf-8")
            ).hexdigest(),
            audio_wav=audio,
            presentation_track_json=track_json,
            waveform=tuple(take.waveform),
        )


def _template_assets(package: ExportPackageSnapshot) -> tuple[StagedIdentityAsset, ...]:
    if package.template is None:
        return ()
    try:
        template = SignatureTemplate.from_json(package.template.payload_json)
    except ValueError as exc:
        raise ExportJobSnapshotValidationError(
            "Queued template snapshot cannot be decoded."
        ) from exc
    identity = template.identity
    pairs = (
        (IdentityAssetRole.BORDER, identity.border),
        (IdentityAssetRole.PROFILE_PHOTO, identity.profile_photo),
        (IdentityAssetRole.WORDMARK_LOGO, identity.wordmark_logo),
    )
    return tuple(
        StagedIdentityAsset.capture(role, asset)
        for role, asset in pairs
        if asset is not None
    )


@dataclass(frozen=True, slots=True)
class ExportJobSnapshot:
    """Everything a retry may consume, independent of current UI state."""

    package: ExportPackageSnapshot
    composition_plan: CompositionPlan
    source_video_path: str
    source_file_size: int
    source_sha256: str
    clips: tuple[ExportClipSnapshot, ...]
    identity_assets: tuple[StagedIdentityAsset, ...] = ()
    voiceover: StagedVoiceoverPayload | None = None
    ink_input_value: str = ""
    ink_event_track_json: str = ""
    hardware_encoder: str = ""
    reel_settings: ExportReelSettingsSnapshot = ExportReelSettingsSnapshot()

    def __post_init__(self) -> None:
        if not isinstance(self.package, ExportPackageSnapshot):
            raise ExportJobSnapshotValidationError(
                "Queued job requires an Export Package snapshot."
            )
        self.package.validate()
        if not isinstance(self.composition_plan, CompositionPlan):
            raise ExportJobSnapshotValidationError(
                "Queued job requires a CompositionPlan."
            )
        plan_errors = self.composition_plan.validation_errors()
        if plan_errors:
            raise ExportJobSnapshotValidationError("; ".join(plan_errors))
        if self.composition_plan.style is not self.package.style:
            raise ExportJobSnapshotValidationError(
                "CompositionPlan style differs from the Export Package."
            )
        try:
            expected_plan = build_composition_plan(
                self.package,
                self.composition_plan.source_film.source_size,
                template_identity=self.composition_plan.template_identity,
            )
        except ValueError as exc:
            raise ExportJobSnapshotValidationError(
                "CompositionPlan cannot be rebuilt from the Export Package."
            ) from exc
        if expected_plan.to_json() != self.composition_plan.to_json():
            raise ExportJobSnapshotValidationError(
                "CompositionPlan differs from the locked Export Package."
            )
        if not isinstance(self.source_video_path, str) \
                or not self.source_video_path.strip() \
                or self.source_video_path != self.source_video_path.strip() \
                or not Path(self.source_video_path).is_absolute():
            raise ExportJobSnapshotValidationError(
                "Queued job requires one canonical absolute source video path."
            )
        if isinstance(self.source_file_size, bool) \
                or not isinstance(self.source_file_size, int) \
                or self.source_file_size <= 0:
            raise ExportJobSnapshotValidationError(
                "Queued source video size is invalid."
            )
        if not isinstance(self.source_sha256, str) \
                or not _SHA256_PATTERN.fullmatch(self.source_sha256):
            raise ExportJobSnapshotValidationError(
                "Queued source video digest is invalid."
            )
        frozen_clips = tuple(self.clips)
        if not frozen_clips or any(
            not isinstance(clip, ExportClipSnapshot) for clip in frozen_clips
        ):
            raise ExportJobSnapshotValidationError(
                "Queued job requires immutable source clip ranges."
            )
        if len({clip.clip_id for clip in frozen_clips}) != len(frozen_clips):
            raise ExportJobSnapshotValidationError(
                "Queued source clip ids must be unique."
            )
        object.__setattr__(self, "clips", frozen_clips)

        source_input = _input_value(self.package, "source_video")
        if self.package.is_composited and source_input != self.source_video_path:
            raise ExportJobSnapshotValidationError(
                "Queued source path differs from the Export Package input."
            )

        assets = tuple(sorted(
            tuple(self.identity_assets), key=lambda item: item.role.value
        ))
        if any(not isinstance(asset, StagedIdentityAsset) for asset in assets):
            raise ExportJobSnapshotValidationError(
                "Queued identity assets must be staged snapshots."
            )
        if len({asset.role for asset in assets}) != len(assets):
            raise ExportJobSnapshotValidationError(
                "Queued identity asset roles must be unique."
            )
        expected_assets = tuple(sorted(
            _template_assets(self.package), key=lambda item: item.role.value
        ))
        if assets != expected_assets:
            raise ExportJobSnapshotValidationError(
                "Staged identity assets differ from the queued template bytes."
            )
        identity = self.composition_plan.template_identity
        plan_refs = tuple(sorted(
            (
                reference
                for reference in (
                    identity.border if identity else None,
                    identity.profile_photo if identity else None,
                    identity.wordmark_logo if identity else None,
                )
                if reference is not None
            ),
            key=lambda item: item.role.value,
        ))
        if tuple(asset.reference for asset in assets) != plan_refs:
            raise ExportJobSnapshotValidationError(
                "Staged identity assets differ from the CompositionPlan locks."
            )
        object.__setattr__(self, "identity_assets", assets)

        if self.package.include_voiceover:
            if not isinstance(self.voiceover, StagedVoiceoverPayload):
                raise ExportJobSnapshotValidationError(
                    "Enabled Voiceover requires a staged selected take."
                )
            frozen_take_id = frozen_voiceover_take_id(self.package)
            if self.voiceover.take_id != frozen_take_id:
                raise ExportJobSnapshotValidationError(
                    "Staged Voiceover differs from the frozen Voiceover take."
                )
            if self.voiceover.audio_input_value != _input_value(
                    self.package, "voiceover_audio") or \
                    self.voiceover.presentation_input_value != _input_value(
                        self.package, "presentation_event_track"):
                raise ExportJobSnapshotValidationError(
                    "Staged Voiceover inputs differ from the Export Package."
                )
            expected_ink_input = (
                _input_value(self.package, "ink_event_track")
                if self.package.include_ink else ""
            )
            if self.voiceover.ink_input_value != expected_ink_input:
                raise ExportJobSnapshotValidationError(
                    "Staged Voiceover ink input differs from the Export Package."
                )
        elif self.voiceover is not None:
            raise ExportJobSnapshotValidationError(
                "A disabled Voiceover layer cannot retain staged audio."
            )

        if self.package.include_ink and self.package.include_voiceover:
            if self.ink_input_value or self.ink_event_track_json:
                raise ExportJobSnapshotValidationError(
                    "Voiceover ink must come only from its recorded event track."
                )
        elif self.package.include_ink:
            if not isinstance(self.ink_input_value, str) \
                    or not self.ink_input_value.strip() \
                    or self.ink_input_value != _input_value(
                        self.package, "ink_event_track"):
                raise ExportJobSnapshotValidationError(
                    "Enabled ink requires the matching staged input."
                )
            try:
                decoded_marks = json.loads(self.ink_event_track_json)
                validate_telestration_snapshot(decoded_marks)
                canonical_marks = marks_to_json(marks_from_json(decoded_marks))
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ExportJobSnapshotValidationError(
                    "Staged ink event track is invalid."
                ) from exc
            object.__setattr__(
                self, "ink_event_track_json", _canonical_json(canonical_marks)
            )
        elif self.ink_input_value or self.ink_event_track_json:
            raise ExportJobSnapshotValidationError(
                "A disabled ink layer cannot retain a staged track."
            )
        if not isinstance(self.hardware_encoder, str) or (
            self.hardware_encoder
            and not _ENCODER_PATTERN.fullmatch(self.hardware_encoder)
        ):
            raise ExportJobSnapshotValidationError(
                "Queued hardware encoder name is invalid."
            )
        if not isinstance(self.reel_settings, ExportReelSettingsSnapshot):
            raise ExportJobSnapshotValidationError(
                "Queued reel settings must be immutable."
            )

    @classmethod
    def capture(
            cls,
            *,
            package: ExportPackageSnapshot,
            composition_plan: CompositionPlan,
            source_video_path: str,
            clips: tuple[Clip, ...] | list[Clip],
            source_identity: SourceMediaIdentity | None = None,
            selected_voiceover: VoiceoverTake | None = None,
            ink_event_track: object | None = None,
            cancel_event: threading.Event | None = None,
            hardware_encoder: str = "",
            reel_settings: ReelSettings | None = None,
    ) -> "ExportJobSnapshot":
        """Copy mutable authoring state exactly once at queue time."""

        _raise_if_cancelled(cancel_event)
        canonical_package = ExportPackageSnapshot.from_json(package.to_json())
        source_identity = source_identity or SourceMediaIdentity.capture(
            source_video_path, cancel_event=cancel_event)
        if source_identity.source_video_path != source_video_path:
            raise ExportJobSnapshotValidationError(
                "Reusable source identity belongs to a different source path."
            )
        assets = _template_assets(canonical_package)
        voiceover = None
        if canonical_package.include_voiceover:
            if selected_voiceover is None:
                raise ExportJobSnapshotValidationError(
                    "Enabled Voiceover requires the selected take at queue time."
                )
            voiceover = StagedVoiceoverPayload.capture(
                selected_voiceover,
                package=canonical_package,
            )
        ink_input = ""
        ink_json = ""
        if canonical_package.include_voiceover and ink_event_track is not None:
            raise ExportJobSnapshotValidationError(
                "Voiceover ink comes from the frozen take; static clip ink "
                "cannot be staged with it."
            )
        if canonical_package.include_ink \
                and not canonical_package.include_voiceover:
            if ink_event_track is None:
                raise ExportJobSnapshotValidationError(
                    "Enabled ink requires its accepted mark snapshot at queue time."
                )
            try:
                validate_telestration_snapshot(ink_event_track)
                canonical_marks = marks_to_json(marks_from_json(ink_event_track))
            except (TypeError, ValueError) as exc:
                raise ExportJobSnapshotValidationError(
                    "Ink staging requires accepted film-coordinate marks."
                ) from exc
            ink_input = _input_value(canonical_package, "ink_event_track")
            ink_json = _canonical_json(canonical_marks)
        return cls(
            package=canonical_package,
            composition_plan=CompositionPlan.from_json(
                composition_plan.to_json()),
            source_video_path=source_video_path,
            source_file_size=source_identity.file_size,
            source_sha256=source_identity.sha256,
            clips=tuple(ExportClipSnapshot.capture(clip) for clip in clips),
            identity_assets=assets,
            voiceover=voiceover,
            ink_input_value=ink_input,
            ink_event_track_json=ink_json,
            hardware_encoder=hardware_encoder,
            reel_settings=ExportReelSettingsSnapshot.capture(reel_settings),
        )

    @property
    def package_json(self) -> str:
        return self.package.to_json()

    def compositor_input_value(self, name: str) -> str:
        """Return one locked logical input value without consulting the UI."""

        return _input_value(self.package, name)

    def verify_source_media(
            self, *, cancel_event: threading.Event | None = None) -> None:
        """Fail closed when the external source revision changed after queueing."""

        SourceMediaIdentity(
            self.source_video_path,
            self.source_file_size,
            self.source_sha256,
        ).verify(cancel_event=cancel_event)

    @property
    def composition_plan_json(self) -> str:
        return self.composition_plan.to_json()

    @property
    def manifest_json(self) -> str:
        voiceover = self.voiceover
        payload = {
            "schema": EXPORT_JOB_SNAPSHOT_SCHEMA,
            "snapshot": {
                "clips": [
                    {
                        "clip_id": clip.clip_id,
                        "end_ms": clip.end_ms,
                        "start_ms": clip.start_ms,
                    }
                    for clip in self.clips
                ],
                "identity_assets": [
                    {
                        "height": asset.height,
                        "mime_type": asset.mime_type,
                        "role": asset.role.value,
                        "sha256": asset.sha256,
                        "width": asset.width,
                    }
                    for asset in self.identity_assets
                ],
                "hardware_encoder": self.hardware_encoder,
                "ink": (
                    {
                        "input_value": self.ink_input_value,
                        "payload": json.loads(self.ink_event_track_json),
                        "sha256": hashlib.sha256(
                            self.ink_event_track_json.encode("utf-8")
                        ).hexdigest(),
                    }
                    if self.ink_event_track_json else None
                ),
                "reel_settings": {
                    "fade_duration": self.reel_settings.fade_duration,
                    "fade_in": self.reel_settings.fade_in,
                    "fade_out": self.reel_settings.fade_out,
                    "gap_seconds": self.reel_settings.gap_seconds,
                    "show_clip_labels": self.reel_settings.show_clip_labels,
                    "title_card_seconds": self.reel_settings.title_card_seconds,
                    "title_card_text": self.reel_settings.title_card_text,
                },
                "source_video_path": self.source_video_path,
                "voiceover": (
                    {
                        "audio_input_value": voiceover.audio_input_value,
                        "audio_sha256": voiceover.audio_sha256,
                        "channels": voiceover.channels,
                        "clip_id": voiceover.clip_id,
                        "duration_ms": voiceover.duration_ms,
                        "frame_count": voiceover.frame_count,
                        "ink_input_value": voiceover.ink_input_value,
                        "presentation_input_value": (
                            voiceover.presentation_input_value),
                        "presentation_sha256": voiceover.presentation_sha256,
                        "project_id": voiceover.project_id,
                        "sample_rate": voiceover.sample_rate,
                        "source_anchor_ms": voiceover.source_anchor_ms,
                        "take_id": voiceover.take_id,
                        "waveform": list(voiceover.waveform),
                    }
                    if voiceover is not None else None
                ),
                "source_file_size": self.source_file_size,
                "source_sha256": self.source_sha256,
            },
            "version": EXPORT_JOB_SNAPSHOT_VERSION,
        }
        return _canonical_json(payload)

    @property
    def integrity_sha256(self) -> str:
        envelope = _canonical_json({
            "composition_plan": json.loads(self.composition_plan_json),
            "manifest": json.loads(self.manifest_json),
            "package": json.loads(self.package_json),
        })
        return hashlib.sha256(envelope.encode("utf-8")).hexdigest()

    @classmethod
    def from_storage(
            cls,
            *,
            package_json: str,
            composition_plan_json: str,
            manifest_json: str,
            identity_assets: tuple[StagedIdentityAsset, ...],
            voiceover: StagedVoiceoverPayload | None,
    ) -> "ExportJobSnapshot":
        """Rebuild and verify a snapshot from repository rows."""

        try:
            root = _expect_mapping(
                json.loads(manifest_json), "Export job snapshot document")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ExportJobSnapshotValidationError(
                "Export job snapshot manifest is invalid JSON."
            ) from exc
        _exact_keys(
            root, {"schema", "snapshot", "version"},
            "Export job snapshot document",
        )
        if root["schema"] != EXPORT_JOB_SNAPSHOT_SCHEMA:
            raise ExportJobSnapshotValidationError(
                "Unknown export job snapshot schema."
            )
        if type(root["version"]) is not int \
                or root["version"] != EXPORT_JOB_SNAPSHOT_VERSION:
            raise ExportJobSnapshotValidationError(
                f"Unsupported export job snapshot version: {root['version']!r}."
            )
        data = _expect_mapping(root["snapshot"], "Export job snapshot")
        _exact_keys(data, {
            "clips", "hardware_encoder", "identity_assets", "ink",
            "reel_settings", "source_file_size", "source_sha256",
            "source_video_path", "voiceover",
        }, "Export job snapshot")
        raw_clips = data["clips"]
        if not isinstance(raw_clips, list):
            raise ExportJobSnapshotValidationError(
                "Export job snapshot clips must be an array."
            )
        clips: list[ExportClipSnapshot] = []
        for index, raw_clip in enumerate(raw_clips):
            clip = _expect_mapping(raw_clip, f"Export clip snapshot {index}")
            _exact_keys(
                clip, {"clip_id", "end_ms", "start_ms"},
                f"Export clip snapshot {index}",
            )
            clips.append(ExportClipSnapshot(
                clip["clip_id"], clip["start_ms"], clip["end_ms"]
            ))
        reel = _expect_mapping(data["reel_settings"], "Export reel settings")
        _exact_keys(reel, {
            "fade_duration", "fade_in", "fade_out", "gap_seconds",
            "show_clip_labels", "title_card_seconds", "title_card_text",
        }, "Export reel settings")
        ink_input = ""
        ink_json = ""
        if data["ink"] is not None:
            ink = _expect_mapping(data["ink"], "Export ink snapshot")
            _exact_keys(
                ink, {"input_value", "payload", "sha256"},
                "Export ink snapshot",
            )
            ink_input = ink["input_value"]
            ink_json = _canonical_json(ink["payload"])
            if hashlib.sha256(ink_json.encode("utf-8")).hexdigest() != ink["sha256"]:
                raise ExportJobSnapshotValidationError(
                    "Export ink snapshot failed its integrity check."
                )
        snapshot = cls(
            package=ExportPackageSnapshot.from_json(package_json),
            composition_plan=CompositionPlan.from_json(composition_plan_json),
            source_video_path=data["source_video_path"],
            source_file_size=data["source_file_size"],
            source_sha256=data["source_sha256"],
            clips=tuple(clips),
            identity_assets=identity_assets,
            voiceover=voiceover,
            ink_input_value=ink_input,
            ink_event_track_json=ink_json,
            hardware_encoder=data["hardware_encoder"],
            reel_settings=ExportReelSettingsSnapshot(**dict(reel)),
        )
        canonical_input = _canonical_json(root)
        if snapshot.manifest_json != canonical_input:
            raise ExportJobSnapshotValidationError(
                "Export job snapshot rows differ from their manifest."
            )
        return snapshot
