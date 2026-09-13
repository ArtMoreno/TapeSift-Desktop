"""The modal backdrop dims the main window while dialogs are open."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMainWindow  # noqa: E402

from tapesift.ui_v2.backdrop import install_modal_backdrop  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def qtbot(qapp):
    """Register widgets so they are destroyed, not leaked between tests."""
    class _Bot:
        def __init__(self):
            self.widgets = []

        def addWidget(self, widget):  # noqa: N802 - matches pytest-qt
            self.widgets.append(widget)

    bot = _Bot()
    yield bot
    for widget in bot.widgets:
        widget.close()
        widget.deleteLater()
    qapp.processEvents()


def _drain(qapp, ms=300):
    """Let show/hide events and any fade animation finish."""
    import time
    end = time.monotonic() + ms / 1000.0
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.01)


def test_backdrop_shows_while_modal_dialog_open(qapp, qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    controller = install_modal_backdrop(window)

    dialog = QDialog(window)
    qtbot.addWidget(dialog)
    dialog.setModal(True)
    dialog.show()
    _drain(qapp)

    assert controller.tracked_dialog_count() == 1
    assert controller.backdrop.isVisible()
    assert controller.backdrop.scrim_opacity() == pytest.approx(1.0)
    assert controller.backdrop.geometry() == window.rect()

    dialog.hide()
    _drain(qapp)
    assert controller.tracked_dialog_count() == 0
    assert not controller.backdrop.isVisible()


def test_backdrop_ignores_non_modal_dialogs(qapp, qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    controller = install_modal_backdrop(window)

    dialog = QDialog(window)
    qtbot.addWidget(dialog)
    dialog.setModal(False)
    dialog.show()
    _drain(qapp)

    assert controller.tracked_dialog_count() == 0
    assert not controller.backdrop.isVisible()


def test_backdrop_ignores_dialogs_of_other_windows(qapp, qtbot):
    window = QMainWindow()
    other = QMainWindow()
    qtbot.addWidget(window)
    qtbot.addWidget(other)
    window.resize(900, 600)
    other.resize(900, 600)
    window.show()
    other.show()
    controller = install_modal_backdrop(window)

    dialog = QDialog(other)
    qtbot.addWidget(dialog)
    dialog.setModal(True)
    dialog.show()
    _drain(qapp)

    assert controller.tracked_dialog_count() == 0
    assert not controller.backdrop.isVisible()


def test_nested_dialogs_keep_the_backdrop_until_all_close(qapp, qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    controller = install_modal_backdrop(window)

    outer = QDialog(window)
    qtbot.addWidget(outer)
    outer.setModal(True)
    outer.show()
    inner = QDialog(outer)
    qtbot.addWidget(inner)
    inner.setModal(True)
    inner.show()
    _drain(qapp)
    assert controller.tracked_dialog_count() == 2

    inner.hide()
    _drain(qapp)
    assert controller.backdrop.isVisible()

    outer.hide()
    _drain(qapp)
    assert controller.tracked_dialog_count() == 0
    assert not controller.backdrop.isVisible()


def test_backdrop_follows_window_resize(qapp, qtbot):
    window = QMainWindow()
    qtbot.addWidget(window)
    window.resize(900, 600)
    window.show()
    controller = install_modal_backdrop(window)

    dialog = QDialog(window)
    qtbot.addWidget(dialog)
    dialog.setModal(True)
    dialog.show()
    _drain(qapp)

    window.resize(1200, 800)
    _drain(qapp, ms=50)
    assert controller.backdrop.geometry() == window.rect()
