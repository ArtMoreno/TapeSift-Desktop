"""Recorded Ledger facts must survive grouping, metadata edits and trim previews."""
from __future__ import annotations

from copy import deepcopy

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication

from tapesift.models.clip import Clip
from tapesift.ui_core.clip_list import COL_NUM, COL_STATUS, COL_TITLE, ClipListWidget
from tapesift.ui_v3.ledger_presentation import LedgerDelegateV3


@pytest.fixture
def ledger():
    app = QApplication.instance() or QApplication([])
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    delegate = LedgerDelegateV3(widget)
    widget.table.setItemDelegate(delegate)
    yield widget, delegate, app
    widget.close()
    widget.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def populate(widget, clips):
    widget.set_clips(clips, 5_000_000, "{clip_number}_{clip_title}", "QA", "underscore")
    return widget.table.model().index(widget._clip_rows[clips[0].id], COL_TITLE)


def test_ledger_shows_recorded_clock_range_and_all_results_without_writing(ledger):
    widget, delegate, _app = ledger
    clip = Clip(74_283, 85_409, clip_title="Actual analyst title", details={
        "quarter":"Q1", "game_clock":"11:54", "player_name":"Malachi Toney",
        "run_pass":"Pass", "play_type":"Dropback", "play_action":"Play Action",
        "result":"Completion; First Down", "custom":"keep verbatim"})
    before = deepcopy(clip.details)
    index = populate(widget, [clip])
    assert delegate.row_text(index) == {
        "headline":"Q1 11:54", "clock":"Q1 11:54", "range":"01:14.283 – 01:25.409",
        "duration":"11.126s", "status":"LOGGED"}
    tip = delegate.tooltip(index)
    for fact in ("Actual analyst title", "Player: Malachi Toney", "Concept: Dropback",
                 "Play action: Play Action", "Result: Completion; First Down",
                 "Source: 01:14.283 – 01:25.409", "Duration: 11.126s"):
        assert fact in tip
    assert widget.table.item(index.row(), COL_STATUS).text() == "LOGGED"
    assert clip.details == before
    accessible = index.data(Qt.ItemDataRole.AccessibleTextRole)
    assert "Game: Q1 11:54" in accessible
    assert "Source: 01:14.283 – 01:25.409" in accessible
    assert "Duration: 11.126s" in accessible
    assert "Actual analyst title" in accessible


def test_ledger_unknown_clock_falls_back_to_title_and_preserves_custom_clock(ledger):
    widget, delegate, _app = ledger
    clip = Clip(0, 11_000, clip_title="Needs a manual clock")
    index = populate(widget, [clip])
    assert delegate.row_text(index)["headline"] == "Needs a manual clock"
    assert "Game: Not recorded" in delegate.tooltip(index)
    clip.details = {"quarter":"Custom quarter", "game_clock":"unknown clock"}
    widget.update_row(clip, 5_000_000, "{clip_number}_{clip_title}", "QA", "underscore")
    index = widget.table.model().index(widget._clip_rows[clip.id], COL_TITLE)
    assert delegate.row_text(index)["headline"] == "Custom quarter unknown clock"
    assert clip.details["game_clock"] == "unknown clock"


def test_ledger_trim_preview_uses_pending_times_without_mutating_clip(ledger):
    widget, delegate, _app = ledger
    clip = Clip(74_283, 85_409, clip_title="Trim QA", details={"quarter":"Q1"})
    index = populate(widget, [clip])
    widget.preview_bounds(clip.id, 74_900, 86_100)
    text = delegate.row_text(index)
    assert text["range"] == "01:14.900 – 01:26.100"
    assert text["duration"] == "11.2s"
    assert "Trim preview; release to save" in delegate.tooltip(index)
    assert "01:14.900 – 01:26.100" in index.data(Qt.ItemDataRole.AccessibleTextRole)
    assert (clip.start_ms, clip.end_ms) == (74_283, 85_409)


def test_ledger_keeps_group_and_multi_selection_authority(ledger):
    widget, delegate, app = ledger
    clips = [Clip(10_000*i, 10_000*i+5_000, clip_number=i+1,
                  clip_title=f"QA {i}", details={"quarter":"Q1"}) for i in range(2)]
    populate(widget, clips)
    widget.show()
    app.processEvents()
    table = widget.table
    table.selectRow(widget._clip_rows[clips[0].id])
    for col in (COL_NUM, COL_TITLE, COL_STATUS):
        table.item(widget._clip_rows[clips[1].id], col).setSelected(True)
    assert widget.selected_clip_ids() == [clip.id for clip in clips]
    group_index = table.model().index(min(widget._group_rows), COL_TITLE)
    assert delegate.row_text(group_index) == {}
    assert all(table.item(widget._clip_rows[c.id], COL_STATUS).text() == "LOGGED" for c in clips)
