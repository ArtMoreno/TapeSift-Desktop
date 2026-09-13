from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from tapesift.database.connection import open_project_db
from tapesift.database.migrations import MIGRATIONS
from tapesift.models.clip import Clip
from tapesift.services import autodetect_capture_service
from tapesift.services.autodetect_export_service import (
    build_correction_bundle,
    canonical_json,
    open_project_readonly,
    write_correction_bundle,
)
from tapesift.services.play_detect_service import REVIEW_PREFIX, REVIEW_TAG
from tapesift.services.project_service import ProjectSession


def test_detector_version_identifies_iteration_4d() -> None:
    assert autodetect_capture_service.DETECTOR_NAME == (
        "TapeSift Cadence Segmentation Engine"
    )
    assert autodetect_capture_service.DETECTOR_SHORT_NAME == "TapeSift CSE"
    assert autodetect_capture_service.DETECTOR_VERSION == "beta-4d"


def _candidate(
    index: int,
    start_ms: int,
    end_ms: int,
    *,
    kind: str = "play",
    needs_review: bool = True,
) -> dict:
    angle_start = start_ms + (end_ms - start_ms) // 2
    return {
        "candidate_kind": kind,
        "candidate_index": index,
        "detector_start_ms": start_ms,
        "detector_end_ms": end_ms,
        "created_start_ms": start_ms,
        "created_end_ms": end_ms,
        "angle_starts_ms": [start_ms, angle_start],
        "angle_count": 2,
        "needs_review": needs_review,
        "review_reason": "check both angles" if needs_review else "",
    }


def _capture(
    session: ProjectSession,
    clips: list[Clip],
    candidates: list[dict],
    *,
    private_root: str = "X:/Private Film",
) -> str:
    return session.add_detected_clips(
        clips,
        candidates,
        detector_id="tapesift-play-detect",
        detector_version="test-detector-v1",
        app_version="test-app-v1",
        source={
            "source": {
                "path": f"{private_root}/Home vs Away.mp4",
                "size_bytes": 1_234_567,
                "mtime_ns": 987_654_321,
            },
            "analysis_source": {
                "path": f"{private_root}/Home vs Away.proxy.mp4",
                "size_bytes": 234_567,
                "mtime_ns": 987_654_322,
            },
            "duration_ms": 120_000,
        },
        parameters={
            "separator_max_s": 5.0,
            "min_play_s": 4.0,
            "max_play_s": 90.0,
            "scene_threshold": 0.35,
        },
        result={
            "schema_version": "1.0",
            "summary": {
                "signal": "black",
                "spans_found": len(candidates),
                "separators_found": max(0, len(candidates) - 1),
                "duration_ms": 120_000,
            },
            "plays": [
                {
                    "start_ms": item["detector_start_ms"],
                    "end_ms": item["detector_end_ms"],
                }
                for item in candidates
                if item["candidate_kind"] == "play"
            ],
            "unclassified": [
                {
                    "start_ms": item["detector_start_ms"],
                    "end_ms": item["detector_end_ms"],
                }
                for item in candidates
                if item["candidate_kind"] == "unclassified"
            ],
        },
        ui_options={"first_camera_angle_only": False},
        runtime_seconds=1.25,
    )


def _create_v6_project(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        for version, script in enumerate(MIGRATIONS[:6], start=1):
            conn.executescript(script)
            conn.execute(f"PRAGMA user_version = {version}")
        conn.execute(
            """INSERT INTO projects (
                   name, source_video_path, source_duration_ms,
                   created_at, updated_at)
               VALUES (?,?,?,?,?)""",
            ("Legacy Game", "C:/film/legacy.mp4", 60_000, "before", "before"),
        )
        conn.execute(
            """INSERT INTO clips (
                   id, project_id, start_ms, end_ms, clip_title,
                   created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            ("legacy-clip", 1, 1_000, 9_000, "Legacy Play", "before", "before"),
        )
        conn.commit()
    finally:
        conn.close()


def test_v7_migrates_v6_and_round_trips_private_clip_lineage(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "Legacy.tapesift"
    _create_v6_project(project_path)

    conn = open_project_db(project_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "autodetect_sessions",
            "autodetect_candidates",
            "autodetect_events",
            "autodetect_review_batches",
            "autodetect_recoveries",
            "autodetect_coverage_reviews",
        } <= tables
        clip_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(clips)")
        }
        assert "detection_lineage_json" in clip_columns
        assert "analysis_json" in clip_columns
        assert conn.execute(
            "SELECT detection_lineage_json FROM clips WHERE id='legacy-clip'"
        ).fetchone()[0] == "{}"
        assert conn.execute(
            "SELECT analysis_json FROM clips WHERE id='legacy-clip'"
        ).fetchone()[0] == "{}"
    finally:
        conn.close()

    session = ProjectSession.open(project_path)
    detected = Clip(start_ms=20_000, end_ms=29_000, clip_title="Detected")
    session_id = _capture(
        session,
        [detected],
        [_candidate(0, 20_000, 29_000, needs_review=False)],
    )
    candidate_id = detected.detection_lineage["candidate_ids"][0]
    session.conn.close()

    reopened = ProjectSession.open(project_path)
    try:
        assert reopened.get_clip("legacy-clip").detection_lineage == {}
        assert reopened.get_clip(detected.id).detection_lineage == {
            "schema_version": "1.0",
            "session_id": session_id,
            "candidate_ids": [candidate_id],
            "origin_clip_id": detected.id,
            "derivation": "detected",
            "reviewed_at": "",
        }
    finally:
        reopened.conn.close()


def test_add_detected_clips_atomically_persists_session_candidates_and_clips(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Sample Game", tmp_path, tmp_path / "out")
    clips = [
        Clip(start_ms=1_000, end_ms=9_000, clip_title="Play 001"),
        Clip(
            start_ms=20_000,
            end_ms=34_000,
            clip_title=f"{REVIEW_PREFIX} Play 002",
            tags=[REVIEW_TAG],
        ),
    ]
    candidates = [
        _candidate(0, 1_000, 9_000, needs_review=False),
        _candidate(
            0, 20_000, 34_000, kind="unclassified", needs_review=True
        ),
    ]

    session_id = _capture(session, clips, candidates)

    stored_session = session.autodetect_repo.get_session(session_id)
    stored_candidates = session.autodetect_repo.list_candidates(session_id)
    events = session.autodetect_repo.list_events(session_id)
    assert stored_session is not None
    assert stored_session["parameters"]["scene_threshold"] == 0.35
    assert stored_session["result"]["plays"] == [
        {"start_ms": 1_000, "end_ms": 9_000}
    ]
    assert {
        (item["candidate_kind"], item["candidate_index"])
        for item in stored_candidates
    } == {("play", 0), ("unclassified", 0)}
    assert len({item["initial_clip_id"] for item in stored_candidates}) == 2
    assert [event["action"] for event in events] == ["create"]
    assert events[0]["before"] == []
    assert len(events[0]["after"]) == 2

    for clip in session.clips:
        assert clip.detection_lineage["session_id"] == session_id
        assert len(clip.detection_lineage["candidate_ids"]) == 1
        assert clip.detection_lineage["derivation"] == "detected"
    assert session.conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 2
    assert session.conn.execute(
        "SELECT COUNT(*) FROM autodetect_sessions"
    ).fetchone()[0] == 1
    assert session.conn.execute(
        "SELECT COUNT(*) FROM autodetect_candidates"
    ).fetchone()[0] == 2
    session.conn.close()

    reopened = ProjectSession.open(tmp_path / "Sample Game.tapesift")
    try:
        assert {
            clip.detection_lineage["session_id"] for clip in reopened.clips
        } == {session_id}
        assert len(reopened.autodetect_repo.list_candidates(session_id)) == 2
    finally:
        reopened.conn.close()


def test_identical_detection_rerun_reuses_ranges_without_adding_clips(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create(
        "Rerun Game", tmp_path, tmp_path / "out")
    first_clips = [
        Clip(start_ms=1_000, end_ms=9_000, clip_title="Play 001"),
        Clip(start_ms=20_000, end_ms=34_000, clip_title="Play 002"),
    ]
    candidates = [
        _candidate(0, 1_000, 9_000, needs_review=False),
        _candidate(1, 20_000, 34_000, needs_review=True),
    ]
    first_session_id = _capture(session, first_clips, candidates)
    original_ids = [clip.id for clip in session.clips]

    second_session_id = _capture(
        session,
        [
            Clip(start_ms=1_000, end_ms=9_000, clip_title="Play 001"),
            Clip(start_ms=20_000, end_ms=34_000, clip_title="Play 002"),
        ],
        copy.deepcopy(candidates),
    )

    assert second_session_id != first_session_id
    assert [clip.id for clip in session.clips] == original_ids
    assert session.last_detection_admission() == {
        "session_id": second_session_id,
        "candidate_count": 2,
        "added_clip_ids": [],
        "reused_clip_ids": original_ids,
    }
    stored = session.autodetect_repo.get_session(second_session_id)
    assert stored is not None
    assert stored["ui_options"]["admission"] == {
        "schema_version": "1.0",
        "candidate_count": 2,
        "added_clip_count": 0,
        "reused_clip_count": 2,
        "added_clip_ids": [],
        "reused_clip_ids": original_ids,
    }
    assert [
        item["initial_clip_id"]
        for item in session.autodetect_repo.list_candidates(
            second_session_id)
    ] == original_ids
    assert session.conn.execute(
        "SELECT COUNT(*) FROM autodetect_sessions"
    ).fetchone()[0] == 2
    assert session.conn.execute(
        "SELECT COUNT(*) FROM clips"
    ).fetchone()[0] == 2
    session.conn.close()


def test_confident_detection_reuses_manual_range_without_changing_metadata(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create(
        "Manual Match", tmp_path, tmp_path / "out")
    manual = Clip(
        start_ms=1_000,
        end_ms=9_000,
        clip_title="Q1 touchdown",
        tags=["TD"],
        details={"result": "Touchdown"},
    )
    session.add_clip(manual)

    session_id = _capture(
        session,
        [Clip(start_ms=1_000, end_ms=9_000, clip_title="Play 001")],
        [_candidate(0, 1_000, 9_000, needs_review=False)],
    )

    assert len(session.clips) == 1
    assert session.clips[0].id == manual.id
    assert session.clips[0].clip_title == "Q1 touchdown"
    assert session.clips[0].tags == ["TD"]
    assert session.clips[0].details == {"result": "Touchdown"}
    assert session.clips[0].detection_lineage == {}
    assert session.last_detection_admission()["reused_clip_ids"] == [
        manual.id]
    candidate = session.autodetect_repo.list_candidates(session_id)[0]
    assert candidate["initial_clip_id"] == manual.id
    session.conn.close()


def test_uncertain_detection_does_not_repurpose_manual_range(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create(
        "Manual Review Match", tmp_path, tmp_path / "out")
    manual = Clip(
        start_ms=1_000,
        end_ms=9_000,
        clip_title="Coach clip",
        notes="Keep this manual edit untouched.",
    )
    session.add_clip(manual)
    detected = Clip(
        start_ms=1_000,
        end_ms=9_000,
        clip_title=f"{REVIEW_PREFIX} Play 001",
        tags=[REVIEW_TAG],
    )

    session_id = _capture(
        session,
        [detected],
        [_candidate(0, 1_000, 9_000, needs_review=True)],
    )

    assert len(session.clips) == 2
    assert session.get_clip(manual.id).detection_lineage == {}
    assert session.get_clip(manual.id).notes == \
        "Keep this manual edit untouched."
    assert session.get_clip(detected.id).detection_lineage[
        "session_id"] == session_id
    assert session.last_detection_admission()["added_clip_ids"] == [
        detected.id]
    session.conn.close()


def test_detection_rerun_adds_only_ranges_that_are_actually_new(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create(
        "Partial Rerun", tmp_path, tmp_path / "out")
    original = Clip(start_ms=1_000, end_ms=9_000)
    _capture(
        session,
        [original],
        [_candidate(0, 1_000, 9_000, needs_review=False)],
    )
    new_clip = Clip(start_ms=20_000, end_ms=34_000)

    _capture(
        session,
        [
            Clip(start_ms=1_000, end_ms=9_000),
            new_clip,
        ],
        [
            _candidate(0, 1_000, 9_000, needs_review=False),
            _candidate(1, 20_000, 34_000, needs_review=False),
        ],
    )

    admission = session.last_detection_admission()
    assert admission["added_clip_ids"] == [new_clip.id]
    assert admission["reused_clip_ids"] == [original.id]
    assert {
        (clip.start_ms, clip.end_ms)
        for clip in session.clips
    } == {(1_000, 9_000), (20_000, 34_000)}
    session.conn.close()


def test_add_detected_clips_rolls_back_database_and_memory_on_capture_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = ProjectSession.create("Atomic Game", tmp_path, tmp_path / "out")
    original_create = session.autodetect_repo.create_session

    def fail_after_inserts(session_row, candidates, *, commit=True):
        original_create(session_row, candidates, commit=False)
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(
        session.autodetect_repo, "create_session", fail_after_inserts
    )
    clip = Clip(start_ms=1_000, end_ms=9_000)

    with pytest.raises(RuntimeError, match="simulated disk failure"):
        _capture(session, [clip], [_candidate(0, 1_000, 9_000)])

    assert session.conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 0
    assert session.conn.execute(
        "SELECT COUNT(*) FROM autodetect_sessions"
    ).fetchone()[0] == 0
    assert session.conn.execute(
        "SELECT COUNT(*) FROM autodetect_candidates"
    ).fetchone()[0] == 0
    assert session.conn.execute(
        "SELECT COUNT(*) FROM autodetect_events"
    ).fetchone()[0] == 0
    assert session.clips == []
    assert session._pending_autodetect_session is None
    assert session._capture_description == ""
    assert not session.can_undo()
    assert not session.can_redo()
    assert session.dirty is False

    # A failed local capture must not poison the open session. Once the
    # transient failure is gone, the exact action should be retryable.
    monkeypatch.setattr(
        session.autodetect_repo, "create_session", original_create
    )
    session_id = _capture(
        session,
        [Clip(start_ms=1_000, end_ms=9_000)],
        [_candidate(0, 1_000, 9_000)],
    )
    assert session.autodetect_repo.get_session(session_id) is not None
    session.conn.close()


def test_correction_events_preserve_split_duplicate_delete_undo_and_review(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Review Game", tmp_path, tmp_path / "out")
    source = Clip(
        start_ms=1_000,
        end_ms=9_000,
        clip_title=f"{REVIEW_PREFIX} Play 001",
        tags=[REVIEW_TAG],
    )
    session_id = _capture(
        session, [source], [_candidate(0, 1_000, 9_000)]
    )

    session.trim_clip_boundary(source.id, "start", 2_000)
    tail = session.split_clip(source.id, 5_000)
    assert tail is not None
    duplicate = session.duplicate_clip(source.id)
    assert duplicate is not None
    session.remove_clips([tail.id])
    assert session.undo() == "delete clips"
    assert session.mark_detection_reviewed([source.id, tail.id]) == 2

    events = session.autodetect_repo.list_events(session_id)
    assert [event["action"] for event in events] == [
        "create",
        "trim",
        "split",
        "duplicate",
        "delete",
        "undo",
        "review",
    ]
    assert [event["sequence"] for event in events] == list(
        range(1, len(events) + 1))
    trim = next(event for event in events if event["action"] == "trim")
    assert trim["before"][0]["start_ms"] == 1_000
    assert trim["after"][0]["start_ms"] == 2_000

    split = next(event for event in events if event["action"] == "split")
    assert len(split["before"]) == 1
    assert len(split["after"]) == 2
    assert {
        state["derivation"] for state in split["after"]
    } == {"detected", "split"}

    duplicate_event = next(
        event for event in events if event["action"] == "duplicate"
    )
    assert len(duplicate_event["after"]) == 3
    assert any(
        state["clip_id"] == duplicate.id
        and state["derivation"] == "duplicate"
        for state in duplicate_event["after"]
    )

    delete = next(event for event in events if event["action"] == "delete")
    undo = next(event for event in events if event["action"] == "undo")
    assert any(state["clip_id"] == tail.id for state in delete["before"])
    assert not any(state["clip_id"] == tail.id for state in delete["after"])
    assert any(state["clip_id"] == tail.id for state in undo["after"])

    truth_clips = [
        clip
        for clip in session.clips
        if clip.detection_lineage.get("derivation") != "duplicate"
    ]
    assert len(truth_clips) == 2
    assert all(clip.detection_lineage["reviewed_at"] for clip in truth_clips)
    assert all(REVIEW_TAG not in clip.tags for clip in truth_clips)
    assert all(
        not clip.clip_title.startswith(f"{REVIEW_PREFIX} ")
        for clip in truth_clips
    )

    bundle = build_correction_bundle(
        session.conn, session_selector=session_id
    )
    result = bundle["candidates"][0]
    assert result["geometry_outcome"] == "split"
    assert result["review_status"] == "reviewed"
    assert result["linked_clip_ids"] == sorted([source.id, tail.id])
    assert result["alternate_export_clip_ids"] == [duplicate.id]
    session.conn.close()


def test_export_is_deterministic_redacted_and_idempotent(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Private Game", tmp_path, tmp_path / "out")
    secret_root = "D:/Secret Recruiting/Miami"
    clip = Clip(start_ms=1_000, end_ms=9_000, clip_title="Play 001")
    session_id = _capture(
        session,
        [clip],
        [_candidate(0, 1_000, 9_000, needs_review=False)],
        private_root=secret_root,
    )
    assert session.mark_detection_reviewed([clip.id]) == 1

    first = build_correction_bundle(
        session.conn, session_selector=session_id
    )
    second = build_correction_bundle(
        session.conn, session_selector=session_id
    )
    assert first == second
    encoded = canonical_json(first)
    assert secret_root not in encoded
    assert "987654321" not in encoded
    assert "1234567" not in encoded
    assert "redacted://source/" in encoded
    assert "redacted://analysis/" in encoded
    assert first["report"]["review_complete"] is True
    assert first["benchmark_ready"] is False
    assert first["report"]["eligible_for_accuracy_metrics"] is False
    assert first["report"]["eligible_for_generalization_claim"] is False
    assert first["report"]["accuracy_metrics"] is None

    unhashed = copy.deepcopy(first)
    expected_hash = unhashed.pop("capture_sha256")
    assert hashlib.sha256(
        canonical_json(unhashed).encode("utf-8")
    ).hexdigest() == expected_hash

    local = build_correction_bundle(
        session.conn,
        session_selector=session_id,
        include_local_paths=True,
    )
    assert local["session"]["source"]["source"]["path"].startswith(secret_root)

    output = tmp_path / "exports" / "autodetect.json"
    assert write_correction_bundle(output, first) is True
    assert write_correction_bundle(output, first) is False
    assert json.loads(output.read_text(encoding="utf-8")) == first
    project_path = session.db_path
    session.close()

    readonly = open_project_readonly(project_path)
    try:
        assert build_correction_bundle(
            readonly, session_selector=session_id) == first
    finally:
        readonly.close()


def test_exclusion_is_not_review_and_film_id_survives_repeat_detection(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Repeat Game", tmp_path, tmp_path / "out")
    first_clip = Clip(start_ms=1_000, end_ms=9_000)
    first_id = _capture(
        session, [first_clip], [_candidate(0, 1_000, 9_000)])
    session.set_clip_enabled(first_clip.id, False)

    excluded = build_correction_bundle(
        session.conn, session_selector=first_id)
    candidate = excluded["candidates"][0]
    assert candidate["geometry_outcome"] == "excluded"
    assert candidate["review_status"] == "excluded_unconfirmed"
    assert candidate["verification_status"] == "seed_unverified"
    assert excluded["report"]["review_complete"] is False

    second_id = _capture(
        session,
        [Clip(start_ms=20_000, end_ms=29_000)],
        [_candidate(0, 20_000, 29_000)],
    )
    repeated = build_correction_bundle(
        session.conn, session_selector=second_id)
    assert repeated["film_id"] == excluded["film_id"]
    session.conn.close()


def test_geometry_change_requires_review_again_and_undo_restores_confirmation(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Review Reset", tmp_path, tmp_path / "out")
    clip = Clip(start_ms=1_000, end_ms=9_000)
    session_id = _capture(
        session, [clip], [_candidate(0, 1_000, 9_000)])
    session.mark_detection_reviewed([clip.id])
    assert clip.detection_lineage["reviewed_at"]

    session.trim_clip_boundary(clip.id, "start", 2_000)
    assert clip.detection_lineage["reviewed_at"] == ""
    changed = build_correction_bundle(
        session.conn, session_selector=session_id)
    assert changed["candidates"][0]["review_status"] == \
        "corrected_unconfirmed"

    assert session.undo() == "trim clip"
    restored = session.get_clip(clip.id)
    assert restored is not None
    assert restored.start_ms == 1_000
    assert restored.detection_lineage["reviewed_at"]
    undone = build_correction_bundle(
        session.conn, session_selector=session_id)
    assert undone["candidates"][0]["review_status"] == "reviewed"

    assert session.redo() == "trim clip"
    redone = session.get_clip(clip.id)
    assert redone is not None
    assert redone.start_ms == 2_000
    assert redone.detection_lineage["reviewed_at"] == ""
    session.conn.close()


def test_splitting_an_alternate_export_never_creates_an_extra_truth_range(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Alternate", tmp_path, tmp_path / "out")
    clip = Clip(start_ms=1_000, end_ms=9_000, clip_title="Play")
    session_id = _capture(
        session, [clip], [_candidate(0, 1_000, 9_000)])
    duplicate = session.duplicate_clip(clip.id)
    assert duplicate is not None
    duplicate_tail = session.split_clip(duplicate.id, 5_000)
    assert duplicate_tail is not None
    assert duplicate_tail.detection_lineage["derivation"] == \
        "duplicate_split"

    bundle = build_correction_bundle(
        session.conn, session_selector=session_id)
    result = bundle["candidates"][0]
    assert result["geometry_outcome"] == "unchanged"
    assert result["final_ranges"] == [{"start_ms": 1_000, "end_ms": 9_000}]
    assert result["alternate_export_clip_ids"] == sorted(
        [duplicate.id, duplicate_tail.id])
    session.conn.close()


def test_readonly_cli_keeps_v7_capture_compatible(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project_path = tmp_path / "Readonly-v7.tapesift"
    session = ProjectSession.create(
        "Readonly-v7", tmp_path, tmp_path / "out")
    detected = Clip(start_ms=1_000, end_ms=9_000)
    session_id = _capture(
        session,
        [detected],
        [_candidate(0, 1_000, 9_000)],
    )
    session.conn.execute("DROP TABLE autodetect_recoveries")
    session.conn.execute("DROP TABLE autodetect_review_batches")
    session.conn.execute("PRAGMA user_version = 7")
    session.conn.commit()
    session.conn.close()
    assert project_path.is_file()

    from scripts.export_autodetect_session import main as export_main

    assert export_main([
        "batches",
        "--project", str(project_path),
        "--session", session_id,
    ]) == 0
    assert capsys.readouterr().out.strip() == \
        "No autodetect review batches."

    assert export_main([
        "score",
        "--project", str(project_path),
        "--session", session_id,
    ]) == 2
    assert "review batch not found" in capsys.readouterr().err.lower()
