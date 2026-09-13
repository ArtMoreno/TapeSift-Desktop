from __future__ import annotations

from fractions import Fraction
import json

import pytest

from tapesift.models.presentation_track import (
    PresentationEvent,
    PresentationEventKind,
    PresentationEventTrack,
    SourcePositionTransition,
)
from tapesift.services.presentation_replay import (
    PresentationReplay,
    PresentationReplayError,
)


RATE = 48_000


def _source(
        frame: int, sequence: int, position: int,
        transition: SourcePositionTransition | None = None,
) -> PresentationEvent:
    payload = {"source_position_ms": position}
    if transition is not None:
        payload["transition"] = transition.value
    return PresentationEvent.create(
        audio_frame=frame,
        sequence=sequence,
        kind=PresentationEventKind.SOURCE_POSITION,
        payload=payload,
    )


def _marks(frame: int, sequence: int, marks: list[dict]) -> PresentationEvent:
    return PresentationEvent.create(
        audio_frame=frame,
        sequence=sequence,
        kind=PresentationEventKind.TELESTRATION_SNAPSHOT,
        payload={"marks": marks},
    )


def _mark(mark_id: str = "arrow-1") -> dict:
    return {
        "id": mark_id,
        "kind": "arrow",
        "ink": "gold",
        "width": 1.0,
        "points": [[0.1, 0.2], [0.8, 0.7]],
    }


def _replay(*events: PresentationEvent, frames: int = RATE) -> PresentationReplay:
    return PresentationReplay(
        PresentationEventTrack(sample_rate=RATE, events=events),
        voiceover_frame_count=frames,
    )


def test_replay_requires_canonical_clock_duration_and_frame_zero_source():
    with pytest.raises(PresentationReplayError, match="48 kHz"):
        PresentationReplay(
            PresentationEventTrack(sample_rate=44_100, events=()),
            voiceover_frame_count=44_100,
        )
    with pytest.raises(PresentationReplayError, match="positive"):
        PresentationReplay(
            PresentationEventTrack(sample_rate=RATE, events=()),
            voiceover_frame_count=0,
        )
    with pytest.raises(PresentationReplayError, match="audio frame 0"):
        _replay(_source(1, 0, 500))


def test_output_frame_clock_is_exact_for_integer_and_ntsc_rates():
    replay = _replay(_source(0, 0, 1_000), frames=RATE)
    assert replay.output_frame_count == 30
    assert replay.audio_frame_for_index(0) == 0
    assert replay.audio_frame_for_index(29) == 46_400

    ntsc = PresentationReplay(
        PresentationEventTrack(
            sample_rate=RATE,
            events=(_source(0, 0, 1_000),),
        ),
        voiceover_frame_count=RATE,
        fps=Fraction(30_000, 1_001),
    )
    assert ntsc.output_frame_count == 30
    assert ntsc.audio_frame_for_index(1) == 1_601


@pytest.mark.parametrize("rate", (1, 2, 4, 8))
@pytest.mark.parametrize("direction", (-1, 1))
def test_legacy_shuttle_samples_are_stepwise_in_both_directions(
        rate: int, direction: int):
    start = 10_000
    delta = rate * 100 * direction
    replay = _replay(
        _source(0, 0, start),
        _source(4_800, 1, start + delta),
    )
    assert replay.source_position_at(2_400) == start
    assert replay.source_position_at(4_800) == start + delta


def test_legacy_position_notification_jitter_never_invents_a_frame():
    replay = _replay(
        _source(0, 0, 10_000),
        _source(4_800, 1, 10_810),
    )
    assert replay.source_position_at(2_400) == 10_000
    assert replay.source_position_at(4_800) == 10_810


def test_explicit_continuous_delivery_holds_until_the_next_painted_pts():
    replay = _replay(
        _source(0, 0, 1_000, SourcePositionTransition.ANCHOR),
        _source(4_800, 1, 1_500, SourcePositionTransition.CONTINUOUS),
    )
    assert replay.source_position_at(2_400) == 1_000
    assert replay.source_position_at(4_799) == 1_000
    assert replay.source_position_at(4_800) == 1_500


def test_explicit_reverse_jkl_delivery_is_stepwise_not_inferred():
    replay = _replay(
        _source(0, 0, 10_000, SourcePositionTransition.ANCHOR),
        _source(4_800, 1, 9_600, SourcePositionTransition.CONTINUOUS),
        _source(9_600, 2, 9_200, SourcePositionTransition.CONTINUOUS),
    )
    assert replay.source_position_at(2_400) == 10_000
    assert replay.source_position_at(7_200) == 9_600
    assert replay.source_position_at(9_600) == 9_200


def test_explicit_small_hard_seek_never_interpolates():
    replay = _replay(
        _source(0, 0, 1_000, SourcePositionTransition.ANCHOR),
        _source(4_800, 1, 1_100, SourcePositionTransition.HARD_SEEK),
        _source(9_600, 2, 1_200, SourcePositionTransition.CONTINUOUS),
    )
    assert replay.source_position_at(2_400) == 1_000
    assert replay.source_position_at(4_800) == 1_100
    assert replay.source_position_at(7_200) == 1_100
    assert replay.source_position_at(9_600) == 1_200


def test_legacy_nine_x_delta_never_invents_an_intermediate_frame():
    replay = _replay(
        _source(0, 0, 10_000),
        _source(4_800, 1, 10_900),
    )
    assert replay.source_position_at(4_799) == 10_000
    assert replay.source_position_at(4_800) == 10_900


def test_legacy_large_discontinuity_switches_only_at_delivery():
    replay = _replay(
        _source(0, 0, 1_000),
        _source(4_800, 1, 20_000),
    )
    assert replay.source_position_at(4_799) == 1_000
    assert replay.source_position_at(4_800) == 20_000


def test_legacy_stale_gap_holds_the_last_delivered_frame():
    replay = _replay(
        _source(0, 0, 1_000),
        _source(RATE, 1, 2_000),
    )
    assert replay.source_position_at(RATE - 1) == 1_000
    assert replay.source_position_at(RATE) == 2_000


def test_duplicate_position_events_use_last_sequence_at_same_frame():
    replay = _replay(
        _source(0, 0, 100),
        _source(0, 1, 200),
        _source(4_800, 2, 300),
    )
    assert replay.source_position_at(0) == 200
    assert replay.source_position_at(2_400) == 200


def test_latest_complete_telestration_snapshot_is_stepwise_and_canonical():
    first = _mark("first")
    second = _mark("second")
    replay = _replay(
        _source(0, 0, 100),
        _marks(0, 1, [first]),
        _marks(2_400, 2, [first, second]),
        _marks(4_800, 3, []),
    )
    assert json.loads(replay.marks_at(0)) == [first]
    assert json.loads(replay.marks_at(2_399)) == [first]
    assert json.loads(replay.marks_at(2_400)) == [first, second]
    assert replay.marks_at(4_800) == b"[]"


def test_missing_initial_telestration_is_empty_for_legacy_tracks():
    replay = _replay(
        _source(0, 0, 100),
        _marks(2_400, 1, [_mark()]),
    )
    assert replay.marks_at(0) == b"[]"


def test_frame_states_carry_source_marks_and_audio_timeline():
    replay = _replay(
        _source(0, 0, 1_000),
        _marks(0, 1, [_mark()]),
        frames=4_800,
    )
    states = list(replay.frames())
    assert len(states) == 3
    assert [state.index for state in states] == [0, 1, 2]
    assert [state.audio_frame for state in states] == [0, 1_600, 3_200]
    assert all(state.source_position_ms == 1_000 for state in states)
    assert all(json.loads(state.marks_json) == [_mark()] for state in states)


def test_track_cannot_extend_beyond_audio_and_queries_are_bounded():
    track = PresentationEventTrack(
        sample_rate=RATE,
        events=(_source(0, 0, 100), _source(4_801, 1, 200)),
    )
    with pytest.raises(PresentationReplayError, match="beyond"):
        PresentationReplay(track, voiceover_frame_count=4_800)

    replay = _replay(_source(0, 0, 100), frames=4_800)
    with pytest.raises(IndexError):
        replay.frame(3)
    with pytest.raises(PresentationReplayError, match="outside"):
        replay.source_position_at(4_801)


def test_json_boundary_rejects_mutable_non_utf8_and_invalid_tracks():
    with pytest.raises(PresentationReplayError, match="immutable"):
        PresentationReplay.from_json_bytes(  # type: ignore[arg-type]
            "{}", voiceover_frame_count=RATE
        )
    with pytest.raises(PresentationReplayError, match="invalid"):
        PresentationReplay.from_json_bytes(
            b"\xff", voiceover_frame_count=RATE
        )
    with pytest.raises(PresentationReplayError, match="invalid"):
        PresentationReplay.from_json_bytes(
            b"{}", voiceover_frame_count=RATE
        )


def test_canonical_json_roundtrip_constructs_identical_replay():
    track = PresentationEventTrack(
        sample_rate=RATE,
        events=(
            _source(0, 0, 1_000),
            _marks(0, 1, [_mark()]),
            _source(4_800, 2, 1_100),
        ),
    )
    replay = PresentationReplay.from_json_bytes(
        track.to_json().encode("utf-8"),
        voiceover_frame_count=RATE,
    )
    assert replay.source_position_at(2_400) == 1_000
    assert replay.source_position_at(4_800) == 1_100
    assert json.loads(replay.marks_at(0)) == [_mark()]
