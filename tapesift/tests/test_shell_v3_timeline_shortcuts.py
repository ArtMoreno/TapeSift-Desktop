"""Real V3 key dispatch, focus safety, and the shared searchable guide."""
from unittest.mock import Mock

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.tests.test_shell_v3_review import _window, _close_test_window
from tapesift.ui.shortcuts_dialog import ShortcutsDialog, ShortcutRow


def test_timeline_keys_preserve_playback_and_respect_focus_and_guide(tmp_path, monkeypatch):
    window = _window(tmp_path)
    app = QApplication.instance()
    session = ProjectSession.create("Zoom keys", tmp_path / "projects", tmp_path / "exports")
    clip = Clip(20000, 30000, details={"quarter": "Q1", "run_pass": "Pass"})
    session.add_clip(clip)
    session.save()
    window.session = session
    window._refresh_clip_list()
    window.select_clip(clip.id)
    player = window.player
    player.slider.setRange(0, 120000)
    player.reset_timeline_zoom()
    monkeypatch.setattr(player, "position_ms", lambda: 25000)
    transport = [Mock() for _ in range(3)]
    for name, spy in zip(("setPosition", "play", "pause"), transport):
        monkeypatch.setattr(player.player, name, spy)
    app.setActiveWindow(window)
    window._return_focus_to_playback()
    app.processEvents()

    def span():
        a, b = player.slider.visible_range()
        return b - a

    def key(code, modifiers=Qt.KeyboardModifier.ShiftModifier):
        QTest.keyClick(app.focusWidget() or player.video_widget, code, modifiers)
        app.processEvents()

    assert span() == 120000
    key(Qt.Key.Key_Up)
    assert span() == 60000
    key(Qt.Key.Key_Down)
    assert span() == 120000
    key(Qt.Key.Key_F)
    start, end = player.slider.visible_range()
    assert start < clip.start_ms < clip.end_ms < end and span() < 120000
    key(Qt.Key.Key_0, Qt.KeyboardModifier.ControlModifier)
    assert span() == 120000
    assert all(not spy.called for spy in transport)
    assert (clip.start_ms, clip.end_ms) == (20000, 30000)

    window._focus_map_row("quarter")
    app.processEvents()
    grid = player.attribute_grid
    assert grid.hasFocus()
    cursor = grid.cursor_cell()
    key(Qt.Key.Key_Up)
    assert span() == 60000 and grid.cursor_cell() == cursor
    app.sendEvent(grid, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Up,
        Qt.KeyboardModifier.ShiftModifier, "", True, 1))
    app.processEvents()
    assert span() == 30000  # The OS repeat event reaches the zoom shortcut.
    for _ in range(10):
        key(Qt.Key.Key_Up)
    minimum = span()
    key(Qt.Key.Key_Up)
    assert span() == minimum > 0
    for _ in range(10):
        key(Qt.Key.Key_Down)
    assert span() == 120000 and grid.cursor_cell() == cursor

    field = window.clip_editor.detail_edits["quarter"].lineEdit()
    assert isinstance(field, QLineEdit)
    field.setFocus()
    app.processEvents()
    field.setText("")
    key(Qt.Key.Key_F)
    assert field.text().lower() == "f" and span() == 120000
    key(Qt.Key.Key_Up)
    key(Qt.Key.Key_Down)
    assert span() == 120000
    field.setText("Q1")
    window._focus_map_row("quarter")
    app.processEvents()
    key(Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)
    popup = window._v3_map_editor
    assert popup.isVisible()
    for code in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_F):
        key(code)
    assert span() == 120000
    key(Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)
    window._return_focus_to_playback()

    guides = []
    def inspect_guide():
        dialog = app.activeModalWidget()
        try:
            if isinstance(dialog, ShortcutsDialog):
                dialog.search.setText("Fit Play")
                app.processEvents()
                guides.append([r.item.keys for r in dialog.findChildren(ShortcutRow) if not r.isHidden()])
                dialog.search.setText("timeline")
                app.processEvents()
                dialog.grab().save(str(tmp_path / "timeline-guide.png"))
        finally:
            if dialog is not None:
                dialog.reject()
    for code in (Qt.Key.Key_Question, Qt.Key.Key_F1):
        # Each invocation starts in playback after the previous native modal
        # has closed; a hidden dialog must not receive the next test key.
        app.setActiveWindow(window)
        window._return_focus_to_playback()
        app.processEvents()
        QTimer.singleShot(100, inspect_guide)
        key(code, Qt.KeyboardModifier.NoModifier)
    assert guides == [["Shift + F"], ["Shift + F"]]
    assert "Shift+Up" in player.timeline_zoom_in.toolTip()
    assert "Shift+Down" in player.timeline_zoom_out.toolTip()
    assert "Shift+F" in player.timeline_fit_play.toolTip()

    window.stack.setCurrentWidget(window.library_screen)
    app.processEvents()
    assert all(not window._transport_shortcuts[k].isEnabled()
        for k in ("Shift+Up", "Shift+Down", "Shift+F"))
    assert span() == 120000
    session.save()
