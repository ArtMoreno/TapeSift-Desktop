"""Suite-wide safety fixtures."""

from __future__ import annotations

import os
import sys

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "keep_widgets: keep top-level widgets alive between tests, for a "
        "module-scoped fixture that deliberately reuses one window")


@pytest.fixture(scope="session", autouse=True)
def _isolate_tapesift_appdata(tmp_path_factory):
    """Never let a UI regression test overwrite the user's live settings.

    Some Qt workspace saves are timer-driven and can run after a narrower
    fixture has restored ``APPDATA``. Keeping the redirect active for the
    entire pytest process makes those late callbacks safe as well.
    """
    previous = os.environ.get("APPDATA")
    isolated = tmp_path_factory.mktemp("tapesift-session-appdata")
    os.environ["APPDATA"] = str(isolated)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = previous


@pytest.fixture(autouse=True)
def _isolate_recovery_marker(tmp_path, monkeypatch):
    # A test that leaves a project open must not trigger a modal recovery
    # dialog in the next test. Keep real recovery behavior within each test.
    from tapesift.services import recovery_service

    monkeypatch.setattr(
        recovery_service, "_marker_path", lambda: tmp_path / "open_project.json")


@pytest.fixture(autouse=True)
def _destroy_leaked_widgets(request, _isolate_recovery_marker):
    """Actually destroy top-level widgets left behind by a test.

    ``deleteLater()`` only queues a DeferredDelete event, and
    ``processEvents()`` does not deliver that event - so the teardown
    written in most UI test files ("deleteLater + processEvents") freed
    nothing whatsoever. Measured: three MainWindowV2 built and torn down
    that way left 3 of 3 C++ objects alive; one ``sendPostedEvents`` call
    freed all three.

    What that costs is a file's worth of full Qt windows, each holding a
    QMediaPlayer on the Windows Media Foundation backend, staying live
    until interpreter exit and then being destroyed in arbitrary order
    after QApplication is gone. Two files were crashing with 0xC0000005
    at roughly one run in five.

    This lives in conftest rather than in each file because remembering to
    call it is exactly what failed: the correct version existed in
    test_ui_v2.py, was documented there, and never reached the twelve
    other files that copied the broken half.

    Mark a module ``keep_widgets`` to opt out - test_windows_frame.py does,
    because it shares one native-framed window across the module.
    """
    yield
    if request.node.get_closest_marker("keep_widgets"):
        return
    # Only if Qt widgets were ever imported: no import, no widgets, and a
    # pure-logic test file should not pay to load Qt on the way out.
    qtwidgets = sys.modules.get("PySide6.QtWidgets")
    if qtwidgets is None:
        return
    app = qtwidgets.QApplication.instance()
    if app is None:
        return
    from PySide6.QtCore import QEvent

    for widget in app.topLevelWidgets():
        # A floating dock is top-level to the window system but still owned
        # by its QMainWindow. Queueing both can double-destroy the native
        # dock window on Windows, so only free what nothing else owns.
        if widget.parent() is None:
            widget.deleteLater()
    app.processEvents()
    app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
