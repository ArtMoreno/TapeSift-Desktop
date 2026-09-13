"""Focused tests for Iteration 7D formation localization."""

from __future__ import annotations

import cv2
import numpy as np

from tapesift.research.snap_onset_formation_motion import (
    CHANNELS,
    FormationAnchor,
    formation_policy_grid,
    formation_roi_mask,
    localize_formation_band,
)


def _green_field() -> np.ndarray:
    frame = np.zeros((270, 480, 3), dtype=np.uint8)
    frame[:] = (35, 125, 35)
    for x in range(30, 480, 60):
        cv2.line(frame, (x, 40), (x, 230), (220, 220, 220), 1)
    return frame


def test_localizes_vertical_formation_band() -> None:
    frame = _green_field()
    for y in range(60, 220, 25):
        cv2.circle(frame, (315 + (y % 3) * 6, y), 6, (20, 20, 230), -1)
        cv2.circle(frame, (345 - (y % 4) * 5, y), 6, (230, 60, 20), -1)

    anchor = localize_formation_band([frame] * 5)

    assert anchor.axis == "x"
    assert 0.55 <= anchor.center_fraction <= 0.78
    assert anchor.confidence > 0


def test_formation_roi_obeys_anchor_axis() -> None:
    anchor = FormationAnchor("x", 0.75, 0.25, 1.0, 60, 0.1)

    mask = formation_roi_mask((200, 400), anchor)

    assert mask[:, 300].sum() > 0
    assert mask[:, 50].sum() == 0


def test_formation_policy_grid_is_bounded_and_deterministic() -> None:
    first = formation_policy_grid()

    assert first == formation_policy_grid()
    assert len(first) == 432
    assert {policy.channel for policy in first} == set(CHANNELS)
