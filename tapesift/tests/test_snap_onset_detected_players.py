"""Focused tests for Iteration 7E detected-player motion."""

from __future__ import annotations

import numpy as np

from tapesift.research.snap_onset_detected_players import (
    CHANNELS,
    PlayerDetection,
    detected_player_motion_channels,
    detected_player_policy_grid,
    filter_person_detections,
)


def test_person_filter_retains_plausible_field_boxes() -> None:
    boxes = np.array([
        [100, 100, 130, 200],
        [200, 10, 205, 15],
        [0, 0, 500, 500],
    ], dtype=np.float32)
    confidences = np.array([0.8, 0.9, 0.9], dtype=np.float32)

    retained = filter_person_detections(boxes, confidences, 960, 540)

    assert len(retained) == 1
    assert retained[0].foot_x == 115
    assert retained[0].foot_y == 200


def test_camera_translation_is_removed_from_player_points() -> None:
    previous_gray = np.zeros((180, 320), dtype=np.uint8)
    current_gray = previous_gray.copy()
    previous = (
        PlayerDetection(100, 100, 0.9),
        PlayerDetection(200, 120, 0.9),
    )
    current = (
        PlayerDetection(100, 100, 0.9),
        PlayerDetection(210, 120, 0.9),
    )

    values, diagnostics = detected_player_motion_channels(
        previous_gray,
        current_gray,
        previous,
        current,
    )

    assert diagnostics["matched_detections"] == 2
    assert values["detected_speed_p75"] > 0
    assert values["detected_moving_share"] == 50


def test_detected_player_policy_grid_is_bounded() -> None:
    first = detected_player_policy_grid()

    assert first == detected_player_policy_grid()
    assert len(first) == 432
    assert {policy.channel for policy in first} == set(CHANNELS)
