"""The First Read dialog reports; it never owns the run."""

from __future__ import annotations

from pathlib import Path

import pytest

from PySide6.QtGui import QCloseEvent, QImage, QColor
from PySide6.QtWidgets import QApplication

from tapesift.services.first_read_batch import BatchProgress, BatchSummary
from tapesift.services.first_read_service import FirstRead
from tapesift.ui_v2.first_read_dialog import (
    VERDICT_HISTORY, FirstReadDialog,
)

AGREED = FirstRead(label="run", agreement=True, views=("run", "run"))
SPLIT = FirstRead(label=None, agreement=False, views=("run", "pass"))
BROKEN = FirstRead(label=None, agreement=False, error="Could not render.")


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def dialog(qapp):
    widget = FirstReadDialog(total=77)
    yield widget
    widget.deleteLater()


def progress(index, read, **kwargs):
    return BatchProgress(index=index, total=77, clip_id=f"c{index}",
                         read=read, **kwargs)


def test_tally_counts_each_outcome_separately(dialog):
    dialog.show_progress(progress(1, AGREED))
    dialog.show_progress(progress(2, SPLIT))
    dialog.show_progress(progress(3, BROKEN))
    text = dialog.tally.text()
    assert "1 suggested" in text
    assert "1 need your call" in text
    assert "1 could not be read" in text


def test_spend_is_shown_so_cost_is_never_a_surprise(dialog):
    for i in range(1, 51):
        dialog.show_progress(progress(i, AGREED))
    assert "$0.06" in dialog.tally.text()


def test_progress_bar_follows_the_play_index(dialog):
    dialog.show_progress(progress(45, AGREED))
    assert dialog.bar.value() == 45
    assert dialog.bar.maximum() == 77


def test_verdict_strip_keeps_only_the_recent_few(dialog):
    for i in range(1, VERDICT_HISTORY + 4):
        dialog.show_progress(progress(i, AGREED))
    assert len(dialog.verdicts._chips) == VERDICT_HISTORY


def test_a_missing_sheet_does_not_break_the_dialog(dialog):
    dialog.show_progress(progress(1, AGREED, sheet_path=Path("nope.jpg")))
    assert dialog.bar.value() == 1


def test_queued_preview_keeps_its_own_pixels_and_failed_play_clears_them(dialog, tmp_path):
    sheet = tmp_path / "shared.png"
    image = QImage(32, 16, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(sheet))
    queued = progress(1, AGREED, sheet_path=sheet, sheet_bytes=sheet.read_bytes())
    image.fill(QColor("blue"))
    assert image.save(str(sheet))
    dialog.show_progress(queued)
    assert dialog.sheet.pixmap().toImage().pixelColor(0, 0) == QColor("red")
    dialog.show_progress(progress(2, BROKEN, sheet_path=sheet))
    assert dialog.sheet.pixmap().isNull()
    assert "No frames" in dialog.sheet.text()


def test_stop_asks_once_and_says_it_heard_you(dialog):
    seen = []
    dialog.stop_requested.connect(lambda: seen.append(True))
    dialog._stop()
    assert seen == [True]
    assert dialog.stop_button.isEnabled() is False


def test_closing_the_window_hides_it_rather_than_cancelling(dialog):
    """A stray click on the frame must not throw away a paid-for run."""
    stopped = []
    dialog.stop_requested.connect(lambda: stopped.append(True))
    dialog.show()
    event = QCloseEvent()
    dialog.closeEvent(event)
    assert stopped == []
    assert event.isAccepted() is False
    assert dialog.isVisible() is False


def test_keep_working_hides_without_stopping(dialog):
    stopped = []
    dialog.stop_requested.connect(lambda: stopped.append(True))
    dialog.show()
    dialog.hide_button.click()
    assert dialog.isVisible() is False
    assert stopped == []


def test_finishing_offers_a_way_out_and_states_the_split(dialog):
    summary = BatchSummary(total=77, suggested=62, needs_analyst=14, failed=1)
    dialog.finish(summary)
    assert dialog.stop_button.isVisible() is False
    assert dialog.hide_button.text() == "Close"
    assert "62 suggested" in dialog.caption.text()
    assert "14 need your call" in dialog.caption.text()
