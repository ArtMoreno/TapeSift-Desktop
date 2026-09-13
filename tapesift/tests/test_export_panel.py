"""Behavior contracts for the staged Export & Cutups workspace."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton  # noqa: E402

from tapesift.models.clip import Clip  # noqa: E402
from tapesift.models.export_job import ExportJob, JobStatus, JobType  # noqa: E402
from tapesift.models.export_package import ExportStyle  # noqa: E402
from tapesift.ui.export_panel import ExportPanel  # noqa: E402
from tapesift.workers.export_worker import ExportWorker  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _job(title: str, clip_id: str) -> ExportJob:
    return ExportJob(
        job_type=JobType.CLIP,
        display_name=title,
        output_path=f"C:/exports/{title}.mp4",
        clip_id=clip_id,
        preset_name="social_1080p",
    )


def test_export_setup_uses_selected_context_and_emits_configuration(qapp):
    panel = ExportPanel()
    clips = [
        Clip(0, 25_000, id="one"),
        Clip(30_000, 55_000, id="two"),
        Clip(60_000, 80_000, id="excluded", enabled=False),
    ]
    panel.set_clip_context(clips)
    panel.set_output_folder("D:/TapeSift/Exports/Game")

    assert panel.selection_label.text().startswith("2 clips")
    assert "D:/TapeSift/Exports/Game" in panel.summary_destination.text()

    requests: list[tuple[str, str, bool]] = []
    panel.export_requested.connect(
        lambda mode, preset, accurate:
        requests.append((mode, preset, accurate)))
    panel.export_btn.click()
    assert requests == [("both", "social_1080p", True)]


def test_clean_export_waits_for_durable_queue_restore(qapp):
    panel = ExportPanel()
    panel.set_export_style(ExportStyle.CLEAN)
    panel.set_bridge_readiness_error(
        "Restoring the durable export queue before starting new work.")

    assert not panel.export_btn.isEnabled()
    assert not panel.composited_unavailable_label.isHidden()
    assert "Restoring" in panel.export_btn.toolTip()

    panel.set_bridge_readiness_error("")
    assert panel.export_btn.isEnabled()
    assert panel.composited_unavailable_label.isHidden()


def test_export_queue_moves_jobs_through_now_next_and_complete(qapp):
    panel = ExportPanel()
    first = _job("Play-01", "one")
    second = _job("Play-02", "two")
    failed = _job("Play-03", "three")
    panel.set_clip_context([
        Clip(0, 25_000, id="one"),
        Clip(30_000, 58_000, id="two"),
        Clip(60_000, 90_000, id="three"),
    ])
    panel.load_jobs([first, second, failed])
    panel.set_running(True)
    panel.on_job_started(first.id)
    # Lane rebuilds are coalesced onto the event loop; flush stands in for
    # the paint that would run before a user could read the lanes.
    panel.flush_queue_refresh()
    panel.on_job_progress(first.id, 62, 84)

    assert panel.now_lane.count.text() == "1 active"
    assert panel.next_lane.count.text() == "2 queued"
    assert panel._active_progress_widgets[first.id][0].value() == 62
    assert panel.overall_progress.value() == 21

    panel.on_job_completed(first.id, first.output_path)
    panel.on_job_started(second.id)
    panel.on_job_failed(second.id, "Encoder unavailable")
    panel.on_job_started(failed.id)
    panel.on_job_completed(failed.id, failed.output_path)
    panel.set_running(False)
    panel.flush_queue_refresh()

    assert panel.now_lane.count.text() == ""
    assert panel.next_lane.count.text() == ""
    assert panel.complete_lane.count.text() == "3 finished"
    assert panel._stage_labels["complete"].property("active") == "true"
    assert "2 of 3 outputs complete" in panel.overall_label.text()
    assert "needs attention" in panel.overall_label.text()


def test_completed_and_failed_jobs_keep_output_actions(qapp):
    panel = ExportPanel()
    complete = _job("Complete", "one")
    failed = _job("Failed", "two")
    queued = _job("Queued", "three")
    panel.load_jobs([complete, failed, queued])
    panel.on_job_started(complete.id)
    panel.on_job_completed(complete.id, complete.output_path)
    panel.on_job_started(failed.id)
    panel.on_job_failed(failed.id, "Disk full")
    panel.flush_queue_refresh()

    buttons = {
        button.text()
        for button in panel.complete_lane.findChildren(QPushButton)
    }
    assert {"Open", "Folder", "Retry"} <= buttons
    queued_buttons = {
        button.text()
        for button in panel.next_lane.findChildren(QPushButton)
    }
    assert "Remove" in queued_buttons
    labels = {
        label.text()
        for label in panel.complete_lane.findChildren(QLabel)
    }
    assert "Failed" in labels


def test_queued_output_can_be_removed_without_cancelling_the_active_job(qapp):
    panel = ExportPanel()
    active = _job("Active", "one")
    queued = _job("Queued", "two")
    panel.load_jobs([active, queued])
    panel.on_job_started(active.id)
    panel.flush_queue_refresh()
    removed: list[str] = []
    panel.remove_queued_requested.connect(removed.append)

    remove = next(
        button
        for button in panel.next_lane.findChildren(QPushButton)
        if button.text() == "Remove"
    )
    remove.click()

    assert removed == [queued.id]
    assert panel.jobs[active.id].status == JobStatus.EXPORTING


def test_export_worker_only_cancels_jobs_that_have_not_started(qapp):
    active = _job("Active", "one")
    queued = _job("Queued", "two")
    active.status = JobStatus.EXPORTING
    worker = ExportWorker("", None, [active, queued], {}, True)

    assert worker.cancel_queued(active.id) is False
    assert worker.cancel_queued(queued.id) is True
    assert queued.status == JobStatus.CANCELLED
