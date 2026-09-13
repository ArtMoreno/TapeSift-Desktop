"""Locked populated, empty, and restored checks for Shell V3 Library."""

from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPixmap, QStandardItemModel
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem, QWidget

from tapesift.core.config import AppSettings
from tapesift.services import library_service
from tapesift.services.library_service import LibraryRow
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
from tapesift.ui_v3.library_screen import LibrarySearchScreenV3, ResultRowDelegateV3
from tapesift.ui_v3.main_window import MainWindowV3
from tapesift.ui_v2.fonts import load_v2_fonts
from tapesift.ui_v3.theme import stylesheet


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    load_v2_fonts()
    for filename in ("segoeui.ttf", "seguisb.ttf", "segmdl2.ttf", "consola.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + filename)
    app.setStyleSheet(stylesheet())
    return app


@pytest.fixture
def settings(tmp_path):
    value = AppSettings(onboarding_seen=True)
    value.default_project_folder = str(tmp_path)
    value.default_output_folder = str(tmp_path / "exports")
    value.save = lambda *args, **kwargs: None
    return value


def _row(*, thumbnail_path: str = "") -> LibraryRow:
    return LibraryRow(
        clip_uid="project:clip-1",
        project_path="C:/project/game.tapesift",
        project_name="EXAMPLE HOME O VS. AWAY D",
        source_video_path="C:/film/game.mp4",
        clip_id="clip-1",
        clip_number=50,
        clip_title="Play 050",
        start_ms=1_599_000,
        end_ms=1_637_100,
        tags=["Run", "First Down"],
        player_name="#2 RB",
        play_type="Run",
        quarter="3",
        down_distance="2nd & 6",
        result="First Down",
        notes="Good finish.",
        thumbnail_path=thumbnail_path,
        details={
            "run_pass": "Run",
            "off_formation": "Pistol",
            "down_distance": "2nd & 6",
            "result": "First Down",
        },
    )


def _stub_library(monkeypatch, rows: list[LibraryRow]) -> None:
    projects = 1 if rows else 0
    monkeypatch.setattr(
        library_service, "stats", lambda: (len(rows), projects))
    monkeypatch.setattr(
        library_service, "projects", lambda: [
            ("EXAMPLE HOME O VS. AWAY D", "C:/project/game.tapesift")
        ] if rows else [])
    monkeypatch.setattr(library_service, "opponents", lambda: [])
    monkeypatch.setattr(library_service, "players", lambda: [])
    monkeypatch.setattr(library_service, "all_tags", lambda: [])
    monkeypatch.setattr(
        library_service, "search", lambda *args, **kwargs: list(rows))
    monkeypatch.setattr(
        library_service, "rank_results", lambda values, _text: list(values))


def _dispose(screen: LibrarySearchScreenV3, qapp: QApplication) -> None:
    screen._stop_inline_preview()
    screen.preview_player.stop()
    screen.preview_player.setSource(QUrl())
    screen.preview_player.setVideoOutput(None)
    screen.preview_player.setAudioOutput(None)
    screen.close()
    screen.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_search_updates_preview_only_after_result_replacement(
        qapp, settings, monkeypatch):
    rows = [replace(_row(), clip_id=f"clip-{i}", clip_uid=f"project:clip-{i}")
            for i in range(3)]
    _stub_library(monkeypatch, rows)
    screen = LibrarySearchScreenV3(settings)
    try:
        screen.refresh()
        assert screen.results_list.count() == 3
        screen.results_list.setCurrentRow(2)
        assert screen._editing_row.clip_id == "clip-2"
        observed = []
        original = screen._show_preview

        def preview(row, count=0):
            observed.append((row.clip_id if row else None, screen.results_list.count()))
            original(row, count)

        monkeypatch.setattr(screen, "_show_preview", preview)
        rows[:] = [replace(_row(), clip_id="replacement", clip_uid="project:replacement")]
        screen._run_search()
        assert observed == [(None, 1)]
        screen.results_list.setCurrentRow(0)
        assert observed[-1] == ("replacement", 1)
        assert screen._editing_row.clip_id == "replacement"
    finally:
        _dispose(screen, qapp)


def test_sort_and_empty_search_release_old_rows_and_reset_preview_once(
        qapp, settings, monkeypatch):
    import shiboken6
    rows = [replace(_row(), clip_id=f"clip-{i}", clip_uid=f"project:clip-{i}",
                    start_ms=(3-i)*1000, end_ms=(4-i)*1000) for i in range(3)]
    _stub_library(monkeypatch, rows)
    screen = LibrarySearchScreenV3(settings)
    try:
        screen.refresh()
        seen = []
        original = screen._show_preview
        def preview(row, count=0):
            seen.append((row.clip_id if row else None, screen.results_list.count()))
            original(row, count)
        monkeypatch.setattr(screen, "_show_preview", preview)
        for _ in range(8):
            screen.results_list.setCurrentRow(0)
            previous = [screen.results_list.item(i) for i in range(3)]
            seen.clear()
            if screen.sort_combo.currentText() != "Source Time":
                screen.sort_combo.setCurrentText("Source Time")
            else:
                screen._run_search()
            assert [screen.results_list.item(i).data(Qt.ItemDataRole.UserRole).clip_id
                    for i in range(3)] == ["clip-2", "clip-1", "clip-0"]
            assert all(not shiboken6.isValid(item) for item in previous)
            assert seen == [(None, 3)]
        rows.clear()
        seen.clear()
        screen._run_search()
        assert seen == [(None, 0)]
        assert screen._editing_row is None
        assert not screen.save_btn.isVisible()
    finally:
        _dispose(screen, qapp)


def test_v3_library_empty_state_is_purposeful(
        qapp, settings, monkeypatch):
    _stub_library(monkeypatch, [])
    screen = LibrarySearchScreenV3(settings)
    screen.resize(1700, 820)
    screen.show()
    screen.refresh()
    qapp.processEvents()
    try:
        assert screen.masthead_stats.text() == "0 CLIPS / 0 PROJECTS"
        assert screen._library_ledger_empty.isVisible()
        assert not screen.results_list.isVisible()
        assert screen.preview_thumb.currentWidget() is \
            screen._library_empty_preview
        assert all(not label.isVisible()
                   for label in screen._library_detail_labels)
        assert not screen.export_btn.isEnabled()
        assert not screen.reel_btn.isEnabled()
        assert not screen.save_btn.isVisible()
        assert not screen.preview_jog_ring.isVisible()
    finally:
        _dispose(screen, qapp)


def test_v3_library_real_thumbnail_drives_the_existing_inspector(
        qapp, settings, tmp_path, monkeypatch):
    thumbnail = tmp_path / "clip.png"
    pixmap = QPixmap(320, 180)
    pixmap.fill(QColor("#2f6b3b"))
    assert pixmap.save(str(thumbnail))
    row = _row(thumbnail_path=str(thumbnail))
    _stub_library(monkeypatch, [row])
    screen = LibrarySearchScreenV3(settings)
    screen.resize(1700, 820)
    screen.show()
    screen.refresh()
    screen.results_list.setCurrentRow(0)
    qapp.processEvents()
    try:
        shown = screen.preview_placeholder.pixmap()
        assert shown is not None and not shown.isNull()
        assert screen.preview_title.text() == "Play 050"
        assert screen.preview_details["run_pass"].text() == "Run"
        assert screen.save_btn.isVisible()
        assert screen.preview_play_btn.isVisible()
        assert not screen.preview_jog_ring.isVisible()
        assert all(label.isVisible()
                   for label in screen._library_detail_labels)

        screen.preview_placeholder.setFixedSize(QSize(420, 150))
        screen._rescale_selected_thumbnail()
        resized = screen.preview_placeholder.pixmap()
        assert resized is not None and not resized.isNull()
        assert resized.width() <= 420
        assert resized.height() <= 150
    finally:
        _dispose(screen, qapp)


def test_v3_library_navigation_and_restored_geometry(
        qapp, settings, monkeypatch):
    _stub_library(monkeypatch, [_row()])
    screen = LibrarySearchScreenV3(settings)
    requested: list[str] = []
    screen.back_requested.connect(lambda: requested.append("home"))
    screen.resize(1354, 718)
    screen._sync_library_geometry()
    assert screen._library_workbench.minimumHeight() == 0
    assert screen._library_workbench.maximumHeight() > 718
    screen.show()
    screen.refresh()
    qapp.processEvents()
    try:
        assert screen.library_home_button.property("iconAsset") == \
            "tapesift-home.png"
        screen.library_home_button.click()
        assert requested == ["home"]
        assert screen.library_home_button.accessibleName() == "Home"
        assert screen.library_back_button.accessibleName() == "Back"
    finally:
        _dispose(screen, qapp)


def test_v3_library_restored_geometry_is_locked_in_the_real_shell(
        qapp, settings, tmp_path, monkeypatch):
    rows = [
        replace(
            _row(),
            clip_uid=f"project:clip-{index}",
            clip_id=f"clip-{index}",
            clip_number=49 + index,
            clip_title=f"Play {49 + index:03d}",
        )
        for index in range(1, 7)
    ]
    _stub_library(monkeypatch, rows)
    monkeypatch.setattr(
        MainWindowV3,
        "_check_recovery",
        lambda self: None,
    )
    window = MainWindowV3(
        settings,
        workspace_state_path=tmp_path / "shell-v3.json",
    )
    window._screen_fit_done = True
    window.resize(1366, 775)
    window.show()
    window.show_library()
    qapp.processEvents()
    try:
        screen = window.library_screen
        assert window.size() == QSize(1366, 775)
        # The IFI masthead is 72 px, with no extra client inset.
        assert screen.height() == 703
        assert screen._library_workbench.height() > 310
        ledger = QRect(screen._library_ledger.mapTo(screen, QPoint()), screen._library_ledger.size())
        inspector = QRect(screen._library_workbench.mapTo(screen, QPoint()), screen._library_workbench.size())
        assert ledger.right() < inspector.left()
        assert screen.rect().contains(inspector)
        assert screen.results_list.count() == 6
        viewport = screen.results_list.viewport().rect()
        for index in range(5):
            row_rect = screen.results_list.visualItemRect(
                screen.results_list.item(index))
            assert row_rect.isValid()
            assert row_rect.bottom() <= viewport.bottom()
        sort_origin = screen.sort_combo.mapTo(screen, QPoint(0, 0))
        assert sort_origin.x() + screen.sort_combo.width() \
            <= screen.rect().right()
    finally:
        window._app_closing = True
        window.library_screen._stop_inline_preview()
        window.player.unload()
        window.hide()
        window.deleteLater()
        qapp.processEvents()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        qapp.processEvents()


def test_v3_library_keeps_the_real_no_matches_state(
        qapp, settings, monkeypatch):
    row = _row()
    _stub_library(monkeypatch, [row])
    monkeypatch.setattr(
        library_service, "search", lambda *args, **kwargs: [])
    screen = LibrarySearchScreenV3(settings)
    screen.search_box.setText("no such clip")
    screen.resize(1700, 820)
    screen.show()
    screen.refresh()
    qapp.processEvents()
    try:
        assert not screen._library_ledger_empty.isVisible()
        assert not screen.results_list.isVisible()
        assert screen.empty_label.isVisible()
        assert "No clips match" in screen.empty_label.text()
    finally:
        _dispose(screen, qapp)


def test_v3_library_corrupt_thumbnail_cannot_leave_the_previous_image(
        qapp, settings, tmp_path, monkeypatch):
    valid = tmp_path / "valid.png"
    pixmap = QPixmap(160, 90)
    pixmap.fill(QColor("#2f6b3b"))
    assert pixmap.save(str(valid))
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    rows = [
        _row(thumbnail_path=str(valid)),
        LibraryRow(**{
            **_row(thumbnail_path=str(corrupt)).__dict__,
            "clip_uid": "project:clip-2",
            "clip_id": "clip-2",
            "clip_number": 51,
            "clip_title": "Play 051",
        }),
    ]
    _stub_library(monkeypatch, rows)
    screen = LibrarySearchScreenV3(settings)
    screen.resize(1700, 820)
    screen.show()
    screen.refresh()
    screen.results_list.setCurrentRow(0)
    qapp.processEvents()
    try:
        assert not screen.preview_placeholder.pixmap().isNull()
        screen.results_list.setCurrentRow(1)
        qapp.processEvents()
        shown = screen.preview_placeholder.pixmap()
        assert shown is None or shown.isNull()
        assert screen.preview_placeholder.text() == "No thumbnail"
    finally:
        _dispose(screen, qapp)


@pytest.mark.parametrize("notes_height", [72, 240])
@pytest.mark.parametrize("screen_height", [553, 820])
def test_library_inspector_scrolls_without_overlapping_fields(
        qapp, settings, monkeypatch, notes_height, screen_height):
    from tapesift.ui_v3.theme import stylesheet

    qapp.setStyleSheet(stylesheet())
    settings.library_notes_height = notes_height
    _stub_library(monkeypatch, [_row()])
    screen = LibrarySearchScreenV3(settings)
    screen.resize(1248 if screen_height < 660 else 1700, screen_height)
    screen.show()
    screen.refresh()
    screen.results_list.setCurrentRow(0)
    qapp.processEvents()
    try:
        fields = [QRect(field.mapTo(screen._preview_panel, QPoint()), field.size())
                  for field in screen.preview_details.values()]
        assert all(not left.intersects(right)
                   for index, left in enumerate(fields) for right in fields[index + 1:])
        assert screen.preview_notes.height() == notes_height
        scroll = screen._library_inspector_scroll
        assert scroll.verticalScrollBar().maximum() > 0
        workbench_left = screen._library_workbench.mapTo(screen, QPoint()).x()
        ledger_right = screen.results_list.mapTo(screen, screen.results_list.rect().topRight()).x()
        assert ledger_right < workbench_left
        inspector = screen._library_inspector
        first_positions = None
        for position in (0, scroll.verticalScrollBar().maximum()):
            scroll.verticalScrollBar().setValue(position)
            qapp.processEvents()
            positions = []
            for button in (screen.save_btn, screen.open_btn):
                assert button.isVisible() and button.isEnabled()
                assert button.parentWidget() is screen._library_inspector_actions
                assert not scroll.isAncestorOf(button)
                bounds = QRect(button.mapTo(inspector, QPoint()), button.size())
                assert inspector.rect().contains(bounds)
                assert not scroll.geometry().intersects(bounds)
                positions.append(button.mapTo(screen, QPoint()))
            if first_positions is None:
                first_positions = positions
            else:
                assert positions == first_positions
    finally:
        _dispose(screen, qapp)


def test_library_clip_navigation_and_review_use_the_selected_result(qapp, settings, monkeypatch):
    rows = [_row(), replace(_row(), clip_uid="other:clip2", clip_id="clip2", project_path="D:/other.tapesift")]
    _stub_library(monkeypatch, rows)
    screen = LibrarySearchScreenV3(settings)
    screen.refresh()
    opened = []
    monkeypatch.setattr(screen, "_open_in_project", opened.append)
    try:
        screen.results_list.setCurrentRow(0)
        controls = screen._library_transport
        assert not controls.step_back_btn.isEnabled()
        controls.step_fwd_btn.click()
        assert screen._editing_row is rows[1]
        assert screen._inline_preview_row is None
        assert controls.step_back_btn.isEnabled()
        assert not controls.step_fwd_btn.isEnabled()
        screen._library_review_button.click()
        assert opened == [rows[1]]
        screen.results_list.item(0).setSelected(True)
        assert screen._editing_row is None
        assert not controls.step_back_btn.isEnabled()
        assert not screen._library_review_button.isEnabled()
        screen._navigate_library_clip(-1)
        screen._review_library_clip()
        assert len(screen._selected_rows()) == 2
        assert opened == [rows[1]]
        # Ctrl-deselecting the current item can leave a different sole selection.
        screen.results_list.item(1).setSelected(False)
        assert screen.results_list.currentRow() == 1
        assert screen._editing_row is rows[0]
        assert not controls.step_back_btn.isEnabled()
        assert controls.step_fwd_btn.isEnabled()
        controls.step_fwd_btn.click()
        assert screen._editing_row is rows[1]
    finally:
        _dispose(screen, qapp)


def test_clear_library_filters_preserves_sort_and_does_not_navigate_back(qapp, settings, monkeypatch):
    _stub_library(monkeypatch, [_row()])
    screen = LibrarySearchScreenV3(settings)
    screen.refresh()
    back = []
    screen.back_requested.connect(lambda: back.append(True))
    try:
        screen.search_box.setText("run")
        screen._active_tags.add("Run")
        screen.project_combo.setCurrentIndex(1)
        screen.sort_combo.setCurrentIndex(1)
        order = screen.sort_combo.currentData()
        screen.clear_filters_button.click()
        assert not screen.search_box.text()
        assert not screen._active_tags
        assert screen.project_combo.currentIndex() == 0
        assert screen.sort_combo.currentData() == order
        screen.clear_filters_button.click()
        assert not back
    finally:
        _dispose(screen, qapp)


def test_library_ledger_can_paint_a_clip_without_optional_details(qapp, settings, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QStyleOptionViewItem

    _stub_library(monkeypatch, [replace(_row(), details=None, play_type="")])
    screen = LibrarySearchScreenV3(settings)
    screen.refresh()
    canvas = QPixmap(1200, 45)
    painter = QPainter(canvas)
    try:
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 1200, 45)
        screen.results_list.itemDelegate().paint(painter, option, screen.results_list.model().index(0, 0))
    finally:
        painter.end()
        _dispose(screen, qapp)


def test_library_transport_keeps_approved_proportions_after_style_and_playback_changes(
        qapp, settings, monkeypatch):
    from tapesift.ui_v3.theme import stylesheet
    _stub_library(monkeypatch, [_row()])
    previous_style = qapp.styleSheet()
    qapp.setStyleSheet(stylesheet())
    screen = LibrarySearchScreenV3(settings)
    screen.show()
    screen.refresh()
    screen.results_list.setCurrentRow(0)
    controls = screen._library_transport
    buttons = screen._library_transport_faces.buttons
    try:
        for width, height in ((1700, 820), (1248, 768), (1354, 718)):
            screen.resize(width, height)
            for playing in (True, False):
                screen._set_preview_play_state(playing)
                screen._sync_preview_transport_state(
                    screen.preview_player.PlaybackState.PlayingState if playing
                    else screen.preview_player.PlaybackState.PausedState)
                screen._apply_locked_font_families()
                qapp.processEvents()
                assert [button.size() for button in buttons] == [
                    QSize(39, 38), QSize(43, 38), QSize(44, 38), QSize(43, 38), QSize(39, 38)]
                assert controls._playing == playing
                assert all(controls.rect().contains(button.geometry()) for button in buttons)
                assert all(left.geometry().right() < right.geometry().left()
                           for left, right in zip(buttons, buttons[1:]))
    finally:
        _dispose(screen, qapp)
        qapp.setStyleSheet(previous_style)


@pytest.mark.parametrize("concept,action", [("Dropback", "Play Action"), ("Custom concept", "Custom action")])
def test_library_exposes_and_saves_recorded_concept_and_play_action(
        qapp, settings, monkeypatch, concept, action):
    row = replace(_row(), details=dict(_row().details, play_type=concept,
                                       play_action=action, yards="8"))
    _stub_library(monkeypatch, [row])
    screen = LibrarySearchScreenV3(settings)
    screen.resize(1248, 553)
    screen.show()
    screen.refresh()
    screen.results_list.setCurrentRow(0)
    qapp.processEvents()
    saved = []
    screen.apply_edit = lambda project, clip, changes: (saved.append((project,clip,changes)) or (True,""))
    try:
        edits = screen._library_play_edits
        assert edits["play_type"] is screen.preview_details["play_type"]
        assert edits["play_action"] is screen.preview_details["play_action"]
        assert edits["play_type"].text() == concept
        assert edits["play_action"].text() == action
        assert concept in edits["play_type"].toolTip()
        assert action in edits["play_action"].toolTip()
        scroll = screen._library_inspector_scroll
        for key, editor in edits.items():
            label = screen._library_play_labels[key]
            assert label.text() == ("Concept" if key == "play_type" else "Play action")
            assert label.buddy() is editor
            scroll.ensureWidgetVisible(editor)
            qapp.processEvents()
            origin = editor.mapTo(scroll.viewport(), QPoint(0,0))
            assert origin.y() >= 0
            assert origin.y()+editor.height() <= scroll.viewport().height()
        edits["play_type"].setText("Duo")
        edits["play_action"].setText("")
        screen.save_btn.click()
        assert len(saved) == 1
        project, clip, changes = saved[0]
        assert (project,clip) == (row.project_path,row.clip_id)
        assert changes["details"]["play_type"] == "Duo"
        assert changes["details"]["play_action"] == ""
        assert changes["details"]["yards"] == "8"
        assert changes["details"]["run_pass"] == "Run"
    finally:
        _dispose(screen,qapp)


def test_library_clears_new_play_axes_with_selection(qapp, settings, monkeypatch):
    rows = [_row(), replace(_row(), clip_uid="other:2", clip_id="2")]
    _stub_library(monkeypatch, rows)
    screen = LibrarySearchScreenV3(settings)
    screen.show()
    screen.refresh()
    screen.results_list.setCurrentRow(0)
    try:
        assert all(edit.isVisible() for edit in screen._library_play_edits.values())
        screen.results_list.item(1).setSelected(True)
        assert all(not edit.isVisible() and not edit.isEnabled()
                   for edit in screen._library_play_edits.values())
        assert not screen._library_inspector_actions.isVisible()
        assert not screen.save_btn.isVisible() and not screen.open_btn.isVisible()
    finally:
        _dispose(screen,qapp)


@pytest.mark.parametrize("recorded,legacy,expected", [
    ("Pass", "Dropback", "Pass"),
    ("Run", "Duo", "Run"),
    ("Special", "Punt", "Special"),
    ("No Play", "False Start", "No Play"),
    (None, "Pass", "Pass"),
    (None, " run ", "Run"),
    (None, "RPO", "—"),
    (None, "Dropback", "—"),
    ("", "Duo", "—"),
    ("Unknown", "Pass", "Unknown"),
])
def test_library_type_column_renders_family_without_inventing_one_from_concept(
        qapp, monkeypatch, recorded, legacy, expected):
    details = {} if recorded is None else {"run_pass": recorded}
    row = replace(_row(), play_type=legacy, details=details)
    model = QStandardItemModel(1, 1)
    index = model.index(0, 0)
    model.setData(index, row, Qt.ItemDataRole.UserRole)
    host = QWidget()
    delegate = ResultRowDelegateV3(host, host)
    painted_text = []
    monkeypatch.setattr(delegate, "_draw_text",
                        lambda painter, rect, value, *args: painted_text.append(value))
    pixmap = QPixmap(1200, 45)
    painter = QPainter(pixmap)
    option = QStyleOptionViewItem()
    option.rect = pixmap.rect()
    try:
        delegate.paint(painter, option, index)
    finally:
        painter.end()
        host.deleteLater()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert painted_text[3] == expected


@pytest.mark.parametrize("width,height", [(1690, 868), (1280, 728)])
def test_library_common_fields_are_editable_and_save_without_duplicate_values(
        qapp, settings, monkeypatch, width, height):
    row = replace(_row(), details=dict(_row().details, player_name="Malachi Toney",
        other_players="Darian Mensah", yards="8", quarterback="Darian Mensah"))
    _stub_library(monkeypatch, [row])
    screen = LibrarySearchScreenV3(settings)
    screen.resize(width, height)
    screen.show()
    screen.refresh()
    screen.results_list.setCurrentRow(0)
    qapp.processEvents()
    saved = []
    screen.apply_edit = lambda project, clip, changes: (saved.append(changes) or (True, ""))
    try:
        fields = screen.preview_details
        quick = ("run_pass", "player_name", "result", "other_players")
        viewport = screen._library_inspector_scroll.viewport()
        for key in quick:
            field = fields[key]
            assert field.isVisible() and field.isEnabled()
            screen._library_inspector_scroll.ensureWidgetVisible(field)
            qapp.processEvents()
            bounds = QRect(field.mapTo(viewport, QPoint()), field.size())
            assert viewport.rect().contains(bounds)
        assert fields["run_pass"].mapTo(screen, QPoint()).y() == fields["player_name"].mapTo(screen, QPoint()).y()
        assert fields["result"].mapTo(screen, QPoint()).y() == fields["other_players"].mapTo(screen, QPoint()).y()
        fields["player_name"].setText("Cooper Barkate")
        fields["other_players"].setText("Darian Mensah, Malachi Toney")
        fields["result"].setText("Reception; Completion; Gain")
        screen.save_btn.click()
        assert len(saved) == 1
        assert saved[0]["details"]["player_name"] == "Cooper Barkate"
        assert saved[0]["details"]["other_players"] == "Darian Mensah, Malachi Toney"
        assert saved[0]["details"]["yards"] == "8"
        assert saved[0]["details"]["quarterback"] == "Darian Mensah"
    finally:
        _dispose(screen, qapp)
