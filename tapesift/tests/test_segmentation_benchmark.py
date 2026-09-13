"""Offline segmentation benchmark metrics."""

from __future__ import annotations

import pytest

from tapesift.research.segmentation_benchmark import (
    discover_films, find_proxy_for_source, relationship_failures,
    score_segments, segment_iou,
)


def segment(start: int, end: int) -> dict[str, int]:
    return {"start_ms": start, "end_ms": end}


def test_segment_iou():
    assert segment_iou(segment(0, 10_000), segment(5_000, 15_000)) == pytest.approx(
        1 / 3)


def test_perfect_segmentation_scores_one():
    truth = [segment(0, 10_000), segment(20_000, 30_000)]
    metrics = score_segments(truth, list(truth))

    assert metrics["f1"] == 1.0
    assert metrics["count_error"] == 0
    assert metrics["merge_count"] == 0
    assert metrics["split_count"] == 0
    assert metrics["median_start_error_ms"] == 0
    assert metrics["median_end_error_ms"] == 0


def test_one_prediction_spanning_two_plays_is_a_merge():
    truth = [segment(0, 10_000), segment(12_000, 22_000)]
    predictions = [segment(0, 22_000)]
    merges, splits = relationship_failures(truth, predictions)

    assert len(merges) == 1
    assert splits == []


def test_two_predictions_inside_one_play_are_a_split():
    truth = [segment(0, 22_000)]
    predictions = [segment(0, 10_000), segment(12_000, 22_000)]
    merges, splits = relationship_failures(truth, predictions)

    assert merges == []
    assert len(splits) == 1


def test_boundary_metrics_use_only_one_to_one_matches():
    truth = [segment(0, 10_000), segment(20_000, 30_000)]
    predictions = [segment(100, 10_200), segment(19_800, 29_700)]
    metrics = score_segments(truth, predictions)

    assert metrics["matched_count"] == 2
    assert metrics["median_start_error_ms"] == 150
    assert metrics["median_end_error_ms"] == 250


def test_find_proxy_for_source_uses_matching_completed_proxy(tmp_path):
    source = tmp_path / "Game Film.mp4"
    source.write_bytes(b"source")
    proxy_root = tmp_path / "outputs"
    proxy = proxy_root / "Proxies" / "Game Film_123.preview.mp4"
    proxy.parent.mkdir(parents=True)
    proxy.write_bytes(b"proxy")
    (proxy.parent / "Other Film_123.preview.mp4").write_bytes(b"other")
    (proxy.parent / "Game Film_123.preview.part.mp4").write_bytes(b"part")

    assert find_proxy_for_source(source, proxy_root) == proxy


def test_discover_adds_films_without_replacing_seed_entries(tmp_path):
    source_root = tmp_path / "all22"
    source_root.mkdir()
    source = source_root / "Home O vs Away D.mp4"
    source.write_bytes(b"source")
    manifest_path = tmp_path / "manifest.local.json"
    manifest_path.write_text(
        """
        {
          "schema_version": "1.0",
          "films": [{
            "film_id": "curated_id",
            "source_file": "%s",
            "analysis_source": "%s",
            "ground_truth_file": "ground_truth/curated.jsonl",
            "prediction_file": "predictions/curated.json",
            "report_file": "reports/curated.json"
          }]
        }
        """ % (str(source).replace("\\", "\\\\"),
               str(source).replace("\\", "\\\\")),
        encoding="utf-8",
    )

    result = discover_films(manifest_path, source_root, tmp_path / "outputs")

    assert result["films_found"] == 1
    assert result["films_added"] == 0
    assert result["manifest_films"] == 1
