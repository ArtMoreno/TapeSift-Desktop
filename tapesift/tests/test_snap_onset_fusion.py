"""Focused tests for Iteration 7C.1 onset fusion."""

from __future__ import annotations

from tapesift.research.snap_onset_fusion import (
    FusionPolicy,
    policy_grid,
    score_fusion_policy,
)


def _row(
    onset: int,
    *,
    angle: int,
    actual: int = 5_000,
) -> dict:
    return {
        "item_id": "film:clip",
        "cohort_id": "film",
        "clip_id": "clip",
        "clip_number": 1,
        "angle": angle,
        "actual_snap_ms": actual,
        "temporal_v21_onset_ms": 6_000,
        "candidate_onset_ms": onset,
    }


def test_player_replaces_global_only_beyond_threshold() -> None:
    global_rows = [_row(4_900, angle=1), _row(5_100, angle=2)]
    player_rows = [_row(5_000, angle=1), _row(6_000, angle=2)]

    result = score_fusion_policy(
        global_rows,
        player_rows,
        FusionPolicy(750),
    )

    assert result["rows"][0]["candidate_onset_ms"] == 4_900
    assert result["rows"][0]["selection_source"] == (
        "iteration_7b1_global_motion"
    )
    assert result["rows"][1]["candidate_onset_ms"] == 6_000
    assert result["rows"][1]["selection_source"] == (
        "iteration_7c_player_motion"
    )


def test_fusion_policy_grid_is_bounded_and_deterministic() -> None:
    first = policy_grid()

    assert first == policy_grid()
    assert len(first) == 8
    assert len({policy.policy_id for policy in first}) == 8
