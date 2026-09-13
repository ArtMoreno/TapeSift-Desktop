"""Play detection: span grouping and boundary-signal selection.

The structures here are taken from real All-22 film measurements:
  defense cut: [wide ~15s][black][tight ~14s][black][separator ~1.7s]
  offense cut: [play ~23s][card ~3s][play ~23s]
"""

import threading

import pytest

from tapesift.core.exceptions import TapeSiftError
import tapesift.services.play_detect_service as detector
from tapesift.services.play_detect_service import (
    DetectedPlay, DetectionResult, FilmProfile, UnclassifiedSegment,
    classify_runs, detect_plays,
    _parse_scene_events, _recover_scene_review_segments,
    _try_weak_scene_recovery, find_merged_plays,
    group_spans_into_plays, spans_from_boundaries,
)
from tapesift.workers.play_detect_worker import PlayDetectWorker


def _black_pair_shape(
    *,
    complete_pairs: int | None = None,
    gap_overrides: dict[int, int] | None = None,
    invalid_pair_index: int | None = None,
) -> dict:
    """A compact South-Carolina-shaped mixed-signal detector result.

    Each ordinary play is two content spans separated by a 250ms black gap.
    A 2.5s black gap separates plays.  The final pair has a frozen second
    component and must remain one unclassified range.
    """
    if complete_pairs is None:
        complete_pairs = detector.BLACK_PAIR_FALLBACK_MIN_PAIRS
    gap_overrides = gap_overrides or {}
    short_ms = 250
    long_ms = 2_500
    assert detector.BLACK_PAIR_SHORT_MIN_MS <= short_ms \
        <= detector.BLACK_PAIR_SHORT_MAX_MS
    assert detector.BLACK_PAIR_LONG_MIN_MS <= long_ms \
        <= detector.BLACK_PAIR_LONG_MAX_MS

    span_durations_ms: list[int] = []
    for pair_index in range(complete_pairs):
        span_durations_ms.extend((10_000, 12_000))
        if pair_index == invalid_pair_index:
            span_durations_ms[-1] = int(
                detector.DEFAULT_MAX_PLAY_S * 1000) + 10_000
    # One terminal pair: a normal first angle followed by frozen footage.
    span_durations_ms.extend((
        10_000,
        int(detector.DEFAULT_MAX_PLAY_S * 1000) + 10_000,
    ))

    spans_ms: list[tuple[int, int]] = []
    black_ms: list[tuple[int, int]] = []
    cursor_ms = 0
    for span_index, span_duration_ms in enumerate(span_durations_ms):
        start_ms = cursor_ms
        end_ms = start_ms + span_duration_ms
        spans_ms.append((start_ms, end_ms))
        cursor_ms = end_ms
        if span_index == len(span_durations_ms) - 1:
            continue
        default_gap_ms = short_ms if span_index % 2 == 0 else long_ms
        gap_ms = gap_overrides.get(span_index, default_gap_ms)
        black_ms.append((cursor_ms, cursor_ms + gap_ms))
        cursor_ms += gap_ms

    spans = [(start / 1000, end / 1000) for start, end in spans_ms]
    black = [(start / 1000, end / 1000) for start, end in black_ms]
    cuts = [
        endpoint / 1000
        for gap in black_ms
        for endpoint in gap
    ]
    runs = group_spans_into_plays(
        spans,
        separator_max_s=detector.DEFAULT_SEPARATOR_MAX_S,
        min_play_s=0.0,
        max_play_s=float("inf"),
    )
    assert len(runs) == 1
    run = runs[0]
    profile = FilmProfile(
        modal_angles=1,
        modal_share=0.0,
        median_play_ms=0,
        max_play_ms=int(detector.DEFAULT_MAX_PLAY_S * 1000),
        calibrated=False,
    )
    review = [
        UnclassifiedSegment(
            start_ms=run.start_ms,
            end_ms=run.end_ms,
            angle_count=run.angle_count,
            reason="primary uncalibrated multi-span block",
            split_points_ms=run.angle_starts[1:],
        )
    ]
    return {
        "complete_pairs": complete_pairs,
        "spans_ms": spans_ms,
        "black_ms": black_ms,
        "spans": spans,
        "black": black,
        "cuts": cuts,
        "runs": runs,
        "plays": [],
        "review": review,
        "profile": profile,
        "duration_ms": cursor_ms,
    }


def _try_black_pair_fallback(shape: dict, **overrides):
    arguments = {
        "plays": shape["plays"],
        "review": shape["review"],
        "runs": shape["runs"],
        "spans": shape["spans"],
        "black": shape["black"],
        "cuts": shape["cuts"],
        "profile": shape["profile"],
        "signal": "mixed",
        "scene_threshold": detector.DEFAULT_SCENE_THRESHOLD,
        "separator_max_s": detector.DEFAULT_SEPARATOR_MAX_S,
        "min_play_s": detector.DEFAULT_MIN_PLAY_S,
        "max_play_s": detector.DEFAULT_MAX_PLAY_S,
        "duration_ms": shape["duration_ms"],
    }
    arguments.update(overrides)
    return detector._try_bimodal_black_angle_fallback(
        arguments["plays"],
        arguments["review"],
        arguments["runs"],
        arguments["spans"],
        arguments["black"],
        arguments["cuts"],
        arguments["profile"],
        signal=arguments["signal"],
        scene_threshold=arguments["scene_threshold"],
        separator_max_s=arguments["separator_max_s"],
        min_play_s=arguments["min_play_s"],
        max_play_s=arguments["max_play_s"],
        duration_ms=arguments["duration_ms"],
    )


class TestSpansFromBoundaries:
    def test_black_gaps_produce_spans(self):
        spans, signal = spans_from_boundaries(
            30.0, black=[(10.0, 10.3), (20.0, 20.3)], cuts=[])
        assert signal == "black"
        assert spans == [(0.0, 10.0), (10.3, 20.0), (20.3, 30.0)]

    def test_scene_cuts_used_when_no_black(self):
        spans, signal = spans_from_boundaries(30.0, black=[], cuts=[12.0, 24.0])
        assert signal == "scene"
        assert spans == [(0.0, 12.0), (12.0, 24.0), (24.0, 30.0)]

    def test_black_wins_when_both_present(self):
        _spans, signal = spans_from_boundaries(
            30.0, black=[(10.0, 10.3)], cuts=[5.0, 20.0])
        assert signal == "mixed"

    def test_no_boundaries_yields_whole_film(self):
        spans, signal = spans_from_boundaries(30.0, black=[], cuts=[])
        assert spans == [(0.0, 30.0)] and signal == "none"


class TestMixedSceneFallback:
    @staticmethod
    def _scene_shape(play_count: int) -> tuple[list[float], int]:
        cuts: list[float] = []
        cursor = 0.0
        for index in range(play_count):
            cursor += 30.0
            if index == play_count - 1:
                break
            cuts.append(cursor)
            cursor += 2.0
            cuts.append(cursor)
        return cuts, round(cursor * 1000)

    @staticmethod
    def _miami_pair_shape(
        *,
        valid_pair_count: int = 9,
        false_pair_count: int = 1,
    ) -> dict:
        """Frozen-batch geometry with normal-play population around it."""
        pair_components = [
            (15_515, 20_020),
            (14_014, 16_516),
            (20_521, 24_024),
            (15_016, 16_517),
            (17_084, 20_521),
            (19_519, 23_524),
            (24_525, 27_028),
            (16_016, 18_518),
            (12_513, 17_017),
        ][:valid_pair_count]
        normal_count = 30
        logical_runs = (
            [(36_536,)] * normal_count
            + pair_components
            + [(7_507, 10_010)] * false_pair_count
        )
        spans_ms: list[tuple[int, int]] = []
        expected_pairs: list[tuple[int, int, list[int]]] = []
        false_ranges: list[tuple[int, int]] = []
        cursor_ms = 0
        for logical_index, components in enumerate(logical_runs):
            starts: list[int] = []
            logical_start_ms = cursor_ms
            for component_index, duration_ms in enumerate(components):
                starts.append(cursor_ms)
                spans_ms.append((cursor_ms, cursor_ms + duration_ms))
                cursor_ms += duration_ms
                if component_index < len(components) - 1:
                    spans_ms.append((cursor_ms, cursor_ms + 500))
                    cursor_ms += 500
            logical_end_ms = cursor_ms
            if len(components) == 2:
                if logical_index < normal_count + valid_pair_count:
                    expected_pairs.append(
                        (logical_start_ms, logical_end_ms, starts))
                else:
                    false_ranges.append(
                        (logical_start_ms, logical_end_ms))
            if logical_index < len(logical_runs) - 1:
                spans_ms.append((cursor_ms, cursor_ms + 2_000))
                cursor_ms += 2_000

        spans = [
            (start_ms / 1000, end_ms / 1000)
            for start_ms, end_ms in spans_ms
        ]
        cuts = [end_ms / 1000 for _start_ms, end_ms in spans_ms[:-1]]
        plays = group_spans_into_plays(
            spans,
            detector.DEFAULT_SEPARATOR_MAX_S,
            min_play_s=0.0,
            max_play_s=float("inf"),
        )
        profile = detector.profile_film(plays)
        for play in plays:
            play.needs_review = True
            play.review_reason = detector.MIXED_SCENE_FALLBACK_REVIEW_REASON
        return {
            "spans": spans,
            "cuts": cuts,
            "plays": plays,
            "profile": profile,
            "duration_ms": cursor_ms,
            "expected_pairs": expected_pairs,
            "false_ranges": false_ranges,
        }

    @staticmethod
    def _fragment_pair_gap(
        shape: dict,
        pair_index: int,
        fragment_durations_ms: list[int],
    ) -> tuple[int, int]:
        """Replace one synthetic camera gap with contiguous raw pieces."""
        _pair_start_ms, _pair_end_ms, angle_starts = \
            shape["expected_pairs"][pair_index]
        original_gap_end_ms = angle_starts[1]
        gap_start_ms = original_gap_end_ms - 500
        new_gap_ms = sum(fragment_durations_ms)
        delta_ms = new_gap_ms - (original_gap_end_ms - gap_start_ms)
        span_index = next(
            index
            for index, (start_s, end_s) in enumerate(shape["spans"])
            if round(start_s * 1000) == gap_start_ms
            and round(end_s * 1000) == original_gap_end_ms
        )

        spans_ms = [
            (round(start_s * 1000), round(end_s * 1000))
            for start_s, end_s in shape["spans"]
        ]
        cursor_ms = gap_start_ms
        fragments = []
        for duration_ms in fragment_durations_ms:
            fragments.append((
                cursor_ms,
                cursor_ms + duration_ms,
            ))
            cursor_ms += duration_ms
        shifted_tail = [
            (start_ms + delta_ms, end_ms + delta_ms)
            for start_ms, end_ms in spans_ms[span_index + 1:]
        ]
        spans_ms[span_index:] = fragments + shifted_tail
        shape["spans"] = [
            (start_ms / 1000, end_ms / 1000)
            for start_ms, end_ms in spans_ms
        ]
        shape["cuts"] = [
            end_s for _start_s, end_s in shape["spans"][:-1]
        ]

        for play in shape["plays"]:
            if play.start_ms < original_gap_end_ms:
                continue
            play.start_ms += delta_ms
            play.end_ms += delta_ms
            play.angle_starts = [
                start_ms + delta_ms for start_ms in play.angle_starts
            ]

        adjusted_pairs = []
        for start_ms, end_ms, starts in shape["expected_pairs"]:
            adjusted_pairs.append((
                start_ms + (
                    delta_ms if start_ms >= original_gap_end_ms else 0),
                end_ms + (
                    delta_ms if end_ms >= original_gap_end_ms else 0),
                [
                    value + (
                        delta_ms if value >= original_gap_end_ms else 0)
                    for value in starts
                ],
            ))
        shape["expected_pairs"] = adjusted_pairs
        shape["false_ranges"] = [
            (
                start_ms + (
                    delta_ms if start_ms >= original_gap_end_ms else 0),
                end_ms + (
                    delta_ms if end_ms >= original_gap_end_ms else 0),
            )
            for start_ms, end_ms in shape["false_ranges"]
        ]
        shape["duration_ms"] += delta_ms
        return (
            shape["expected_pairs"][pair_index][0],
            shape["expected_pairs"][pair_index][1],
        )

    def test_frozen_miami_shape_pairs_angles_but_not_short_no_play(self):
        shape = self._miami_pair_shape()
        plays = shape["plays"]
        profile = shape["profile"]

        repaired, pair_count = (
            detector._pair_mixed_scene_fallback_angles(
                plays,
                shape["spans"],
                profile,
                pair_ceiling_ms=profile.max_play_ms,
            )
        )

        assert profile.median_play_ms == 36_536
        assert pair_count == 9
        assert len(repaired) == len(plays) - 9
        paired = [
            play for play in repaired
            if play.review_reason == detector.MIXED_SCENE_PAIR_REVIEW_REASON
        ]
        assert [
            (play.start_ms, play.end_ms, play.angle_starts)
            for play in paired
        ] == shape["expected_pairs"]
        assert [
            (play.start_ms, play.end_ms) for play in repaired[-2:]
        ] == [
            (shape["false_ranges"][0][0],
             shape["false_ranges"][0][0] + 7_507),
            (shape["false_ranges"][0][0] + 8_007,
             shape["false_ranges"][0][1]),
        ]
        assert repaired[-2] is plays[-2]
        assert repaired[-1] is plays[-1]

    def test_human_confirmed_three_fragment_miami_gap_remains_pairable(self):
        shape = self._miami_pair_shape()
        pair_range = self._fragment_pair_gap(
            shape, 5, [201, 33, 267])

        repaired, pair_count = (
            detector._pair_mixed_scene_fallback_angles(
                shape["plays"],
                shape["spans"],
                shape["profile"],
                pair_ceiling_ms=shape["profile"].max_play_ms,
            )
        )

        assert pair_count == 9
        assert any(
            (play.start_ms, play.end_ms) == pair_range
            and play.angle_count == 2
            and play.review_reason
            == detector.MIXED_SCENE_PAIR_REVIEW_REASON
            for play in repaired
        )

    @pytest.mark.parametrize(
        ("fragments", "expected"),
        [
            ([150, 50, 250], True),
            ([300, 1, 249], True),
            ([149, 33, 269], False),
            ([301, 33, 167], False),
            ([200, 51, 200], False),
        ],
    )
    def test_fragmented_micro_signature_boundaries(
            self, fragments, expected):
        start_ms = 1_000
        raw_spans = []
        cursor_ms = start_ms
        for duration_ms in fragments:
            raw_spans.append((cursor_ms, cursor_ms + duration_ms))
            cursor_ms += duration_ms

        assert detector._mixed_scene_micro_gap_is_verified(
            start_ms, cursor_ms, raw_spans) is expected

    @pytest.mark.parametrize(
        "raw_spans",
        [
            [(1_000, 1_201), (1_204, 1_237), (1_237, 1_501)],
            [(1_000, 1_201), (1_198, 1_231), (1_231, 1_501)],
        ],
    )
    def test_fragmented_micro_signature_rejects_holes_and_overlaps(
            self, raw_spans):
        assert not detector._mixed_scene_micro_gap_is_verified(
            1_000, 1_501, raw_spans)

    @pytest.mark.parametrize(
        "fragments",
        [
            [301, 33, 167],
            [167, 166, 167],
            [250, 250],
            [125, 125, 125, 125],
        ],
    )
    def test_other_fragmented_miami_micro_shapes_still_abstain(
            self, fragments):
        shape = self._miami_pair_shape()
        pair_range = self._fragment_pair_gap(shape, 5, fragments)

        repaired, pair_count = (
            detector._pair_mixed_scene_fallback_angles(
                shape["plays"],
                shape["spans"],
                shape["profile"],
                pair_ceiling_ms=shape["profile"].max_play_ms,
            )
        )

        assert pair_count == 8
        assert not any(
            (play.start_ms, play.end_ms) == pair_range
            and play.angle_count == 2
            for play in repaired
        )

    @pytest.mark.parametrize(
        ("valid_pair_count", "false_pair_count"),
        [
            (7, 1),
            (9, 4),
        ],
    )
    def test_weak_mixed_pair_population_abstains(
            self, valid_pair_count, false_pair_count):
        shape = self._miami_pair_shape(
            valid_pair_count=valid_pair_count,
            false_pair_count=false_pair_count,
        )

        repaired, pair_count = (
            detector._pair_mixed_scene_fallback_angles(
                shape["plays"],
                shape["spans"],
                shape["profile"],
                pair_ceiling_ms=shape["profile"].max_play_ms,
            )
        )

        assert repaired is shape["plays"]
        assert pair_count == 0

    def test_mixed_fallback_integration_pairs_without_second_decode(
            self, monkeypatch, tmp_path):
        shape = self._miami_pair_shape()
        stdout = "\n".join(
            f"frame:{index} pts_time:{cut:.3f}"
            for index, cut in enumerate(shape["cuts"])
        )
        calls = []

        def fake_analysis(
                _ffmpeg, _source, threshold, _process_started=None):
            calls.append(threshold)
            return (
                stdout,
                "black_start:400.100 black_end:400.300",
            )

        monkeypatch.setattr(
            detector, "_run_ffmpeg_analysis", fake_analysis)
        source = tmp_path / "sample-mixed-shape.mp4"
        source.touch()

        result = detect_plays(
            "ffmpeg",
            source,
            duration_ms=shape["duration_ms"],
        )

        assert calls == [detector.DEFAULT_SCENE_THRESHOLD]
        assert result.signal == "scene"
        assert result.unclassified == []
        assert len(result.plays) == len(shape["plays"]) - 9
        assert sum(
            play.angle_count == 2
            and play.review_reason == detector.MIXED_SCENE_PAIR_REVIEW_REASON
            for play in result.plays
        ) == 9
        assert all(play.needs_review for play in result.plays)
        assert [
            (play.start_ms, play.end_ms)
            for play in result.plays[-2:]
        ] == [
            (shape["false_ranges"][0][0],
             shape["false_ranges"][0][0] + 7_507),
            (shape["false_ranges"][0][0] + 8_007,
             shape["false_ranges"][0][1]),
        ]

    def test_sparse_black_cannot_hide_strong_scene_structure(
            self, monkeypatch, tmp_path):
        cuts, duration_ms = self._scene_shape(
            detector.MIXED_SCENE_FALLBACK_MIN_PROFILED_RUNS)
        stdout = "\n".join(
            f"frame:{index} pts_time:{cut:.3f}"
            for index, cut in enumerate(cuts)
        )
        monkeypatch.setattr(
            detector,
            "_run_ffmpeg_analysis",
            lambda *_args: (
                stdout,
                "black_start:400.000 black_end:400.200",
            ),
        )
        source = tmp_path / "mixed-film.mp4"
        source.touch()

        result = detect_plays("ffmpeg", source, duration_ms=duration_ms)

        assert result.signal == "scene"
        assert len(result.plays) == \
            detector.MIXED_SCENE_FALLBACK_MIN_PROFILED_RUNS
        assert result.unclassified == []
        assert result.profile is not None
        assert result.profile.calibrated is True
        assert all(play.needs_review for play in result.plays)
        assert {
            play.review_reason for play in result.plays
        } == {detector.MIXED_SCENE_FALLBACK_REVIEW_REASON}

    def test_small_scene_population_cannot_override_black(
            self, monkeypatch, tmp_path):
        cuts, duration_ms = self._scene_shape(
            detector.MIXED_SCENE_FALLBACK_MIN_PROFILED_RUNS - 1)
        stdout = "\n".join(
            f"frame:{index} pts_time:{cut:.3f}"
            for index, cut in enumerate(cuts)
        )
        monkeypatch.setattr(
            detector,
            "_run_ffmpeg_analysis",
            lambda *_args: (
                stdout,
                "black_start:400.000 black_end:400.200",
            ),
        )
        source = tmp_path / "small-mixed-film.mp4"
        source.touch()

        result = detect_plays("ffmpeg", source, duration_ms=duration_ms)

        assert result.signal == "mixed"
        assert result.plays == []
        assert len(result.unclassified) == 1

    def test_raw_scene_count_cannot_mask_too_few_profiled_runs(
            self, monkeypatch, tmp_path):
        plausible_count = 8
        overlong_count = 16
        assert plausible_count == detector.MIN_RUNS_TO_PROFILE
        assert plausible_count + overlong_count == \
            detector.MIXED_SCENE_FALLBACK_MIN_PROFILED_RUNS
        durations_s = (
            [30.0] * plausible_count
            + [detector.DEFAULT_MAX_PLAY_S + 10.0] * overlong_count
        )
        cuts: list[float] = []
        cursor = 0.0
        for index, duration_s in enumerate(durations_s):
            cursor += duration_s
            if index == len(durations_s) - 1:
                break
            cuts.append(cursor)
            cursor += 2.0
            cuts.append(cursor)
        scene_spans, _signal = spans_from_boundaries(
            cursor, black=[], cuts=cuts)
        scene_runs = group_spans_into_plays(
            scene_spans,
            detector.DEFAULT_SEPARATOR_MAX_S,
            min_play_s=0.0,
            max_play_s=float("inf"),
        )
        assert len(scene_runs) == 24
        assert len(detector._profiled_runs(scene_runs)) == 8
        stdout = "\n".join(
            f"frame:{index} pts_time:{cut:.3f}"
            for index, cut in enumerate(cuts)
        )
        monkeypatch.setattr(
            detector,
            "_run_ffmpeg_analysis",
            lambda *_args: (
                stdout,
                "black_start:400.000 black_end:400.200",
            ),
        )
        source = tmp_path / "mostly-overlong-mixed-film.mp4"
        source.touch()

        result = detect_plays(
            "ffmpeg", source, duration_ms=round(cursor * 1000))

        assert result.signal == "mixed"
        assert result.plays == []
        assert len(result.unclassified) == 1

    def test_calibrated_black_profile_never_requests_scene_fallback(self):
        profile = FilmProfile(
            modal_angles=2,
            modal_share=1.0,
            median_play_ms=30_000,
            max_play_ms=60_000,
            calibrated=True,
        )

        assert detector._try_mixed_scene_fallback(
            800.0,
            [(400.0, 400.2)],
            [30.0, 32.0] * 24,
            profile,
            separator_max_s=5.0,
        ) is None


class TestFFmpegAnalysis:
    def test_worker_cancel_terminates_and_waits_for_ffmpeg(
            self, monkeypatch, tmp_path):
        source = tmp_path / "film.mp4"
        source.touch()
        communicating = threading.Event()
        released = threading.Event()

        class BlockingProcess:
            pid = 4400
            returncode = None
            terminated = False
            waited = False

            def communicate(self, timeout):
                communicating.set()
                assert released.wait(timeout=2)
                return "", "cancelled"

            def poll(self):
                return self.returncode

            def terminate(self):
                self.terminated = True
                self.returncode = 1
                released.set()

            def wait(self, timeout):
                self.waited = True
                assert released.wait(timeout)
                return self.returncode

            def kill(self):
                raise AssertionError("terminate should release the process")

        process = BlockingProcess()
        monkeypatch.setattr(
            detector.subprocess, "Popen", lambda *_a, **_k: process)
        monkeypatch.setattr(
            detector.background_service, "register", lambda _pid: None)
        monkeypatch.setattr(
            detector.background_service, "unregister", lambda _pid: None)
        worker = PlayDetectWorker(
            "ffmpeg", source, 30_000, 5.0, 4.0, 90.0)

        worker.start()
        assert communicating.wait(timeout=2)
        worker.cancel()

        assert process.terminated
        assert process.waited
        assert not worker.isRunning()

    def test_nonzero_exit_rejects_partial_detector_output(
            self, monkeypatch, tmp_path):
        calls = []

        class FailedProcess:
            pid = 4401
            returncode = 1

            @staticmethod
            def communicate(timeout):
                assert timeout == 3600
                return "partial scene metadata", "conversion failed"

        monkeypatch.setattr(
            detector.subprocess,
            "Popen",
            lambda *_args, **_kwargs: FailedProcess(),
        )
        monkeypatch.setattr(
            detector.background_service,
            "register",
            lambda pid: calls.append(("register", pid)),
        )
        monkeypatch.setattr(
            detector.background_service,
            "unregister",
            lambda pid: calls.append(("unregister", pid)),
        )

        with pytest.raises(
                TapeSiftError,
                match="FFmpeg did not finish analysing"):
            detector._run_ffmpeg_analysis(
                "ffmpeg",
                tmp_path / "film.mp4",
                detector.DEFAULT_SCENE_THRESHOLD,
            )

        assert calls == [("register", 4401), ("unregister", 4401)]


class TestGrouping:
    def test_two_angles_group_into_one_play(self):
        # wide, tight, then a short separator card
        spans = [(0.0, 15.0), (15.3, 29.0), (29.3, 31.0),
                 (31.3, 46.0), (46.3, 60.0)]
        plays = group_spans_into_plays(spans, separator_max_s=5.0)
        assert len(plays) == 2
        assert plays[0].angle_count == 2
        assert (plays[0].start_ms, plays[0].end_ms) == (0, 29_000)
        assert plays[0].angle_starts == [0, 15_300]

    def test_card_separated_offense_structure(self):
        # [play 23s][card 3s][play 23s][card 3s][play 20s]
        spans = [(0.0, 23.0), (23.0, 26.0), (26.0, 49.0),
                 (49.0, 52.0), (52.0, 72.0)]
        plays = group_spans_into_plays(spans, separator_max_s=5.0)
        assert len(plays) == 3
        assert all(p.angle_count == 1 for p in plays)

    def test_separator_length_is_the_discriminator(self):
        spans = [(0.0, 20.0), (20.0, 22.0), (22.0, 42.0)]
        # With a 5s separator threshold the 2s span splits the plays...
        assert len(group_spans_into_plays(spans, separator_max_s=5.0)) == 2
        # ...but treating it as content merges everything into one play.
        merged = group_spans_into_plays(spans, separator_max_s=1.0)
        assert len(merged) == 1
        assert merged[0].angle_count == 3

    def test_too_short_and_too_long_are_dropped(self):
        # Separators between each candidate, so they are judged individually:
        # 6s (too short), 190s (dead time, too long), 15s (a real play).
        spans = [(0.0, 6.0), (6.5, 6.9),
                 (10.0, 200.0), (200.5, 200.9),
                 (205.0, 220.0)]
        plays = group_spans_into_plays(spans, separator_max_s=1.0,
                                       min_play_s=8.0, max_play_s=90.0)
        assert len(plays) == 1
        assert plays[0].start_ms == 205_000

    def test_content_runs_merge_without_a_separator(self):
        """No separator between spans means they are one play, by design."""
        spans = [(0.0, 20.0), (20.5, 40.0)]
        plays = group_spans_into_plays(spans, separator_max_s=0.2)
        assert len(plays) == 1 and plays[0].angle_count == 2

    def test_trailing_run_is_flushed(self):
        spans = [(0.0, 2.0), (2.0, 20.0)]
        plays = group_spans_into_plays(spans, separator_max_s=5.0)
        assert len(plays) == 1 and plays[0].start_ms == 2_000

    def test_empty_input(self):
        assert group_spans_into_plays([]) == []

    def test_realistic_defense_film_shape(self):
        """51 plays, each [wide][tight] then a separator - the measured shape."""
        spans, t = [], 0.0
        for _ in range(51):
            spans.append((t, t + 15.0)); t += 15.3
            spans.append((t, t + 14.0)); t += 14.3
            spans.append((t, t + 1.7)); t += 2.0
        plays = group_spans_into_plays(spans, separator_max_s=5.0)
        assert len(plays) == 51
        assert all(p.angle_count == 2 for p in plays)
        assert all(25_000 <= p.duration_ms <= 32_000 for p in plays)


class TestMergedPlayDetection:
    """Spotting clips that hold more than one play (the real-world case:
    a boundary between plays wasn't marked in the film)."""

    def _result(self, plays):
        return DetectionResult(plays=plays, signal="black", spans_found=0,
                               separators_found=0, duration_ms=3_000_000)

    def _play(self, start, angles, angle_len=15_000):
        starts = [start + i * angle_len for i in range(angles)]
        return DetectedPlay(start_ms=start, end_ms=start + angles * angle_len,
                            angle_count=angles, angle_starts=starts)

    def test_double_angle_count_is_flagged_with_exact_split(self):
        # Nine normal 2-angle plays and one carrying 4 segments.
        plays = [self._play(i * 60_000, 2) for i in range(9)]
        plays.append(self._play(600_000, 4))
        suspects = find_merged_plays(self._result(plays))
        assert len(suspects) == 1
        suspect = suspects[0]
        assert suspect.angle_count == 4
        # Split exactly where the third camera segment began.
        assert suspect.split_points_ms == [600_000 + 2 * 15_000]
        assert "camera segments" in suspect.reason

    def test_triple_length_yields_two_splits(self):
        plays = [self._play(i * 60_000, 2) for i in range(9)]
        plays.append(self._play(600_000, 6))
        suspects = find_merged_plays(self._result(plays))
        assert suspects[0].split_points_ms == [630_000, 660_000]

    def test_normal_plays_are_left_alone(self):
        plays = [self._play(i * 60_000, 2) for i in range(10)]
        assert find_merged_plays(self._result(plays)) == []

    def test_long_outlier_without_extra_segments(self):
        plays = [self._play(i * 60_000, 2, 10_000) for i in range(9)]
        # Same segment count, but each segment is far longer.
        plays.append(self._play(600_000, 2, 40_000))
        suspects = find_merged_plays(self._result(plays))
        assert len(suspects) == 1
        assert "typical play" in suspects[0].reason

    def test_too_few_plays_to_judge(self):
        plays = [self._play(0, 2), self._play(60_000, 6)]
        assert find_merged_plays(self._result(plays)) == []


class TestAggressiveSplitSafety:
    """Fallback splitting may help, but it must never call a giant tail a play."""

    def test_oversized_final_remainder_stays_unclassified(self):
        # Reproduces the Bethune tail: one plausible angle followed by an
        # 884-second remainder inside a 914-second source block.
        run = DetectedPlay(
            start_ms=1_640_566,
            end_ms=2_554_283,
            angle_count=2,
            angle_starts=[1_640_566, 1_669_433],
        )
        profile = FilmProfile(
            modal_angles=2,
            modal_share=0.98,
            median_play_ms=28_466,
            max_play_ms=60_000,
            calibrated=True,
        )

        plays, unclassified = classify_runs([run], profile)

        assert [(play.start_ms, play.end_ms) for play in plays] == [
            (1_640_566, 1_669_433),
        ]
        assert all(play.duration_ms <= profile.max_play_ms for play in plays)
        assert len(unclassified) == 1
        assert (
            unclassified[0].start_ms,
            unclassified[0].end_ms,
        ) == (1_669_433, 2_554_283)
        assert "remainder" in unclassified[0].reason
        assert "60s play ceiling" in unclassified[0].reason

    def test_partition_preserves_every_millisecond(self):
        run = DetectedPlay(
            start_ms=0,
            end_ms=240_000,
            angle_count=4,
            angle_starts=[0, 30_000, 60_000, 90_000],
        )
        profile = FilmProfile(
            modal_angles=1,
            modal_share=0.9,
            median_play_ms=30_000,
            max_play_ms=60_000,
            calibrated=True,
        )

        plays, unclassified = classify_runs([run], profile)

        covered = sum(play.duration_ms for play in plays)
        covered += sum(segment.duration_ms for segment in unclassified)
        assert covered == run.duration_ms
        assert all(play.duration_ms <= profile.max_play_ms for play in plays)


class TestBimodalBlackAngleFallback:
    """Only a fully corroborated alternating black-gap topology may pair."""

    def test_south_carolina_shape_pairs_exact_angles_and_keeps_frozen_tail(
            self):
        shape = _black_pair_shape()

        plays, review = _try_black_pair_fallback(shape)

        assert len(plays) == shape["complete_pairs"]
        expected = []
        for pair_index in range(shape["complete_pairs"]):
            first = shape["spans_ms"][pair_index * 2]
            second = shape["spans_ms"][pair_index * 2 + 1]
            expected.append((
                first[0],
                second[1],
                [first[0], second[0]],
            ))
        assert [
            (play.start_ms, play.end_ms, play.angle_starts)
            for play in plays
        ] == expected
        assert all(play.angle_count == 2 for play in plays)
        assert all(play.needs_review for play in plays)
        assert all(
            play.review_reason == detector.BLACK_PAIR_REVIEW_REASON
            for play in plays
        )
        # The long between-play gap is an outer boundary, never swallowed.
        assert plays[1].start_ms - plays[0].end_ms == 2_500

        terminal_first, terminal_second = shape["spans_ms"][-2:]
        assert len(review) == 1
        assert (
            review[0].start_ms,
            review[0].end_ms,
            review[0].angle_count,
            review[0].split_points_ms,
        ) == (
            terminal_first[0],
            terminal_second[1],
            2,
            [terminal_second[0]],
        )
        combined = sorted(
            [(play.start_ms, play.end_ms) for play in plays]
            + [(item.start_ms, item.end_ms) for item in review]
        )
        assert all(0 <= start < end <= shape["duration_ms"]
                   for start, end in combined)
        assert all(
            left_end <= right_start
            for (_left_start, left_end), (right_start, _right_end)
            in zip(combined, combined[1:])
        )

    def test_detect_integration_uses_one_decode_and_preserves_boundaries(
            self, monkeypatch, tmp_path):
        shape = _black_pair_shape()
        stdout = "\n".join(
            f"frame:{index} pts_time:{cut:.3f}"
            for index, cut in enumerate(shape["cuts"])
        )
        stderr = "\n".join(
            f"black_start:{start:.3f} black_end:{end:.3f}"
            for start, end in shape["black"]
        )
        calls = []

        def fake_analysis(
                _ffmpeg, _source, threshold, _process_started=None):
            calls.append(threshold)
            return stdout, stderr

        monkeypatch.setattr(
            detector, "_run_ffmpeg_analysis", fake_analysis)
        source = tmp_path / "south-carolina-shape.mp4"
        source.touch()

        result = detect_plays(
            "ffmpeg",
            source,
            duration_ms=shape["duration_ms"],
        )

        assert calls == [detector.DEFAULT_SCENE_THRESHOLD]
        assert result.signal == "mixed"
        assert result.spans_found == len(shape["spans"])
        assert result.separators_found == 0
        assert len(result.plays) == shape["complete_pairs"]
        assert all(play.needs_review for play in result.plays)
        expected_bounds = [
            (
                shape["spans_ms"][pair_index * 2][0],
                shape["spans_ms"][pair_index * 2 + 1][1],
            )
            for pair_index in range(shape["complete_pairs"])
        ]
        assert [
            (play.start_ms, play.end_ms) for play in result.plays
        ] == expected_bounds
        assert [
            (item.start_ms, item.end_ms)
            for item in result.unclassified
        ] == [(
            shape["spans_ms"][-2][0],
            shape["spans_ms"][-1][1],
        )]

    def test_one_millisecond_film_end_rounding_does_not_block_recovery(self):
        shape = _black_pair_shape()
        reported_duration_ms = shape["duration_ms"] + 1

        plays, review = _try_black_pair_fallback(
            shape,
            duration_ms=reported_duration_ms,
        )

        assert len(plays) == shape["complete_pairs"]
        assert len(review) == 1
        assert review[0].end_ms == reported_duration_ms

    @pytest.mark.parametrize("signal", ["black", "scene", "none"])
    def test_non_mixed_signal_preserves_exact_primary_output(self, signal):
        shape = _black_pair_shape()

        plays, review = _try_black_pair_fallback(shape, signal=signal)

        assert plays is shape["plays"]
        assert review is shape["review"]

    @pytest.mark.parametrize(
        ("setting", "value"),
        [
            ("scene_threshold", detector.DEFAULT_SCENE_THRESHOLD - 0.01),
            ("separator_max_s", detector.DEFAULT_SEPARATOR_MAX_S - 0.5),
            ("min_play_s", detector.DEFAULT_MIN_PLAY_S + 1.0),
            ("max_play_s", detector.DEFAULT_MAX_PLAY_S - 10.0),
        ],
    )
    def test_custom_detector_setting_preserves_exact_primary_output(
            self, setting, value):
        shape = _black_pair_shape()

        plays, review = _try_black_pair_fallback(
            shape, **{setting: value})

        assert plays is shape["plays"]
        assert review is shape["review"]

    @pytest.mark.parametrize("blocker", ["calibrated", "existing-play"])
    def test_calibrated_or_existing_output_preserves_exact_primary_result(
            self, blocker):
        shape = _black_pair_shape()
        overrides = {}
        if blocker == "calibrated":
            overrides["profile"] = FilmProfile(
                modal_angles=2,
                modal_share=1.0,
                median_play_ms=22_250,
                max_play_ms=60_000,
                calibrated=True,
            )
        else:
            overrides["plays"] = [
                DetectedPlay(0, 20_000, 2, [0, 10_250])
            ]
        original_plays = overrides.get("plays", shape["plays"])

        plays, review = _try_black_pair_fallback(shape, **overrides)

        assert plays is original_plays
        assert review is shape["review"]

    def test_too_few_complete_pairs_preserves_exact_primary_output(self):
        shape = _black_pair_shape(
            complete_pairs=detector.BLACK_PAIR_FALLBACK_MIN_PAIRS - 1)

        plays, review = _try_black_pair_fallback(shape)

        assert plays is shape["plays"]
        assert review is shape["review"]

    @pytest.mark.parametrize("gap_case", ["nonalternating", "ambiguous"])
    def test_nonalternating_or_ambiguous_gap_preserves_primary_output(
            self, gap_case):
        if gap_case == "nonalternating":
            # Gap 1 is normally the long boundary after the first pair.
            gap_ms = detector.BLACK_PAIR_SHORT_MAX_MS
            gap_index = 1
        else:
            gap_ms = (
                detector.BLACK_PAIR_SHORT_MAX_MS
                + detector.BLACK_PAIR_LONG_MIN_MS
            ) // 2
            gap_index = 0
        shape = _black_pair_shape(
            gap_overrides={gap_index: gap_ms})

        plays, review = _try_black_pair_fallback(shape)

        assert plays is shape["plays"]
        assert review is shape["review"]

    @pytest.mark.parametrize("endpoint_index", [0, 1])
    def test_missing_scene_endpoint_corroboration_preserves_primary_output(
            self, endpoint_index):
        shape = _black_pair_shape()
        cuts = list(shape["cuts"])
        cuts.pop(endpoint_index)

        plays, review = _try_black_pair_fallback(shape, cuts=cuts)

        assert plays is shape["plays"]
        assert review is shape["review"]

    def test_malformed_run_topology_preserves_exact_primary_output(self):
        shape = _black_pair_shape()
        source = shape["runs"][0]
        malformed = DetectedPlay(
            start_ms=source.start_ms,
            end_ms=source.end_ms,
            angle_count=source.angle_count,
            angle_starts=source.angle_starts[:-1],
        )

        plays, review = _try_black_pair_fallback(
            shape, runs=[malformed])

        assert plays is shape["plays"]
        assert review is shape["review"]

    def test_invalid_interior_pair_preserves_exact_primary_output(self):
        shape = _black_pair_shape(invalid_pair_index=1)

        plays, review = _try_black_pair_fallback(shape)

        assert plays is shape["plays"]
        assert review is shape["review"]

    def test_unexpected_failure_preserves_exact_primary_output(
            self, monkeypatch):
        shape = _black_pair_shape()

        def broken_median(_values):
            raise RuntimeError("unexpected topology failure")

        monkeypatch.setattr(detector.statistics, "median", broken_median)

        plays, review = _try_black_pair_fallback(shape)

        assert plays is shape["plays"]
        assert review is shape["review"]


class TestWeakSceneRecovery:
    """At most one bounded play is peeled from an existing review range."""

    def test_pittsburgh_shaped_cluster_peels_one_play_and_keeps_remainder(
            self):
        review = [
            UnclassifiedSegment(
                start_ms=500,
                end_ms=80_080,
                angle_count=2,
                reason="camera changes are unreliable",
            )
        ]
        events = [
            (23_523, 0.225),
            (43_043, 0.233),
            (43_544, 0.518),
            (63_063, 0.221),
        ]

        recovered, remaining = _recover_scene_review_segments(
            review, events, floor_ms=4_000, ceiling_ms=60_000,
            median_play_ms=39_289)

        assert [(play.start_ms, play.end_ms) for play in recovered] == [
            (500, 43_043),
        ]
        assert recovered[0].needs_review is True
        assert "weak scene cuts" in recovered[0].review_reason
        assert [(item.start_ms, item.end_ms) for item in remaining] == [
            (43_043, 80_080),
        ]
        covered = recovered[0].duration_ms + remaining[0].duration_ms
        assert covered == review[0].duration_ms

    def test_recovery_never_recurses_into_the_new_remainder(self):
        review = [
            UnclassifiedSegment(
                start_ms=0,
                end_ms=120_000,
                angle_count=1,
                reason="long source block",
            )
        ]
        events = [
            (40_000, 0.4),
            (80_000, 0.4),
        ]

        recovered, remaining = _recover_scene_review_segments(
            review, events, floor_ms=4_000, ceiling_ms=60_000,
            median_play_ms=40_000)

        assert [(play.start_ms, play.end_ms) for play in recovered] == [
            (0, 40_000),
        ]
        assert [(item.start_ms, item.end_ms) for item in remaining] == [
            (40_000, 120_000),
        ]

    def test_short_or_boundaryless_review_range_is_unchanged(self):
        review = [
            UnclassifiedSegment(
                start_ms=20_000,
                end_ms=40_000,
                angle_count=1,
                reason="review",
            )
        ]

        recovered, remaining = _recover_scene_review_segments(
            review, [(30_000, 0.9)], floor_ms=4_000, ceiling_ms=60_000,
            median_play_ms=40_000)

        assert recovered == []
        assert remaining == review


class TestWeakSceneSecondPass:
    @staticmethod
    def _profile() -> FilmProfile:
        return FilmProfile(
            modal_angles=1,
            modal_share=0.9,
            median_play_ms=40_000,
            max_play_ms=60_000,
            calibrated=True,
        )

    @staticmethod
    def _long_review() -> list[UnclassifiedSegment]:
        return [
            UnclassifiedSegment(
                start_ms=0,
                end_ms=100_000,
                angle_count=1,
                reason="review",
            )
        ]

    def test_scene_metadata_parser_pairs_timestamp_and_score(self):
        output = "\n".join([
            "frame:0 pts:44044 pts_time:43.043",
            "lavfi.scene_score=2.330000e-01",
            "frame:1 pts:44557 pts_time:43.544 "
            "lavfi.scene_score=0.518",
        ])

        assert _parse_scene_events(output) == [
            (43_043, 0.233),
            (43_544, 0.518),
        ]

    def test_scene_metadata_parser_ignores_malformed_score(self):
        output = (
            "frame:0 pts_time:40.000\n"
            "lavfi.scene_score=not-a-number"
        )

        assert _parse_scene_events(output) == []

    def test_guarded_pass_runs_at_weak_threshold(self, monkeypatch, tmp_path):
        calls = []

        def fake_analysis(
                ffmpeg_path, source, threshold, _process_started=None):
            calls.append((ffmpeg_path, source, threshold))
            return (
                "frame:0 pts:0 pts_time:40.000\n"
                "lavfi.scene_score=0.250000",
                "",
            )

        monkeypatch.setattr(detector, "_run_ffmpeg_analysis", fake_analysis)
        source = tmp_path / "film.mp4"

        recovered, remaining = _try_weak_scene_recovery(
            "ffmpeg",
            source,
            self._long_review(),
            self._profile(),
            scene_threshold=0.35,
            floor_ms=4_000,
        )

        assert calls == [("ffmpeg", source, 0.15)]
        assert [(play.start_ms, play.end_ms) for play in recovered] == [
            (0, 40_000),
        ]
        assert [(item.start_ms, item.end_ms) for item in remaining] == [
            (40_000, 100_000),
        ]

    def test_second_pass_failure_keeps_primary_review(
            self, monkeypatch, tmp_path):
        review = self._long_review()

        def fail_analysis(*_args):
            raise TapeSiftError("weak pass failed")

        monkeypatch.setattr(detector, "_run_ffmpeg_analysis", fail_analysis)

        recovered, remaining = _try_weak_scene_recovery(
            "ffmpeg",
            tmp_path / "film.mp4",
            review,
            self._profile(),
            scene_threshold=0.35,
            floor_ms=4_000,
        )

        assert recovered == []
        assert remaining == review

    def test_unexpected_secondary_error_keeps_primary_review(
            self, monkeypatch, tmp_path):
        review = self._long_review()
        monkeypatch.setattr(
            detector,
            "_run_ffmpeg_analysis",
            lambda *_args: (
                "frame:0 pts_time:40.000\nlavfi.scene_score=0.25",
                "",
            ),
        )

        def broken_parser(_output):
            raise RuntimeError("malformed metadata")

        monkeypatch.setattr(detector, "_parse_scene_events", broken_parser)

        recovered, remaining = _try_weak_scene_recovery(
            "ffmpeg",
            tmp_path / "film.mp4",
            review,
            self._profile(),
            scene_threshold=0.35,
            floor_ms=4_000,
        )

        assert recovered == []
        assert remaining == review

    @pytest.mark.parametrize(
        ("review", "profile", "threshold"),
        [
            ([], _profile(), 0.35),
            (_long_review(), FilmProfile(1, 0.0, 0, 90_000, False), 0.35),
            (_long_review(), _profile(), 0.15),
            (
                [UnclassifiedSegment(0, 50_000, 1, "short review")],
                _profile(),
                0.35,
            ),
        ],
    )
    def test_guard_abstains_without_running_ffmpeg(
            self, review, profile, threshold, monkeypatch, tmp_path):
        def forbidden_analysis(*_args):
            raise AssertionError("weak FFmpeg pass should not run")

        monkeypatch.setattr(detector, "_run_ffmpeg_analysis",
                            forbidden_analysis)

        recovered, remaining = _try_weak_scene_recovery(
            "ffmpeg",
            tmp_path / "film.mp4",
            review,
            profile,
            scene_threshold=threshold,
            floor_ms=4_000,
        )

        assert recovered == []
        assert remaining == review


class TestWeakSceneIntegration:
    @staticmethod
    def _scene_cut_output(cuts: list[int]) -> str:
        return "\n".join(
            f"frame:{index} pts_time:{cut / 1000:.3f}"
            for index, cut in enumerate(cuts)
        )

    @classmethod
    def _calibrated_scene_output(cls, *, include_long_review: bool) -> str:
        cuts: list[int] = []
        cursor = 0
        for _ in range(8):
            cursor += 30_000
            cuts.append(cursor)
            cursor += 2_000
            cuts.append(cursor)
        if include_long_review:
            cursor += 80_000
            cuts.append(cursor)
        return cls._scene_cut_output(cuts)

    def test_scene_review_is_the_only_path_that_invokes_recovery(
            self, monkeypatch, tmp_path):
        source = tmp_path / "film.mp4"
        source.touch()
        primary = self._calibrated_scene_output(include_long_review=True)
        weak = (
            "frame:0 pts_time:286.000\n"
            "lavfi.scene_score=0.250000"
        )
        calls = []

        def fake_analysis(
                _ffmpeg, _source, threshold, _process_started=None):
            calls.append(threshold)
            return (primary, "") if len(calls) == 1 else (weak, "")

        monkeypatch.setattr(
            detector, "_run_ffmpeg_analysis", fake_analysis)

        result = detect_plays(
            "ffmpeg", source, duration_ms=336_000)

        assert result.signal == "scene"
        assert calls == [0.35, 0.15]
        assert any(
            play.start_ms == 256_000 and play.end_ms == 286_000
            for play in result.plays
        )
        primary_signatures = [
            (
                play.start_ms,
                play.end_ms,
                play.angle_count,
                play.angle_starts,
                play.needs_review,
                play.review_reason,
            )
            for play in result.plays[:8]
        ]
        assert primary_signatures == [
            (start, start + 30_000, 1, [start], False, "")
            for start in range(0, 256_000, 32_000)
        ]
        assert [(item.start_ms, item.end_ms)
                for item in result.unclassified] == [(286_000, 336_000)]
        partitions = sorted(
            [
                (play.start_ms, play.end_ms)
                for play in result.plays
                if play.start_ms >= 256_000
            ]
            + [
                (item.start_ms, item.end_ms)
                for item in result.unclassified
                if item.start_ms >= 256_000
            ]
        )
        assert partitions == [(256_000, 286_000), (286_000, 336_000)]
        assert sum(end - start for start, end in partitions) == 80_000
        assert all(
            right_start >= left_end
            for (_left_start, left_end), (right_start, _right_end)
            in zip(partitions, partitions[1:])
        )

    @pytest.mark.parametrize(
        ("stdout", "stderr", "expected_signal"),
        [
            ("", "", "none"),
            ("frame:0 pts_time:50.000", "black_start:20 black_end:20.2",
             "mixed"),
            ("", "black_start:20 black_end:20.2", "black"),
        ],
    )
    def test_non_scene_signals_never_invoke_recovery(
            self, stdout, stderr, expected_signal,
            monkeypatch, tmp_path):
        source = tmp_path / "film.mp4"
        source.touch()
        monkeypatch.setattr(
            detector,
            "_run_ffmpeg_analysis",
            lambda *_args: (stdout, stderr),
        )

        def forbidden_recovery(*_args, **_kwargs):
            raise AssertionError("weak recovery is scene-only")

        monkeypatch.setattr(
            detector, "_try_weak_scene_recovery", forbidden_recovery)

        result = detect_plays(
            "ffmpeg", source, duration_ms=100_000)

        assert result.signal == expected_signal

    def test_clean_scene_result_does_not_run_a_second_pass(
            self, monkeypatch, tmp_path):
        source = tmp_path / "film.mp4"
        source.touch()
        primary = self._calibrated_scene_output(include_long_review=False)
        monkeypatch.setattr(
            detector,
            "_run_ffmpeg_analysis",
            lambda *_args: (primary, ""),
        )

        def forbidden_recovery(*_args, **_kwargs):
            raise AssertionError("clean primary result needs no recovery")

        monkeypatch.setattr(
            detector, "_try_weak_scene_recovery", forbidden_recovery)

        result = detect_plays(
            "ffmpeg", source, duration_ms=256_000)

        assert result.signal == "scene"
        assert result.unclassified == []


class TestWeakSceneEvidence:
    def test_event_outside_review_range_cannot_strengthen_candidate(self):
        review = [
            UnclassifiedSegment(
                start_ms=0,
                end_ms=61_500,
                angle_count=1,
                reason="review",
            )
        ]

        recovered, remaining = _recover_scene_review_segments(
            review,
            [(60_000, 0.16), (62_000, 0.17)],
            floor_ms=1_000,
            ceiling_ms=60_000,
            median_play_ms=60_000,
        )

        assert recovered == []
        assert remaining == review


class TestBimodalScenePairing:
    """A clean two-mode scene pattern may pair two angles, and nothing else."""

    @staticmethod
    def _profile(*, modal_share=1.0, calibrated=True):
        return FilmProfile(
            modal_angles=1,
            modal_share=modal_share,
            median_play_ms=12_000 if calibrated else 0,
            max_play_ms=30_000 if calibrated else 90_000,
            calibrated=calibrated,
        )

    @staticmethod
    def _film(gap_kinds):
        durations = {"micro": 500, "card": 3_000, "cut": 0}
        plays = []
        spans = []
        cursor = 0
        for index in range(len(gap_kinds) + 1):
            start_ms = cursor
            end_ms = start_ms + 12_000
            plays.append(DetectedPlay(
                start_ms=start_ms,
                end_ms=end_ms,
                angle_count=1,
                angle_starts=[start_ms],
            ))
            spans.append((start_ms / 1000, end_ms / 1000))
            if index < len(gap_kinds):
                gap_ms = durations[gap_kinds[index]]
                if gap_ms:
                    spans.append((
                        end_ms / 1000,
                        (end_ms + gap_ms) / 1000,
                    ))
                cursor = end_ms + gap_ms
        return plays, spans

    @classmethod
    def _virginia_shape(cls):
        gaps = [
            "micro" if index % 2 == 0 else "card"
            for index in range(31)
        ]
        return cls._film(gaps)

    def test_alternating_modes_produce_two_angle_review_clips(self):
        plays, spans = self._virginia_shape()

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=60_000)

        assert pair_count == 16
        assert len(repaired) == 16
        assert all(play.angle_count == 2 for play in repaired)
        assert all(play.needs_review for play in repaired)
        assert all(
            play.review_reason == detector.SCENE_PAIR_REVIEW_REASON
            for play in repaired
        )
        assert repaired[0].angle_starts == [
            plays[0].start_ms,
            plays[1].start_ms,
        ]
        assert repaired[0].end_ms == plays[1].end_ms

    def test_following_long_card_is_not_swallowed(self):
        plays, spans = self._virginia_shape()

        repaired, _pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=60_000)

        assert repaired[1].start_ms - repaired[0].end_ms == 3_000

    @pytest.mark.parametrize(
        "gap_kinds",
        [
            ["card"] * 31,
            ["micro"] * 31,
        ],
    )
    def test_non_bimodal_film_abstains(self, gap_kinds):
        plays, spans = self._film(gap_kinds)

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=60_000)

        assert repaired is plays
        assert pair_count == 0

    @pytest.mark.parametrize(
        "profile",
        [
            _profile(modal_share=0.89),
            _profile(calibrated=False),
            FilmProfile(2, 0.95, 28_000, 60_000, True),
            FilmProfile(1, 0.95, 21_000, 30_000, True),
        ],
    )
    def test_weak_profile_abstains(self, profile):
        plays, spans = self._virginia_shape()

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, profile, pair_ceiling_ms=60_000)

        assert repaired is plays
        assert pair_count == 0

    def test_two_flash_micro_transition_remains_pairable(self):
        plays, spans = self._virginia_shape()
        # Some Virginia transitions contain two brief graphic flashes.
        gap_start, gap_end = spans[1]
        midpoint = (gap_start + gap_end) / 2
        spans[1:2] = [(gap_start, midpoint), (midpoint, gap_end)]

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=60_000)

        assert pair_count == 16
        assert repaired[0].angle_count == 2

    def test_three_part_micro_transition_keeps_only_that_location_unpaired(
            self):
        plays, spans = self._virginia_shape()
        gap_start, gap_end = spans[1]
        first = gap_start + (gap_end - gap_start) / 3
        second = gap_start + 2 * (gap_end - gap_start) / 3
        spans[1:2] = [
            (gap_start, first),
            (first, second),
            (second, gap_end),
        ]

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=60_000)

        assert pair_count == 15
        assert repaired[0] is plays[0]
        assert repaired[1] is plays[1]

    def test_miami_fragment_topology_does_not_leak_to_general_pairing(self):
        plays, spans = self._virginia_shape()
        original_gap_end_ms = plays[1].start_ms
        gap_start_ms = plays[0].end_ms

        # Match the measured Miami transition exactly: 201 + 33 + 267 ms.
        spans_ms = [
            (round(start_s * 1000), round(end_s * 1000))
            for start_s, end_s in spans
        ]
        shifted_tail = [
            (start_ms + 1, end_ms + 1)
            for start_ms, end_ms in spans_ms[2:]
        ]
        spans_ms[1:] = [
            (gap_start_ms, gap_start_ms + 201),
            (gap_start_ms + 201, gap_start_ms + 234),
            (gap_start_ms + 234, gap_start_ms + 501),
        ] + shifted_tail
        spans = [
            (start_ms / 1000, end_ms / 1000)
            for start_ms, end_ms in spans_ms
        ]
        for play in plays[1:]:
            assert play.start_ms >= original_gap_end_ms
            play.start_ms += 1
            play.end_ms += 1
            play.angle_starts = [
                start_ms + 1 for start_ms in play.angle_starts
            ]

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=60_000)

        assert pair_count == 15
        assert repaired[0] is plays[0]
        assert repaired[1] is plays[1]

    def test_multi_span_long_card_remains_a_safe_outer_boundary(self):
        plays, spans = self._virginia_shape()
        # The first long card may contain several flashes. It remains outside
        # both neighboring pairs and is never included in either output.
        card_start, card_end = spans[3]
        first_cut = card_start + 2.0
        second_cut = first_cut + 0.5
        spans[3:4] = [
            (card_start, first_cut),
            (first_cut, second_cut),
            (second_cut, card_end),
        ]

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=60_000)

        assert pair_count == 16
        assert repaired[1].start_ms - repaired[0].end_ms == 3_000

    def test_existing_hard_cut_may_bound_two_independent_pairs(self):
        gaps = [
            "micro" if index % 2 == 0 else "card"
            for index in range(31)
        ]
        gaps[1] = "cut"
        plays, spans = self._film(gaps)

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=60_000)

        assert pair_count == 16
        assert repaired[0].end_ms == plays[1].end_ms
        assert repaired[1].start_ms == plays[2].start_ms
        assert repaired[1].end_ms == plays[3].end_ms

    def test_unsorted_or_overlapping_plays_abstain(self):
        plays, spans = self._virginia_shape()
        plays[2].start_ms = plays[1].start_ms - 1

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=60_000)

        assert repaired is plays
        assert pair_count == 0

    def test_malformed_angle_start_keeps_that_pair_unmodified(self):
        plays, spans = self._virginia_shape()
        plays[0].angle_starts = []

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=60_000)

        assert pair_count == 15
        assert repaired[0] is plays[0]
        assert repaired[1] is plays[1]

    def test_too_few_pairs_after_ceiling_filter_abstains_entirely(self):
        plays, spans = self._virginia_shape()

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=20_000)

        assert repaired is plays
        assert pair_count == 0

    def test_low_repaired_output_share_abstains(self):
        # Eight isolated pairs among 24 input plays pass the raw count gate,
        # but the repaired output would still be mostly one-angle clips.
        gaps = [
            "micro" if index % 2 == 0 else "card"
            for index in range(15)
        ] + ["card"] * 8
        plays, spans = self._film(gaps)

        repaired, pair_count = detector._pair_bimodal_scene_angles(
            plays, spans, self._profile(), pair_ceiling_ms=60_000)

        assert repaired is plays
        assert pair_count == 0

    def test_wrapper_recomputes_two_angle_profile_and_preserves_coverage(self):
        plays, spans = self._virginia_shape()
        original_coverage = sum(play.duration_ms for play in plays)

        repaired, profile, pair_count = detector._try_scene_angle_pairing(
            plays,
            [],
            spans,
            self._profile(),
            signal="scene",
            scene_threshold=0.35,
            separator_max_s=5.0,
            max_play_ms=90_000,
            duration_ms=plays[-1].end_ms,
        )

        assert pair_count == 16
        assert profile.calibrated is True
        assert profile.modal_angles == 2
        assert profile.max_play_ms == 60_000
        swallowed = sum(
            plays[index + 1].start_ms - plays[index].end_ms
            for index in range(0, len(plays), 2)
        )
        assert sum(play.duration_ms for play in repaired) == (
            original_coverage + swallowed)
        assert detector._scene_pairing_is_valid(
            plays,
            repaired,
            pair_count,
            duration_ms=plays[-1].end_ms,
        )

    @pytest.mark.parametrize(
        ("signal", "threshold", "separator"),
        [
            ("mixed", 0.35, 5.0),
            ("black", 0.35, 5.0),
            ("none", 0.35, 5.0),
            ("scene", 0.30, 5.0),
            ("scene", 0.35, 4.5),
        ],
    )
    def test_non_default_or_non_scene_wrapper_abstains(
            self, signal, threshold, separator):
        plays, spans = self._virginia_shape()
        profile = self._profile()

        repaired, returned_profile, pair_count = (
            detector._try_scene_angle_pairing(
                plays,
                [],
                spans,
                profile,
                signal=signal,
                scene_threshold=threshold,
                separator_max_s=separator,
                max_play_ms=90_000,
                duration_ms=plays[-1].end_ms,
            )
        )

        assert repaired is plays
        assert returned_profile is profile
        assert pair_count == 0

    @pytest.mark.parametrize("blocker", ["review", "play-review"])
    def test_review_state_prevents_pairing(self, blocker):
        plays, spans = self._virginia_shape()
        review = []
        if blocker == "review":
            review = [UnclassifiedSegment(0, 10_000, 1, "review")]
        else:
            plays[0].needs_review = True
        profile = self._profile()

        repaired, returned_profile, pair_count = (
            detector._try_scene_angle_pairing(
                plays,
                review,
                spans,
                profile,
                signal="scene",
                scene_threshold=0.35,
                separator_max_s=5.0,
                max_play_ms=90_000,
                duration_ms=plays[-1].end_ms,
            )
        )

        assert repaired is plays
        assert returned_profile is profile
        assert pair_count == 0

    @pytest.mark.parametrize("failure", ["profile", "validation"])
    def test_post_pairing_failure_returns_exact_primary_result(
            self, failure, monkeypatch):
        plays, spans = self._virginia_shape()
        profile = self._profile()
        if failure == "profile":
            monkeypatch.setattr(
                detector,
                "profile_film",
                lambda _plays: (_ for _ in ()).throw(
                    RuntimeError("profile failed")),
            )
        else:
            monkeypatch.setattr(
                detector,
                "_scene_pairing_is_valid",
                lambda *_args, **_kwargs: False,
            )

        repaired, returned_profile, pair_count = (
            detector._try_scene_angle_pairing(
                plays,
                [],
                spans,
                profile,
                signal="scene",
                scene_threshold=0.35,
                separator_max_s=5.0,
                max_play_ms=90_000,
                duration_ms=plays[-1].end_ms,
            )
        )

        assert repaired is plays
        assert returned_profile is profile
        assert pair_count == 0

    def test_detect_integration_adds_no_decode_and_keeps_separator_count(
            self, monkeypatch, tmp_path):
        expected_plays, spans = self._virginia_shape()
        cuts_ms = [round(end * 1000) for _start, end in spans[:-1]]
        stdout = "\n".join(
            f"frame:{index} pts_time:{cut / 1000:.3f}"
            for index, cut in enumerate(cuts_ms)
        )
        calls = []

        def fake_analysis(
                _ffmpeg, _source, threshold, _process_started=None):
            calls.append(threshold)
            return stdout, ""

        monkeypatch.setattr(
            detector, "_run_ffmpeg_analysis", fake_analysis)
        source = tmp_path / "virginia-shape.mp4"
        source.touch()

        result = detect_plays(
            "ffmpeg",
            source,
            duration_ms=expected_plays[-1].end_ms,
        )

        assert calls == [0.35]
        assert result.signal == "scene"
        assert len(result.plays) == 16
        assert result.separators_found == 31
        assert result.unclassified == []
        assert result.profile is not None
        assert result.profile.modal_angles == 2
        assert all(play.needs_review for play in result.plays)

    def test_candidate_must_leave_a_usable_remainder(self):
        review = [
            UnclassifiedSegment(
                start_ms=0,
                end_ms=60_001,
                angle_count=1,
                reason="review",
            )
        ]

        recovered, remaining = _recover_scene_review_segments(
            review,
            [(60_000, 0.9)],
            floor_ms=4_000,
            ceiling_ms=60_000,
            median_play_ms=60_000,
        )

        assert recovered == []
        assert remaining == review

    def test_isolated_low_score_event_is_not_enough_evidence(self):
        review = [
            UnclassifiedSegment(
                start_ms=0,
                end_ms=100_000,
                angle_count=1,
                reason="review",
            )
        ]

        recovered, remaining = _recover_scene_review_segments(
            review, [(40_000, 0.18)], floor_ms=4_000,
            ceiling_ms=60_000, median_play_ms=40_000)

        assert recovered == []
        assert remaining == review

    def test_two_low_score_events_in_one_cluster_are_enough_evidence(self):
        review = [
            UnclassifiedSegment(
                start_ms=0,
                end_ms=100_000,
                angle_count=1,
                reason="review",
            )
        ]

        recovered, remaining = _recover_scene_review_segments(
            review, [(40_000, 0.16), (40_500, 0.17)],
            floor_ms=4_000, ceiling_ms=60_000, median_play_ms=40_000)

        assert [(play.start_ms, play.end_ms) for play in recovered] == [
            (0, 40_000),
        ]
        assert remaining[0].start_ms == 40_000

    def test_similarly_plausible_boundaries_are_left_ambiguous(self):
        review = [
            UnclassifiedSegment(
                start_ms=0,
                end_ms=100_000,
                angle_count=1,
                reason="review",
            )
        ]

        recovered, remaining = _recover_scene_review_segments(
            review, [(38_000, 0.3), (42_000, 0.3)],
            floor_ms=4_000, ceiling_ms=60_000, median_play_ms=40_000)

        assert recovered == []
        assert remaining == review

    def test_candidate_cannot_exceed_primary_ceiling(self):
        review = [
            UnclassifiedSegment(
                start_ms=0,
                end_ms=100_000,
                angle_count=1,
                reason="review",
            )
        ]

        recovered, remaining = _recover_scene_review_segments(
            review, [(64_000, 0.9)], floor_ms=4_000,
            ceiling_ms=60_000, median_play_ms=60_000)

        assert recovered == []
        assert remaining == review
