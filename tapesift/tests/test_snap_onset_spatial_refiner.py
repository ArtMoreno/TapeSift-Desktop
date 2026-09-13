"""Focused tests for Iteration 7B.2 spatial snap-onset research."""

from __future__ import annotations

from pathlib import Path

import pytest

from tapesift.research.run_pass_features import SignalFrame
from tapesift.research.snap_onset_spatial_refiner import (
    CHANNELS,
    FIELD_REGIONS,
    SpatialAngleTrace,
    SpatialPolicy,
    build_spatial_channels,
    build_spatial_signalstats_command,
    score_spatial_policy,
    spatial_policy_grid,
)
from tapesift.research.snap_onset_refiner import RiseCandidate


def _frames(motion: float) -> list[SignalFrame]:
    return [
        SignalFrame(
            time_s=index / 8,
            yavg=50.0,
            satavg=10.0,
            ydif=motion,
            udif=0.0,
            vdif=0.0,
        )
        for index in range(40)
    ]


def test_command_crops_before_scaling() -> None:
    command = build_spatial_signalstats_command(
        "ffmpeg",
        Path("film.mp4"),
        1_000,
        5_000,
        FIELD_REGIONS[0],
    )
    filters = command[command.index("-vf") + 1]

    assert "fps=8" in filters
    assert FIELD_REGIONS[0].crop_filter in filters
    assert filters.index("crop=") < filters.index("scale=")
    assert "metadata=print:file=-" in filters


def test_spatial_channels_separate_common_and_local_motion() -> None:
    region_frames = {
        region.region_id: _frames(float(index + 1))
        for index, region in enumerate(FIELD_REGIONS)
    }

    channels = build_spatial_channels(region_frames)

    assert tuple(channels) == CHANNELS
    assert channels["field_median"][0].ydif == 3.5
    assert channels["field_upper_quartile"][0].ydif == pytest.approx(4.75)
    assert channels["localized_excess"][0].ydif == 2.5
    assert channels["field_local_blend"][0].ydif == 4.75


def test_spatial_channels_reject_missing_region() -> None:
    region_frames = {
        region.region_id: _frames(1.0)
        for region in FIELD_REGIONS[:-1]
    }

    with pytest.raises(ValueError, match="frozen field regions"):
        build_spatial_channels(region_frames)


def test_spatial_policy_scores_selected_channel() -> None:
    candidates = {
        channel: (RiseCandidate(5.0, 0.6, 2.0),)
        for channel in CHANNELS
    }
    trace = SpatialAngleTrace(
        item_id="film:clip",
        cohort_id="film",
        clip_id="clip",
        clip_number=1,
        angle=1,
        source_video_path="C:/film.mp4",
        range_start_ms=0,
        range_end_ms=10_000,
        actual_snap_ms=5_100,
        temporal_v21_onset_ms=6_000,
        candidates_by_channel=candidates,
    )
    policy = SpatialPolicy("localized_excess", 0.5, 0.12, 1.5, 0.0)

    result = score_spatial_policy([trace], policy)

    assert result["overall"]["within_500_ms"] == 1
    assert result["rows"][0]["candidate_onset_ms"] == 5_000


def test_spatial_policy_grid_is_bounded_and_deterministic() -> None:
    first = spatial_policy_grid()

    assert first == spatial_policy_grid()
    assert len(first) == 432
    assert len({policy.policy_id for policy in first}) == 432
