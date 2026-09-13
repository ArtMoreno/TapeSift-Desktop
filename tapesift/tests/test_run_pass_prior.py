"""Run or pass from the situation alone - the floor a video model must beat."""

from __future__ import annotations

import pytest

from tapesift.models.clip import Clip
from tapesift.services.run_pass_prior_service import (
    MIN_PLAYS_TO_FIT, RunPassPrior, evaluate, fit_for_project, situation_for,
    truth_for)


def _clip(down_distance="", quarter="Q2", ball_on="Own 30", run_pass="",
          index=0):
    details = {"quarter": quarter, "ball_on": ball_on}
    if down_distance:
        details["down_distance"] = down_distance
    if run_pass:
        details["run_pass"] = run_pass
    return Clip(start_ms=index * 1000, end_ms=index * 1000 + 900,
                clip_number=index + 1, details=details)


class TestReadingTheSituation:
    @pytest.mark.parametrize("logged,down,to_go", [
        ("3rd & 7", 3, 7.0),
        ("1st & 10", 1, 10.0),
        ("4th & 1", 4, 1.0),
        ("2nd & Goal", 2, 3.0),
    ])
    def test_a_logged_down_and_distance_is_read(self, logged, down, to_go):
        s = situation_for(_clip(logged))
        assert (s.down, s.to_go) == (down, to_go)

    def test_goal_to_go_is_short_yardage_whatever_the_yard_line(self):
        assert situation_for(_clip("2nd & Goal")).goal_to_go

    def test_overtime_reads_as_a_quarter(self):
        assert situation_for(_clip("1st & 10", quarter="OT")).quarter == 5

    def test_an_unlogged_play_is_not_a_situation(self):
        assert not situation_for(_clip()).known


class TestFootballReads:
    """These are the tendencies any coach would state without watching."""

    @staticmethod
    def _call(down_distance, ball_on="Own 30"):
        return RunPassPrior.default().predict(
            _clip(down_distance, ball_on=ball_on))[0]

    @pytest.mark.parametrize("situation,expected", [
        ("3rd & 12", "pass"),
        ("3rd & 15", "pass"),
        ("4th & 8", "pass"),
        ("2nd & 9", "pass"),
        ("3rd & 1", "run"),
        ("2nd & 1", "run"),
        ("1st & 10", "run"),
    ])
    def test_the_obvious_reads_are_right(self, situation, expected):
        assert self._call(situation) == expected

    def test_first_and_ten_is_not_a_passing_down(self):
        """10 yards is the standard state of football, not long yardage.

        Bucketing distance without regard to down made the model call the
        most common down in the game a pass.
        """
        assert self._call("1st & 10") == "run"

    def test_first_and_long_after_a_penalty_still_reads_pass(self):
        assert self._call("1st & 15") == "pass"

    def test_goal_line_reads_run(self):
        assert self._call("2nd & Goal", ball_on="Opp 3") == "run"

    def test_third_and_long_is_the_most_confident_read(self):
        model = RunPassPrior.default()
        long_conf = model.predict(_clip("3rd & 12"))[1]
        for other in ("1st & 10", "2nd & 5", "3rd & 3"):
            assert long_conf > model.predict(_clip(other))[1]

    def test_no_situation_means_no_opinion(self):
        """A confident answer from no information is how trust is lost."""
        assert RunPassPrior.default().predict(_clip()) == ("", 0.0)


class TestTruth:
    def test_an_explicit_call_is_trainable(self):
        assert truth_for(_clip("3rd & 7", run_pass="Pass")) == "pass"

    def test_an_unlabelled_play_is_not_truth(self):
        assert truth_for(_clip("3rd & 7")) == ""


class TestFitting:
    @staticmethod
    def _season(pass_on_third_and_long=True, count=400):
        """A synthetic team with three situations and both classes.

        Both classes on purpose: a single-class sample is refused by fit,
        correctly, and an earlier version of this fixture made every play
        a run in the odd variant and tripped that guard rather than the
        behaviour it meant to test.
        """
        clips = []
        for i in range(count):
            slot = i % 3
            if slot == 0:
                dd = "3rd & 12"
                call = "Pass" if pass_on_third_and_long else "Run"
            elif slot == 1:
                dd = "2nd & 1"
                call = "Run"
            else:
                dd = "1st & 10"
                call = "Pass"
            clips.append(_clip(dd, run_pass=call, index=i))
        return clips

    def test_a_handful_of_plays_does_not_move_the_priors(self):
        """Fitting on five plays produces confident nonsense."""
        before = RunPassPrior.default()
        after = before.fit(self._season(count=6))
        assert after.weights == before.weights
        assert after.fitted_on == 0

    def test_enough_plays_fits(self):
        fitted = RunPassPrior.default().fit(self._season())
        assert fitted.fitted_on >= MIN_PLAYS_TO_FIT

    def test_one_game_is_not_enough_to_fit(self):
        """Measured: 56 real plays fitted a model that said pass to all 56."""
        assert RunPassPrior.default().fit(
            self._season(count=56)).fitted_on == 0

    def test_a_model_that_collapses_to_one_class_is_refused(self):
        """The cheapest way to cut loss on a skewed sample, and it is useless.

        Nine passes to one run, with the situation carrying no signal at
        all - a fitted model would answer pass to everything and score 90%.
        The priors are kept instead.
        """
        lopsided = []
        for i in range(400):
            call = "Run" if i % 10 == 0 else "Pass"
            lopsided.append(_clip("2nd & 5", run_pass=call, index=i))
        assert RunPassPrior.default().fit(lopsided).fitted_on == 0

    def test_a_team_that_runs_on_third_and_long_is_learned(self):
        """The priors say pass. This team does not. The team wins."""
        odd = self._season(pass_on_third_and_long=False)
        assert RunPassPrior.default().predict(_clip("3rd & 12"))[0] == "pass"
        fitted = RunPassPrior.default().fit(odd)
        assert fitted.predict(_clip("3rd & 12"))[0] == "run"

    def test_fitting_does_not_erase_reads_the_data_never_covered(self):
        """Regularised toward football, so unseen situations keep sense."""
        fitted = RunPassPrior.default().fit(self._season())
        assert fitted.predict(_clip("2nd & Goal", ball_on="Opp 2"))[0] == "run"


class TestScorecard:
    def test_majority_is_reported_beside_accuracy(self):
        """70% on a 70%-pass sample is a model that learned to say pass."""
        clips = [_clip("3rd & 12", run_pass="Pass", index=i) for i in range(7)]
        clips += [_clip("2nd & 1", run_pass="Run", index=7 + i)
                  for i in range(3)]
        card = evaluate(clips)
        assert card.plays == 10
        assert card.majority_correct == 7
        assert card.accuracy >= card.majority_accuracy

    def test_balanced_accuracy_catches_a_one_class_model(self):
        clips = [_clip("3rd & 12", run_pass="Pass", index=i) for i in range(9)]
        clips += [_clip("3rd & 12", run_pass="Run", index=9)]
        card = evaluate(clips)
        assert card.pass_recall == 1.0
        assert card.run_recall == 0.0
        assert card.balanced_accuracy == 0.5

    def test_unlogged_and_unlabelled_plays_are_not_scored(self):
        assert evaluate([_clip(), _clip("3rd & 7")]).plays == 0

    def test_the_default_model_beats_majority_on_a_mixed_sample(self):
        clips = []
        for i in range(30):
            clips.append(_clip("3rd & 12", run_pass="Pass", index=i))
        for i in range(30):
            clips.append(_clip("2nd & 1", run_pass="Run", index=30 + i))
        card = evaluate(clips)
        assert card.beats_majority
        assert card.balanced_accuracy > 0.9


def test_a_project_with_no_labels_still_gets_a_usable_model():
    model = fit_for_project([_clip("3rd & 12"), _clip("2nd & 1")])
    assert model.fitted_on == 0
    assert model.predict(_clip("3rd & 12"))[0] == "pass"
