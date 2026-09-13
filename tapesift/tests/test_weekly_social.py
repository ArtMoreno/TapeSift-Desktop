"""Weekly exports carry the saved truth and share editable copy across formats."""
from dataclasses import replace
import json

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from PySide6.QtPdf import QPdfDocument
from PySide6.QtTest import QTest

from tapesift.models.clip import Clip
from tapesift.services.heatmap_service import build_heatmap, select_heatmap
from tapesift.services.heatmap_export import export_all, read_png_metadata
from tapesift.ui_v3.heatmap_page import HeatmapPageV3
from tapesift.ui_v3.weekly_social import tagged_count
from tapesift.ui_v3.theme import stylesheet


@pytest.fixture(scope="module")
def app():
    application = QApplication.instance() or QApplication([])
    previous = application.styleSheet()
    application.setStyleSheet(stylesheet())
    yield application
    application.setStyleSheet(previous)


def sample():
    return build_heatmap([
        Clip(0, 1000, clip_number=1, details=dict(quarter="Q1", run_pass="Pass",
             player_name="Toney", result="Completion; Reception; TD; Touchdown; First Down")),
        Clip(1500, 2500, clip_number=2, details=dict(quarter="OT", run_pass="Run",
             player_name="Fletcher", result="Gain; First Down")),
        Clip(3000, 4000, clip_number=3),
    ], title="MIAMI O VS. STANFORD D — Game Heat Map", layered=True)


def test_social_export_counts_dimensions_metadata_and_carousel_manifest(app, tmp_path):
    data = replace(sample(), report_template="weekly_summary", report_orientation="portrait",
                   social_text=(("headline", "Weekly film"), ("film_note", "My own analysis")))
    assert tagged_count(data, "touchdown") == 1  # Alias pair in one clip is not two TDs.
    assert tagged_count(data, "first down") == 2
    assert data.type_counts["Unlogged"] == 1
    assert sum(p.snaps for p in data.players) == 2
    single = export_all(data, tmp_path, "summary", ("png", "pdf", "svg", "json"))
    assert len(single) == 4
    img = QImage(str(single[0]))
    assert (img.width(), img.height()) == (1080, 1350)
    assert read_png_metadata(single[0])["social_text"]["film_note"] == "My own analysis"
    carousel = replace(data, report_template="weekly_carousel", report_slide=3)
    paths = export_all(carousel, tmp_path, "story", ("png", "pdf", "svg", "json", "csv"))
    assert len(paths) == 11
    for slide in (1, 2, 3):
        png = tmp_path / f"story-{slide:02d}.png"
        payload = read_png_metadata(png)
        assert payload["report_slide"] == slide
        assert payload["plays"] == data.as_dict()["plays"]
        assert payload["social_text"] == dict(data.social_text)
        doc = QPdfDocument()
        doc.load(str(tmp_path / f"story-{slide:02d}.pdf"))
        assert doc.pageCount() == 1
        size = doc.pagePointSize(0)
        assert abs(size.height()/size.width()-1.25) < .01
        doc.close()
    images = [QImage(str(tmp_path / f"story-{i:02d}.png")) for i in (1,2,3)]
    assert images[0] != images[1] and images[1] != images[2]


def test_social_editor_saves_reloads_and_exports_all_slides_without_changing_clips(app, tmp_path):
    data = sample()
    before = data.to_json()
    draft = tmp_path / "game.social.json"
    page = HeatmapPageV3()
    page.set_snapshot(data, tmp_path, "game", draft_path=draft)
    page.template.setCurrentIndex(page.template.findData("weekly_summary"))
    page.social_inputs["film_note"].setText("Look at the protection on third down.")
    page.social_inputs["takeaway_1"].setText("An analyst observation.")
    page.social_clip.setCurrentIndex(1)
    page.resize(1248,768)
    page.show()
    QTest.qWait(80)
    assert page.settings_scroll.horizontalScrollBar().maximum() == 0
    page.save_social.click()
    assert json.loads(draft.read_text())["clip_id"] == data.plays[0].clip_id
    page.template.setCurrentIndex(page.template.findData("weekly_carousel"))
    page.slide.setCurrentIndex(2)
    assert page.data.report_slide == 3
    assert page.orientation.currentData() == "portrait"
    assert dict(page.data.social_text)["film_note"] == "Look at the protection on third down."
    assert "-01.png" in page.destination_note.text() and "-03.png" in page.destination_note.text()
    page.export.click()
    assert len(page.written) == 3 and all(p.exists() for p in page.written)
    assert "Exported 3" in page.status.text()
    page.set_snapshot(data, tmp_path, "game", draft_path=draft)
    assert page.social_inputs["takeaway_1"].text() == "An analyst observation."
    assert data.to_json() == before
    page.set_snapshot(data, tmp_path, "different", draft_path=tmp_path/"different.social.json")
    assert not page.social_inputs["film_note"].text()
    page.close()


def test_failed_carousel_writer_preserves_existing_exports(app, tmp_path, monkeypatch):
    from tapesift.services import heatmap_export as service
    previous = tmp_path / "story-01.png"
    previous.write_bytes(b"previous export")
    writer = service._WRITERS["png"]
    def fail_second(data, path, **kwargs):
        if data.report_slide == 2:
            raise OSError("disk full")
        return writer(data, path, **kwargs)
    monkeypatch.setitem(service._WRITERS, "png", fail_second)
    with pytest.raises(OSError, match="disk full"):
        export_all(replace(sample(), report_template="weekly_carousel"), tmp_path, "story", ("png",))
    assert previous.read_bytes() == b"previous export"
    assert not (tmp_path / "story-02.png").exists()


def test_social_empty_and_filtered_views_export_without_inventing_data(app, tmp_path):
    data = replace(select_heatmap(sample(), quarter="OT"), report_template="weekly_summary")
    export_all(data, tmp_path, "ot", ("png",))
    assert read_png_metadata(tmp_path/"ot.png")["play_count"] == 1
    empty = replace(build_heatmap([], layered=True), report_template="weekly_carousel")
    assert len(export_all(empty, tmp_path, "empty", ("png",))) == 3
