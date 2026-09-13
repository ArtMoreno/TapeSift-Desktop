"""One recorded Voiceover take attached to a clip."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from tapesift.models.presentation_track import PresentationEventTrack


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class VoiceoverTake:
    """Persisted narration audio and its source-film anchor.

    ``clip_id`` is deliberately an identity reference rather than a database
    foreign key. ProjectSession replaces every clip row during an ordinary
    save, so a clip foreign key would erase every take on every autosave.
    """

    clip_id: str
    audio_wav: bytes = field(repr=False)
    sample_rate: int
    channels: int
    frame_count: int
    duration_ms: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    project_id: int = 0
    label: str = ""
    source_anchor_ms: int = 0
    waveform: tuple[float, ...] = field(default_factory=tuple)
    presentation_track: PresentationEventTrack | None = None
    device_id: str = ""
    device_name: str = ""
    selected: bool = False
    created_at: str = field(default_factory=_now)

    @property
    def calculated_duration_ms(self) -> int:
        """Duration derived from the recording clock, rounded to 1 ms."""
        if self.sample_rate <= 0:
            return 0
        return (self.frame_count * 1000 + self.sample_rate // 2) \
            // self.sample_rate

    @property
    def audio_byte_count(self) -> int:
        return len(self.audio_wav)


@dataclass(frozen=True)
class VoiceoverTakeSummary:
    """Take-list metadata that can never materialize the audio BLOB."""

    id: str
    project_id: int
    clip_id: str
    label: str
    source_anchor_ms: int
    sample_rate: int
    channels: int
    frame_count: int
    duration_ms: int
    waveform: tuple[float, ...]
    device_id: str
    device_name: str
    selected: bool
    created_at: str
    has_presentation_track: bool
