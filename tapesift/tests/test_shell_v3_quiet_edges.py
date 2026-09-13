"""The standard compact bar keeps its live controls and one play-color meaning."""
from copy import deepcopy

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.tests.test_shell_v3_review import _window
from tapesift.ui_v3.tag_map_field import TagMapField
from tapesift.ui_v3.workspace_state import ReviewRailState


def test_compact_bar_survives_play_state_and_preserves_explicit_play_colors(tmp_path):
    app = QApplication.instance() or QApplication([])
    previous_sheet = app.styleSheet()
    previous_font = app.font()
    window = _window(tmp_path)
    app = QApplication.instance()
    session = ProjectSession.create("Quiet edges QA", tmp_path/"projects", tmp_path/"exports")
    for start, details in ((0, {"run_pass": "Run"}),
                           (10000, {"run_pass": "Pass", "result": "Touchdown"}),
                           (20000, {"play_type": "RPO"}), (30000, {})):
        session.add_clip(Clip(start, start+9000, details=details))
    window.session = session
    before = deepcopy([clip.details for clip in session.clips])
    window.settings.timeline_color_by = "result"
    window._refresh_clip_list()
    window._v3_review.set_rail_state(ReviewRailState(True, True))
    player, deck = window.player, window.control_center
    player._duration_changed(40000)
    colors = [block.colour for block in player.slider.blocks()]
    assert colors[:3] == [TagMapField.play_colors[key] for key in ("run", "pass", "rpo")]
    assert colors[3] == "#677078"
    assert [clip.details for clip in session.clips] == before
    assert window.settings.timeline_color_by == "result"  # No saved preference rewrite.
    controls = (player.timeline_fit_play, deck.in_button, deck.out_button,
                window._v3_timeline_zoom_cluster, *window._v3_transport_surface.buttons,
                player.predicted_snap_button, deck.overflow_button, window._v3_play_type_key)
    for width in (1908, 1250):
        window.resize(width, 800)
        app.processEvents()
        for playing in (True, False):
            deck.set_playing(playing)
            deck._apply_premium_geometry()
            app.processEvents()
            rectangles = [QRect(c.mapTo(deck,QPoint()),c.size()) for c in controls if c.isVisible()]
            assert all(c.isVisible() for c in controls[:-1])
            if window._v3_play_type_key.isVisible():
                assert window._v3_play_type_key.width() >= window._v3_play_type_key.sizeHint().width()
            assert all(r.left() >= 0 and r.right() < deck.width() for r in rectangles)
            assert all(a.right() < b.left() for a,b in zip(rectangles,rectangles[1:]))
            assert deck.current_label.isHidden() and deck.position_detail.isHidden()
            assert window._v3_tag_map_tools.isHidden() and deck.jog_toggle.isHidden()
            assert not player.attribute_grid.footer.isVisible()
    menu = window._v3_tools_menu
    assert menu.layoutDirection() == Qt.LayoutDirection.LeftToRight
    assert [a.text() for a in menu.actions() if not a.isSeparator()] == [
        "Show jog wheel", "Playback speed", "Tag Map options", "More playback tools", "Collapse Tag Map"]
    for action in menu.actions()[:2]:
        assert action in window._v3_legacy_transport_menu.actions()
    collapse = menu.actions()[-1]
    player.set_tag_map_collapsed(False, persist=False)
    collapse.trigger()
    app.processEvents()
    assert player.attribute_grid.isHidden()
    menu.aboutToShow.emit()
    assert collapse.text() == "Expand Tag Map"
    collapse.trigger()
    app.processEvents()
    assert not player.attribute_grid.isHidden()
    session.save()
    window.close()
    app.setStyleSheet(previous_sheet)
    app.setFont(previous_font)


def test_black_field_uses_neutral_ink_and_a_clear_lower_number_band():
    app = QApplication.instance() or QApplication([])
    image = QImage(650, 300, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    field = TagMapField()
    field.paint(painter, image.rect())
    painter.end()
    # Sample field material away from Windows' subpixel glyph antialiasing.
    for y in range(0, 260, 3):
        for x in range(0, 650, 3):
            pixel = image.pixelColor(x,y)
            assert max(pixel.red(),pixel.green(),pixel.blue())-min(pixel.red(),pixel.green(),pixel.blue()) < 20
    assert image.pixelColor(13,250).lightness() < 15
    # Numerals are in the clear lower band, separate from the overlaid cells.
    lower = sum(image.pixelColor(x,y).lightness() > 140 for y in range(270,295) for x in range(45,80))
    upper = sum(image.pixelColor(x,y).lightness() > 140 for y in range(40,70) for x in range(45,80))
    assert lower > 15 and upper == 0
