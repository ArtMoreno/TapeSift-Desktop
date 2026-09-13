from __future__ import annotations

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from tapesift.core.config import AppSettings
from tapesift.services.project_service import ProjectSession
from tapesift.ui_v3.empty_review import ZeroPlayOverlayV3
from tapesift.ui_v3.fonts import MONO_FAMILY, SANS_FAMILY
from tapesift.ui_v3.main_window import MainWindowV3
from tapesift.ui_v3.workspace_state import ReviewRailState


def _window(tmp_path) -> tuple[MainWindowV3, ProjectSession]:
    app = QApplication.instance() or QApplication([])
    window = MainWindowV3(
        AppSettings(), workspace_state_path=tmp_path / "shell-v3.json")
    window._screen_fit_done = True
    window.resize(1708, 921)
    session = ProjectSession.create(
        "ZERO PLAY PROJECT", tmp_path / "projects", tmp_path / "exports")
    session.project.source_duration_ms = 2_400_000
    window.session = session
    window._refresh_clip_list()
    window.stack.setCurrentWidget(window.workspace)
    window.show()
    app.processEvents()
    return window, session


def test_zero_play_review_keeps_authorities_and_opens_locked_rails(tmp_path):
    window, session = _window(tmp_path)
    review = window._v3_review
    assert review._zero_mode is True
    assert review.zero_overlay.isVisible()
    assert review.rail_state() == ReviewRailState(True, True)
    assert review.footer.height() == 38
    assert review.footer.count_label.text() == "0 plays  ·  0 logged"
    assert review.zero_overlay._film_surface is window.player.video_widget
    assert review.zero_overlay._detect_source is window.detect_plays_button
    assert review.zero_overlay._new_clip_source is window.new_clip_button
    card = review.zero_overlay.findChild(QWidget, "V3ZeroPlayCard")
    assert card is not None
    assert (card.width(), card.height()) == (520, 320)
    privacy = review.zero_overlay.findChild(QLabel, "V3ZeroPrivacy")
    assert privacy is not None
    assert privacy.text() == \
        "Detection and playback stay on this computer."
    assert window.live_v3_objects_valid()
    session.close()
    window.close()


def test_zero_play_ledger_card_forwards_to_live_detect_action(tmp_path):
    window, session = _window(tmp_path)
    body = window.findChild(QWidget, "V3ZeroLedgerBody")
    card = window.findChild(QWidget, "V3ZeroLedgerCard")
    action = window.findChild(QPushButton, "V3ZeroLedgerDetect")
    assert body is not None and body.isVisible()
    assert card is not None and card.width() <= 292
    assert action is not None and action.text() == "Run Detect Plays"

    calls: list[str] = []
    window.detect_plays_button.clicked.disconnect()
    window.detect_plays_button.clicked.connect(lambda: calls.append("detect"))
    action.click()
    QApplication.processEvents()
    assert calls == ["detect"]

    session.close()
    window.close()


def test_zero_overlay_forwards_to_existing_buttons_without_owning_logic():
    app = QApplication.instance() or QApplication([])
    film = QWidget()
    film.resize(900, 500)
    detect = QPushButton("Detect")
    new_clip = QPushButton("New")
    calls: list[str] = []
    detect.clicked.connect(lambda: calls.append("detect"))
    new_clip.clicked.connect(lambda: calls.append("new"))
    overlay = ZeroPlayOverlayV3(
        film_surface=film,
        detect_source=detect,
        new_clip_source=new_clip,
    )
    overlay.set_zero_mode(True)
    overlay.detect_button.click()
    overlay.new_clip_button.click()
    app.processEvents()
    assert calls == ["detect", "new"]
    assert overlay.parentWidget() is film


def test_review_registers_the_locked_ibm_plex_families(tmp_path):
    window, session = _window(tmp_path)
    assert SANS_FAMILY in window._v3_font_families
    assert MONO_FAMILY in window._v3_font_families
    session.close()
    window.close()
