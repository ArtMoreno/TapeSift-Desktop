from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsOpacityEffect, QToolButton,
)

from tapesift.ui_v2.dock_v2 import DockV2Deck, MachinedTransportSurface


def test_selected_machined_transport_geometry_and_order():
    app = QApplication.instance() or QApplication([])
    deck = DockV2Deck()
    deck.resize(1015, deck.DECK_HEIGHT)
    zoom_out = QToolButton(deck)
    zoom_in = QToolButton(deck)
    fit_play = QToolButton(deck)
    at_snap = QToolButton(deck)
    at_snap.setText("At Snap")
    deck.mount_viewport_controls(zoom_out, zoom_in, fit_play, at_snap)
    deck.show()
    deck.layout().activate()
    app.processEvents()

    surface = deck.transport_island
    assert isinstance(surface, MachinedTransportSurface)
    assert surface.size() == QSize(224, 44)
    assert deck.height() == 46

    controls = (
        deck.step_back_btn,
        deck.rewind_btn,
        deck.play_btn,
        deck.fast_forward_btn,
        deck.step_fwd_btn,
    )
    expected = (
        QRect(3, 5, 42, 34),
        QRect(45, 5, 47, 34),
        QRect(90, 0, 44, 44),
        QRect(132, 5, 47, 34),
        QRect(179, 5, 42, 34),
    )
    assert tuple(control.geometry() for control in controls) == expected
    assert [control.geometry().center().x() for control in controls] == [
        23, 68, 111, 155, 199,
    ]
    assert all(control.parentWidget() is surface for control in controls)
    assert all(control.isVisible() for control in controls)
    assert all(isinstance(control.graphicsEffect(), QGraphicsOpacityEffect)
               for control in controls)

    surface_center = surface.mapTo(deck, QPoint(112, 22))
    play_center = deck.play_btn.mapTo(
        deck, deck.play_btn.rect().center())
    assert abs(surface_center.x() - play_center.x()) <= 1
    assert abs(surface_center.y() - play_center.y()) <= 1


def test_selected_machined_transport_assets_and_playing_state():
    app = QApplication.instance() or QApplication([])
    deck = DockV2Deck()
    surface = deck.transport_island

    assert Path(surface.PLAY_ASSET).is_file()
    assert Path(surface.PAUSE_ASSET).is_file()
    assert not surface._play_art.isNull()
    assert not surface._pause_art.isNull()

    deck.set_playing(False)
    app.processEvents()
    assert surface.property("playing") in (None, "false")
    assert deck.play_btn.accessibleName() == "Play playback (Space)"

    deck.set_playing(True)
    app.processEvents()
    assert surface.property("playing") == "true"
    assert deck.play_btn.accessibleName() == "Pause playback (Space)"
    assert deck.play_btn.size() == QSize(44, 44)


def test_selected_machined_transport_keeps_neighboring_surfaces():
    app = QApplication.instance() or QApplication([])
    deck = DockV2Deck()
    deck.resize(1015, deck.DECK_HEIGHT)
    zoom_out = QToolButton(deck)
    zoom_in = QToolButton(deck)
    fit_play = QToolButton(deck)
    at_snap = QToolButton(deck)
    at_snap.setText("At Snap")
    deck.mount_viewport_controls(zoom_out, zoom_in, fit_play, at_snap)
    deck.show()
    deck.layout().activate()
    app.processEvents()

    assert deck.transport_island.width() == 224
    assert deck.jog_toggle.isVisible()
    assert deck.jog_toggle.text() == "Jog Wheel"
    transport_right = deck.transport_zone.mapTo(
        deck, deck.transport_zone.rect().topRight()).x()
    viewport_left = deck.viewport_group.mapTo(
        deck, deck.viewport_group.rect().topLeft()).x()
    assert deck.viewport_group.isVisible()
    assert at_snap.isVisible()
    assert at_snap.text() == "At Snap"
    assert deck.viewport_group.width() == 212
    assert zoom_out.size() == QSize(28, 30)
    assert zoom_in.size() == QSize(28, 30)
    assert fit_play.size() == QSize(58, 30)
    assert at_snap.size() == QSize(84, 30)
    assert deck.jog_toggle.size() == QSize(88, 30)
    assert deck.overflow_button.text() == ""
    assert not deck.overflow_button.icon().isNull()
    assert deck.overflow_button.iconSize() == QSize(14, 14)
    assert "DockV2Overflow::menu-indicator" in deck.styleSheet()
    assert deck.marks_group.size() == QSize(97, 34)
    assert transport_right < viewport_left


def test_inline_zoom_cluster_stays_beside_jog_then_yields_when_compact():
    app = QApplication.instance() or QApplication([])
    deck = DockV2Deck()
    deck.resize(1147, deck.DECK_HEIGHT)
    utility_layout = deck.deck_export_zone.layout()

    inline_zoom = QFrame(deck.deck_export_zone)
    inline_zoom.setFixedSize(172, deck.KEY_SIZE)
    snap = QToolButton(deck.deck_export_zone)
    snap.setFixedSize(84, deck.KEY_SIZE)
    utility_layout.insertWidget(0, inline_zoom)
    utility_layout.insertWidget(1, snap)
    deck.inline_viewport_group = inline_zoom

    deck.show()
    deck._update_responsive_state(1147)
    deck.layout().activate()
    app.processEvents()

    assert inline_zoom.isVisible()
    assert utility_layout.indexOf(inline_zoom) == 0
    assert utility_layout.indexOf(snap) == 1
    assert utility_layout.indexOf(deck.jog_toggle) == 2
    assert utility_layout.indexOf(deck.overflow_button) == 3
    play_center = deck.play_btn.mapTo(deck, deck.play_btn.rect().center()).x()
    assert abs(play_center - deck.rect().center().x()) <= 1

    deck.resize(750, deck.DECK_HEIGHT)
    deck._update_responsive_state(750)
    deck.layout().activate()
    app.processEvents()

    assert inline_zoom.isHidden()
    assert snap.isVisible()
    assert deck.jog_toggle.isVisible()
    assert deck.overflow_button.isVisible()
