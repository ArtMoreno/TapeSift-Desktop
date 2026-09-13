"""Marks drawn on a play.

Every mark is stored in **film coordinates**: a fraction of the frame,
0.0-1.0 on each axis, with the origin at the frame's top-left. Not pixels
on the widget.

That is the one decision here that is expensive to change later. Screen
positions are meaningless the moment anything about the presentation moves
- a resized window, a different monitor, a 1080p export of a preview drawn
at 480 wide, and above all zoom. Zooming to look at the right guard is
exactly when an analyst draws, and screen-space marks would slide off the
players they were pointing at. Fractions survive all of it, because they
describe the film rather than the display.

Converting is arithmetic against the rectangle the frame actually occupies,
which the video surface reports as ``frame_rect()``.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from enum import Enum


class MarkKind(str, Enum):
    """Every drawable kind, stored by name so files survive reordering.

    The first four are the original tools and must never be renamed: they
    are already written into saved projects.
    """

    ARROW = "arrow"
    CIRCLE = "circle"
    LINE = "line"
    FREEHAND = "freehand"

    # Coach's chalk - standard film-study notation.
    ROUTE_ARROW = "route_arrow"
    ROUTE_CURVE = "route_curve"
    ROUTE_COMEBACK = "route_comeback"
    MOTION_DASHED = "motion_dashed"
    BLOCK_TEE = "block_tee"
    DOUBLE_TEAM = "double_team"
    PULL_TRAP = "pull_trap"
    HANDOFF = "handoff"
    OPTION_PITCH = "option_pitch"
    COVERAGE_MAN = "coverage_man"
    ZONE_BUBBLE = "zone_bubble"
    PURSUIT_ANGLE = "pursuit_angle"

    # Player markers.
    PLAYER_O = "player_o"
    PLAYER_X = "player_x"
    PLAYER_RING = "player_ring"
    PLAYER_TRIANGLE = "player_triangle"
    NUMBER_MARKER = "number_marker"

    # General markup.
    LINE_SOLID = "line_solid"
    LINE_DASHED = "line_dashed"
    ARROW_DOUBLE = "arrow_double"
    RECTANGLE = "rectangle"
    ZONE_BOX = "zone_box"
    ELLIPSE_SHAPE = "ellipse_shape"
    SPOTLIGHT = "spotlight"
    FREEHAND_SHAPE = "freehand_shape"
    MEASURE = "measure"
    TEXT_LABEL = "text_label"


@dataclass(frozen=True)
class ShapeStyle:
    """How one kind is drawn, as data rather than a branch per tool.

    Twenty-seven hand-written painters would drift apart; a handful of
    primitives with flags stays consistent, and a new tool becomes a table
    entry rather than a new code path.
    """

    primitive: str = "segment"   # segment curve ellipse box marker path
    dashed: bool = False
    arrow_end: bool = False
    arrow_start: bool = False
    tee_end: bool = False
    end_ticks: bool = False
    bar_end: bool = False
    bow: float = 0.0             # curve depth, fraction of the chord
    marker: str = ""             # x triangle number spotlight coverage etc.
    heavy: bool = False          # thicker core stroke


#: One entry per kind. Anything absent falls back to a plain segment.
SHAPES: dict["MarkKind", ShapeStyle] = {
    MarkKind.ARROW: ShapeStyle("segment", arrow_end=True),
    MarkKind.LINE: ShapeStyle("segment"),
    MarkKind.CIRCLE: ShapeStyle("ellipse"),
    MarkKind.FREEHAND: ShapeStyle("path"),

    MarkKind.ROUTE_ARROW: ShapeStyle("segment", arrow_end=True),
    MarkKind.ROUTE_CURVE: ShapeStyle("curve", arrow_end=True, bow=0.30),
    MarkKind.ROUTE_COMEBACK: ShapeStyle("curve", arrow_end=True, bow=-0.34),
    MarkKind.MOTION_DASHED: ShapeStyle("segment", dashed=True, arrow_end=True),
    MarkKind.BLOCK_TEE: ShapeStyle("segment", tee_end=True),
    MarkKind.DOUBLE_TEAM: ShapeStyle("marker", marker="double_team"),
    MarkKind.PULL_TRAP: ShapeStyle("curve", arrow_end=True, bow=0.55),
    MarkKind.HANDOFF: ShapeStyle("segment", arrow_end=True, bar_end=True),
    MarkKind.OPTION_PITCH: ShapeStyle(
        "curve", arrow_end=True, dashed=True, bow=0.38),
    MarkKind.COVERAGE_MAN: ShapeStyle("marker", marker="coverage", dashed=True),
    MarkKind.ZONE_BUBBLE: ShapeStyle("ellipse", dashed=True),
    MarkKind.PURSUIT_ANGLE: ShapeStyle("segment", arrow_end=True, dashed=True),

    MarkKind.PLAYER_O: ShapeStyle("ellipse"),
    MarkKind.PLAYER_X: ShapeStyle("marker", marker="x"),
    MarkKind.PLAYER_RING: ShapeStyle("ellipse", heavy=True),
    MarkKind.PLAYER_TRIANGLE: ShapeStyle("marker", marker="triangle"),
    MarkKind.NUMBER_MARKER: ShapeStyle("marker", marker="number"),

    MarkKind.LINE_SOLID: ShapeStyle("segment"),
    MarkKind.LINE_DASHED: ShapeStyle("segment", dashed=True),
    MarkKind.ARROW_DOUBLE: ShapeStyle(
        "segment", arrow_end=True, arrow_start=True),
    MarkKind.RECTANGLE: ShapeStyle("box"),
    MarkKind.ZONE_BOX: ShapeStyle("box", dashed=True),
    MarkKind.ELLIPSE_SHAPE: ShapeStyle("ellipse"),
    MarkKind.SPOTLIGHT: ShapeStyle("marker", marker="spotlight"),
    MarkKind.FREEHAND_SHAPE: ShapeStyle("path"),
    MarkKind.MEASURE: ShapeStyle("segment", end_ticks=True),
    MarkKind.TEXT_LABEL: ShapeStyle("marker", marker="label"),
}


def shape_style(kind: "MarkKind") -> ShapeStyle:
    return SHAPES.get(kind, ShapeStyle())


def is_path_kind(kind: "MarkKind") -> bool:
    """True when a kind keeps its whole sampled point sequence."""
    return shape_style(kind).primitive == "path"


#: Ink colours. Deliberately not the interface palette: these sit on the
#: film, competing with turf, white jerseys and shadow, so they are chosen
#: to survive that rather than to match the chrome. Gold reads as offense,
#: cyan as defense, red as emphasis - and every stroke is drawn twice, a
#: dark halo under a bright core, which is how a broadcast telestrator stays
#: legible over both a white lineman and dark grass.
INK = {
    "gold": "#ffd24a",
    "cyan": "#5ad6f0",
    "red": "#ff6b5e",
    "neon": "#39ff5e",
    "white": "#ffffff",
    "black": "#101010",
    "orange": "#ff9430",
    "yellow": "#ffe94a",
    "green": "#4cd964",
    "blue": "#4a9cff",
    "purple": "#b07cff",
    "pink": "#ff6fc4",
}
DEFAULT_INK = "gold"


@dataclass
class Mark:
    """One drawn element, in film coordinates.

    ``points`` are (x, y) fractions of the frame. An arrow and a line use
    two, a circle uses two opposite corners of its bounding box, freehand
    uses as many as were sampled.
    """

    kind: MarkKind
    points: list[tuple[float, float]]
    ink: str = DEFAULT_INK
    width: float = 1.0
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "ink": self.ink,
            "width": self.width,
            # Rounded because six decimals is a tenth of a pixel on an
            # 8K frame, and the rest is noise that bloats every project file.
            "points": [[round(x, 6), round(y, 6)] for x, y in self.points],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Mark":
        raw = data.get("points") or []
        points = [(float(p[0]), float(p[1])) for p in raw if len(p) >= 2]
        try:
            kind = MarkKind(data.get("kind", "arrow"))
        except ValueError:
            kind = MarkKind.ARROW
        ink = data.get("ink", DEFAULT_INK)
        return cls(
            kind=kind,
            points=points,
            ink=ink if ink in INK else DEFAULT_INK,
            width=float(data.get("width", 1.0) or 1.0),
            id=str(data.get("id") or uuid.uuid4().hex),
        )


def marks_to_json(marks: list[Mark]) -> list[dict]:
    return [mark.to_dict() for mark in marks]


def marks_from_json(data) -> list[Mark]:
    """Rebuild marks, skipping anything malformed rather than failing.

    A project that cannot open because one drawing is corrupt would be a bad
    trade: the marks are an annotation on the play, not the play itself.
    """
    if not isinstance(data, list):
        return []
    out: list[Mark] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        raw_points = entry.get("points")
        if not isinstance(raw_points, (list, tuple)) or not raw_points:
            continue
        points: list[tuple[float, float]] = []
        valid = True
        for point in raw_points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                valid = False
                break
            x_raw, y_raw = point[0], point[1]
            if isinstance(x_raw, bool) or isinstance(y_raw, bool):
                valid = False
                break
            if not isinstance(x_raw, (int, float)) \
                    or not isinstance(y_raw, (int, float)):
                valid = False
                break
            try:
                x, y = float(x_raw), float(y_raw)
            except (TypeError, ValueError, OverflowError):
                valid = False
                break
            if not math.isfinite(x) or not math.isfinite(y) \
                    or not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                valid = False
                break
            points.append((x, y))
        if not valid:
            continue
        try:
            kind = MarkKind(entry.get("kind", "arrow"))
        except (TypeError, ValueError):
            # Retain the legacy/future-kind fallback, but validate it as the
            # arrow it becomes rather than accepting geometry it cannot draw.
            kind = MarkKind.ARROW
        # Every kind except a sampled path is defined by two points; a
        # path keeps its complete sequence and must not be truncated.
        if not is_path_kind(kind) and len(points) < 2:
            continue
        # Freehand deliberately keeps its complete point sequence; unlike the
        # geometric tools it must not be truncated to a start/end pair.
        normalized = dict(entry)
        normalized["points"] = points
        try:
            raw_width = normalized.get("width", 1.0)
            if raw_width is None or raw_width == "":
                raw_width = 1.0
            if isinstance(raw_width, bool):
                continue
            width = float(raw_width)
            if not math.isfinite(width) or width <= 0.0:
                continue
            normalized["width"] = width
            mark = Mark.from_dict(normalized)
        except (TypeError, ValueError, OverflowError):
            continue
        out.append(mark)
    return out


def to_film(x: float, y: float, rect) -> tuple[float, float]:
    """Widget point -> film fraction, clamped to the frame.

    Clamped because a drag that leaves the picture should stop at its edge
    rather than store a mark nobody can ever see again.
    """
    width = max(1e-6, rect.width())
    height = max(1e-6, rect.height())
    fx = (x - rect.x()) / width
    fy = (y - rect.y()) / height
    return (min(1.0, max(0.0, fx)), min(1.0, max(0.0, fy)))


def to_widget(fx: float, fy: float, rect) -> tuple[float, float]:
    """Film fraction -> widget point, against the frame's current rectangle."""
    return (rect.x() + fx * rect.width(), rect.y() + fy * rect.height())
