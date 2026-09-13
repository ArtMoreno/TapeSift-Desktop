"""Focused tests for the Iteration 4B completion report."""

from __future__ import annotations

from scripts.finalize_iteration_4b_generalization import (
    semantic_prediction,
    summarize_review,
)


def _item(
    item_id: str,
    *,
    kind: str = "play",
    decision: str = "accepted",
    edits: list[str] | None = None,
) -> dict:
    return {
        "item_id": item_id,
        "film_id": "film-a",
        "candidate_kind": kind,
        "candidate_stratum": (
            "unclassified" if kind == "unclassified" else "confident"),
        "decision": decision,
        "edit_history": edits or [],
        "original_start_ms": 100,
        "original_end_ms": 200,
        "start_ms": 100,
        "end_ms": 200,
    }


def test_semantic_prediction_ignores_only_run_metadata() -> None:
    left = {
        "film_id": "film-a",
        "generated_at": "first",
        "runtime_seconds": 10.0,
        "plays": [{"start_ms": 1, "end_ms": 2}],
    }
    right = {
        "film_id": "film-a",
        "generated_at": "second",
        "runtime_seconds": 12.0,
        "plays": [{"start_ms": 1, "end_ms": 2}],
    }

    assert semantic_prediction(left) == semantic_prediction(right)
    right["plays"][0]["end_ms"] = 3
    assert semantic_prediction(left) != semantic_prediction(right)


def test_review_summary_separates_plays_from_unclassified_windows() -> None:
    result = summarize_review([
        _item("play-1"),
        _item("play-2"),
        _item(
            "window-1",
            kind="unclassified",
            decision="revised",
            edits=["start@100"],
        ),
    ])

    assert result["reviewed"] == 3
    assert result["emitted_play_sample"]["accepted_unchanged"] == 2
    assert result["emitted_play_sample"]["revised"] == 0
    assert result["unclassified_window_sample"]["revised"] == 1
    assert result["items_with_edits"] == 1


def test_review_summary_reports_exclusions_without_hiding_them() -> None:
    result = summarize_review([
        _item("play-1", decision="excluded"),
        _item("play-2"),
    ])

    assert result["decisions"] == {"accepted": 1, "excluded": 1}
    assert result["emitted_play_sample"]["excluded"] == 1
    assert result["emitted_play_sample"]["accepted_unchanged_rate"] == 0.5


def test_review_summary_discloses_disjoint_relocation() -> None:
    relocated = _item(
        "window-1",
        kind="unclassified",
        decision="revised",
        edits=["start@300", "end@400"],
    )
    relocated["start_ms"] = 300
    relocated["end_ms"] = 400

    result = summarize_review([relocated])

    assert result["disjoint_relocations"] == [{
        "item_id": "window-1",
        "original_start_ms": 100,
        "original_end_ms": 200,
        "reviewed_start_ms": 300,
        "reviewed_end_ms": 400,
    }]
