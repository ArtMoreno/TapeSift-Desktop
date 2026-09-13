"""Label-blind Temporal onset-review session behavior."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from tapesift.research.run_pass_temporal_review import (
    TemporalReviewSession,
)


def _record(
    clip_number: int,
    *,
    eligible: bool,
    clip_id: str | None = None,
    project_name: str = "Home O vs Away D - review",
    project_path: str = (
        r"Z:\SampleProjects"
        r"\Home O vs Away D - review.tapesift"
    ),
    start_ms: int = 100_000,
    end_ms: int = 126_560,
) -> dict:
    return {
        "schema_version": "2.1",
        "dataset_kind": "tapesift_run_pass_temporal_features",
        "research_cohort_id": "test-cohort",
        "project_name": project_name,
        "source_project": project_path,
        "source_video_path": r"Z:\SampleFilm\Test Film.mp4",
        "clip_id": clip_id or f"clip-{clip_number}",
        "clip_number": clip_number,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "classifier_eligible": eligible,
        "abstain_reasons": (
            [] if eligible else ["expected_two_usable_angles_found_1"]
        ),
        # These answer-bearing fields must never cross into review state.
        "label": "SECRET-RUN",
        "label_display": "SECRET RUN",
        "play_type": "SECRET RPO",
        "play_action": "SECRET PLAY ACTION",
        "features": {"secret_classifier_feature": 0.5},
        "temporal_diagnostics": {
            "classifier_eligible": eligible,
            "abstain_reasons": (
                [] if eligible else ["expected_two_usable_angles_found_1"]
            ),
            "transition": {
                "left_pulse_seconds": 9.0,
                "midpoint_seconds": 9.25,
                "right_pulse_seconds": 9.5,
            },
            "angles": [
                {
                    "angle": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 9.0,
                    "onset_seconds": 2.125,
                    "onset_confidence": 0.89,
                    "eligibility_reasons": [],
                },
                {
                    "angle": 2,
                    "start_seconds": 9.625,
                    "end_seconds": 26.56,
                    "onset_seconds": 3.375,
                    "onset_confidence": 0.42,
                    "eligibility_reasons": (
                        [] if eligible else ["weak_post_pre_motion_ratio"]
                    ),
                },
            ],
        },
    }


def _write_manifest(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(record, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _session(
    tmp_path: Path,
    records: list[dict] | None = None,
    *,
    only_flagged: bool = True,
) -> tuple[TemporalReviewSession, Path, Path, Path]:
    manifest = tmp_path / "temporal.jsonl"
    _write_manifest(
        manifest,
        records or [
            _record(1, eligible=False),
            _record(2, eligible=True),
        ],
    )
    state = tmp_path / "review.state.json"
    judgments = tmp_path / "review.judgments.jsonl"
    session = TemporalReviewSession.create(
        [manifest],
        state,
        judgments,
        only_flagged=only_flagged,
    )
    return session, manifest, state, judgments


def _assert_no_answer_keys(value: object) -> None:
    forbidden = {
        "label",
        "label_display",
        "play_type",
        "play_action",
        "features",
    }
    if isinstance(value, dict):
        assert forbidden.isdisjoint(value)
        for nested in value.values():
            _assert_no_answer_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_answer_keys(nested)


def test_default_queue_is_flagged_only_and_uses_exact_source_times(
    tmp_path: Path,
) -> None:
    session, _, _, _ = _session(tmp_path)

    assert len(session.items) == 1
    item = session.current
    assert item is not None
    assert item.clip_number == 1
    assert item.project_file_name == (
        "Home O vs Away D - review.tapesift"
    )
    assert item.angles[0].proposed_onset_ms == 102_125
    assert item.angles[1].proposed_onset_ms == 113_000
    assert session.seek_target_ms(item.item_id, 2) == 113_000
    assert session.preview_range_ms(item.item_id, 1) == (
        100_125,
        105_125,
    )
    assert session.preview_range_ms(
        item.item_id,
        2,
        lead_ms=10_000,
        tail_ms=30_000,
    ) == (109_625, 126_560)


def test_state_and_completed_sidecar_are_strictly_label_blind(
    tmp_path: Path,
) -> None:
    session, _, state, judgments = _session(tmp_path)
    item = session.current
    assert item is not None
    session.judge_angle(item.item_id, 1, "correct")
    session.judge_angle(item.item_id, 2, "late")

    state_payload = json.loads(state.read_text(encoding="utf-8"))
    judgment_payload = json.loads(
        judgments.read_text(encoding="utf-8").strip())
    _assert_no_answer_keys(state_payload)
    _assert_no_answer_keys(judgment_payload)
    state_text = state.read_text(encoding="utf-8")
    judgment_text = judgments.read_text(encoding="utf-8")
    for secret in (
        "SECRET-RUN",
        "SECRET RUN",
        "SECRET RPO",
        "SECRET PLAY ACTION",
        "secret_classifier_feature",
    ):
        assert secret not in state_text
        assert secret not in judgment_text


def test_completion_requires_both_angles_but_not_split(
    tmp_path: Path,
) -> None:
    session, _, _, judgments = _session(tmp_path)
    item = session.current
    assert item is not None
    original_index = session.current_index

    session.judge_angle(item.item_id, 1, "Early")
    assert item.angles[0].judgment == "early"
    assert not item.complete
    assert session.pending_count == 1
    assert judgments.read_text(encoding="utf-8") == ""

    session.judge_angle(item.item_id, 2, "unsure")
    assert item.complete
    assert item.split.judgment == ""
    assert session.completed_count == 1
    # Save & Next in the UI owns advancement; a judgment keeps both choices
    # visible until that explicit action.
    assert session.current_index == original_index
    records = [
        json.loads(line)
        for line in judgments.read_text(encoding="utf-8").splitlines()
    ]
    assert len(records) == 1
    assert [value["judgment"] for value in records[0]["angles"]] == [
        "early",
        "unsure",
    ]

    with pytest.raises(ValueError, match="Unsupported angle judgment"):
        session.judge_angle(item.item_id, 1, "probably")
    session.clear_angle_judgment(item.item_id, 1)
    assert not item.complete
    assert session.completed_count == 0
    assert judgments.read_text(encoding="utf-8") == ""


def test_optional_split_judgment_does_not_complete_an_item(
    tmp_path: Path,
) -> None:
    session, _, _, _ = _session(tmp_path)
    item = session.current
    assert item is not None

    session.judge_split(item.item_id, "incorrect")
    assert item.split.judgment == "incorrect"
    assert not item.complete
    with pytest.raises(ValueError, match="Unsupported split judgment"):
        session.judge_split(item.item_id, "early")


def test_load_resumes_judgments_and_refuses_changed_manifest(
    tmp_path: Path,
) -> None:
    session, manifest, state, judgments = _session(tmp_path)
    item = session.current
    assert item is not None
    session.judge_angle(item.item_id, 1, "correct")

    reopened = TemporalReviewSession.load(
        state,
        [manifest],
        judgments,
    )
    assert reopened.current is not None
    assert reopened.current.angles[0].judgment == "correct"
    assert reopened.current.angles[1].judgment == ""

    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="source manifest changed"):
        TemporalReviewSession.load(state, [manifest], judgments)


def test_tampered_measurement_is_refused_even_with_same_manifest(
    tmp_path: Path,
) -> None:
    _, manifest, state, judgments = _session(tmp_path)
    payload = json.loads(state.read_text(encoding="utf-8"))
    payload["items"][0]["angles"][0]["proposed_onset_ms"] += 1
    state.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="measurement signature is stale"):
        TemporalReviewSession.load(state, [manifest], judgments)


def test_cursor_navigation_and_full_queue_option(tmp_path: Path) -> None:
    records = [
        _record(1, eligible=False, clip_id="one"),
        _record(2, eligible=True, clip_id="two"),
        _record(3, eligible=False, clip_id="three"),
    ]
    session, _, _, _ = _session(
        tmp_path,
        records,
        only_flagged=False,
    )
    assert [item.clip_number for item in session.items] == [1, 2, 3]
    assert session.current_index == 0
    assert session.navigate(1).clip_number == 2
    assert session.set_cursor(99).clip_number == 3
    assert session.navigate(-1).clip_number == 2

    second = session.review_for("test-cohort:two")
    session.judge_angle(second.item_id, 1, "correct")
    session.judge_angle(second.item_id, 2, "correct")
    session.set_cursor(0)
    assert session.navigate(1, pending_only=True).clip_number == 3


@dataclass
class _ClipSnapshot:
    id: str
    start_ms: int
    end_ms: int


def test_project_binding_uses_file_identity_alias_and_never_touches_project(
    tmp_path: Path,
) -> None:
    manifest_project = (
        r"Z:\SampleProjects\Alias.tapesift"
    )
    session, _, _, _ = _session(
        tmp_path,
        [
            _record(
                4,
                eligible=False,
                clip_id="stable-id",
                project_name="Alias",
                project_path=manifest_project,
            )
        ],
    )
    current_project = tmp_path / "Current.tapesift"
    current_project.write_bytes(b"not opened by the review service")
    before = current_project.read_bytes()
    before_mtime = current_project.stat().st_mtime_ns
    snapshots = [_ClipSnapshot("stable-id", 100_000, 126_560)]

    matched = session.bind_project(
        current_project,
        snapshots,
        current_project_path_candidates=[
            tmp_path / "Alias.tapesift",
        ],
    )
    assert [item.clip_id for item in matched] == ["stable-id"]
    assert current_project.read_bytes() == before
    assert current_project.stat().st_mtime_ns == before_mtime

    with pytest.raises(ValueError, match="boundaries changed"):
        session.bind_project(
            current_project,
            [_ClipSnapshot("stable-id", 100_001, 126_560)],
            current_project_path_candidates=[tmp_path / "Alias.tapesift"],
        )
    with pytest.raises(ValueError, match="is missing"):
        session.bind_project(
            current_project,
            [],
            current_project_path_candidates=[tmp_path / "Alias.tapesift"],
        )


def test_snap_calibration_marks_exact_source_times_and_writes_sidecar(
    tmp_path: Path,
) -> None:
    session, manifest, state, judgments = _session(tmp_path)
    session = TemporalReviewSession.load(
        state,
        [manifest],
        judgments,
        workflow="snap_calibration",
    )
    item = session.current
    assert item is not None
    assert session.workflow == "snap_calibration"
    assert not item.snap_complete
    assert session.completed_count == 0

    session.mark_actual_snap(item.item_id, 1, 102_500)
    first = item.angle(1)
    assert first.actual_snap_ms == 102_500
    assert first.snap_status == "marked"
    assert first.snap_reviewed_at
    assert first.snap_delta_ms == 375
    assert first.delta_ms == 375
    assert session.pending_count == 1
    assert judgments.read_text(encoding="utf-8") == ""

    session.mark_snap_unavailable(item.item_id, 2, "not_visible")
    second = item.angle(2)
    assert second.actual_snap_ms is None
    assert second.snap_status == "not_visible"
    assert second.delta_ms is None
    assert item.snap_complete
    assert session.item_complete(item)
    assert session.completed_count == 1

    state_payload = json.loads(state.read_text(encoding="utf-8"))
    assert state_payload["workflow"] == "snap_calibration"
    assert state_payload["items"][0]["status"] == "complete"
    record = json.loads(judgments.read_text(encoding="utf-8").strip())
    assert record["dataset_kind"] == "tapesift_temporal_snap_calibration"
    assert record["workflow"] == "snap_calibration"
    assert record["angles"] == [
        {
            "actual_snap_ms": 102_500,
            "angle": 1,
            "delta_ms": 375,
            "proposed_onset_ms": 102_125,
            "range_end_ms": 109_000,
            "range_start_ms": 100_000,
            "snap_reviewed_at": first.snap_reviewed_at,
            "snap_status": "marked",
        },
        {
            "actual_snap_ms": None,
            "angle": 2,
            "delta_ms": None,
            "proposed_onset_ms": 113_000,
            "range_end_ms": 126_560,
            "range_start_ms": 109_625,
            "snap_reviewed_at": second.snap_reviewed_at,
            "snap_status": "not_visible",
        },
    ]
    _assert_no_answer_keys(state_payload)
    _assert_no_answer_keys(record)


def test_snap_calibration_completes_a_single_view_manifest(
    tmp_path: Path,
) -> None:
    record = _record(1, eligible=False)
    record["temporal_diagnostics"]["angles"] = [
        record["temporal_diagnostics"]["angles"][0]
    ]
    session, manifest, state, judgments = _session(tmp_path, [record])
    session = TemporalReviewSession.load(
        state,
        [manifest],
        judgments,
        workflow="snap_calibration",
    )
    item = session.current
    assert item is not None
    assert len(item.angles) == 1

    session.mark_actual_snap(item.item_id, 1, 102_500)

    assert item.snap_complete
    assert session.completed_count == 1
    saved = json.loads(judgments.read_text(encoding="utf-8").strip())
    assert len(saved["angles"]) == 1
    assert saved["angles"][0]["actual_snap_ms"] == 102_500


def test_actual_snap_must_be_inside_the_specific_angle_source_range(
    tmp_path: Path,
) -> None:
    session, _, _, _ = _session(tmp_path)
    session.workflow = "snap_calibration"
    item = session.current
    assert item is not None

    # 113 seconds is inside the clip but belongs to angle 2, not angle 1.
    with pytest.raises(ValueError, match="outside angle 1 source range"):
        session.mark_actual_snap(item.item_id, 1, 113_000)
    with pytest.raises(ValueError, match="outside angle 2 source range"):
        session.mark_actual_snap(item.item_id, 2, 109_624)
    with pytest.raises(ValueError, match="not_visible.*unsure"):
        session.mark_snap_unavailable(item.item_id, 1, "missing")

    session.mark_actual_snap(item.item_id, 1, 100_000)
    assert item.angle(1).actual_snap_ms == 100_000
    session.mark_actual_snap(item.item_id, 2, 126_560)
    assert item.angle(2).actual_snap_ms == 126_560


def test_snap_clear_and_pending_navigation_honor_calibration_workflow(
    tmp_path: Path,
) -> None:
    records = [
        _record(1, eligible=False, clip_id="one"),
        _record(2, eligible=False, clip_id="two"),
    ]
    session, _, _, judgments = _session(tmp_path, records)
    session.workflow = "snap_calibration"
    first = session.review_for("test-cohort:one")
    second = session.review_for("test-cohort:two")

    session.mark_snap_unavailable(first.item_id, 1, "unsure")
    session.mark_actual_snap(first.item_id, 2, 113_250)
    assert session.item_complete(first.item_id)
    assert session.completed_count == 1
    assert session.navigate(1, pending_only=True) is second

    session.clear_snap(first.item_id, 1)
    assert first.angle(1).snap_status == ""
    assert first.angle(1).snap_reviewed_at == ""
    assert not session.item_complete(first)
    assert session.completed_count == 0
    assert judgments.read_text(encoding="utf-8") == ""


def test_workflow_round_trips_and_legacy_state_defaults_to_onset(
    tmp_path: Path,
) -> None:
    session, manifest, state, judgments = _session(tmp_path)
    item = session.current
    assert item is not None
    assert session.workflow == "onset_judgment"

    legacy = json.loads(state.read_text(encoding="utf-8"))
    legacy.pop("workflow")
    for angle in legacy["items"][0]["angles"]:
        angle.pop("actual_snap_ms")
        angle.pop("snap_status")
        angle.pop("snap_reviewed_at")
    state.write_text(
        json.dumps(legacy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    reopened_legacy = TemporalReviewSession.load(
        state, [manifest], judgments)
    assert reopened_legacy.workflow == "onset_judgment"
    assert reopened_legacy.current is not None
    assert reopened_legacy.current.angle(1).snap_status == ""

    calibration = TemporalReviewSession.load_or_create(
        [manifest],
        state,
        judgments,
        workflow="snap_calibration",
    )
    assert calibration.workflow == "snap_calibration"
    resumed = TemporalReviewSession.load(state, [manifest], judgments)
    assert resumed.workflow == "snap_calibration"
    assert resumed.completed_count == 0
