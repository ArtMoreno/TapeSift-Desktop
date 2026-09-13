"""Moving a widget must invalidate the old, native-owned layout item."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
import shiboken6
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenuBar, QVBoxLayout, QWidget,
)

from tapesift.ui_core.layout_ownership import reparent_widget
from tapesift.ui_v2.components import CenteredApplicationMenu


@pytest.mark.parametrize("nested", [False, True])
def test_reparent_invalidates_old_layout_item_without_deleting_widget(nested):
    app = QApplication.instance() or QApplication([])
    source, destination = QWidget(), QWidget()
    root = QVBoxLayout(source)
    layout = QHBoxLayout() if nested else root
    if nested:
        root.addLayout(layout)
    label = QLabel("Keep this control")
    layout.addWidget(label)
    item = layout.itemAt(0)

    reparent_widget(label, destination)

    assert not shiboken6.isValid(item)
    assert layout.count() == 0
    assert shiboken6.isValid(label)
    assert label.text() == "Keep this control"
    assert label.parentWidget() is destination
    assert app is not None


@pytest.mark.parametrize("side", ["left", "right"])
def test_masthead_move_invalidates_source_layout_item(side):
    app = QApplication.instance() or QApplication([])
    source = QWidget()
    layout = QVBoxLayout(source)
    label = QLabel("Page identity")
    layout.addWidget(label)
    item = layout.itemAt(0)
    menu = CenteredApplicationMenu(QMenuBar())

    getattr(menu, f"add_mode_{side}_widget")("library", label)

    assert not shiboken6.isValid(item)
    assert layout.count() == 0
    assert label.text() == "Page identity"
    assert menu.isAncestorOf(label)
    assert app is not None
