"""The worker reports as it goes, and stops when asked."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from PySide6.QtWidgets import QApplication, QLabel

from tapesift.services import first_read_batch
from tapesift.services.first_read_service import FirstRead, load, store
from tapesift.ui_v2.first_read_confirm import FirstReadConfirm
from tapesift.workers.first_read_worker import FirstReadWorker

AGREED = FirstRead(label="run", agreement=True, views=("run", "run"))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@dataclass
class FakeClip:
    id: str
    start_ms: int = 0
    end_ms: int = 10_000
    analysis_json: str = ""


def make_worker(n=3, **kwargs):
    return FirstReadWorker(
        [FakeClip(id=f"c{i}") for i in range(n)],
        ffmpeg_path="ffmpeg", source=Path("film.mp4"),
        scratch_dir=Path("."), api_key="k", **kwargs)


def test_worker_emits_each_result_for_saving(qapp, monkeypatch):
    """Saving per play is what makes a stopped run keep its work."""
    monkeypatch.setattr(
        first_read_batch, "read_play",
        lambda *a, **k: AGREED)
    worker = make_worker(3)
    saved, progressed, done = [], [], []
    worker.result_ready.connect(lambda cid, aj: saved.append((cid, aj)))
    worker.progressed.connect(progressed.append)
    worker.finished_batch.connect(done.append)

    worker.run()  # run inline: the thread body, without the thread

    assert [cid for cid, _ in saved] == ["c0", "c1", "c2"]
    assert all(load(aj) is not None for _, aj in saved)
    assert len(progressed) == 3
    assert done[0].suggested == 3


def test_worker_never_emits_an_analyst_label(qapp, monkeypatch):
    monkeypatch.setattr(
        first_read_batch, "read_play", lambda *a, **k: AGREED)
    worker = make_worker(2)
    saved = []
    worker.result_ready.connect(lambda cid, aj: saved.append(aj))
    worker.run()
    for analysis in saved:
        assert "run_pass" not in json.loads(analysis)


def test_stop_ends_the_run_and_keeps_what_was_paid_for(qapp, monkeypatch):
    monkeypatch.setattr(
        first_read_batch, "read_play", lambda *a, **k: AGREED)
    worker = make_worker(5)
    saved = []

    def on_result(clip_id, analysis):
        saved.append(clip_id)
        if len(saved) == 2:
            worker.stop()

    worker.result_ready.connect(on_result)
    summaries = []
    worker.finished_batch.connect(summaries.append)
    worker.run()

    assert saved == ["c0", "c1"]
    assert summaries[0].cancelled is True


def test_plan_is_available_before_the_run_starts(qapp):
    worker = make_worker(4)
    assert worker.plan().count == 4


def test_worker_freezes_clip_inputs_before_background_edits(qapp, monkeypatch):
    clip = FakeClip(id="original")
    worker = FirstReadWorker(
        [clip], ffmpeg_path="ffmpeg", source=Path("film.mp4"),
        scratch_dir=Path("."), api_key="k")
    clip.end_ms = 99_000
    observed = []

    def read(_ffmpeg, _source, start, end, *_args, **_kwargs):
        observed.append((start, end))
        return AGREED

    monkeypatch.setattr(first_read_batch, "read_play", read)
    worker.run()
    assert observed == [(0, 10_000)]


def test_unexpected_thread_failure_reports_terminal_state_and_keeps_results(
        qapp, monkeypatch):
    def fail_after_result(*_args, save, **_kwargs):
        save("c0", store("{}", AGREED))
        raise RuntimeError("unexpected reader failure")

    monkeypatch.setattr(first_read_batch, "run", fail_after_result)
    worker = make_worker(3)
    saved, summaries = [], []
    worker.result_ready.connect(lambda cid, analysis: saved.append(cid))
    worker.finished_batch.connect(summaries.append)
    worker.start()
    assert worker.wait(2000)
    qapp.processEvents()
    assert saved == ["c0"]
    assert len(summaries) == 1
    assert "Unexpected error" in summaries[0].stopped_reason


def dialog_text(dialog) -> str:
    return " ".join(label.text() for label in dialog.findChildren(QLabel))


def test_confirm_states_the_count_and_the_cost(qapp):
    plan = first_read_batch.plan([FakeClip(id=f"c{i}") for i in range(77)])
    dialog = FirstReadConfirm(plan, "Georgia O vs Ole Miss D")
    text = dialog_text(dialog)
    assert "77 plays" in text
    assert "$0.09" in text
    assert "154 requests" in text
    dialog.deleteLater()


def test_confirm_says_what_leaves_the_machine(qapp):
    plan = first_read_batch.plan([FakeClip(id="c1")])
    dialog = FirstReadConfirm(plan, "Some Game", provider="OpenRouter")
    text = dialog_text(dialog)
    assert "still frames" in text
    assert "OpenRouter" in text
    assert "never pass through TapeSift" in text
    dialog.deleteLater()


def test_confirm_does_not_send_on_a_stray_return_key(qapp):
    """The default button is the one that does nothing."""
    plan = first_read_batch.plan([FakeClip(id="c1")])
    dialog = FirstReadConfirm(plan, "Some Game")
    assert dialog.cancel_button.isDefault() is True
    assert dialog.run_button.isDefault() is False
    dialog.deleteLater()
