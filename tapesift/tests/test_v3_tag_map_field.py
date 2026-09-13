"""The field is presentation only; saved quarters keep film-time coordinates."""
from PySide6.QtWidgets import QApplication

from tapesift.models.clip import Clip
from tapesift.services.project_service import ProjectSession
from tapesift.tests.test_shell_v3_review import _window, _close_test_window
from tapesift.ui_v3.workspace_state import ReviewRailState


def test_field_reclaims_strip_and_pylons_follow_saved_quarters(tmp_path, monkeypatch):
    window = _window(tmp_path)
    app = QApplication.instance()
    window._v3_review.set_rail_state(ReviewRailState(False, True))
    session = ProjectSession.create('Field QA', tmp_path/'projects', tmp_path/'exports')
    for start, quarter in ((1000, 'Q1'), (4000, 'Q1'), (11000, 'Q2'), (23000, 'Q3')):
        session.add_clip(Clip(start, start+2000, details={'quarter': quarter}))
    window.session = session
    window._refresh_clip_list()
    player = window.player
    grid, header = player.attribute_grid, player.timeline_header
    player.set_tag_map_collapsed(False, persist=False)
    app.processEvents()
    height, video = grid.height(), player.video_widget.height()
    assert window._v3_play_field_ribbon is None
    assert header.height() == 0
    # Emulate the previous allocation without constructing another player.
    with monkeypatch.context() as patch:
        patch.setattr(window, '_position_map_controls', lambda: None)
        header.setFixedHeight(46)
        header.show()
        grid.setFixedHeight(height-46)
        player.layout().activate()
        assert player.video_widget.height() == video
    window._position_map_controls()
    window._apply_v3_review_geometry()
    player.layout().activate()
    assert grid.height() == height
    assert player.video_widget.height() == video
    assert grid._quarter_pylons == ((11000, 'Q2'), (23000, 'Q3'))
    assert not player.slider._quarter_pylons
    if player.timeline_overview is not None:
        assert not player.timeline_overview._quarter_pylons
    player._duration_changed(30000)
    for start, end in ((0, 30000), (8000, 28000)):
        # The shared timeline viewport owns V3 map coordinates.
        player.slider.fit_range(start, end)
        assert grid._visible_range == player.slider.visible_range()
        image = grid.grab().toImage()
        cache = grid._field_surface._cache.cacheKey()
        grid.grab()
        assert grid._field_surface._cache.cacheKey() == cache
        for ms in (11000, 23000):
            pixel = image.pixelColor(round(grid._x_for(ms)), grid._review_body_top()+12)
            assert pixel.name() == '#f18b38'
    assert [c.details['quarter'] for c in session.clips] == ['Q1', 'Q1', 'Q2', 'Q3']
