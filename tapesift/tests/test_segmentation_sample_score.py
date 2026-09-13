"""Tests for component-isolated scoring of sampled segmentation truth."""

from __future__ import annotations

import copy

import pytest

from tapesift.research.segmentation_sample_score import (
    SampleScoreError,
    score_sampled_holdout,
)


FILM_DURATION_MS = 200_000


def root(
    index: int,
    start: int,
    end: int,
    *,
    kind: str = "play",
    film_id: str = "film-a",
    stratum: str = "confident",
) -> dict:
    item_id = f"{film_id}:{kind}:{index:04d}"
    return {
        "item_id": item_id,
        "film_id": film_id,
        "film_duration_ms": FILM_DURATION_MS,
        "start_ms": start,
        "end_ms": end,
        "original_start_ms": start,
        "original_end_ms": end,
        "candidate_kind": kind,
        "candidate_stratum": (
            "unclassified" if kind == "unclassified" else stratum),
        "prediction_indices": [index] if kind == "play" else [],
        "status": "pending",
        "decision": "",
        "edit_history": [],
    }


def final(
    source: dict,
    *,
    item_id: str | None = None,
    start: int | None = None,
    end: int | None = None,
    status: str = "verified",
    decision: str = "accepted",
    roots: list[dict] | None = None,
    history: list[str] | None = None,
) -> dict:
    lineage = roots or [source]
    return {
        **source,
        "item_id": item_id or source["item_id"],
        "start_ms": source["start_ms"] if start is None else start,
        "end_ms": source["end_ms"] if end is None else end,
        "original_start_ms": min(
            item["original_start_ms"] for item in lineage),
        "original_end_ms": max(
            item["original_end_ms"] for item in lineage),
        "prediction_indices": sorted({
            index
            for item in lineage
            for index in item["prediction_indices"]
        }),
        "status": status,
        "decision": decision,
        "edit_history": history or [],
    }


def predictions(roots: list[dict]) -> dict[str, dict]:
    by_film: dict[str, list[dict]] = {}
    unclassified: dict[str, list[dict]] = {}
    for item in roots:
        by_film.setdefault(item["film_id"], [])
        unclassified.setdefault(item["film_id"], [])
        if item["candidate_kind"] == "play":
            index = item["prediction_indices"][0]
            while len(by_film[item["film_id"]]) <= index:
                by_film[item["film_id"]].append({
                    "start_ms": 1,
                    "end_ms": 2,
                })
            by_film[item["film_id"]][index] = {
                "start_ms": item["start_ms"],
                "end_ms": item["end_ms"],
            }
        else:
            unclassified[item["film_id"]].append({
                "start_ms": item["start_ms"],
                "end_ms": item["end_ms"],
            })
    return {
        film_id: {
            "film_id": film_id,
            "duration_ms": FILM_DURATION_MS,
            "plays": plays,
            "unclassified": unclassified[film_id],
        }
        for film_id, plays in by_film.items()
    }


def score(roots: list[dict], items: list[dict]) -> dict:
    return score_sampled_holdout(
        roots,
        items,
        predictions(roots),
        expected_root_count=len(roots),
    )


def test_perfect_play_and_reviewed_unclassified_negative() -> None:
    play = root(0, 1_000, 11_000)
    gap = root(0, 20_000, 30_000, kind="unclassified")

    report = score([
        play, gap,
    ], [
        final(play),
        final(gap, status="excluded", decision="excluded"),
    ])

    assert report["aggregate"]["primary"]["precision"] == 1.0
    assert report["aggregate"]["primary"]["recall"] == 1.0
    assert report["aggregate"]["primary"]["false_positive_count"] == 0
    assert report["population"]["unclassified_review"] == {
        "reviewed_no_play": 1}


def test_excluded_play_is_false_positive() -> None:
    play = root(0, 1_000, 11_000)

    report = score(
        [play],
        [final(play, status="excluded", decision="excluded")],
    )

    primary = report["aggregate"]["primary"]
    assert primary["truth_count"] == 0
    assert primary["prediction_count"] == 1
    assert primary["false_positive_count"] == 1
    assert report["population"]["root_dispositions"] == {"excluded": 1}


def test_accepted_unclassified_root_is_one_false_negative() -> None:
    gap = root(0, 20_000, 30_000, kind="unclassified")

    report = score([gap], [final(gap)])

    primary = report["aggregate"]["primary"]
    assert primary["truth_count"] == 1
    assert primary["prediction_count"] == 0
    assert primary["false_negative_count"] == 1
    assert report["population"]["unclassified_review"] == {
        "standalone_missed_play": 1}


def test_one_root_split_into_two_truths_scores_one_prediction() -> None:
    play = root(0, 1_000, 11_000)
    token = "abcd1234"
    left = final(
        play,
        item_id=f"{play['item_id']}:split:{token}:a",
        end=6_000,
        decision="revised",
        history=["split@6000:left"],
    )
    right = final(
        play,
        item_id=f"{play['item_id']}:split:{token}:b",
        start=6_000,
        decision="revised",
        history=["split@6000:right"],
    )

    report = score([play], [left, right])

    primary = report["aggregate"]["primary"]
    assert report["population"]["root_count"] == 1
    assert primary["prediction_count"] == 1
    assert primary["truth_count"] == 2
    assert primary["matched_count"] == 0  # each half is below IoU .75
    assert primary["merge_count"] == 1
    assert report["population"]["root_dispositions"] == {
        "structural_split": 1}


def test_two_play_roots_merged_into_one_truth() -> None:
    first = root(0, 1_000, 6_000)
    second = root(1, 6_000, 11_000)
    merged_id = f"{first['item_id']}:merge:abcd1234"
    merged = final(
        first,
        item_id=merged_id,
        end=11_000,
        decision="revised",
        roots=[first, second],
        history=[
            f"merged:{first['item_id']}+{second['item_id']}",
        ],
    )

    report = score([first, second], [merged])

    primary = report["aggregate"]["primary"]
    assert report["population"]["root_count"] == 2
    assert primary["prediction_count"] == 2
    assert primary["truth_count"] == 1
    assert primary["split_count"] == 1
    assert report["population"]["root_dispositions"] == {
        "structural_merge": 2}
    assert len(report["components"]) == 1


def test_play_plus_unclassified_merge_is_boundary_continuation() -> None:
    play = root(0, 1_000, 8_000)
    gap = root(0, 8_000, 11_000, kind="unclassified")
    merged = final(
        play,
        item_id=f"{play['item_id']}:merge:abcd1234",
        end=11_000,
        decision="revised",
        roots=[play, gap],
        history=[f"merged:{play['item_id']}+{gap['item_id']}"],
    )

    report = score([play, gap], [merged])

    primary = report["aggregate"]["primary"]
    assert primary["prediction_count"] == 1
    assert primary["truth_count"] == 1
    assert report["population"]["unclassified_review"] == {
        "boundary_continuation": 1}


def test_two_unclassified_roots_merged_are_one_false_negative() -> None:
    first = root(0, 1_000, 6_000, kind="unclassified")
    second = root(1, 6_000, 11_000, kind="unclassified")
    merged = final(
        first,
        item_id=f"{first['item_id']}:merge:abcd1234",
        end=11_000,
        decision="revised",
        roots=[first, second],
        history=[f"merged:{first['item_id']}+{second['item_id']}"],
    )

    report = score([first, second], [merged])

    primary = report["aggregate"]["primary"]
    assert primary["truth_count"] == 1
    assert primary["prediction_count"] == 0
    assert primary["false_negative_count"] == 1
    assert report["population"]["root_count"] == 2


def test_split_may_have_one_verified_and_one_excluded_child() -> None:
    play = root(0, 1_000, 11_000)
    token = "abcd1234"
    left = final(
        play,
        item_id=f"{play['item_id']}:split:{token}:a",
        end=6_000,
        decision="revised",
        history=["split@6000:left"],
    )
    right = final(
        play,
        item_id=f"{play['item_id']}:split:{token}:b",
        start=6_000,
        status="excluded",
        decision="excluded",
        history=["split@6000:right"],
    )

    report = score([play], [left, right])

    assert report["integrity"]["truth_item_count"] == 1
    assert report["integrity"]["excluded_item_count"] == 1
    assert report["population"]["root_count"] == 1


def test_primary_and_diagnostic_thresholds_are_separate() -> None:
    play = root(0, 1_000, 11_000)
    revised = final(
        play,
        start=1_000,
        end=16_000,
        decision="revised",
        history=["end@16000"],
    )

    report = score([play], [revised])

    assert report["aggregate"]["primary"]["matched_count"] == 0
    assert report["aggregate"]["diagnostic"]["matched_count"] == 1
    boundary = report["aggregate"]["all_one_to_one_boundary"]
    assert boundary["denominator"] == 1
    assert boundary["skipped_non_one_to_one_component_count"] == 0
    assert boundary["median_max_boundary_error_ms"] == 5_000
    assert boundary["p90_max_boundary_error_ms"] == 5_000
    assert boundary["comparisons"][0]["iou"] == pytest.approx(2 / 3)


def test_all_one_to_one_boundary_metrics_ignore_iou_match_threshold() -> None:
    first = root(0, 1_000, 11_000)
    second = root(1, 50_000, 60_000)
    first_truth = final(
        first,
        end=21_000,
        decision="revised",
        history=["end@21000"],
    )
    second_truth = final(
        second,
        end=80_000,
        decision="revised",
        history=["end@80000"],
    )

    report = score([first, second], [first_truth, second_truth])

    assert report["aggregate"]["primary"]["matched_count"] == 0
    boundary = report["aggregate"]["all_one_to_one_boundary"]
    assert boundary["denominator"] == 2
    assert boundary["zero_max_boundary_error_count"] == 0
    assert boundary["median_max_boundary_error_ms"] == 15_000
    assert boundary["p90_max_boundary_error_ms"] == 19_000
    assert boundary["skipped_by_component_shape"] == {}


def test_all_one_to_one_boundary_metrics_report_skipped_shapes() -> None:
    play = root(0, 1_000, 11_000)
    gap = root(0, 20_000, 30_000, kind="unclassified")
    token = "abcd1234"
    left = final(
        play,
        item_id=f"{play['item_id']}:split:{token}:a",
        end=6_000,
        decision="revised",
        history=["split@6000:left"],
    )
    right = final(
        play,
        item_id=f"{play['item_id']}:split:{token}:b",
        start=6_000,
        decision="revised",
        history=["split@6000:right"],
    )

    report = score(
        [play, gap],
        [
            left,
            right,
            final(gap, status="excluded", decision="excluded"),
        ],
    )

    boundary = report["aggregate"]["all_one_to_one_boundary"]
    assert boundary["denominator"] == 0
    assert boundary["total_lineage_component_count"] == 2
    assert boundary["skipped_non_one_to_one_component_count"] == 2
    assert boundary["skipped_by_component_shape"] == {
        "0_sampled_predictions_0_verified_truths": 1,
        "1_sampled_predictions_2_verified_truths": 1,
    }
    assert boundary["median_max_boundary_error_ms"] is None
    assert boundary["comparisons"] == []


def test_matching_never_crosses_unmerged_root_components() -> None:
    first = root(0, 1_000, 11_000)
    second = root(1, 11_000, 21_000)
    # The reviewed bounds are swapped but remain disjoint. Film-global
    # matching would cross-match both perfectly; component-local matching
    # must not.
    first_truth = final(
        first, start=11_000, end=21_000, decision="revised",
        history=["start@11000", "end@21000"])
    second_truth = final(
        second, start=1_000, end=11_000, decision="revised",
        history=["start@1000", "end@11000"])

    report = score([first, second], [first_truth, second_truth])

    assert report["aggregate"]["diagnostic"]["matched_count"] == 0
    assert len(report["components"]) == 2


@pytest.mark.parametrize("mutation, match", [
    (
        lambda roots, items, payloads: items[0].update(status="pending"),
        "not terminal",
    ),
    (
        lambda roots, items, payloads: items.clear(),
        "silently dropped",
    ),
    (
        lambda roots, items, payloads: items.append(copy.deepcopy(items[0])),
        "Duplicate final item id",
    ),
    (
        lambda roots, items, payloads: payloads["film-a"]["plays"][0].update(
            end_ms=12_000),
        "bounds changed",
    ),
])
def test_fail_closed_on_invalid_review_or_prediction(
    mutation,
    match: str,
) -> None:
    play = root(0, 1_000, 11_000)
    roots = [play]
    items = [final(play)]
    payloads = predictions(roots)
    mutation(roots, items, payloads)

    with pytest.raises(SampleScoreError, match=match):
        score_sampled_holdout(roots, items, payloads)


def test_fail_closed_on_duplicate_selected_prediction() -> None:
    first = root(0, 1_000, 11_000)
    second = root(1, 20_000, 30_000)
    second["prediction_indices"] = [0]

    with pytest.raises(SampleScoreError, match="sampled twice"):
        score_sampled_holdout(
            [first, second],
            [final(first), final(second)],
            {
                "film-a": {
                    "film_id": "film-a",
                    "duration_ms": FILM_DURATION_MS,
                    "plays": [{"start_ms": 1_000, "end_ms": 11_000}],
                    "unclassified": [],
                },
            },
        )


def test_fail_closed_on_unknown_or_incomplete_lineage() -> None:
    play = root(0, 1_000, 11_000)
    broken = final(
        play,
        item_id=f"{play['item_id']}:merge:abcd1234",
        decision="revised",
    )

    with pytest.raises(SampleScoreError, match="without merge history"):
        score([play], [broken])


def test_fail_closed_on_truth_overlap() -> None:
    first = root(0, 1_000, 11_000)
    second = root(1, 11_000, 21_000)
    overlap = final(
        second,
        start=10_000,
        decision="revised",
        history=["start@10000"],
    )

    with pytest.raises(SampleScoreError, match="Final truth overlaps"):
        score([first, second], [final(first), overlap])


def test_fail_closed_on_unclassified_root_outside_frozen_range() -> None:
    gap = root(0, 20_000, 30_000, kind="unclassified")
    payloads = predictions([gap])
    payloads["film-a"]["unclassified"][0] = {
        "start_ms": 21_000,
        "end_ms": 30_000,
    }

    with pytest.raises(SampleScoreError, match="exactly one"):
        score_sampled_holdout([gap], [final(gap)], payloads)


def test_reports_per_film_and_per_stratum_candidate_rates() -> None:
    confident = root(0, 1_000, 11_000, stratum="confident")
    review = root(0, 1_000, 11_000, film_id="film-b",
                  stratum="other_review")
    review_truth = final(
        review,
        start=1_000,
        end=20_000,
        decision="revised",
        history=["end@20000"],
    )

    report = score(
        [confident, review],
        [final(confident), review_truth],
    )

    assert set(report["by_film"]) == {"film-a", "film-b"}
    assert report["by_stratum"]["confident"]["candidate_match_rate"] == 1.0
    assert report["by_stratum"]["confident"]["primary"][
        "matched_count"] == 1
    assert report["by_stratum"]["other_review"][
        "candidate_match_rate"] == 0.0
