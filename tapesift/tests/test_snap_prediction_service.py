from __future__ import annotations

from pathlib import Path

from tapesift.models.clip import Clip
from tapesift.services import snap_prediction_service as service


def test_first_angle_uses_the_first_internal_cse_start() -> None:
    assert service.first_angle_end_ms(
        10_000, 40_000, [10_000, 24_000, 33_000]) == 24_000
    assert service.first_angle_end_ms(10_000, 40_000, []) == 40_000


def test_prediction_converts_existing_onset_to_source_time(
        tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "film.mp4"
    source.write_bytes(b"video-placeholder")
    captured = {}

    def read_frames(_ffmpeg, _source, start_ms, end_ms, _cancel):
        captured["range"] = (start_ms, end_ms)
        return [object()] * 64

    monkeypatch.setattr(service, "_read_frames", read_frames)
    monkeypatch.setattr(
        service,
        "summarize_temporal_angle",
        lambda _frames: ({}, {
            "onset_seconds": 2.125,
            "onset_confidence": 0.75,
            "onset_method": "sustained_motion_rise",
            "classifier_usable": True,
            "eligibility_reasons": [],
        }),
    )

    prediction = service.predict_snap(
        "ffmpeg", source, 100_000, 130_000,
        angle_starts_ms=[100_000, 114_000],
    )

    assert captured["range"] == (100_000, 114_000)
    assert prediction["source_ms"] == 102_125
    assert prediction["angle_end_ms"] == 114_000
    assert prediction["eligible"] is True


def test_cached_prediction_rejects_changed_clip_geometry() -> None:
    clip = Clip(start_ms=10_000, end_ms=20_000)
    prediction = {
        "predictor_id": service.PREDICTOR_ID,
        "predictor_version": service.PREDICTOR_VERSION,
        "source_ms": 12_000,
        "clip_start_ms": 10_000,
        "clip_end_ms": 20_000,
    }
    clip.analysis[service.PREDICTION_KEY] = prediction
    assert service.cached_prediction(clip) is prediction

    clip.end_ms = 19_000
    assert service.cached_prediction(clip) is None
