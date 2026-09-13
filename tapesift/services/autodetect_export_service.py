"""Deterministic, privacy-safe exports of captured autodetect corrections."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from tapesift.database.autodetect_repository import AutodetectRepository
from tapesift.database.repositories import ClipRepository, ProjectRepository
from tapesift.models.clip import Clip
from tapesift.services.autodetect_capture_service import selected_candidate_keys
from tapesift.services.autodetect_score_service import (
    build_development_batch_score,
)

EXPORT_SCHEMA_VERSION = "1.1"


class AutodetectExportError(ValueError):
    pass


def open_project_readonly(project_path: Path) -> sqlite3.Connection:
    if not project_path.is_file():
        raise AutodetectExportError(f"Project not found: {project_path}")
    conn = sqlite3.connect(f"{project_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def list_captured_sessions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    project = ProjectRepository(conn).load()
    if project is None:
        raise AutodetectExportError("The project contains no project record.")
    try:
        return AutodetectRepository(conn).list_sessions(project.id)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return []
        raise


def build_correction_bundle(
    conn: sqlite3.Connection,
    *,
    session_selector: str = "latest",
    include_local_paths: bool = False,
) -> dict[str, Any]:
    """Build from one stable SQLite snapshot without modifying the project."""
    owns_read_transaction = not conn.in_transaction
    if owns_read_transaction:
        conn.execute("BEGIN")
    try:
        return _build_correction_bundle(
            conn,
            session_selector=session_selector,
            include_local_paths=include_local_paths,
        )
    finally:
        if owns_read_transaction:
            conn.rollback()


def _build_correction_bundle(
    conn: sqlite3.Connection,
    *,
    session_selector: str,
    include_local_paths: bool,
) -> dict[str, Any]:
    project = ProjectRepository(conn).load()
    if project is None:
        raise AutodetectExportError("The project contains no project record.")
    repo = AutodetectRepository(conn)
    try:
        sessions = repo.list_sessions(project.id)
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            raise AutodetectExportError(
                "This project has no captured autodetect sessions.") from exc
        raise
    if not sessions:
        raise AutodetectExportError(
            "This project has no captured autodetect sessions.")
    if session_selector == "latest":
        session = sessions[-1]
    else:
        session = next(
            (item for item in sessions if item["id"] == session_selector),
            None,
        )
        if session is None:
            raise AutodetectExportError(
                f"Autodetect session not found: {session_selector}")

    candidates = repo.list_candidates(session["id"])
    capture_validation = _validate_candidate_capture(session, candidates)
    events = repo.list_events(session["id"])
    try:
        review_batches = repo.list_review_batches(session["id"])
        recoveries = repo.list_recoveries(session["id"])
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        # Read-only CLI export must remain compatible with a v7 project that
        # has not yet been opened by the v8 GUI migration.
        review_batches = []
        recoveries = []
    clips = ClipRepository(conn).list_for_project(project.id)
    film_id = _film_id(session)
    candidate_results = [
        _candidate_result(candidate, clips, events)
        for candidate in candidates
    ]
    manual_recovery_results = [
        _manual_recovery_result(recovery, clips, events)
        for recovery in recoveries
    ]
    batch_results = [
        _review_batch_result(
            batch,
            candidates=candidates,
            candidate_results=candidate_results,
            manual_recoveries=manual_recovery_results,
            capture_validation=capture_validation,
        )
        for batch in review_batches
    ]
    review_complete = (capture_validation["valid"]
                       and not capture_validation["omitted_detector_play_ranges"]
                       and bool(candidate_results)) and all(
        item["review_status"] == "reviewed"
        for item in candidate_results
    )

    safe_session = copy.deepcopy(session)
    safe_session.pop("project_id", None)
    if not include_local_paths:
        safe_session["source"] = _redacted_source(
            safe_session["source"], film_id)

    outcome_counts = Counter(
        item["geometry_outcome"] for item in candidate_results)
    review_counts = Counter(
        item["review_status"] for item in candidate_results)
    report = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "dataset_role": "development",
        "review_blinding": "none",
        "review_complete": review_complete,
        "eligible_for_accuracy_metrics": False,
        "eligible_for_generalization_claim": False,
        "accuracy_metrics": None,
        "accuracy_note": (
            "This custom capture bundle is supervised development data, not "
            "an untouched holdout or benchmark-ready truth adapter. No "
            "film-wide, holdout, generalization, or public accuracy claim is "
            "computed by this export."
        ),
        "capture_limitations": [
            "Only explicitly marked manual misses inside a review batch count.",
            "Source identity is advisory unless content hashes are present.",
            "Undo and redo are editor state snapshots, not independent human "
            "reviews.",
            "Batch metrics are development-only and describe only their "
            "confirmed contiguous source range.",
        ],
        "omitted_detector_play_ranges": capture_validation["omitted_detector_play_ranges"],
        "raw_detector_play_count": capture_validation.get("raw_detector_play_count", 0),
        "original_candidates": len(candidate_results),
        "original_plays": sum(
            item["candidate_kind"] == "play"
            for item in candidate_results),
        "original_unclassified": sum(
            item["candidate_kind"] == "unclassified"
            for item in candidate_results),
        "final_active_ranges": sum(
            len(item["final_ranges"]) for item in candidate_results),
        "geometry_outcomes": dict(sorted(outcome_counts.items())),
        "review_statuses": dict(sorted(review_counts.items())),
        "manual_recoveries": len(manual_recovery_results),
        "review_batches": len(batch_results),
        "completed_review_batches": sum(
            item["status"] == "completed" for item in batch_results),
        "active_review_batches": sum(
            item["status"] == "active" for item in batch_results),
        "eligible_for_scoped_development_metrics": any(
            item["eligible_for_scoped_development_metrics"]
            for item in batch_results
        ),
        "candidate_capture_valid": capture_validation["valid"],
        "candidate_capture_validation_errors": capture_validation["errors"],
    }
    core = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "bundle_kind": "autodetect_correction_capture",
        "benchmark_ready": False,
        "film_id": film_id,
        "privacy_mode": (
            "local_paths_included" if include_local_paths
            else "source_identity_redacted"
        ),
        "session": safe_session,
        "candidate_capture_validation": capture_validation,
        "candidates": candidate_results,
        "manual_recoveries": manual_recovery_results,
        "review_batches": batch_results,
        "events": events,
        "report": report,
    }
    core["capture_sha256"] = hashlib.sha256(
        canonical_json(core).encode("utf-8")
    ).hexdigest()
    return core


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def completion_snapshot_sha256(
    batch: dict[str, Any],
    *,
    immutable_candidates: list[dict[str, Any]],
    immutable_recoveries: list[dict[str, Any]],
    frozen_candidates: list[dict[str, Any]],
    frozen_recoveries: list[dict[str, Any]],
    frozen_intruding_truth: list[dict[str, Any]],
) -> str:
    """Bind a completion snapshot to its immutable roots.

    This is tamper-evident protection for accidental or casual edits to the
    project database. It is not a signature: an attacker who can rewrite the
    SQLite database and recompute this digest can still forge a snapshot.
    """
    candidate_roots = [
        {
            "id": str(item["id"]),
            "candidate_kind": str(item["candidate_kind"]),
            "candidate_index": int(item["candidate_index"]),
            "detector_start_ms": int(item["detector_start_ms"]),
            "detector_end_ms": int(item["detector_end_ms"]),
            "created_start_ms": int(item["created_start_ms"]),
            "created_end_ms": int(item["created_end_ms"]),
            "initial_clip_id": str(item["initial_clip_id"]),
        }
        for item in immutable_candidates
    ]
    recovery_roots = [
        {
            "id": str(item.get("recovery_id", item.get("id", ""))),
            "session_id": str(item["session_id"]),
            "batch_id": str(item["batch_id"]),
            "initial_clip_id": str(item["initial_clip_id"]),
            "created_start_ms": int(item["created_start_ms"]),
            "created_end_ms": int(item["created_end_ms"]),
            "creation_method": str(item["creation_method"]),
        }
        for item in immutable_recoveries
    ]
    payload = {
        "batch": {
            "id": str(batch["id"]),
            "session_id": str(batch["session_id"]),
            "start_ms": int(batch["start_ms"]),
            "end_ms": int(batch["end_ms"]),
        },
        "immutable_candidates": sorted(
            candidate_roots, key=lambda item: item["id"]),
        "immutable_recoveries": sorted(
            recovery_roots, key=lambda item: item["id"]),
        "frozen_candidates": frozen_candidates,
        "frozen_recoveries": frozen_recoveries,
        "frozen_intruding_truth": frozen_intruding_truth,
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_correction_bundle(
    output_path: Path,
    bundle: dict[str, Any],
) -> bool:
    """Write atomically; return False when identical output already exists."""
    data = canonical_json(bundle)
    if output_path.exists():
        if output_path.read_text(encoding="utf-8") == data:
            return False
        raise AutodetectExportError(
            f"Refusing to overwrite a different export: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f"{output_path.name}.tmp")
    temporary.write_text(data, encoding="utf-8")
    temporary.replace(output_path)
    return True


def _film_id(session: dict[str, Any]) -> str:
    persisted = session.get("source", {}).get("film_id")
    if persisted:
        return str(persisted)
    value = (
        f"{session['id']}|"
        f"{session.get('source', {}).get('duration_ms', '')}"
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _redacted_source(source: dict[str, Any], film_id: str) -> dict[str, Any]:
    # Allowlist only values needed by the correction adapter. This fails
    # closed if future session records add filenames, paths, or local labels.
    return {
        "film_id": film_id,
        "duration_ms": source.get("duration_ms"),
        "source": {"path": f"redacted://source/{film_id}"},
        "analysis_source": {
            "path": f"redacted://analysis/{film_id}"},
    }


def _validate_candidate_capture(
    session: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    result = session.get("result", {})
    ui_options = session.get("ui_options", {})
    if not isinstance(result, dict) or not isinstance(ui_options, dict):
        return {"valid": False, "errors": ["Detector result and UI options must be objects."],
                "candidate_count": len(candidates), "immutable_result_root_count": 0,
                "omitted_detector_play_ranges": []}
    captured_kinds = {
        str(candidate.get("candidate_kind", ""))
        for candidate in candidates
    }
    unclassified_materialized = ui_options.get(
        "unclassified_materialized")
    # Unclassified detector coverage used to become ordinary clip rows.
    # It now remains only in the immutable coverage ledger so hundreds of
    # one-second fragments cannot flood the Clips list.  Play candidates are
    # always required; unclassified roots are required only for an explicit
    # legacy/materialized capture (or when such candidates are actually
    # present).  Sessions written during the transition did not yet carry the
    # UI flag, so absence plus no unclassified candidates means ledger-only.
    require_unclassified_candidates = (
        unclassified_materialized is True
        or "unclassified" in captured_kinds
    )
    errors: list[str] = []
    expected_keys: set[tuple[str, int]] = set()
    for kind, collection_name in (
        ("play", "plays"),
        ("unclassified", "unclassified"),
    ):
        collection = result.get(collection_name, [])
        if not isinstance(collection, list):
            errors.append(f"session.result.{collection_name} is not a list")
            continue
        if kind == "unclassified" and not require_unclassified_candidates:
            continue
        for index in range(len(collection)):
            expected_keys.add((kind, index))

    try:
        kept = selected_candidate_keys(result, ui_options)
        if kept is not None:
            expected_keys = kept
    except ValueError as exc:
        errors.append(str(exc))

    actual_keys: set[tuple[str, int]] = set()
    for candidate in candidates:
        kind = str(candidate["candidate_kind"])
        index = int(candidate["candidate_index"])
        key = (kind, index)
        if key in actual_keys:
            errors.append(f"duplicate captured {kind} candidate index {index}")
        actual_keys.add(key)
        collection_name = (
            "plays" if kind == "play"
            else "unclassified" if kind == "unclassified"
            else ""
        )
        if not collection_name:
            errors.append(f"candidate {candidate['id']} has unknown kind {kind}")
            continue
        collection = result.get(collection_name, [])
        if not isinstance(collection, list) or not 0 <= index < len(collection):
            errors.append(
                f"candidate {candidate['id']} has no immutable result root")
            continue
        root = collection[index]
        if not isinstance(root, dict):
            errors.append(
                f"candidate {candidate['id']} result root is not an object")
            continue
        if (
            int(root.get("start_ms", -1))
            != int(candidate["detector_start_ms"])
            or int(root.get("end_ms", -1))
            != int(candidate["detector_end_ms"])
        ):
            errors.append(
                f"candidate {candidate['id']} detector bounds do not match "
                "the immutable session result")
    for kind, index in sorted(expected_keys - actual_keys):
        errors.append(f"missing captured {kind} candidate index {index}")
    for kind, index in sorted(actual_keys - expected_keys):
        errors.append(f"unexpected captured {kind} candidate index {index}")
    omitted = []
    for index, root in enumerate(result.get("plays", []) if isinstance(result.get("plays", []), list) else []):
        if ("play", index) in actual_keys:
            continue
        if (not isinstance(root, dict) or type(root.get("start_ms")) is not int
                or type(root.get("end_ms")) is not int
                or not 0 <= root["start_ms"] < root["end_ms"]):
            errors.append(f"omitted play {index} has invalid detector bounds")
            continue
        omitted.append({"candidate_index": index, "start_ms": root["start_ms"], "end_ms": root["end_ms"]})
    return {
        "valid": not errors,
        "errors": errors,
        "candidate_count": len(candidates),
        "immutable_result_root_count": len(expected_keys),
        "omitted_detector_play_ranges": omitted,
        "raw_detector_play_count": len(result.get("plays", [])) if isinstance(result.get("plays", []), list) else 0,
    }


def _candidate_result(
    candidate: dict[str, Any],
    clips: list[Clip],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    linked = [
        clip for clip in clips
        if candidate["id"] in _candidate_ids(clip)
    ]
    truth_clips = [
        clip for clip in linked
        if not str(
            clip.detection_lineage.get("derivation", "")
        ).startswith("duplicate")
    ]
    active = [clip for clip in truth_clips if clip.enabled]
    ranges = sorted({
        (int(clip.start_ms), int(clip.end_ms))
        for clip in active
    })
    original_range = (
        candidate["created_start_ms"], candidate["created_end_ms"])
    if not truth_clips:
        geometry_outcome = "excluded_deleted"
    elif not active:
        geometry_outcome = "excluded"
    elif len(ranges) > 1:
        geometry_outcome = "split"
    elif ranges[0] == original_range:
        geometry_outcome = "unchanged"
    else:
        geometry_outcome = "revised"

    historical_truth_ids: set[str] = set()
    retracted_truth_ids: set[str] = set()
    effective_review: dict[str, bool] = {}
    effective_false_positive: dict[str, bool] = {}
    effective_review_batch: dict[str, str] = {}
    for event in events:
        before_states = {
            str(state["clip_id"]): state
            for state in event["before"]
            if candidate["id"] in state.get("candidate_ids", [])
            and not str(
                state.get("derivation", "")).startswith("duplicate")
        }
        after_states = {
            str(state["clip_id"]): state
            for state in event["after"]
            if candidate["id"] in state.get("candidate_ids", [])
            and not str(
                state.get("derivation", "")).startswith("duplicate")
        }
        historical_truth_ids.update(before_states)
        historical_truth_ids.update(after_states)
        for clip_id in before_states.keys() - after_states.keys():
            if (
                event.get("action") == "undo"
                and "split" in str(
                    event.get("description", "")).lower()
            ):
                retracted_truth_ids.add(clip_id)
            else:
                retracted_truth_ids.discard(clip_id)
            # Preserve the review state at an explicit deletion. Undo/redo
            # can later replace it through an after-state.
            effective_review[clip_id] = bool(
                before_states[clip_id].get("reviewed_at"))
            effective_false_positive[clip_id] = bool(
                before_states[clip_id].get(
                    "false_positive_confirmed_at"))
            effective_review_batch[clip_id] = str(
                before_states[clip_id].get("review_batch_id", ""))
        for clip_id, state in after_states.items():
            retracted_truth_ids.discard(clip_id)
            effective_review[clip_id] = bool(state.get("reviewed_at"))
            effective_false_positive[clip_id] = bool(
                state.get("false_positive_confirmed_at"))
            effective_review_batch[clip_id] = str(
                state.get("review_batch_id", ""))
    for clip in truth_clips:
        historical_truth_ids.add(clip.id)
        effective_review[clip.id] = bool(
            clip.detection_lineage.get("reviewed_at"))
        effective_false_positive[clip.id] = bool(
            clip.detection_lineage.get("false_positive_confirmed_at"))
        effective_review_batch[clip.id] = str(
            clip.detection_lineage.get("review_batch_id", ""))
    active_truth_ids = {clip.id for clip in active}
    decision_truth_ids = historical_truth_ids - retracted_truth_ids
    if geometry_outcome.startswith("excluded"):
        explicitly_reviewed = (
            bool(decision_truth_ids)
            and all(effective_false_positive.get(clip_id, False)
                    for clip_id in decision_truth_ids)
        )
    else:
        explicitly_reviewed = (
            bool(decision_truth_ids)
            and all(
                effective_review.get(clip_id, False)
                if clip_id in active_truth_ids
                else effective_false_positive.get(clip_id, False)
                for clip_id in decision_truth_ids
            )
        )
    decision_batch_ids = {
        effective_review_batch.get(clip_id, "")
        for clip_id in decision_truth_ids
    }
    review_batch_id = (
        next(iter(decision_batch_ids))
        if len(decision_batch_ids) == 1 and "" not in decision_batch_ids
        else ""
    )
    if explicitly_reviewed:
        review_status = "reviewed"
    elif geometry_outcome.startswith("excluded"):
        review_status = "excluded_unconfirmed"
    elif geometry_outcome in {"revised", "split"}:
        review_status = "corrected_unconfirmed"
    else:
        review_status = "pending"

    return {
        "candidate_id": candidate["id"],
        "candidate_kind": candidate["candidate_kind"],
        "candidate_index": candidate["candidate_index"],
        "detector_start_ms": candidate["detector_start_ms"],
        "detector_end_ms": candidate["detector_end_ms"],
        "created_start_ms": candidate["created_start_ms"],
        "created_end_ms": candidate["created_end_ms"],
        "angle_starts_ms": candidate["angle_starts_ms"],
        "angle_count": candidate["angle_count"],
        "detector_needs_review": candidate["needs_review"],
        "detector_reason": candidate["review_reason"],
        "initial_clip_id": candidate["initial_clip_id"],
        "geometry_outcome": geometry_outcome,
        "review_status": review_status,
        "review_batch_id": review_batch_id,
        "verification_status": (
            "verified" if review_status == "reviewed"
            else "seed_unverified"
        ),
        "final_ranges": [
            {"start_ms": start, "end_ms": end}
            for start, end in ranges
        ],
        "linked_clip_ids": sorted(clip.id for clip in truth_clips),
        "alternate_export_clip_ids": sorted(
            clip.id for clip in linked
            if str(
                clip.detection_lineage.get("derivation", "")
            ).startswith("duplicate")
        ),
    }


def _manual_recovery_result(
    recovery: dict[str, Any],
    clips: list[Clip],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    linked = [
        clip for clip in clips
        if str(clip.detection_lineage.get("recovery_id", ""))
        == recovery["id"]
    ]
    truth_clips = [
        clip for clip in linked
        if not str(
            clip.detection_lineage.get("derivation", "")
        ).startswith("duplicate")
    ]
    active = [clip for clip in truth_clips if clip.enabled]
    ranges = sorted({
        (int(clip.start_ms), int(clip.end_ms))
        for clip in active
    })
    original = (
        int(recovery["created_start_ms"]),
        int(recovery["created_end_ms"]),
    )

    historical_truth_ids: set[str] = set()
    retracted_truth_ids: set[str] = set()
    effective_states: dict[str, dict[str, Any]] = {}
    for event in events:
        before_states = {
            str(state["clip_id"]): state
            for state in event["before"]
            if str(state.get("recovery_id", "")) == recovery["id"]
            and not str(
                state.get("derivation", "")).startswith("duplicate")
        }
        after_states = {
            str(state["clip_id"]): state
            for state in event["after"]
            if str(state.get("recovery_id", "")) == recovery["id"]
            and not str(
                state.get("derivation", "")).startswith("duplicate")
        }
        historical_truth_ids.update(before_states)
        historical_truth_ids.update(after_states)
        for clip_id in before_states.keys() - after_states.keys():
            removed_state = copy.deepcopy(before_states[clip_id])
            if (
                event.get("action") == "undo"
                and "missed autodetect play" in str(
                    event.get("description", "")).lower()
            ):
                # Undoing the explicit mark-missed action is itself an
                # explicit withdrawal. A generic delete/disable is not.
                removed_state["recovery_withdrawn_at"] = (
                    event.get("created_at") or "undo")
            elif (
                event.get("action") == "undo"
                and "split" in str(
                    event.get("description", "")).lower()
            ):
                retracted_truth_ids.add(clip_id)
            else:
                retracted_truth_ids.discard(clip_id)
            effective_states[clip_id] = removed_state
        for clip_id in after_states:
            retracted_truth_ids.discard(clip_id)
        effective_states.update(after_states)
    for clip in truth_clips:
        historical_truth_ids.add(clip.id)
        effective_states[clip.id] = {
            "enabled": bool(clip.enabled),
            "reviewed_at": str(
                clip.detection_lineage.get("reviewed_at", "")),
            "recovery_withdrawn_at": str(
                clip.detection_lineage.get(
                    "recovery_withdrawn_at", "")),
            "review_batch_id": str(
                clip.detection_lineage.get("review_batch_id", "")),
        }

    if not truth_clips or not active:
        geometry_outcome = "withdrawn"
    elif len(ranges) > 1:
        geometry_outcome = "split"
    elif ranges[0] == original:
        geometry_outcome = "unchanged"
    else:
        geometry_outcome = "revised"

    decision_truth_ids = historical_truth_ids - retracted_truth_ids
    explicitly_withdrawn = (
        bool(decision_truth_ids)
        and all(
            effective_states.get(clip_id, {}).get(
                "recovery_withdrawn_at")
            for clip_id in decision_truth_ids
        )
    )
    current_truth_ids = {clip.id for clip in truth_clips}
    all_truth_present = decision_truth_ids <= current_truth_ids
    explicitly_reviewed = (
        bool(truth_clips)
        and all_truth_present
        and all(
            clip.enabled
            and clip.detection_lineage.get("reviewed_at")
            for clip in truth_clips
        )
    )
    if geometry_outcome == "withdrawn":
        review_status = (
            "withdrawn" if explicitly_withdrawn
            else "withdrawn_unconfirmed"
        )
    elif explicitly_reviewed:
        review_status = "reviewed"
    elif (
        geometry_outcome in {"revised", "split"}
        or not all_truth_present
        or any(not clip.enabled for clip in truth_clips)
    ):
        review_status = "corrected_unconfirmed"
    else:
        review_status = "pending"

    return {
        "recovery_id": recovery["id"],
        "session_id": recovery["session_id"],
        "batch_id": recovery["batch_id"],
        "initial_clip_id": recovery["initial_clip_id"],
        "created_start_ms": recovery["created_start_ms"],
        "created_end_ms": recovery["created_end_ms"],
        "creation_method": recovery["creation_method"],
        "created_at": recovery["created_at"],
        "geometry_outcome": geometry_outcome,
        "review_status": review_status,
        "review_batch_id": (
            str(recovery["batch_id"])
            if explicitly_reviewed or explicitly_withdrawn else ""
        ),
        "verification_status": (
            "verified" if review_status == "reviewed"
            else "excluded" if review_status == "withdrawn"
            else "seed_unverified"
        ),
        "final_ranges": [
            {"start_ms": start, "end_ms": end}
            for start, end in ranges
        ],
        "linked_clip_ids": sorted(clip.id for clip in truth_clips),
        "alternate_export_clip_ids": sorted(
            clip.id for clip in linked
            if str(
                clip.detection_lineage.get("derivation", "")
            ).startswith("duplicate")
        ),
    }


def _frozen_completion_snapshot_errors(
    frozen_candidates: Any,
    frozen_recoveries: Any,
    frozen_intruding_truth: Any,
    *,
    batch: dict[str, Any],
    owned_candidates: list[dict[str, Any]],
    expected_recoveries: list[dict[str, Any]],
    stored_snapshot_sha256: Any,
) -> list[str]:
    def valid_int(value: Any) -> bool:
        return isinstance(value, int) and not isinstance(value, bool)

    def valid_range(value: Any) -> bool:
        return (
            isinstance(value, dict)
            and {"start_ms", "end_ms"} <= value.keys()
            and valid_int(value["start_ms"])
            and valid_int(value["end_ms"])
            and value["end_ms"] > value["start_ms"]
        )

    errors: list[str] = []
    if not isinstance(frozen_candidates, list):
        errors.append("frozen_candidates is missing or not a list")
    if not isinstance(frozen_recoveries, list):
        errors.append("frozen_manual_recoveries is missing or not a list")
    if not isinstance(frozen_intruding_truth, list):
        errors.append(
            "frozen_intruding_candidate_truth_ranges is missing or not a "
            "list")
    if errors:
        return errors

    owned_by_id = {
        str(candidate["id"]): candidate for candidate in owned_candidates
    }
    expected_recoveries_by_id = {
        str(recovery["recovery_id"]): recovery
        for recovery in expected_recoveries
    }
    candidate_ids: list[str] = []
    for index, item in enumerate(frozen_candidates):
        if not isinstance(item, dict):
            errors.append(f"frozen candidate {index} is not an object")
            continue
        required = {
            "candidate_id", "candidate_kind", "created_start_ms",
            "created_end_ms", "review_status", "review_batch_id",
            "final_ranges",
        }
        if not required <= item.keys() or not isinstance(
                item.get("final_ranges"), list):
            errors.append(
                f"frozen candidate {index} is missing score fields")
            continue
        if (
            not valid_int(item["created_start_ms"])
            or not valid_int(item["created_end_ms"])
            or item["created_end_ms"] <= item["created_start_ms"]
        ):
            errors.append(
                f"frozen candidate {index} has invalid created bounds")
            continue
        if any(
            not valid_range(final_range)
            for final_range in item["final_ranges"]
        ):
            errors.append(
                f"frozen candidate {index} has invalid truth ranges")
            continue
        candidate_ids.append(str(item["candidate_id"]))
        immutable = owned_by_id.get(str(item["candidate_id"]))
        if immutable is not None and (
            str(item["candidate_kind"])
            != str(immutable["candidate_kind"])
            or int(item["created_start_ms"])
            != int(immutable["created_start_ms"])
            or int(item["created_end_ms"])
            != int(immutable["created_end_ms"])
        ):
            errors.append(
                f"frozen candidate {item['candidate_id']} does not match its "
                "immutable candidate root")
        if str(item.get("review_batch_id", "")) != str(batch["id"]):
            errors.append(
                f"frozen candidate {item['candidate_id']} was not reviewed "
                "in this batch")

    recovery_ids: list[str] = []
    for index, item in enumerate(frozen_recoveries):
        if not isinstance(item, dict):
            errors.append(f"frozen recovery {index} is not an object")
            continue
        required = {
            "recovery_id", "created_start_ms", "created_end_ms",
            "review_status", "final_ranges",
        }
        if not required <= item.keys() or not isinstance(
                item.get("final_ranges"), list):
            errors.append(
                f"frozen recovery {index} is missing score fields")
            continue
        if (
            not valid_int(item["created_start_ms"])
            or not valid_int(item["created_end_ms"])
            or item["created_end_ms"] <= item["created_start_ms"]
        ):
            errors.append(
                f"frozen recovery {index} has invalid created bounds")
            continue
        if any(
            not valid_range(final_range)
            for final_range in item["final_ranges"]
        ):
            errors.append(
                f"frozen recovery {index} has invalid truth ranges")
            continue
        recovery_ids.append(str(item["recovery_id"]))
        immutable = expected_recoveries_by_id.get(str(item["recovery_id"]))
        if immutable is not None and (
            int(item["created_start_ms"])
            != int(immutable["created_start_ms"])
            or int(item["created_end_ms"])
            != int(immutable["created_end_ms"])
        ):
            errors.append(
                f"frozen recovery {item['recovery_id']} does not match its "
                "immutable recovery root")

    if (
        len(candidate_ids) != len(owned_by_id)
        or set(candidate_ids) != set(owned_by_id)
    ):
        errors.append(
            "frozen candidate roots do not match the batch candidate roots")
    if (
        len(recovery_ids) != len(expected_recoveries_by_id)
        or set(recovery_ids) != set(expected_recoveries_by_id)
    ):
        errors.append(
            "frozen recovery roots do not match the persisted batch "
            "recoveries")
    if any(
        not isinstance(item, dict)
        or not {"candidate_id", "start_ms", "end_ms"} <= item.keys()
        or not valid_int(item["start_ms"])
        or not valid_int(item["end_ms"])
        or item["end_ms"] <= item["start_ms"]
        for item in frozen_intruding_truth
    ):
        errors.append(
            "frozen intruding candidate truth contains a non-object")
    if not isinstance(stored_snapshot_sha256, str) or not \
            stored_snapshot_sha256:
        errors.append("frozen completion snapshot digest is missing")
    elif not errors:
        expected_sha256 = completion_snapshot_sha256(
            batch,
            immutable_candidates=owned_candidates,
            immutable_recoveries=expected_recoveries,
            frozen_candidates=frozen_candidates,
            frozen_recoveries=frozen_recoveries,
            frozen_intruding_truth=frozen_intruding_truth,
        )
        if stored_snapshot_sha256 != expected_sha256:
            errors.append("frozen completion snapshot digest does not match")
    return errors


def _review_batch_result(
    batch: dict[str, Any],
    *,
    candidates: list[dict[str, Any]],
    candidate_results: list[dict[str, Any]],
    manual_recoveries: list[dict[str, Any]],
    capture_validation: dict[str, Any],
) -> dict[str, Any]:
    start = int(batch["start_ms"])
    end = int(batch["end_ms"])
    omitted_predictions = [item for item in capture_validation.get("omitted_detector_play_ranges", [])
                           if item["start_ms"] < end and item["end_ms"] > start]
    owned_candidates = [
        candidate for candidate in candidates
        if int(candidate["created_start_ms"]) >= start
        and int(candidate["created_end_ms"]) <= end
    ]
    crossing = [
        candidate for candidate in candidates
        if int(candidate["created_start_ms"]) < end
        and int(candidate["created_end_ms"]) > start
        and candidate not in owned_candidates
    ]
    owned_ids = {candidate["id"] for candidate in owned_candidates}
    current_scoped_candidates = []
    for result in candidate_results:
        if result["candidate_id"] not in owned_ids:
            continue
        scoped = copy.deepcopy(result)
        if (
            scoped["review_status"] == "reviewed"
            and str(scoped.get("review_batch_id", "")) != str(batch["id"])
        ):
            scoped["review_status"] = "pending"
        current_scoped_candidates.append(scoped)
    current_scoped_recoveries = [
        recovery for recovery in manual_recoveries
        if recovery["batch_id"] == batch["id"]
    ]
    current_intruding_truth = [
        {
            "candidate_id": result["candidate_id"],
            "start_ms": int(final_range["start_ms"]),
            "end_ms": int(final_range["end_ms"]),
        }
        for result in candidate_results
        if result["candidate_id"] not in owned_ids
        for final_range in result["final_ranges"]
        if int(final_range["start_ms"]) < end
        and int(final_range["end_ms"]) > start
    ]
    stored_confirmation = copy.deepcopy(batch.get("confirmation", {}))
    frozen_candidates = stored_confirmation.get("frozen_candidates")
    frozen_recoveries = stored_confirmation.get("frozen_manual_recoveries")
    frozen_intruding_truth = stored_confirmation.get(
        "frozen_intruding_candidate_truth_ranges")
    stored_snapshot_sha256 = stored_confirmation.get(
        "frozen_snapshot_sha256")
    frozen_snapshot_errors = (
        _frozen_completion_snapshot_errors(
            frozen_candidates,
            frozen_recoveries,
            frozen_intruding_truth,
            batch=batch,
            owned_candidates=owned_candidates,
            expected_recoveries=current_scoped_recoveries,
            stored_snapshot_sha256=stored_snapshot_sha256,
        )
        if batch["status"] == "completed"
        else []
    )
    frozen_snapshot_valid = (
        not frozen_snapshot_errors
        if batch["status"] == "completed"
        else None
    )
    uses_frozen_completion = (
        batch["status"] == "completed"
        and frozen_snapshot_valid is True
    )
    scoped_candidates = (
        copy.deepcopy(frozen_candidates)
        if uses_frozen_completion else current_scoped_candidates
    )
    scoped_recoveries = (
        copy.deepcopy(frozen_recoveries)
        if uses_frozen_completion else current_scoped_recoveries
    )
    intruding_truth = (
        copy.deepcopy(frozen_intruding_truth)
        if uses_frozen_completion else current_intruding_truth
    )
    pending_candidate_count = sum(
        result["review_status"] != "reviewed"
        for result in scoped_candidates
    )
    pending_recovery_count = sum(
        recovery["review_status"] not in {"reviewed", "withdrawn"}
        for recovery in scoped_recoveries
    )
    truth_ranges = [
        {
            "root_id": f"candidate:{result['candidate_id']}",
            "start_ms": int(final_range["start_ms"]),
            "end_ms": int(final_range["end_ms"]),
        }
        for result in scoped_candidates
        for final_range in result["final_ranges"]
    ] + [
        {
            "root_id": f"recovery:{recovery['recovery_id']}",
            "start_ms": int(final_range["start_ms"]),
            "end_ms": int(final_range["end_ms"]),
        }
        for recovery in scoped_recoveries
        for final_range in recovery["final_ranges"]
    ]
    out_of_scope_truth = [
        item for item in truth_ranges
        if item["start_ms"] < start or item["end_ms"] > end
    ]
    overlapping_truth = _overlapping_truth_pairs(truth_ranges)
    manual_prediction_overlaps = _manual_prediction_overlaps(
        owned_candidates, scoped_recoveries)
    review_complete = (
        bool(scoped_candidates)
        and capture_validation["valid"]
        and not omitted_predictions
        and (
            batch["status"] != "completed"
            or frozen_snapshot_valid is True
        )
        and not crossing
        and not intruding_truth
        and not out_of_scope_truth
        and not overlapping_truth
        and not manual_prediction_overlaps
        and pending_candidate_count == 0
        and pending_recovery_count == 0
    )
    confirmation = {
        key: value
        for key, value in stored_confirmation.items()
        if key not in {
            "frozen_candidates",
            "frozen_manual_recoveries",
            "frozen_intruding_candidate_truth_ranges",
            "frozen_snapshot_sha256",
        }
    }
    computed_snapshot_sha256 = completion_snapshot_sha256(
        batch,
        immutable_candidates=owned_candidates,
        immutable_recoveries=current_scoped_recoveries,
        frozen_candidates=scoped_candidates,
        frozen_recoveries=scoped_recoveries,
        frozen_intruding_truth=intruding_truth,
    )
    confirmation_complete = (
        confirmation.get("complete_source_range_reviewed") is True
        and confirmation.get("all_missed_plays_added") is True
    )
    scope_confirmed = (
        batch["status"] == "completed"
        and confirmation_complete
        and review_complete
    )
    # Dismiss at import is not a confirmed false-positive review. Scoring
    # only the kept roots would silently lower the prediction denominator.
    score = build_development_batch_score(
        [] if omitted_predictions else scoped_candidates,
        [] if omitted_predictions else scoped_recoveries,
        sample_complete=scope_confirmed,
    )
    if omitted_predictions:
        score["scope"] = "Unavailable: detector play predictions were omitted from this source range before review."
    eligible = (
        scope_confirmed
        and score["available"]
    )
    return {
        "id": batch["id"],
        "session_id": batch["session_id"],
        "start_ms": start,
        "end_ms": end,
        "status": batch["status"],
        "started_at": batch["started_at"],
        "completed_at": batch["completed_at"],
        "confirmation": confirmation,
        "uses_frozen_completion_snapshot": uses_frozen_completion,
        "frozen_completion_snapshot_valid": frozen_snapshot_valid,
        "frozen_completion_snapshot_errors": frozen_snapshot_errors,
        "computed_completion_snapshot_sha256": computed_snapshot_sha256,
        "confirmed_candidates": scoped_candidates,
        "confirmed_manual_recoveries": scoped_recoveries,
        "candidate_ids": sorted(owned_ids),
        "recovery_ids": sorted(
            recovery["recovery_id"] for recovery in scoped_recoveries),
        "omitted_detector_play_ranges": omitted_predictions,
        "crossing_candidate_ids": sorted(
            candidate["id"] for candidate in crossing),
        "intruding_candidate_truth_ranges": intruding_truth,
        "out_of_scope_truth_ranges": out_of_scope_truth,
        "overlapping_truth_pairs": overlapping_truth,
        "manual_prediction_overlaps": manual_prediction_overlaps,
        "pending_candidate_count": pending_candidate_count,
        "pending_recovery_count": pending_recovery_count,
        "review_complete": review_complete,
        "candidate_capture_valid": capture_validation["valid"],
        "candidate_capture_validation_errors": capture_validation["errors"],
        "partial_film_scope": True,
        "eligible_for_scoped_development_metrics": eligible,
        "eligible_for_film_wide_metrics": False,
        "eligible_for_generalization_claim": False,
        "development_score": score,
    }


def _overlapping_truth_pairs(
    truth_ranges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for left_index, left in enumerate(truth_ranges):
        for right in truth_ranges[left_index + 1:]:
            intersection = max(
                0,
                min(left["end_ms"], right["end_ms"])
                - max(left["start_ms"], right["start_ms"]),
            )
            union = (
                left["end_ms"] - left["start_ms"]
                + right["end_ms"] - right["start_ms"]
                - intersection
            )
            iou = intersection / union if union > 0 else 0.0
            if intersection <= 0:
                continue
            pairs.append({
                "left_root_id": left["root_id"],
                "right_root_id": right["root_id"],
                "iou": iou,
            })
    return pairs


def _manual_prediction_overlaps(
    candidates: list[dict[str, Any]],
    manual_recoveries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    overlaps: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate["candidate_kind"] != "play":
            continue
        prediction_start = int(candidate["created_start_ms"])
        prediction_end = int(candidate["created_end_ms"])
        for recovery in manual_recoveries:
            if recovery["review_status"] != "reviewed":
                continue
            for final_range in recovery["final_ranges"]:
                intersection = max(
                    0,
                    min(prediction_end, int(final_range["end_ms"]))
                    - max(prediction_start, int(final_range["start_ms"])),
                )
                if intersection <= 0:
                    continue
                overlaps.append({
                    "candidate_id": candidate["id"],
                    "recovery_id": recovery["recovery_id"],
                    "intersection_ms": intersection,
                })
    return overlaps


def _candidate_ids(clip: Clip) -> list[str]:
    values = clip.detection_lineage.get("candidate_ids", [])
    if isinstance(values, str):
        return [values]
    return [str(value) for value in values]
