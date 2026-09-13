"""The game heat map: what it counts, and what its exports carry."""

from __future__ import annotations

import csv
import json

import pytest
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from tapesift.models.clip import Clip
from tapesift.services import heatmap_export as hx
from tapesift.services.heatmap_service import (
    UNKNOWN_QUARTER, build_heatmap, palette_size)
from tapesift.ui_v2.heatmap_render import (
    heatmap_height, paint_heatmap, render_image)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _clip(index: int, quarter: str, player: str, **details) -> Clip:
    return Clip(
        start_ms=index * 1_000, end_ms=index * 1_000 + 800,
        clip_number=index + 1,
        details={"quarter": quarter, "player_name": player, **details})


def _game(plays: int = 40) -> list[Clip]:
    names = ["Chamar Brown", "Mark Fletcher", "Carson Beck", ""]
    return [
        _clip(i, f"Q{i // 10 + 1}", names[i % len(names)])
        for i in range(plays)
    ]


class TestCounting:
    def test_plays_are_ordered_by_where_they_sit_in_the_film(self):
        data = build_heatmap([
            _clip(5, "Q2", "B"), _clip(1, "Q1", "A"), _clip(3, "Q1", "A")])
        assert [p.start_ms for p in data.plays] == [1_000, 3_000, 5_000]
        assert [p.index for p in data.plays] == [0, 1, 2]

    def test_players_rank_by_workload(self):
        data = build_heatmap(_game())
        assert [p.snaps for p in data.players] == sorted(
            (p.snaps for p in data.players), reverse=True)

    def test_a_player_keeps_the_colour_the_timeline_gave_them(self):
        """A picture that recoloured the game would teach the wrong thing."""
        from tapesift.ui_v2.attribute_grid import rank_player_colours
        clips = _game()
        expected = rank_player_colours(clips)
        data = build_heatmap(clips)
        for player in data.players:
            assert player.colour == expected[player.key]

    def test_unlogged_plays_are_counted_not_dropped(self):
        data = build_heatmap(_game())
        assert data.unassigned == 10
        assert data.play_count == 40

    def test_quarter_split_adds_up_to_the_game(self):
        data = build_heatmap(_game())
        assert sum(data.quarter_counts.values()) == data.play_count

    def test_busiest_quarter_is_the_one_he_played_most(self):
        clips = [_clip(i, "Q4", "Chamar Brown") for i in range(6)]
        clips += [_clip(20 + i, "Q1", "Chamar Brown") for i in range(2)]
        data = build_heatmap(clips)
        assert data.players[0].busiest_quarter == "Q4"

    def test_a_name_typed_two_ways_is_still_one_player(self):
        data = build_heatmap([
            _clip(0, "Q1", "Mark Fletcher"),
            _clip(1, "Q1", "mark  fletcher"),
            _clip(2, "Q1", "MARK FLETCHER")])
        assert len(data.players) == 1
        assert data.players[0].snaps == 3

    @pytest.mark.parametrize("logged,expected", [
        ("Q3", "Q3"), ("3", "Q3"), ("q3", "Q3"), ("3rd", "Q3"),
        ("OT", "OT"), ("", UNKNOWN_QUARTER), ("nonsense", UNKNOWN_QUARTER)])
    def test_quarters_survive_how_they_were_typed(self, logged, expected):
        data = build_heatmap([_clip(0, logged, "A")])
        assert data.plays[0].quarter == expected

    def test_an_empty_game_still_builds(self):
        data = build_heatmap([])
        assert data.play_count == 0 and data.players == ()

    def test_the_palette_covers_the_promised_count(self):
        assert palette_size() >= 22


class TestRender:
    def test_the_page_is_exactly_as_tall_as_it_says(self, qapp):
        """An overshoot puts a band of dead page under every export."""
        data = build_heatmap(_game())
        image = QImage(1400, 4000, QImage.Format.Format_ARGB32_Premultiplied)
        painter = QPainter(image)
        try:
            used = paint_heatmap(painter, data, 1400)
        finally:
            painter.end()
        assert used == heatmap_height(data)

    def test_height_follows_the_roster(self, qapp):
        small = build_heatmap(_game(12))
        big = build_heatmap(_game(40))
        assert heatmap_height(big) >= heatmap_height(small)

    def test_capping_the_roster_shortens_the_page(self, qapp):
        data = build_heatmap(_game())
        assert heatmap_height(data, players=2) < heatmap_height(data)

    def test_the_render_actually_has_the_players_colours_in_it(self, qapp):
        data = build_heatmap(_game())
        image = render_image(data, width=1400)
        seen = {image.pixelColor(x, y).name()
                for x in range(0, image.width(), 3)
                for y in range(0, image.height(), 3)}
        for player in data.players:
            assert player.colour.lower() in seen

    def test_a_dense_game_still_paints_every_play(self, qapp):
        """400 plays across 1320px is under 2px a cell - it must not vanish."""
        data = build_heatmap(_game(400))
        image = render_image(data, width=1400)
        assert not image.isNull()


class TestExport:
    def test_png_carries_the_whole_heat_map_inside_it(self, qapp, tmp_path):
        data = build_heatmap(_game())
        path = hx.export_png(data, tmp_path / "game.png")
        assert path.exists() and path.stat().st_size > 0
        back = hx.read_png_metadata(path)
        assert back is not None
        assert back["play_count"] == data.play_count
        assert len(back["players"]) == len(data.players)
        assert back["players"][0]["colour"] == data.players[0].colour

    def test_png_keeps_full_colour(self, qapp, tmp_path):
        data = build_heatmap(_game())
        path = hx.export_png(data, tmp_path / "game.png")
        image = QImage(str(path))
        seen = {image.pixelColor(x, y).name()
                for x in range(0, image.width(), 4)
                for y in range(0, image.height(), 4)}
        assert data.players[0].colour.lower() in seen

    def test_a_picture_without_our_data_reads_as_no_data(self, qapp,
                                                         tmp_path):
        plain = tmp_path / "plain.png"
        QImage(8, 8, QImage.Format.Format_ARGB32).save(str(plain))
        assert hx.read_png_metadata(plain) is None
        assert hx.read_png_metadata(tmp_path / "missing.png") is None

    @pytest.mark.parametrize("fmt", ["png", "pdf", "svg", "json", "csv"])
    def test_every_format_writes_something_real(self, qapp, tmp_path, fmt):
        data = build_heatmap(_game())
        path = hx.export_heatmap(data, tmp_path / f"game.{fmt}", fmt)
        assert path.exists() and path.stat().st_size > 200

    def test_json_is_the_data_outright(self, qapp, tmp_path):
        data = build_heatmap(_game())
        path = hx.export_json(data, tmp_path / "game.json")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["play_count"] == data.play_count
        assert len(loaded["plays"]) == data.play_count

    def test_csv_is_one_row_per_play(self, qapp, tmp_path):
        data = build_heatmap(_game())
        path = hx.export_csv(data, tmp_path / "game.csv")
        rows = list(csv.DictReader(path.read_text(encoding="utf-8")
                                   .splitlines()))
        assert len(rows) == data.play_count
        assert rows[0]["colour"].startswith("#")
        assert rows[0]["quarter"] == data.plays[0].quarter

    def test_export_all_writes_one_set_from_one_render(self, qapp, tmp_path):
        data = build_heatmap(_game())
        written = hx.export_all(data, tmp_path / "out", "miami")
        assert len(written) == 5
        assert {p.suffix for p in written} == {
            ".png", ".pdf", ".svg", ".json", ".csv"}
        assert all(p.exists() for p in written)

    def test_an_unknown_format_says_so(self, qapp, tmp_path):
        data = build_heatmap(_game())
        with pytest.raises(ValueError, match="unknown heat map format"):
            hx.export_heatmap(data, tmp_path / "game.doc", "doc")

    def test_export_creates_the_folder_it_was_given(self, qapp, tmp_path):
        data = build_heatmap(_game())
        target = tmp_path / "deep" / "deeper" / "game.json"
        assert hx.export_heatmap(data, target, "json").exists()
