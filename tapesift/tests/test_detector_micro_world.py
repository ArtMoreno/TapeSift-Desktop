"""Tests for the project-independent detector teaching sandbox."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.ui.detector_micro_world import (  # noqa: E402
    DetectorMicroWorldDialog,
    TEACHING_SPANS,
    classify_teaching_spans,
)


def test_default_micro_world_accounts_for_every_source_span() -> None:
    decisions = classify_teaching_spans()

    claimed = [
        index
        for decision in decisions
        for index in decision.span_indices
    ]
    assert sorted(claimed) == list(range(len(TEACHING_SPANS)))
    assert len(claimed) == len(set(claimed))
    assert decisions[0].kind == "play"
    assert decisions[1].kind == "separator"


def test_separator_setting_changes_grouping_without_losing_coverage() -> None:
    default = classify_teaching_spans(separator_max_s=5.0)
    raised = classify_teaching_spans(separator_max_s=6.0)

    assert sum(item.kind == "separator" for item in raised) == \
        sum(item.kind == "separator" for item in default) + 1
    assert sum(item.duration_s for item in default) == \
        sum(span.duration_s for span in TEACHING_SPANS)
    assert sum(item.duration_s for item in raised) == \
        sum(span.duration_s for span in TEACHING_SPANS)


def test_minimum_and_maximum_turn_runs_into_review_decisions() -> None:
    decisions = classify_teaching_spans(
        separator_max_s=6.0,
        min_play_s=10.0,
        max_play_s=25.0,
    )
    review_durations = {
        round(item.duration_s, 1)
        for item in decisions
        if item.kind == "review"
    }

    assert 31.0 in review_durations
    assert 8.0 in review_durations


def test_dialog_controls_refresh_the_decision_table() -> None:
    app = QApplication.instance() or QApplication([])
    dialog = DetectorMicroWorldDialog()
    default_rows = dialog.table.rowCount()

    dialog.separator_spin.setValue(6.0)
    app.processEvents()

    # The newly recognized long card becomes one separator and also splits
    # the former combined content run into two decisions.
    assert dialog.table.rowCount() == default_rows + 2
    assert "accounted" in dialog.summary_label.text()
    dialog.deleteLater()
    app.processEvents()
