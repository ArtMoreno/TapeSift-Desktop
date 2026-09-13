"""Focused tests for the Iteration 7B candidate onset refiner."""

from __future__ import annotations

from tapesift.research.run_pass_features import SignalFrame
from tapesift.research.snap_onset_refiner import (
    AngleTrace,
    RefinerPolicy,
    RiseCandidate,
    apply_policy,
    build_rise_candidates,
    policy_grid,
    score_policy,
)


def _trace(
    candidates: tuple[RiseCandidate, ...],
    *,
    item_id: str = "cohort:clip",
    angle: int = 1,
    actual_snap_ms: int = 5_000,
) -> AngleTrace:
    return AngleTrace(
        item_id=item_id,
        cohort_id="cohort",
        clip_id=item_id,
        clip_number=1,
        angle=angle,
        source_video_path="C:/film.mp4",
        range_start_ms=0,
        range_end_ms=10_000,
        actual_snap_ms=actual_snap_ms,
        temporal_v21_onset_ms=6_000,
        candidates=candidates,
    )


def test_policy_selects_earliest_qualified_relative_rise() -> None:
    trace = _trace((
        RiseCandidate(3.0, 0.30, 1.8),
        RiseCandidate(4.0, 0.50, 2.2),
        RiseCandidate(5.0, 0.60, 2.4),
    ))
    policy = RefinerPolicy(0.65, 0.18, 1.75, 0.125)

    result = apply_policy(trace, policy)

    assert result["selection_status"] == (
        "earliest_qualified_sustained_rise"
    )
    assert result["candidate_onset_ms"] == 4_125


def test_policy_falls_back_when_no_rise_qualifies() -> None:
    trace = _trace((
        RiseCandidate(3.0, 0.10, 1.1),
        RiseCandidate(5.0, 0.15, 1.2),
    ))
    policy = RefinerPolicy(0.80, 0.24, 2.0, 0.0)

    result = apply_policy(trace, policy)

    assert result["candidate_onset_ms"] == 6_000
    assert result["selection_status"] == (
        "temporal_v21_fallback_no_qualified_rise"
    )


def test_rise_candidates_capture_calm_to_motion_transition() -> None:
    frames = []
    for index in range(96):
        time_seconds = index / 8
        motion = 1.0 if time_seconds < 4.0 else 8.0
        frames.append(SignalFrame(
            time_s=time_seconds,
            yavg=50.0,
            satavg=10.0,
            ydif=motion,
            udif=motion,
            vdif=motion,
        ))

    candidates = build_rise_candidates(frames)

    assert candidates
    assert max(candidate.score for candidate in candidates) > 0.5
    assert max(candidate.post_pre_ratio for candidate in candidates) > 2.0


def test_score_policy_reports_paired_play_metric() -> None:
    policy = RefinerPolicy(0.50, 0.12, 1.5, 0.0)
    candidates = (RiseCandidate(5.0, 0.60, 2.0),)
    traces = [
        _trace(candidates, angle=1, actual_snap_ms=5_100),
        _trace(candidates, angle=2, actual_snap_ms=4_900),
    ]

    result = score_policy(traces, policy)

    assert result["overall"]["within_500_ms"] == 2
    assert result["overall"]["both_angles_within_500_ms"] == 1


def test_policy_grid_is_small_and_deterministic() -> None:
    first = policy_grid()
    second = policy_grid()

    assert first == second
    assert len(first) == 108
    assert len({policy.policy_id for policy in first}) == 108
