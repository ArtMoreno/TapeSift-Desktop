"""Play attributes as a grid aligned to the timeline.

One column per play, one row per attribute, sharing the timeline's visible
range so a column sits directly under the play it describes. This is what
lets you read across a drive - every result, every player, every review
state - instead of hunting the same facts one clip at a time.

Two densities, decided by the timeline rather than here: words when a play
has room for them, colour chips when it does not. Zooming out is never
blocked, so the whole game stays reachable as chips.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from html import escape
from typing import Callable

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QScrollArea, QScrollBar,
    QSizePolicy, QToolTip, QWidget,
)

from tapesift.models.clip import Clip
from tapesift.services.football_context import down_distance, parse_down_distance
from tapesift.services.football_vocab import category_for, short_for, values_for
from tapesift.services.result_service import split_results
from tapesift.services.detail_service import split_players
from tapesift.services.roster_service import strip_jersey_number
from tapesift.services.timestamp_parser import format_ms
from tapesift.services.player_name_service import (
    resolve_roster, resolved_key)
from tapesift.services.tag_style_service import canonical_tag_key
from tapesift.ui_core.timeline import DENSITY_CHIP, DENSITY_TEXT
from tapesift.ui_v2.tag_readout import (
    DATA_GREEN, OUTCOME_COLORS, PLAY_TYPE_COLORS, _clean, _outcome,
    _play_type, _surname, outcome_key,
)

ROW_H = 26
CHEVRON_W = 16
#: A collapsed row keeps a slim stub, or its chevron would go
#: with it and there would be no way back.
COLLAPSED_H = 14
ROW_GAP = 2
GUTTER_W = 168
RULER_H = 0
CELL_PAD = 4
#: Rows beyond this and the grid scrolls rather than growing further.
MAX_VISIBLE_ROWS = 8

C_BG = QColor("#0f0f0f")
C_EDGE = QColor("#242422")
C_GUTTER_TEXT = QColor("#a6aaa5")
C_ROW_ALT = QColor(255, 255, 255, 7)
C_CELL_TEXT = QColor("#eceee7")
C_MUTED = QColor("#6f776c")
C_SELECTED = QColor("#a7afa5")
C_SELECT_WASH = QColor(226, 235, 227, 18)
C_CHEVRON = QColor("#6f776c")
C_BAR_TRACK = QColor("#191918")
C_AMBER = QColor("#f0b429")

# Option 4 gives each horizontal family a thin scan rail without turning the
# gutter into a second control surface. These are presentation-only markers;
# values and their authoritative colours still come from each AttributeRow.
ROW_ACCENTS: dict[str, QColor] = {
    "quarter": QColor("#9567d8"),
    "down": QColor("#4da3ff"),
    "primary_tag": QColor("#57c98a"),
    "result": QColor("#d9b43b"),
    "action": QColor("#e9783f"),
    "people": QColor("#ff5d73"),
    "notes": QColor("#8f968d"),
    "review": QColor("#36d6c0"),
    "confidence": QColor("#7894a1"),
}

#: Player colours, assigned by how much of the game a player accounts
#: for rather than by hashing their name. Hashing spread 12 buckets over
#: a whole roster, so two workload leaders could collide - which is
#: exactly the pair you least want to look alike. Ranked assignment
#: guarantees the players you see most are distinct.
#:
#: Hues walk by the golden angle so consecutive ranks land on opposite
#: sides of the wheel, with lightness cycling through three bands. An
#: earlier version padded a 12-colour list with lighter copies of its own
#: hues, which put two golds 4.4 deltaE apart - indistinguishable, and it
#: showed up as two players looking identical. Minimum separation is now
#: 10 across every pair and 51 between neighbouring ranks.
PLAYER_COLORS: tuple[str, ...] = (
    "#7ca6e6", "#a5cc4e", "#f7a3f6", "#7ce6c8",
    "#cc844e", "#afa3f7", "#8ce67c", "#cc4e85",
    "#a3e1f7", "#e6e37c", "#a44ecc", "#a3f7c4",
    "#e6867c", "#4e66cc", "#ccf7a3", "#e67cce",
    "#4eccc3", "#f7d9a3", "#a17ce6", "#4ecc55",
    "#f7a3b7", "#7cb4e6",
)
C_NO_PLAYER = "#3a4038"


#: Rows whose value is a choice, and the field(s) each choice writes.
#: Everything else - Review Status, Notes, Detection Confidence - is
#: derived or free text and stays read-only here.
def _choices(*pairs: tuple[str, dict[str, str]]):
    return tuple(pairs)


EDIT_CHOICES: dict[str, tuple[tuple[str, dict[str, str]], ...]] = {
    "primary_tag": _choices(
        ("Run", {"run_pass": "Run"}),
        ("Pass", {"run_pass": "Pass"}),
        ("Screen", {"run_pass": "Pass", "play_type": "Screen"}),
        ("RPO", {"play_type": "RPO"}),
    ),
    "result": _choices(
        ("No Gain", {"result": "No Gain"}),
        ("First Down", {"result": "First Down"}),
        ("Reception", {"result": "Reception"}),
        (short_for("result", "Touchdown"), {"result": "Touchdown"}),
        ("Sack", {"result": "Sack"}),
        (short_for("result", "Interception"), {"result": "Interception"}),
    ),
    "quarter": _choices(
        *((value, {"quarter": value}) for value in values_for("quarter")),
    ),
    "action": _choices(
        ("Block", {"action": "Block"}),
        ("Pressure", {"action": "Pressure"}),
        ("Missed Tackle", {"action": "Missed Tackle"}),
        ("Clear", {"action": ""}),
    ),
    "down": _choices(
        ("1st", {"down_distance": "1st"}),
        ("2nd", {"down_distance": "2nd"}),
        ("3rd", {"down_distance": "3rd"}),
        ("4th", {"down_distance": "4th"}),
    ),
}

#: Rows a menu cannot finish. A down is one of four, but the distance
#: with it is any number and sometimes the word Goal, so these offer the
#: common picks and then a way to type the rest.
EDIT_TYPED: dict[str, tuple[str, str]] = {
    "down": ("Down & distance", "3rd & 7, or 3rd & Goal"),
    "yards": ("Yards gained", "7, or -3"),
}

def normalize_down_distance(raw: str) -> str:
    """Take a down and distance however it was typed.

    "3rd & 7", "3 7", "3rd and goal" and "4th&G" are all the same thing
    said at different speeds, and someone logging film at pace should not
    have to remember which one this field wants.
    """
    return down_distance(*parse_down_distance(str(raw)))


def set_down_keeping_distance(current: str, down: str) -> str:
    """Change the down without throwing away the distance beside it."""
    return down_distance(parse_down_distance(down)[0], parse_down_distance(current)[1])


@dataclass(frozen=True)
class AttributeRow:
    """One horizontal band: how to label it and what to read per clip."""

    key: str
    label: str
    value: Callable[[Clip], str]
    colour: Callable[[Clip], str]
    #: 0..1 for rows drawn as a filled bar rather than a flat cell.
    ratio: Callable[[Clip], float] | None = None


def _primary_tag(clip: Clip) -> str:
    found = _play_type(clip.details)
    return found[1] if found else ""


def _primary_tag_colour(clip: Clip) -> str:
    found = _play_type(clip.details)
    return PLAY_TYPE_COLORS.get(found[0], "#526158") if found else "#3a4038"


def _result(clip: Clip) -> str:
    found = _outcome(clip)
    return found[1] if found else ""


def _result_colour(clip: Clip) -> str:
    found = _outcome(clip)
    return OUTCOME_COLORS.get(found[0], "#526158") if found else "#3a4038"


def _review(clip: Clip) -> str:
    if not clip.enabled:
        return "Excluded"
    return "Logged" if clip.details else "Unlogged"


def _review_colour(clip: Clip) -> str:
    if not clip.enabled:
        return "#59615b"
    return DATA_GREEN if clip.details else "#3a4038"


def _people(clip: Clip) -> str:
    primary = _clean(clip.details.get("player_name"))
    return _surname(primary).title() if primary else ""


def _player_key(clip: Clip, roster: dict[str, str] | None = None) -> str:
    """The key this clip's player counts under.

    With a roster, spellings of one name fold together - otherwise the
    same player is two rows, two colours and two lines in the heat map.
    """
    name = _clean(clip.details.get("player_name"))
    if roster is not None:
        return resolved_key(name, roster)
    return canonical_tag_key(name)


def roster_for(clips: list[Clip]) -> dict[str, str]:
    """Every spelling in these clips, mapped to the one name to show."""
    return resolve_roster(
        _clean(clip.details.get("player_name")) for clip in clips)


def rank_player_colours(clips: list[Clip]) -> dict[str, str]:
    """Colour by workload: the most-tagged player takes the first hue.

    Ties break on the name so the order is deterministic, and players
    past the palette wrap rather than going colourless - by then they
    appear rarely enough that a repeat costs nothing.
    """
    roster = roster_for(list(clips))
    counts: dict[str, int] = {}
    for clip in clips:
        key = _player_key(clip, roster)
        if key:
            counts[key] = counts.get(key, 0) + 1
    ranked = sorted(counts, key=lambda k: (-counts[k], k))
    return {
        key: PLAYER_COLORS[index % len(PLAYER_COLORS)]
        for index, key in enumerate(ranked)
    }


def _notes(clip: Clip) -> str:
    return _clean(clip.notes)


def _confidence_ratio(clip: Clip) -> float:
    raw = clip.detection_lineage.get("confidence", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value if value <= 1 else value / 100.0))


def _confidence(clip: Clip) -> str:
    raw = clip.detection_lineage.get("confidence", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return ""
    return f"{round(value * 100 if value <= 1 else value)}%"


#: Quarters read as bands when the whole game is on screen, which is the
#: one thing the play list cannot show at all.
QUARTER_COLOURS = {
    "Q1": "#4d7fd6", "Q2": "#4dc4a6", "Q3": "#c9a43d",
    "Q4": "#d1603d", "OT": "#a44ecc",
}
DOWN_COLOURS = {
    "1st": "#3f7a4d", "2nd": "#5c7a3f", "3rd": "#a8863a", "4th": "#b0503a",
}
ACTION_COLOURS = {
    "block": "#7ca6e6", "pressure": "#a5cc4e", "missed tackle": "#cc4e85",
    "tackle": "#718078", "big hit": "#e6867c", "coverage": "#36c8d8",
    "pbu": "#36c8d8", "forced fumble": "#cc4e85",
}


def _quarter(clip: Clip) -> str:
    return _clean(clip.details.get("quarter")).upper()


def _quarter_colour(clip: Clip) -> str:
    return QUARTER_COLOURS.get(_quarter(clip), C_NO_PLAYER)


def _down_distance(clip: Clip) -> str:
    return _clean(clip.details.get("down_distance"))


def _down_colour(clip: Clip) -> str:
    """Colour by the down alone - the distance is read, not scanned."""
    head = _down_distance(clip).split(" ")[0].lower()
    return DOWN_COLOURS.get(head, C_NO_PLAYER)


def _action(clip: Clip) -> str:
    return _clean(clip.details.get("action"))


def _action_colour(clip: Clip) -> str:
    return ACTION_COLOURS.get(_action(clip).lower(), C_NO_PLAYER)


DEFAULT_ROWS: tuple[AttributeRow, ...] = (
    AttributeRow("quarter", "Quarter", _quarter, _quarter_colour),
    AttributeRow("down", "Down & Distance", _down_distance, _down_colour),
    AttributeRow("primary_tag", "Primary Tag", _primary_tag,
                 _primary_tag_colour),
    AttributeRow("result", "Result", _result, _result_colour),
    AttributeRow("action", "Action", _action, _action_colour),
    AttributeRow("people", "People", _people, lambda _c: C_NO_PLAYER),
    AttributeRow("notes", "Notes", _notes, lambda _c: "#4a5148"),
    AttributeRow("review", "Review Status", _review, _review_colour),
    AttributeRow("confidence", "Detection Confidence", _confidence,
                 lambda _c: "#4da3ff", ratio=_confidence_ratio),
)

#: Rows that open closed. Zoomed out, a down and a yard line are muddy
#: near-identical colours with no pattern to scan - they are values you
#: read, so they earn a strip until you zoom in. Without this the grid
#: also runs past its own ceiling and silently drops the last rows.
DEFAULT_COLLAPSED: frozenset[str] = frozenset(
    {"down", "review", "confidence"})

REVIEW_ROWS = tuple(sorted(
    (replace(row, label="Play Type") if row.key == "primary_tag" else row
     for row in DEFAULT_ROWS if row.key not in {"review", "confidence"}),
    key=lambda row: row.key == "action"))


class AttributeGrid(QWidget):
    """Read-only attribute columns under the timeline."""

    clipActivated = Signal(str)
    #: (clip_id, row_key) - a cell asking to be changed.
    cellEditRequested = Signal(str, str)
    #: clip id, row key, index into that row's EDIT_CHOICES.
    cellChoicePicked = Signal(str, str, int)
    #: The player key now isolated, or "" for the whole game.
    playerFilterChanged = Signal(str)
    quarterContextChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("V2AttributeGrid")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMouseTracking(True)

        self._review_style = False
        self._review_labels_visible = True
        self._review_toolbar_height = 34
        self._compact_review_ruler = False
        self._clips: list[Clip] = []
        self._player_colours: dict[str, str] = {}
        self._roster: dict[str, str] = {}
        # People is bound to this grid because its colour depends on the
        # project's ranking, not on the clip alone.
        self._rows: tuple[AttributeRow, ...] = tuple(
            replace(row, colour=self._people_colour)
            if row.key == "people" else row
            for row in DEFAULT_ROWS
        )
        self._collapsed: set[str] = set(DEFAULT_COLLAPSED)
        #: Rows the auto-empty rule collapsed, so it can bring them back
        #: when data appears - and rows the user toggled by hand, which
        #: the rule must never override.
        self._auto_empty: set[str] = set()
        self._rows_touched: set[str] = set()
        self._visible_range: tuple[int, int] = (0, 0)
        self._density = DENSITY_CHIP
        self._selected_clip_id: str | None = None
        #: (row index, clip index) of the keyboard cursor, if any. Logging
        #: a game is hundreds of small edits, and reaching for the mouse
        #: for each one is the difference between usable and not.
        self._cursor: tuple[int, int] | None = None
        self._player_filter: str = ""
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._apply_height()

    # ------------------------------------------------------------- content

    def set_clips(self, clips: list[Clip]) -> None:
        self._clips = sorted(clips, key=lambda c: c.start_ms)
        # Resolved over the whole project, not the visible window, so a
        # player does not change colour or split in two as you pan.
        self._roster = roster_for(self._clips)
        self._player_colours = rank_player_colours(self._clips)
        if self._review_style:
            from tapesift.services.heatmap_palette import player_colour
            self._player_colours = {resolved_key(name, self._roster): player_colour(name, self._roster)
                                    for name in self._roster.values()}
        self._auto_collapse_empty_rows()
        if self._review_style:
            self._refresh_review_legend()
            self._layout_review_controls()
        self.update()
        self.quarterContextChanged.emit()

    def _auto_collapse_empty_rows(self) -> None:
        """Collapse rows that have no data anywhere in the project.

        An empty expanded row is 26px of dead lane per row - in a fresh
        project that is most of the grid. Collapsing to the stub keeps the
        chevron reachable while giving the video the space back. A row the
        user toggled by hand is never touched, and a row the rule collapsed
        re-opens on its own the moment data lands in it.
        """
        if self._review_style:
            return
        changed = False
        for row in self._rows:
            if row.key in self._rows_touched:
                continue
            has_data = any(row.value(clip) for clip in self._clips)
            if has_data and row.key in self._auto_empty:
                self._auto_empty.discard(row.key)
                self._collapsed.discard(row.key)
                changed = True
            elif not has_data and row.key not in self._collapsed:
                self._collapsed.add(row.key)
                self._auto_empty.add(row.key)
                changed = True
        if changed:
            self._apply_height()

    def _people_colour(self, clip: Clip) -> str:
        key = _player_key(clip, getattr(self, "_roster", None))
        if not key:
            return C_NO_PLAYER
        return self._player_colours.get(key, C_NO_PLAYER)

    def set_visible_range(self, start_ms: int, end_ms: int) -> None:
        normalized = (int(start_ms), int(end_ms))
        if normalized == self._visible_range:
            return
        self._visible_range = normalized
        self.update()
        self.quarterContextChanged.emit()

    def set_density(self, density: str) -> None:
        if density == self._density:
            return
        self._density = density
        self.update()

    def set_selected_clip_id(self, clip_id: str | None) -> None:
        normalized = str(clip_id) if clip_id else None
        if normalized == self._selected_clip_id:
            return
        self._selected_clip_id = normalized
        self.update()

    # --------------------------------------------------------------- rows

    def rows(self) -> tuple[AttributeRow, ...]:
        return self._rows

    def visible_rows(self) -> tuple[AttributeRow, ...]:
        return tuple(
            row for row in self._rows if row.key not in self._collapsed)

    def is_collapsed(self, key: str) -> bool:
        return key in self._collapsed

    def set_row_collapsed(self, key: str, collapsed: bool) -> None:
        if collapsed:
            self._collapsed.add(key)
        else:
            self._collapsed.discard(key)
        self._apply_height()
        if self._review_style:
            self._layout_review_controls()
        self.update()

    def toggle_row(self, key: str) -> None:
        self._rows_touched.add(key)
        self._auto_empty.discard(key)
        self.set_row_collapsed(key, key not in self._collapsed)

    def set_review_labels_visible(self, visible: bool) -> None:
        self._review_labels_visible = bool(visible)
        self.update()

    def _row_height(self, row: AttributeRow) -> int:
        if self._review_style:
            return 22 if row.key in self._collapsed else 36
        return COLLAPSED_H if row.key in self._collapsed else ROW_H

    def _apply_height(self) -> None:
        """Grow with open rows, then stop and let the panel scroll."""
        self.setFixedHeight(min(self.content_height(), self._ceiling()))

    def _ceiling(self) -> int:
        if self._review_style:
            return getattr(self, "_review_height_limit", 344 + self._review_toolbar_height)
        return RULER_H + MAX_VISIBLE_ROWS * (ROW_H + ROW_GAP) + ROW_GAP

    def content_height(self) -> int:
        """Height every row needs at its current state, no ceiling."""
        if self._review_style and not self._rows:
            return 0
        total = (self._review_body_top() + 32 if self._review_style else RULER_H) + ROW_GAP
        for row in self._rows:
            total += self._row_height(row) + ROW_GAP
        return max(total, RULER_H + ROW_H + ROW_GAP * 2)

    def sizeHint(self) -> QSize:
        return QSize(1200, self.height())

    # ----------------------------------------------------------- geometry

    def plot_left(self) -> int:
        return (152 if self._review_labels_visible else 0) if self._review_style else GUTTER_W

    def plot_width(self) -> int:
        return max(1, self.width() - self.plot_left() - (12 if self._review_style else 0))

    def _x_for(self, position_ms: int) -> int:
        if getattr(self, "timeline_x_for", None) is not None:
            return self.timeline_x_for(position_ms)
        start, end = self._visible_range
        if end <= start:
            return self.plot_left()
        ratio = (int(position_ms) - start) / float(end - start)
        ratio = max(0.0, min(1.0, ratio))
        return self.plot_left() + int(round(ratio * (self.plot_width() - 1)))

    def _row_top(self, index: int) -> int:
        top = (self._review_body_top() - self.lane_scroll.value() if self._review_style else RULER_H) + ROW_GAP
        for row in self._rows[:index]:
            top += self._row_height(row) + ROW_GAP
        return top

    def row_at(self, y: float) -> AttributeRow | None:
        if self._review_style and not self._review_body_top() <= y < self.height() - 32:
            return None
        for index, row in enumerate(self._rows):
            top = self._row_top(index)
            if top <= y <= top + self._row_height(row):
                return row
        return None

    def visible_clips(self) -> list[Clip]:
        start, end = self._visible_range
        if end <= start:
            return []
        return [
            clip for clip in self._clips
            if clip.end_ms > start and clip.start_ms < end
        ]

    def clip_at(self, x: float, y: float) -> Clip | None:
        if x < self.plot_left():
            return None
        if self._review_style:
            if self.row_at(y) is None:
                return None
            # Prefer the actual duration span; tolerance is only for tiny
            # chips at game zoom, never a neighboring clip's wider hit area.
            spans = [(clip, *self.clip_span(clip)) for clip in self.visible_clips()]
            for clip, left, right in spans:
                if left <= x < right:
                    return clip
            near = min(spans, key=lambda span: min(abs(x - span[1]), abs(x - span[2])),
                       default=None)
            if near is not None and near[1] - 3 <= x <= near[2] + 3:
                return near[0]
            return None
        for clip in self.visible_clips():
            if self._x_for(clip.start_ms) <= x <= self._x_for(clip.end_ms):
                return clip
        return None

    # ------------------------------------------------------------ drawing

    def paintEvent(self, _event) -> None:
        if self._review_style:
            self._paint_review()
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), C_BG)

        label_font = QFont(["IBM Plex Sans", "Segoe UI"])
        label_font.setPointSizeF(8.0)
        label_font.setBold(True)
        cell_font = QFont(["IBM Plex Sans", "Segoe UI"])
        cell_font.setPointSizeF(8.5)
        cell_font.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(cell_font)
        clips = self.visible_clips()

        for index, row in enumerate(self._rows):
            top = self._row_top(index)
            height = self._row_height(row)
            if index % 2 == 0:
                painter.fillRect(QRect(0, top, self.width(), height), C_ROW_ALT)

            accent = ROW_ACCENTS.get(row.key, C_MUTED)
            painter.fillRect(QRect(5, top + 4, 3, max(2, height - 8)), accent)

            # Drawn, not typed: the display font has no triangle glyphs
            # and renders them as empty boxes.
            self._draw_chevron(
                painter, 10, top, height,
                row.key not in self._collapsed)
            painter.setFont(label_font)
            painter.setPen(C_GUTTER_TEXT)
            painter.drawText(
                QRect(10 + CHEVRON_W, top, GUTTER_W - 26 - CHEVRON_W,
                      height),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                row.label)
            painter.setPen(C_EDGE)
            painter.drawLine(0, top + height, self.width(), top + height)

            if row.key in self._collapsed:
                continue
            painter.setFont(cell_font)
            for clip in clips:
                self._draw_cell(painter, row, clip, top, metrics)

        painter.setPen(C_EDGE)
        painter.drawLine(GUTTER_W, 0, GUTTER_W, self.height())
        # Last, so the ring sits over the cell it is on.
        self._paint_cursor(painter)

    def _draw_chevron(
            self, painter: QPainter, x: int, top: int, height: int,
            expanded: bool) -> None:
        cx = x + CHEVRON_W / 2.0
        cy = top + height / 2.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(C_CHEVRON)
        if expanded:
            points = [
                QPointF(cx - 3.5, cy - 2.0), QPointF(cx + 3.5, cy - 2.0),
                QPointF(cx, cy + 2.5)]
        else:
            points = [
                QPointF(cx - 2.0, cy - 3.5), QPointF(cx - 2.0, cy + 3.5),
                QPointF(cx + 2.5, cy)]
        painter.drawPolygon(points)

    def _draw_cell(
            self, painter: QPainter, row: AttributeRow, clip: Clip,
            top: int, metrics: QFontMetrics) -> None:
        x1 = self._x_for(clip.start_ms)
        x2 = self._x_for(clip.end_ms)
        width = max(2, x2 - x1)
        value = row.value(clip)
        colour = QColor(row.colour(clip))
        # Dimmed, never hidden. The grid is positional - dropping plays
        # would either leave holes or compress time, and the whole reading
        # of when a player was leaned on depends on position being true.
        # Applied as a multiplier, because both branches below set alpha
        # outright and would otherwise discard it.
        dim = 1.0 if self._passes_player_filter(clip) else 0.2
        selected = clip.id == self._selected_clip_id

        cell = QRect(x1, top + 2, width - 1, ROW_H - 4)
        if row.ratio is not None:
            # A bar reads as a quantity at a glance; the number alone
            # makes you compare digits across nine columns.
            painter.setBrush(C_BAR_TRACK)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(cell, 3, 3)
            filled = QColor(colour)
            filled.setAlpha(int(170 * dim))
            painter.setBrush(filled)
            span = int(round((cell.width() - 2) * row.ratio(clip)))
            if span > 0:
                painter.drawRoundedRect(
                    QRect(cell.left() + 1, cell.top() + 1, span,
                          cell.height() - 2), 2, 2)
        else:
            fill = QColor(colour)
            fill.setAlpha(int((150 if value else 40) * dim))
            painter.setBrush(fill)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(cell, 3, 3)
        if selected:
            painter.setBrush(C_SELECT_WASH)
            painter.setPen(colour.lighter(150) if value else C_SELECTED)
            painter.drawRoundedRect(cell, 3, 3)

        if self._density != DENSITY_TEXT or not value:
            return
        room = width - CELL_PAD * 2
        if room < 12:
            return
        text_colour = QColor(C_CELL_TEXT if value else C_MUTED)
        if dim < 1.0:
            text_colour.setAlpha(int(text_colour.alpha() * dim))
        painter.setPen(text_colour)
        painter.drawText(
            QRect(x1 + CELL_PAD, top + 2, room, ROW_H - 4),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(value, Qt.TextElideMode.ElideRight, room))

    # ------------------------------------------------------ player filter

    def player_filter(self) -> str:
        return getattr(self, "_player_filter", "")

    def set_player_filter(self, key: str) -> None:
        """Show one player against the rest of the game, or clear it."""
        key = str(key or "")
        if key == self.player_filter():
            return
        self._player_filter = key
        self.playerFilterChanged.emit(key)
        self.update()

    def _passes_player_filter(self, clip: Clip) -> bool:
        wanted = self.player_filter()
        if not wanted:
            return True
        return _player_key(clip, getattr(self, "_roster", None)) == wanted

    def toggle_player_filter(self, clip: Clip) -> None:
        """Isolate this clip's player, or clear if they are already it."""
        key = _player_key(clip, getattr(self, "_roster", None))
        self.set_player_filter("" if key == self.player_filter() else key)

    # ----------------------------------------------------------- keyboard

    def cursor_cell(self) -> tuple[AttributeRow, Clip] | None:
        """The row and clip under the keyboard cursor, if it is on one."""
        if self._cursor is None:
            return None
        row_index, clip_index = self._cursor
        rows, clips = self._rows, self.visible_clips()
        if not (0 <= row_index < len(rows)) or not (
                0 <= clip_index < len(clips)):
            return None
        return rows[row_index], clips[clip_index]

    def set_cursor_cell(self, row_index: int, clip_index: int) -> None:
        rows, clips = self._rows, self.visible_clips()
        if not rows or not clips:
            self._cursor = None
            return
        self._cursor = (
            max(0, min(row_index, len(rows) - 1)),
            max(0, min(clip_index, len(clips) - 1)))
        if self._review_style:
            index = self._cursor[0]
            top = self._row_top(index)
            bottom = top + self._row_height(rows[index])
            offset = min(0, top - self._review_body_top() - ROW_GAP) or max(0, bottom - (self.height() - 32))
            self.lane_scroll.setValue(self.lane_scroll.value() + offset)
        self.update()

    def _move_cursor(self, d_row: int, d_clip: int) -> None:
        if self._cursor is None:
            # Start where the eye already is rather than at the origin.
            clips = self.visible_clips()
            start = next(
                (i for i, c in enumerate(clips)
                 if c.id == self._selected_clip_id), 0)
            self.set_cursor_cell(0, start)
        else:
            row_index, clip_index = self._cursor
            self.set_cursor_cell(row_index + d_row, clip_index + d_clip)
        cell = self.cursor_cell()
        if cell is not None and d_clip:
            # Moving across plays follows the selection, so the player and
            # inspector track the cursor without a second keystroke.
            self.clipActivated.emit(cell[1].id)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        moves = {
            Qt.Key.Key_Left: (0, -1), Qt.Key.Key_Right: (0, 1),
            Qt.Key.Key_Up: (-1, 0), Qt.Key.Key_Down: (1, 0),
        }
        if key in moves:
            self._move_cursor(*moves[key])
            event.accept()
            return
        cell = self.cursor_cell()
        if cell is None:
            super().keyPressEvent(event)
            return
        row, clip = cell
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if row.key == "people":
                # People is not editable here, so Enter does the thing you
                # actually want on a player: show only their plays.
                self.toggle_player_filter(clip)
            elif row.key in EDIT_CHOICES or row.key in EDIT_TYPED:
                self.cellEditRequested.emit(clip.id, row.key)
            event.accept()
            return
        # Digits pick straight off the focused row. One key per edit is
        # the whole reason to log here rather than in the panel.
        choices = EDIT_CHOICES.get(row.key)
        if choices and Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            index = key - Qt.Key.Key_1
            if index < len(choices):
                self.cellChoicePicked.emit(clip.id, row.key, index)
            event.accept()
            return
        if key == Qt.Key.Key_Escape:
            self._cursor = None
            self.update()
            event.accept()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        if not self.property("mapEditorOpen"):
            self._cursor = None
        self.update()

    def _paint_cursor(self, painter: QPainter) -> None:
        cell = self.cursor_cell()
        if cell is None or not (self.hasFocus() or self.property("mapEditorOpen")):
            return
        row, clip = cell
        row_index = self._rows.index(row)
        top = self._row_top(row_index)
        x1, x2 = self.clip_span(clip)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(DATA_GREEN), 2))
        painter.drawRoundedRect(
            QRectF(x1 - 1, top - 1, max(6.0, x2 - x1 + 2),
                   self._row_height(row) + 2), 3, 3)

    def editable_row_at(self, y: float) -> AttributeRow | None:
        row = self.row_at(y)
        if row is None or row.key in self._collapsed:
            return None
        return row if row.key in EDIT_CHOICES else None

    def mouseDoubleClickEvent(self, event) -> None:
        """Double-click a choice cell to change it.

        Single click already selects the play, so editing takes the
        second click rather than stealing the first.
        """
        x, y = event.position().x(), event.position().y()
        people = self.row_at(y)
        if (people is not None and people.key == "people"
                and not self.is_collapsed("people")):
            clip = self.clip_at(x, y)
            if clip is not None:
                self.toggle_player_filter(clip)
                event.accept()
                return
        row = self.editable_row_at(y)
        clip = self.clip_at(x, y) if row is not None else None
        if row is None or clip is None:
            super().mouseDoubleClickEvent(event)
            return
        self.cellEditRequested.emit(clip.id, row.key)
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        x, y = event.position().x(), event.position().y()
        if x < self.plot_left():
            row = self.row_at(y)
            if row is not None:
                # A hand toggle outranks the auto-empty rule for this row
                # from now on, whatever the data does.
                self.toggle_row(row.key)
                event.accept()
                return
            super().mousePressEvent(event)
            return
        clip = self.clip_at(x, y)
        if clip is not None:
            self.clipActivated.emit(clip.id)
            event.accept()
            return
        super().mousePressEvent(event)


    # V3 opts into this presentation on the already-wired grid. V2 retains
    # its original layout, density, editing, and auto-collapse behavior.
    def enable_review_style(self) -> None:
        if self._review_style:
            return
        self._review_style = True
        self._all_review_rows = tuple(
            replace(row, colour=self._people_colour) if row.key == "people" else row
            for row in REVIEW_ROWS)
        self._rows = self._all_review_rows
        self.setProperty("shellV3TagMap", True)
        self.setAccessibleName("Timeline Tag Map")
        self._collapsed = set()
        self._auto_empty.clear()
        self.toolbar = QWidget(self)
        self.toolbar.setObjectName("V3TagMapTools")
        tools = QHBoxLayout(self.toolbar)
        tools.setContentsMargins(12, 2, 10, 2)
        tools.setSpacing(8)
        tools.addWidget(QLabel("Color by:"))
        self.color_by = QComboBox(self.toolbar)
        self.color_by.setAccessibleName("Tag Map color dimension")
        self.color_by.setToolTip(
            "By lane uses each row's own meaning. Other choices compare one "
            "color dimension across populated rows; labels and saved tags stay unchanged.")
        for label, key in (("By lane", "lane"), ("Primary Tag", "primary_tag"),
                            ("Results", "result"), ("People", "people")):
            self.color_by.addItem(label, key)
        self.color_by.setFixedWidth(140)
        self.color_by.currentIndexChanged.connect(self._refresh_review_legend)
        tools.addWidget(self.color_by)
        tools.addStretch(1)
        self.heatmap_button = QPushButton("Game Heat Map", self.toolbar)
        self.heatmap_button.setAccessibleName("Export Game Heat Map")
        self.heatmap_button.setToolTip("Open the existing Game Heat Map export")
        tools.addWidget(self.heatmap_button)
        self.lane_scroll = QScrollBar(Qt.Orientation.Vertical, self)
        self.lane_scroll.setObjectName("V3TagMapLaneScroll")
        self.lane_scroll.setAccessibleName("Scroll Tag Map lanes")
        self.lane_scroll.setSingleStep(32)
        self.lane_scroll.valueChanged.connect(self.update)
        self.footer = QWidget(self)
        self.footer.setObjectName("V3TagMapLegend")
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(12, 0, 10, 0)
        footer.setSpacing(10)
        self.legend_scroll = QScrollArea(self.footer)
        self.legend_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.legend_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.legend_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.legend_scroll.setWidgetResizable(True)
        self.legend_scroll.setAccessibleName("Tag Map color legend")
        self.legend = QLabel()
        self.legend.setTextFormat(Qt.TextFormat.RichText)
        self.legend_scroll.setWidget(self.legend)
        footer.addWidget(self.legend_scroll, 1)
        self.totals = QLabel()
        footer.addWidget(self.totals)
        self._refresh_review_legend()
        self._apply_height()
        self._layout_review_controls()

    def set_hidden_review_rows(self, keys) -> None:
        """Remove hidden lanes from every layout, paint and keyboard index."""
        hidden = {key for key in keys if isinstance(key, str)} if isinstance(keys, (list, tuple, set)) else set()
        self._rows = tuple(row for row in self._all_review_rows if row.key not in hidden)
        self._collapsed.clear()
        self._cursor = None
        self.lane_scroll.setValue(0)
        self._apply_height()
        self._layout_review_controls()
        self._refresh_review_legend()

    def clip_span(self, clip: Clip) -> tuple[int, int]:
        return self._x_for(clip.start_ms), self._x_for(clip.end_ms)

    def _review_value(self, row: AttributeRow, clip: Clip) -> str:
        if row.key == "primary_tag":
            return " · ".join(dict.fromkeys(
                _clean(clip.details.get(key))
                for key in ("run_pass", "play_type", "play_action")
                if _clean(clip.details.get(key))))
        if row.key in {"result", "action"}:
            return " · ".join(split_results(clip.details.get(row.key, "")))
        if row.key == "people":
            names = {}
            for value in [_clean(clip.details.get("player_name")),
                          *split_players(clip.details.get("other_players", ""))]:
                key = canonical_tag_key(value)
                if key:
                    names.setdefault(key, value)
            quarterback = _clean(clip.details.get("quarterback"))
            if quarterback:
                key = canonical_tag_key(quarterback)
                unnumbered = strip_jersey_number(quarterback, [quarterback])
                # Only merge against a name already present; keep number-only role labels.
                name_key = canonical_tag_key(unnumbered)
                if key not in names and len(unnumbered.split()) >= 2 and name_key in names:
                    key = name_key
                names[key] = f"QB {quarterback}"
            return " · ".join(names.values())
        return row.value(clip)

    def _lane_color_entries(self, row: AttributeRow, clip: Clip) -> list[tuple[str, str]]:
        value = self._review_value(row, clip)
        if not value:
            return []
        field = getattr(self, "_field_surface", None)
        if field:
            if row.key == "quarter":
                return [(value, field.quarter_color(value))]
            if row.key == "result":
                return [(label, field.result_color(label))
                        for label in split_results(clip.details.get("result", ""))]
            if row.key in {"down", "notes"}:
                return [(value, "#71808e" if row.key == "down" else "#797b83")]
            if row.key == "action":
                return [(label, field.action_color(label))
                        for label in split_results(clip.details.get("action", ""))]
        if row.key == "result":
            from tapesift.services.heatmap_palette import result_colour
            return [(result, result_colour(result, self._review_tag_styles()))
                    for result in split_results(clip.details.get("result", ""))]
        if row.key == "primary_tag":
            return self._primary_color_entries(clip)
        if row.key == "people":
            from tapesift.services.heatmap_palette import player_colour
            return [(name, player_colour(name.removeprefix("QB "), self._roster))
                    for name in value.split(" · ")]
        if row.key == "quarter":
            from tapesift.services.heatmap_palette import quarter_colour
            from tapesift.services.heatmap_service import _quarter_of
            return [(value, quarter_colour(_quarter_of(clip)))]
        if row.key in {"down", "notes"}:
            return [(value, "#536b5f")]
        if row.key == "action":
            return [(action, "#78958c") for action in split_results(clip.details.get("action", ""))]
        return [(value, row.colour(clip))]

    def _review_tag_styles(self):
        provider = getattr(self, "tag_styles_provider", None)
        return provider() if provider else None

    def _primary_color_entries(self, clip: Clip) -> list[tuple[str, str]]:
        from tapesift.services.heatmap_palette import TYPE_COLORS
        from tapesift.services.tag_style_service import style_for_tag
        found = _play_type(clip.details)
        field = getattr(self, "_field_surface", None)
        if found:
            label = "RPO" if found[0] == "rpo" else found[1].title()
            if field:
                return [(label, field.play_colors[found[0]])]
            return [(label, str(style_for_tag(label, self._review_tag_styles())["color"])
                     or TYPE_COLORS[found[0]])]
        value = any(_clean(clip.details.get(key))
                    for key in ("run_pass", "play_type", "play_action"))
        return [("Other", "#677078" if field else "#89939b")] if value else []

    def color_entries(self, clip: Clip) -> list[tuple[str, str]]:
        mode = self.color_by.currentData()
        if mode == "result":
            from tapesift.services.heatmap_palette import result_colour
            field = getattr(self, "_field_surface", None)
            return [(value, field.result_color(value) if field else result_colour(value, self._review_tag_styles()))
                    for value in split_results(clip.details.get("result", ""))]
        if mode == "people":
            key = _player_key(clip, self._roster)
            return [(self._roster.get(key, key), self._people_colour(clip))] if key else []
        return self._primary_color_entries(clip)

    def _refresh_review_legend(self, *_args) -> None:
        result_mode = self.color_by.currentData() == "result"
        # Roll up legend names without collapsing the recorded result stripes.
        if self.color_by.currentData() == "lane":
            entries = dict.fromkeys(
                (f"{row.label}: " + (category_for("result", value)
                                    if row.key == "result" else value), color)
                for row in self._rows if row.key in {"primary_tag", "result", "people"}
                for clip in self._clips for value, color in self._lane_color_entries(row, clip))
        else:
            entries = dict.fromkeys(
                (category_for("result", value) if result_mode else value, color)
                for clip in self._clips for value, color in self.color_entries(clip))
        # An empty lane stays neutral even when its play has a known type.
        entries[("Unknown / empty", "#343b38")] = None
        text = "&nbsp;&nbsp; ".join(
            f'<span style="color:{color}">■</span>&nbsp;{escape(label)}'
            for label, color in entries)
        self.legend.setText(text)
        self.legend.setMinimumWidth(self.legend.sizeHint().width())
        self.legend.setToolTip(" · ".join(label for label, _color in entries))
        unlogged = sum(_review(clip) == "Unlogged" for clip in self._clips)
        self.totals.setText(f"{len(self._clips)} plays · {unlogged} unlogged")
        self.heatmap_button.setEnabled(bool(self._clips))
        self.update()

    def set_review_toolbar_external(self) -> None:
        """Release the tool row after V3 moves its live controls to the header."""
        if not self._review_style:
            return
        self._review_toolbar_height = 0
        self.toolbar.hide()
        self._apply_height()
        self._layout_review_controls()
        self.update()

    def _review_body_top(self) -> int:
        return self._review_toolbar_height + (32 if getattr(self, "_field_surface", None)
                                              else 28 if self._compact_review_ruler else 46)

    def _layout_review_controls(self) -> None:
        if not self._review_style:
            return
        self.toolbar.setGeometry(0, 0, self.width(), self._review_toolbar_height)
        self.footer.setGeometry(0, self.height() - 32, self.width(), 32)
        height = max(0, self.height() - self._review_body_top() - 32)
        self.lane_scroll.setGeometry(self.width() - 12, self._review_body_top(), 12, height)
        self.lane_scroll.setPageStep(height)
        total = sum(self._row_height(row) + ROW_GAP for row in self._rows) + ROW_GAP
        self.lane_scroll.setRange(0, max(0, total - height))
        self.lane_scroll.setVisible(total > height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_review_controls()

    def wheelEvent(self, event) -> None:
        if self._review_style and event.angleDelta().y():
            self.lane_scroll.setValue(self.lane_scroll.value() - event.angleDelta().y() // 4)
            event.accept()
            return
        super().wheelEvent(event)

    def tooltip_for_clip(self, clip: Clip) -> str:
        values = [f"Play {clip.clip_number:03d} · {format_ms(clip.start_ms)}"]
        values.extend(f"{row.label}: {self._review_value(row, clip) or 'Unknown'}"
                      for row in self._rows if row.key != "confidence" or _confidence(clip))
        primary = _clean(clip.details.get("player_name"))
        if primary:
            values.append(f"Primary Player: {primary}")
        values.extend(f"{key.replace('_', ' ').title()}: {value}"
                      for key, value in clip.details.items()
                      if "player" in key and key != "player_name" and value)
        return "\n".join(values)

    def mouseMoveEvent(self, event) -> None:
        if self._review_style:
            clip = self.clip_at(event.position().x(), event.position().y())
            self.setCursor(Qt.CursorShape.PointingHandCursor if clip else Qt.CursorShape.ArrowCursor)
            if clip:
                QToolTip.showText(event.globalPosition().toPoint(), self.tooltip_for_clip(clip), self)
            else:
                row = self.row_at(event.position().y())
                if row and event.position().x() < self.plot_left():
                    QToolTip.showText(event.globalPosition().toPoint(), row.label, self)
                else:
                    QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._review_style:
            QToolTip.hideText()
        super().leaveEvent(event)

    def _draw_row_icon(self, painter: QPainter, key: str, x: float, y: float) -> None:
        painter.save()
        painter.translate(x, y)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#94a29a"), 1.1))
        if key in {"quarter", "down", "notes"}:
            painter.drawRoundedRect(QRectF(1, 1, 12, 12), 1, 1)
            for line in ((5,) if key == "quarter" else (4, 7, 10)):
                painter.drawLine(QPointF(3, line), QPointF(11, line))
        elif key == "primary_tag":
            painter.drawPolygon([QPointF(1, 1), QPointF(7, 1), QPointF(13, 7), QPointF(7, 13), QPointF(1, 7)])
            painter.drawEllipse(QPointF(4, 4), 1, 1)
        elif key == "people":
            painter.drawEllipse(QPointF(5, 4), 2, 2)
            painter.drawEllipse(QPointF(11, 5), 1.5, 1.5)
            painter.drawArc(QRectF(1, 8, 8, 8), 0, 180 * 16)
            painter.drawArc(QRectF(8, 9, 6, 6), 0, 180 * 16)
        elif key == "review":
            painter.drawEllipse(QRectF(1, 1, 12, 12))
            painter.drawPolyline([QPointF(4, 7), QPointF(6, 9), QPointF(10, 5)])
        elif key == "confidence":
            for i in range(3):
                painter.drawLine(QPointF(3 + 4 * i, 12), QPointF(3 + 4 * i, 9 - 3 * i))
        else:
            painter.drawPolygon([QPointF(7, 0), QPointF(9, 5), QPointF(14, 7), QPointF(9, 9),
                                 QPointF(7, 14), QPointF(5, 9), QPointF(0, 7), QPointF(5, 5)])
        painter.restore()

    def quarter_spans(self, *, join_clip_gaps: bool = False) -> list[tuple[int, int, str]]:
        spans = []
        previous = ""
        clips = sorted(self._clips, key=lambda c: c.start_ms) if self._review_style else self._clips
        for clip in clips:
            quarter = _quarter(clip)
            if self._review_style:
                from tapesift.services.heatmap_service import _quarter_of
                quarter = _quarter_of(clip)
                if quarter == "?":
                    quarter = ""
            if quarter:
                if spans and previous == quarter and (join_clip_gaps or clip.start_ms <= spans[-1][1]):
                    spans[-1] = (spans[-1][0], max(spans[-1][1], clip.end_ms), quarter)
                else:
                    spans.append((clip.start_ms, clip.end_ms, quarter))
            previous = quarter
        return spans

    def _draw_ink_cell(self, painter, rect, value, entries, visible, split_labels,
                       primary=False, selected=False):
        """Keep tiny duration cells colored; reveal each member when zoom allows."""
        entries = entries or [("", "#677078" if getattr(self, "_field_surface", None) else "#252e29")]
        widths = [painter.fontMetrics().horizontalAdvance(label) + 14 for label, _ in entries]
        horizontal = split_labels and rect.width() >= sum(widths)
        painter.save()
        painter.setClipRect(rect, Qt.ClipOperation.IntersectClip)
        # Filtering must not let the football field bleed through saved tags.
        painter.setOpacity(1)
        if getattr(self, "_field_surface", None):
            # Flat semantic fills stay opaque over the field and its watermark.
            segmented = len(entries) > 1 and rect.width() >= 42 * len(entries)
            painter.fillRect(rect, QColor(entries[0][1]).darker(115 if primary else 145))
            for part, (label, color) in enumerate(entries):
                if segmented:
                    cell = QRectF(rect.x() + rect.width()*part/len(entries), rect.y(),
                                  rect.width()/len(entries), rect.height())
                    painter.fillRect(cell, QColor(color).darker(145))
                    painter.fillRect(QRectF(cell.x(), cell.y(), 3, cell.height()), QColor(color))
                    painter.setPen(C_CELL_TEXT)
                    text = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight,
                                                           max(0, int(cell.width()) - 10))
                    painter.drawText(cell.adjusted(6, 0, -3, 0), Qt.AlignmentFlag.AlignVCenter, text)
                else:
                    painter.fillRect(QRectF(rect.x(), rect.y() + rect.height()*part/len(entries),
                                           min(3, rect.width()), rect.height()/len(entries)), QColor(color))
            painter.setPen(QPen(QColor("#d8e1e8" if selected else "#394650"), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect.adjusted(.5, .5, -.5, -.5))
            if rect.width() >= 30 and not segmented:
                text = painter.fontMetrics().elidedText(value or "—", Qt.TextElideMode.ElideRight,
                                                       max(0, int(rect.width()) - 12))
                painter.setPen(C_CELL_TEXT if value else C_MUTED)
                painter.drawText(rect.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, text)
            if visible and self.player_filter():
                painter.fillRect(QRectF(rect.left(), rect.bottom()-1, rect.width(), 1), QColor("#b6d0c6"))
            painter.restore()
            return
        for part, (label, color) in enumerate(entries):
            if horizontal:
                x = rect.x() + rect.width() * sum(widths[:part]) / sum(widths)
                cell = QRectF(x, rect.y(), rect.width() * widths[part] / sum(widths) - 1, rect.height())
            else:
                cell = QRectF(rect.x(), rect.y() + rect.height() * part / len(entries),
                              rect.width(), rect.height() / len(entries))
            shade = QColor(color)
            gradient = QLinearGradient(cell.topLeft(), cell.bottomLeft())
            top_color, bottom_color = shade.darker(235), shade.darker(310)
            top_color.setAlpha(255)
            bottom_color.setAlpha(255)
            gradient.setColorAt(0, top_color)
            gradient.setColorAt(1, bottom_color)
            painter.fillRect(cell, gradient)
            painter.fillRect(QRectF(cell.x(), cell.y(), cell.width(), 1), shade)
            if horizontal:
                painter.setPen(C_CELL_TEXT)
                painter.drawText(cell.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, label)
        if not horizontal and rect.width() >= 30:
            text = painter.fontMetrics().elidedText(value or "—", Qt.TextElideMode.ElideRight,
                                                   max(0, int(rect.width()) - 12))
            # A solid neutral text backing keeps multiple thin result stripes readable.
            if len(entries) > 1:
                painter.fillRect(rect.adjusted(3, 2, -3, -2), QColor(8, 12, 10, 185))
            painter.setPen(C_CELL_TEXT if value else C_MUTED)
            painter.drawText(rect.adjusted(6, 0, -4, 0), Qt.AlignmentFlag.AlignVCenter, text)
        if visible and self.player_filter():
            painter.fillRect(QRectF(rect.left(), rect.bottom()-1, rect.width(), 1),
                             QColor("#b6d0c6"))
        painter.restore()

    def _paint_review(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        accent = QColor(self.property("reviewAccent") or DATA_GREEN)
        painter.fillRect(self.rect(), QColor("#080c0a"))
        font = QFont(["IBM Plex Sans", "Segoe UI"])
        font.setPixelSize(13)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        by_lane = self.color_by.currentData() == "lane"
        toolbar_height = self._review_toolbar_height
        body_top = self._review_body_top()
        painter.setPen(QColor("#b4b9b7"))
        compact = self._compact_review_ruler
        if self._review_labels_visible:
            painter.drawText(QRect(12, toolbar_height, 135, body_top-toolbar_height),
                             Qt.AlignmentFlag.AlignVCenter, "TAG MAP" if compact else "TIMELINE")
        start, end = self._visible_range
        if end > start:
            # Only saved quarter spans earn a label. Gaps remain unknown.
            spans = [] if compact else self.quarter_spans()
            for begin, finish, quarter in spans:
                x1, x2 = self._x_for(begin), self._x_for(finish)
                painter.fillRect(QRect(x1, toolbar_height + 1, max(1, x2 - x1), 19), QColor("#1c2924"))
                if x2 - x1 >= painter.fontMetrics().horizontalAdvance(quarter) + 4:
                    painter.drawText(QRect(x1, toolbar_height + 1, x2 - x1, 19), Qt.AlignmentFlag.AlignCenter, quarter)
            intervals = max(1, self.plot_width() // 130)
            for index in range(intervals + 1):
                timestamp = start + (end - start) * index // intervals
                x = self._x_for(timestamp)
                label = format_ms(timestamp).split(".")[0]
                width = painter.fontMetrics().horizontalAdvance(label)
                left = max(self.plot_left(), min(x - width // 2, self.width() - width - 14))
                if compact and left + width > self.width() - 80:
                    continue
                painter.drawText(QRect(left, toolbar_height + (1 if compact else 21), width + 2, 23), Qt.AlignmentFlag.AlignVCenter, label)
                painter.setPen(QColor("#303635"))
                painter.drawLine(x, body_top - 3, x, body_top + 1)
                painter.setPen(QColor("#b4b9b7"))
        else:
            painter.drawText(QRect(self.plot_left(), toolbar_height + 1,
                                  max(0, self.plot_width() - (66 if compact else 0)),
                                  26 if compact else 44), Qt.AlignmentFlag.AlignVCenter, "Open film to see play positions")
        body = QRect(0, body_top, self.width() - 12, max(0, self.height() - body_top - 32))
        painter.save()
        field = getattr(self, "_field_surface", None)
        if field:
            # The retired legend band provides a clear, noninteractive yard-number lane.
            field.paint(painter, QRect(self.plot_left(), body.top(), self.plot_width(), body.height()+32))
        painter.setClipRect(body)
        clips = self.visible_clips()
        # Lanes share positions and colors. Resolve them once per paint;
        # resizing used to repeat this work for every row of the game.
        marks = [(clip, *self.clip_span(clip), [] if by_lane else self.color_entries(clip),
                  self._passes_player_filter(clip)) for clip in clips]
        selected = next((c for c in clips if c.id == self._selected_clip_id), None)
        if selected:
            left, right = self.clip_span(selected)
            band = QRect(left - 3, body.top(), right - left + 6, body.height())
            # V3 review sets reviewAccent; over the turf field the old 4%
            # wash and grey edges vanished, so the selected play was only
            # findable by its triangles. V2 keeps its original treatment.
            review = bool(self.property("reviewAccent"))
            band_color = QColor(accent) if review else QColor(87, 201, 138)
            band_color.setAlpha(0 if field else 32 if review else 10)
            painter.fillRect(band, band_color)
            painter.setPen(QColor("#74828d") if field else QColor(accent) if review else QColor("#b6baac"))
            painter.drawLine(band.left(), body.top(), band.left(), body.bottom())
            painter.drawLine(band.right(), body.top(), band.right(), body.bottom())
        for index, row in enumerate(self._rows):
            top, height = self._row_top(index), self._row_height(row)
            if top + height < body.top() or top > body.bottom():
                continue
            painter.fillRect(QRect(0, top, self.plot_left(), height), QColor("#0b100d"))
            painter.setPen(QColor("#292e2c"))
            painter.drawLine(0, top + height, self.width(), top + height)
            if self._review_labels_visible:
                self._draw_chevron(painter, 7, top, height, row.key not in self._collapsed)
                self._draw_row_icon(painter, row.key, 29, top + (height - 14) / 2)
                painter.setPen(QColor("#bfc6c2"))
                painter.drawText(QRect(49, top, self.plot_left() - 52, height), Qt.AlignmentFlag.AlignVCenter,
                                 painter.fontMetrics().elidedText(row.label, Qt.TextElideMode.ElideRight, self.plot_left() - 52))
            if row.key in self._collapsed:
                continue
            for clip, left, right, clip_entries, passes_filter in marks:
                value = self._review_value(row, clip)
                if (row.key == "notes" and not clip.notes
                        or row.key == "action" and not value):
                    continue
                entries = (self._lane_color_entries(row, clip) if by_lane else clip_entries) if value else []
                inset = 2 if field else 5
                self._draw_ink_cell(painter, QRectF(left, top + inset, max(2, right-left-2),
                                                    max(3, height-2*inset)), value, entries,
                                    passes_filter, by_lane and row.key in {"result", "people"},
                                    primary=row.key == "primary_tag", selected=clip is selected)
            if field and row.key == "quarter":
                field.paint_pylons(painter, self, top, height)
        painter.setPen(QColor("#303532"))
        painter.drawLine(self.plot_left(), body.top(), self.plot_left(), body.bottom())
        self._paint_cursor(painter)
        painter.restore()
        if selected and body.height() and not field:
            left, right = self.clip_span(selected)
            center = (left + right) / 2
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawPolygon([QPointF(center - 5, body_top - 2), QPointF(center + 5, body_top - 2), QPointF(center, body_top + 4)])
            painter.drawPolygon([QPointF(center - 5, body.bottom() + 2), QPointF(center + 5, body.bottom() + 2), QPointF(center, body.bottom() - 4)])
        painter.end()
