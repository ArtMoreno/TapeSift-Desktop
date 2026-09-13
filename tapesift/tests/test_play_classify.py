"""Classifying detected runs: plays, splits, and film that needs review.

The rule this file exists to defend: **detection never discards footage.**
The old classifier kept runs between 4 and 90 seconds and silently dropped
everything else, which is how whole films came back empty with no error and
how a real play could vanish without a trace.

The second rule: a split is only performed when it is not a guess. Cutting a
merged play in the wrong place produces two half-plays, which is worse than
handing the segment to the user intact.
"""

from __future__ import annotations

import pytest

from tapesift.services import play_detect_service as P


def run(start_s: float, end_s: float, angles: int = 2) -> P.DetectedPlay:
    """A detected run whose angle boundaries divide it evenly."""
    start, end = int(start_s * 1000), int(end_s * 1000)
    step = (end - start) // angles
    return P.DetectedPlay(start_ms=start, end_ms=end, angle_count=angles,
                          angle_starts=[start + i * step for i in range(angles)])


def typical_film(n: int = 20, seconds: float = 42.0) -> list[P.DetectedPlay]:
    """A film that looks like Notre Dame: two angles, ~42s per play."""
    return [run(i * 60.0, i * 60.0 + seconds, angles=2) for i in range(n)]


class TestNothingIsDiscarded:
    def test_every_run_is_either_a_play_or_reviewable(self):
        runs = typical_film() + [run(9000, 9002, angles=1),      # 2s fragment
                                 run(9100, 9400, angles=3)]      # 300s blob
        profile = P.profile_film(runs)
        plays, review = P.classify_runs(runs, profile)
        accounted = sum(len(p.angle_starts) or 1 for p in plays) + len(review)
        assert accounted >= len(runs)
        assert review, "out-of-range runs must surface, not disappear"

    def test_a_short_fragment_is_kept_for_review(self):
        runs = typical_film() + [run(9000, 9002, angles=1)]
        plays, review = P.classify_runs(runs, P.profile_film(runs))
        assert any("too short" in s.reason for s in review)
        assert not any(p.duration_ms < 4000 for p in plays)

    def test_film_with_no_recognisable_plays_still_returns_its_footage(self):
        """The Pittsburgh case: almost nothing parses, so almost everything
        must come back for review rather than an empty result."""
        runs = [run(i * 200.0, i * 200.0 + 180.0, angles=3) for i in range(6)]
        plays, review = P.classify_runs(runs, P.profile_film(runs))
        assert len(plays) + len(review) >= len(runs)
        assert review


class TestCeilingComesFromTheFilm:
    def test_ceiling_scales_with_camera_angles(self):
        two = P.profile_film(typical_film())
        three = P.profile_film([run(i * 90.0, i * 90.0 + 63.0, angles=3)
                                for i in range(20)])
        assert three.max_play_ms > two.max_play_ms

    def test_ceiling_is_far_below_the_old_ninety_seconds(self):
        """90s was never a football number; two angles cannot fill it."""
        profile = P.profile_film(typical_film())
        assert profile.max_play_ms < 90_000

    def test_too_few_runs_falls_back_instead_of_calibrating_on_noise(self):
        profile = P.profile_film([run(0, 42), run(60, 102)])
        assert not profile.calibrated

    def test_seconds_per_angle_flags_an_impossible_angle_length(self):
        """One 'angle' lasting 39s is two angles the detector merged."""
        profile = P.profile_film([run(i * 90.0, i * 90.0 + 39.0, angles=1)
                                  for i in range(20)])
        assert profile.angle_count_suspect


class TestSplittingOnlyWhenCertain:
    def test_a_clean_double_is_split_into_two_plays(self):
        runs = typical_film() + [run(9000, 9084, angles=4)]   # 84s, 4 angles
        plays, review = P.classify_runs(runs, P.profile_film(runs))
        assert len(plays) == 22, "the 4-angle run should become two plays"
        assert not review

    def test_split_pieces_inherit_real_angle_boundaries(self):
        merged = run(9000, 9084, angles=4)
        runs = typical_film() + [merged]
        plays, _ = P.classify_runs(runs, P.profile_film(runs))
        new = [p for p in plays if p.start_ms >= 9_000_000]
        assert [p.start_ms for p in new] == [merged.angle_starts[0],
                                             merged.angle_starts[2]]

    def test_an_odd_angle_count_is_split_roughly_and_flagged(self):
        """7 angles is not a whole number of 2-angle plays - but at 157s it
        is certainly several, and one 157s clip has to be cut by hand
        anyway. Cut it roughly on real camera changes and flag it."""
        runs = typical_film() + [run(9000, 9157, angles=7)]
        plays, review = P.classify_runs(runs, P.profile_film(runs))
        rough = [p for p in plays if p.needs_review]
        assert len(rough) > 1, "a 157s block should not survive whole"
        assert all(p.review_reason for p in rough)
        assert not review, "it was divided, so nothing is left unclassified"

    def test_rough_pieces_are_play_sized(self):
        runs = typical_film() + [run(9000, 9157, angles=7)]
        plays, _ = P.classify_runs(runs, P.profile_film(runs))
        rough = [p.duration_ms / 1000 for p in plays if p.needs_review]
        assert all(4 <= d <= 70 for d in rough), rough

    def test_a_long_block_is_split_even_when_angles_are_untrustworthy(self):
        """Pittsburgh: the angle count is unreliable, but a 160s block is
        unusable. Rough pieces on real boundaries beat one giant clip."""
        runs = [run(i * 200.0, i * 200.0 + 39.0, angles=1) for i in range(20)]
        runs.append(run(9000, 9160, angles=4))
        profile = P.profile_film(runs)
        assert profile.angle_count_suspect
        plays, _ = P.classify_runs(runs, profile)
        assert len([p for p in plays if p.needs_review]) > 1

    def test_a_long_block_with_no_camera_changes_stays_for_review(self):
        """With one angle there is nothing to cut on, so guessing a time to
        cut at would be inventing a boundary that does not exist."""
        runs = typical_film() + [run(9000, 9200, angles=1)]
        _, review = P.classify_runs(runs, P.profile_film(runs))
        assert len(review) == 1 and review[0].angle_count == 1

    def test_no_split_when_a_piece_would_not_look_like_a_play(self):
        """A long run with lopsided angles must not be forced into pieces."""
        merged = P.DetectedPlay(start_ms=9_000_000, end_ms=9_120_000,
                                angle_count=4,
                                angle_starts=[9_000_000, 9_002_000,
                                              9_004_000, 9_006_000])
        runs = typical_film() + [merged]
        _, review = P.classify_runs(runs, P.profile_film(runs))
        assert len(review) == 1

    def test_a_weakly_held_angle_pattern_does_not_authorise_splits(self):
        mixed = ([run(i * 90.0, i * 90.0 + 42.0, angles=2) for i in range(6)] +
                 [run(600 + i * 90.0, 600 + i * 90.0 + 40.0, angles=3)
                  for i in range(5)] +
                 [run(1500 + i * 90.0, 1500 + i * 90.0 + 38.0, angles=1)
                  for i in range(5)])
        mixed.append(run(9000, 9084, angles=4))
        profile = P.profile_film(mixed)
        assert profile.modal_share < 0.8
        _, review = P.classify_runs(mixed, profile)
        assert any(s.angle_count == 4 for s in review)


class TestUncalibratedFilmsBehaveLikeBefore:
    def test_uncalibrated_profile_uses_the_default_ceiling(self):
        profile = P.FilmProfile(modal_angles=1, modal_share=0.0,
                                median_play_ms=0,
                                max_play_ms=int(P.DEFAULT_MAX_PLAY_S * 1000),
                                calibrated=False)
        runs = [run(0, 42), run(60, 200, angles=2)]
        plays, review = P.classify_runs(runs, profile)
        assert len(plays) == 1 and len(review) == 1
