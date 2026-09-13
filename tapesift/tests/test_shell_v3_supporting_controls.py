"""standard supporting-dialog controls retain existing project/settings commits."""

from copy import deepcopy
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QPushButton, QWidget

from tapesift.services.project_service import ProjectSession
from tapesift.services.tag_style_service import DEFAULT_STYLE, primary_timeline_tag, style_for_tag
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
from tapesift.ui_v3.dialog_surface import ProjectSettingsDialogV3, ResultManagerDialogV3, TagColorManagerDialogV3
from tapesift.ui_v3.main_window import MainWindowV3


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _close(dialog, qapp):
    dialog.close()
    dialog.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def _tree_item(dialog, value):
    return next(dialog.result_tree.topLevelItem(i).child(j)
                for i in range(dialog.result_tree.topLevelItemCount())
                for j in range(dialog.result_tree.topLevelItem(i).childCount())
                if dialog.result_tree.topLevelItem(i).child(j).text(0) == value)


def test_tag_display_order_reopens_with_unchanged_styles_and_primary_precedence(qapp, tmp_path):
    original = {"run": {"color": "#123456", "category": "Rushing"},
                "historic_custom": {"color": "#abcdef", "primary": False}}
    dialog = TagColorManagerDialogV3(["Run", "Pass", "Screen"], original)
    dialog._rows["pass"]["category"].setText("Passing")
    dialog.table.setCurrentCell(dialog._rows["screen"]["row"], 0)
    dialog.move_style_up.click()
    dialog.move_style_up.click()
    assert dialog._display_keys() == ["screen", "run", "pass"]
    assert dialog.preview_flow.itemAt(0).widget() is dialog.preview_labels["screen"]
    staged = dialog.tag_styles()
    assert list(staged) == ["screen", "run", "pass", "historic_custom"]
    assert staged["screen"] == DEFAULT_STYLE
    assert style_for_tag("Run", staged) == style_for_tag("Run", original)
    assert style_for_tag("Historic custom", staged) == style_for_tag("Historic custom", original)
    assert primary_timeline_tag(["Run", "Screen"], staged) == ("rushing", "Rushing", "#123456")
    assert "screen" not in original
    session = ProjectSession.create("Tag order QA", tmp_path, tmp_path / "exports")
    session.project.tag_styles = staged
    session.save()
    reopened = ProjectSession.open_read_only(session.db_path)
    restored = TagColorManagerDialogV3(["Pass", "Run", "Screen", "New tag"], reopened.project.tag_styles)
    assert restored._display_keys() == ["screen", "run", "pass", "new_tag"]
    assert list(restored.tag_styles()) == ["screen", "run", "pass", "new_tag", "historic_custom"]
    reopened.close()
    session.close()
    _close(restored, qapp)
    _close(dialog, qapp)


def test_filtered_tag_moves_keep_hidden_controls_and_reset_restores_alphabetical_defaults(qapp):
    dialog = TagColorManagerDialogV3(["Run", "Pass", "Screen"], {})
    dialog._rows["pass"]["category"].setText("Visible")
    dialog._rows["screen"]["category"].setText("Visible")
    dialog._set_color_button(dialog._rows["run"]["color"], "#123456")
    dialog._rows["run"]["primary"].setChecked(False)
    hidden_widgets = {name: id(value) for name, value in dialog._rows["run"].items()}
    dialog.filter_edit.setText("Visible")
    dialog.table.setCurrentCell(dialog._rows["screen"]["row"], 0)
    dialog.move_style_up.click()
    assert dialog._display_keys() == ["screen", "pass", "run"]
    assert dialog.table.isRowHidden(dialog._rows["run"]["row"])
    assert {name: id(value) for name, value in dialog._rows["run"].items()} == hidden_widgets
    assert dialog.tag_styles()["run"]["color"] == "#123456"
    assert dialog.tag_styles()["run"]["primary"] is False
    dialog.table.setCurrentCell(dialog._rows["run"]["row"], 0)
    assert not dialog.move_style_up.isEnabled() and not dialog.move_style_down.isEnabled()
    before = dialog._display_keys()
    dialog._move_style(-1)
    assert dialog._display_keys() == before
    dialog.filter_edit.clear()
    dialog._restore_defaults()
    assert dialog._display_keys() == ["pass", "run", "screen"]
    assert dialog.tag_styles() == {}
    _close(dialog, qapp)


def test_native_tag_grip_drag_reorders_display_without_rebinding_row_controls(qapp):
    dialog = TagColorManagerDialogV3(["Run", "Pass", "Screen"], {})
    dialog.show()
    qapp.processEvents()
    header = dialog.table.verticalHeader()
    source = QPoint(header.width() // 2, header.sectionViewportPosition(2) + header.sectionSize(2) // 2)
    target = QPoint(header.width() // 2, header.sectionViewportPosition(0) + 2)
    QTest.mousePress(header.viewport(), Qt.MouseButton.LeftButton, pos=source)
    QTest.mouseMove(header.viewport(), QPoint(source.x(), source.y() - 15), delay=20)
    QTest.mouseMove(header.viewport(), target, delay=20)
    QTest.mouseRelease(header.viewport(), Qt.MouseButton.LeftButton, pos=target)
    qapp.processEvents()
    assert dialog._display_keys() == ["screen", "pass", "run"]
    assert dialog.table.cellWidget(dialog._rows["run"]["row"], 1) is dialog._rows["run"]["color"]
    _close(dialog, qapp)


@pytest.mark.parametrize("route", ["tag", "project"])
@pytest.mark.parametrize("outcome", ["cancel", "fail_cancel", "fail_retry"])
def test_tag_order_uses_existing_save_cancel_and_failed_save_boundary(qapp, tmp_path, monkeypatch, route, outcome):
    session = ProjectSession.create("Tag ordering transaction", tmp_path, tmp_path / "exports")
    session.project.tag_styles = {"run": dict(DEFAULT_STYLE, color="#123456")}
    session.save()
    before = deepcopy(session.project.tag_styles)
    host = QWidget()
    host.session = session
    host._require_session = lambda: True
    host._known_tags = lambda: ["Pass", "Run", "Screen"]
    host.project_settings_dialog_class = ProjectSettingsDialogV3
    host._refresh_clip_list = lambda: None
    host._index_current_project = lambda: None
    host.export_panel = SimpleNamespace(set_output_folder=lambda _value: None)
    host.statusBar = lambda: SimpleNamespace(showMessage=lambda *_args: None)
    monkeypatch.setattr(QMessageBox, "critical", lambda *_args: None)
    real_save = session.save
    writes = []
    def persist():
        writes.append(True)
        if len(writes) == 1:
            raise OSError("Injected order save failure")
        real_save()
    session.save = persist
    calls = []
    def interact(dialog):
        calls.append(id(dialog))
        if len(calls) == 1:
            style_dialog = dialog if route == "tag" else TagColorManagerDialogV3(host._known_tags(), dialog._tag_styles, dialog)
            style_dialog.table.setCurrentCell(style_dialog._rows["screen"]["row"], 0)
            style_dialog._move_style(-1)
            style_dialog._move_style(-1)
            if route == "project":
                dialog._tag_styles = style_dialog.tag_styles()
            assert session.project.tag_styles == before
            if outcome == "cancel":
                dialog.reject()
            else:
                dialog.accept()
        else:
            disk = ProjectSession.open_read_only(session.db_path)
            assert list(disk.project.tag_styles) == list(before)
            assert disk.project.tag_styles == session.project.tag_styles == before
            disk.close()
            if outcome == "fail_retry":
                dialog.accept()
            else:
                dialog.reject()
        return dialog.result()
    cls = TagColorManagerDialogV3 if route == "tag" else ProjectSettingsDialogV3
    monkeypatch.setattr(cls, "exec", interact)
    if route == "tag":
        MainWindowV3._open_v3_tag_colors(host)
    else:
        MainWindowWorkflow._open_project_settings(host)
    disk = ProjectSession.open_read_only(session.db_path)
    if outcome == "fail_retry":
        assert list(disk.project.tag_styles) == list(session.project.tag_styles) == ["screen", "run", "pass"]
        assert disk.project.tag_styles["run"]["color"] == "#123456"
    else:
        assert list(disk.project.tag_styles) == list(before)
        assert disk.project.tag_styles == session.project.tag_styles == before
    assert len(calls) == (1 if outcome == "cancel" else 2)
    assert len(set(calls)) == 1
    disk.close()
    session.conn.close()
    _close(host, qapp)


def test_result_row_add_and_remove_controls_respect_six_limit_and_leave_library_intact(qapp):
    dialog = ResultManagerDialogV3(["TD", "Sack", "Completion", "First Down", "Interception"], ["Custom QA"])
    original = dialog.results()
    add = dialog.result_tree.itemWidget(_tree_item(dialog, "Custom QA"), 1)
    assert not add.icon().isNull() and add.isEnabled()
    add.click()
    qapp.processEvents()
    assert dialog.favorites()[-1] == "Custom QA" and len(dialog.favorites()) == 6
    assert not dialog.result_tree.itemWidget(_tree_item(dialog, "Fumble Lost"), 1).isEnabled()
    assert not dialog.result_tree.itemWidget(_tree_item(dialog, "TD"), 1).isEnabled()
    row = dialog.favorite_list.itemWidget(dialog.favorite_list.item(5))
    remove = row.findChild(QPushButton, "V3ResultRemove")
    assert not remove.icon().isNull()
    remove.click()
    qapp.processEvents()
    assert dialog.favorites() == ["TD", "Sack", "Completion", "First Down", "Interception"]
    assert dialog.results() == original
    assert dialog.result_tree.itemWidget(_tree_item(dialog, "Custom QA"), 1).isEnabled()
    _close(dialog, qapp)


def test_result_action_icons_follow_visible_selection_and_custom_rules(qapp, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    dialog = ResultManagerDialogV3(["TD"], ["Custom QA"])
    buttons = dialog.result_action_buttons
    assert all(not buttons[name].icon().isNull() for name in (
        "Add to fast results", "Remove from fast results", "Rename custom result", "Remove custom result"))
    dialog.result_tree.setCurrentItem(_tree_item(dialog, "Sack"))
    assert buttons["Add to fast results"].isEnabled()
    assert not buttons["Rename custom result"].isEnabled()
    assert not buttons["Remove custom result"].isEnabled()
    dialog.result_tree.setCurrentItem(_tree_item(dialog, "Custom QA"))
    assert buttons["Rename custom result"].isEnabled() and buttons["Remove custom result"].isEnabled()
    monkeypatch.setattr(QInputDialog, "getText", lambda *_args, **_kwargs: ("Renamed QA", True))
    buttons["Rename custom result"].click()
    qapp.processEvents()
    assert "Renamed QA" in dialog.results() and "Custom QA" not in dialog.results()
    dialog.result_tree.setCurrentItem(_tree_item(dialog, "Renamed QA"))
    dialog.result_filter.setText("Sack")
    assert not any(buttons[name].isEnabled() for name in (
        "Add to fast results", "Rename custom result", "Remove custom result"))
    assert dialog.result_list.currentItem() is None
    dialog.result_filter.clear()
    dialog.result_tree.setCurrentItem(_tree_item(dialog, "Renamed QA"))
    buttons["Remove custom result"].click()
    qapp.processEvents()
    assert "Renamed QA" not in dialog.results() and "TD" in dialog.results()
    _close(dialog, qapp)


@pytest.mark.parametrize("save", [False, True])
def test_preview_remove_uses_favorite_draft_and_save_cancel_keeps_logged_results(qapp, tmp_path, monkeypatch, save):
    from tapesift.core.config import AppSettings
    from tapesift.models.clip import Clip
    from tapesift.ui_core.clip_editor import ClipEditor
    target = tmp_path / "preview-favorites.json"
    original_save = AppSettings.save
    monkeypatch.setattr(AppSettings, "save", lambda settings: original_save(settings, target))
    settings = AppSettings(result_favorites=["TD", "Sack"])
    settings.save()
    disk_before = target.read_bytes()
    editor = ClipEditor(settings)
    editor.set_result_choices([])
    editor.result_dialog_class = ResultManagerDialogV3
    editor.set_clip(Clip(start_ms=1000, end_ms=2000))
    editor.detail_edits["result"].setText("TD; Historic result")
    def interact(dialog):
        original = dialog.results()
        dialog.preview_remove_buttons["TD"].click()
        qapp.processEvents()
        assert dialog.favorites() == ["Sack"]
        assert dialog.results() == original
        assert settings.result_favorites == ["TD", "Sack"]
        assert target.read_bytes() == disk_before
        if save:
            dialog.accept()
        else:
            dialog.reject()
        return dialog.result()
    monkeypatch.setattr(ResultManagerDialogV3, "exec", interact)
    editor._manage_results()
    expected = ["Sack"] if save else ["TD", "Sack"]
    assert settings.result_favorites == AppSettings.load(target).result_favorites == expected
    assert editor.detail_edits["result"].text() == "TD; Historic result"
    _close(editor, qapp)


def test_removing_last_preview_favorite_shows_passive_default_preview(qapp):
    dialog = ResultManagerDialogV3(["Sack"], [])
    dialog.preview_remove_buttons["Sack"].click()
    qapp.processEvents()
    assert dialog.favorites() == []
    assert dialog.preview_remove_buttons == {}
    assert "Default chips" in dialog.preview_caption.text()
    assert "Sack" in dialog.results()
    _close(dialog, qapp)


def test_tag_preview_keeps_raw_labels_when_display_categories_are_shared(qapp):
    tags = ["RPO Run", "RPO Pass", "INT", "TD", "Custom film tag"]
    styles = {"rpo_run": {"category": "Offense", "color": "#123456"},
              "rpo_pass": {"category": "Offense", "color": "#123456"},
              "int": {"category": "Turnover"}, "td": {"category": "Score"}}
    dialog = TagColorManagerDialogV3(tags, styles)
    before = primary_timeline_tag(["RPO Run", "INT"], dialog.tag_styles())
    assert {label.text() for label in dialog.preview_labels.values()} == set(tags)
    assert "Category: Offense" in dialog.preview_labels["rpo_run"].toolTip()
    dialog._rows["rpo_pass"]["category"].setText("Passing")
    assert dialog.preview_labels["rpo_pass"].text() == "RPO Pass"
    assert dialog.tag_styles()["rpo_pass"]["category"] == "Passing"
    assert primary_timeline_tag(["RPO Run", "INT"], dialog.tag_styles()) == before
    assert dialog.style_reassurance.text() == "These styling settings do not remove tags from clips, search, or exports."
    assert not dialog.filter_edit.actions()[0].icon().isNull()
    _close(dialog, qapp)


@pytest.mark.parametrize("size", [(1100, 730), (800, 600), (760, 520)])
def test_supporting_previews_stay_fully_visible_above_pinned_footer_at_scroll_endpoints(qapp, size):
    from PySide6.QtWidgets import QDialogButtonBox, QLabel, QToolButton
    from tapesift.ui_v2.fonts import load_v2_fonts
    from tapesift.ui_v3.theme import stylesheet
    original_style = qapp.styleSheet()
    load_v2_fonts()
    qapp.setStyleSheet(stylesheet())
    tags = ["Run", "Pass", "RPO Run", "RPO Pass", "Screen", "Sack", "INT", "TD", "First Down"]
    dialogs = [TagColorManagerDialogV3(tags, {}),
               ResultManagerDialogV3(["Fumble Recovered", "Turnover on Downs", "Completion", "Interception", "Sack", "Touchdown"], [])]
    def rect_in(widget, parent):
        rect = widget.rect()
        rect.moveTopLeft(widget.mapTo(parent, QPoint()))
        return rect
    try:
        for dialog in dialogs:
            dialog.resize(*size)
            dialog.show()
            qapp.processEvents()
            assert (dialog.width(), dialog.height()) == size
            search = getattr(dialog, "filter_edit", None) or dialog.result_filter
            action = search.actions()[0]
            search_button = next(button for button in search.findChildren(QToolButton)
                                 if button.defaultAction() is action)
            assert search.rect().contains(search_button.geometry())
            assert abs(search_button.geometry().center().y() - search.rect().center().y()) <= 1
            assert not dialog.support_scroll.isAncestorOf(dialog.preview)
            position = dialog.preview.mapTo(dialog, QPoint())
            scroll = dialog.support_scroll.verticalScrollBar()
            for endpoint in (scroll.minimum(), scroll.maximum()):
                scroll.setValue(endpoint)
                qapp.processEvents()
                assert dialog.preview.mapTo(dialog, QPoint()) == position
                preview_rect = rect_in(dialog.preview, dialog)
                footer_rect = rect_in(dialog.support_footer, dialog)
                assert dialog.rect().contains(preview_rect)
                assert preview_rect.bottom() < footer_rect.top()
                for chip in dialog.preview.findChildren(QLabel):
                    assert dialog.preview.rect().contains(rect_in(chip, dialog.preview))
                for button in getattr(dialog, "preview_remove_buttons", {}).values():
                    assert dialog.preview.rect().contains(rect_in(button, dialog.preview))
                for button in dialog.findChild(QDialogButtonBox).buttons():
                    assert dialog.rect().contains(rect_in(button, dialog))
    finally:
        for dialog in dialogs:
            _close(dialog, qapp)
        qapp.setStyleSheet(original_style)


def test_supporting_control_icons_use_official_asset_geometry(qapp):
    from pathlib import Path
    from tapesift.ui_v3.dialog_surface import _support_action_icon
    from tapesift.ui_v3.icons import icon_path
    import tapesift.ui_v3.dialog_surface as source
    assert "<svg" not in Path(source.__file__).read_text(encoding="utf-8")
    for name in ("grip", "trash", "left", "right", "up", "down"):
        assert not _support_action_icon(name).isNull()
    for name in ("color-24.svg", "search-16.svg", "arrow-reset-24.svg", "question-circle-24.svg"):
        assert icon_path(name).is_file()


@pytest.mark.parametrize("count", [24, 80])
def test_many_tag_preview_leaves_compact_editors_and_footer_reachable(qapp, count):
    from PySide6.QtWidgets import QDialogButtonBox
    from tapesift.ui_v2.fonts import load_v2_fonts
    from tapesift.ui_v3.theme import stylesheet
    original_style = qapp.styleSheet()
    load_v2_fonts()
    qapp.setStyleSheet(stylesheet())
    dialog = TagColorManagerDialogV3([f"Custom film tag {i:02d}" for i in range(count)], {})
    try:
        dialog.resize(800, 600)
        dialog.show()
        qapp.processEvents()
        assert (dialog.width(), dialog.height()) == (800, 600)
        assert dialog.support_scroll.viewport().height() >= 160, {
            "body_height": dialog.support_scroll.viewport().height(),
            "preview_height": dialog.support_preview.height()}
        assert len(dialog.preview_labels) == count
        assert dialog.preview_scroll.height() <= 144
        assert dialog.preview_scroll.verticalScrollBar().maximum() > 0
        last = dialog.preview_labels[f"custom_film_tag_{count - 1:02d}"]
        dialog.preview_scroll.ensureWidgetVisible(last)
        qapp.processEvents()
        last_rect = last.rect()
        last_rect.moveTopLeft(last.mapTo(dialog.preview_scroll.viewport(), QPoint()))
        assert dialog.preview_scroll.viewport().rect().contains(last_rect)
        for button in dialog.findChild(QDialogButtonBox).buttons():
            rect = button.rect()
            rect.moveTopLeft(button.mapTo(dialog, QPoint()))
            assert dialog.rect().contains(rect)
    finally:
        _close(dialog, qapp)
        qapp.setStyleSheet(original_style)
