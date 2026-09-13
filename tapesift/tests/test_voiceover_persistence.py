"""Voiceover model, schema, and repository persistence."""

from __future__ import annotations

import io
import sqlite3
import struct
import wave
from pathlib import Path

import pytest

from tapesift.database.connection import open_project_db
from tapesift.database.migrations import MIGRATIONS
from tapesift.database.voiceover_repository import (
    VoiceoverRepository,
    VoiceoverRepositoryError,
)
from tapesift.models.clip import Clip
from tapesift.models.presentation_track import (
    PresentationEvent,
    PresentationEventKind,
    PresentationEventTrack,
)
from tapesift.models.voiceover import VoiceoverTake, VoiceoverTakeSummary
from tapesift.services.project_service import ProjectSession


def _wav(*, frames: int = 4_800, sample_rate: int = 48_000,
         channels: int = 1) -> bytes:
    target = io.BytesIO()
    with wave.open(target, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\0\0" * frames * channels)
    return target.getvalue()


def _wav_with_partial_data_frame(*, frames: int = 100) -> bytes:
    value = bytearray(_wav(frames=frames))
    data_offset = value.index(b"data")
    data_size = struct.unpack_from("<I", value, data_offset + 4)[0]
    struct.pack_into("<I", value, data_offset + 4, data_size + 1)
    riff_size = struct.unpack_from("<I", value, 4)[0]
    # One malformed payload byte plus RIFF's required odd-chunk pad byte.
    struct.pack_into("<I", value, 4, riff_size + 2)
    value.extend(b"\0\0")
    return bytes(value)


def _take(project_id: int, clip_id: str, *, label: str = "Take 1",
          selected: bool = False, created_at: str = "2026-08-20T01:00:00+00:00",
          frames: int = 4_800) -> VoiceoverTake:
    return VoiceoverTake(
        project_id=project_id,
        clip_id=clip_id,
        label=label,
        source_anchor_ms=12_500,
        sample_rate=48_000,
        channels=1,
        frame_count=frames,
        duration_ms=round(frames / 48_000 * 1000),
        waveform=(0.0, 0.25, 0.75, 1.0),
        device_id="wasapi:fixture",
        device_name="Fixture microphone",
        audio_wav=_wav(frames=frames),
        selected=selected,
        created_at=created_at,
    )


def _presentation_track() -> PresentationEventTrack:
    return PresentationEventTrack(
        sample_rate=48_000,
        events=(
            PresentationEvent.create(
                audio_frame=0,
                sequence=0,
                kind=PresentationEventKind.SOURCE_POSITION,
                payload={"source_position_ms": 12_500},
            ),
            PresentationEvent.create(
                audio_frame=2_400,
                sequence=1,
                kind=PresentationEventKind.TELESTRATION_SNAPSHOT,
                payload={"marks": []},
            ),
        ),
    )


@pytest.fixture
def session(tmp_path: Path):
    value = ProjectSession.create("Voiceover", tmp_path, tmp_path / "out")
    yield value
    value.conn.close()


def test_model_duration_comes_from_the_audio_frame_clock():
    take = _take(1, "clip-1", frames=2_401)

    assert take.calculated_duration_ms == 50
    assert take.audio_byte_count == 44 + 2_401 * 2
    assert len(take.id) == 32
    assert take.created_at


def test_v12_schema_owns_audio_and_event_track_by_project_not_clip(tmp_path):
    project = tmp_path / "voiceover-schema.tapesift"
    conn = open_project_db(project)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == \
            len(MIGRATIONS)
        columns = {
            row["name"] for row in conn.execute(
                "PRAGMA table_info(voiceover_takes)")
        }
        assert {
            "project_id", "clip_id", "source_anchor_ms", "sample_rate",
            "channels", "frame_count", "duration_ms", "waveform_json",
            "presentation_track_json", "device_id", "device_name",
            "audio_wav", "selected",
        } <= columns
        foreign_keys = list(conn.execute(
            "PRAGMA foreign_key_list(voiceover_takes)"))
        assert [(row["from"], row["table"], row["on_delete"])
                for row in foreign_keys] == [
                    ("project_id", "projects", "CASCADE")]
        indexes = {
            row["name"]: bool(row["unique"])
            for row in conn.execute("PRAGMA index_list(voiceover_takes)")
        }
        assert indexes["idx_voiceover_one_selected_take"]
    finally:
        conn.close()


def test_repository_round_trips_canonical_wav_and_metadata(session):
    clip = session.add_clip(Clip(start_ms=10_000, end_ms=30_000))
    repo = VoiceoverRepository(session.conn)

    saved = repo.save(_take(session.project.id, clip.id))
    loaded = repo.get(saved.id)

    assert loaded is not None
    assert loaded.selected
    assert loaded.project_id == session.project.id
    assert loaded.clip_id == clip.id
    assert loaded.source_anchor_ms == 12_500
    assert loaded.frame_count == 4_800
    assert loaded.duration_ms == 100
    assert loaded.waveform == (0.0, 0.25, 0.75, 1.0)
    assert loaded.device_id == "wasapi:fixture"
    assert loaded.device_name == "Fixture microphone"
    assert loaded.audio_wav == _wav()


def test_repository_round_trips_audio_clock_presentation_track(session):
    clip = session.add_clip(Clip(start_ms=10_000, end_ms=30_000))
    repo = VoiceoverRepository(session.conn)
    take = _take(session.project.id, clip.id)
    take.presentation_track = _presentation_track()

    loaded = repo.save(take)

    assert loaded.presentation_track == _presentation_track()
    assert loaded.presentation_track.to_json() == _presentation_track().to_json()


def test_repository_rejects_presentation_events_beyond_audio(session):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    take = _take(session.project.id, clip.id, frames=100)
    take.presentation_track = PresentationEventTrack(
        sample_rate=48_000,
        events=(PresentationEvent.create(
            audio_frame=101,
            sequence=0,
            kind=PresentationEventKind.SOURCE_POSITION,
            payload={"source_position_ms": 0},
        ),),
    )

    with pytest.raises(ValueError, match="exceeds the audio clock"):
        repo.save(take)


def test_repository_rejects_presentation_track_sample_rate_mismatch(session):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    take = _take(session.project.id, clip.id)
    take.presentation_track = PresentationEventTrack(sample_rate=44_100)

    with pytest.raises(ValueError, match="different audio sample rate"):
        repo.save(take)


def test_repository_keeps_exactly_one_selected_take(session):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    first = repo.save(_take(session.project.id, clip.id, label="First"))
    second = repo.save(_take(
        session.project.id, clip.id, label="Second", selected=True,
        created_at="2026-08-20T01:01:00+00:00"))
    third = repo.save(_take(
        session.project.id, clip.id, label="Third",
        created_at="2026-08-20T01:02:00+00:00"))

    assert repo.selected_for_clip(session.project.id, clip.id).id == second.id
    assert [take.selected for take in repo.list_for_clip(
        session.project.id, clip.id)] == [False, True, False]

    selected = repo.select(session.project.id, clip.id, first.id)
    assert selected.selected
    assert sum(take.selected for take in repo.list_for_clip(
        session.project.id, clip.id)) == 1

    assert repo.delete(first.id)
    assert repo.selected_for_clip(session.project.id, clip.id).id == third.id
    assert sum(take.selected for take in repo.list_for_clip(
        session.project.id, clip.id)) == 1


def test_repository_rejects_non_wav_and_mismatched_clock_metadata(session):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    invalid = _take(session.project.id, clip.id)
    invalid.audio_wav = b"not a wav file"
    with pytest.raises(ValueError, match="valid WAV"):
        repo.save(invalid)

    mismatch = _take(session.project.id, clip.id)
    mismatch.duration_ms = 500
    with pytest.raises(ValueError, match="frame clock"):
        repo.save(mismatch)


def test_repository_reads_the_exact_declared_wav_pcm_payload(session):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    truncated = _take(session.project.id, clip.id, frames=100)
    truncated.audio_wav = truncated.audio_wav[:-2]

    with pytest.raises(ValueError, match="payload is truncated"):
        repo.save(truncated)

    partial_frame = _take(session.project.id, clip.id, frames=100)
    partial_frame.audio_wav = _wav_with_partial_data_frame(frames=100)
    with pytest.raises(ValueError, match="payload length"):
        repo.save(partial_frame)


def test_new_take_requires_a_clip_in_the_same_project(session):
    repo = VoiceoverRepository(session.conn)

    with pytest.raises(ValueError, match="clip in this project"):
        repo.save(_take(session.project.id, "missing-clip"))

    created_at = "2026-08-20T02:00:00+00:00"
    other_project_id = session.conn.execute(
        "INSERT INTO projects (name, created_at, updated_at) VALUES (?,?,?)",
        ("Other project", created_at, created_at),
    ).lastrowid
    session.conn.execute(
        """INSERT INTO clips (
               id, project_id, start_ms, end_ms, created_at, updated_at)
           VALUES (?,?,?,?,?,?)""",
        ("other-project-clip", other_project_id, 0, 1_000,
         created_at, created_at),
    )
    session.conn.commit()
    with pytest.raises(ValueError, match="clip in this project"):
        repo.save(_take(session.project.id, "other-project-clip"))


def test_existing_legacy_orphan_can_update_without_adding_a_clip_fk(session):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    take = repo.save(_take(session.project.id, clip.id))
    session.conn.execute("DELETE FROM clips WHERE id=?", (clip.id,))
    session.conn.commit()

    take.label = "Legacy orphan retained"
    saved = repo.save(take)

    assert saved.label == "Legacy orphan retained"
    assert repo.get(take.id).label == "Legacy orphan retained"


def test_corrupt_nonempty_presentation_track_is_a_controlled_error(session):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    take = repo.save(_take(session.project.id, clip.id))
    session.conn.execute(
        """UPDATE voiceover_takes SET presentation_track_json=?
           WHERE id=?""",
        ("{not-valid-json", take.id),
    )
    session.conn.commit()

    summary = repo.get(take.id, include_audio=False)
    assert isinstance(summary, VoiceoverTakeSummary)
    assert summary.has_presentation_track
    with pytest.raises(
            VoiceoverRepositoryError, match="corrupt audio or metadata"):
        repo.get(take.id, include_audio=True)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("audio_wav", sqlite3.Binary(b"RIFF\x04\x00\x00\x00WAVE")),
        ("waveform_json", "{not-json"),
        ("sample_rate", 44_100),
    ],
)
def test_full_fetch_rejects_corrupt_audio_or_metadata(
        session, column, value):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    take = repo.save(_take(session.project.id, clip.id))
    session.conn.execute(
        f"UPDATE voiceover_takes SET {column}=? WHERE id=?",
        (value, take.id),
    )
    session.conn.commit()

    if column == "waveform_json":
        with pytest.raises(
                VoiceoverRepositoryError, match="corrupt metadata"):
            repo.get(take.id, include_audio=False)
    else:
        assert isinstance(
            repo.get(take.id, include_audio=False), VoiceoverTakeSummary)
    with pytest.raises(
            VoiceoverRepositoryError, match="corrupt audio or metadata"):
        repo.get(take.id, include_audio=True)


def test_full_fetch_revalidates_track_clock_against_audio(session):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    take = repo.save(_take(session.project.id, clip.id, frames=100))
    invalid_tracks = (
        PresentationEventTrack(
            sample_rate=48_000,
            events=(PresentationEvent.create(
                audio_frame=101,
                sequence=0,
                kind=PresentationEventKind.SOURCE_POSITION,
                payload={"source_position_ms": 0},
            ),),
        ),
        PresentationEventTrack(sample_rate=44_100),
    )
    for invalid_track in invalid_tracks:
        session.conn.execute(
            """UPDATE voiceover_takes SET presentation_track_json=?
               WHERE id=?""",
            (invalid_track.to_json(), take.id),
        )
        session.conn.commit()

        with pytest.raises(
                VoiceoverRepositoryError, match="corrupt audio or metadata"):
            repo.get(take.id, include_audio=True)


def test_summary_and_save_select_paths_never_select_audio_blob(session):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    first = repo.save(_take(session.project.id, clip.id, label="First"))
    second = repo.save(_take(
        session.project.id, clip.id, label="Second",
        created_at="2026-08-20T01:01:00+00:00"))
    statements = []
    session.conn.set_trace_callback(statements.append)
    try:
        summaries = repo.list_summaries_for_clip(
            session.project.id, clip.id)
        lazy = repo.get(first.id, include_audio=False)
        selected = repo.select(session.project.id, clip.id, second.id)
        first.label = "First renamed"
        saved = repo.save(first)
    finally:
        session.conn.set_trace_callback(None)

    assert all(isinstance(value, VoiceoverTakeSummary)
               for value in summaries)
    assert isinstance(lazy, VoiceoverTakeSummary)
    assert isinstance(selected, VoiceoverTakeSummary)
    assert not hasattr(lazy, "audio_wav")
    assert saved.audio_wav is first.audio_wav
    select_statements = [
        statement.casefold() for statement in statements
        if statement.lstrip().casefold().startswith("select")
    ]
    assert select_statements
    assert all("audio_wav" not in statement
               for statement in select_statements)

    full = repo.get(first.id, include_audio=True)
    assert isinstance(full, VoiceoverTake)
    assert full.audio_wav == first.audio_wav


def test_ordinary_project_save_cannot_delete_voiceover_takes(session):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    take = repo.save(_take(session.project.id, clip.id))

    # ProjectSession's normal persistence deletes and recreates every clip.
    clip.clip_title = "Changed before autosave"
    session.save()

    loaded = repo.get(take.id)
    assert loaded is not None
    assert loaded.audio_wav == take.audio_wav
    assert repo.selected_for_clip(session.project.id, clip.id).id == take.id


def test_project_delete_cascades_voiceover_audio(session):
    clip = session.add_clip(Clip(start_ms=0, end_ms=20_000))
    repo = VoiceoverRepository(session.conn)
    take = repo.save(_take(session.project.id, clip.id))

    session.conn.execute(
        "DELETE FROM projects WHERE id=?", (session.project.id,))
    session.conn.commit()

    assert repo.get(take.id) is None


def test_previous_schema_migrates_to_voiceover_without_replay(tmp_path):
    project = tmp_path / "v10.tapesift"
    conn = sqlite3.connect(project)
    try:
        # Build the exact v10 fixture. New migrations may be appended after
        # Voiceover without changing what this test means.
        for version, script in enumerate(MIGRATIONS[:10], start=1):
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name='voiceover_takes'"
        ).fetchone() is None
    finally:
        conn.close()

    migrated = open_project_db(project)
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == \
            len(MIGRATIONS)
        assert migrated.execute(
            "SELECT name FROM sqlite_master WHERE name='voiceover_takes'"
        ).fetchone()[0] == "voiceover_takes"
    finally:
        migrated.close()


def test_v11_voiceover_schema_migrates_event_track_once_and_reopens(tmp_path):
    project = tmp_path / "v11-voiceover.tapesift"
    conn = sqlite3.connect(project)
    try:
        # Build the exact v11 fixture. Do not infer it from the end of the
        # migration list because later queue/storage work is append-only.
        for version, script in enumerate(MIGRATIONS[:11], start=1):
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 11
        columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(voiceover_takes)")
        }
        assert "presentation_track_json" not in columns
        created_at = "2026-08-20T03:00:00+00:00"
        project_id = conn.execute(
            """INSERT INTO projects (name, created_at, updated_at)
               VALUES (?,?,?)""",
            ("V11 fixture", created_at, created_at),
        ).lastrowid
        conn.execute(
            """INSERT INTO clips (
                   id, project_id, start_ms, end_ms, created_at, updated_at)
               VALUES (?,?,?,?,?,?)""",
            ("v11-clip", project_id, 0, 1_000, created_at, created_at),
        )
        original_audio = _wav(frames=17)
        conn.execute(
            """INSERT INTO voiceover_takes (
                   id, project_id, clip_id, sample_rate, channels,
                   frame_count, duration_ms, audio_wav, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            ("v11-take", project_id, "v11-clip", 48_000, 1,
             17, 0, sqlite3.Binary(original_audio), created_at),
        )
        v11_columns = [row[1] for row in conn.execute(
            "PRAGMA table_info(voiceover_takes)")]
        v11_values = conn.execute(
            "SELECT * FROM voiceover_takes WHERE id='v11-take'").fetchone()
        conn.commit()
    finally:
        conn.close()

    migrated = open_project_db(project)
    try:
        assert migrated.execute("PRAGMA user_version").fetchone()[0] == \
            len(MIGRATIONS)
        columns = {
            row["name"] for row in migrated.execute(
                "PRAGMA table_info(voiceover_takes)")
        }
        assert "presentation_track_json" in columns
        migrated_row = migrated.execute(
            "SELECT * FROM voiceover_takes WHERE id='v11-take'").fetchone()
        assert tuple(migrated_row[name] for name in v11_columns) == \
            tuple(v11_values)
        assert bytes(migrated_row["audio_wav"]) == original_audio
        assert migrated_row["presentation_track_json"] == ""
    finally:
        migrated.close()

    reopened = open_project_db(project)
    try:
        assert reopened.execute("PRAGMA user_version").fetchone()[0] == \
            len(MIGRATIONS)
    finally:
        reopened.close()
