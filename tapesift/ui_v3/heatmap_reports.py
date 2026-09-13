"""Seven saved-clip reports. One measured painter serves preview, PNG, SVG and PDF."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPen
from PySide6.QtSvg import QSvgRenderer

from tapesift.services.football_context import parse_ball, parse_down_distance
from tapesift.services.heatmap_palette import BACKGROUND, EMPTY, RESULT_COLORS
from tapesift.services.heatmap_service import TYPE_COLOURS
from tapesift.services.result_service import result_key

TEMPLATES = {
    "game_fingerprint": "Game Fingerprint",
    "player_spotlight": "Player Spotlight",
    "quarter_story": "Quarter Story",
    "situation": "Situation at a Glance",
    "players": "Who the Film Features",
    "result_pulse": "Result Pulse",
    "field_position": "Where Plays Start",
}
INK, DIM, LINE = "#e2e6de", "#9da99f", "#28382e"


@dataclass(frozen=True)
class Section:
    title: str
    columns: tuple[str, ...]
    # Every row carries its own identity color; shade only encodes count.
    rows: tuple[tuple[str, str, tuple[int, ...]], ...]
    kind: str = "matrix"
    note: str = ""


def _players(data, plays=None, *, quarters=True, title="Primary player by quarter"):
    plays = data.plays if plays is None else plays
    columns = data.quarters if quarters else ("Clips",)
    rows = []
    for player in data.players:
        selected = [p for p in plays if p.player_key == player.key]
        if not selected:
            continue
        values = tuple(sum(p.quarter == q for p in selected) for q in columns) if quarters else (len(selected),)
        rows.append((player.name, player.colour, values))
    unassigned = [p for p in plays if not p.player_key]
    if unassigned:
        values = tuple(sum(p.quarter == q for p in unassigned) for q in columns) if quarters else (len(unassigned),)
        rows.append(("Unassigned", "#77817e", values))
    return Section(title, tuple(columns), tuple(rows), "matrix" if quarters else "bars",
                   "Counts are primary-player clip assignments, not touches. Shade: fewer → more clips.")


def _results(data, *, quarters=True):
    labels, colors = {}, {}
    for play in data.plays:
        for value, color in zip(play.results, play.result_colours):
            key = result_key(value)
            labels.setdefault(key, value)
            colors.setdefault(key, color)
    columns = data.quarters if quarters else ("Clips",)
    rows = []
    for key in sorted(labels, key=lambda k: (-sum(k in {result_key(v) for v in p.results}
                                                     for p in data.plays), k)):
        selected = [p for p in data.plays if key in {result_key(v) for v in p.results}]
        values = tuple(sum(p.quarter == q for p in selected) for q in columns) if quarters else (len(selected),)
        rows.append((labels[key], colors[key], values))
    return Section("Saved results by quarter" if quarters else "Saved result tags", tuple(columns), tuple(rows),
                   "matrix" if quarters else "bars",
                   f"A clip may have multiple results. {sum(not p.results for p in data.plays)} clips have no result logged.")


def _mix(data, groups, title):
    types = [key for key, count in data.type_counts.items() if count]
    # Keep unknown/custom types visible; never fold them into Run or Pass.
    rows = tuple((label, "#5d8eac", tuple(sum(p.type_category == key for p in plays) for key in types))
                 for label, plays in groups)
    return Section(title, tuple(types), rows, "mix")


def field_zone(ball_on):
    side, yard = parse_ball(ball_on)
    if yard is None or not side:
        return None
    position = 100 - yard if side == "OPP" else yard
    if not 0 < position < 100:
        return None
    return min(4, int((position - 1) // 20))


def report_sections(data):
    """Project recorded values only. No success, routes, possession or gain inference."""
    key = data.report_template
    if key not in TEMPLATES:
        raise ValueError("Unknown heat map template")
    by_quarter = [(q if q != "?" else "Unknown", data.plays_in(q)) for q in data.quarters]
    if key == "game_fingerprint":
        summary = Section("Saved result summary", (), (
            ("First downs", RESULT_COLORS["first_down"], (sum(any(result_key(v) == "first down" for v in p.results) for p in data.plays),)),
            ("Touchdowns", RESULT_COLORS["score"], (sum(any(result_key(v) == "touchdown" for v in p.results) for p in data.plays),)),
            ("Turnovers", RESULT_COLORS["turnover"], (data.turnover_count,)),
        ), "summary", "Result tags can overlap. See Result Pulse for every logged result.")
        return [_mix(data, by_quarter, "Quarter play mix"), _players(data), summary]
    if key == "player_spotlight":
        return [_players(data, title="Primary-player clips by quarter"), _results(data, quarters=False)]
    if key == "quarter_story":
        return [_mix(data, by_quarter, "How the game was logged"), _results(data), _players(data)]
    if key == "players":
        return [_players(data, quarters=False, title="Who the film features"), _players(data)]
    if key == "result_pulse":
        first_down = [p for p in data.plays if any(result_key(v) == "first down" for v in p.results)]
        return [_results(data), _players(data, first_down, quarters=False,
                    title="Primary-player clips with a first-down tag")]
    if key == "situation":
        downs = [(d, [p for p in data.plays if parse_down_distance(p.down_distance)[0] == str(i)])
                 for i, d in enumerate(("1st", "2nd", "3rd", "4th"), 1)]
        unknown = [p for p in data.plays if not parse_down_distance(p.down_distance)[0]]
        if unknown:
            downs.append(("Unknown", unknown))
        distances = Counter()
        for play in data.plays:
            distance = parse_down_distance(play.down_distance)[1]
            if distance.isdigit():
                number = int(distance)
                band = "1–3 yd" if 1 <= number <= 3 else "4–6 yd" if 4 <= number <= 6 else "7–10 yd" if 7 <= number <= 10 else "11+ yd" if number > 10 else "Unknown"
            else:
                band = distance if distance in {"Goal", "Inches"} else "Unknown"
            distances[band] += 1
        distance_section = Section("Distance to gain before the play", ("Clips",), tuple(
            (label, "#778d80", (distances[label],)) for label in
            ("1–3 yd", "4–6 yd", "7–10 yd", "11+ yd", "Goal", "Inches", "Unknown") if distances[label]), "bars")
        return [_mix(data, downs, "Down × play type"), distance_section,
                _players(data, downs[2][1], quarters=False, title="Primary-player clips on third down")]
    groups = [(label, [p for p in data.plays if field_zone(p.ball_on) == i])
              for i, label in enumerate(("Own 1–20", "Own 21–40", "Midfield 41–Opp 40", "Opp 39–20", "Opp 19–Goal"))]
    unknown = sum(field_zone(p.ball_on) is None for p in data.plays)
    field = Section("Recorded starting positions", (), tuple((label, "#46976e", (len(plays),))
                    for label, plays in groups), "field",
                    f"{sum(len(p) for _, p in groups)} positioned clips · {unknown} unknown or ambiguous. No inferred tracking.")
    return [field, _mix(data, groups, "Play type by starting zone"),
            _players(data, groups[-1][1], quarters=False, title="Inside the opponent 20")]


@lru_cache(maxsize=1)
def _logo():
    return QSvgRenderer(str(Path(__file__).resolve().parents[1] / "resources/icons/ifi-horizontal-white.svg"))


def _font(size, bold=False):
    font = QFont("Segoe UI")
    font.setPixelSize(size)
    font.setBold(bold)
    return font


def _shade(color, count, peak):
    if not count:
        return QColor(EMPTY)
    return QColor(color).darker(round(290 - 150 * count / max(1, peak)))


class _Report:
    def __init__(self, painter, width, portrait):
        self.painter, self.width, self.portrait = painter, width, portrait
        self.margin = 28 if portrait else 42

    def text(self, x, y, width, text, *, size=16, color=INK, bold=False):
        font = _font(size, bold)
        rect = QFontMetrics(font).boundingRect(0, 0, max(1, int(width)), 10000,
                                              int(Qt.TextFlag.TextWordWrap), str(text))
        height = max(size + 6, rect.height() + 4)
        if self.painter:
            self.painter.setFont(font)
            self.painter.setPen(QColor(color))
            self.painter.drawText(QRectF(x, y, width, height), int(Qt.TextFlag.TextWordWrap), str(text))
        return height

    def rule(self, x, y, width):
        if self.painter:
            self.painter.fillRect(QRectF(x, y, width, 1), QColor(LINE))

    def cell(self, rect, color, value, peak):
        if not self.painter:
            return
        shade = _shade(color, value, peak)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0, shade.lighter(110))
        gradient.setColorAt(1, shade)
        self.painter.fillRect(rect, gradient)
        self.painter.fillRect(QRectF(rect.x(), rect.y(), rect.width(), 1), shade.lighter(135))
        self.painter.setFont(_font(17))
        self.painter.setPen(QColor(INK if value else DIM))
        self.painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(value))

    def section(self, section, x, y, width, data):
        self.rule(x, y, width)
        y += 19
        y += self.text(x, y, width, section.title.upper(), size=17, bold=True) + 15
        if not section.rows or not section.columns and section.kind not in {"field", "summary"}:
            y += self.text(x, y, width, "No matching details logged in this view.", color=DIM) + 15
        elif section.kind == "field":
            height = 380 if self.portrait else 175
            counts = [row[2][0] for row in section.rows]
            peak = max(counts, default=1)
            y += self.text(x, y, width, "OWN GOAL  ↓  OPPONENT GOAL" if self.portrait else "OWN GOAL  →  OPPONENT GOAL", size=13, color=DIM) + 10
            for i, (label, color, values) in enumerate(section.rows):
                rect = QRectF(x, y + height * i / 5, width, height / 5 - 1) if self.portrait else QRectF(x + width * i / 5, y, width / 5 - 1, height)
                self.cell(rect, color, values[0], peak)
                self.text(rect.x()+10, rect.y()+10, rect.width()-20, label, size=13)
                if self.painter:
                    self.painter.setPen(QPen(QColor("#829879"), 1))
                    for tick in range(1, 6):
                        tx = rect.x() + rect.width() * tick / 6
                        self.painter.drawLine(int(tx), int(rect.bottom()-7), int(tx), int(rect.bottom()-12))
            y += height + 16
        elif section.kind == "summary":
            for i, (label, color, values) in enumerate(section.rows):
                cw = width / len(section.rows)
                xx = x + i*cw
                self.text(xx, y, cw-10, str(values[0]), size=32, bold=True)
                self.text(xx, y+42, cw-10, label.upper(), size=13, color=DIM)
                if self.painter:
                    self.painter.fillRect(QRectF(xx, y+68, min(100, cw-16), 3), QColor(color))
            y += 94
        elif section.kind == "mix":
            colors = [next((p.type_colour for p in data.plays if p.type_category == category), TYPE_COLOURS[category])
                      for category in section.columns]
            legend_y = y
            for i, (label, color) in enumerate(zip(section.columns, colors)):
                cw = width / len(colors)
                if self.painter:
                    self.painter.fillRect(QRectF(x+i*cw, legend_y+4, 8, 10), QColor(color))
                self.text(x+i*cw+15, legend_y, cw-20, label, size=13, color=DIM)
            y += 34
            peak = max((sum(values) for _, _, values in section.rows), default=1)
            label_width = min(170, width*.24)
            for label, _, values in section.rows:
                h = max(36, self.text(x, y+7, label_width-12, label, size=14)+12)
                xx = x+label_width
                room = width-label_width-38
                for count, color in zip(values, colors):
                    if count:
                        cw = room * count / max(1, peak)
                        self.cell(QRectF(xx, y, max(1, cw-1), h-3), color, count, count)
                        xx += cw
                self.text(x+width-29, y+7, 29, str(sum(values)), size=14)
                y += h+7
            y += 14
        elif section.kind == "bars":
            peak = max((r[2][0] for r in section.rows), default=1)
            label_width = min(230, width * .40)
            for label, color, values in section.rows:
                count = values[0]
                h = max(34, self.text(x+18, y, label_width-22, label, size=15))
                if self.painter:
                    self.painter.fillRect(QRectF(x, y+4, 8, 12), QColor(color))
                    self.painter.fillRect(QRectF(x+label_width, y+6, (width-label_width-46)*count/max(peak, 1), 13), _shade(color, count, peak))
                self.text(x+width-38, y, 38, str(count), size=17)
                y += h+8
        else:
            # Repeat labels when a long overtime game needs another group of columns.
            group_size = 4 if self.portrait or width < 700 else 6
            peak = max((v for _, _, vals in section.rows for v in vals), default=1)
            label_width = min(225, width * .35)
            for offset in range(0, len(section.columns), group_size):
                columns = section.columns[offset:offset+group_size]
                cw = (width-label_width) / len(columns)
                header_height = 26
                for i, column in enumerate(columns):
                    header_height = max(header_height, self.text(x+label_width+i*cw+4, y, cw-8,
                                            "Unknown" if column == "?" else column, size=14, color=DIM))
                y += header_height + 6
                for label, color, values in section.rows:
                    h = max(36, self.text(x+17, y+8, label_width-24, label, size=14) + 14)
                    if self.painter:
                        self.painter.fillRect(QRectF(x, y+12, 8, 10), QColor(color))
                    for i, value in enumerate(values[offset:offset+group_size]):
                        self.cell(QRectF(x+label_width+i*cw, y, cw-2, h-2), color, value, peak)
                    y += h+3
                y += 14
        if section.note:
            y += self.text(x, y, width, section.note, size=13, color=DIM) + 10
        return y+18

    def draw(self, data, sections):
        x, width = self.margin, self.width - 2*self.margin
        y = 30
        logo_width = min(250, width * .44)
        logo_height = logo_width / 3.9
        if self.painter:
            _logo().render(self.painter, QRectF(x, y, logo_width, logo_height))
        wide_header = not self.portrait and self.width >= 1100
        if wide_header:
            header_x, header_width = x+logo_width+40, width-logo_width-40
            top = y
            y += self.text(header_x, y, header_width, TEMPLATES[data.report_template].upper(), size=28, bold=True) + 7
            y += self.text(header_x, y, header_width, data.title.removesuffix(" — Game Heat Map"), size=17, color=DIM) + 8
            y = max(y, top+logo_height)+15
        else:
            y += logo_height + 24
            y += self.text(x, y, width, TEMPLATES[data.report_template].upper(), size=28, bold=True) + 7
            y += self.text(x, y, width, data.title.removesuffix(" — Game Heat Map"), size=17, color=DIM) + 8
        if data.report_template == "player_spotlight":
            name = data.report_player_name or next(
                (p.name for p in data.players if p.key == data.report_player), "Choose a player")
            y += self.text(x, y, width, name, size=25, bold=True) + 8
        y += self.text(x, y, width, f"{data.play_count} CLIPS    {data.type_counts['Pass']} PASS    {data.type_counts['Run']} RUN", size=22, bold=True) + 8
        extra = data.play_count - data.type_counts['Pass'] - data.type_counts['Run']
        if extra:
            y += self.text(x, y, width, f"{extra} clips have other or unlogged play types.", size=13, color=DIM) + 6
        y += self.text(x, y, width, f"{data.view} · saved snapshot", size=13, color=DIM) + 20
        if not self.portrait and data.report_template not in {"field_position", "result_pulse"}:
            half = (width-36)/2
            for offset in range(0, len(sections), 2):
                pair = sections[offset:offset+2]
                if len(pair) == 1:
                    y = self.section(pair[0], x, y, width, data)
                else:
                    y = max(self.section(s, x+i*(half+36), y, half, data) for i, s in enumerate(pair))
        else:
            for section in sections:
                y = self.section(section, x, y, width, data)
        self.rule(x, y, width)
        y += 18
        scope = data.scope or "Saved clip rows; versions count separately."
        y += self.text(x, y, width, scope, size=12, color=DIM) + 12
        y += self.text(x, y, width, "Created with TapeSift · Independent Football Intelligence", size=13, color=DIM) + 28
        return max(int(y), int(self.width * (1.55 if self.portrait else .66)))


def paint_report(painter, data, width):
    from tapesift.ui_v3.weekly_social import SOCIAL_TEMPLATES, paint_social
    if data.report_template in SOCIAL_TEMPLATES:
        return paint_social(painter, data, width)
    if data.report_orientation not in {"portrait", "landscape"}:
        raise ValueError("Unknown report orientation")
    sections = report_sections(data)
    portrait = data.report_orientation == "portrait"
    height = _Report(None, width, portrait).draw(data, sections)
    if painter:
        painter.save()
        try:
            painter.setRenderHint(painter.RenderHint.Antialiasing)
            painter.fillRect(QRectF(0, 0, width, height), QColor(BACKGROUND))
            _Report(painter, width, portrait).draw(data, sections)
        finally:
            painter.restore()
    return height
