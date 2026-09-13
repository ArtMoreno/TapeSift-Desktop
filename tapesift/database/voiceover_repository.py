"""Persistence for clip-attached Voiceover takes."""

from __future__ import annotations

from dataclasses import replace
import io
import json
import math
import sqlite3
import wave

from tapesift.core.exceptions import DatabaseError
from tapesift.models.presentation_track import PresentationEventTrack
from tapesift.models.voiceover import VoiceoverTake, VoiceoverTakeSummary


class VoiceoverRepositoryError(DatabaseError):
    """A persisted Voiceover record is corrupt or cannot be trusted."""


_FULL_COLUMNS = """
    id, project_id, clip_id, label, source_anchor_ms, sample_rate, channels,
    frame_count, duration_ms, waveform_json, presentation_track_json,
    device_id, device_name, audio_wav, selected, created_at
"""

_SUMMARY_COLUMNS = """
    id, project_id, clip_id, label, source_anchor_ms, sample_rate, channels,
    frame_count, duration_ms, waveform_json, device_id, device_name, selected,
    created_at,
    CASE WHEN presentation_track_json <> '' THEN 1 ELSE 0 END
        AS has_presentation_track
"""


class _BytesViewReader:
    """Seekable WAV reader over existing bytes without copying the BLOB."""

    def __init__(self, value: bytes) -> None:
        self._value = memoryview(value)
        self._position = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            stop = len(self._value)
        else:
            stop = min(len(self._value), self._position + size)
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
    """Return the one RIFF data-chunk size after exact boundary checks."""
    view = memoryview(value)
    if len(view) < 12 or bytes(view[:4]) != b"RIFF" \
            or bytes(view[8:12]) != b"WAVE":
        raise ValueError("Voiceover audio is not a valid WAV file.")
    riff_end = int.from_bytes(view[4:8], "little") + 8
    if riff_end > len(view):
        raise ValueError("Voiceover WAV audio payload is truncated.")
    if riff_end < len(view):
        raise ValueError("Voiceover WAV has an invalid RIFF payload length.")

    offset = 12
    data_sizes: list[int] = []
    while offset < riff_end:
        if offset + 8 > riff_end:
            raise ValueError("Voiceover WAV has a truncated chunk header.")
        chunk_id = bytes(view[offset:offset + 4])
        chunk_size = int.from_bytes(view[offset + 4:offset + 8], "little")
        chunk_end = offset + 8 + chunk_size
        next_offset = chunk_end + (chunk_size & 1)
        if chunk_end > riff_end or next_offset > riff_end:
            raise ValueError("Voiceover WAV has a truncated chunk payload.")
        if chunk_id == b"data":
            data_sizes.append(chunk_size)
        offset = next_offset
    if offset != riff_end or len(data_sizes) != 1:
        raise ValueError("Voiceover WAV needs exactly one audio data chunk.")
    return data_sizes[0]


def _decode_waveform(value: object) -> tuple[float, ...]:
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Voiceover waveform is not valid JSON.") from exc
    if not isinstance(decoded, list):
        raise ValueError("Voiceover waveform must be a JSON list.")
    waveform: list[float] = []
    for item in decoded:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise ValueError("Voiceover waveform contains a non-number.")
        number = float(item)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise ValueError("Voiceover waveform value is out of range.")
        waveform.append(number)
    return tuple(waveform)


class VoiceoverRepository:
    """Store multiple takes while keeping one selected take per clip."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @staticmethod
    def _validate(take: VoiceoverTake) -> None:
        if isinstance(take.project_id, bool) \
                or not isinstance(take.project_id, int) \
                or take.project_id <= 0:
            raise ValueError("A Voiceover take needs a saved project.")
        if not isinstance(take.clip_id, str) or not take.clip_id.strip():
            raise ValueError("A Voiceover take needs a clip id.")
        if isinstance(take.source_anchor_ms, bool) \
                or not isinstance(take.source_anchor_ms, int) \
                or take.source_anchor_ms < 0:
            raise ValueError("Voiceover source anchor cannot be negative.")
        if isinstance(take.sample_rate, bool) \
                or not isinstance(take.sample_rate, int) \
                or isinstance(take.channels, bool) \
                or not isinstance(take.channels, int) \
                or take.sample_rate <= 0 or take.channels <= 0:
            raise ValueError("Voiceover audio format is invalid.")
        if isinstance(take.frame_count, bool) \
                or not isinstance(take.frame_count, int) \
                or isinstance(take.duration_ms, bool) \
                or not isinstance(take.duration_ms, int) \
                or take.frame_count < 0 or take.duration_ms < 0:
            raise ValueError("Voiceover duration cannot be negative.")
        if abs(take.duration_ms - take.calculated_duration_ms) > 1:
            raise ValueError(
                "Voiceover duration does not match its audio frame clock.")
        for value in take.waveform:
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(float(value)) \
                    or not 0.0 <= float(value) <= 1.0:
                raise ValueError(
                    "Voiceover waveform values must be between 0 and 1.")
        if take.presentation_track is not None:
            if not isinstance(take.presentation_track, PresentationEventTrack):
                raise ValueError("Voiceover presentation track is invalid.")
            if take.presentation_track.sample_rate != take.sample_rate:
                raise ValueError(
                    "Voiceover presentation track uses a different audio "
                    "sample rate.")
            if any(event.audio_frame > take.frame_count
                   for event in take.presentation_track.events):
                raise ValueError(
                    "Voiceover presentation event exceeds the audio clock.")

        if not isinstance(take.audio_wav, bytes):
            raise ValueError("Voiceover audio must be immutable WAV bytes.")
        expected_data_size = (
            take.frame_count * take.channels * 2)
        if _canonical_wav_data_size(take.audio_wav) != expected_data_size:
            raise ValueError(
                "Voiceover WAV audio payload length does not match the take.")
        try:
            with wave.open(_BytesViewReader(take.audio_wav), "rb") as audio:
                if audio.getcomptype() != "NONE" or audio.getsampwidth() != 2:
                    raise ValueError(
                        "Voiceover audio must be uncompressed 16-bit PCM WAV.")
                if audio.getframerate() != take.sample_rate \
                        or audio.getnchannels() != take.channels \
                        or audio.getnframes() != take.frame_count:
                    raise ValueError(
                        "Voiceover WAV metadata does not match the take.")
                frame_bytes = take.channels * audio.getsampwidth()
                frames_remaining = take.frame_count
                while frames_remaining:
                    requested = min(frames_remaining, 65_536)
                    chunk = audio.readframes(requested)
                    if not chunk or len(chunk) % frame_bytes:
                        raise ValueError(
                            "Voiceover WAV audio payload is truncated.")
                    frames_remaining -= len(chunk) // frame_bytes
                if audio.readframes(1):
                    raise ValueError(
                        "Voiceover WAV audio payload has an incomplete frame.")
        except (EOFError, wave.Error) as exc:
            raise ValueError("Voiceover audio is not a valid WAV file.") from exc

    def save(
            self, take: VoiceoverTake, *, commit: bool = True
    ) -> VoiceoverTake:
        """Insert or update a take and preserve the one-selected invariant."""
        self._validate(take)
        existing = self.conn.execute(
            "SELECT project_id, clip_id FROM voiceover_takes WHERE id=?",
            (take.id,),
        ).fetchone()
        if existing is None:
            clip_exists = self.conn.execute(
                "SELECT 1 FROM clips WHERE id=? AND project_id=?",
                (take.clip_id, take.project_id),
            ).fetchone()
            if clip_exists is None:
                raise ValueError(
                    "A new Voiceover take needs a clip in this project.")
        if existing is not None and (
                int(existing["project_id"]) != take.project_id
                or str(existing["clip_id"]) != take.clip_id):
            raise ValueError("A Voiceover take cannot move to another clip.")

        try:
            self.conn.execute(
                """INSERT INTO voiceover_takes (
                       id, project_id, clip_id, label, source_anchor_ms,
                       sample_rate, channels, frame_count, duration_ms,
                       waveform_json, presentation_track_json, device_id,
                       device_name, audio_wav, selected, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     label=excluded.label,
                     source_anchor_ms=excluded.source_anchor_ms,
                     sample_rate=excluded.sample_rate,
                     channels=excluded.channels,
                     frame_count=excluded.frame_count,
                     duration_ms=excluded.duration_ms,
                     waveform_json=excluded.waveform_json,
                     presentation_track_json=excluded.presentation_track_json,
                     device_id=excluded.device_id,
                     device_name=excluded.device_name,
                     audio_wav=excluded.audio_wav""",
                (
                    take.id, take.project_id, take.clip_id, take.label,
                    take.source_anchor_ms, take.sample_rate, take.channels,
                    take.frame_count, take.duration_ms,
                    json.dumps(list(take.waveform)),
                    take.presentation_track.to_json()
                    if take.presentation_track is not None else "",
                    take.device_id, take.device_name,
                    sqlite3.Binary(take.audio_wav), 0, take.created_at,
                ),
            )
            selected = self.conn.execute(
                """SELECT id FROM voiceover_takes
                   WHERE project_id=? AND clip_id=? AND selected=1""",
                (take.project_id, take.clip_id),
            ).fetchone()
            should_select = take.selected or selected is None
            if should_select:
                self._select_in_group(
                    take.project_id, take.clip_id, take.id)
            if commit:
                self.conn.commit()
        except Exception:
            if commit:
                self.conn.rollback()
            raise

        saved_selected = should_select or (
            selected is not None and str(selected["id"]) == take.id)
        return replace(take, selected=saved_selected)

    def get(
            self, take_id: str, *, include_audio: bool = True
    ) -> VoiceoverTake | VoiceoverTakeSummary | None:
        """Fetch full audio explicitly, or metadata for list surfaces."""
        columns = _FULL_COLUMNS if include_audio else _SUMMARY_COLUMNS
        row = self.conn.execute(
            f"SELECT {columns} FROM voiceover_takes WHERE id=?", (take_id,)
        ).fetchone()
        if row is None:
            return None
        return self._to_take(row) if include_audio else self._to_summary(row)

    def list_for_clip(
            self, project_id: int, clip_id: str
    ) -> list[VoiceoverTake]:
        """Explicitly load every audio BLOB; UI lists must use summaries."""
        rows = self.conn.execute(
            f"""SELECT {_FULL_COLUMNS} FROM voiceover_takes
               WHERE project_id=? AND clip_id=?
               ORDER BY created_at, id""",
            (project_id, clip_id),
        ).fetchall()
        return [self._to_take(row) for row in rows]

    def list_summaries_for_clip(
            self, project_id: int, clip_id: str
    ) -> list[VoiceoverTakeSummary]:
        """List take metadata without selecting or materializing audio."""
        rows = self.conn.execute(
            f"""SELECT {_SUMMARY_COLUMNS} FROM voiceover_takes
                WHERE project_id=? AND clip_id=?
                ORDER BY created_at, id""",
            (project_id, clip_id),
        ).fetchall()
        return [self._to_summary(row) for row in rows]

    def selected_for_clip(
            self, project_id: int, clip_id: str
    ) -> VoiceoverTake | None:
        """Load selected audio for audition/export, not list rendering."""
        row = self.conn.execute(
            f"""SELECT {_FULL_COLUMNS} FROM voiceover_takes
               WHERE project_id=? AND clip_id=? AND selected=1""",
            (project_id, clip_id),
        ).fetchone()
        return self._to_take(row) if row is not None else None

    def select(
            self, project_id: int, clip_id: str, take_id: str,
            *, commit: bool = True,
    ) -> VoiceoverTakeSummary:
        row = self.conn.execute(
            """SELECT id FROM voiceover_takes
               WHERE id=? AND project_id=? AND clip_id=?""",
            (take_id, project_id, clip_id),
        ).fetchone()
        if row is None:
            raise ValueError("That Voiceover take does not belong to this clip.")
        try:
            self._select_in_group(project_id, clip_id, take_id)
            if commit:
                self.conn.commit()
        except Exception:
            if commit:
                self.conn.rollback()
            raise
        selected = self.get(take_id, include_audio=False)
        assert selected is not None
        assert isinstance(selected, VoiceoverTakeSummary)
        return selected

    def delete(self, take_id: str, *, commit: bool = True) -> bool:
        row = self.conn.execute(
            """SELECT project_id, clip_id, selected
               FROM voiceover_takes WHERE id=?""",
            (take_id,),
        ).fetchone()
        if row is None:
            return False
        project_id = int(row["project_id"])
        clip_id = str(row["clip_id"])
        try:
            self.conn.execute(
                "DELETE FROM voiceover_takes WHERE id=?", (take_id,))
            if bool(row["selected"]):
                replacement = self.conn.execute(
                    """SELECT id FROM voiceover_takes
                       WHERE project_id=? AND clip_id=?
                       ORDER BY created_at DESC, id DESC LIMIT 1""",
                    (project_id, clip_id),
                ).fetchone()
                if replacement is not None:
                    self._select_in_group(
                        project_id, clip_id, str(replacement["id"]))
            if commit:
                self.conn.commit()
        except Exception:
            if commit:
                self.conn.rollback()
            raise
        return True

    def _select_in_group(
            self, project_id: int, clip_id: str, take_id: str) -> None:
        # Deselect first because SQLite checks the partial unique index row by
        # row even when both changes are expressed in one UPDATE statement.
        # Both statements remain in the caller's transaction.
        self.conn.execute(
            """UPDATE voiceover_takes
               SET selected=0
               WHERE project_id=? AND clip_id=?""",
            (project_id, clip_id),
        )
        self.conn.execute(
            """UPDATE voiceover_takes SET selected=1
               WHERE id=? AND project_id=? AND clip_id=?""",
            (take_id, project_id, clip_id),
        )

    @classmethod
    def _to_take(cls, row: sqlite3.Row) -> VoiceoverTake:
        try:
            waveform = _decode_waveform(row["waveform_json"])
            raw_track = row["presentation_track_json"] or ""
            presentation_track = PresentationEventTrack.from_json(raw_track) \
                if raw_track else None
            take = VoiceoverTake(
                id=row["id"],
                project_id=row["project_id"],
                clip_id=row["clip_id"],
                label=row["label"],
                source_anchor_ms=row["source_anchor_ms"],
                sample_rate=row["sample_rate"],
                channels=row["channels"],
                frame_count=row["frame_count"],
                duration_ms=row["duration_ms"],
                waveform=waveform,
                presentation_track=presentation_track,
                device_id=row["device_id"],
                device_name=row["device_name"],
                audio_wav=bytes(row["audio_wav"]),
                selected=bool(row["selected"]),
                created_at=row["created_at"],
            )
            cls._validate(take)
        except (TypeError, ValueError, OverflowError) as exc:
            raise VoiceoverRepositoryError(
                f"Voiceover take {row['id']} has corrupt audio or metadata.",
                "Restore the project from a backup or remove the damaged "
                "take.",
            ) from exc
        return take

    @staticmethod
    def _to_summary(row: sqlite3.Row) -> VoiceoverTakeSummary:
        try:
            waveform = _decode_waveform(row["waveform_json"])
        except (TypeError, ValueError, OverflowError) as exc:
            raise VoiceoverRepositoryError(
                f"Voiceover take {row['id']} has corrupt metadata.",
                "Restore the project from a backup or remove the damaged "
                "take.",
            ) from exc
        return VoiceoverTakeSummary(
            id=row["id"],
            project_id=row["project_id"],
            clip_id=row["clip_id"],
            label=row["label"],
            source_anchor_ms=row["source_anchor_ms"],
            sample_rate=row["sample_rate"],
            channels=row["channels"],
            frame_count=row["frame_count"],
            duration_ms=row["duration_ms"],
            waveform=waveform,
            device_id=row["device_id"],
            device_name=row["device_name"],
            selected=bool(row["selected"]),
            created_at=row["created_at"],
            has_presentation_track=bool(row["has_presentation_track"]),
        )
