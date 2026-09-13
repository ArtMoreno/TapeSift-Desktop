"""Focused tests for Iteration 7H deterministic short tracklets."""

from __future__ import annotations

import numpy as np

from tapesift.research.snap_onset_detected_players import PlayerDetection
from tapesift.research.snap_onset_tracklets import (
    CHANNEL,
    TRACKLET_POLICY,
    advance_tracklets,
    initialize_tracklets,
)


def _detections(offset: float = 0.0) -> tuple[PlayerDetection, ...]:
    return tuple(
        PlayerDetection(20 + index * 20 + (offset if index < 4 else 0), 80, 0.9)
        for index in range(8)
    )


def test_tracklets_require_three_consecutive_observations() -> None:
    identity = np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32)
    tracks, next_id = initialize_tracklets(_detections())

    tracks, first, diagnostics, next_id = advance_tracklets(
        tracks, _detections(), identity, (120, 220), next_id
    )

    assert first == 0
    assert diagnostics["stable_matches"] == 0

    tracks, second, diagnostics, _next_id = advance_tracklets(
        tracks, _detections(4), identity, (120, 220), next_id
    )

    assert diagnostics["stable_matches"] == 8
    assert diagnostics["quality_passed"] is True
    assert second == 50


def test_one_missing_frame_retains_short_track() -> None:
    identity = np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32)
    tracks, next_id = initialize_tracklets(_detections())

    updated, _value, diagnostics, _next_id = advance_tracklets(
        tracks, _detections()[:-1], identity, (120, 220), next_id
    )

    assert len(updated) == 8
    assert diagnostics["retained_missed_tracks"] == 1


def test_tracklet_policy_reuses_frozen_7e_thresholds() -> None:
    assert TRACKLET_POLICY.channel == CHANNEL
    assert TRACKLET_POLICY.relative_score_floor == 0.8
    assert TRACKLET_POLICY.minimum_score == 0.12
    assert TRACKLET_POLICY.minimum_post_pre_ratio == 2.0
    assert TRACKLET_POLICY.output_lag_seconds == 0.25


def test_tracklet_assignment_is_deterministic() -> None:
    identity = np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32)
    tracks, next_id = initialize_tracklets(_detections())

    first = advance_tracklets(
        tracks, _detections(2), identity, (120, 220), next_id
    )
    second = advance_tracklets(
        tracks, _detections(2), identity, (120, 220), next_id
    )

    assert first == second
