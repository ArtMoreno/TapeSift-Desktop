"""A suggestion reaches the project as analysis, and never as a label."""

from __future__ import annotations

import json

import pytest

from tapesift.models.clip import Clip
from tapesift.services.first_read_service import ANALYSIS_KEY, FirstRead, load, store
from tapesift.services.project_service import ProjectSession


AGREED = FirstRead(label="run", agreement=True, views=("run", "run"))


@pytest.fixture
def session(tmp_path):
    session = ProjectSession.create(
        "Georgia O vs Ole Miss D", tmp_path, tmp_path / "out")
    session.add_clip(Clip(start_ms=0, end_ms=10_000,
                          project_id=session.project.id))
    return session


def test_a_suggestion_is_stored_on_the_clip(session):
    clip = session.clips[0]
    session.store_clip_analysis(clip.id, store(clip.analysis_json(), AGREED))
    stored = load(session.get_clip(clip.id).analysis_json())
    assert stored is not None
    assert stored.label == "run"
    assert stored.agreement is True


def test_storing_a_suggestion_never_touches_the_label(session):
    clip = session.clips[0]
    session.store_clip_analysis(clip.id, store(clip.analysis_json(), AGREED))
    after = session.get_clip(clip.id)
    assert after.details.get("run_pass", "") == ""
    assert "run_pass" not in json.loads(after.analysis_json())


def test_an_analyst_label_is_untouched_by_a_later_suggestion(session):
    """The analyst decided; a background job must not quietly disagree."""
    clip = session.clips[0]
    clip.details["run_pass"] = "Pass"
    session.store_clip_analysis(clip.id, store(clip.analysis_json(), AGREED))
    after = session.get_clip(clip.id)
    assert after.details["run_pass"] == "Pass"
    assert load(after.analysis_json()).label == "run"


def test_other_analysis_survives(session):
    clip = session.clips[0]
    clip.analysis = {"snap_onset_ms": 4321}
    session.store_clip_analysis(clip.id, store(clip.analysis_json(), AGREED))
    after = session.get_clip(clip.id)
    assert after.analysis["snap_onset_ms"] == 4321
    assert ANALYSIS_KEY in after.analysis


def test_unknown_clip_and_bad_payload_are_refused(session):
    assert session.store_clip_analysis("nope", "{}") is None
    assert session.store_clip_analysis(session.clips[0].id, "{bad") is None
    assert session.store_clip_analysis(session.clips[0].id, "[]") is None


def test_a_suggestion_survives_a_save_and_reload(session, tmp_path):
    clip = session.clips[0]
    session.store_clip_analysis(clip.id, store(clip.analysis_json(), AGREED))
    session.save()
    reopened = ProjectSession.open(session.db_path)
    assert load(reopened.clips[0].analysis_json()).label == "run"
