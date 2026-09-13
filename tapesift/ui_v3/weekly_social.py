"""The standard weekly post and three-slide story, painted from saved clips."""
from collections import Counter
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage

from tapesift.services.result_service import legacy_result_key
from tapesift.ui_v3.heatmap_reports import _logo

SOCIAL_TEMPLATES = {"weekly_summary": "Weekly film breakdown · single post",
                    "weekly_carousel": "Weekly film breakdown · 3-slide carousel"}
SOCIAL_FIELDS = {"headline": ("Matchup / headline", 100),
                 "film_note": ("The film note", 240),
                 "player_note": ("Player to watch", 240),
                 "takeaway_1": ("Takeaway 1", 220),
                 "takeaway_2": ("Takeaway 2", 220),
                 "takeaway_3": ("Takeaway 3", 220)}
INK, GREEN, BLUE, GOLD, DIM = "#e8e4d7", "#4e9d68", "#639fc1", "#d3a72f", "#abb1a8"
RESOURCES = Path(__file__).resolve().parents[1] / "resources"


@lru_cache(maxsize=1)
def _assets():
    for name in ("Rajdhani-Bold.ttf", "IBMPlexSans-Regular.ttf"):
        QFontDatabase.addApplicationFont(str(RESOURCES / "fonts" / name))
    return QImage(str(RESOURCES / "images" / "weekly-social-paper.png"))


def tagged_count(data, key):
    return sum(any(legacy_result_key(v) == key for v in p.results) for p in data.plays)


class _Post:
    def __init__(self, painter, data):
        self.p = painter
        self.data = data
        self.copy = dict(data.social_text)

    def text(self, x, y, w, h, value, size=30, color=INK, *, display=False, center=False, editorial=False):
        if value is None or value == "":
            return
        value = str(value)
        font = QFont(("Impact" if size >= 70 else "Rajdhani") if display else "Georgia" if editorial else "IBM Plex Sans")
        font.setBold(display and size < 70)
        font.setItalic(editorial)
        if display:
            while True:
                font.setPixelSize(size)
                bounds = QFontMetricsF(font).tightBoundingRect(str(value))
                if (bounds.width() <= w and bounds.height() <= h) or size <= 16:
                    break
                size -= 1
            self.p.setFont(font)
            self.p.setPen(QColor(color))
            if bounds.width() > w:
                value = QFontMetricsF(font).elidedText(str(value), Qt.TextElideMode.ElideRight, max(0, w))
            bounds = QFontMetricsF(font).tightBoundingRect(value)
            self.p.drawText(QPointF(x+(w-bounds.width())/2 if center else x,
                                   y-bounds.y()), str(value))
            return
        flags = Qt.TextFlag.TextWordWrap | (Qt.AlignmentFlag.AlignHCenter if center else Qt.AlignmentFlag.AlignLeft)
        # Fit user-authored copy rather than clipping it or changing export size.
        while True:
            font.setPixelSize(size)
            measured = QFontMetricsF(font).boundingRect(QRectF(0, 0, w, 10000), flags, str(value))
            if (measured.height() <= h and measured.width() <= w) or size <= 16:
                break
            size -= 1
        self.p.setFont(font)
        self.p.setPen(QColor(color))
        self.p.drawText(QRectF(x, y, w, h), flags, str(value))

    def rule(self, y, x=48, w=984):
        self.p.fillRect(QRectF(x, y, w, 1), QColor("#35543d"))

    def header(self, slide):
        _logo().render(self.p, QRectF(375, 40, 330, 85))
        if slide:
            self.text(916, 53, 115, 50, f"0{slide} / 03", 30, GREEN, display=True)
        matchup = self.copy.get("headline") or self.data.title.removesuffix(" — Game Heat Map")
        if slide:
            self.text(48, 151, 984, 48, matchup.upper(), 32, GREEN, display=True, center=True)
            title = ("THE GAME AT A GLANCE", "WHO THE FILM FEATURES", "MY FILM TAKEAWAYS")[slide-1]
            self.text(48, 215, 984, 115, title, 96, display=True, center=True)
        else:
            self.text(48, 151, 984, 40, "WEEKLY FILM BREAKDOWN", 34, GREEN, display=True, center=True)
            self.text(48, 210, 984, 120, matchup.upper(), 130, display=True, center=True)
        self.rule(347)

    def totals(self, y, hero=False):
        counts = self.data.type_counts
        remaining = [(k, v) for k, v in counts.items() if k not in ("Run", "Pass") and v]
        if hero:
            self.text(295, y, 220, 104, self.data.play_count, 116, GREEN, display=True, center=True)
            self.text(535, y+40, 270, 60, "CLIPS", 54, display=True)
            remainder = (f"{sum(v for _,v in remaining)} OTHER / UNLOGGED" if len(remaining) > 1
                         else " · ".join(f"{v} {k.upper()}" for k,v in remaining) or "ALL TYPES LOGGED")
            for i, (text, color) in enumerate(((f"{counts['Pass']} PASS", BLUE), (f"{counts['Run']} RUN", GREEN),
                    (remainder, GOLD))):
                self.text(48+i*328, y+119, 310, 40, text, 32, color, display=True, center=True)
            return
        values = ((self.data.play_count, "CLIPS", GREEN), (counts["Pass"], "PASS", BLUE),
                  (counts["Run"], "RUN", GREEN))
        for i, (count, label, color) in enumerate(values):
            x = 48 + i * 250
            self.text(x, y, 230, 112, count, 105, color, display=True, center=True)
            self.text(x, y+110, 230, 46, label, 38, display=True, center=True)
        self.text(815, y+18, 217, 136,
                  "\n".join(f"{v} {k.lower()}" for k, v in remaining) or "All play types logged",
                  27, GOLD, center=True)

    def map(self, y, row_h=48):
        data = self.data
        if not data.plays:
            self.text(48, y+40, 984, 100, "No saved clips in this view", 34, DIM, center=True)
            return
        # Continuous film order: quarter markers label real transitions, including unknown/OT.
        x, width = 134, 898
        cell = width / len(data.plays)
        labels = ("TYPE", "RESULT", "PLAYER")
        for row, label in enumerate(labels):
            self.text(48, y+42+row*(row_h+5), 82, row_h, label, 22, display=True)
        starts = [i for i,p in enumerate(data.plays) if i == 0 or p.quarter != data.plays[i-1].quarter]
        for j, start in enumerate(starts):
            end = starts[j+1] if j+1 < len(starts) else len(data.plays)
            segment_w = (end-start)*cell
            q = data.plays[start].quarter
            label = f"{q} · {end-start} {'clip' if end-start == 1 else 'clips'}" if segment_w >= 115 else q
            self.text(x+start*cell, y, segment_w, 34, label, 24, GREEN, display=True, center=True)
        for i, play in enumerate(data.plays):
            for row, colors in enumerate(((play.type_colour,), play.result_colours or ("#29322c",), (play.colour,))):
                for k, color in enumerate(colors):
                    self.p.fillRect(QRectF(x+i*cell, y+42+row*(row_h+5)+k*row_h/len(colors),
                                          max(.2, cell-min(1.5, cell*.15)), row_h/len(colors)), QColor(color))
        self.text(134, y+52+3*(row_h+5), 898, 34,
                  "Clips in film order · Stacked results · Legend: up to 8 most frequent result tags.", 18, DIM)

    def legend(self, y):
        colors = {}
        counts = Counter()
        for play in self.data.plays:
            for label, color in zip(play.results, play.result_colours):
                colors.setdefault(label, color)
                counts[label] += 1
        type_colors = {p.type_category: p.type_colour for p in self.data.plays}
        entries = [("Pass", type_colors.get("Pass", BLUE)), ("Run", type_colors.get("Run", GREEN))]
        entries += [(label, colors[label]) for label,_ in counts.most_common(8)]
        cell = 984 / 5
        for i, (label, color) in enumerate(entries):
            x, top = 48+(i%5)*cell, y+(i//5)*24
            self.p.fillRect(QRectF(x, top+3, 12, 12), QColor(color))
            self.text(x+19, top, cell-23, 22, label.upper(), 19, display=True)

    def result_totals(self, x, y, width):
        self.text(x, y, width, 36, "SAVED RESULT TAGS", 29, GREEN, display=True)
        for i, (key, label, color) in enumerate((("touchdown", "TOUCHDOWN", "#9162b7"),
                                                 ("first down", "FIRST DOWN", GOLD))):
            color = next((c for p in self.data.plays for v,c in zip(p.results,p.result_colours)
                          if legacy_result_key(v) == key), color)
            col = x+i*width/2
            self.text(col, y+48, width/2-12, 103, tagged_count(self.data,key), 92, color, display=True)
            self.text(col, y+153, width/2-12, 40, label, 26, display=True)
        self.text(x, y+203, width, 28, "Result tags can overlap.", 18, DIM)

    def players(self, x, y, width, count=5, row_h=100):
        players = self.data.players[:count]
        maximum = max((p.snaps for p in players), default=1)
        if not players:
            self.text(x, y, width, 80, "No primary players logged", 30, DIM)
        for i, player in enumerate(players):
            top = y+i*row_h
            self.text(x, top, width*.47, row_h-10, player.name.upper(), 48 if width > 700 else 34, display=True)
            bar_x, bar_w = x+width*.49, width*.40
            self.p.fillRect(QRectF(bar_x, top+10, bar_w*player.snaps/maximum, 44 if width > 700 else 32), QColor(player.colour))
            self.text(x+width*.91, top, width*.09, 60, player.snaps, 56 if width > 700 else 39, player.colour, display=True)

    def draw(self):
        slide = self.data.report_slide if self.data.report_template == "weekly_carousel" else 0
        self.header(slide)
        if slide in (0,1):
            self.totals(365, hero=slide == 1)
            self.legend(532)
            self.map(584, 49 if slide == 0 else 66)
            self.rule(838 if slide == 0 else 898)
            if slide == 0:
                self.result_totals(48, 862, 382)
                self.text(484, 862, 548, 40, "MOST FEATURED PLAYERS", 29, GREEN, display=True)
                self.players(484, 910, 548, 3, 60)
                self.text(484, 1080, 548, 22, "Primary-player clip assignments, not touches.", 17, DIM)
                self.rule(1102)
                self.text(48, 1120, 984, 35, "THE FILM NOTE", 28, GREEN, display=True)
                self.text(48, 1168, 984, 105, self.copy.get("film_note", ""), 32, editorial=True)
            else:
                self.result_totals(48, 925, 984)
        elif slide == 2:
            self.players(48, 388, 984, 5, 110)
            self.text(48, 945, 984, 42, "Primary-player clip assignments — not carries or receptions.", 23, DIM)
            self.rule(1010)
            self.text(48, 1043, 984, 40, "PLAYER TO WATCH", 30, GREEN, display=True)
            self.text(48, 1100, 984, 160, self.copy.get("player_note", ""), 34, editorial=True)
        else:
            for i in range(3):
                y = 394+i*222
                self.text(48, y, 104, 102, f"0{i+1}", 82, GREEN, display=True)
                self.text(180, y+3, 852, 172, self.copy.get(f"takeaway_{i+1}", ""), 37)
                self.rule(y+195)
            clip = next((p for p in self.data.plays if p.clip_id == self.copy.get("clip_id")), None)
            if clip:
                self.text(48, 1086, 984, 43, "PLAY TO REVISIT", 32, GREEN, display=True)
                self.text(48, 1140, 984, 121, f"{clip.clip_number:03d} · {clip.title}", 34)
        self.rule(1290)
        self.text(48, 1307, 680, 27, f"{self.data.view} · Saved clips · Analysis by IFI", 18, DIM)
        self.text(754, 1307, 278, 27, "Charted with TapeSift", 18, GREEN)


def paint_social(painter, data, width):
    if data.report_slide not in (1, 2, 3):
        raise ValueError("Choose carousel slide 1, 2 or 3")
    height = round(width * 1.25)
    if painter:
        texture = _assets()
        painter.save()
        try:
            painter.scale(width / 1080, width / 1080)
            painter.fillRect(QRectF(0, 0, 1080, 1350), QColor("#080c0a"))
            if not texture.isNull():
                painter.drawImage(QRectF(0, 0, 1080, 1350), texture)
            painter.setRenderHint(painter.RenderHint.Antialiasing)
            _Post(painter, data).draw()
        finally:
            painter.restore()
    return height
