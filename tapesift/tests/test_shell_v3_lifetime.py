from __future__ import annotations

import shiboken6

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from tapesift.core.config import AppSettings
from tapesift.ui_v3.main_window import MainWindowV3


def test_v3_survives_repeated_rail_and_window_state_loops(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindowV3(
        AppSettings(), workspace_state_path=tmp_path / "shell-v3.json")
    window._screen_fit_done = True
    window.resize(1708, 921)
    window.stack.setCurrentWidget(window.workspace)
    window.show()
    app.processEvents()
    review = window._v3_review
    for cycle in range(60):
        review.ledger_rail.set_open(cycle % 2 == 0)
        review.details_rail.set_open(cycle % 3 == 0)
        if cycle % 4 == 0:
            window._set_workspace_stage("export")
            app.processEvents()
            if cycle % 8 == 0:
                review.back_to_review_button.click()
            else:
                window._shortcut_escape()
        if cycle % 10 == 0:
            window.showMinimized()
            app.processEvents()
            window.showNormal()
        app.processEvents()
        assert window._workspace_stage == "review"
        assert window.live_v3_objects_valid()
        assert shiboken6.isValid(review)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    assert window.live_v3_objects_valid()
