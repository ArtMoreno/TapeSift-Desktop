"""Focused tests for the Iteration 7F failure atlas."""

from __future__ import annotations

from tapesift.research.snap_onset_failure_atlas import classify_failure


def test_near_boundary_late_failure_with_stable_detections() -> None:
    tags = classify_failure(-521, {
        "sampled_frames": 36,
        "low_match_frames": 0,
        "minimum_person_detections": 13,
    })

    assert tags == (
        "late_detection",
        "near_boundary_quantization_candidate",
        "stable_detection_timing_ambiguity",
    )


def test_severe_failure_with_detector_continuity_risk() -> None:
    tags = classify_failure(-1373, {
        "sampled_frames": 80,
        "low_match_frames": 14,
        "minimum_person_detections": 0,
    })

    assert tags == (
        "late_detection",
        "severe_timing_miss",
        "detector_continuity_risk",
    )


def test_early_midrange_failure_is_distinct_from_quantization() -> None:
    tags = classify_failure(802, {
        "sampled_frames": 94,
        "low_match_frames": 0,
        "minimum_person_detections": 8,
    })

    assert tags == (
        "early_detection",
        "midrange_timing_miss",
        "stable_detection_timing_ambiguity",
    )
