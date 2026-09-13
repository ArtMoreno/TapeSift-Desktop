"""Versioned schema migrations, applied in order via PRAGMA user_version."""

from __future__ import annotations

import logging
import sqlite3

from tapesift.core.exceptions import DatabaseError

log = logging.getLogger(__name__)

MIGRATIONS: list[str] = [
    # v1 - initial schema
    """
    CREATE TABLE projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        source_video_path TEXT NOT NULL DEFAULT '',
        source_duration_ms INTEGER NOT NULL DEFAULT 0,
        source_metadata_json TEXT NOT NULL DEFAULT '',
        output_folder TEXT NOT NULL DEFAULT '',
        naming_template TEXT NOT NULL DEFAULT '{clip_number}_{clip_name}',
        default_preset TEXT NOT NULL DEFAULT 'source_quality',
        accurate_cut INTEGER NOT NULL DEFAULT 1,
        pre_roll_ms INTEGER NOT NULL DEFAULT 5000,
        post_roll_ms INTEGER NOT NULL DEFAULT 8000,
        output_organization TEXT NOT NULL DEFAULT 'structured',
        export_settings_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE clips (
        id TEXT PRIMARY KEY,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        clip_number INTEGER NOT NULL DEFAULT 0,
        order_index INTEGER NOT NULL DEFAULT 0,
        start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL,
        central_timestamp_ms INTEGER,
        clip_title TEXT NOT NULL DEFAULT '',
        output_filename_base TEXT NOT NULL DEFAULT '',
        label TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        export_preset TEXT NOT NULL DEFAULT '',
        include_in_reel INTEGER NOT NULL DEFAULT 1,
        enabled INTEGER NOT NULL DEFAULT 1,
        thumbnail_path TEXT NOT NULL DEFAULT '',
        exported_path TEXT NOT NULL DEFAULT '',
        export_status TEXT NOT NULL DEFAULT 'not_exported',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_clips_project ON clips(project_id, order_index);

    CREATE TABLE export_jobs (
        id TEXT PRIMARY KEY,
        project_id INTEGER NOT NULL,
        clip_id TEXT NOT NULL DEFAULT '',
        job_type TEXT NOT NULL,
        status TEXT NOT NULL,
        settings_json TEXT NOT NULL DEFAULT '{}',
        output_path TEXT NOT NULL DEFAULT '',
        ffmpeg_command TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT
    );
    """,
    # v2 - legacy column from the removed overlay-annotation feature. The
    # migration must remain so existing databases keep a consistent version
    # history; the app no longer reads or writes this column.
    """
    ALTER TABLE clips ADD COLUMN overlays_json TEXT NOT NULL DEFAULT '[]';
    """,
    # v3 - structured detail fields (film-breakdown metadata)
    """
    ALTER TABLE clips ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}';
    """,
    # v4 - the opponent faced in this film (project-level, inherited by clips)
    """
    ALTER TABLE projects ADD COLUMN opponent TEXT NOT NULL DEFAULT '';
    """,
    # v5 - film-time boundaries for Q2, Q3, Q4, and overtime periods
    """
    ALTER TABLE projects ADD COLUMN quarter_markers_json TEXT NOT NULL DEFAULT '[]';
    """,
    # v6 - per-project timeline presentation for primary tags. This is
    # display-only metadata and never rewrites a clip's tags.
    """
    ALTER TABLE projects ADD COLUMN tag_styles_json TEXT NOT NULL DEFAULT '{}';
    """,
    # v7 - immutable automatic-detection inputs plus append-only correction
    # history. Clip ids are intentionally stored as plain text in candidate
    # and event payloads: ProjectSession replaces clip rows on every save, so
    # a foreign key to clips would erase provenance during normal persistence.
    """
    ALTER TABLE clips
        ADD COLUMN detection_lineage_json TEXT NOT NULL DEFAULT '{}';

    CREATE TABLE autodetect_sessions (
        id TEXT PRIMARY KEY,
        project_id INTEGER NOT NULL
            REFERENCES projects(id) ON DELETE CASCADE,
        schema_version TEXT NOT NULL DEFAULT '1.0',
        detector_id TEXT NOT NULL,
        detector_version TEXT NOT NULL,
        app_version TEXT NOT NULL,
        source_json TEXT NOT NULL,
        parameters_json TEXT NOT NULL,
        result_json TEXT NOT NULL,
        ui_options_json TEXT NOT NULL DEFAULT '{}',
        provenance_json TEXT NOT NULL DEFAULT '{}',
        runtime_seconds REAL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_autodetect_sessions_project
        ON autodetect_sessions(project_id, created_at);

    CREATE TABLE autodetect_candidates (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL
            REFERENCES autodetect_sessions(id) ON DELETE CASCADE,
        candidate_kind TEXT NOT NULL,
        candidate_index INTEGER NOT NULL,
        detector_start_ms INTEGER NOT NULL,
        detector_end_ms INTEGER NOT NULL,
        created_start_ms INTEGER NOT NULL,
        created_end_ms INTEGER NOT NULL,
        angle_starts_json TEXT NOT NULL DEFAULT '[]',
        angle_count INTEGER NOT NULL DEFAULT 1,
        needs_review INTEGER NOT NULL DEFAULT 0,
        review_reason TEXT NOT NULL DEFAULT '',
        initial_clip_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(session_id, candidate_kind, candidate_index)
    );
    CREATE INDEX idx_autodetect_candidates_session
        ON autodetect_candidates(session_id, candidate_kind, candidate_index);

    CREATE TABLE autodetect_events (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL
            REFERENCES autodetect_sessions(id) ON DELETE CASCADE,
        sequence INTEGER NOT NULL,
        action TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        before_json TEXT NOT NULL DEFAULT '[]',
        after_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL,
        UNIQUE(session_id, sequence)
    );
    CREATE INDEX idx_autodetect_events_session
        ON autodetect_events(session_id, sequence);
    """,
    # v8 - explicit, bounded development-review batches and immutable records
    # for real plays a person recovered after the detector missed them.
    """
    CREATE TABLE autodetect_review_batches (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL
            REFERENCES autodetect_sessions(id) ON DELETE CASCADE,
        start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active', 'completed', 'cancelled')),
        confirmation_json TEXT NOT NULL DEFAULT '{}',
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL DEFAULT '',
        CHECK(start_ms >= 0),
        CHECK(end_ms > start_ms),
        UNIQUE(id, session_id)
    );
    CREATE INDEX idx_autodetect_review_batches_session
        ON autodetect_review_batches(session_id, started_at, id);
    CREATE UNIQUE INDEX idx_autodetect_one_active_batch
        ON autodetect_review_batches(status)
        WHERE status = 'active';

    CREATE TABLE autodetect_recoveries (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL
            REFERENCES autodetect_sessions(id) ON DELETE CASCADE,
        batch_id TEXT NOT NULL,
        initial_clip_id TEXT NOT NULL,
        created_start_ms INTEGER NOT NULL,
        created_end_ms INTEGER NOT NULL,
        creation_method TEXT NOT NULL DEFAULT 'explicit',
        created_at TEXT NOT NULL,
        CHECK(created_start_ms >= 0),
        CHECK(created_end_ms > created_start_ms),
        FOREIGN KEY (batch_id, session_id)
            REFERENCES autodetect_review_batches(id, session_id)
            ON DELETE CASCADE
    );
    CREATE INDEX idx_autodetect_recoveries_session
        ON autodetect_recoveries(session_id, batch_id, created_at, id);
    """,
    # v9 - everyday Coverage Guardian review decisions. These are deliberately
    # separate from bounded research batches: an editor can resolve possible
    # misses without claiming benchmark truth or mutating detector output.
    """
    CREATE TABLE autodetect_coverage_reviews (
        session_id TEXT NOT NULL
            REFERENCES autodetect_sessions(id) ON DELETE CASCADE,
        segment_index INTEGER NOT NULL,
        status TEXT NOT NULL
            CHECK(status IN ('clip_created', 'dismissed')),
        clip_id TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT NOT NULL,
        PRIMARY KEY (session_id, segment_index),
        CHECK(segment_index >= 0)
    );
    CREATE INDEX idx_autodetect_coverage_reviews_session
        ON autodetect_coverage_reviews(session_id, segment_index);
    """,
    # v10 - private, replaceable machine analysis. Predictions live here so
    # they cannot masquerade as analyst metadata or rewrite detector truth.
    """
    ALTER TABLE clips
        ADD COLUMN analysis_json TEXT NOT NULL DEFAULT '{}';
    """,
    # v11 - canonical recorded narration. clip_id is deliberately plain text:
    # ProjectSession replaces clip rows on every save, so a clip foreign key
    # would cascade-delete every take during ordinary persistence. The project
    # row is stable and safely owns the audio BLOB for project deletion/copy.
    """
    CREATE TABLE voiceover_takes (
        id TEXT PRIMARY KEY,
        project_id INTEGER NOT NULL
            REFERENCES projects(id) ON DELETE CASCADE,
        clip_id TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        source_anchor_ms INTEGER NOT NULL DEFAULT 0,
        sample_rate INTEGER NOT NULL,
        channels INTEGER NOT NULL,
        frame_count INTEGER NOT NULL,
        duration_ms INTEGER NOT NULL,
        waveform_json TEXT NOT NULL DEFAULT '[]',
        device_id TEXT NOT NULL DEFAULT '',
        device_name TEXT NOT NULL DEFAULT '',
        audio_wav BLOB NOT NULL,
        selected INTEGER NOT NULL DEFAULT 0
            CHECK(selected IN (0, 1)),
        created_at TEXT NOT NULL,
        CHECK(length(clip_id) > 0),
        CHECK(source_anchor_ms >= 0),
        CHECK(sample_rate > 0),
        CHECK(channels > 0),
        CHECK(frame_count >= 0),
        CHECK(duration_ms >= 0),
        CHECK(length(audio_wav) >= 12)
    );
    CREATE INDEX idx_voiceover_takes_clip
        ON voiceover_takes(project_id, clip_id, created_at, id);
    CREATE UNIQUE INDEX idx_voiceover_one_selected_take
        ON voiceover_takes(project_id, clip_id)
        WHERE selected = 1;
    """,
    # v12 - immutable narration presentation inputs, stamped against the
    # audio-frame clock. Kept separate from v11 because a build containing
    # the Voiceover table may already have opened and migrated a project.
    """
    ALTER TABLE voiceover_takes
        ADD COLUMN presentation_track_json TEXT NOT NULL DEFAULT '';
    """,
    # v13 - durable, immutable export queue inputs. The original export_jobs
    # row was history-only and its settings_json column was never written.
    # Keep that row for status/history compatibility, while the locked package,
    # plan, source ranges, and staged payloads live behind the job id. Identity
    # images and Voiceover audio are copied at queue time so template edits,
    # take deletion, or UI changes cannot alter a retry.
    """
    CREATE TABLE export_job_snapshots (
        job_id TEXT PRIMARY KEY
            REFERENCES export_jobs(id) ON DELETE CASCADE,
        package_json TEXT NOT NULL,
        composition_plan_json TEXT NOT NULL,
        manifest_json TEXT NOT NULL,
        integrity_sha256 TEXT NOT NULL,
        CHECK(length(package_json) > 2),
        CHECK(length(composition_plan_json) > 2),
        CHECK(length(manifest_json) > 2),
        CHECK(length(integrity_sha256) = 64)
    );

    CREATE TABLE export_job_identity_assets (
        job_id TEXT NOT NULL
            REFERENCES export_job_snapshots(job_id) ON DELETE CASCADE,
        role TEXT NOT NULL
            CHECK(role IN ('border', 'profile_photo', 'wordmark_logo')),
        sha256 TEXT NOT NULL,
        mime_type TEXT NOT NULL
            CHECK(mime_type IN ('image/png', 'image/jpeg', 'image/webp')),
        width INTEGER NOT NULL CHECK(width > 0),
        height INTEGER NOT NULL CHECK(height > 0),
        data BLOB NOT NULL,
        PRIMARY KEY (job_id, role),
        CHECK(length(sha256) = 64),
        CHECK(length(data) > 0)
    );
    CREATE INDEX idx_export_job_identity_digest
        ON export_job_identity_assets(sha256);

    CREATE TABLE export_job_voiceover_payloads (
        job_id TEXT PRIMARY KEY
            REFERENCES export_job_snapshots(job_id) ON DELETE CASCADE,
        take_id TEXT NOT NULL,
        project_id INTEGER NOT NULL CHECK(project_id > 0),
        clip_id TEXT NOT NULL,
        audio_input_value TEXT NOT NULL,
        presentation_input_value TEXT NOT NULL,
        ink_input_value TEXT NOT NULL DEFAULT '',
        source_anchor_ms INTEGER NOT NULL CHECK(source_anchor_ms >= 0),
        sample_rate INTEGER NOT NULL CHECK(sample_rate = 48000),
        channels INTEGER NOT NULL CHECK(channels = 1),
        frame_count INTEGER NOT NULL CHECK(frame_count > 0),
        duration_ms INTEGER NOT NULL CHECK(duration_ms >= 0),
        waveform_json TEXT NOT NULL CHECK(length(waveform_json) >= 2),
        audio_sha256 TEXT NOT NULL CHECK(length(audio_sha256) = 64),
        presentation_sha256 TEXT NOT NULL
            CHECK(length(presentation_sha256) = 64),
        audio_wav BLOB NOT NULL CHECK(length(audio_wav) >= 12),
        presentation_track_json TEXT NOT NULL
            CHECK(length(presentation_track_json) > 2),
        CHECK(length(take_id) > 0),
        CHECK(length(clip_id) > 0),
        CHECK(length(audio_input_value) > 0),
        CHECK(length(presentation_input_value) > 0)
    );
    """,
    # v14 - repair schema drift observed in a v13 project where the v8
    # review-batch tables were absent despite the current version stamp.
    """
    CREATE TABLE IF NOT EXISTS autodetect_review_batches (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL
            REFERENCES autodetect_sessions(id) ON DELETE CASCADE,
        start_ms INTEGER NOT NULL,
        end_ms INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active'
            CHECK(status IN ('active', 'completed', 'cancelled')),
        confirmation_json TEXT NOT NULL DEFAULT '{}',
        started_at TEXT NOT NULL,
        completed_at TEXT NOT NULL DEFAULT '',
        CHECK(start_ms >= 0),
        CHECK(end_ms > start_ms),
        UNIQUE(id, session_id)
    );
    CREATE INDEX IF NOT EXISTS idx_autodetect_review_batches_session
        ON autodetect_review_batches(session_id, started_at, id);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_autodetect_one_active_batch
        ON autodetect_review_batches(status)
        WHERE status = 'active';

    CREATE TABLE IF NOT EXISTS autodetect_recoveries (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL
            REFERENCES autodetect_sessions(id) ON DELETE CASCADE,
        batch_id TEXT NOT NULL,
        initial_clip_id TEXT NOT NULL,
        created_start_ms INTEGER NOT NULL,
        created_end_ms INTEGER NOT NULL,
        creation_method TEXT NOT NULL DEFAULT 'explicit',
        created_at TEXT NOT NULL,
        CHECK(created_start_ms >= 0),
        CHECK(created_end_ms > created_start_ms),
        FOREIGN KEY (batch_id, session_id)
            REFERENCES autodetect_review_batches(id, session_id)
            ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_autodetect_recoveries_session
        ON autodetect_recoveries(session_id, batch_id, created_at, id);
    """,
    # v15 - explicit game teams; old projects remain unassigned.
    """ALTER TABLE projects ADD COLUMN game_team_ids_json TEXT NOT NULL DEFAULT '[]';""",
    # v16 - source evidence travels with the project, outside disposable caches.
    """
    ALTER TABLE clips ADD COLUMN source_photo_json TEXT NOT NULL DEFAULT '{}';
    ALTER TABLE clips ADD COLUMN source_photo_png BLOB NOT NULL DEFAULT X'';
    """,
    # v17 - distinguish generated names from analyst-owned names.
    """ALTER TABLE clips ADD COLUMN generated_title TEXT;""",
    # v18 - remembered logging context belongs to a video, not global settings.
    """ALTER TABLE projects ADD COLUMN logging_defaults_json TEXT NOT NULL DEFAULT '{}';""",
    # v19 - recorded game year, independent of import/creation dates.
    """ALTER TABLE projects ADD COLUMN game_year TEXT NOT NULL DEFAULT '';""",
]


def assert_supported(conn: sqlite3.Connection) -> int:
    """Refuse a project from a newer build. Returns its schema version.

    Separate from `run_migrations` so a caller can check *before* writing
    anything: `PRAGMA journal_mode = WAL` rewrites the file header, and a
    project we are about to refuse should be left exactly as we found it.

    Opening one anyway is not harmless. ClipRepository reads and writes a
    fixed column list and ProjectSession._write replaces every clip row on
    save, so whatever a newer schema added survives until the first
    autosave and no further.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > len(MIGRATIONS):
        raise DatabaseError(
            f"This project was made by a newer version of TapeSift "
            f"(project schema v{current}, this build understands "
            f"v{len(MIGRATIONS)}).",
            "Update TapeSift to open it. Opening it with this build would "
            "discard the newer information the project contains.",
        )
    return current


def run_migrations(conn: sqlite3.Connection) -> None:
    current = assert_supported(conn)
    for version, script in enumerate(MIGRATIONS, start=1):
        if version > current:
            log.info("Applying database migration v%d", version)
            # One transaction for the schema change *and* the version bump.
            # executescript runs DDL in autocommit, so with the bump as a
            # separate statement a crash in between left the tables created
            # and the version unchanged - the next open replayed the same
            # migration onto tables that already existed, and the project
            # stopped opening at all. SQLite rolls DDL and user_version back
            # together, so this is all-or-nothing.
            conn.executescript(
                f"BEGIN;\n{script}\n"
                f"PRAGMA user_version = {version};\nCOMMIT;")
