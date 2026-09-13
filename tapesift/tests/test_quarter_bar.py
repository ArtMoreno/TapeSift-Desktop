"""Quarter cues are saved metadata decorations, never media boundaries."""
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from tapesift.models.clip import Clip
from tapesift.services.heatmap_palette import QUARTER_COLORS, quarter_colour, quarter_starts
from tapesift.ui_core.timeline import Timeline, TimelineBlock
from tapesift.ui_v2.attribute_grid import AttributeGrid


def test_period_changes_use_source_order_and_keep_unknowns_neutral():
    clips = [Clip(4000, 4500, details={"quarter": "2nd"}),
             Clip(1000, 1500, details={"quarter": "Q1"}),
             Clip(2000, 2500, details={}),
             Clip(3000, 3500, details={"quarter": "Q1"}),
             Clip(5000, 5500, details={"quarter": "OT"}),
             Clip(6000, 6500, details={"quarter": "2OT"})]
    assert quarter_starts(clips) == [(1000, "Q1"), (4000, "Q2"), (5000, "OT"), (6000, "2OT")]
    assert len(set(QUARTER_COLORS.values())) == 5
    assert quarter_colour("") not in QUARTER_COLORS.values()
    assert quarter_colour("2OT") == QUARTER_COLORS["OT"]


def test_pylons_and_quarter_ink_do_not_change_geometry_or_snap_targets():
    app = QApplication.instance() or QApplication([])
    timeline = Timeline()
    timeline.resize(1000, timeline.height())
    timeline.setRange(0, 10000)
    timeline.set_blocks([TimelineBlock(1000, 8000, clip_id="clip", colour="#589bce")])
    before = (timeline.height(), timeline.visible_range(), timeline.blocks(), timeline._snap_ms(4000))
    timeline.set_ink_blocks(True)
    timeline.set_quarter_pylons([(4000, "Q2")])
    assert (timeline.height(), timeline.visible_range(), timeline.blocks(), timeline._snap_ms(4000)) == before
    frame = timeline._render_static().toImage()
    x, y = timeline._x_for(4000), timeline._band_top()-8
    assert frame.pixelColor(x, y) == QColor("#f18b38")
    timeline.set_quarter_pylons([])
    assert timeline._render_static().toImage().pixelColor(x, y) != QColor("#f18b38")
    grid = AttributeGrid(); grid.enable_review_style()
    row = next(row for row in grid.rows() if row.key == "quarter")
    for quarter, color in QUARTER_COLORS.items():
        clip = Clip(0, 1000, details={"quarter": quarter})
        assert grid._lane_color_entries(row, clip) == [(quarter, color)]
    grid.set_clips([Clip(2000, 3000, details={"quarter": "2nd"}),
                    Clip(0, 1000, details={"quarter": "1"})])
    assert grid.quarter_spans() == [(0, 1000, "Q1"), (2000, 3000, "Q2")]
    grid.close(); timeline.close()
