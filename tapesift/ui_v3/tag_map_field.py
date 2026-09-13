"""Cached field artwork; film-time coordinates remain owned by AttributeGrid."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap

from tapesift.ui_v3.icons import brand_pixmap
from tapesift.services.result_service import legacy_result_key
from tapesift.services.heatmap_palette import player_colour


class TagMapField:
    # V3 display ink only; saved tag styles and report palettes stay intact.
    play_colors = {"run": "#46735a", "pass": "#426584", "rpo": "#457a76",
                   "screen": "#726084", "special": "#726084", "no_play": "#677078"}
    quarter_colors = {"Q1": "#657f99", "Q2": "#88728f", "Q3": "#9b875d",
                      "Q4": "#5d8a83", "OT": "#9a7268"}
    result_colors = {"gain": "#6e8a70", "completion": "#5a8797", "reception": "#64948c",
                     "first down": "#a18b59", "touchdown": "#91719a", "no gain": "#727b85",
                     "loss": "#986960", "incompletion": "#7c8299", "drop": "#967887",
                     "sack": "#a07565", "tfl": "#8d6e5f", "interception": "#995e71",
                     "fumble": "#8c698c", "penalty": "#9b8b71"}

    @classmethod
    def quarter_color(cls, value):
        key = str(value).strip().upper()
        if key.endswith("OT") and key[:-2].isdigit():
            key = "OT"
        return cls.quarter_colors.get(key, "#677078")

    @classmethod
    def result_color(cls, value):
        return cls.result_colors.get(legacy_result_key(value), player_colour(value))

    @staticmethod
    def action_color(value):
        return {"block": "#6d8c76", "coverage": "#62858d", "pressure": "#9a736f",
                "tackle": "#8e7b67"}.get(str(value).strip().casefold(), player_colour(value))

    def __init__(self):
        self._cache = QPixmap()
        self._key = None

    def paint(self, painter, rect):
        if rect.width() <= 0 or rect.height() <= 0:
            return
        dpr = painter.device().devicePixelRatioF()
        key = (rect.width(), rect.height(), dpr)
        if key != self._key:
            self._key = key
            self._cache = QPixmap(round(rect.width()*dpr), round(rect.height()*dpr))
            self._cache.setDevicePixelRatio(dpr)
            self._cache.fill(QColor("#08090a"))
            p = QPainter(self._cache)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            field = QRectF(0, 0, rect.width(), rect.height()).adjusted(2, 2, -2, -2)
            # The bundled JPG is byte-identical to the user's supplied IFI artwork.
            logo = brand_pixmap("ifi-full-name.jpg", 360)
            if not logo.isNull():
                scale = min(field.width() * .42 / logo.width(), field.height() * .82 / logo.height())
                target = QRectF(0, 0, logo.width()*scale, logo.height()*scale)
                target.moveCenter(field.center())
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
                p.setOpacity(.08)
                p.drawPixmap(target, logo, QRectF(logo.rect()))
                p.setOpacity(1)
            for yard in range(101):
                x = field.left() + field.width()*yard/100
                if yard % 5 == 0:
                    p.setPen(QPen(QColor(230, 234, 238, 110 if yard % 10 == 0 else 60), 1))
                    p.drawLine(QPointF(x, field.top()), QPointF(x, field.bottom()))
                else:
                    p.setPen(QPen(QColor(230, 234, 238, 125), 1))
                    for fraction in (0, .375, .625, .98):
                        y = field.top() + field.height()*fraction
                        p.drawLine(QPointF(x, y), QPointF(x, y + field.height()*.02))
            font = QFont("Georgia")
            font.setBold(False)
            # Decorative field geometry, independent of the film-time ruler.
            font.setPixelSize(max(11, min(19, round(field.height()*.09))))
            p.setFont(font)
            p.setPen(QColor("#c8cdd2"))
            for yard in range(10, 100, 10):
                x = field.left() + field.width()*yard/100
                p.drawText(QRectF(x-22, field.bottom()-32, 44, 28), Qt.AlignmentFlag.AlignCenter,
                           str(min(yard, 100-yard)))
            p.setPen(QPen(QColor("#343c42"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(field)
            p.end()
        painter.drawPixmap(rect.topLeft(), self._cache)

    @staticmethod
    def paint_pylons(painter, grid, row_top, row_height):
        painter.save()
        for ms, label in getattr(grid, "_quarter_pylons", ()):
            if not grid._visible_range[0] <= ms <= grid._visible_range[1]:
                continue
            x = grid._x_for(ms)
            painter.setPen(QPen(QColor("#ffc27b"), 1))
            painter.setBrush(QColor("#f18b38"))
            painter.drawRoundedRect(QRectF(x-3, row_top+2, 6, row_height-4), 1, 1)
            # A pylon is a saved period boundary, never a scrub or trim target.
        painter.restore()
