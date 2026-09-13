"""Saved-row truth, format parity and live controls of the V3 heat map."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from tapesift.models.clip import Clip
from tapesift.services import heatmap_export as hx
from tapesift.services.heatmap_service import build_heatmap, select_heatmap
from tapesift.services.project_service import ProjectSession
from tapesift.ui_v2.heatmap_render import heatmap_height, paint_heatmap, layer_legend
from tapesift.ui_v3.heatmap_page import HeatmapPageV3, saved_heatmap
from tapesift.ui_v3.theme import stylesheet


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def rows():
    specs = [("Q1", "Pass", "RPO Run", "INT; Penalty; Interception", "A"),
             ("2OT", "Other", "Screen", "Fumble", "B"),
             ("Q11", "", "Pass", "Fumble Lost; Custom result", ""),
             ("Q1", "Maybe", "Run", "", "B"),
             ("OT", "Run", "", "Interception", "B")]
    return [Clip(start_ms=i*1000,end_ms=i*1000+800,clip_number=i+1,
                 details=dict(quarter=q,run_pass=t,play_type=c,result=r,player_name=p))
            for i,(q,t,c,r,p) in enumerate(specs)]


def test_layers_keep_recorded_values_and_conservative_turnovers():
    data = build_heatmap(rows(), layered=True, tag_styles={"int":{"color":"#123456"}})
    assert [p.type_category for p in data.plays] == ["Pass", "Other", "Unlogged", "Unresolved", "Run"]
    assert data.plays[0].concept == "RPO Run" and data.plays[2].concept == "Pass"
    assert data.plays[0].results == ("INT", "Penalty", "Interception")
    assert data.result_counts == {"int":1,"interception":2,"penalty":1,"fumble":1,"fumble lost":1,"custom result":1}
    assert data.turnover_count == 3 and not data.plays[1].turnover
    assert data.plays[0].result_colours[0] == data.plays[-1].result_colours[0] == "#123456"
    assert data.quarters == ("Q1","OT","2OT","?")
    assert data.plays[2].quarter == "?" and data.plays[2].quarter_raw == "Q11"
    assert layer_legend(data,"players")[-1][2]==data.plays[2].colour
    view = select_heatmap(data, quarter="Q1", layers=("results",), quarter_layer="results")
    assert [p.clip_id for p in view.plays] == [data.plays[0].clip_id,data.plays[3].clip_id]
    assert view.source_clip_count == 5 and view.play_count == 2
    assert view.subtitle.startswith("2 clips") and "Q1" in view.subtitle
    assert {p.key:p.colour for p in view.players} == {p.key:p.colour for p in data.players}
    assert view.as_dict()["visible_layers"] == ["results"]
    assert view.as_dict()["plays"][0]["run_pass"] == "Pass"


def test_known_families_kneels_and_penalties_match_exports(tmp_path):
    clips = [Clip(i * 1000, i * 1000 + 800, details=details)
             for i, details in enumerate([
                 {"run_pass": "Run", "result": "Kneel", "yards": "-1"},
                 {"run_pass": "No Play", "result": "False Start; Penalty Accepted"},
                 {"run_pass": "Pass", "result": "Completion; Offside; Penalty Declined"},
                 {"run_pass": "Special", "result": "Touchback"},
             ])]
    data = build_heatmap(clips, layered=True)
    assert [p.type_category for p in data.plays] == ["Run", "No Play", "Pass", "Special"]
    assert data.type_counts["Unresolved"] == 0
    assert sum(data.type_counts.values()) == 4
    assert data.turnover_count == 0
    assert data.plays[1].results == ("False Start", "Penalty Accepted")
    hx.export_csv(data, tmp_path / "families.csv")
    with (tmp_path / "families.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    payload = json.loads(data.to_json())["plays"]
    for row, play in zip(rows, payload):
        for key in ("run_pass", "type_category", "result", "yards"):
            assert row[key] == play[key]


def test_snapshot_uses_saved_enabled_rows_and_keeps_versions_separate(tmp_path):
    session=ProjectSession.create("Saved game",tmp_path,tmp_path/"exports")
    try:
        clips=rows()
        clips[1].enabled=False
        clips[3].start_ms=clips[0].start_ms
        clips[3].end_ms=clips[0].end_ms
        session.clips=clips;session.save()
        before=list(session.conn.iterdump())
        session.clips[0].details["run_pass"]="Run"
        session.project.name="Unsaved new name"
        session.dirty=True
        data,folder,stem=saved_heatmap(session)
        assert data.play_count==4 and data.plays[0].run_pass=="Pass"
        assert {p.clip_id for p in data.plays}=={c.id for c in clips if c.enabled}
        assert "Saved game" in data.title and "versions count separately" in data.scope
        assert folder==tmp_path/"exports" and stem=="Saved game-heat-map"
        assert before==list(session.conn.iterdump()) and session.dirty
        assert session.clips[0].details["run_pass"]=="Run"
        session.conn.execute("UPDATE projects SET name=? WHERE id=?",("In-flight name",session.project.id))
        session.conn.execute("UPDATE clips SET details_json=? WHERE id=?",
                             (json.dumps({"run_pass":"In-flight value"}),clips[0].id))
        with pytest.raises(ValueError,match="update is in progress"):
            saved_heatmap(session)
        assert session.conn.in_transaction
        assert session.project_repo.load().name=="In-flight name"
        session.conn.rollback()
        assert saved_heatmap(session)[0].title.startswith("Saved game")
    finally:
        session.close()


def test_layered_png_svg_json_csv_carry_the_same_rows_and_pdf_has_real_content(qapp,tmp_path):
    from PySide6.QtPdf import QPdfDocument
    data=select_heatmap(build_heatmap(rows(),title="Format parity QA",layered=True),
                        quarter="Q1",layers=("run_pass","results","players"))
    paths=hx.export_all(data,tmp_path,"game")
    assert len(paths)==5
    payload=json.loads((tmp_path/"game.json").read_text())
    assert payload==hx.read_png_metadata(tmp_path/"game.png")==data.as_dict()
    root=ET.parse(tmp_path/"game.svg").getroot()
    assert json.loads(root.find("{http://www.w3.org/2000/svg}desc").text)==payload
    with (tmp_path/"game.csv").open(newline="",encoding="utf-8") as handle:
        table=list(csv.DictReader(handle))
    assert [r["clip_id"] for r in table]==[p.clip_id for p in data.plays]
    for row,play in zip(table,data.plays):
        assert row["run_pass"]==play.run_pass and row["concept"]==play.concept
        assert json.loads(row["results_json"])==list(play.results)
        assert json.loads(row["result_colours_json"])==list(play.result_colours)
        assert row["colour"]==play.colour and row["type_colour"]==play.type_colour
    pdf=QPdfDocument()
    assert pdf.load(str(tmp_path/"game.pdf"))==QPdfDocument.Error.None_
    assert pdf.pageCount()==1
    text=pdf.getAllText(0).text()
    assert "Format parity QA" in text and "PRIMARY PLAYER ASSIGNMENTS" in text
    assert "Q1 (1–4)" not in text and "Q1 (1)" in text and "Q1 (4)" in text
    assert not pdf.render(0,QImage(str(tmp_path/"game.png")).size()/2).isNull()
    pdf.close()


@pytest.mark.parametrize("width",[800,1400])
def test_layered_measure_matches_paint_for_wrapped_categories_and_overtime(qapp,width):
    clips=rows()
    clips[0].details["result"]="A long custom outcome that needs several lines; INT"
    data=build_heatmap(clips,layered=True)
    height=heatmap_height(data,width=width)
    image=QImage(width,height,QImage.Format.Format_ARGB32)
    painter=QPainter(image)
    try:
        assert paint_heatmap(painter,data,width)==height
    finally:
        painter.end()
    assert not image.isNull()


def test_export_staging_preserves_old_files_if_a_later_writer_fails(qapp,tmp_path,monkeypatch):
    data=build_heatmap(rows(),layered=True)
    old=tmp_path/"game.json";old.write_text("old export")
    def fail(*args,**kwargs):raise OSError("deliberate writer failure")
    monkeypatch.setitem(hx._WRITERS,"csv",fail)
    with pytest.raises(OSError,match="Files already written: none"):
        hx.export_all(data,tmp_path,"game",("json","csv"))
    assert old.read_text()=="old export" and not (tmp_path/"game.csv").exists()
    assert not list(tmp_path.glob(".tapesift-heatmap-*"))


def test_export_name_and_partial_commit_report_are_exact(qapp,tmp_path,monkeypatch):
    data=build_heatmap(rows(),layered=True)
    with pytest.raises(OSError,match="Enter a file name"):
        hx.export_all(data,tmp_path,"...")
    with pytest.raises(ValueError):
        hx.export_all(data,tmp_path/"absent","name",("json","exe"))
    assert not (tmp_path/"absent").exists()
    written=hx.export_all(data,tmp_path,"../CON",("json",))
    assert written==[tmp_path/"CON-clip.json"]
    old=tmp_path/"game.csv";old.write_text("old table")
    replace=hx.os.replace
    def fail_second(source,target):
        if Path(target).suffix==".csv":raise OSError("deliberate replacement failure")
        replace(source,target)
    monkeypatch.setattr(hx.os,"replace",fail_second)
    with pytest.raises(OSError,match="game.json"):
        hx.export_all(data,tmp_path,"game",("json","csv"))
    assert (tmp_path/"game.json").is_file() and old.read_text()=="old table"


def test_native_page_controls_project_one_snapshot_and_report_failure(qapp,tmp_path,monkeypatch):
    previous=qapp.styleSheet();qapp.setStyleSheet(stylesheet())
    page=HeatmapPageV3();page.resize(1248,608)
    try:
        snapshot=build_heatmap(rows(),layered=True)
        page.set_snapshot(snapshot,tmp_path,"page")
        page.template.setCurrentIndex(page.template.findData(""))
        page.show();QTest.qWait(50)
        page.view.setCurrentIndex(page.view.findData("Q1"))
        assert page.data.play_count==2 and page.snapshot is snapshot
        page.layer_checks["players"].setChecked(False)
        assert page.data.layers==("run_pass","results")
        page.canvas.quarter_layer.setCurrentIndex(page.canvas.quarter_layer.findData("results"))
        assert page.data.quarter_layer=="results"
        page.zoom_in.click();QTest.qWait(20)
        assert page.zoom==125 and page.chart_scroll.horizontalScrollBar().maximum()>0
        assert page.export.isVisible() and not page.settings_scroll.isAncestorOf(page.export)
        original=page.folder.text()
        monkeypatch.setattr(QFileDialog,"getExistingDirectory",lambda *args:"")
        page.browse.click();assert page.folder.text()==original
        for button in page.format_buttons.values():button.setChecked(False)
        assert not page.export.isEnabled()
        page.format_buttons["json"].setChecked(True)
        page.filename.setText("...");assert not page.export.isEnabled()
        page.filename.setText("page")
        blocker=tmp_path/"not-a-folder";blocker.write_text("preserve")
        page.folder.setText(str(blocker));page.export.click()
        assert "Export failed" in page.status.text() and not page.written
        assert blocker.read_text()=="preserve"
        page.folder.setText(str(tmp_path));assert page.status.text()==""
        page.export.click()
        assert page.written==[tmp_path/"page.json"] and "Exported 1" in page.status.text()
        assert json.loads(page.written[0].read_text())==page.data.as_dict()
        page.layer_checks["players"].setChecked(True)
        assert page.status.text()==""
    finally:
        page.close();qapp.setStyleSheet(previous)
