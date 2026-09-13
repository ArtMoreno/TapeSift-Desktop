import json
from pathlib import Path

import pytest

from tapesift.research.run_pass_features import SignalFrame
from tapesift.research.run_pass_temporal_features import (
    TEMPORAL_CLASSIFIER_FEATURES,
    TEMPORAL_SAMPLE_FPS,
    analyze_temporal_frames,
    detect_motion_onset,
    extract_run_pass_temporal_features,
    resolve_angle_ranges,
)


_FPS = 8
_DURATION_SECONDS = 32.0
_LEFT_CUT_PULSE_SECONDS = 15.0
_RIGHT_CUT_PULSE_SECONDS = 15.5
_SECOND_ANGLE_START_SECONDS = 15.625


def _frame(time_s: float, motion: float) -> SignalFrame:
    """Distribute one combined-motion value across all three channels."""
    return SignalFrame(
        time_s=time_s,
        yavg=100.0,
        satavg=20.0,
        ydif=motion * 0.6,
        udif=motion * 0.25,
        vdif=motion * 0.15,
    )


def _action_motion(local_s: float, onset_s: float) -> float:
    if onset_s <= local_s < onset_s + 1.5:
        return 9.0
    if onset_s + 1.5 <= local_s < onset_s + 2.75:
        return 6.0
    if onset_s + 2.75 <= local_s < onset_s + 5.0:
        return 4.0
    return 1.0


def _two_angle_frames(
        *, scale: float = 1.0, offset: float = 0.0
) -> list[SignalFrame]:
    """Two sustained actions separated by one replay-transition signature."""
    frames: list[SignalFrame] = []
    for index in range(round(_DURATION_SECONDS * _FPS)):
        time_s = index / _FPS
        if time_s < _LEFT_CUT_PULSE_SECONDS:
            base_motion = _action_motion(time_s, onset_s=5.5)
        elif time_s >= _SECOND_ANGLE_START_SECONDS:
            base_motion = _action_motion(
                time_s - _SECOND_ANGLE_START_SECONDS,
                onset_s=5.0,
            )
        else:
            base_motion = 0.25

        if time_s in {
            _LEFT_CUT_PULSE_SECONDS,
            _RIGHT_CUT_PULSE_SECONDS,
        }:
            base_motion = 30.0

        frames.append(
            _frame(time_s, base_motion * scale + offset)
        )
    return frames


def _competing_transition_frames() -> list[SignalFrame]:
    """A trace with two distinct, equally plausible replay transitions."""
    cut_pairs = ((10.0, 10.5), (20.0, 20.5))
    frames: list[SignalFrame] = []
    for index in range(round(_DURATION_SECONDS * _FPS)):
        time_s = index / _FPS
        motion = (
            5.0
            if 4.0 <= time_s < 8.0 or 24.0 <= time_s < 28.0
            else 1.0
        )
        for left_s, right_s in cut_pairs:
            if time_s in {left_s, right_s}:
                motion = 30.0
            elif left_s < time_s < right_s:
                motion = 0.25
        frames.append(_frame(time_s, motion))
    return frames


def _single_angle_frames(
        *,
        onset_s: float,
        audible_range: tuple[float, float] | None = None,
        duration_s: float = 20.0,
) -> list[SignalFrame]:
    frames: list[SignalFrame] = []
    for index in range(round(duration_s * _FPS)):
        time_s = index / _FPS
        motion = 8.0 if time_s >= onset_s else 1.0
        if (
            audible_range is not None
            and audible_range[0] <= time_s < audible_range[1]
        ):
            motion = 8.0
        frames.append(_frame(time_s, motion))
    return frames


def test_paired_pulse_quiet_pulse_splits_at_eight_fps():
    resolution = resolve_angle_ranges(
        _two_angle_frames(),
        clip_start_ms=0,
        clip_end_ms=32_000,
    )

    assert resolution.source == "paired_transition"
    assert resolution.classifier_eligible is True
    assert resolution.transition is not None
    assert resolution.transition.left_pulse_s == 15.0
    assert resolution.transition.right_pulse_s == 15.5
    assert [(angle.start_s, angle.end_s)
            for angle in resolution.ranges] == [
        (0.0, 15.0),
        (15.625, 32.0),
    ]


def test_analyze_diagnostics_show_transition_gap_is_excluded():
    analysis = analyze_temporal_frames(
        _two_angle_frames(),
        clip_start_ms=0,
        clip_end_ms=32_000,
    )
    diagnostics = analysis["diagnostics"]

    assert diagnostics["transition"]["left_pulse_seconds"] == 15.0
    assert diagnostics["transition"]["right_pulse_seconds"] == 15.5
    assert diagnostics["angles"][0]["end_seconds"] == 15.0
    assert diagnostics["angles"][1]["start_seconds"] == 15.625
    assert (
        diagnostics["angles"][0]["end_seconds"]
        < diagnostics["angles"][1]["start_seconds"]
    )


def test_competing_paired_transitions_abstain_without_midpoint_guess():
    resolution = resolve_angle_ranges(
        _competing_transition_frames(),
        clip_start_ms=0,
        clip_end_ms=32_000,
    )

    assert resolution.transition is None
    assert resolution.source == "ambiguous_paired_transitions"
    assert resolution.classifier_eligible is False
    assert resolution.abstain_reasons == (
        "ambiguous_paired_transitions",
    )
    assert [(angle.start_s, angle.end_s)
            for angle in resolution.ranges] == [(0.0, 32.0)]


def test_motion_onset_handles_a_long_audible_before_the_snap():
    onset = detect_motion_onset(
        _single_angle_frames(onset_s=12.0)
    )

    assert onset.method == "sustained_motion_rise"
    assert onset.eligible is True
    assert onset.time_s == pytest.approx(12.0, abs=0.75)
    assert onset.confidence >= 0.45


def test_motion_onset_ignores_brief_audible_reset_before_real_snap():
    onset = detect_motion_onset(
        _single_angle_frames(
            onset_s=12.0,
            audible_range=(4.0, 5.0),
        )
    )

    assert onset.method == "sustained_motion_rise"
    assert onset.eligible is True
    assert onset.time_s == pytest.approx(12.0, abs=0.75)
    assert onset.time_s > 10.0


def test_classifier_features_are_positive_affine_invariant():
    original = analyze_temporal_frames(
        _two_angle_frames(),
        clip_start_ms=0,
        clip_end_ms=32_000,
    )
    transformed = analyze_temporal_frames(
        _two_angle_frames(scale=6.5, offset=37.0),
        clip_start_ms=0,
        clip_end_ms=32_000,
    )

    assert original["diagnostics"]["classifier_eligible"] is True
    assert transformed["diagnostics"]["classifier_eligible"] is True
    assert original["features"] == transformed["features"]


def test_each_angle_excess_share_partition_sums_to_one():
    analysis = analyze_temporal_frames(
        _two_angle_frames(),
        clip_start_ms=0,
        clip_end_ms=32_000,
    )

    for angle in analysis["diagnostics"]["angles"]:
        shares = angle["features"]
        assert sum(
            shares[name] for name in (
                "early_excess_share",
                "middle_excess_share",
                "late_excess_share",
            )
        ) == pytest.approx(1.0, abs=0.000002)


def test_exactly_two_usable_angles_emit_only_allowlisted_features():
    analysis = analyze_temporal_frames(
        _two_angle_frames(),
        clip_start_ms=0,
        clip_end_ms=32_000,
    )

    assert analysis["diagnostics"]["classifier_eligible"] is True
    assert analysis["diagnostics"]["analysis_mode"] == "two_angle"
    assert analysis["diagnostics"]["angles_detected"] == 2
    assert set(analysis["features"]) == set(
        TEMPORAL_CLASSIFIER_FEATURES
    )
    assert analysis["diagnostics"]["classifier_feature_names"] == list(
        TEMPORAL_CLASSIFIER_FEATURES
    )


def test_three_angle_cse_topology_conflict_forces_abstention():
    analysis = analyze_temporal_frames(
        _two_angle_frames(),
        clip_start_ms=100_000,
        clip_end_ms=132_000,
        angle_starts_ms=[100_000, 108_000, 122_000],
    )

    assert analysis["features"] == {}
    assert analysis["diagnostics"]["classifier_eligible"] is False
    assert analysis["diagnostics"]["analysis_mode"] == "abstain"
    assert analysis["diagnostics"]["angle_split_source"] \
        == "paired_transition_cse_topology_conflict"
    assert "cse_reports_more_than_two_angles" in (
        analysis["diagnostics"]["abstain_reasons"]
    )


def test_temporal_export_preserves_provenance_and_defaults_to_eight_fps(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "film.mp4"
    source.write_bytes(b"film")
    labels = tmp_path / "labels.jsonl"
    labels.write_text(json.dumps({
        "clip_id": "clip-1",
        "clip_number": 7,
        "project_name": "Test",
        "source_project": "test.tapesift",
        "source_video_path": str(source),
        "start_ms": 1_000,
        "end_ms": 33_000,
        "angle_starts_ms": [1_000, 16_250],
        "label": "run",
        "label_display": "Run",
        "play_type": "Run",
        "play_action": "RPO Run",
        "trainable": True,
        "research_cohort_id": "test-cohort",
    }) + "\n", encoding="utf-8")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffmpeg.write_bytes(b"exe")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "tapesift.research.run_pass_temporal_features."
        "ffmpeg_service.get_version",
        lambda _path: "ffmpeg test",
    )

    def fake_measure(
            _ffmpeg, _source, _start, _end, **kwargs):
        captured.update(kwargs)
        return {
            "features": {
                name: 0.2 for name in TEMPORAL_CLASSIFIER_FEATURES
            },
            "diagnostics": {
                "classifier_eligible": True,
                "analysis_mode": "two_angle",
                "abstain_reasons": [],
                "angle_split_source": "paired_transition_cse_agree",
                "angles": [],
            },
        }

    output = tmp_path / "temporal.jsonl"
    summary = extract_run_pass_temporal_features(
        labels,
        output,
        ffmpeg,
        measure=fake_measure,
    )
    record = json.loads(output.read_text(encoding="utf-8"))

    assert captured["sample_fps"] == TEMPORAL_SAMPLE_FPS == 8.0
    assert captured["angle_starts_ms"] == [1_000, 16_250]
    assert summary["sample_fps"] == 8.0
    assert summary["classifier_eligible_records"] == 1
    assert summary["classifier_coverage"] == 1.0
    assert summary["angle_split_sources"] == {
        "paired_transition_cse_agree": 1,
    }
    assert record["research_cohort_id"] == "test-cohort"
    assert record["source_project"] == "test.tapesift"
    assert record["play_type"] == "Run"
    assert record["play_action"] == "RPO Run"
    assert record["feature_extractor"]["sample_fps"] == 8.0
    assert record["feature_extractor"]["ffmpeg_version"] == "ffmpeg test"
    assert record["classifier_eligible"] is True
    assert set(record["features"]) == set(
        TEMPORAL_CLASSIFIER_FEATURES
    )
