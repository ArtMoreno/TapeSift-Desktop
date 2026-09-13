"""Preservation gate for the existing Detect Plays activity animation."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.ui.play_detect_dialog import (  # noqa: E402
    AnalysisActivityProgress,
    LiveFrameScanPanel,
)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def test_live_frame_scan_animation_still_advances(qapp):
    panel = LiveFrameScanPanel(90_000)
    starting_phase = panel.scan_phase

    panel.start()
    QTest.qWait(130)

    assert panel._timer.isActive()
    assert panel.scan_phase > starting_phase
    panel.stop()
    assert not panel._timer.isActive()


def test_analysis_activity_segment_still_advances(qapp):
    progress = AnalysisActivityProgress()
    starting_phase = progress._phase

    progress.start()
    QTest.qWait(130)

    assert progress._timer.isActive()
    assert progress._phase > starting_phase
    progress.stop()
    assert not progress._timer.isActive()
