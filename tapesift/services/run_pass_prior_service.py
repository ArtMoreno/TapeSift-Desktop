"""Run or pass from the situation alone, before any video is looked at.

Two prior attempts at run/pass both measured motion and both landed on
50.0% selective accuracy on a held-out game - a coin flip on a binary
task. The roadmap's own reading is that they measured motion magnitude
and timing rather than portable football structure.

This measures neither. It uses only what an analyst has already typed:
the down, the distance, where the ball is, and the quarter. A coach reads
3rd and 8 as a pass without watching anything, and that read travels
between games, cameras and broadcasts in a way that pixel motion does
not.

Its job is to be the floor. Right now the research has no floor except
majority class, which is why 79.2% on one game looked like progress while
the same model scored 50.0% on another. Any video model has to beat this,
on the same games, or it has not earned its complexity.

It ships with football's own tendencies as its starting weights, so it is
useful on a project with zero labelled plays. Once a project has enough
logged plays of its own it fits to them, because a team's tendencies are
not the league's.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from tapesift.models.clip import Clip
from tapesift.services.run_pass_label_service import (
    PASS, RUN, resolve_run_pass_label)

#: Below this many labelled plays a project keeps the shipped priors.
#: Measured, not guessed: 56 plays from one real game fitted a model that
#: answered "pass" to all 56 - 76.8% accuracy, the majority rate exactly,
#: and zero of thirteen runs found. Fifteen weights need far more than one
#: game, and one game is what 40 allowed.
MIN_PLAYS_TO_FIT = 150
#: A fitted model has to actually separate the classes. Anything at or
#: below this on the training data has collapsed to the majority answer.
MIN_FITTED_BALANCED_ACCURACY = 0.55
#: Distance in yards past which "long" stops meaning anything more.
LONG_YARDS = 15.0


@dataclass(frozen=True)
class Situation:
    """What the analyst logged, as numbers a model can use."""

    down: int = 0            # 1-4, 0 when unlogged
    to_go: float = 0.0       # yards; goal-to-go carries its real distance
    goal_to_go: bool = False
    quarter: int = 0         # 1-4, 5 for OT, 0 when unlogged
    own_half: bool = True
    opponent_half: bool = False

    @property
    def known(self) -> bool:
        """Enough to say anything at all."""
        return self.down > 0

    def features(self) -> dict[str, float]:
        """Named features, so a fitted weight can be read and argued with."""
        # Distance means nothing on first down. A fresh set is 10 yards, so
        # "1st and 10" is the standard state of football, not a long-yardage
        # situation - bucketing it as long made the model call the most
        # common down in the game a pass. From second down on, distance is
        # the strongest read there is.
        informative = self.down >= 2
        short = 1.0 if self.to_go <= 2 else 0.0
        medium = 1.0 if informative and 3 <= self.to_go <= 6 else 0.0
        long_ = 1.0 if informative and self.to_go >= 7 else 0.0
        if self.down <= 1 and self.to_go >= 11:
            # First and long only exists after a penalty, and it is real.
            long_ = 1.0
        return {
            "bias": 1.0,
            "down_1": 1.0 if self.down == 1 else 0.0,
            "down_2": 1.0 if self.down == 2 else 0.0,
            "down_3": 1.0 if self.down == 3 else 0.0,
            "down_4": 1.0 if self.down == 4 else 0.0,
            "short": short,
            "medium": medium,
            "long": long_,
            "to_go_scaled": (
                min(self.to_go, LONG_YARDS) / LONG_YARDS
                if informative or self.to_go >= 11 else 0.0),
            "third_and_long": 1.0 if self.down == 3 and long_ else 0.0,
            "third_and_short": 1.0 if self.down == 3 and short else 0.0,
            "fourth_and_long": 1.0 if self.down == 4 and long_ else 0.0,
            "goal_to_go": 1.0 if self.goal_to_go else 0.0,
            "red_zone": 1.0 if self.opponent_half and self.to_go <= 10 else 0.0,
            "late": 1.0 if self.quarter >= 4 else 0.0,
        }


#: Positive weight leans pass, negative leans run. These are football's
#: tendencies, not one team's, and they are what a project starts with
#: before it has logged enough of its own plays to know better.
DEFAULT_WEIGHTS: dict[str, float] = {
    "bias": -0.15,           # slightly more runs than passes overall
    "down_1": -0.35,         # first down leans run
    "down_2": 0.0,
    "down_3": 0.55,          # third down leans pass
    "down_4": 0.30,
    "short": -1.10,          # short yardage is a run situation
    "medium": 0.25,
    "long": 0.95,            # long yardage is a pass situation
    "to_go_scaled": 0.70,
    "third_and_long": 0.90,  # the strongest read in football
    "third_and_short": -0.60,
    "fourth_and_long": 0.80,
    "goal_to_go": -0.55,     # inside the five, teams run
    "red_zone": -0.20,
    "late": 0.20,            # trailing teams throw, and most teams trail
}

_DOWN_WORDS = {
    "1": 1, "1st": 1, "first": 1,
    "2": 2, "2nd": 2, "second": 2,
    "3": 3, "3rd": 3, "third": 3,
    "4": 4, "4th": 4, "fourth": 4,
}


def _clean(value: object) -> str:
    return str(value or "").strip()


def truth_for(clip: Clip) -> str:
    """The analyst's own call, or "" when there is not one to learn from.

    Uses the existing resolver's trainable rule: an explicit Run or Pass
    with no contradicting evidence. A label derived from taxonomy, or one
    the resolver flagged as conflicting, is not truth - training on it
    would teach the model the app's own inferences back to itself.
    """
    resolution = resolve_run_pass_label(clip.details, clip.tags or ())
    if not resolution.trainable or resolution.label not in (RUN, PASS):
        return ""
    return resolution.label


def situation_for(clip: Clip) -> Situation:
    """Read a clip's logged situation. Missing parts stay missing."""
    details = clip.details or {}
    combined = _clean(details.get("down_distance")).lower()
    down = 0
    to_go = 0.0
    goal = False
    if combined:
        head, _, tail = combined.replace("&", " ").partition(" ")
        down = _DOWN_WORDS.get(head.strip(), 0)
        tail = tail.strip()
        if tail.startswith("g") or "goal" in tail:
            goal = True
            # Goal to go is short yardage whatever the exact yard line.
            to_go = 3.0
        else:
            digits = re.findall(r"\d+", tail)
            to_go = float(digits[0]) if digits else 0.0

    quarter_raw = _clean(details.get("quarter")).upper()
    quarter = 0
    if quarter_raw.startswith("OT"):
        quarter = 5
    else:
        digits = re.findall(r"\d", quarter_raw)
        quarter = int(digits[0]) if digits else 0

    spot = _clean(details.get("ball_on")).lower()
    opponent_half = spot.startswith("opp")
    own_half = spot.startswith("own") or not spot

    return Situation(
        down=down, to_go=to_go, goal_to_go=goal, quarter=quarter,
        own_half=own_half, opponent_half=opponent_half)


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


@dataclass
class RunPassPrior:
    """Probability that a play is a pass, from the situation alone."""

    weights: dict[str, float]
    fitted_on: int = 0

    @classmethod
    def default(cls) -> "RunPassPrior":
        return cls(weights=dict(DEFAULT_WEIGHTS))

    def pass_probability(self, situation: Situation) -> float:
        total = 0.0
        for name, value in situation.features().items():
            total += self.weights.get(name, 0.0) * value
        return _sigmoid(total)

    def predict(self, clip: Clip) -> tuple[str, float]:
        """(label, confidence). Confidence is distance from a coin flip.

        An unlogged situation returns no opinion rather than a guess -
        a confident answer from no information is the failure mode that
        makes an analyst stop trusting the whole feature.
        """
        situation = situation_for(clip)
        if not situation.known:
            return "", 0.0
        probability = self.pass_probability(situation)
        label = PASS if probability >= 0.5 else RUN
        return label, abs(probability - 0.5) * 2.0

    # ------------------------------------------------------------ fitting

    def fit(self, clips: list[Clip], *, epochs: int = 400,
            learning_rate: float = 0.28,
            l2: float = 0.02) -> "RunPassPrior":
        """Learn this project's tendencies, starting from football's.

        Regularised toward the shipped weights rather than toward zero, so
        a small sample bends the priors instead of replacing them. A team
        that runs on third and long should have to prove it.
        """
        rows = training_rows(clips)
        if len(rows) < MIN_PLAYS_TO_FIT:
            return self
        # Both classes, or there is nothing to separate.
        passes = sum(1 for _, is_pass in rows if is_pass)
        if passes == 0 or passes == len(rows):
            return self
        weights = dict(self.weights)
        prior = dict(self.weights)
        for _ in range(epochs):
            gradients: dict[str, float] = {k: 0.0 for k in weights}
            for features, is_pass in rows:
                total = sum(weights.get(k, 0.0) * v
                            for k, v in features.items())
                error = _sigmoid(total) - is_pass
                for name, value in features.items():
                    gradients[name] = gradients.get(name, 0.0) + error * value
            for name in weights:
                pull = l2 * (weights[name] - prior.get(name, 0.0))
                weights[name] -= learning_rate * (
                    gradients[name] / len(rows) + pull)

        # Refuse a model that answers one class to everything. On an
        # imbalanced sample that is the cheapest way to minimise loss and
        # it scores the majority rate, which reads as success and is not.
        fitted = RunPassPrior(weights=weights, fitted_on=len(rows))
        if fitted._balanced_accuracy_on(rows) < MIN_FITTED_BALANCED_ACCURACY:
            return self
        return fitted

    def _balanced_accuracy_on(
            self, rows: list[tuple[dict[str, float], float]]) -> float:
        """Mean of the two class recalls, which a one-class model fails."""
        hits = {0.0: 0, 1.0: 0}
        totals = {0.0: 0, 1.0: 0}
        for features, is_pass in rows:
            total = sum(self.weights.get(k, 0.0) * v
                        for k, v in features.items())
            predicted = 1.0 if _sigmoid(total) >= 0.5 else 0.0
            totals[is_pass] += 1
            if predicted == is_pass:
                hits[is_pass] += 1
        recalls = [
            hits[cls] / totals[cls] for cls in (0.0, 1.0) if totals[cls]
        ]
        return sum(recalls) / len(recalls) if recalls else 0.0


def training_rows(clips: list[Clip]) -> list[tuple[dict[str, float], float]]:
    """Every play that has both a logged situation and a run/pass label."""
    rows: list[tuple[dict[str, float], float]] = []
    for clip in clips:
        label = truth_for(clip)
        if not label:
            continue
        situation = situation_for(clip)
        if not situation.known:
            continue
        rows.append((situation.features(), 1.0 if label == PASS else 0.0))
    return rows


@dataclass(frozen=True)
class Scorecard:
    """How the model did, next to the only baseline that matters."""

    plays: int
    correct: int
    majority_correct: int
    run_recall: float
    pass_recall: float

    @property
    def accuracy(self) -> float:
        return self.correct / self.plays if self.plays else 0.0

    @property
    def majority_accuracy(self) -> float:
        return self.majority_correct / self.plays if self.plays else 0.0

    @property
    def balanced_accuracy(self) -> float:
        return (self.run_recall + self.pass_recall) / 2.0

    @property
    def beats_majority(self) -> bool:
        return self.correct > self.majority_correct


def evaluate(clips: list[Clip], model: RunPassPrior | None = None) -> Scorecard:
    """Score a model against always-guessing-the-common-class.

    Accuracy alone hides a model that learned "say pass" - on a 70% pass
    sample that scores 70% and knows nothing. Majority accuracy sits
    beside it, and balanced accuracy sits beside that.
    """
    model = model or RunPassPrior.default()
    rows = [
        (clip, truth_for(clip))
        for clip in clips
        if truth_for(clip) and situation_for(clip).known
    ]
    if not rows:
        return Scorecard(0, 0, 0, 0.0, 0.0)

    passes = sum(1 for _, label in rows if label == PASS)
    runs = len(rows) - passes
    majority = PASS if passes >= runs else RUN

    correct = 0
    run_hits = 0
    pass_hits = 0
    for clip, truth in rows:
        predicted, _ = model.predict(clip)
        if predicted == truth:
            correct += 1
            if truth == RUN:
                run_hits += 1
            else:
                pass_hits += 1
    return Scorecard(
        plays=len(rows),
        correct=correct,
        majority_correct=max(passes, runs),
        run_recall=run_hits / runs if runs else 0.0,
        pass_recall=pass_hits / passes if passes else 0.0,
    )


def fit_for_project(clips: list[Clip]) -> RunPassPrior:
    """The model a project should use: its own if it has earned one."""
    return RunPassPrior.default().fit(list(clips))
