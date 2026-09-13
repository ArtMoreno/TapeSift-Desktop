"""Write the game heat map out, with its own data riding along.

A picture of a game gets mailed, pasted into a deck and screenshotted.
Six weeks later nobody can say which game it was or who the green one is.
So every export carries its source: the PNG keeps the full JSON in its
metadata, the SVG keeps it in a comment, and the sidecar formats are the
data outright. Open the file, get the picture; read the file, get the
numbers back.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import replace
from tempfile import TemporaryDirectory
from pathlib import Path

from PySide6.QtCore import QRectF, QSizeF
from PySide6.QtGui import QImage, QPageSize, QPainter, QPdfWriter
from PySide6.QtSvg import QSvgGenerator

from tapesift.services.heatmap_service import HeatmapData
from tapesift.services.filename_service import sanitize_filename_base
from tapesift.ui_v2.heatmap_render import (
    heatmap_height, paint_heatmap, render_image)

#: The PNG text key the data lands under. Read it back with
#: ``QImage.text(HEATMAP_METADATA_KEY)`` or any exiftool-style reader.
HEATMAP_METADATA_KEY = "tapesift.heatmap"
PNG_SCALE = 2.0

FORMATS: tuple[tuple[str, str, str], ...] = (
    ("png", "PNG image", "PNG Image (*.png)"),
    ("pdf", "PDF page", "PDF Document (*.pdf)"),
    ("svg", "SVG vector", "SVG Vector (*.svg)"),
    ("json", "JSON data", "JSON Data (*.json)"),
    ("csv", "CSV table", "CSV Table (*.csv)"),
)


def _stamp(image: QImage, data: HeatmapData) -> None:
    """Put the numbers inside the picture, not beside it."""
    image.setText(HEATMAP_METADATA_KEY, data.to_json(indent=None))
    image.setText("Title", data.title)
    image.setText("Description", data.subtitle)
    image.setText("Software", data.generator)


def export_png(data: HeatmapData, path: Path, *, width: int = 1400,
               scale: float = PNG_SCALE,
               players: int | None = None) -> Path:
    """Full colour at 2x, with the whole heat map embedded as text."""
    if data.report_template.startswith("weekly_") and width == 1400:
        width, scale = 1080, 1.0
    elif data.report_template and data.report_orientation == "portrait" and width == 1400:
        width = 720
    image = render_image(data, width=width, scale=scale, players=players)
    _stamp(image, data)
    if not image.save(str(path), "PNG"):
        raise OSError(f"could not write {path}")
    return path


def read_png_metadata(path: Path) -> dict[str, object] | None:
    """Get the numbers back out of a picture someone sent you."""
    image = QImage(str(path))
    if image.isNull():
        return None
    raw = image.text(HEATMAP_METADATA_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def export_pdf(data: HeatmapData, path: Path, *, width: int = 1400,
               players: int | None = None) -> Path:
    """Vector and print ready, so it survives being handed to a staff."""
    if data.report_template.startswith("weekly_") and width == 1400:
        width = 1080
    elif data.report_template and data.report_orientation == "portrait" and width == 1400:
        width = 720
    height = heatmap_height(data, width=width, players=players)
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QSizeF(width * .6, height * .6), QPageSize.Unit.Point)
                       if data.report_template else QPageSize(QPageSize.PageSizeId.A4))
    writer.setResolution(150)
    writer.setTitle(data.title)
    writer.setCreator(data.generator)
    painter = QPainter(writer)
    if not painter.isActive():
        raise OSError(f"could not open PDF output: {path}")
    try:
        # Fit the page's own geometry rather than assuming A4 landscape:
        # the picture is as wide as it is and should not be cropped.
        page = painter.viewport()
        ratio = min(page.width() / width, page.height() / height)
        painter.translate(
            (page.width() - width * ratio) / 2,
            (page.height() - height * ratio) / 2)
        painter.scale(ratio, ratio)
        painter.fillRect(QRectF(0, 0, width, height), painter.background())
        paint_heatmap(painter, data, width, players=players)
    finally:
        finished = painter.end()
    if not finished or not path.is_file() or not path.stat().st_size:
        raise OSError(f"could not finish output: {path}")
    return path


def export_svg(data: HeatmapData, path: Path, *, width: int = 1400,
               players: int | None = None) -> Path:
    """Vector with real text, and the data in a comment at the top."""
    if data.report_template.startswith("weekly_") and width == 1400:
        width = 1080
    elif data.report_template and data.report_orientation == "portrait" and width == 1400:
        width = 720
    height = heatmap_height(data, width=width, players=players)
    generator = QSvgGenerator()
    generator.setFileName(str(path))
    generator.setSize(generator.size().__class__(width, height))
    generator.setViewBox(QRectF(0, 0, width, height))
    generator.setTitle(data.title)
    generator.setDescription(data.to_json(indent=None))
    painter = QPainter(generator)
    if not painter.isActive():
        raise OSError(f"could not open SVG output: {path}")
    try:
        paint_heatmap(painter, data, width, players=players)
    finally:
        finished = painter.end()
    if not finished or not path.is_file() or not path.stat().st_size:
        raise OSError(f"could not finish output: {path}")
    return path


def export_json(data: HeatmapData, path: Path) -> Path:
    path.write_text(data.to_json(), encoding="utf-8")
    return path


def export_csv(data: HeatmapData, path: Path) -> Path:
    """One row per play, so it opens in whatever the staff already uses."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "index", "clip_number", "quarter", "player", "colour",
            "result", "start_ms", "end_ms", "title", "clip_id",
            "quarter_raw", "run_pass", "concept", "type_category",
            "type_colour", "results_json", "result_colours_json", "turnover",
            "view", "scope", "visible_layers_json", "play_action", "down_distance",
            "ball_on", "yards", "action", "other_players", "offense_team_id",
            "attack_direction", "game_clock"])
        for play in data.plays:
            writer.writerow([
                play.index, play.clip_number, play.quarter, play.player,
                play.colour, play.result, play.start_ms, play.end_ms,
                play.title, play.clip_id, play.quarter_raw, play.run_pass,
                play.concept, play.type_category, play.type_colour,
                json.dumps(play.results), json.dumps(play.result_colours),
                play.turnover, data.view, data.scope, json.dumps(data.layers),
                play.play_action, play.down_distance, play.ball_on, play.yards,
                play.action, play.other_players, play.offense_team_id,
                play.attack_direction, play.game_clock])
    return path


_WRITERS = {
    "png": export_png,
    "pdf": export_pdf,
    "svg": export_svg,
    "json": export_json,
    "csv": export_csv,
}


def export_heatmap(data: HeatmapData, path: Path, fmt: str,
                   **kwargs: object) -> Path:
    """Write one format by name."""
    key = fmt.lower().lstrip(".")
    writer = _WRITERS.get(key)
    if writer is None:
        raise ValueError(f"unknown heat map format: {fmt}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if key in ("json", "csv"):
        return writer(data, path)
    return writer(data, path, **kwargs)


def export_variants(data: HeatmapData, stem: str, formats):
    """The same manifest drives the preview of filenames and staged export."""
    for fmt in formats:
        if data.report_template == "weekly_carousel" and fmt in ("png", "pdf", "svg"):
            for slide in (1, 2, 3):
                yield replace(data, report_slide=slide), f"{stem}-{slide:02d}.{fmt}", fmt
        else:
            yield data, f"{stem}.{fmt}", fmt


def export_all(data: HeatmapData, folder: Path, stem: str,
               formats: tuple[str, ...] = ("png", "pdf", "svg", "json", "csv"),
               **kwargs: object) -> list[Path]:
    """Every chosen format from one render, so they cannot disagree."""
    keys = tuple(fmt.lower().lstrip(".") for fmt in formats)
    if not keys or len(set(keys)) != len(keys) or any(k not in _WRITERS for k in keys):
        raise ValueError("Choose one or more distinct supported heat map formats")
    stem = sanitize_filename_base(stem)
    if not stem:
        raise OSError("Enter a file name")
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    # Finish every writer before replacing any previous export. Replacement
    # itself is per file; report a partial commit if the destination changes.
    try:
        with TemporaryDirectory(prefix=".tapesift-heatmap-", dir=folder) as staging:
            staged = [export_heatmap(page, Path(staging) / name, fmt, **kwargs)
                      for page, name, fmt in export_variants(data, stem, keys)]
            for source in staged:
                target = folder / source.name
                os.replace(source, target)
                written.append(target)
    except OSError as error:
        completed = ", ".join(str(p) for p in written) or "none"
        raise OSError(f"Export could not finish. Files already written: {completed}. "
                      f"{error}") from error
    return written
