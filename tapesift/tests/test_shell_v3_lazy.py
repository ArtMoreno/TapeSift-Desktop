from __future__ import annotations

from PySide6.QtWidgets import QApplication

from tapesift.core.config import AppSettings
from tapesift.services import library_service
from tapesift.services.project_service import ProjectSession
from tapesift.ui_v3.main_window import MainWindowV3


def test_v3_populates_stable_review_and_library_pages_once(
        tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    for name, value in (
            ("stats", (0, 0)), ("projects", []), ("opponents", []),
            ("players", []), ("all_tags", []), ("search", [])):
        monkeypatch.setattr(
            library_service, name,
            (lambda *args, result=value, **kwargs: result))

    window = MainWindowV3(
        AppSettings(), workspace_state_path=tmp_path / "shell-v3.json")
    workspace = window.workspace
    library = window.library_screen
    authorities = (
        window.player, window.clip_list, window.clip_editor,
        window.export_panel,
    )
    assert window._v3_review is None
    assert library._screen is None

    session = ProjectSession.create(
        "LAZY", tmp_path / "projects", tmp_path / "exports")
    try:
        assert window._activate_session(session)
        review = window._v3_review
        assert review is not None
        assert window.stack.currentWidget() is workspace
        assert authorities == (
            window.player, window.clip_list, window.clip_editor,
            window.export_panel,
        )
        window._set_workspace_stage("export")
        window._set_workspace_stage("review")
        window._ensure_v3_review()
        assert window._v3_review is review

        window.show_library()
        screen = library._screen
        assert screen is not None
        assert window.library_screen is library
        assert window.stack.currentWidget() is library
        window.show_library()
        assert library._screen is screen
    finally:
        session.close()
        window._app_closing = True
        window.library_screen._stop_inline_preview()
        window.player.unload()
        window.hide()
        app.processEvents()
