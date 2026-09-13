"""Focused invariants for the frozen Iteration 4B driver."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.prepare_iteration_4b_generalization import (
    FILMS,
    PARAMETERS,
    QUEUE_ID,
    Iteration4BError,
    canonical_json,
    load_selection,
    sha256_file,
    write_new,
)


def test_corpus_is_unique_and_predeclared() -> None:
    ids = [film["film_id"] for film in FILMS]
    sources = [film["source_file"].casefold() for film in FILMS]

    assert len(FILMS) == 5
    assert len(ids) == len(set(ids))
    assert len(sources) == len(set(sources))
    assert all("holdout_4b_v1" in film_id for film_id in ids)
    assert PARAMETERS == {
        "separator_max_s": 5.0,
        "min_play_s": 4.0,
        "max_play_s": 90.0,
        "scene_threshold": 0.35,
    }


def test_write_new_refuses_to_replace_frozen_artifact(
    tmp_path: Path,
) -> None:
    target = tmp_path / "frozen.json"
    write_new(target, "{}\n")

    with pytest.raises(FileExistsError):
        write_new(target, '{"changed": true}\n')

    assert target.read_text(encoding="utf-8") == "{}\n"


def test_load_selection_fails_closed_on_identity_change(
    tmp_path: Path,
) -> None:
    selection = tmp_path / "selection.locked.json"
    selection.write_text(canonical_json({
        "schema_version": "1.0",
        "queue_id": "wrong-queue",
        "status": "locked_before_detection",
        "films": [{}, {}, {}, {}, {}],
    }), encoding="utf-8")

    with pytest.raises(Iteration4BError):
        load_selection(tmp_path)


def test_canonical_json_and_digest_are_stable(tmp_path: Path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(
        canonical_json({"queue_id": QUEUE_ID, "b": 2, "a": 1}),
        encoding="utf-8",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert list(payload) == ["a", "b", "queue_id"]
    assert sha256_file(path) == sha256_file(path)
