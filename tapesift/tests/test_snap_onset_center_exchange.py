from __future__ import annotations

from tapesift.research.snap_onset_center_exchange import (
    score_rows,
    select_local_candidate,
)


def test_select_local_candidate_uses_strongest_qualified_rise():
    times = [-500, -400, -300, -200, -100, 0, 100, 200]
    scores = [2.0, 2.5, 2.0, 2.4, 3.0, 14.0, 10.0, 20.0]

    selected = select_local_candidate(times, scores, 0)

    assert selected["status"] == "selected_local_rise"
    assert selected["candidate_ms"] == 150
    assert selected["qualified_count"] == 3


def test_select_local_candidate_falls_back_for_weak_signal():
    selected = select_local_candidate(
        [-500, -400, -300, -200, -100, 0, 100, 200],
        [2.0, 2.5, 2.0, 2.4, 3.0, 3.5, 3.0, 3.2],
        0,
    )

    assert selected["status"] == "no_qualified_local_rise"
    assert selected["candidate_ms"] is None


def test_score_rows_counts_angle_and_paired_coverage():
    rows = [
        {"item_id": "a", "error_ms": 100},
        {"item_id": "a", "error_ms": -200},
        {"item_id": "b", "error_ms": 600},
        {"item_id": "b", "error_ms": 300},
    ]

    metrics = score_rows(rows)

    assert metrics["within_500_ms"] == 3
    assert metrics["within_500_ms_share"] == 0.75
    assert metrics["both_angles_within_500_ms"] == 1
    assert metrics["both_angles_within_500_ms_share"] == 0.5
