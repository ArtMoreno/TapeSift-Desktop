"""Behavior and hierarchy gates for the Shell V3 dialog family."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QDialog, QTabWidget, QLabel

from tapesift.core.config import AppSettings
from tapesift.services import ffmpeg_service, recovery_service
from tapesift.ui.play_detect_dialog import PlayDetectDialog
from tapesift.ui_v3.dialog_surface import (
    RecoveryAction,
    RecoveryDialogV3,
    SettingsDialogV3,
)
from tapesift.ui_v3.main_window import MainWindowV3
from tapesift.ui_v3.start_screen import NewProjectDialog as NewProjectDialogV3
import tapesift.ui_v3.main_window as main_window_module


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    value = AppSettings(onboarding_seen=True)
    value.default_project_folder = str(tmp_path)
    value.default_output_folder = str(tmp_path / "exports")
    value.save = lambda *args, **kwargs: None
    return value


def _destroy(widget, qapp: QApplication) -> None:
    widget.close()
    widget.deleteLater()
    qapp.processEvents()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_settings_does_not_offer_the_removed_tag_map_gutter(qapp):
    from PySide6.QtWidgets import QCheckBox
    settings = AppSettings()
    dialog = SettingsDialogV3(settings)
    assert all(check.text() != "Show tag-map labels" for check in dialog.findChildren(QCheckBox))
    _destroy(dialog, qapp)


def _destroy_window(window: MainWindowV3, qapp: QApplication) -> None:
    window._app_closing = True
    window.library_screen._stop_inline_preview()
    window.player.unload()
    window.hide()
    window.deleteLater()
    qapp.processEvents()


def test_phone_companion_is_dev_gated_in_shipped_v3(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.delenv("TAPESIFT_ENABLE_PHONE_COMPANION", raising=False)
    monkeypatch.setattr(MainWindowV3, "_check_recovery", lambda self: None)
    windows = []
    try:
        window = MainWindowV3(
            settings, workspace_state_path=tmp_path / "shell-v3-off.json")
        windows.append(window)
        file_menu = None
        for action in window.menuBar().actions():
            if action.text().replace("&", "") == "File":
                file_menu = action.menu()
                break
        assert file_menu is not None
        assert all(
                action.text().replace("&", "") != "Phone Companion…"
                for action in file_menu.actions())

        monkeypatch.setenv("TAPESIFT_ENABLE_PHONE_COMPANION", "1")
        window = MainWindowV3(
            settings, workspace_state_path=tmp_path / "shell-v3-on.json")
        windows.append(window)
        file_menu = None
        for action in window.menuBar().actions():
            if action.text().replace("&", "") == "File":
                file_menu = action.menu()
                break
        assert any(
            action.text().replace("&", "") == "Phone Companion…"
            for action in file_menu.actions())
    finally:
        for window in windows:
            _destroy_window(window, qapp)
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_settings_v3_rehosts_every_real_page_behind_side_navigation(
        qapp, settings, monkeypatch):
    monkeypatch.setattr(ffmpeg_service, "detect_hw_encoders", lambda _path: [])
    monkeypatch.setattr(ffmpeg_service, "get_version", lambda _path: "test")
    dialog = SettingsDialogV3(settings)
    dialog.show()
    qapp.processEvents()
    try:
        assert [dialog.section_nav.item(index).text()
                for index in range(dialog.section_nav.count())] == [
                    "General", "Playback", "First Read", "Clip Defaults",
                    "Export", "FFmpeg"
                ]
        assert dialog.section_stack.count() == 6
        assert dialog.findChild(QTabWidget, "SettingsTabs") is None
        dialog.section_nav.setCurrentRow(3)
        assert dialog.section_stack.currentIndex() == 3

        dialog.project_folder_edit.setText("D:/TapeSift/Projects")
        dialog._save()
        assert settings.default_project_folder == "D:/TapeSift/Projects"
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        _destroy(dialog, qapp)


def test_dialog_family_opens_at_the_locked_setup_dimensions(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg_service, "detect_hw_encoders", lambda _path: [])
    monkeypatch.setattr(ffmpeg_service, "get_version", lambda _path: "test")
    source = tmp_path / "fixture.mp4"
    source.write_bytes(b"fixture")
    project = tmp_path / "fixture.tapesift"
    project.write_bytes(b"project")
    dialogs = (
        (NewProjectDialogV3(settings), (
            max(840, min(960, qapp.primaryScreen().availableGeometry().width() - 32)),
            max(540, min(680, qapp.primaryScreen().availableGeometry().height() - 56)))),
        (PlayDetectDialog("ffmpeg", source, 60_000), (818, 395)),
        (SettingsDialogV3(settings), (
            max(760, min(1100, qapp.primaryScreen().availableGeometry().width() - 40)),
            max(520, min(730, qapp.primaryScreen().availableGeometry().height() - 60)))),
        (RecoveryDialogV3(project, "Fixture"), (662, 406)),
    )
    try:
        for dialog, expected in dialogs:
            dialog.show()
            qapp.processEvents()
            assert (dialog.width(), dialog.height()) == expected
    finally:
        for dialog, _expected in dialogs:
            _destroy(dialog, qapp)


@pytest.mark.parametrize(
    ("button_name", "expected"),
    [
        ("discard_button", RecoveryAction.DISCARD),
        ("read_only_button", RecoveryAction.READ_ONLY),
        ("restore_button", RecoveryAction.RESTORE),
    ],
)
def test_recovery_dialog_returns_one_explicit_action(
        qapp, tmp_path, button_name, expected):
    project = tmp_path / "EXAMPLE HOME O VS. AWAY D.tapesift"
    project.write_bytes(b"project placeholder")
    dialog = RecoveryDialogV3(project, "EXAMPLE HOME O VS. AWAY D")
    getattr(dialog, button_name).click()
    try:
        assert dialog.selected_action is expected
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        _destroy(dialog, qapp)


def test_recovery_dialog_cancel_preserves_the_no_action_result(
        qapp, tmp_path):
    project = tmp_path / "game.tapesift"
    project.write_bytes(b"project placeholder")
    dialog = RecoveryDialogV3(project, "Game")
    dialog.reject()
    try:
        assert dialog.selected_action is RecoveryAction.NONE
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        _destroy(dialog, qapp)


def test_main_window_routes_recovery_without_consuming_the_wrong_choice(
        qapp, settings, tmp_path, monkeypatch):
    original_check = MainWindowV3._check_recovery
    monkeypatch.setattr(MainWindowV3, "_check_recovery", lambda self: None)
    window = MainWindowV3(
        settings,
        workspace_state_path=tmp_path / "shell-v3.json",
    )
    monkeypatch.setattr(MainWindowV3, "_check_recovery", original_check)
    project = tmp_path / "game.tapesift"
    project.write_bytes(b"project placeholder")
    monkeypatch.setattr(
        recovery_service,
        "pending_recovery",
        lambda: (str(project), "Game"),
    )
    routed: list[str] = []
    monkeypatch.setattr(window, "_open_project", lambda _path: routed.append("restore"))
    monkeypatch.setattr(
        window,
        "_open_recovery_read_only",
        lambda _path: routed.append("read_only"),
    )
    monkeypatch.setattr(
        recovery_service, "mark_closed", lambda: routed.append("discard"))

    class DummyRecoveryDialog:
        DialogCode = QDialog.DialogCode
        action = RecoveryAction.NONE

        def __init__(self, *_args, **_kwargs):
            self.selected_action = self.action

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        main_window_module, "RecoveryDialogV3", DummyRecoveryDialog)
    window._v2_initializing = False
    try:
        for action, expected in (
            (RecoveryAction.RESTORE, "restore"),
            (RecoveryAction.READ_ONLY, "read_only"),
            (RecoveryAction.DISCARD, "discard"),
        ):
            routed.clear()
            DummyRecoveryDialog.action = action
            original_check(window)
            assert routed == [expected]
    finally:
        _destroy_window(window, qapp)


def test_read_only_recovery_locks_edits_but_keeps_jkl(
        qapp, settings, tmp_path, monkeypatch):
    monkeypatch.setattr(MainWindowV3, "_check_recovery", lambda self: None)
    window = MainWindowV3(
        settings,
        workspace_state_path=tmp_path / "shell-v3.json",
    )
    try:
        window.stack.setCurrentWidget(window.workspace)
        window._sync_transport_shortcut_page(window.stack.currentIndex())
        window._set_recovery_read_only_surface(True)
        assert not window.clip_editor.isEnabled()
        for key in ("J", "K", "L"):
            assert window._transport_shortcuts[key].isEnabled()
        for key in ("I", "O", "A", "Delete", "C", "M", "Ctrl+E"):
            assert not window._transport_shortcuts[key].isEnabled()
        assert all(not shortcut.isEnabled()
                   for shortcut in window._quick_tag_shortcuts)

        window._set_recovery_read_only_surface(False)
        assert window.clip_editor.isEnabled()
        for key in ("I", "O", "A"):
            assert window._transport_shortcuts[key].isEnabled()
    finally:
        _destroy_window(window, qapp)


def test_settings_children_cancel_and_failed_save_preserve_live_preferences(qapp, tmp_path, monkeypatch):
    from copy import deepcopy
    from PySide6.QtWidgets import QMessageBox
    from tapesift.ui.settings_dialog import FixedListsDialog, DetailFieldLayoutDialog
    target = tmp_path / "isolated-settings.json"
    original_save = AppSettings.save
    failing = []
    def persist(candidate):
        if failing:
            raise OSError("Injected write failure")
        original_save(candidate, target)
    monkeypatch.setattr(AppSettings, "save", persist)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    monkeypatch.setattr(ffmpeg_service, "detect_hw_encoders", lambda _path: [])
    monkeypatch.setattr(ffmpeg_service, "get_version", lambda _path: "test")
    live = AppSettings(volume=19, recent_projects=[])
    live.save()
    initial = deepcopy(live)
    bytes_before = target.read_bytes()
    dialog = SettingsDialogV3(live)
    child = FixedListsDialog(dialog.settings, dialog)
    child.values_edit.setPlainText("Manual QA tag")
    child._save()
    layout = DetailFieldLayoutDialog(dialog.settings, dialog)
    layout.label_edit.setText("QA Quarter")
    layout._save()
    assert target.read_bytes() == bytes_before and live == initial
    dialog.reject()
    assert live == initial
    _destroy(dialog, qapp)

    dialog = SettingsDialogV3(live)
    dialog.show()
    child = FixedListsDialog(dialog.settings, dialog)
    child.values_edit.setPlainText("Manual QA tag")
    child._save()
    dialog.volume_spin.setValue(42)
    live.recent_projects.append("Unrelated project added while open")
    before_failure = deepcopy(live)
    failing.append(True)
    dialog._save()
    assert live == before_failure and target.read_bytes() == bytes_before
    assert dialog.isVisible() and dialog.result() != QDialog.DialogCode.Accepted
    failing.clear()
    dialog._save()
    reopened = AppSettings.load(target)
    assert live.volume == reopened.volume == 42
    assert live.fixed_tags == reopened.fixed_tags == ["Manual QA tag"]
    assert live.recent_projects == reopened.recent_projects == ["Unrelated project added while open"]
    assert live.save.__self__ is live
    _destroy(dialog, qapp)


def test_result_projection_keeps_vocabulary_and_six_ordered_favorites(qapp, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox
    from tapesift.ui_v3.dialog_surface import ResultManagerDialogV3
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    dialog = ResultManagerDialogV3(["TD", "Sack"], ["Custom QA"])
    dialog.show()
    QTest.qWait(40)
    original = dialog.results()
    dialog.result_filter.setText("fumble")
    visible = [group.child(j).text(0) for i in range(dialog.result_tree.topLevelItemCount())
               for group in [dialog.result_tree.topLevelItem(i)] for j in range(group.childCount())
               if not group.child(j).isHidden()]
    assert set(visible) == {"Fumble", "Fumble Lost", "Fumble Recovered"}
    heading = dialog.result_tree.topLevelItem(0)
    assert not heading.flags() & Qt.ItemFlag.ItemIsSelectable
    assert heading.data(0, Qt.ItemDataRole.UserRole) is None
    dialog.result_filter.clear()
    for value in ("Interception", "Reception", "Completion", "Custom QA", "Penalty Accepted"):
        item = dialog.result_list.findItems(value, Qt.MatchFlag.MatchExactly)[0]
        dialog.result_list.setCurrentItem(item)
        dialog._add_selected_favorite()
    assert dialog.favorites() == ["TD", "Sack", "Interception", "Reception", "Completion", "Custom QA"]
    assert dialog.results() == original
    dialog.favorite_list.setCurrentRow(5)
    dialog._move_favorite(-1)
    QTest.qWait(40)
    assert dialog.favorites()[-2:] == ["Custom QA", "Completion"]
    assert dialog.result_list.isHidden()
    assert all(label.text() in dialog.favorites() for label in dialog.preview.findChildren(QLabel))
    _destroy(dialog, qapp)


def test_canonical_penalties_are_grouped_separately_from_custom_results(qapp):
    from tapesift.ui_v3.dialog_surface import ResultManagerDialogV3
    dialog = ResultManagerDialogV3([], ["False Start", "Offensive Holding", "Custom QA"])
    groups = {dialog.result_tree.topLevelItem(i).text(0): [
        dialog.result_tree.topLevelItem(i).child(j).text(0)
        for j in range(dialog.result_tree.topLevelItem(i).childCount())]
        for i in range(dialog.result_tree.topLevelItemCount())}
    assert "False Start" in groups["PENALTIES"]
    assert "Offensive Holding" in groups["PENALTIES"]
    assert "Fumble Recovered" in groups["POSSESSION"]
    assert "Extra Point Missed" in groups["SPECIAL TEAMS"]
    assert "Custom QA" in groups["CUSTOM"]
    _destroy(dialog, qapp)


def test_results_filter_clears_hidden_command_target(qapp, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    from tapesift.ui_v3.dialog_surface import ResultManagerDialogV3
    prompts = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: prompts.append("information"))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: prompts.append("rename"))
    dialog = ResultManagerDialogV3([], ["Custom QA"])
    original = dialog.results()
    for value in ("Touchdown", "Custom QA"):
        dialog.result_filter.clear()
        item = next(dialog.result_tree.topLevelItem(i).child(j)
                    for i in range(dialog.result_tree.topLevelItemCount())
                    for j in range(dialog.result_tree.topLevelItem(i).childCount())
                    if dialog.result_tree.topLevelItem(i).child(j).text(0) == value)
        dialog.result_tree.setCurrentItem(item)
        assert dialog.result_list.currentItem().text() == value
        dialog.result_filter.setText("Sack")
        assert dialog.result_tree.currentItem() is None
        assert dialog.result_list.currentItem() is None
        dialog._add_selected_favorite()
        dialog._rename_selected_result()
        dialog._remove_selected_result()
        assert dialog.favorites() == [] and dialog.results() == original and prompts == []
    _destroy(dialog, qapp)


@pytest.mark.parametrize("retry", [False, True])
def test_result_save_failure_keeps_preferences_and_logged_results(qapp, tmp_path, monkeypatch, retry):
    from copy import deepcopy
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    from tapesift.models.clip import Clip
    from tapesift.ui_core.clip_editor import ClipEditor
    from tapesift.ui_v3.dialog_surface import ResultManagerDialogV3
    target = tmp_path / "results.json"
    original_save = AppSettings.save
    writes = []
    def save(settings):
        writes.append(True)
        if len(writes) == 1:
            raise OSError("Injected results failure")
        original_save(settings, target)
    monkeypatch.setattr(AppSettings, "save", save)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    settings = AppSettings(result_favorites=["TD"])
    original_save(settings, target)
    before = deepcopy(settings)
    disk_before = target.read_bytes()
    editor = ClipEditor(settings)
    editor.set_result_choices([])
    editor.result_dialog_class = ResultManagerDialogV3
    clip = Clip(start_ms=1000, end_ms=2000)
    editor.set_clip(clip)
    editor.detail_edits["result"].setText("Historic custom; TD")
    calls = []
    def interact(dialog):
        calls.append(id(dialog))
        if len(calls) == 1:
            dialog.custom_edit.setText("QA new result")
            dialog._add_custom()
            dialog.result_list.setCurrentItem(dialog.result_list.findItems("QA new result", Qt.MatchFlag.MatchExactly)[0])
            dialog._add_selected_favorite()
            dialog.accept()
        else:
            assert settings == before and target.read_bytes() == disk_before
            assert editor.detail_edits["result"].text() == "Historic custom; TD"
            if retry:
                dialog.accept()
            else:
                dialog.reject()
        return dialog.result()
    monkeypatch.setattr(ResultManagerDialogV3, "exec", interact)
    editor._manage_results()
    assert len(calls) == 2 and calls[0] == calls[1]
    if retry:
        reopened = AppSettings.load(target)
        assert settings.result_favorites == reopened.result_favorites == ["TD", "QA new result"]
        assert "QA new result" in reopened.fixed_details["result"]
    else:
        assert settings == before and target.read_bytes() == disk_before
    assert editor.detail_edits["result"].text() == "Historic custom; TD"
    _destroy(editor, qapp)


@pytest.mark.parametrize("route", ["project", "tag"])
@pytest.mark.parametrize("retry", [False, True])
def test_project_dialog_save_failure_restores_live_fields_and_disk(qapp, tmp_path, monkeypatch, route, retry):
    from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
    from copy import deepcopy
    from types import SimpleNamespace
    from PySide6.QtWidgets import QWidget, QMessageBox
    from tapesift.services.project_service import ProjectSession
    from tapesift.ui_v3.dialog_surface import ProjectSettingsDialogV3, TagColorManagerDialogV3
    session = ProjectSession.create("Dialog failure QA", tmp_path, tmp_path / "exports")
    project = session.project
    before = deepcopy(project)
    names = ("opponent", "output_folder", "naming_template", "quarter_markers_ms", "tag_styles")
    refreshed = []
    host = QWidget()
    host.session = session
    host._require_session = lambda: True
    host._known_tags = lambda: ["Pass"]
    host.project_settings_dialog_class = ProjectSettingsDialogV3
    host._refresh_clip_list = lambda: refreshed.append("refresh")
    host._index_current_project = lambda: refreshed.append("index")
    host.export_panel = SimpleNamespace(set_output_folder=lambda value: refreshed.append("folder"))
    host.statusBar = lambda: SimpleNamespace(showMessage=lambda *args: refreshed.append("status"))
    monkeypatch.setattr(QMessageBox, "critical", lambda *args: None)
    real_save = session.save
    writes = []
    def save():
        writes.append(True)
        if len(writes) == 1:
            raise OSError("Injected project failure")
        real_save()
    session.save = save
    calls = []
    typ = ProjectSettingsDialogV3 if route == "project" else TagColorManagerDialogV3
    def interact(dialog):
        calls.append(id(dialog))
        if len(calls) == 1:
            if route == "project":
                dialog.opponent_edit.setText("Changed opponent")
                dialog.folder_edit.setText(str(tmp_path / "changed"))
                dialog.template_edit.setText("QA_{clip_number}")
                dialog.q2_edit.setText("01:00")
                dialog._tag_styles = {"pass": {"color": "#123456"}}
            else:
                dialog._set_color_button(dialog._rows["pass"]["color"], "#123456")
            dialog.accept()
        else:
            assert session.project is project and refreshed == []
            assert all(getattr(project, name) == getattr(before, name) for name in names)
            disk = ProjectSession.open_read_only(session.db_path)
            assert all(getattr(disk.project, name) == getattr(before, name) for name in names)
            disk.close()
            if retry:
                dialog.accept()
            else:
                dialog.reject()
        return dialog.result()
    monkeypatch.setattr(typ, "exec", interact)
    if route == "project":
        MainWindowWorkflow._open_project_settings(host)
    else:
        MainWindowV3._open_v3_tag_colors(host)
    assert len(calls) == 2 and calls[0] == calls[1] and session.project is project
    disk = ProjectSession.open_read_only(session.db_path)
    if retry:
        assert refreshed == (["folder", "refresh", "index", "status"] if route == "project" else ["refresh", "status"])
        assert disk.project.tag_styles["pass"]["color"] == project.tag_styles["pass"]["color"] == "#123456"
        if route == "project":
            assert disk.project.opponent == "Changed opponent" and disk.project.quarter_markers_ms == [60000]
    else:
        assert refreshed == []
        assert all(getattr(project, name) == getattr(disk.project, name) == getattr(before, name) for name in names)
    disk.close()
    session.conn.close()
    _destroy(host, qapp)
