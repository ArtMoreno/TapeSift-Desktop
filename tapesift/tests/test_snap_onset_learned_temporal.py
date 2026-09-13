from __future__ import annotations

from tapesift.research.snap_onset_learned_temporal import (
    fit_balanced_logistic,
    predict_probabilities,
    select_learned_candidate,
    temporal_context_vectors,
)


def test_temporal_context_vectors_repeat_boundaries():
    result = temporal_context_vectors([[1.0], [2.0], [3.0]])

    assert result == [
        [1.0, 1.0, 2.0],
        [1.0, 2.0, 3.0],
        [2.0, 3.0, 3.0],
    ]


def test_balanced_logistic_learns_separable_examples():
    model = fit_balanced_logistic(
        [[-2.0], [-1.0], [1.0], [2.0]],
        [0, 0, 1, 1],
    )
    probabilities = predict_probabilities(model, [[-1.5], [1.5]])

    assert probabilities[0] < 0.5
    assert probabilities[1] > 0.5
    assert model["positive_examples"] == 2
    assert model["negative_examples"] == 2


def test_learned_candidate_requires_probability_and_margin():
    confident = select_learned_candidate(
        [100, 200, 300], [0.10, 0.85, 0.20]
    )
    weak = select_learned_candidate(
        [100, 200, 300], [0.45, 0.59, 0.50]
    )

    assert confident["candidate_ms"] == 200
    assert confident["confident"] is True
    assert weak["confident"] is False
