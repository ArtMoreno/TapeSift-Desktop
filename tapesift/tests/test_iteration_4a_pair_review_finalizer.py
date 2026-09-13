from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.finalize_iteration_4a_pair_review import (
    PairReviewFinalizationError,
    _exact_success_interval,
    _expected_truth,
    _grade_review_roots,
    build_report,
    default_spec,
    write_reports,
)


def test_exact_all_success_interval_matches_25_item_review() -> None:
    lower, upper = _exact_success_interval(25, 25)

    assert lower == pytest.approx(0.8628148284692875)
    assert upper == 1.0


def test_exact_zero_edit_interval_is_complementary() -> None:
    lower, upper = _exact_success_interval(0, 25)

    assert lower == 0.0
    assert upper == pytest.approx(0.1371851715307125)


def test_truth_mirror_rejects_changed_review_boundary() -> None:
    from scripts.finalize_iteration_4a_pair_review import _truth_mirror

    item = {
        "item_id": "item-1",
        "film_id": "film",
        "source_file": "film.mp4",
        "start_ms": 1_000,
        "end_ms": 2_000,
        "angle_starts_ms": [1_000, 1_500],
        "status": "verified",
        "decision": "accepted",
        "edit_history": [],
        "verified_at": "now",
        "candidate_kind": "black_gap_pair",
        "candidate_stratum": "paired",
        "candidate_index": 1,
        "prediction_indices": [1],
        "original_start_ms": 1_000,
        "original_end_ms": 2_000,
        "detector_needs_review": True,
        "detector_reason": "review",
        "detector_signal": "mixed",
        "angle_count": 2,
    }
    truth = _expected_truth(
        item, "segmentation-iteration-4a-black-gap-pair-v2")

    assert _truth_mirror(item, truth) is True
    truth["end_ms"] = 2_001
    assert _truth_mirror(item, truth) is False
    truth = _expected_truth(
        item, "segmentation-iteration-4a-black-gap-pair-v2")
    truth["unexpected"] = True
    assert _truth_mirror(item, truth) is False


def test_write_reports_refuses_to_replace_either_artifact(
        tmp_path: Path) -> None:
    from scripts.finalize_iteration_4a_pair_review import (
        LockedArtifact,
        PairReviewSpec,
        ReportBundle,
    )

    placeholder = LockedArtifact(tmp_path / "unused", "0" * 64)
    spec = PairReviewSpec(
        root=tmp_path,
        locked_queue=placeholder,
        final_state=placeholder,
        final_truth=placeholder,
        selection=placeholder,
        candidate_lock=placeholder,
        prediction=placeholder,
        detector_source=placeholder,
        development_ab=placeholder,
        detector_commit="0" * 40,
        output_json=tmp_path / "result.json",
        output_markdown=tmp_path / "result.md",
    )
    bundle = ReportBundle({}, json.dumps({}) + "\n", "# Result\n")
    spec.output_markdown.write_text("existing", encoding="utf-8")

    with pytest.raises(FileExistsError, match="Refusing to replace"):
        write_reports(spec, bundle)

    assert not spec.output_json.exists()
    assert spec.output_markdown.read_text(encoding="utf-8") == "existing"


def test_non_boundary_exact_interval_contains_observed_rate() -> None:
    lower, upper = _exact_success_interval(24, 25)

    assert 0.0 < lower < 24 / 25 < upper < 1.0


def _review_item(item_id: str, *, start: int = 1_000,
                 end: int = 3_000) -> dict:
    return {
        "item_id": item_id,
        "film_id": "film",
        "film_name": "Film",
        "source_file": "film.mp4",
        "analysis_source": "film.mp4",
        "frame_rate": 60.0,
        "film_duration_ms": 10_000,
        "start_ms": start,
        "end_ms": end,
        "original_start_ms": 1_000,
        "original_end_ms": 3_000,
        "candidate_kind": "black_gap_pair",
        "candidate_index": 0,
        "prediction_indices": [0],
        "angle_starts_ms": [start, 2_000] if start < 2_000 < end else [start],
        "detector_needs_review": True,
        "detector_reason": "review",
        "detector_signal": "mixed",
        "angle_count": 2 if start < 2_000 < end else 1,
        "candidate_stratum": "paired",
        "status": "pending",
        "decision": "",
        "edit_history": [],
        "verified_at": "",
    }


def test_review_root_grading_counts_revisions_and_splits() -> None:
    revised_root = _review_item("root-a")
    split_root = _review_item("root-b")
    split_root["candidate_index"] = 1
    split_root["prediction_indices"] = [1]

    revised = {
        **revised_root,
        "start_ms": 1_100,
        "angle_starts_ms": [1_100, 2_000],
        "status": "verified",
        "decision": "revised",
        "edit_history": ["start@1100"],
        "verified_at": "now",
    }
    left = {
        **split_root,
        "item_id": "root-b:split:abcd:a",
        "end_ms": 2_000,
        "angle_starts_ms": [1_000],
        "angle_count": 1,
        "status": "verified",
        "decision": "revised",
        "edit_history": ["split@2000:left"],
        "verified_at": "now",
    }
    right = {
        **split_root,
        "item_id": "root-b:split:abcd:b",
        "start_ms": 2_000,
        "angle_starts_ms": [2_000],
        "angle_count": 1,
        "status": "verified",
        "decision": "revised",
        "edit_history": ["split@2000:right"],
        "verified_at": "now",
    }

    grade = _grade_review_roots(
        [revised_root, split_root], [revised, left, right])

    assert grade == {
        "same_snap_pairs": 1,
        "accepted_unchanged": 0,
        "accepted_with_boundary_revision": 1,
        "excluded": 0,
        "split_roots": 1,
        "exact_boundary_accepts": 0,
        "boundary_edits": 1,
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda item: item.update({
                "item_id": "root:split:abcd:a",
                "status": "verified",
                "decision": "revised",
                "edit_history": ["split@2000:left"],
                "verified_at": "now",
            }),
            "lost a descendant",
        ),
        (
            lambda item: item.update({
                "start_ms": 1_100,
                "angle_starts_ms": [1_100, 2_000],
                "status": "verified",
                "decision": "accepted",
                "edit_history": ["start@1100"],
                "verified_at": "now",
            }),
            "unrecorded edits",
        ),
        (
            lambda item: item.update({
                "end_ms": 1_900,
                "angle_starts_ms": [1_000],
                "angle_count": 1,
                "status": "verified",
                "decision": "revised",
                "edit_history": ["end@1900"],
                "verified_at": "now",
            }),
            "two-angle pair",
        ),
    ],
)
def test_review_root_grading_rejects_inconsistent_pair_lineage(
        mutation, message: str) -> None:
    root = _review_item("root")
    final = dict(root)
    mutation(final)

    with pytest.raises(PairReviewFinalizationError, match=message):
        _grade_review_roots([root], [final])


def test_frozen_completed_review_builds_deterministically_when_available(
        ) -> None:
    spec = default_spec()
    if not spec.locked_queue.path.is_file():
        pytest.skip("Local ignored Iteration 4A artifacts are unavailable")

    first = build_report(spec)
    second = build_report(spec)

    assert first.json_text == second.json_text
    assert first.markdown_text == second.markdown_text
    assert first.report["integrity"]["passed"] is True
    assert first.report["results"]["same_snap_pairs"] == 25
