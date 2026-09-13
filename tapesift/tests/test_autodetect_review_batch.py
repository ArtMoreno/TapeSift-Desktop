"""Focused coverage for short, bounded autodetect development batches."""

from __future__ import annotations

import os
import json
import sqlite3
from pathlib import Path

import pytest

from tapesift.core.exceptions import DatabaseError
from tapesift.models.clip import Clip
from tapesift.services.autodetect_export_service import (
    _validate_candidate_capture,
    _overlapping_truth_pairs,
    build_correction_bundle,
    canonical_json,
    completion_snapshot_sha256,
)
from tapesift.services.autodetect_score_service import (
    build_development_batch_score,
)
from tapesift.services.project_service import ProjectSession


def _capture(
    session: ProjectSession,
    ranges: list[tuple[int, int, int, int, str]],
) -> tuple[str, list[Clip]]:
    """(detector start/end, applied start/end, kind) capture fixture."""
    duration_ms = max(
        30_000,
        max(detector_end for _, detector_end, _, _, _ in ranges) + 1_000,
    )
    source_path = session.db_path.with_name("fixture-film.mp4")
    source_path.write_bytes(b"tapesift-test-film")
    session.project.source_video_path = str(source_path)
    session.project.source_duration_ms = duration_ms
    clips = [
        Clip(start_ms=applied_start, end_ms=applied_end)
        for _, _, applied_start, applied_end, _ in ranges
    ]
    candidate_indexes = {"play": 0, "unclassified": 0}
    candidates = []
    for (
        detector_start,
        detector_end,
        applied_start,
        applied_end,
        kind,
    ) in ranges:
        candidate_index = candidate_indexes[kind]
        candidate_indexes[kind] += 1
        candidates.append({
            "candidate_kind": kind,
            "candidate_index": candidate_index,
            "detector_start_ms": detector_start,
            "detector_end_ms": detector_end,
            "created_start_ms": applied_start,
            "created_end_ms": applied_end,
            "angle_starts_ms": [detector_start],
            "angle_count": 1,
            "needs_review": False,
            "review_reason": "",
        })
    source_stat = source_path.stat()
    session_id = session.add_detected_clips(
        clips,
        candidates,
        detector_id="tapesift-play-detect",
        detector_version="test-v1",
        app_version="test-app",
        source={
            "film_id": "film_test",
            "duration_ms": duration_ms,
            "source": {
                "path": str(source_path),
                "size_bytes": source_stat.st_size,
                "mtime_ns": source_stat.st_mtime_ns,
            },
            "analysis_source": {
                "path": str(source_path),
                "size_bytes": source_stat.st_size,
                "mtime_ns": source_stat.st_mtime_ns,
            },
        },
        parameters={},
        result={
            "schema_version": "1.0",
            "plays": [
                {"start_ms": detector_start, "end_ms": detector_end}
                for detector_start, detector_end, _, _, kind in ranges
                if kind == "play"
            ],
            "unclassified": [
                {"start_ms": detector_start, "end_ms": detector_end}
                for detector_start, detector_end, _, _, kind in ranges
                if kind == "unclassified"
            ],
        },
        ui_options={},
        runtime_seconds=0.5,
    )
    return session_id, clips


def test_completed_short_batch_scores_true_false_and_missed_plays(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Scoped", tmp_path, tmp_path / "out")
    session_id, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
        (12_000, 18_000, 12_000, 18_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 30_000)
    assert batch["session_id"] == session_id

    assert session.mark_detection_reviewed([clips[0].id]) == 1
    assert session.confirm_detection_false_positive([clips[1].id]) == 1

    manual = Clip(
        start_ms=22_000,
        end_ms=28_000,
        clip_title="PRIVATE Miami recovered snap",
        notes="PRIVATE scouting note",
        tags=["PRIVATE"],
    )
    session.add_clip(manual)
    assert session.mark_missed_detection([manual.id]) == 1
    recovery_id = manual.detection_lineage["recovery_id"]
    assert manual.detection_lineage["batch_id"] == batch["id"]
    assert manual.detection_lineage["reviewed_at"]

    completed = session.complete_autodetect_review_batch(
        batch_id=batch["id"])
    assert completed["status"] == "completed"
    assert session.active_autodetect_review_batch() is None

    bundle = build_correction_bundle(
        session.conn, session_selector=session_id)
    exported_batch = bundle["review_batches"][0]
    assert exported_batch["review_complete"] is True
    assert exported_batch[
        "eligible_for_scoped_development_metrics"] is True
    assert exported_batch["recovery_ids"] == [recovery_id]
    score = exported_batch["development_score"]
    metrics = score["primary_metrics"]
    assert metrics["prediction_count"] == 2
    assert metrics["truth_count"] == 2
    assert metrics["matched_count"] == 1
    assert metrics["false_positive_count"] == 1
    assert metrics["false_negative_count"] == 1
    assert metrics["precision"] == pytest.approx(0.5)
    assert metrics["recall"] == pytest.approx(0.5)
    assert score["all_one_to_one_boundary_metrics"][
        "median_max_boundary_error_ms"] == 0
    assert bundle["benchmark_ready"] is False
    assert bundle["report"]["eligible_for_accuracy_metrics"] is False
    encoded = canonical_json(bundle)
    assert "PRIVATE Miami" not in encoded
    assert "PRIVATE scouting" not in encoded
    session.conn.close()


def test_manual_recovery_undo_redo_split_review_and_duplicate_lifecycle(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Recovery", tmp_path, tmp_path / "out")
    session_id, detected = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    session.start_autodetect_review_batch(0, 30_000)
    session.mark_detection_reviewed([detected[0].id])
    manual = session.add_clip(Clip(start_ms=12_000, end_ms=20_000))
    session.mark_missed_detection([manual.id])
    recovery_id = manual.detection_lineage["recovery_id"]

    assert session.undo() == "mark 1 missed autodetect play"
    assert session.get_clip(manual.id).detection_lineage == {}
    withdrawn = build_correction_bundle(
        session.conn, session_selector=session_id)
    assert withdrawn["manual_recoveries"][0]["review_status"] == "withdrawn"

    assert session.redo() == "mark 1 missed autodetect play"
    restored = session.get_clip(manual.id)
    assert restored.detection_lineage["recovery_id"] == recovery_id

    tail = session.split_clip(restored.id, 16_000)
    assert tail is not None
    assert restored.detection_lineage["reviewed_at"] == ""
    assert tail.detection_lineage["reviewed_at"] == ""
    assert tail.detection_lineage["recovery_id"] == recovery_id
    assert session.mark_detection_reviewed([restored.id, tail.id]) == 2

    duplicate = session.duplicate_clip(restored.id)
    assert duplicate is not None
    bundle = build_correction_bundle(
        session.conn, session_selector=session_id)
    recovery = bundle["manual_recoveries"][0]
    assert recovery["geometry_outcome"] == "split"
    assert recovery["review_status"] == "reviewed"
    assert recovery["linked_clip_ids"] == sorted([restored.id, tail.id])
    assert recovery["alternate_export_clip_ids"] == [duplicate.id]
    session.conn.close()


def test_manual_recovery_rolls_back_database_and_memory_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = ProjectSession.create("Atomic recovery", tmp_path, tmp_path / "out")
    _, detected = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    session.start_autodetect_review_batch(0, 20_000)
    session.mark_detection_reviewed([detected[0].id])
    manual = session.add_clip(Clip(start_ms=12_000, end_ms=18_000))
    undo_count = len(session._undo_stack)
    original_create = session.autodetect_repo.create_recovery

    def fail_after_insert(recovery, *, commit=True):
        original_create(recovery, commit=False)
        raise RuntimeError("simulated recovery write failure")

    monkeypatch.setattr(
        session.autodetect_repo, "create_recovery", fail_after_insert)
    with pytest.raises(RuntimeError, match="simulated recovery"):
        session.mark_missed_detection([manual.id])

    restored = session.get_clip(manual.id)
    assert restored is not None
    assert restored.detection_lineage == {}
    assert len(session._undo_stack) == undo_count
    assert session._pending_autodetect_recoveries == []
    assert session.conn.execute(
        "SELECT COUNT(*) FROM autodetect_recoveries").fetchone()[0] == 0

    monkeypatch.setattr(
        session.autodetect_repo, "create_recovery", original_create)
    assert session.mark_missed_detection([manual.id]) == 1
    assert session.conn.execute(
        "SELECT COUNT(*) FROM autodetect_recoveries").fetchone()[0] == 1
    session.conn.close()


def test_batch_start_and_finish_fail_closed(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Guarded", tmp_path, tmp_path / "out")
    _, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    with pytest.raises(DatabaseError, match="cuts through"):
        session.start_autodetect_review_batch(2_000, 12_000)

    batch = session.start_autodetect_review_batch(0, 12_000)
    with pytest.raises(DatabaseError, match="unconfirmed corrections"):
        session.complete_autodetect_review_batch(batch_id=batch["id"])

    session.mark_detection_reviewed([clips[0].id])
    completed = session.complete_autodetect_review_batch(
        batch_id=batch["id"])
    confirmation = completed["confirmation"]
    assert confirmation["all_missed_plays_added"] is True
    assert confirmation["complete_source_range_reviewed"] is True
    assert confirmation["scope"] == "contiguous_source_range"
    assert len(confirmation["frozen_candidates"]) == 1
    assert confirmation["frozen_manual_recoveries"] == []
    session.conn.close()


def test_score_uses_applied_first_angle_bounds_and_primary_iou_075(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Applied", tmp_path, tmp_path / "out")
    session_id, clips = _capture(session, [
        (1_000, 10_000, 1_000, 5_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 12_000)
    session.mark_detection_reviewed([clips[0].id])
    session.complete_autodetect_review_batch(batch_id=batch["id"])

    bundle = build_correction_bundle(
        session.conn, session_selector=session_id)
    score = bundle["review_batches"][0]["development_score"]
    assert score["primary_metrics"]["iou_threshold"] == 0.75
    assert score["primary_metrics"]["precision"] == 1.0
    assert score["primary_metrics"]["recall"] == 1.0
    assert score["all_one_to_one_boundary_metrics"][
        "median_max_boundary_error_ms"] == 0
    session.conn.close()


def test_batch_rejects_overlapping_duplicate_truth_roots(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Overlap", tmp_path, tmp_path / "out")
    _, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 12_000)
    session.mark_detection_reviewed([clips[0].id])
    duplicate_truth = session.add_clip(Clip(start_ms=1_000, end_ms=9_000))
    session.mark_missed_detection([duplicate_truth.id])

    with pytest.raises(DatabaseError, match="same play"):
        session.complete_autodetect_review_batch(batch_id=batch["id"])
    exported = build_correction_bundle(session.conn)
    assert exported["review_batches"][0]["overlapping_truth_pairs"]
    assert exported["review_batches"][0]["review_complete"] is False
    session.conn.close()


def test_zero_truth_or_prediction_denominators_are_not_measurable() -> None:
    excluded_prediction = {
        "candidate_id": "candidate-fp",
        "candidate_kind": "play",
        "created_start_ms": 1_000,
        "created_end_ms": 9_000,
        "review_status": "reviewed",
        "final_ranges": [],
    }
    score = build_development_batch_score(
        [excluded_prediction], [], sample_complete=True)
    metrics = score["primary_metrics"]
    assert metrics["precision"] == 0.0
    assert metrics["recall"] is None
    assert metrics["f1"] is None
    assert score["personal_use_target_progress"]["recall"]["passed"] is None


def test_active_score_is_provisional_and_all_false_positive_batch_completes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session = ProjectSession.create("All FP", tmp_path, tmp_path / "out")
    session_id, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 12_000)
    assert session.confirm_detection_false_positive([clips[0].id]) == 1

    active = build_correction_bundle(
        session.conn, session_selector=session_id)["review_batches"][0]
    assert active["status"] == "active"
    assert active["review_complete"] is True
    assert active["eligible_for_scoped_development_metrics"] is False
    assert active["development_score"]["sample_complete"] is False
    assert active["development_score"]["ready_for_tuning"] is False
    assert "Provisional" in active["development_score"]["scope"]

    session.complete_autodetect_review_batch(batch_id=batch["id"])
    completed_bundle = build_correction_bundle(
        session.conn, session_selector=session_id)
    completed = completed_bundle["review_batches"][0]
    metrics = completed["development_score"]["primary_metrics"]
    assert completed["eligible_for_scoped_development_metrics"] is True
    assert completed["development_score"]["sample_complete"] is True
    assert metrics["precision"] == 0.0
    assert metrics["recall"] is None

    from scripts.export_autodetect_session import main as export_main
    assert export_main([
        "score",
        "--project", str(session.db_path),
        "--session", session_id,
        "--batch", batch["id"],
    ]) == 0
    score_export = json.loads(capsys.readouterr().out)
    assert score_export["film_id"] == completed_bundle["film_id"]
    assert score_export["session_id"] == session_id
    assert score_export["batch_id"] == batch["id"]
    assert score_export["privacy_mode"] == "source_identity_redacted"
    assert "path" not in json.dumps(score_export["detector"])
    session.conn.close()


def test_completed_batch_score_is_frozen_after_later_edits_and_undo(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Frozen", tmp_path, tmp_path / "out")
    session_id, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 12_000)
    session.mark_detection_reviewed([clips[0].id])
    session.complete_autodetect_review_batch(batch_id=batch["id"])

    def completed_result() -> dict:
        return build_correction_bundle(
            session.conn,
            session_selector=session_id,
        )["review_batches"][0]

    before = completed_result()
    assert before["uses_frozen_completion_snapshot"] is True
    assert before["confirmed_candidates"][0]["final_ranges"] == [
        {"start_ms": 1_000, "end_ms": 9_000},
    ]

    session.trim_clip_boundary(clips[0].id, "start", 2_000)
    after_trim = completed_result()
    assert after_trim["development_score"] == before["development_score"]
    assert after_trim["confirmed_candidates"] == before[
        "confirmed_candidates"]

    session.undo()
    session.redo()
    after_redo = completed_result()
    assert after_redo["development_score"] == before["development_score"]
    session.conn.close()


def test_manual_miss_generic_delete_is_pending_but_explicit_withdrawal_is_not(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Withdraw", tmp_path, tmp_path / "out")
    session_id, detected = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 25_000)
    session.mark_detection_reviewed([detected[0].id])
    manual = session.add_clip(Clip(start_ms=12_000, end_ms=18_000))
    session.mark_missed_detection([manual.id])

    session.remove_clips([manual.id])
    deleted = build_correction_bundle(
        session.conn, session_selector=session_id)
    assert deleted["manual_recoveries"][0][
        "review_status"] == "withdrawn_unconfirmed"
    assert deleted["review_batches"][0]["pending_recovery_count"] == 1
    with pytest.raises(DatabaseError, match="unconfirmed corrections"):
        session.complete_autodetect_review_batch(batch_id=batch["id"])

    session.undo()
    assert session.withdraw_missed_detection([manual.id]) == 1
    withdrawn = build_correction_bundle(
        session.conn, session_selector=session_id)
    assert withdrawn["manual_recoveries"][0]["review_status"] == "withdrawn"
    assert withdrawn["review_batches"][0]["pending_recovery_count"] == 0
    session.complete_autodetect_review_batch(batch_id=batch["id"])
    session.conn.close()


def test_cancel_clears_batch_reviews_and_recovery_lineage_for_clean_retry(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Cancel", tmp_path, tmp_path / "out")
    _, detected = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 25_000)
    session.mark_detection_reviewed([detected[0].id])
    assert detected[0].detection_lineage["review_batch_id"] == batch["id"]
    manual = session.add_clip(Clip(start_ms=12_000, end_ms=18_000))
    session.mark_missed_detection([manual.id])
    session.trim_clip_boundary(manual.id, "end", 19_000)

    cancelled = session.cancel_autodetect_review_batch(
        batch_id=batch["id"])
    assert cancelled["status"] == "cancelled"
    assert session.get_clip(manual.id).detection_lineage == {}
    assert detected[0].detection_lineage["reviewed_at"] == ""
    assert detected[0].detection_lineage["review_batch_id"] == ""
    session.undo()
    assert session.get_clip(manual.id).detection_lineage == {}
    assert detected[0].detection_lineage["reviewed_at"] == ""
    session.redo()
    assert session.get_clip(manual.id).detection_lineage == {}
    retried = session.start_autodetect_review_batch(0, 25_000)
    assert retried["status"] == "active"
    retried_result = next(
        item for item in build_correction_bundle(
            session.conn)["review_batches"]
        if item["id"] == retried["id"]
    )
    assert retried_result["pending_candidate_count"] == 1
    session.conn.close()


def test_source_mtime_and_missing_file_fail_closed(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Identity", tmp_path, tmp_path / "out")
    _, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 12_000)
    session.mark_detection_reviewed([clips[0].id])
    source_path = Path(session.project.source_video_path)
    original = source_path.stat()
    os.utime(
        source_path,
        ns=(original.st_atime_ns, original.st_mtime_ns + 2_000_000_000),
    )
    with pytest.raises(DatabaseError, match="changed after autodetect"):
        session.complete_autodetect_review_batch(batch_id=batch["id"])
    assert session.active_autodetect_review_batch()["id"] == batch["id"]

    os.utime(
        source_path,
        ns=(original.st_atime_ns, original.st_mtime_ns),
    )
    source_path.unlink()
    with pytest.raises(DatabaseError, match="missing or unreadable"):
        session.complete_autodetect_review_batch(batch_id=batch["id"])
    session.conn.close()


def test_completion_snapshot_rolls_back_if_repository_update_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = ProjectSession.create("Atomic complete", tmp_path, tmp_path / "out")
    _, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 12_000)
    session.mark_detection_reviewed([clips[0].id])
    original_complete = session.autodetect_repo.complete_review_batch

    def update_then_fail(batch_id, *, confirmation, completed_at=None,
                         commit=True):
        assert session.conn.in_transaction
        assert commit is False
        original_complete(
            batch_id,
            confirmation=confirmation,
            completed_at=completed_at,
            commit=False,
        )
        raise RuntimeError("simulated completion failure")

    monkeypatch.setattr(
        session.autodetect_repo,
        "complete_review_batch",
        update_then_fail,
    )
    with pytest.raises(RuntimeError, match="simulated completion"):
        session.complete_autodetect_review_batch(batch_id=batch["id"])
    stored = session.autodetect_repo.get_review_batch(batch["id"])
    assert stored["status"] == "active"
    assert stored["confirmation"] == batch["confirmation"]
    session.conn.close()


def test_candidate_capture_tampering_blocks_completion(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Tampered", tmp_path, tmp_path / "out")
    session_id, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
        (12_000, 18_000, 12_000, 18_000, "unclassified"),
    ])
    batch = session.start_autodetect_review_batch(0, 22_000)
    session.mark_detection_reviewed([clip.id for clip in clips])
    session.conn.execute(
        """UPDATE autodetect_candidates
           SET detector_start_ms=detector_start_ms + 1
           WHERE session_id=? AND candidate_kind='play'""",
        (session_id,),
    )
    session.conn.commit()

    bundle = build_correction_bundle(
        session.conn, session_selector=session_id)
    assert bundle["candidate_capture_validation"]["valid"] is False
    with pytest.raises(DatabaseError, match="immutable detector result"):
        session.complete_autodetect_review_batch(batch_id=batch["id"])
    session.conn.close()


def test_candidate_capture_accepts_ledger_only_unclassified_sections() -> None:
    session = {
        "result": {
            "plays": [{"start_ms": 1_000, "end_ms": 9_000}],
            "unclassified": [{"start_ms": 9_000, "end_ms": 12_000}],
        },
        "ui_options": {"unclassified_materialized": False},
    }
    play_candidate = {
        "id": "play-0",
        "candidate_kind": "play",
        "candidate_index": 0,
        "detector_start_ms": 1_000,
        "detector_end_ms": 9_000,
    }

    validation = _validate_candidate_capture(session, [play_candidate])

    assert validation == {
        "valid": True,
        "errors": [],
        "candidate_count": 1,
        "immutable_result_root_count": 1,
        "omitted_detector_play_ranges": [],
        "raw_detector_play_count": 1,
    }


def test_candidate_capture_requires_every_materialized_unclassified_root() -> None:
    session = {
        "result": {
            "plays": [{"start_ms": 1_000, "end_ms": 9_000}],
            "unclassified": [
                {"start_ms": 9_000, "end_ms": 12_000},
                {"start_ms": 12_000, "end_ms": 15_000},
            ],
        },
        "ui_options": {"unclassified_materialized": True},
    }
    candidates = [
        {
            "id": "play-0",
            "candidate_kind": "play",
            "candidate_index": 0,
            "detector_start_ms": 1_000,
            "detector_end_ms": 9_000,
        },
        {
            "id": "unclassified-0",
            "candidate_kind": "unclassified",
            "candidate_index": 0,
            "detector_start_ms": 9_000,
            "detector_end_ms": 12_000,
        },
    ]

    validation = _validate_candidate_capture(session, candidates)

    assert validation["valid"] is False
    assert validation["errors"] == [
        "missing captured unclassified candidate index 1"
    ]


def test_truth_overlap_guard_rejects_any_intersection_but_not_touching() -> None:
    assert _overlapping_truth_pairs([
        {"root_id": "one", "start_ms": 1_000, "end_ms": 2_000},
        {"root_id": "two", "start_ms": 1_999, "end_ms": 3_000},
    ])
    assert _overlapping_truth_pairs([
        {"root_id": "one", "start_ms": 1_000, "end_ms": 2_000},
        {"root_id": "one", "start_ms": 1_999, "end_ms": 3_000},
    ])
    assert _overlapping_truth_pairs([
        {"root_id": "one", "start_ms": 1_000, "end_ms": 2_000},
        {"root_id": "two", "start_ms": 2_000, "end_ms": 3_000},
    ]) == []


def test_v8_constraints_reject_two_active_batches_and_cross_session_recovery(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Constraints", tmp_path, tmp_path / "out")
    session_id, _ = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    second_session_id = f"{session_id}_second"
    session.conn.execute(
        """INSERT INTO autodetect_sessions
           SELECT ?, project_id, schema_version, detector_id,
                  detector_version, app_version, source_json,
                  parameters_json, result_json, ui_options_json,
                  provenance_json, runtime_seconds, created_at
           FROM autodetect_sessions WHERE id=?""",
        (second_session_id, session_id),
    )
    session.conn.commit()
    batch = session.start_autodetect_review_batch(
        0, 12_000, session_id=session_id)
    with pytest.raises(sqlite3.IntegrityError):
        session.autodetect_repo.create_review_batch({
            "id": "batch_second",
            "session_id": second_session_id,
            "start_ms": 0,
            "end_ms": 12_000,
            "status": "active",
            "confirmation": {},
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "",
        })
    session.conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        session.autodetect_repo.create_recovery({
            "id": "recovery_wrong_session",
            "session_id": second_session_id,
            "batch_id": batch["id"],
            "initial_clip_id": "manual",
            "created_start_ms": 10_000,
            "created_end_ms": 11_000,
            "creation_method": "test",
            "created_at": "2026-01-01T00:00:00+00:00",
        })
    session.conn.rollback()
    session.conn.close()


def test_completed_ranges_cannot_be_reused_but_touching_range_can(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Fresh ranges", tmp_path, tmp_path / "out")
    _, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
        (15_000, 22_000, 15_000, 22_000, "play"),
    ])
    first = session.start_autodetect_review_batch(0, 12_000)
    session.mark_detection_reviewed([clips[0].id])
    session.complete_autodetect_review_batch(batch_id=first["id"])

    with pytest.raises(DatabaseError, match="overlaps a completed"):
        session.start_autodetect_review_batch(8_000, 25_000)
    second = session.start_autodetect_review_batch(12_000, 25_000)
    assert second["start_ms"] == 12_000
    session.conn.close()


def test_non_owned_candidate_truth_cannot_move_into_active_batch(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Intruding truth", tmp_path, tmp_path / "out")
    _, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
        (22_000, 28_000, 22_000, 28_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 20_000)
    session.trim_clip_boundary(clips[1].id, "start", 12_000)
    session.trim_clip_boundary(clips[1].id, "end", 18_000)
    session.mark_detection_reviewed([clips[0].id, clips[1].id])

    active = build_correction_bundle(session.conn)["review_batches"][0]
    assert active["intruding_candidate_truth_ranges"] == [{
        "candidate_id": clips[1].detection_lineage["candidate_ids"][0],
        "start_ms": 12_000,
        "end_ms": 18_000,
    }]
    with pytest.raises(DatabaseError, match="outside the test batch"):
        session.complete_autodetect_review_batch(batch_id=batch["id"])
    session.cancel_autodetect_review_batch(batch_id=batch["id"])
    with pytest.raises(DatabaseError, match="boundaries do not match"):
        session.start_autodetect_review_batch(0, 20_000)
    session.conn.close()


def test_undo_split_retracts_candidate_and_recovery_child_roots(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Undo split", tmp_path, tmp_path / "out")
    session_id, detected = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 25_000)
    session.mark_detection_reviewed([detected[0].id])
    session.split_clip(detected[0].id, 5_000)
    assert session.undo() == "split clip"

    manual = session.add_clip(Clip(start_ms=12_000, end_ms=18_000))
    session.mark_missed_detection([manual.id])
    session.split_clip(manual.id, 15_000)
    assert session.undo() == "split clip"

    bundle = build_correction_bundle(
        session.conn, session_selector=session_id)
    assert bundle["candidates"][0]["review_status"] == "reviewed"
    assert bundle["manual_recoveries"][0]["review_status"] == "reviewed"
    session.complete_autodetect_review_batch(batch_id=batch["id"])
    session.conn.close()


def test_review_decisions_restore_memory_when_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for action in ("review", "not_play", "withdraw"):
        folder = tmp_path / action
        folder.mkdir()
        session = ProjectSession.create(
            action, folder, folder / "out")
        _, detected = _capture(session, [
            (1_000, 9_000, 1_000, 9_000, "play"),
        ])
        session.start_autodetect_review_batch(0, 25_000)
        target = detected[0]
        if action == "withdraw":
            session.mark_detection_reviewed([detected[0].id])
            target = session.add_clip(
                Clip(start_ms=12_000, end_ms=18_000))
            session.mark_missed_detection([target.id])

        before_lineage = dict(target.detection_lineage)
        before_enabled = target.enabled
        before_undo_count = len(session._undo_stack)
        before_description = session._capture_description
        original_save_many = session.clip_repo.save_many

        def fail_save(*_args, **_kwargs):
            raise RuntimeError("simulated clip persistence failure")

        monkeypatch.setattr(session.clip_repo, "save_many", fail_save)
        with pytest.raises(RuntimeError, match="simulated clip"):
            if action == "review":
                session.mark_detection_reviewed([target.id])
            elif action == "not_play":
                session.confirm_detection_false_positive([target.id])
            else:
                session.withdraw_missed_detection([target.id])

        restored = session.get_clip(target.id)
        assert restored is not None
        assert restored.detection_lineage == before_lineage
        assert restored.enabled is before_enabled
        assert len(session._undo_stack) == before_undo_count
        assert session._capture_description == before_description
        monkeypatch.setattr(
            session.clip_repo, "save_many", original_save_many)
        session.conn.close()


def test_corrupted_or_incomplete_frozen_snapshot_is_never_eligible(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Frozen guard", tmp_path, tmp_path / "out")
    _, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 12_000)
    session.mark_detection_reviewed([clips[0].id])
    session.complete_autodetect_review_batch(batch_id=batch["id"])
    corrupt = {
        "complete_source_range_reviewed": True,
        "all_missed_plays_added": True,
        "scope": "contiguous_source_range",
        "frozen_candidates": [],
        "frozen_manual_recoveries": [],
        "frozen_intruding_candidate_truth_ranges": [],
    }
    session.conn.execute(
        """UPDATE autodetect_review_batches
           SET confirmation_json=? WHERE id=?""",
        (json.dumps(corrupt), batch["id"]),
    )
    session.conn.commit()

    exported = build_correction_bundle(session.conn)["review_batches"][0]
    assert exported["frozen_completion_snapshot_valid"] is False
    assert exported["frozen_completion_snapshot_errors"]
    assert exported["uses_frozen_completion_snapshot"] is False
    assert exported["eligible_for_scoped_development_metrics"] is False
    assert exported["development_score"]["sample_complete"] is False
    session.conn.close()


def test_preexisting_review_must_be_reattributed_inside_new_batch(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Fresh review", tmp_path, tmp_path / "out")
    _, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    assert session.mark_detection_reviewed([clips[0].id]) == 1
    assert clips[0].detection_lineage.get("review_batch_id", "") == ""

    batch = session.start_autodetect_review_batch(0, 12_000)
    active = build_correction_bundle(session.conn)["review_batches"][0]
    assert active["pending_candidate_count"] == 1
    with pytest.raises(DatabaseError, match="unconfirmed corrections"):
        session.complete_autodetect_review_batch(batch_id=batch["id"])

    assert session.mark_detection_reviewed([clips[0].id]) == 1
    assert clips[0].detection_lineage["review_batch_id"] == batch["id"]
    session.complete_autodetect_review_batch(batch_id=batch["id"])
    session.conn.close()


def test_dirty_session_cannot_freeze_stale_clip_rows(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Dirty guard", tmp_path, tmp_path / "out")
    _, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 12_000)
    session.mark_detection_reviewed([clips[0].id])
    clips[0].include_in_reel = False
    session.dirty = True

    with pytest.raises(DatabaseError, match="unsaved edits"):
        session.complete_autodetect_review_batch(batch_id=batch["id"])
    assert session.active_autodetect_review_batch()["id"] == batch["id"]

    session.save()
    session.complete_autodetect_review_batch(batch_id=batch["id"])
    session.conn.close()


def test_frozen_value_tampering_is_ineligible_even_with_same_root_ids(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Frozen values", tmp_path, tmp_path / "out")
    session_id, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 12_000)
    session.mark_detection_reviewed([clips[0].id])
    session.complete_autodetect_review_batch(batch_id=batch["id"])
    stored = session.autodetect_repo.get_review_batch(batch["id"])
    confirmation = stored["confirmation"]
    confirmation["frozen_candidates"][0]["final_ranges"] = [
        {"start_ms": 2_000, "end_ms": 9_000},
    ]
    session.conn.execute(
        """UPDATE autodetect_review_batches
           SET confirmation_json=? WHERE id=?""",
        (json.dumps(confirmation), batch["id"]),
    )
    session.conn.commit()

    exported = build_correction_bundle(
        session.conn, session_selector=session_id)["review_batches"][0]
    assert exported["frozen_completion_snapshot_valid"] is False
    assert any(
        "digest does not match" in error
        for error in exported["frozen_completion_snapshot_errors"]
    )
    assert exported["eligible_for_scoped_development_metrics"] is False
    session.conn.close()


def test_frozen_created_bounds_must_match_immutable_candidate_root(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Frozen roots", tmp_path, tmp_path / "out")
    session_id, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 12_000)
    session.mark_detection_reviewed([clips[0].id])
    session.complete_autodetect_review_batch(batch_id=batch["id"])
    stored = session.autodetect_repo.get_review_batch(batch["id"])
    confirmation = stored["confirmation"]
    confirmation["frozen_candidates"][0]["created_start_ms"] = 2_000
    confirmation["frozen_snapshot_sha256"] = completion_snapshot_sha256(
        stored,
        immutable_candidates=session.autodetect_repo.list_candidates(
            session_id),
        immutable_recoveries=[],
        frozen_candidates=confirmation["frozen_candidates"],
        frozen_recoveries=confirmation["frozen_manual_recoveries"],
        frozen_intruding_truth=confirmation[
            "frozen_intruding_candidate_truth_ranges"],
    )
    session.conn.execute(
        """UPDATE autodetect_review_batches
           SET confirmation_json=? WHERE id=?""",
        (json.dumps(confirmation), batch["id"]),
    )
    session.conn.commit()

    exported = build_correction_bundle(
        session.conn, session_selector=session_id)["review_batches"][0]
    assert exported["frozen_completion_snapshot_valid"] is False
    assert any(
        "immutable candidate root" in error
        for error in exported["frozen_completion_snapshot_errors"]
    )
    session.conn.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created_start_ms", "not-a-number"),
        ("final_start_ms", {"malformed": True}),
    ],
)
def test_malformed_frozen_candidate_numbers_fail_closed(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    session = ProjectSession.create(
        f"Malformed candidate {field}", tmp_path, tmp_path / "out")
    session_id, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 12_000)
    session.mark_detection_reviewed([clips[0].id])
    session.complete_autodetect_review_batch(batch_id=batch["id"])
    stored = session.autodetect_repo.get_review_batch(batch["id"])
    confirmation = stored["confirmation"]
    frozen = confirmation["frozen_candidates"][0]
    if field == "created_start_ms":
        frozen["created_start_ms"] = value
    else:
        frozen["final_ranges"][0]["start_ms"] = value
    session.conn.execute(
        """UPDATE autodetect_review_batches
           SET confirmation_json=? WHERE id=?""",
        (json.dumps(confirmation), batch["id"]),
    )
    session.conn.commit()

    exported = build_correction_bundle(
        session.conn, session_selector=session_id)["review_batches"][0]
    assert exported["frozen_completion_snapshot_valid"] is False
    assert exported["frozen_completion_snapshot_errors"]
    assert exported["eligible_for_scoped_development_metrics"] is False
    session.conn.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created_end_ms", ["not", "a", "number"]),
        ("final_end_ms", "not-a-number"),
    ],
)
def test_malformed_frozen_recovery_numbers_fail_closed(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    session = ProjectSession.create(
        f"Malformed recovery {field}", tmp_path, tmp_path / "out")
    session_id, clips = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])
    batch = session.start_autodetect_review_batch(0, 25_000)
    session.mark_detection_reviewed([clips[0].id])
    manual = session.add_clip(Clip(start_ms=12_000, end_ms=18_000))
    session.mark_missed_detection([manual.id])
    session.complete_autodetect_review_batch(batch_id=batch["id"])
    stored = session.autodetect_repo.get_review_batch(batch["id"])
    confirmation = stored["confirmation"]
    frozen = confirmation["frozen_manual_recoveries"][0]
    if field == "created_end_ms":
        frozen["created_end_ms"] = value
    else:
        frozen["final_ranges"][0]["end_ms"] = value
    session.conn.execute(
        """UPDATE autodetect_review_batches
           SET confirmation_json=? WHERE id=?""",
        (json.dumps(confirmation), batch["id"]),
    )
    session.conn.commit()

    exported = build_correction_bundle(
        session.conn, session_selector=session_id)["review_batches"][0]
    assert exported["frozen_completion_snapshot_valid"] is False
    assert exported["frozen_completion_snapshot_errors"]
    assert exported["eligible_for_scoped_development_metrics"] is False
    session.conn.close()


def test_older_session_truth_cannot_be_omitted_from_new_iteration(
    tmp_path: Path,
) -> None:
    session = ProjectSession.create("Old truth", tmp_path, tmp_path / "out")
    _, old_clips = _capture(session, [
        (12_000, 18_000, 12_000, 18_000, "play"),
    ])
    session.mark_detection_reviewed([old_clips[0].id])
    new_session_id, _ = _capture(session, [
        (1_000, 9_000, 1_000, 9_000, "play"),
    ])

    with pytest.raises(DatabaseError, match="older autodetect run"):
        session.start_autodetect_review_batch(
            0, 20_000, session_id=new_session_id)
    session.conn.close()

    missed_only = {
        "recovery_id": "recovery-fn",
        "review_status": "reviewed",
        "final_ranges": [{"start_ms": 10_000, "end_ms": 18_000}],
    }
    score = build_development_batch_score(
        [], [missed_only], sample_complete=True)
    metrics = score["primary_metrics"]
    assert metrics["precision"] is None
    assert metrics["recall"] == 0.0
    assert metrics["f1"] is None
