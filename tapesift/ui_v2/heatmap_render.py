"""Paint the game heat map onto any device, so every export agrees.

One painter serves the picture, the printed page and the vector file. A
PDF that disagreed with the PNG next to it would make both untrustworthy,
and the only way to guarantee they agree is to draw them the same way.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen

from tapesift.services.heatmap_service import (
    HeatmapData, HeatmapPlay, LAYERS, TYPE_COLOURS, C_NO_PLAYER)
from tapesift.services.result_service import result_key

BG = "#101109"
INK = "#eceee7"
DIM = "#6d7568"
LINE = "#26281f"
TRACK = "#1b1d16"

MARGIN = 40
RIBBON_H = 46
QUARTER_ROW_H = 32
PLAYER_ROW_H = 26
#: Below this a colour cell stops being a colour and becomes a hairline.
MIN_CELL_W = 1.4
MIN_MARK_W = 2.4
#: Title block above the first ribbon, and the note below the last row.
HEADER_H = 128
FOOTER_H = 44


def _font(size: int, *, mono: bool = False,
          weight: QFont.Weight = QFont.Weight.DemiBold) -> QFont:
    font = QFont("Consolas" if mono else "Rajdhani", size)
    font.setWeight(weight)
    return font


def _text(painter: QPainter, x: float, y: float, value: str, colour: str,
          size: int, *, mono: bool = False,
          weight: QFont.Weight = QFont.Weight.DemiBold) -> None:
    painter.setFont(_font(size, mono=mono, weight=weight))
    painter.setPen(QColor(colour))
    painter.drawText(int(x), int(y), value)


def _rule(painter: QPainter, x0: float, x1: float, y: float,
          colour: str = LINE) -> None:
    painter.setPen(QPen(QColor(colour), 1))
    painter.drawLine(int(x0), int(y), int(x1), int(y))


def _ribbon(painter: QPainter, x: float, y: float, width: float,
            height: float, plays: tuple[HeatmapPlay, ...],
            *, gap: float = 1.2, radius: float = 2.0) -> None:
    """One cell per play, in order. This is the texture people read."""
    if not plays:
        return
    cell = (width - gap * (len(plays) - 1)) / len(plays)
    if cell < MIN_CELL_W:
        # Past this density the gaps eat the colour, so drop them and let
        # the plays read as a continuous band rather than grey noise.
        cell, gap = width / len(plays), 0.0
        radius = 0.0
    painter.setPen(Qt.PenStyle.NoPen)
    for index, play in enumerate(plays):
        painter.setBrush(QColor(play.colour))
        painter.drawRoundedRect(
            QRectF(x + index * (cell + gap), y, max(cell, 0.6), height),
            radius, radius)


def heatmap_height(data: HeatmapData, *, width: int = 1400,
                   players: int | None = None) -> int:
    """How tall the page needs to be, before anything is drawn."""
    if data.report_template:
        from tapesift.ui_v3.heatmap_reports import paint_report
        return paint_report(None, data, width)
    if data.layered:
        return paint_layered_heatmap(None, data, width, players=players)
    shown = len(data.players) if players is None else min(
        players, len(data.players))
    # Must equal what paint_heatmap actually consumes, or every export
    # carries a band of dead page at the bottom.
    return int(
        HEADER_H + 22 + RIBBON_H + 32
        + 14 + QUARTER_ROW_H * len(data.quarters)
        + 26 + 16 + PLAYER_ROW_H * shown
        + FOOTER_H)


def paint_heatmap(painter: QPainter, data: HeatmapData, width: int,
                  *, players: int | None = None) -> int:
    """Draw the whole page and return the height used."""
    if data.report_template:
        from tapesift.ui_v3.heatmap_reports import paint_report
        return paint_report(painter, data, width)
    if data.layered:
        return paint_layered_heatmap(painter, data, width, players=players)
    ranked = list(data.players)
    if players is not None:
        ranked = ranked[:players]
    left, right = MARGIN, width - MARGIN

    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.fillRect(
        QRectF(0, 0, width, heatmap_height(data, players=players)),
        QColor(BG))

    _text(painter, left, 52, data.title.upper(), INK, 26,
          weight=QFont.Weight.Bold)
    if data.subtitle:
        _text(painter, left, 74, data.subtitle.upper(), DIM, 11, mono=True)
    _rule(painter, left, right, 92)

    y = HEADER_H
    _text(painter, left, y, "FULL GAME", DIM, 11, mono=True)
    y += 22
    _ribbon(painter, left, y, right - left, RIBBON_H, data.plays,
            gap=1.2, radius=2)

    # Quarter breaks are cut into the ribbon rather than drawn under it,
    # so the eye reads one band split four ways, not five objects.
    total = len(data.plays)
    if total:
        cell = (right - left - 1.2 * (total - 1)) / total
        seen = 0
        _text(painter, left + 2, y - 5, data.quarters[0] if data.quarters
              else "", DIM, 9, mono=True)
        for quarter in data.quarters[:-1]:
            seen += data.quarter_counts.get(quarter, 0)
            x = left + seen * (cell + 1.2) - 1.2
            painter.setPen(QPen(QColor(BG), 3))
            painter.drawLine(int(x), int(y - 2), int(x), int(y + RIBBON_H + 2))
            nxt = data.quarters[data.quarters.index(quarter) + 1]
            _text(painter, x + 6, y - 5, nxt, DIM, 9, mono=True)
    y += RIBBON_H + 32

    _text(painter, left, y, "BY QUARTER", DIM, 11, mono=True)
    y += 14
    for quarter in data.quarters:
        plays = data.plays_in(quarter)
        _text(painter, left, y + 18, quarter, INK, 15)
        _text(painter, left + 30, y + 18, f"{len(plays):>3}", DIM, 11,
              mono=True)
        _ribbon(painter, left + 62, y + 4, right - left - 62, 20, plays,
                gap=1.2, radius=2)
        y += QUARTER_ROW_H
    y += 26

    _text(painter, left, y, "WHO CARRIED IT, AND WHEN", DIM, 11, mono=True)
    y += 16
    if ranked:
        peak = ranked[0].snaps or 1
        bar_x, bar_w = left + 210, 120
        strip_x = bar_x + bar_w + 24
        strip_w = right - strip_x
        for index in range(1, max(1, len(data.quarters))):
            x = strip_x + strip_w * index / len(data.quarters)
            painter.setPen(QPen(QColor(LINE), 1))
            painter.drawLine(
                int(x), int(y - 4), int(x), int(y + len(ranked) * PLAYER_ROW_H))
        for player in ranked:
            colour = QColor(player.colour)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawRoundedRect(QRectF(left, y + 4, 10, 10), 2, 2)
            _text(painter, left + 20, y + 14, player.name, INK, 14)
            _text(painter, bar_x - 34, y + 14, f"{player.snaps:>2}", DIM, 11,
                  mono=True)
            painter.setBrush(QColor(TRACK))
            painter.drawRect(QRectF(bar_x, y + 5, bar_w, 8))
            painter.setBrush(colour)
            painter.drawRect(
                QRectF(bar_x, y + 5, bar_w * player.snaps / peak, 8))
            # Their snaps on the game's own axis: the "and when".
            for position in player.positions:
                painter.setBrush(colour)
                painter.drawRoundedRect(
                    QRectF(strip_x + strip_w * position, y + 3,
                           MIN_MARK_W, 12), 1, 1)
            y += PLAYER_ROW_H
    _rule(painter, left, right, y + 8)
    note = ("EACH CELL IS ONE PLAY, IN ORDER.  COLOUR IS THE PRIMARY PLAYER."
            f"  {data.unassigned} UNASSIGNED.")
    _text(painter, left, y + 26, note, DIM, 10, mono=True)
    return int(y + 44)


def render_image(data: HeatmapData, *, width: int = 1400,
                 scale: float = 1.0, players: int | None = None) -> QImage:
    """The page as pixels, at whatever scale the export asked for."""
    height = heatmap_height(data, width=width, players=players)
    image = QImage(
        int(width * scale), int(height * scale),
        QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(1.0)
    image.fill(QColor(BG))
    painter = QPainter(image)
    if scale != 1.0:
        painter.scale(scale, scale)
    try:
        paint_heatmap(painter, data, width, players=players)
    finally:
        painter.end()
    return image


V3_BG = "#0a0f11"
V3_INK = "#dee3df"
V3_DIM = "#9ba6a0"
V3_LINE = "#28322f"
V3_EMPTY = "#28302e"


def _v3_font(size: int = 14, bold: bool = False) -> QFont:
    font = QFont("Segoe UI")
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def _v3_text(painter, rect, text, *, size=14, colour=V3_INK, bold=False):
    font = _v3_font(size, bold)
    flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
    height = max(size + 6, QFontMetrics(font).boundingRect(
        QRectF(rect).toRect(), flags, text).height())
    if painter is not None:
        painter.setFont(font)
        painter.setPen(QColor(colour))
        painter.drawText(QRectF(rect), flags, text)
    return height


def layer_legend(data: HeatmapData, layer: str):
    """All categories, including unresolved values, from this same snapshot."""
    if layer == "run_pass":
        colours = {p.type_category: p.type_colour for p in data.plays}
        return [(key, count, colours.get(key, TYPE_COLOURS[key]))
                for key, count in data.type_counts.items()
                if count or key != "Unresolved"]
    if layer == "players":
        return [(p.name, p.snaps, p.colour) for p in data.players] + [
            ("Unassigned", data.unassigned, next((p.colour for p in data.plays if not p.player_key), C_NO_PLAYER))]
    colours, names = {}, {}
    for play in data.plays:
        for value, colour in zip(play.results, play.result_colours):
            key = result_key(value)
            names.setdefault(key, value)
            colours[key] = colour
    return [(names[key], count, colours[key]) for key, count in data.result_counts.items()] + [
        ("Unlogged", sum(not p.results for p in data.plays), V3_EMPTY)]


def _v3_cells(painter, plays, layer, rect):
    if painter is None or not plays:
        return
    cell = rect.width() / len(plays)
    gap = 2 if cell >= 5 else 0
    painter.setPen(Qt.PenStyle.NoPen)
    for i, play in enumerate(plays):
        colours = ((play.type_colour,) if layer == "run_pass" else
                   (play.colour,) if layer == "players" else
                   play.result_colours or (V3_EMPTY,))
        for j, colour in enumerate(colours):
            painter.fillRect(QRectF(rect.x() + i * cell, rect.y() + j * rect.height() / len(colours),
                                   cell - gap, rect.height() / len(colours)), QColor(colour))


def _v3_legend(painter, items, x, y, width, *, inline=False):
    origin, row_top, row_h = x, y, 0
    for label, count, colour in items:
        text = f"{label} {count}"
        needed = QFontMetrics(_v3_font(12 if inline else 14)).horizontalAdvance(text) + (22 if inline else 30)
        used_w = min(width, needed) if inline else width
        if inline and x > origin and x + used_w > origin + width:
            x, y, row_h = origin, y + row_h + 8, 0
        height = _v3_text(None, QRectF(0, 0, used_w - (16 if inline else 24), 10000), text,
                          size=12 if inline else 14)
        if painter is not None:
            painter.fillRect(QRectF(x, y + 3, 12, 12), QColor(colour))
            painter.setPen(QPen(QColor("#75847a"), 1))
            painter.drawRect(QRectF(x, y + 3, 12, 12))
            inset = 16 if inline else 22
            _v3_text(painter, QRectF(x + inset, y, used_w - inset, height), text,
                     size=12 if inline else 14)
        if inline:
            x += used_w
            row_h = max(row_h, height)
        else:
            y += max(28, height + 8)
    return y + row_h - row_top if inline else y - row_top


def paint_layered_heatmap(painter, data: HeatmapData, width: int, *,
                          players=None, header=True, layout=None) -> int:
    """The locked three-lane chart; measure and paint follow the same layout."""
    left, right = 28, width - 28
    content = right - left
    if painter is not None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(QRectF(0, 0, width, paint_layered_heatmap(
            None, data, width, players=players, header=header)), QColor(V3_BG))
    def text(x, y, w, value, **kwargs):
        return _v3_text(painter, QRectF(x, y, w, 10000), value, **kwargs)
    def rule(y):
        if painter is not None:
            _rule(painter, left, right, y, V3_LINE)
    y = 18
    if header:
        y += text(left, y, content, data.title, size=22, bold=True) + 6
        y += text(left, y, content, data.subtitle, colour=V3_DIM) + 18
        rule(y)
        y += 24
    ribbon_x, ribbon_w = left + 103, content - 103
    # Film order may return to an earlier quarter. Mark actual transitions,
    # never cumulative quarter totals masquerading as contiguous boundaries.
    runs = []
    for i, play in enumerate(data.plays):
        if not runs or runs[-1][0] != play.quarter or play.index != data.plays[i-1].index + 1:
            runs.append([play.quarter, i, i + 1])
        else:
            runs[-1][2] = i + 1
    if layout is not None:
        layout["ribbons"] = []
    for quarter, start, end in runs:
        x = ribbon_x + ribbon_w * start / len(data.plays)
        w = ribbon_w * (end - start) / len(data.plays)
        if w >= 38:
            first, last = data.plays[start].index + 1, data.plays[end-1].index + 1
            span = str(first) if first == last else f"{first}–{last}"
            label = f"{quarter} ({span})" if w > 130 else quarter
            text(x + 2, y, w - 4, label, size=12, colour=V3_DIM)
    text(left + 62, y + 27, 38, "Clip", size=12, colour=V3_DIM)
    if data.plays:
        text(ribbon_x, y + 27, 80, str(data.plays[0].index + 1), size=12, colour=V3_DIM)
        text(right - 38, y + 27, 38, str(data.plays[-1].index + 1), size=12, colour=V3_DIM)
    y += 54
    for layer in data.layers:
        text(left, y + 9, 99, LAYERS[layer], size=13)
        rect = QRectF(ribbon_x, y, ribbon_w, 40)
        _v3_cells(painter, data.plays, layer, rect)
        if layout is not None:
            layout["ribbons"].append((layer, rect))
        y += 44
    if not data.layers or not data.plays:
        text(ribbon_x, y + 4, ribbon_w, "No visible layers selected" if not data.layers else
             "No enabled saved clips in this view", colour=V3_DIM)
        y += 36
    y += 12
    legend_bottom = y
    if data.layers:
        col_w = min(230, (ribbon_w - 30 * (len(data.layers) - 1)) / len(data.layers))
        for i, layer in enumerate(data.layers):
            x = ribbon_x + i * (col_w + 30)
            heading_h = text(x, y, col_w, LAYERS[layer], bold=True)
            height = _v3_legend(painter, layer_legend(data, layer), x, y + heading_h + 10, col_w)
            bottom = y + heading_h + 10 + height
            if layer == "results":
                bottom += text(x, bottom + 4, col_w,
                               f"Outcomes can overlap · {data.turnover_count} turnover clips", size=12, colour=V3_DIM) + 4
            legend_bottom = max(legend_bottom, bottom)
        if painter is not None:
            painter.setPen(QPen(QColor(V3_LINE), 1))
            for i in range(1, len(data.layers)):
                x = ribbon_x + i * (col_w + 30) - 40
                painter.drawLine(int(x), int(y + 20), int(x), int(legend_bottom))
    y = legend_bottom + 12
    rule(y)
    y += 22
    if data.layers:
        text(left, y, 224, "BY QUARTER (selected layer)", size=13, colour=V3_DIM)
        text(left + 232, y, 220, LAYERS[data.quarter_layer], size=13, colour="#69b98c")
        if layout is not None:
            layout["quarter_header"] = y
        y += 40
        columns = 4 if width >= 1100 else 2
        card_w = (content - 24 * (columns - 1)) / columns
        for offset in range(0, len(data.quarters), columns):
            row_h = 0
            for col, quarter in enumerate(data.quarters[offset:offset + columns]):
                x = left + col * (card_w + 24)
                plays_in = data.plays_in(quarter)
                text(x, y, card_w, f"{quarter} · {len(plays_in)} clips", bold=True)
                _v3_cells(painter, plays_in, data.quarter_layer, QRectF(x, y + 30, card_w, 38))
                # The projection keeps the original player's colour ranking.
                from tapesift.services.heatmap_service import select_heatmap
                quarter_data = select_heatmap(data, quarter=quarter)
                h = _v3_legend(painter, layer_legend(quarter_data, data.quarter_layer),
                               x, y + 82, card_w, inline=True)
                row_h = max(row_h, 82 + h + 26)
            y += row_h
        rule(y)
        y += 24
    elif layout is not None:
        layout.pop("quarter_header", None)
    if "players" in data.layers:
        text(left, y, content, "PRIMARY PLAYER ASSIGNMENTS (logged primary player)", size=13, colour=V3_DIM)
        y += 38
        ranked = layer_legend(data, "players")
        if players is not None:
            ranked = ranked[:players]
        peak = max((count for _, count, _ in ranked), default=1) or 1
        bar_x = left + 150
        bar_w = max(60, content - 220)
        for label, count, colour in ranked:
            if painter is not None:
                painter.fillRect(QRectF(left, y + 4, 14, 14), QColor(colour))
            short = QFontMetrics(_v3_font()).elidedText(label, Qt.TextElideMode.ElideRight, 116)
            text(left + 24, y, 122, short)
            if painter is not None:
                painter.fillRect(QRectF(bar_x, y + 6, bar_w * count / peak, 11), QColor(colour))
            text(bar_x + bar_w * count / peak + 14, y, 50, str(count))
            y += 31
        rule(y + 8)
        y += 26
    note = data.scope or "Each cell is one saved clip in film order. Primary player counts are logged assignments."
    y += text(left, y, content, note, size=12, colour=V3_DIM) + 24
    return int(y)
