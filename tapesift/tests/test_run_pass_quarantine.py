from pathlib import Path

import pytest

from tapesift.research.run_pass_baseline import evaluate_feature_manifests
from tapesift.research.run_pass_features import extract_run_pass_features
from tapesift.research.run_pass_temporal_baseline import (
    evaluate_temporal_feature_manifests,
)
from tapesift.research.run_pass_temporal_features import (
    extract_run_pass_temporal_features,
)


@pytest.mark.parametrize("extractor", [
    extract_run_pass_features,
    extract_run_pass_temporal_features,
])
def test_feature_exports_refuse_quarantined_labels(tmp_path: Path, extractor):
    labels = tmp_path / "quarantine" / "suspect.jsonl"
    labels.parent.mkdir()
    labels.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="quarantined"):
        extractor(labels, tmp_path / "out.jsonl", tmp_path / "ffmpeg.exe")


@pytest.mark.parametrize("evaluate", [
    evaluate_feature_manifests,
    evaluate_temporal_feature_manifests,
])
def test_model_evaluation_refuses_quarantined_inputs(tmp_path: Path, evaluate):
    manifest = tmp_path / "quarantine" / "suspect.jsonl"
    manifest.parent.mkdir()
    manifest.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="quarantined"):
        evaluate(
            manifest,
            manifest,
            tmp_path / "report.json",
            tmp_path / "report.md",
        )
