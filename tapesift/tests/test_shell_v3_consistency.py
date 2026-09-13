"""Behavior regressions from the V3 settings/menu consistency audit."""
from copy import deepcopy
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from tapesift.core import paths
from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.ui_v3.main_window import MainWindowV3
from tapesift.ui_v3.dialog_surface import SettingsDialogV3
from tapesift.ui_v3.result_picker import ResultShortcutsDialog


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(paths, 'app_data_dir', lambda: tmp_path)
    monkeypatch.setattr(MainWindowV3, '_check_recovery', lambda self: None)
    monkeypatch.setattr(MainWindowV3, '_finish_deferred_session_open', lambda *args: None)
    settings = AppSettings(onboarding_seen=True)
    settings.default_project_folder = str(tmp_path / 'projects')
    settings.default_output_folder = str(tmp_path / 'exports')
    settings.save = lambda *args, **kwargs: None
    w = MainWindowV3(settings, workspace_state_path=tmp_path / 'workspace.json')
    w._screen_fit_done = True
    w.resize(1708, 921)
    w.show()
    app.processEvents()
    yield w
    w._app_closing = True
    w.autosave_timer.stop()
    w.player.unload()
    w.hide()
    w.deleteLater()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def test_export_defaults_seed_new_projects_but_never_overwrite_saved_choices(window, tmp_path, monkeypatch):
    window.settings.naming_template = 'INITIAL_{clip_name}'
    window.settings.output_organization = 'by_tag'
    window._new_project('Fresh', str(tmp_path / 'projects'), str(tmp_path / 'exports'))
    session = window.session
    window.autosave_timer.stop()
    assert session.project.naming_template == 'INITIAL_{clip_name}'
    assert session.project.output_organization == 'by_tag'
    session.project.naming_template = 'PROJECT_{clip_name}'
    session.project.output_organization = 'flat'
    session.project.default_preset = 'fast_copy'
    session.project.accurate_cut = False
    session.save()
    db = Path(session.db_path)
    window._close_project()
    window.settings.naming_template = 'CHANGED_{clip_name}'
    window._open_project(str(db))
    window.autosave_timer.stop()
    before = deepcopy(window.session.project)
    assert before.naming_template == 'PROJECT_{clip_name}'
    def save_preferences(dialog):
        dialog.template_edit.setText('NEXT_PROJECT_{clip_name}')
        dialog._save()
        return dialog.result()
    monkeypatch.setattr(SettingsDialogV3, 'exec', save_preferences)
    window._open_settings()
    assert window.settings.naming_template == 'NEXT_PROJECT_{clip_name}'
    assert window.session.project == before
    with_session = ProjectSession.open_read_only(db)
    try:
        assert with_session.project.naming_template == 'PROJECT_{clip_name}'
        assert with_session.project.output_organization == 'flat'
        assert with_session.project.default_preset == 'fast_copy'
        assert with_session.project.accurate_cut is False
    finally:
        with_session.close()


def test_details_aliases_toggle_one_action_and_home_disables_project_commands(window, tmp_path):
    app = QApplication.instance()
    actions = window._v3_menu_actions
    assert not actions['Save Project'].isEnabled()
    assert not actions['Project Settings'].isEnabled()
    file_menu = next(a.menu() for a in window.menuBar().actions() if window._action_text(a) == 'File')
    titles = [window._action_text(a) for a in file_menu.actions() if not a.isSeparator()]
    assert titles[:2] == ['New Project', 'Open Existing'] and titles[-1] == 'Exit'
    assert not any('Autodetect' in title for title in titles)
    session = ProjectSession.create('Keys', tmp_path / 'projects', tmp_path / 'exports')
    window._activate_session(session)
    window.autosave_timer.stop()
    # Let the native page/window activation finish before sending shortcuts.
    QTest.qWait(100)
    window._sync_v3_menu_availability()
    assert actions['Save Project'].isEnabled()
    assert window.details_action is window.details_rail_action
    assert {k.toString() for k in window.details_action.shortcuts()} == {'Ctrl+B', 'Ctrl+I'}
    assert not any(s.isEnabled() and s.key().toString() == 'Ctrl+B' for s in window.findChildren(QShortcut))
    app.setActiveWindow(window)
    window._set_details_open(True)
    for key in [Qt.Key.Key_B, Qt.Key.Key_I, Qt.Key.Key_B, Qt.Key.Key_I]:
        app.setActiveWindow(window)
        window._return_focus_to_playback()
        app.processEvents()
        before = window._v3_review.details_rail.is_open()
        QTest.keyClick(app.focusWidget() or window.player.video_widget, key, Qt.KeyboardModifier.ControlModifier)
        app.processEvents()
        assert window._v3_review.details_rail.is_open() is not before
    window.stack.setCurrentWidget(window.start_screen)
    assert not window.details_action.isEnabled()


def test_manage_results_uses_visible_sixteen_button_preferences_and_preserves_data(window, monkeypatch):
    editor = window.clip_editor
    clip = Clip(1000, 2000, details={'result': 'Historic custom; TD'})
    editor.set_clip(clip)
    legacy = list(window.settings.result_favorites)
    calls = []
    def interact(dialog):
        calls.append(dialog)
        assert len(dialog.favorites()) == 16
        dialog._favorites = ['Historic custom', 'TD']
        dialog.buttons.accepted.emit()
        return dialog.result()
    monkeypatch.setattr(ResultShortcutsDialog, 'exec', interact)
    editor._manage_results()
    assert len(calls) == 1
    assert list(editor.quick_result_buttons) == ['Historic custom', 'TD']
    assert window.settings.v3_result_favorites == ['Historic custom', 'TD']
    assert window.settings.result_favorites == legacy
    assert 'Historic custom' in window.settings.fixed_details['result']
    assert clip.details['result'] == 'Historic custom; TD'
    previous = deepcopy(window.settings)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: None)
    def fail():
        raise OSError('Injected settings write failure')
    window.settings.save = fail
    assert not editor._save_result_preferences(['Loss'], ['Another custom'], editor)
    assert window.settings.v3_result_favorites == previous.v3_result_favorites
    assert window.settings.fixed_details == previous.fixed_details
    assert list(editor.quick_result_buttons) == ['Historic custom', 'TD']
