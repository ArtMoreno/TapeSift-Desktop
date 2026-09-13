"""A batch must be interruptible, resumable, and honest about failure."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from tapesift.models.clip import Clip
from tapesift.services import first_read_batch as batch
from tapesift.services.first_read_service import (
    FirstRead, FirstReadError, FirstReadRateLimited, load, store,
)


@dataclass
class FakeClip:
    id: str
    start_ms: int = 0
    end_ms: int = 10_000
    analysis_json: str = ""


def clips(n, **kwargs):
    return [FakeClip(id=f"clip{i}", **kwargs) for i in range(1, n + 1)]


def reader_for(*results):
    """A reader returning each result in turn, then repeating the last."""
    queue = list(results)

    def read(ffmpeg_path, source, start_ms, end_ms, scratch, **kwargs):
        return queue.pop(0) if len(queue) > 1 else queue[0]
    return read


AGREED = FirstRead(label="run", agreement=True, views=("run", "run"))
SPLIT = FirstRead(label=None, agreement=False, views=("run", "pass"))
BROKEN = FirstRead(label=None, agreement=False, error="Could not render.")


def run(items, reader, **kwargs):
    saved = {}
    summary = batch.run(
        items, ffmpeg_path="ffmpeg", source=Path("film.mp4"),
        scratch_dir=Path("."), api_key="k",
        save=lambda cid, aj: saved.__setitem__(cid, aj),
        reader=reader, sleep=lambda s: None, **kwargs)
    return summary, saved


def test_plan_states_the_cost_before_anything_runs():
    selected = batch.plan(clips(70))
    assert selected.count == 70
    assert 0.05 < selected.estimated_cost_usd < 0.12
    assert "70 plays" in selected.describe()


def test_plan_skips_plays_that_already_have_a_suggestion():
    done = FakeClip(id="done", analysis_json=store("", AGREED))
    selected = batch.plan([done, FakeClip(id="todo")])
    assert selected.to_read == ("todo",)
    assert selected.already_read == ("done",)
    assert "1 already done" in selected.describe()


def test_redo_ignores_existing_suggestions():
    done = FakeClip(id="done", analysis_json=store("", AGREED))
    assert batch.plan([done], redo=True).to_read == ("done",)


def test_a_finished_batch_counts_the_split():
    summary, saved = run(clips(3), reader_for(AGREED, SPLIT, AGREED))
    assert summary.total == 3
    assert summary.suggested == 2
    assert summary.needs_analyst == 1
    assert summary.failed == 0
    assert len(saved) == 3
    assert "2 suggested" in summary.describe()
    assert "1 need your call" in summary.describe()


def test_results_are_saved_as_they_arrive_not_at_the_end():
    """A run that dies must keep the plays it already paid for."""
    seen = []

    def cancel_after_two():
        return len(seen) >= 2

    saved = {}
    batch.run(
        clips(5), ffmpeg_path="ffmpeg", source=Path("f.mp4"),
        scratch_dir=Path("."), api_key="k",
        save=lambda cid, aj: saved.__setitem__(cid, aj),
        reader=reader_for(AGREED), sleep=lambda s: None,
        on_progress=lambda p: seen.append(p),
        should_cancel=cancel_after_two)
    assert len(saved) == 2
    assert all(load(value) is not None for value in saved.values())


def test_cancelling_stops_and_says_so():
    summary, saved = run(clips(5), reader_for(AGREED),
                         should_cancel=lambda: True)
    assert summary.cancelled is True
    assert saved == {}
    assert "Stopped" in summary.describe()


def test_one_unrenderable_play_does_not_end_the_batch():
    summary, saved = run(clips(3), reader_for(BROKEN, AGREED, AGREED))
    assert summary.failed == 1
    assert summary.suggested == 2
    assert len(saved) == 3
    assert summary.failures[0][0] == "clip1"


def test_a_refused_key_stops_the_run_rather_than_burning_money():
    def refuse(*a, **k):
        raise FirstReadError("The provider refused the request (401).")
    summary, saved = run(clips(50), refuse)
    assert saved == {}
    assert summary.answered == 0
    assert "401" in summary.stopped_reason


def test_throttling_waits_then_continues():
    calls = {"n": 0}
    waited = []

    def throttled_once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise FirstReadRateLimited("slow down", retry_after=7)
        return AGREED

    summary = batch.run(
        clips(1), ffmpeg_path="ffmpeg", source=Path("f.mp4"),
        scratch_dir=Path("."), api_key="k", save=lambda c, a: None,
        reader=throttled_once, sleep=waited.append)
    assert summary.suggested == 1
    assert waited == [7]


def test_endless_throttling_gives_up_instead_of_running_all_night():
    def always_throttled(*a, **k):
        raise FirstReadRateLimited("slow down", retry_after=1)
    waited = []
    summary = batch.run(
        clips(1), ffmpeg_path="ffmpeg", source=Path("f.mp4"),
        scratch_dir=Path("."), api_key="k", save=lambda c, a: None,
        reader=always_throttled, sleep=waited.append)
    assert summary.failed == 1
    assert len(waited) == batch.MAX_RATE_LIMIT_WAITS
    assert "Rate limited" in summary.failures[0][1]


def test_resuming_re_reads_only_what_is_missing():
    done = FakeClip(id="clip1", analysis_json=store("", AGREED))
    todo = FakeClip(id="clip2")
    summary, saved = run([done, todo], reader_for(AGREED))
    assert summary.total == 1
    assert summary.skipped == 1
    assert set(saved) == {"clip2"}


def test_a_batch_never_writes_an_analyst_label():
    """The invariant, checked again at the level that touches the project."""
    summary, saved = run(clips(3), reader_for(AGREED, SPLIT, BROKEN))
    for value in saved.values():
        assert "run_pass" not in json.loads(value)


def test_existing_analysis_data_survives_a_batch():
    clip = FakeClip(id="c", analysis_json=json.dumps({"snap_onset_ms": 900}))
    summary, saved = run([clip], reader_for(AGREED))
    assert json.loads(saved["c"])["snap_onset_ms"] == 900


def test_real_clip_batch_preserves_analysis_and_resumes():
    clip = Clip(start_ms=0, end_ms=10_000,
                analysis={"snap_onset_ms": 900}, details={"run_pass": "Pass"})
    summary, saved = run([clip], reader_for(AGREED))
    assert summary.suggested == 1
    clip.analysis = json.loads(saved[clip.id])
    assert clip.analysis["snap_onset_ms"] == 900
    assert load(clip.analysis_json()).label == "run"
    assert clip.details == {"run_pass": "Pass"}

    def unexpected_read(*args, **kwargs):
        pytest.fail("A saved real clip must not trigger another model call")

    resumed, saved_again = run([clip], unexpected_read)
    assert resumed.skipped == 1
    assert resumed.total == 0
    assert saved_again == {}


def test_progress_keeps_each_plays_first_sheet_bytes(tmp_path):
    sheet = tmp_path / "first-read-a.jpg"
    progress = []
    calls = []

    def reader(*args, on_sheet, **kwargs):
        content = f"play {len(calls) + 1}".encode()
        calls.append(content)
        sheet.write_bytes(content)
        on_sheet("a", sheet)
        return AGREED

    run(clips(2), reader, on_progress=progress.append)
    assert sheet.read_bytes() == b"play 2"
    assert [item.sheet_bytes for item in progress] == [b"play 1", b"play 2"]
    assert [item.clip_id for item in progress] == ["clip1", "clip2"]


def test_missing_preview_does_not_discard_the_result(tmp_path):
    progress = []

    def reader(*args, on_sheet, **kwargs):
        on_sheet("a", tmp_path / "missing.jpg")
        return AGREED

    summary, saved = run(clips(1), reader, on_progress=progress.append)
    assert summary.suggested == 1 and len(saved) == 1
    assert progress[0].sheet_bytes == b""
