"""Soft-shadow elevation for in-window V2 surfaces.

Qt stylesheets cannot paint shadows, and native top-level windows already
get their depth from the window manager. These helpers give the in-window
surfaces - cards, trays, and overlay panels - a real soft shadow so the
interface reads in layers instead of hairlines.

Never apply these to the video viewport or the timeline: the shadow
effect rasterizes its target on every repaint, which is wasted work on
per-frame content.
"""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget

from tapesift.ui_v2.tokens import COLORS, SHADOWS

_PROPERTY = "tapesiftElevation"


def apply_elevation(widget: QWidget, level: str = "card") -> None:
    """Attach the soft shadow for *level* to *widget* (once per level)."""
    recipe = SHADOWS[level]
    if widget.property(_PROPERTY) == level:
        return
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(float(recipe["blur"]))
    effect.setOffset(0.0, float(recipe["y"]))
    color = QColor(COLORS["backdrop"])
    color.setAlpha(int(recipe["alpha"]))
    effect.setColor(color)
    widget.setGraphicsEffect(effect)
    widget.setProperty(_PROPERTY, level)


def clear_elevation(widget: QWidget) -> None:
    """Drop any shadow previously attached by :func:`apply_elevation`."""
    if widget.property(_PROPERTY) is not None:
        widget.setGraphicsEffect(None)
        widget.setProperty(_PROPERTY, None)
