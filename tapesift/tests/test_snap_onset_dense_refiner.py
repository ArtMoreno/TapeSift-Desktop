"""Focused tests for Iteration 7G dense transition bracketing."""

from __future__ import annotations

import pytest

from tapesift.research.snap_onset_dense_refiner import (
    select_dense_transition,
)


def test_selects_strongest_sustained_transition_midpoint() -> None:
    times = [1000 + index * 125 for index in range(9)]
    motion = [0, 5, 8, 12, 20, 58, 72, 74, 76]

    selected = select_dense_transition(times, motion, 1500)

    assert selected["selection_status"] == "dense_transition_interval_midpoint"
    assert selected["candidate_onset_ms"] == 1562
    assert selected["motion_gain"] > 15


def test_retains_7e_when_no_transition_qualifies() -> None:
    times = [1000 + index * 125 for index in range(7)]
    motion = [0, 4, 6, 7, 9, 10, 12]

    selected = select_dense_transition(times, motion, 1375)

    assert selected["candidate_onset_ms"] == 1375
    assert selected["selection_status"].startswith("iteration_7e_fallback")


def test_rejects_mismatched_dense_vectors() -> None:
    with pytest.raises(ValueError, match="equal length"):
        select_dense_transition([1000, 1125], [0], 1000)


def test_transition_selection_is_deterministic() -> None:
    times = [1000 + index * 125 for index in range(10)]
    motion = [0, 5, 10, 45, 55, 12, 18, 60, 70, 75]

    first = select_dense_transition(times, motion, 1500)

    assert first == select_dense_transition(times, motion, 1500)
