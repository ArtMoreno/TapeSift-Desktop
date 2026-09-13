"""ClipList row-level update (Phase 1.1c): edits rewrite one row, not all."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from tapesift.models.clip import Clip, ExportStatus
from tapesift.ui_core import clip_list as clip_list_module
from tapesift.ui_core.clip_list import (
    COL_DUR, COL_END, COL_FILE, COL_NUM, COL_START, COL_STATUS, COL_TITLE,
    REVIEW_KIND_ROLE, ClipListWidget, review_status_for,
)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def menu_watch(qapp):
    """Auto-dismiss any context menu and report whether one opened.

    QMenu.exec() runs a blocking modal loop. It cannot be monkeypatched away
    on this PySide6 build - assigning QMenu.exec silently fails to intercept
    the C++ slot, so exec() runs for real and hangs forever with no user to
    close the menu, which is exactly why this file used to stall the suite.

    A short-interval timer fires inside exec's own nested event loop, closes
    the popup so exec() returns, and records the sighting so a test can assert
    a menu did or did not appear.
    """
    seen: list = []
    timer = QTimer()
    timer.setInterval(5)

    def _tick() -> None:
        popup = qapp.activePopupWidget()
        if popup is not None:
            seen.append(popup)
            popup.close()

    timer.timeout.connect(_tick)
    timer.start()
    yield seen
    timer.stop()


def _make_clips() -> list[Clip]:
    return [
        Clip(start_ms=0, end_ms=5_000, clip_number=1, clip_title="Alpha"),
        Clip(start_ms=5_000, end_ms=10_000, clip_number=2, clip_title="Beta"),
    ]


def test_set_clips_populates_all_rows(qapp):
    widget = ClipListWidget()
    widget.set_compact(True)
    clips = _make_clips()
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")
    assert widget.table.rowCount() == 2
    assert widget.table.item(0, 1).text()  # start col exists
    assert "Alpha" in widget.table.item(0, 4).text()
    assert "Beta" in widget.table.item(1, 4).text()


def test_ignored_detector_fragments_stay_out_of_everyday_clip_count(qapp):
    normal = Clip(
        start_ms=0, end_ms=5_000, clip_number=1, clip_title="Real play")
    ignored = Clip(
        start_ms=5_000,
        end_ms=6_000,
        clip_number=2,
        clip_title="Detector fragment",
        enabled=False,
        detection_lineage={
            "session_id": "detect-1",
            "candidate_ids": ["candidate-2"],
            "suppressed_unclassified": True,
        },
    )
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    widget.set_clips(
        [normal, ignored], 60_000, "{clip_number}", "Game", "-")
    widget.set_ignored_fragment_summary(8, 12_500)

    assert widget.table.isRowHidden(widget._clip_rows[normal.id]) is False
    assert widget.table.isRowHidden(widget._clip_rows[ignored.id]) is True
    assert widget.count_label.text().startswith("1 plays")
    assert "8 fragments hidden" in widget.progress_label.text()
    assert widget.ignored_filter_btn.isHidden() is False

    widget._set_filter_mode("ignored")
    assert widget.table.isRowHidden(widget._clip_rows[normal.id]) is True
    assert widget.table.isRowHidden(widget._clip_rows[ignored.id]) is False


def test_context_menu_targets_the_clicked_unselected_row(qapp, menu_watch):
    widget = ClipListWidget()
    widget.resize(800, 500)
    clips = _make_clips()
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")
    widget.show()
    qapp.processEvents()
    widget.select_clip_id(clips[0].id)
    second_index = widget.table.model().index(
        widget._clip_rows[clips[1].id], COL_NUM)
    pos = widget.table.visualRect(second_index).center()

    widget._context_menu(pos)

    assert menu_watch, "a context menu should have opened on a valid row"
    assert widget.selected_clip_ids() == [clips[1].id]
    widget.close()


def test_context_menu_ignores_empty_space_and_group_headers(
    qapp,
    menu_watch,
):
    widget = ClipListWidget()
    widget.resize(800, 500)
    widget.set_sidebar_mode(True)
    clips = _make_clips()
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")
    widget.show()
    qapp.processEvents()
    widget.select_clip_id(clips[0].id)

    widget._context_menu(widget.table.viewport().rect().bottomRight())
    group_index = widget.table.model().index(
        next(iter(widget._group_rows)), COL_NUM)
    widget._context_menu(widget.table.visualRect(group_index).center())

    assert menu_watch == [], "no menu should open on empty space or a header"
    assert widget.selected_clip_ids() == [clips[0].id]
    widget.close()


def test_long_titles_are_ellipsized_visually_but_complete_on_hover(qapp):
    widget = ClipListWidget()
    title = (
        "Q4 3rd & 12 on 38 Pass Complete to the Boundary for a First Down")
    clip = Clip(
        start_ms=0, end_ms=5_000, clip_number=1, clip_title=title,
        tags=["Pass", "Third Down", "Boundary"],
    )
    widget.set_clips([clip], 600_000, "{clip_number}_{clip_name}", "Game", "-")

    title_item = widget.table.item(0, COL_TITLE)
    assert widget.table.textElideMode() == Qt.TextElideMode.ElideRight
    assert widget.table.wordWrap() is False
    assert title_item.text() == title
    assert title_item.toolTip() == title
    assert title_item.data(Qt.ItemDataRole.AccessibleTextRole) == title
    assert widget.table.item(0, COL_FILE).toolTip().endswith(".mp4")


def test_update_row_rewrites_only_target(qapp):
    widget = ClipListWidget()
    widget.set_compact(True)
    clips = _make_clips()
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")

    # Mutate only the first clip's title, then update just that row.
    clips[0].clip_title = "Alpha-renamed"
    widget.update_row(clips[0], 600_000, "{clip_number}", "Game", "-")

    # Target row reflects the change...
    assert "Alpha-renamed" in widget.table.item(0, 4).text()
    # ...and the other row is untouched.
    assert "Beta" in widget.table.item(1, 4).text()
    # Row count unchanged (no full rebuild).
    assert widget.table.rowCount() == 2


def test_update_row_preserves_selection_and_scroll(qapp):
    widget = ClipListWidget()
    widget.resize(900, 220)
    clips = [
        Clip(start_ms=i * 5_000, end_ms=(i + 1) * 5_000,
             clip_number=i + 1, clip_title=f"Play {i + 1:03d}")
        for i in range(40)
    ]
    widget.set_compact(True)
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")
    widget.show()
    qapp.processEvents()

    widget.table.selectRow(24)
    scroll = widget.table.verticalScrollBar()
    scroll.setValue(min(15, scroll.maximum()))
    before_scroll = scroll.value()
    before_ids = widget.selected_clip_ids()

    clips[24].clip_title = "Play 025 renamed"
    widget.update_row(clips[24], 600_000, "{clip_number}", "Game", "-")

    assert widget.selected_clip_ids() == before_ids
    assert scroll.value() == before_scroll
    assert "renamed" in widget.table.item(24, 4).text()
    widget.close()


def test_preview_bounds_updates_times_without_mutating_clip(qapp):
    widget = ClipListWidget()
    clips = _make_clips()
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")

    widget.preview_bounds(clips[0].id, 1_250, 6_750)

    assert widget.table.item(0, COL_START).text() == "00:01.250"
    assert widget.table.item(0, COL_END).text() == "00:06.750"
    assert widget.table.item(0, COL_DUR).text() == "00:05.500"
    assert (clips[0].start_ms, clips[0].end_ms) == (0, 5_000)
    assert widget.table.item(1, COL_START).text() == "00:05"


def test_full_refresh_preserves_selection_current_row_and_scroll(qapp):
    widget = ClipListWidget()
    widget.resize(900, 220)
    clips = [
        Clip(start_ms=i * 5_000, end_ms=(i + 1) * 5_000,
             clip_number=i + 1, clip_title=f"Play {i + 1:03d}")
        for i in range(40)
    ]
    widget.set_compact(True)
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")
    widget.show()
    qapp.processEvents()

    widget.table.selectRow(24)
    scroll = widget.table.verticalScrollBar()
    scroll.setValue(min(15, scroll.maximum()))
    before_scroll = scroll.value()
    selected_id = clips[24].id

    clips[24].clip_title = "Play 025 refreshed"
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")

    assert widget.selected_clip_ids() == [selected_id]
    assert widget.table.currentRow() == 24
    assert scroll.value() == before_scroll
    assert "refreshed" in widget.table.item(24, COL_TITLE).text()
    widget.close()


def test_full_refresh_restores_selection_by_identity_after_reorder(qapp):
    widget = ClipListWidget()
    clips = _make_clips()
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")
    widget.table.selectRow(1)
    selected_id = clips[1].id

    reordered = [clips[1], clips[0]]
    widget.set_clips(reordered, 600_000, "{clip_number}", "Game", "-")

    assert widget.selected_clip_ids() == [selected_id]
    assert widget.table.currentRow() == 0


def test_visible_navigation_buttons_emit_and_track_boundaries(qapp):
    widget = ClipListWidget()
    clips = _make_clips()
    previous_calls = []
    next_calls = []
    widget.previous_requested.connect(lambda: previous_calls.append(1))
    widget.next_requested.connect(lambda: next_calls.append(1))
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")

    assert widget.previous_btn.isEnabled() is False
    assert widget.next_btn.isEnabled() is True
    widget.next_btn.click()
    assert next_calls == [1]

    widget.table.selectRow(1)
    assert widget.previous_btn.isEnabled() is True
    assert widget.next_btn.isEnabled() is False
    widget.previous_btn.click()
    assert previous_calls == [1]


def test_review_status_is_separate_from_export_status(qapp):
    widget = ClipListWidget()
    clip = Clip(
        start_ms=0, end_ms=5_000, clip_number=1, clip_title="Inside Zone",
        details={"quarter": "Q1", "run_pass": "Run"})
    widget.set_clips([clip], 600_000, "{clip_number}", "Game", "-")

    play_item = widget.table.item(0, COL_NUM)
    review_item = widget.table.item(0, COL_STATUS)
    assert play_item.text().startswith("R ")
    assert review_item.text() == "Logged"
    assert review_item.data(REVIEW_KIND_ROLE) == "logged"

    widget.update_status(clip.id, ExportStatus.COMPLETED)
    assert review_item.text() == "Logged"
    assert "Export:" in review_item.toolTip()


def test_review_status_prioritizes_exclusion_and_warnings():
    clip = Clip(start_ms=0, end_ms=5_000, details={"quarter": "Q1"})
    assert review_status_for(clip, [])[0] == "logged"
    assert review_status_for(clip, ["Overlaps the next clip."])[0] == \
        "needs_fix"
    clip.enabled = False
    assert review_status_for(clip, ["Overlaps the next clip."])[0] == \
        "excluded"


def test_autodetect_review_statuses_and_scoped_pending_filter(qapp):
    pending = Clip(start_ms=0, end_ms=5_000, clip_title="Pending")
    pending.detection_lineage = {
        "session_id": "detect-1",
        "candidate_ids": ["candidate-1"],
        "reviewed_at": "",
    }
    checked = Clip(start_ms=6_000, end_ms=11_000, clip_title="Checked")
    checked.detection_lineage = {
        "session_id": "detect-1",
        "candidate_ids": ["candidate-2"],
        "reviewed_at": "now",
    }
    no_play = Clip(
        start_ms=12_000, end_ms=17_000, clip_title="No play",
        enabled=False,
    )
    no_play.detection_lineage = {
        "session_id": "detect-1",
        "candidate_ids": ["candidate-3"],
        "reviewed_at": "now",
        "false_positive_confirmed_at": "now",
    }
    generic_exclusion = Clip(
        start_ms=18_000, end_ms=23_000, clip_title="Still pending",
        enabled=False,
    )
    generic_exclusion.detection_lineage = {
        "session_id": "detect-1",
        "candidate_ids": ["candidate-4"],
        "reviewed_at": "",
        "false_positive_confirmed_at": "",
    }
    withdrawn = Clip(
        start_ms=24_000, end_ms=29_000, clip_title="Withdrawn",
        enabled=False,
    )
    withdrawn.detection_lineage = {
        "session_id": "detect-1",
        "candidate_ids": [],
        "recovery_id": "recovery-1",
        "reviewed_at": "now",
        "recovery_withdrawn_at": "now",
    }

    assert review_status_for(pending, [])[0] == "autodetect_pending"
    assert review_status_for(checked, [])[0] == "autodetect_confirmed"
    assert review_status_for(no_play, [])[0] == "autodetect_no_play"
    assert review_status_for(generic_exclusion, [])[0] == "excluded"
    assert review_status_for(withdrawn, [])[0] == "autodetect_withdrawn"

    widget = ClipListWidget()
    clips = [pending, checked, no_play, generic_exclusion, withdrawn]
    widget.set_clips(clips, 60_000, "{clip_number}", "Game", "-")
    widget.show_review_filter(
        "autodetect_pending", clip_ids={pending.id})
    assert widget.review_filter_mode == "autodetect_pending"
    assert widget.table.isRowHidden(widget._clip_rows[pending.id]) is False
    assert widget.table.isRowHidden(
        widget._clip_rows[generic_exclusion.id]) is True


def test_detector_rows_keep_quality_and_logging_statuses_and_filters(qapp):
    warning_checked = Clip(
        start_ms=0, end_ms=500, clip_number=1, clip_title="Short checked",
        details={"quarter": "Q1"},
    )
    warning_checked.detection_lineage = {
        "session_id": "detect-1",
        "candidate_ids": ["candidate-1"],
        "reviewed_at": "now",
    }
    logged_checked = Clip(
        start_ms=1_000, end_ms=6_000, clip_number=2,
        clip_title="Logged checked", details={"quarter": "Q1"},
    )
    logged_checked.detection_lineage = {
        "session_id": "detect-1",
        "candidate_ids": ["candidate-2"],
        "reviewed_at": "now",
    }
    warning_pending = Clip(
        start_ms=7_000, end_ms=7_500, clip_number=3,
        clip_title="Short pending",
    )
    warning_pending.detection_lineage = {
        "session_id": "detect-1",
        "candidate_ids": ["candidate-3"],
        "reviewed_at": "",
    }
    logged_pending = Clip(
        start_ms=8_000, end_ms=13_000, clip_number=4,
        clip_title="Logged pending", details={"quarter": "Q2"},
    )
    logged_pending.detection_lineage = {
        "session_id": "detect-1",
        "candidate_ids": ["candidate-4"],
        "reviewed_at": "",
    }

    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    clips = [
        warning_checked, logged_checked, warning_pending, logged_pending,
    ]
    widget.set_clips(clips, 60_000, "{clip_number}", "Game", "-")

    assert widget.table.item(
        widget._clip_rows[warning_checked.id],
        COL_STATUS,
    ).data(REVIEW_KIND_ROLE) == "needs_fix"
    assert widget.table.item(
        widget._clip_rows[logged_checked.id],
        COL_STATUS,
    ).data(REVIEW_KIND_ROLE) == "logged"
    assert widget.table.item(
        widget._clip_rows[logged_pending.id],
        COL_STATUS,
    ).data(REVIEW_KIND_ROLE) == "logged"
    # One name for the state: the filter chip, the row badge and this count
    # all say "needs fix".
    assert "2 needs fix" in widget.progress_label.text()

    widget.show_review_filter("needs_fix")
    assert not widget.table.isRowHidden(widget._clip_rows[warning_checked.id])
    assert widget.table.isRowHidden(widget._clip_rows[logged_checked.id])
    assert not widget.table.isRowHidden(widget._clip_rows[warning_pending.id])

    # A warning is the primary row label, but it must not hide the independent
    # detector decision from the scoped pending-review filter.
    widget.show_review_filter("autodetect_pending")
    assert widget.table.isRowHidden(widget._clip_rows[warning_checked.id])
    assert widget.table.isRowHidden(widget._clip_rows[logged_checked.id])
    assert not widget.table.isRowHidden(widget._clip_rows[warning_pending.id])
    assert not widget.table.isRowHidden(widget._clip_rows[logged_pending.id])


def test_sidebar_mode_keeps_a_focused_clip_ledger(qapp):
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)

    assert widget._sidebar_mode is True
    assert widget.table.isColumnHidden(COL_NUM) is False
    assert widget.table.isColumnHidden(COL_START) is True
    assert widget.table.isColumnHidden(COL_TITLE) is False
    assert widget.table.isColumnHidden(COL_STATUS) is False
    assert widget.table.isColumnHidden(COL_FILE) is True
    assert widget.view_btn.isHidden()
    assert widget.up_btn.isHidden()
    assert widget.down_btn.isHidden()
    assert widget.previous_btn.text() == "‹ Previous"
    assert widget.next_btn.text() == "Next ›"


def test_sidebar_ledger_labels_in_out_and_duration(qapp):
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    clip = Clip(
        start_ms=43_000,
        end_ms=99_950,
        clip_number=1,
        clip_title="Q1 2 & 10 - No Gain",
        details={"result": "No Gain", "player_name": "Jakobe Thomas"},
    )

    widget.set_clips([clip], 600_000, "{clip_number}", "Game", "-")

    assert widget.table.rowHeight(widget._clip_rows[clip.id]) == 42
    row_text = widget.table.item(
        widget._clip_rows[clip.id], COL_TITLE).text()
    headline, situation = row_text.splitlines()
    # Player and result lead: they are what the row is scanned for, so a long
    # generated title can no longer push them off the end.
    assert headline == "Jakobe Thomas  ·  No Gain"
    assert situation == "00:43 – 01:39.950  (57.0s)"


def test_sidebar_ledger_groups_by_quarter_and_keeps_clip_identity(qapp):
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    clips = [
        Clip(
            start_ms=0, end_ms=5_000, clip_number=1,
            clip_title="Inside Zone",
            details={"quarter": "Q1", "run_pass": "Run"}),
        Clip(
            start_ms=5_000, end_ms=10_000, clip_number=2,
            clip_title="Boundary Screen",
            details={"quarter": "Q2", "run_pass": "Pass",
                     "play_type": "Screen"}),
    ]

    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")

    assert widget.table.rowCount() == 4
    assert widget.table.item(0, COL_NUM).text().startswith("v  Q1")
    assert widget.table.item(2, COL_NUM).text().startswith("v  Q2")
    assert widget.select_clip_id(clips[1].id)
    assert widget.selected_clip_ids() == [clips[1].id]
    assert widget.selected_rows() == [1]
    widget._cell_clicked(2, COL_NUM)
    assert widget.table.isRowHidden(widget._clip_rows[clips[1].id])
    assert widget.table.item(2, COL_NUM).text().startswith(">  Q2")


def test_sidebar_filters_review_states_without_losing_groups(qapp):
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    clips = [
        Clip(
            start_ms=0, end_ms=5_000, clip_number=1,
            clip_title="Logged",
            details={"quarter": "Q1", "run_pass": "Run"}),
        Clip(
            start_ms=5_000, end_ms=10_000, clip_number=2,
            clip_title="Unlogged"),
    ]
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")

    widget.unlogged_filter_btn.click()

    assert widget.table.isRowHidden(widget._clip_rows[clips[0].id])
    assert not widget.table.isRowHidden(widget._clip_rows[clips[1].id])
    assert widget.filter_count.text() == "1 of 2"


def test_sidebar_targeted_update_regroups_a_changed_quarter(qapp):
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    clips = [
        Clip(
            start_ms=0, end_ms=5_000, clip_number=1,
            clip_title="Run", details={"quarter": "Q1"}),
        Clip(
            start_ms=5_000, end_ms=10_000, clip_number=2,
            clip_title="Pass", details={"quarter": "Q2"}),
    ]
    widget.set_clips(clips, 600_000, "{clip_number}", "Game", "-")
    widget.select_clip_id(clips[0].id)

    clips[0].details["quarter"] = "Q2"
    widget.update_row(clips[0], 600_000, "{clip_number}", "Game", "-")

    assert widget.table.rowCount() == 3
    assert widget.table.item(0, COL_NUM).text().startswith("v  Q2")
    assert widget.selected_clip_ids() == [clips[0].id]


def test_sidebar_trim_preview_targets_clip_row_not_group_header(qapp):
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    clip = Clip(
        start_ms=0, end_ms=5_000, clip_number=1,
        clip_title="Run", details={"quarter": "Q1"})
    widget.set_clips([clip], 600_000, "{clip_number}", "Game", "-")

    widget.preview_bounds(clip.id, 1_000, 4_500)

    row = widget._clip_rows[clip.id]
    assert widget.table.item(row, COL_START).text() == "00:01"
    assert widget.table.item(row, COL_DUR).text() == "00:03.500"
    assert widget.table.item(row, COL_TITLE).text().splitlines()[1] == (
        "Q1   ·   00:01 – 00:04.500  (3.5s)"
    )


def test_sidebar_row_leads_with_player_and_result(qapp):
    """A long generated title must not push the scannable facts off the end."""
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    clip = Clip(
        start_ms=51_016,
        end_ms=72_600,
        clip_number=3,
        clip_title="Mark Fletcher Jr. - Q1 1st & 10 on 44 Run RPO - First Down",
        details={
            "quarter": "Q1", "down_distance": "2 & 8", "ball_on": "45",
            "run_pass": "Run", "play_type": "RPO",
            "result": "First Down", "player_name": "Mark Fletcher Jr.",
        },
    )
    widget.set_clips([clip], 600_000, "{clip_number}", "Game", "-")

    headline, situation = widget.table.item(
        widget._clip_rows[clip.id], COL_TITLE).text().splitlines()
    assert headline == "Mark Fletcher Jr.  ·  First Down"
    assert situation == (
        "Q1 2 & 8 on 45   ·   Run  ·  RPO   ·   00:51.016 – 01:12.600  (21.6s)")


def test_sidebar_row_falls_back_to_the_title_when_nothing_is_logged(qapp):
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    clip = Clip(start_ms=0, end_ms=4_000, clip_number=1, clip_title="Play 001")
    widget.set_clips([clip], 600_000, "{clip_number}", "Game", "-")

    headline = widget.table.item(
        widget._clip_rows[clip.id], COL_TITLE).text().splitlines()[0]
    assert headline == "Play 001"


def test_sidebar_needs_fix_row_drops_the_redundant_bang(qapp):
    """The row carries a NEEDS FIX badge, so the headline should not repeat it."""
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    clip = Clip(start_ms=0, end_ms=4_000, clip_number=1, clip_title="Play 001")
    # A clip running past the end of the source is a real computed warning.
    widget.set_clips([clip], 2_000, "{clip_number}", "Game", "-")

    row = widget._clip_rows[clip.id]
    assert widget._warnings.get(clip.id), "expected a computed warning"
    assert widget.table.item(row, COL_STATUS).data(
        REVIEW_KIND_ROLE) == "needs_fix"
    assert widget.table.item(row, COL_STATUS).text() == "NEEDS FIX"
    headline = widget.table.item(row, COL_TITLE).text().splitlines()[0]
    assert not headline.startswith("!")
