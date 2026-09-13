"""Focused tests for Iteration 7C player-motion onset research."""

from __future__ import annotations

import cv2
import numpy as np

from tapesift.research.snap_onset_player_motion import (
    CHANNELS,
    compensated_motion_channels,
    estimate_global_camera_transform,
    player_motion_policy_grid,
)


def _field_pattern() -> np.ndarray:
    image = np.zeros((180, 320), dtype=np.uint8)
    for x in range(20, 320, 40):
        cv2.line(image, (x, 25), (x, 155), 90, 2)
    for y in range(35, 160, 30):
        cv2.line(image, (10, y), (310, y), 120, 2)
    for x, y in ((55, 70), (125, 115), (210, 60), (270, 125)):
        cv2.circle(image, (x, y), 7, 220, -1)
    return image


def test_camera_transform_recovers_translation() -> None:
    previous = _field_pattern()
    transform = np.float32([[1, 0, 5], [0, 1, -3]])
    current = cv2.warpAffine(
        previous,
        transform,
        (previous.shape[1], previous.shape[0]),
        borderMode=cv2.BORDER_REPLICATE,
    )

    estimated, inliers = estimate_global_camera_transform(previous, current)

    assert inliers >= 6
    assert abs(float(estimated[0, 2]) - 5) < 0.75
    assert abs(float(estimated[1, 2]) + 3) < 0.75


def test_player_residual_exceeds_camera_only_motion() -> None:
    previous = _field_pattern()
    transform = np.float32([[1, 0, 4], [0, 1, 2]])
    camera_only = cv2.warpAffine(
        previous,
        transform,
        (previous.shape[1], previous.shape[0]),
        borderMode=cv2.BORDER_REPLICATE,
    )
    player_motion = camera_only.copy()
    cv2.rectangle(player_motion, (145, 75), (180, 120), 255, -1)

    camera_channels, _ = compensated_motion_channels(
        previous,
        camera_only,
    )
    player_channels, _ = compensated_motion_channels(
        previous,
        player_motion,
    )

    assert player_channels["compensated_active_share"] > (
        camera_channels["compensated_active_share"]
    )
    assert player_channels["localized_cell_excess"] > (
        camera_channels["localized_cell_excess"]
    )


def test_policy_grid_is_bounded_and_deterministic() -> None:
    first = player_motion_policy_grid()

    assert first == player_motion_policy_grid()
    assert len(first) == 432
    assert {policy.channel for policy in first} == set(CHANNELS)
