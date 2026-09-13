from __future__ import annotations

import gc
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QSize, QTimer, QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.services import recovery_service, snap_prediction_service
from tapesift.services.project_service import ProjectSession
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow
from tapesift.ui_core.video_player import VideoPlayer
from tapesift.ui_v2.dock_v2 import DockV2Deck, MachinedTransportSurface
from tapesift.ui_v3.main_window import MainWindowV3


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def clean_qt_roots(qapp):
    yield
    roots = [widget for widget in qapp.topLevelWidgets()
             if widget.parent() is None]
    for widget in roots:
        for timer in widget.findChildren(QTimer):
            timer.stop()
        videos = ([widget] if isinstance(widget, VideoPlayer) else []) \
            + widget.findChildren(VideoPlayer)
        for video in videos:
            video.stop()
            video.player.setSource(QUrl())
            video.player.setVideoOutput(None)
            video.player.setAudioOutput(None)
        for media in widget.findChildren(QMediaPlayer):
            media.stop()
            media.setSource(QUrl())
            media.setVideoOutput(None)
            media.setAudioOutput(None)
    qapp.processEvents()
    for widget in [item for item in qapp.topLevelWidgets()
                   if item.parent() is None]:
        widget.close()
        widget.deleteLater()
    gc.collect()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_locked_transport_uses_native_scale_assets_and_parks_add_clip(qapp):
    deck = DockV2Deck()
    surface = deck.transport_island

    assert isinstance(surface, MachinedTransportSurface)
    assert surface._play_art.width() == 448
    assert surface._play_art.height() == 88
    assert surface._play_art.devicePixelRatio() == 2.0
    assert surface._pause_art.width() == 448
    assert surface._pause_art.height() == 88
    assert surface._pause_art.devicePixelRatio() == 2.0
    assert deck.add_clip_btn.parentWidget() is deck.overflow_bay
    assert deck.add_clip_btn.isHidden()
    assert deck.inout_readout.parentWidget() is deck.overflow_bay
    assert not deck.inout_readout.isVisible()
    assert deck.rate_group.parentWidget() is deck.overflow_bay
    assert deck.rate_group.isHidden()


def test_v3_open_selects_one_play_and_restores_original_snap_route(
        qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setattr(MainWindowV3, "_check_recovery", lambda self: None)
    recovery_service.mark_closed()

    source = tmp_path / "source.mp4"
    source.write_bytes(b"test source placeholder")
    session = ProjectSession.create("Snap Route", tmp_path, tmp_path / "out")
    session.project.source_video_path = str(source)
    session.project.source_duration_ms = 30_000
    clip = Clip(start_ms=1_000, end_ms=6_000, clip_title="Play 001")
    session.add_clip(clip)
    session.save()

    settings = AppSettings(onboarding_seen=True, recent_projects=[])
    settings.save = lambda *args, **kwargs: None
    window = MainWindowV3(
        settings, workspace_state_path=tmp_path / "shell-v3.json")
    monkeypatch.setattr(window, "_load_preview_source", lambda *args: None)
    monkeypatch.setattr(window, "_show_metadata", lambda: None)

    assert window._activate_session(session)
    qapp.processEvents()
    assert window.clip_list.selected_clip_ids() == [clip.id]
    assert window._selected_clip_id == clip.id
    assert window.player.predicted_snap_button.text() == "Find Snap"
    assert window.player.predicted_snap_button.isEnabled()
    assert window.player.predicted_snap_button.size() == QSize(70, 24)
    assert "TransportExportZone QPushButton#PredictedSnapAction" in \
        window.control_center.styleSheet()
    assert window._v3_timeline_zoom_cluster.size() == QSize(68, 24)
    assert window.player.timeline_zoom_out.property("iconAsset") == \
        "subtract-24.svg"
    assert window.player.timeline_zoom_in.property("iconAsset") == \
        "add-24.svg"
    assert window.player.timeline_fit_play.property("iconAsset") == \
        "fit-play-20.svg"
    assert window.player.predicted_snap_button.property("iconAsset") == \
        "find-snap-football-24.svg"
    assert window.control_center.jog_toggle.isHidden()
    tools = window.control_center.overflow_button
    assert tools.text() == "Tools ▾" and not tools.icon().isNull()
    assert any(action.text() == "Show jog wheel" for action in tools.menu().actions())
    assert window.control_center._rate_divider.isHidden()
    assert window.player.predicted_snap_button.parentWidget() is tools.parentWidget()
    assert tools.accessibleName() == "Playback and Tag Map tools"
    assert "QWidget#TransportPill" in window.control_center.styleSheet()
    assert "background: #080c0a" in window.control_center.styleSheet()
    assert "border-bottom: 1px solid #151d17" in window.control_center.styleSheet()
    assert window.player._region_rule_top.styleSheet() == \
        "background:#030503;border:none;"
    assert window.player._region_rule_bottom.styleSheet() == \
        "background:#030503;border:none;"

    prediction = {
        "predictor_id": snap_prediction_service.PREDICTOR_ID,
        "predictor_version": snap_prediction_service.PREDICTOR_VERSION,
        "source_ms": 2_250,
        "confidence": 0.8,
        "eligible": True,
        "clip_start_ms": clip.start_ms,
        "clip_end_ms": clip.end_ms,
    }
    session.cache_snap_prediction(clip.id, prediction)
    window._sync_predicted_snap_action()
    assert window.player.predicted_snap_button.text() == "Go to Snap"

    seeks: list[int] = []
    monkeypatch.setattr(window.player, "seek_to", seeks.append)
    window.player.predicted_snap_button.click()
    window._transport_shortcuts["G"].activated.emit()
    assert seeks == [2_250, 2_250]

    assert window._close_project()
    window.hide()
