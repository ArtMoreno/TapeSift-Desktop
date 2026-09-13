"""Tests for the research-only segmentation verification queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.run_segmentation_verifier import parse_selection, parse_strata
from tapesift.research.segmentation_verification import (
    VerificationItem,
    VerificationSession,
    _blind_unclassified_windows,
    _even_time_sample,
    _select_stratified_candidates,
    build_pair_proposal_items,
    build_pilot_items,
)


def _item(index: int = 0, *, film_id: str = "film-a") -> VerificationItem:
    start_ms = 1_000 + index * 10_000
    return VerificationItem(
        item_id=f"{film_id}:play:{index:04d}",
        film_id=film_id,
        film_name=f"Film {film_id}",
        source_file=f"C:/{film_id}.mp4",
        analysis_source=f"C:/{film_id}-proxy.mp4",
        frame_rate=30.0,
        film_duration_ms=120_000,
        start_ms=start_ms,
        end_ms=start_ms + 8_000,
        original_start_ms=start_ms,
        original_end_ms=start_ms + 8_000,
        candidate_kind="play",
        candidate_index=index,
        prediction_indices=[index],
    )


def _session(tmp_path: Path, items: list[VerificationItem]
             ) -> VerificationSession:
    return VerificationSession.create(
        tmp_path / "pilot.state.json",
        tmp_path / "pilot.verified.jsonl",
        "pilot",
        items,
    )


def _truth(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_accept_autosaves_truth_and_advances(tmp_path: Path) -> None:
    session = _session(tmp_path, [_item(0), _item(1)])

    session.accept()

    assert session.cursor == 1
    assert session.items[0].status == "verified"
    assert session.items[0].decision == "accepted"
    assert _truth(session.ground_truth_path)[0]["item_id"] \
        == "film-a:play:0000"
    loaded = VerificationSession.load(session.state_path)
    assert loaded.cursor == 1
    assert loaded.items[0].status == "verified"


def test_split_and_undo_restore_original_queue(tmp_path: Path) -> None:
    session = _session(tmp_path, [_item()])

    session.split(5_000)

    assert len(session.items) == 2
    assert (session.items[0].start_ms, session.items[0].end_ms) \
        == (1_000, 5_000)
    assert (session.items[1].start_ms, session.items[1].end_ms) \
        == (5_000, 9_000)
    assert session.undo() == "split"
    assert len(session.items) == 1
    assert session.current.item_id == "film-a:play:0000"


def test_split_normalizes_pair_angle_metadata_for_each_child(
        tmp_path: Path) -> None:
    item = _item()
    item.start_ms = 0
    item.end_ms = 30_000
    item.original_start_ms = 0
    item.original_end_ms = 30_000
    item.angle_starts_ms = [0, 15_000]
    item.angle_count = 2
    session = _session(tmp_path, [item])

    session.split(15_000)

    left, right = session.items
    assert (left.start_ms, left.end_ms) == (0, 15_000)
    assert left.angle_starts_ms == [0]
    assert left.angle_count == 1
    assert (right.start_ms, right.end_ms) == (15_000, 30_000)
    assert right.angle_starts_ms == [15_000]
    assert right.angle_count == 1
    assert all(
        child.start_ms <= angle_start < child.end_ms
        for child in (left, right)
        for angle_start in child.angle_starts_ms
    )


def test_boundary_edits_normalize_pair_angle_metadata(
        tmp_path: Path) -> None:
    item = _item()
    item.start_ms = 0
    item.end_ms = 30_000
    item.original_start_ms = 0
    item.original_end_ms = 30_000
    item.angle_starts_ms = [0, 15_000]
    item.angle_count = 2
    session = _session(tmp_path, [item])

    session.set_start(16_000)

    assert session.current.angle_starts_ms == [16_000]
    assert session.current.angle_count == 1

    session.undo()
    session.set_end(14_000)

    assert session.current.angle_starts_ms == [0]
    assert session.current.angle_count == 1


def test_merge_requires_same_film_and_combines_ranges(tmp_path: Path) -> None:
    session = _session(tmp_path, [_item(0), _item(1)])
    session.set_cursor(1)

    session.merge(-1)

    assert len(session.items) == 1
    assert session.current.start_ms == 1_000
    assert session.current.end_ms == 19_000
    assert session.current.status == "pending"
    assert "merged:" in session.current.edit_history[-1]
    assert session.current.angle_starts_ms == [1_000, 11_000]
    assert session.current.angle_count == 2

    mixed = _session(tmp_path / "mixed", [_item(0), _item(1, film_id="b")])
    with pytest.raises(ValueError, match="different films"):
        mixed.merge(1)

    distant = _session(tmp_path / "distant", [_item(0), _item(4)])
    with pytest.raises(ValueError, match="neighboring predictions"):
        distant.merge(1)


def test_merge_availability_uses_the_same_preflight_rules(
        tmp_path: Path) -> None:
    session = _session(tmp_path, [_item(0), _item(1)])

    assert session.can_merge(-1) is False
    assert session.merge_error(-1) == "There is no adjacent segment to merge"
    assert session.can_merge(1) is True
    assert session.merge_error(1) is None

    session.set_cursor(1)
    assert session.can_merge(-1) is True
    assert session.can_merge(1) is False

    exact_limit = [_item(0), _item(1)]
    exact_limit[1].start_ms = exact_limit[0].end_ms + 5_000
    exact_limit[1].end_ms = exact_limit[1].start_ms + 8_000
    exact_session = _session(tmp_path / "exact", exact_limit)
    assert exact_session.can_merge(1) is True

    over_limit = [_item(0), _item(1)]
    over_limit[1].start_ms = over_limit[0].end_ms + 5_001
    over_limit[1].end_ms = over_limit[1].start_ms + 8_000
    over_session = _session(tmp_path / "over", over_limit)
    assert over_session.can_merge(1) is False
    assert "too far away" in (over_session.merge_error(1) or "")


def test_boundary_edits_and_exclude_are_undoable(tmp_path: Path) -> None:
    session = _session(tmp_path, [_item()])

    session.set_start(1_500)
    session.set_end(8_500)
    session.exclude()

    assert session.current.status == "excluded"
    assert _truth(session.ground_truth_path)[0]["decision"] == "excluded"
    assert session.undo() == "exclude"
    assert session.current.status == "pending"
    assert session.undo() == "set end"
    assert session.current.end_ms == 9_000
    assert session.undo() == "set start"
    assert session.current.start_ms == 1_000


def test_build_pilot_balances_films_and_difficult_candidates(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    predictions = tmp_path / "predictions"
    predictions.mkdir()
    films = []
    for film_number in range(2):
        film_id = f"film-{film_number}"
        prediction_name = f"{film_id}.json"
        plays = [
            {
                "start_ms": index * 10_000,
                "end_ms": index * 10_000 + 8_000,
                "needs_review": index < 4,
                "review_reason": "weak signal" if index < 4 else "",
                "angle_count": 1,
                "angle_starts": [],
            }
            for index in range(12)
        ]
        prediction = {
            "plays": plays,
            "unclassified": [
                {
                    "start_ms": 130_000,
                    "end_ms": 138_000,
                    "reason": "unclassified",
                    "angle_count": 1,
                    "split_points_ms": [],
                }
            ],
            "summary": {"signal": "scene"},
        }
        (predictions / prediction_name).write_text(
            json.dumps(prediction), encoding="utf-8")
        source = tmp_path / f"{film_id}.mp4"
        source.touch()
        films.append({
            "film_id": film_id,
            "source_file": str(source),
            "analysis_source": str(source),
            "prediction_file": f"predictions/{prediction_name}",
        })
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "films": films,
    }), encoding="utf-8")
    monkeypatch.setattr(
        "tapesift.research.segmentation_verification.probe_video",
        lambda *_args: SimpleNamespace(frame_rate=60.0, duration_ms=180_000),
    )

    items = build_pilot_items(
        manifest, [("film-0", 8), ("film-1", 8)], Path("ffprobe"))

    assert len(items) == 16
    assert sum(item.film_id == "film-0" for item in items) == 8
    assert sum(item.film_id == "film-1" for item in items) == 8
    assert sum(item.candidate_kind == "unclassified" for item in items) == 2
    assert sum(item.detector_needs_review for item in items) >= 8


def test_build_named_queue_honors_explicit_strata(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    predictions = tmp_path / "predictions"
    predictions.mkdir()
    source = tmp_path / "holdout.mp4"
    source.touch()
    plays = []
    for index in range(29):
        if index < 20:
            needs_review = False
            reason = ""
        elif index < 25:
            needs_review = True
            reason = "recovered using weak scene cuts"
        else:
            needs_review = True
            reason = "rough split needs checking"
        plays.append({
            "start_ms": index * 10_000,
            "end_ms": index * 10_000 + 8_000,
            "needs_review": needs_review,
            "review_reason": reason,
            "angle_count": 1,
            "angle_starts": [index * 10_000],
        })
    prediction = {
        "plays": plays,
        "unclassified": [
            {
                "start_ms": (30 + index) * 10_000,
                "end_ms": (30 + index) * 10_000 + 8_000,
                "reason": "unclassified",
                "angle_count": 1,
                "split_points_ms": [],
            }
            for index in range(4)
        ],
        "summary": {"signal": "scene"},
    }
    (predictions / "holdout.json").write_text(
        json.dumps(prediction), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "films": [{
            "film_id": "holdout",
            "source_file": str(source),
            "analysis_source": str(source),
            "prediction_file": "predictions/holdout.json",
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(
        "tapesift.research.segmentation_verification.probe_video",
        lambda *_args: SimpleNamespace(frame_rate=60.0, duration_ms=400_000),
    )

    items = build_pilot_items(
        manifest,
        [("holdout", 25)],
        Path("ffprobe"),
        strata={
            "confident": 12,
            "weak_recovered": 5,
            "other_review": 4,
            "unclassified": 4,
        },
    )

    counts = {
        name: sum(item.candidate_stratum == name for item in items)
        for name in (
            "confident", "weak_recovered", "other_review", "unclassified")
    }
    assert len(items) == 25
    assert counts == {
        "confident": 12,
        "weak_recovered": 5,
        "other_review": 4,
        "unclassified": 4,
    }


def test_build_named_queue_exposes_scene_angle_pair_stratum(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "holdout.mp4"
    source.touch()
    reason = (
        "paired across a short in-play card in scene-only film; "
        "check the boundaries")
    prediction = {
        "plays": [
            {
                "start_ms": index * 20_000,
                "end_ms": index * 20_000 + 16_000,
                "needs_review": True,
                "review_reason": reason,
                "angle_count": 2,
                "angle_starts": [
                    index * 20_000,
                    index * 20_000 + 8_000,
                ],
            }
            for index in range(5)
        ],
        "unclassified": [],
        "summary": {"signal": "scene"},
    }
    prediction_path = tmp_path / "prediction.json"
    prediction_path.write_text(json.dumps(prediction), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "films": [{
            "film_id": "holdout",
            "source_file": str(source),
            "analysis_source": str(source),
            "prediction_file": str(prediction_path),
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(
        "tapesift.research.segmentation_verification.probe_video",
        lambda *_args: SimpleNamespace(
            frame_rate=60.0, duration_ms=100_000),
    )

    items = build_pilot_items(
        manifest,
        [("holdout", 5)],
        Path("ffprobe"),
        strata={
            "confident": 0,
            "weak_recovered": 0,
            "other_review": 0,
            "unclassified": 0,
            "scene_angle_pair": 5,
        },
    )

    assert len(items) == 5
    assert all(item.candidate_kind == "scene_angle_pair" for item in items)
    assert all(item.candidate_stratum == "scene_angle_pair" for item in items)


def test_build_named_queue_expands_long_unclassified_detector_range(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "holdout.mp4"
    source.touch()
    prediction_path = tmp_path / "prediction.json"
    prediction_path.write_text(json.dumps({
        "plays": [],
        "unclassified": [{
            "start_ms": 0,
            "end_ms": 600_000,
            "reason": "whole film needs review",
            "angle_count": 7,
            "split_points_ms": [
                20_000, 40_000, 60_000, 80_000, 100_000, 120_000,
            ],
        }],
        "summary": {"signal": "mixed"},
    }), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "films": [{
            "film_id": "holdout",
            "source_file": str(source),
            "analysis_source": str(source),
            "prediction_file": str(prediction_path),
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(
        "tapesift.research.segmentation_verification.probe_video",
        lambda *_args: SimpleNamespace(
            frame_rate=60.0, duration_ms=600_000),
    )

    items = build_pilot_items(
        manifest,
        [("holdout", 10)],
        Path("ffprobe"),
        strata={
            "confident": 0,
            "weak_recovered": 0,
            "other_review": 0,
            "unclassified": 10,
            "scene_angle_pair": 0,
        },
    )

    assert len(items) == 10
    assert all(item.candidate_kind == "unclassified" for item in items)
    assert all(item.duration_ms <= 90_000 for item in items)
    assert items[0].start_ms <= 60_000
    assert items[-1].end_ms >= 540_000


@pytest.mark.parametrize(
    "candidate_kind",
    ["scene_angle_pair", "black_gap_pair"],
)
def test_build_pair_queue_blindly_samples_exact_review_proposals(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        candidate_kind: str) -> None:
    source = tmp_path / "virginia.mp4"
    source.touch()
    prediction_path = tmp_path / "iteration3a.json"
    baseline_path = tmp_path / "baseline.json"
    reference_truth = tmp_path / "reference.jsonl"
    reason = (
        "paired across a short in-play card in scene-only film; "
        "check the boundaries")
    plays = []
    baseline_plays = []
    for index in range(63):
        start_ms = index * 40_000
        angle_two_ms = start_ms + 15_000
        end_ms = start_ms + 30_000
        plays.append({
            "start_ms": start_ms,
            "end_ms": end_ms,
            "needs_review": True,
            "review_reason": reason,
            "angle_count": 2,
            "angle_starts": [start_ms, angle_two_ms],
        })
        baseline_plays.extend([
            {"start_ms": start_ms, "end_ms": angle_two_ms - 500},
            {"start_ms": angle_two_ms, "end_ms": end_ms},
        ])
    prediction_path.write_text(json.dumps({
        "film_id": "virginia",
        "duration_ms": 2_510_000,
        "plays": plays,
        "summary": {
            "signal": "scene",
            "profile": {"max_play_ms": 60_000},
        },
    }), encoding="utf-8")
    baseline_path.write_text(
        json.dumps({"plays": baseline_plays}), encoding="utf-8")
    reference_truth.write_text(
        '{"film_id":"virginia","start_ms":0,"end_ms":30000}\n',
        encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "films": [{
            "film_id": "virginia",
            "source_file": str(source),
            "analysis_source": str(source),
            "prediction_file": str(prediction_path),
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(
        "tapesift.research.segmentation_verification.probe_video",
        lambda *_args: SimpleNamespace(
            frame_rate=29.97, duration_ms=2_510_000),
    )

    items, sidecar = build_pair_proposal_items(
        manifest,
        "virginia",
        prediction_path,
        Path("ffprobe"),
        count=25,
        review_reason=reason,
        baseline_prediction_path=baseline_path,
        reference_truth_path=reference_truth,
        candidate_kind=candidate_kind,
    )

    expected_indexes = sorted({
        round(index * 62 / 24) for index in range(25)
    })
    assert len(items) == 25
    assert [item.candidate_index for item in items] == expected_indexes
    assert all(item.candidate_kind == candidate_kind for item in items)
    assert all(item.candidate_stratum == "paired" for item in items)
    assert all(item.detector_needs_review for item in items)
    assert all(item.angle_count == 2 for item in items)
    assert sidecar["proposal_pool_count"] == 63
    assert sidecar["selected_count"] == 25
    assert sidecar["candidate_kind"] == candidate_kind
    assert sidecar["selection_strategy"].endswith("no truth consulted")
    assert sidecar["prediction_sha256"]
    assert sidecar["reference_truth_sha256"]
    assert all(
        len(selected["baseline_component_indices"]) == 2
        for selected in sidecar["selected"]
    )


def test_build_pair_queue_rejects_too_few_exact_proposals(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "film.mp4"
    source.touch()
    prediction_path = tmp_path / "prediction.json"
    prediction_path.write_text(json.dumps({
        "film_id": "film",
        "duration_ms": 100_000,
        "plays": [{
            "start_ms": 0,
            "end_ms": 30_000,
            "needs_review": True,
            "review_reason": "different reason",
            "angle_count": 2,
            "angle_starts": [0, 15_000],
        }],
        "summary": {
            "signal": "scene",
            "profile": {"max_play_ms": 60_000},
        },
    }), encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "schema_version": "1.0",
        "films": [{
            "film_id": "film",
            "source_file": str(source),
            "prediction_file": str(prediction_path),
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(
        "tapesift.research.segmentation_verification.probe_video",
        lambda *_args: SimpleNamespace(
            frame_rate=30.0, duration_ms=100_000),
    )

    with pytest.raises(ValueError, match="valid pair proposals"):
        build_pair_proposal_items(
            manifest,
            "film",
            prediction_path,
            Path("ffprobe"),
            count=1,
            review_reason="required reason",
        )


def test_stratified_selection_backfills_missing_bucket() -> None:
    candidates = [
        {
            "start_ms": index,
            "candidate_stratum": "confident",
        }
        for index in range(10)
    ]

    selected = _select_stratified_candidates(
        candidates,
        6,
        {
            "confident": 3,
            "recovered": 3,
            "review": 0,
            "unclassified": 0,
        },
    )

    assert len(selected) == 6
    assert all(item["candidate_stratum"] == "confident"
               for item in selected)


def test_stratified_selection_accepts_legacy_quota_names() -> None:
    candidates = [
        {
            "start_ms": index,
            "end_ms": index + 1,
            "candidate_kind": "play",
            "candidate_stratum": (
                "weak_recovered" if index < 2 else "other_review"),
        }
        for index in range(4)
    ]

    selected = _select_stratified_candidates(
        candidates,
        4,
        {
            "confident": 0,
            "recovered": 2,
            "review": 2,
            "unclassified": 0,
        },
    )

    assert [item["candidate_stratum"] for item in selected] == [
        "weak_recovered",
        "weak_recovered",
        "other_review",
        "other_review",
    ]


def test_blind_unclassified_windows_cover_and_bound_the_range() -> None:
    windows = _blind_unclassified_windows({
        "start_ms": 0,
        "end_ms": 250_000,
        "split_points_ms": [10_000, 20_000],
    })

    assert windows[:2] == [(0, 10_000), (10_000, 20_000)]
    assert windows[0][0] == 0
    assert windows[-1][1] == 250_000
    assert all(left[1] == right[0]
               for left, right in zip(windows, windows[1:]))
    assert all(0 < end - start <= 90_000 for start, end in windows)


def test_even_time_sample_handles_uneven_candidate_density() -> None:
    candidates = [
        {
            "start_ms": index * 1_000,
            "end_ms": index * 1_000 + 500,
            "candidate_kind": "unclassified",
        }
        for index in range(20)
    ]
    candidates.extend([
        {
            "start_ms": 80_000,
            "end_ms": 90_000,
            "candidate_kind": "unclassified",
        },
        {
            "start_ms": 90_000,
            "end_ms": 100_000,
            "candidate_kind": "unclassified",
        },
    ])

    selected = _even_time_sample(candidates, 5)

    assert len(selected) == 5
    assert selected[0]["start_ms"] < 20_000
    assert any(item["start_ms"] == 80_000 for item in selected)
    assert any(item["start_ms"] == 90_000 for item in selected)


def test_stratified_selection_rejects_invalid_quota() -> None:
    with pytest.raises(ValueError, match="Unknown verification strata"):
        _select_stratified_candidates(
            [], 1, {"confident": 0, "mystery": 1})
    with pytest.raises(ValueError, match="strata total"):
        _select_stratified_candidates(
            [], 2, {"confident": 1})


def test_old_verification_item_loads_without_stratum() -> None:
    payload = _item().__dict__.copy()
    payload.pop("candidate_stratum")

    loaded = VerificationItem.from_dict(payload)

    assert loaded.candidate_stratum == ""


def test_named_queue_cli_parsers() -> None:
    assert parse_selection("film-id=25") == ("film-id", 25)
    assert parse_strata(
        "confident=12,recovered=5,review=4,unclassified=4"
    ) == {
        "confident": 12,
        "recovered": 5,
        "review": 4,
        "unclassified": 4,
    }

    with pytest.raises(argparse.ArgumentTypeError):
        parse_selection("film-id=0")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_selection("missing-count")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_strata("confident=twelve")
    with pytest.raises(argparse.ArgumentTypeError):
        parse_strata("confident=1,confident=2")
