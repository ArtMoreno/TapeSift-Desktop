"""Shared color identities, honest projections and native report export parity."""
import csv
import json
from dataclasses import replace
from xml.etree import ElementTree

import pytest
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication
from PySide6.QtPdf import QPdfDocument

from tapesift.models.clip import Clip
from tapesift.services.heatmap_export import export_all, read_png_metadata
from tapesift.services.heatmap_palette import player_colour
from tapesift.services.heatmap_service import build_heatmap, select_heatmap
from tapesift.ui_v2.attribute_grid import AttributeGrid
from tapesift.ui_v2.heatmap_render import heatmap_height, paint_heatmap
from tapesift.ui_v3.heatmap_reports import TEMPLATES, field_zone, report_sections
from tapesift.ui_v3.heatmap_page import HeatmapPageV3


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def sample():
    return build_heatmap([
        Clip(0, 1000, clip_number=1, details=dict(quarter="Q2", run_pass="Pass", player_name="M. Toney",
            other_players="B. Nicholson", quarterback="#10 D. Mensah", result="Completion; First Down",
            down_distance="3rd & 7", ball_on="OWN 41")),
        Clip(1500, 2500, clip_number=2, details=dict(quarter="2OT", run_pass="Run", player_name="M. Fletcher",
            result="Gain; Touchdown", down_distance="2nd & Goal", ball_on="OPP 19")),
        Clip(3000, 5000, clip_number=3, details=dict(quarter="", run_pass="Maybe", player_name="D. Mensah",
            result="Interception", down_distance="To Go 12", ball_on="25")),
        Clip(5500, 7000, clip_number=4, details=dict(quarter="Q2", run_pass="", player_name="", result="")),
    ], title="Report export QA", layered=True)


def test_player_colors_match_live_roles_exports_filters_and_new_workload(app):
    clips = [Clip(0, 1000, details=dict(player_name="M. Toney", other_players="M. Fletcher",
                                    quarterback="#10 D. Mensah")),
             Clip(2000, 3000, details=dict(player_name="D. Mensah"))]
    grid = AttributeGrid(); grid.enable_review_style(); grid.set_clips(clips)
    row = next(r for r in grid.rows() if r.key == "people")
    entries = dict(grid._lane_color_entries(row, clips[0]))
    data = build_heatmap(clips, layered=True)
    assert entries["M. Toney"] == data.plays[0].colour
    assert entries["QB #10 D. Mensah"] == data.plays[1].colour
    assert len(set(entries.values())) == 3
    before = data.plays[0].colour
    clips.extend(Clip(i*1000, i*1000+500, details=dict(player_name="New Player")) for i in range(4, 30))
    grid.set_clips(clips)
    assert grid._people_colour(clips[0]) == build_heatmap(clips, layered=True).plays[0].colour == before
    assert len({player_colour(f"Player {i}") for i in range(200)}) == 200
    grid.close()


def test_reports_keep_unknown_context_and_count_each_result_without_inference():
    data = sample()
    results = report_sections(replace(data, report_template="result_pulse"))[0]
    counts = {name: sum(values) for name, _, values in results.rows}
    assert counts == {"Completion": 1, "First Down": 1, "Gain": 1, "Touchdown": 1, "Interception": 1}
    assert data.quarters == ("Q2", "2OT", "?")
    assert [field_zone(v) for v in ("OWN 20", "OWN 21", "OPP 40", "OPP 39", "OPP 20", "OPP 19", "25", "MIA 25")] == [0,1,2,3,3,4,None,None]
    sections = report_sections(replace(data, report_template="field_position"))
    assert sum(row[2][0] for row in sections[0].rows) == 2
    assert "2 unknown" in sections[0].note
    situation = report_sections(replace(data, report_template="situation"))
    assert sum(sum(values) for _, _, values in situation[0].rows) == 4
    assert sum(sum(values) for _, _, values in situation[1].rows) == 4


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize("orientation", ("landscape", "portrait"))
def test_all_report_formats_use_the_measured_layout_and_same_snapshot(app, tmp_path, template, orientation):
    data = sample()
    if template == "player_spotlight":
        key = data.players[0].key
        data = replace(select_heatmap(data, player_key=key), report_player=key)
    data = replace(data, report_template=template, report_orientation=orientation)
    width = 720 if orientation == "portrait" else 1400
    height = heatmap_height(data, width=width)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    try:
        assert paint_heatmap(painter, data, width) == height
    finally:
        painter.end()
    paths = export_all(data, tmp_path, "report")
    assert len(paths) == 5
    payload = json.loads((tmp_path / "report.json").read_text())
    assert payload == read_png_metadata(tmp_path / "report.png") == data.as_dict()
    svg = ElementTree.parse(tmp_path / "report.svg").getroot()
    assert json.loads(svg.find("{http://www.w3.org/2000/svg}desc").text) == payload
    with (tmp_path / "report.csv").open(newline="", encoding="utf-8") as file:
        assert [row["clip_id"] for row in csv.DictReader(file)] == [p.clip_id for p in data.plays]
    png = QImage(str(tmp_path / "report.png"))
    assert (png.width(), png.height()) == (width*2, height*2)
    pdf = QPdfDocument()
    assert pdf.load(str(tmp_path / "report.pdf")) == QPdfDocument.Error.None_
    assert pdf.pageCount() == 1
    assert TEMPLATES[template].upper() in pdf.getAllText(0).text()
    assert not pdf.render(0, png.size()/2).isNull()
    pdf.close()


def test_template_selection_player_filter_and_names_do_not_overwrite_other_reports(app, tmp_path):
    data = sample()
    page = HeatmapPageV3(); page.set_snapshot(data, tmp_path, "Game")
    page.template.setCurrentIndex(page.template.findData("player_spotlight"))
    key = page.report_player.currentData()
    assert page.data.plays and all(p.player_key == key for p in page.data.plays)
    assert page.snapshot is data
    name = page._export_stem()
    page.orientation.setCurrentIndex(page.orientation.findData("portrait"))
    assert page._export_stem() != name and "portrait" in page._export_stem()
    for button in page.format_buttons.values():
        button.setChecked(False)
    page.format_buttons["json"].setChecked(True)
    page.export.click()
    assert len(page.written) == 1
    assert json.loads(page.written[0].read_text()) == page.data.as_dict()
    assert page.data.report_player == key
    # An empty quarter retains the chosen identity; hidden Classic switches
    # must not silently remove report layers from exported metadata.
    page.view.setCurrentIndex(page.view.findData("2OT" if page.data.plays[0].quarter != "2OT" else "Q2"))
    assert page.data.play_count == 0
    assert page.data.report_player_name == page.report_player.currentText()
    page.template.setCurrentIndex(page.template.findData(""))
    for check in page.layer_checks.values():
        check.setChecked(False)
    assert not page.data.layers
    page.template.setCurrentIndex(page.template.findData("game_fingerprint"))
    assert set(page.data.layers) == set(page.layer_checks)
    page.close()
