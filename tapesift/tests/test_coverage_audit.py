"""Conservative possible-miss prioritization stays explainable and fail-safe."""

from __future__ import annotations

import copy

from tapesift.services.coverage_audit_service import (
    AUDIT_VERSION,
    CHECK_FIRST,
    LOW_SIGNAL,
    REVIEW,
    audit_possible_missed,
)


def _capture(
    coverage: list[dict],
    *,
    median_play_ms: int = 30_000,
    plays: list[dict] | None = None,
) -> dict:
    return {
        "parameters": {
            "min_play_s": 4.0,
            "max_play_s": 90.0,
        },
        "result": {
            "summary": {
                "profile": {
                    "median_play_ms": median_play_ms,
                    "max_play_ms": 60_000,
                    "modal_angles": 1,
                    "modal_share": 0.8,
                    "calibrated": bool(median_play_ms),
                },
            },
            "plays": list(plays or []),
            "coverage": coverage,
        },
    }


def _segment(start_ms: int, end_ms: int, kind: str) -> dict:
    return {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "kind": kind,
        "reason": "",
        "candidate_kind": "",
        "candidate_index": None,
    }


def test_typical_gap_bounded_by_black_is_check_first() -> None:
    capture = _capture([
        _segment(0, 2_000, "separator"),
        _segment(2_000, 32_000, "possible_missed"),
        _segment(32_000, 34_000, "separator"),
    ])

    audit = audit_possible_missed(capture)[1]

    assert audit.level == CHECK_FIRST
    assert audit.label == "CHECK FIRST"
    assert audit.score >= 7
    assert "bounded by verified black separators" in audit.reasons
    assert audit.version == AUDIT_VERSION


def test_source_edge_gap_remains_reviewable_but_is_not_overstated() -> None:
    capture = _capture([
        _segment(0, 30_000, "possible_missed"),
        _segment(30_000, 60_000, "play"),
    ])

    audit = audit_possible_missed(capture)[0]

    assert audit.level == REVIEW
    assert any("beginning or end" in reason for reason in audit.reasons)


def test_short_flash_and_overlong_gap_are_low_signal_not_removed() -> None:
    capture = _capture([
        _segment(0, 2_000, "separator"),
        _segment(2_000, 3_000, "possible_missed"),
        _segment(3_000, 5_000, "separator"),
        _segment(5_000, 205_000, "possible_missed"),
        _segment(205_000, 207_000, "separator"),
    ])

    audits = audit_possible_missed(capture)

    assert set(audits) == {1, 3}
    assert audits[1].level == LOW_SIGNAL
    assert audits[3].level == LOW_SIGNAL
    assert any("shorter than" in reason for reason in audits[1].reasons)
    assert any("longer than" in reason for reason in audits[3].reasons)


def test_audit_can_infer_median_without_mutating_capture() -> None:
    capture = _capture(
        [
            _segment(0, 2_000, "separator"),
            _segment(2_000, 32_000, "possible_missed"),
            _segment(32_000, 34_000, "separator"),
        ],
        median_play_ms=0,
        plays=[
            {"start_ms": 40_000, "end_ms": 68_000},
            {"start_ms": 70_000, "end_ms": 102_000},
        ],
    )
    before = copy.deepcopy(capture)

    audits = audit_possible_missed(capture)

    assert audits[1].level == CHECK_FIRST
    assert capture == before


def test_malformed_coverage_is_ignored_without_inventing_ranges() -> None:
    capture = _capture([
        {"kind": "possible_missed", "start_ms": 10_000},
        _segment(20_000, 20_000, "possible_missed"),
        _segment(30_000, 40_000, "play"),
    ])

    assert audit_possible_missed(capture) == {}
    assert audit_possible_missed({"result": {"coverage": "invalid"}}) == {}
