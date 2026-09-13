from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QLabel

from tapesift.core.config import AppSettings
from tapesift.ui_v3.main_window import MainWindowV3


def _window(tmp_path) -> MainWindowV3:
    app = QApplication.instance() or QApplication([])
    window = MainWindowV3(
        AppSettings(), workspace_state_path=tmp_path / "shell-v3.json")
    window._screen_fit_done = True
    window.resize(1708, 921)
    window.stack.setCurrentWidget(window.workspace)
    window.show()
    app.processEvents()
    return window


def test_v3_uses_the_native_opaque_top_level_surface(tmp_path):
    window = _window(tmp_path)
    assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert not window.testAttribute(
        Qt.WidgetAttribute.WA_TranslucentBackground)
    assert window._backdrop_controller is None
    assert window._centered_menu_shell.window_controls.isHidden()
    assert window._centered_menu_shell.property("pageMode") == "review"
    assert window._centered_menu_shell.height() == 40


def test_v3_does_not_render_the_legacy_runtime_metrics_strip(tmp_path):
    window = _window(tmp_path)
    application_bar = window._centered_menu_shell
    assert application_bar.findChild(QLabel, "V3RuntimeMetrics") is None
    assert all(
        "FPS" not in label.text()
        and "GPU" not in label.text()
        and "CPU" not in label.text()
        and "LAT" not in label.text()
        for label in application_bar.findChildren(QLabel)
    )


def test_v3_bezel_frames_without_obscuring_control_geometry(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    review = window._v3_review
    before = (
        review.ledger_rail.width(), review.center_stack.width(),
        review.details_rail.width(),
    )
    bezel = window._v3_window_bezel
    margins = window.contentsMargins()
    assert (
        margins.left(), margins.top(),
        margins.right(), margins.bottom(),
    ) == (0, 0, 0, 0)
    assert bezel.geometry() == window.rect()
    assert bezel.band.geometry() == bezel.rect().adjusted(1, 1, -1, -1)
    assert bezel.inner.geometry() == bezel.rect().adjusted(5, 5, -5, -5)
    assert bezel.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert bezel.band.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert bezel.inner.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert before == (72, 1564, 72)

    window.resize(1600, 840)
    app.processEvents()
    assert bezel.geometry() == window.rect()
    assert bezel.band.geometry() == bezel.rect().adjusted(1, 1, -1, -1)
    assert bezel.inner.geometry() == bezel.rect().adjusted(5, 5, -5, -5)
    assert review.ledger_rail.width() == 72
    assert review.details_rail.width() == 72

    window._set_v3_bezel_maximized(True)
    assert bezel.property("bezelState") == "maximized"
    assert bezel.band.property("bezelState") == "maximized"
    assert bezel.inner.property("bezelState") == "maximized"

    window._set_v3_bezel_maximized(False)
    assert bezel.property("bezelState") == "restored"


def test_v3_retires_temporary_docks_after_rehosting(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = _window(tmp_path)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    from PySide6.QtWidgets import QDockWidget
    assert window.findChildren(QDockWidget) == []
    assert window.live_v3_objects_valid()


def test_native_application_bar_keeps_menu_objects_alive_through_traversal():
    import shiboken6
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import QMainWindow, QMenu
    from tapesift.ui_v3.application_bar import NativeApplicationBar

    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    menu_bar = QMainWindow.menuBar(window)
    titles = ("&File", "&Edit", "&Playback", "How TapeSift &Works", "&Help")
    menus = [menu_bar.addMenu(title) for title in titles]
    pointers = [shiboken6.getCppPointer(menu)[0] for menu in menus]
    dispatched = []
    command = QAction("QA command", window)
    command.triggered.connect(lambda: dispatched.append("File"))
    menus[0].addAction(command)

    def assert_original_menus():
        # Do not retain menuAction wrappers: PYSIDE-3380 incorrectly gave the
        # temporary action returned by actions() ownership of its menu.
        assert all(shiboken6.isValid(menu) for menu in menus)
        assert [shiboken6.getCppPointer(menu)[0] for menu in menus] == pointers
        assert all("&" not in menu.title() for menu in menus)

    try:
        bar = NativeApplicationBar(menu_bar, window)
        window.setMenuWidget(bar)
        assert_original_menus()
        late_menu = QMenu("&Window", window)
        menu_bar.addMenu(late_menu)
        menus.append(late_menu)
        pointers.append(shiboken6.getCppPointer(late_menu)[0])
        window.resize(1260, 640)
        window.show()
        app.processEvents()
        assert_original_menus()
        for mode in ("home", "library", "review", "export", "home"):
            bar.set_mode(mode)
            app.processEvents()
            assert_original_menus()
        window.resize(1040, 620)
        app.processEvents()
        assert_original_menus()
        command.trigger()
        assert dispatched == ["File"]
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()

