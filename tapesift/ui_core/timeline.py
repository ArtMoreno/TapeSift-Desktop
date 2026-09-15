"""Film timeline: scrubber, clip blocks, in/out flags, minute ticks.

Drawn entirely by hand rather than styling a QSlider, so every pixel earns
its place in a compact strip.

Performance matters here more than anywhere else in the app: the playhead
moves ~60x a second, and repainting a full-width widget that often is
exactly how scrubbing regressions creep in. So the static parts (groove,
ticks, clip blocks, marks) are cached into a pixmap that is only rebuilt
when something actually changes, and playhead movement repaints just the
narrow strips around the old and new positions.

It mimics the slice of the QSlider API the player uses, so it can be
dropped in without changing call sites.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QPolygon
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QWidget

from tapesift.core.perf import PerfTimer

from tapesift.services import result_service
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_core.timeline_viewport import SourceTimeViewport
from tapesift.ui_core.timeline_variants import COMPACT, DUAL, FOCUS

HEIGHT = 58
PERIOD_TOP = 1
PERIOD_H = 17
GROOVE_TOP = 25
GROOVE_H = 9
BAND_TOP = 43
BAND_H = 18
# A project with no quarter markers used to reserve the quarter rail anyway,
# so the strip opened with a band of empty black above the groove. The rail
# now collapses and everything below it moves up by exactly its height.
RAIL_RESERVE = PERIOD_H + 5
BAND_BOTTOM_PAD = 5
# A slim rail under the lanes showing which footage a play
# claims. Amber gaps are the film nothing has been cut from.
UNCLAIMED_RAIL_H = 5
UNCLAIMED_RAIL_GAP = 3

# How much horizontal room one play needs before a surface aligned to
# this timeline can put words in it. Below TEXT it falls back to colour
# chips; below CHIP even a chip is a sliver and the row is a heat strip.
CELL_TEXT_MIN_PX = 76
CELL_CHIP_MIN_PX = 14
#: Part-plays at the left and right edges, counted as visible.
EDGE_PARTIAL_CELLS = 3.0

# Dragging the playhead past either edge pans the film rather than
# stopping dead. The rate is a fraction of the visible span per second,
# so it stays proportional at any zoom - fine at high zoom where a frame
# matters, quick at low zoom where distance does. Pushing further past
# the edge ramps it up to EDGE_SCROLL_MAX_BOOST, which is the difference
# between nudging and crossing a whole game.
EDGE_SCROLL_INTERVAL_MS = 32
EDGE_SCROLL_BASE_FRACTION = 0.45
EDGE_SCROLL_RAMP_PX = 140.0
EDGE_SCROLL_MAX_BOOST = 4.0
DENSITY_TEXT = "text"
DENSITY_CHIP = "chip"
DENSITY_SPARSE = "sparse"
# Overlapping plays are stacked instead of painted on top of each other. A
# film with no overlaps uses exactly one lane and the original widget height.
LANE_GAP = 2
MAX_LANES = 3
PLAYHEAD_W = 3
PLAYHEAD_HIT_RADIUS = 10
DRAG_START_PX = 4
SNAP_TOLERANCE_PX = 8
TRIM_HANDLE_HIT_RADIUS = 7
MIN_TRIM_DURATION_MS = 250
# Below this pixel distance two neighbouring blocks would touch, so an
# explicit separator column is drawn to keep separate plays readable.
BLOCK_SEPARATOR_PX = 1
DETECTED_CAP_W = 2
# Adaptive per-block decoration. At the full-film zoom on a real project the
# blocks fall below ten pixels; a keyline + dark bottom + detected cap on
# each fills the interior with texture that hides the colour underneath.
# Above BLOCK_FULL_DETAIL_PX we draw everything; between the two thresholds
# we draw only the top keyline; below BLOCK_KEYLINE_MIN_PX we draw the raw
# block. The exact conflicts show as an amber underline, not a hatch, so the
# overlap zones do not stack pattern on top of pattern.
BLOCK_KEYLINE_MIN_PX = 12
BLOCK_FULL_DETAIL_PX = 24
CONFLICT_UNDERLINE_H = 1
# The film's total duration is rendered immediately right of this widget.
LABEL_RIGHT_MARGIN = 44
# Frame ticks only appear once frames are far enough apart to read.
MIN_FRAME_TICK_PX = 5

C_BG = QColor("#191a17")
C_GROOVE = QColor("#34352e")
C_ELAPSED = QColor("#5b594d")
C_TICK = QColor("#4b4a40")
C_TICK_TEXT = QColor("#8f8a78")
C_BLOCK = QColor("#9aa3a6")
C_BLOCK_SEL = QColor("#f2f5f2")
C_PLAYHEAD = QColor("#ffffff")
C_IN = QColor("#6ddc82")
C_OUT = QColor("#e8836f")
C_PERIOD_A = QColor("#182019")
C_PERIOD_B = QColor("#202a22")
C_PERIOD_LINE = QColor("#4b5d50")
C_PERIOD_TEXT = QColor("#b9c8bd")
C_CONFLICT = QColor("#ffd166")
C_FOCUS_BG = QColor("#18221c")
C_FOCUS_EDGE = QColor("#39e07a")
C_SOURCE_BASE = QColor("#23282a")
C_COVERAGE_PLAY = QColor("#2f7650")
C_COVERAGE_REVIEW = QColor("#9b7635")
C_COVERAGE_MISSED = QColor("#d89d3d")
C_COVERAGE_MISSED_HIGH = QColor("#f0b44b")
C_COVERAGE_MISSED_LOW = QColor("#a67836")
C_COVERAGE_SEPARATOR = QColor("#090b0a")
C_COVERAGE_RESOLVED = QColor("#54615a")
C_PREDICTED_SNAP = QColor("#77e69d")
C_PREDICTED_SNAP_LOW = QColor("#e0b341")

STACKED = "stacked"
TIMELINE_LAYOUTS = (STACKED, COMPACT, DUAL, FOCUS)
FOCUS_WINDOW_MIN_MS = 90_000
FOCUS_LENS_LEFT_RATIO = 0.28
FOCUS_LENS_RIGHT_RATIO = 0.72
COMPACT_EXPAND_ZOOM = 8.0
COVERAGE_KINDS = frozenset({
    "play", "review", "possible_missed", "separator",
})

# Semantic play colours tuned to stay legible in the brighter source rail.
# Saturation separates adjacent plays while the one-pixel keyline and white
# selection treatment continue to carry the editing hierarchy.
# Unlabelled clips intentionally stay neutral gray.
PLAY_COLORS = {
    "run": QColor("#3cc879"),
    "pass": QColor("#4d9de8"),
    "rpo": QColor("#42c5ad"),
    "penalty": QColor("#d9b43b"),
    "sack": QColor("#ddb33f"),
    "interception": QColor("#df6678"),
    "touchdown": QColor("#e779b8"),
}

PLAY_LABELS = {
    "run": "Run",
    "pass": "Pass",
    "rpo": "RPO",
    "penalty": "Penalty",
    "sack": "Sack",
    "interception": "Interception",
    "touchdown": "Touchdown",
}

# Stable display order for the legend. Text labels accompany every swatch so
# the timeline never relies on colour alone.
PLAY_LEGEND = (
    ("run", PLAY_LABELS["run"], PLAY_COLORS["run"]),
    ("pass", PLAY_LABELS["pass"], PLAY_COLORS["pass"]),
    ("rpo", PLAY_LABELS["rpo"], PLAY_COLORS["rpo"]),
    ("penalty", PLAY_LABELS["penalty"], PLAY_COLORS["penalty"]),
    ("sack", PLAY_LABELS["sack"], PLAY_COLORS["sack"]),
    ("interception", PLAY_LABELS["interception"],
     PLAY_COLORS["interception"]),
    ("touchdown", PLAY_LABELS["touchdown"], PLAY_COLORS["touchdown"]),
    ("", "Unlabelled", C_BLOCK),
)

# The selector is intentionally a presentation setting. It changes the colour
# used for each block, never the clip data, playhead, or playback engine.
TIMELINE_COLOR_MODES = (
    ("play_type", "Play Type"),
    ("result", "Result"),
    ("primary_tag", "Primary Tag"),
    ("personnel", "Personnel"),
    ("review_status", "Review Status"),
)
TIMELINE_COLOR_MODE_LABELS = dict(TIMELINE_COLOR_MODES)

# A restrained qualitative palette for project-defined values such as tags and
# personnel groupings. Assignment is alphabetical, so the same project produces
# the same key every time it is opened.
DYNAMIC_COLORS = tuple(QColor(value) for value in (
    "#3cc879", "#4d9de8", "#9b6ed6", "#ddb33f",
    "#df6678", "#45b7c7", "#8aa958", "#cf718d",
    "#cfa64c", "#6d9ee2", "#a47bd5", "#42c5ad",
))

REVIEW_LABELS = {
    "logged": "Logged",
    "unlogged": "Unlogged",
    "needs_fix": "Needs Fix",
    "excluded": "Excluded",
}
REVIEW_COLORS = {
    # Block fill is data, not an action, so it takes the softer green rather
    # than the brand token used by primary buttons and focus.
    "logged": QColor("#57c98a"),
    "unlogged": QColor("#8e9b91"),
    "needs_fix": QColor("#f0c46a"),
    "excluded": QColor("#59615b"),
}
REVIEW_LEGEND = tuple(
    (key, REVIEW_LABELS[key], REVIEW_COLORS[key])
    for key in ("logged", "unlogged", "needs_fix", "excluded")
)


@dataclass(frozen=True)
class TimelineBlock:
    start_ms: int
    end_ms: int
    selected: bool = False
    clip_id: str = ""
    kind: str = ""
    title: str = ""
    colour: str = ""
    category_label: str = ""
    # True for plays that came from automatic detection. Presentation only:
    # it draws a shape cue, never a different colour.
    detected: bool = False
    # A machine estimate inside the play. It is a seek/snap bookmark only;
    # clip boundaries and human marks remain authoritative.
    predicted_snap_ms: int | None = None
    snap_confidence: float | None = None
    snap_eligible: bool = False
    # Assigned by the widget, never by the caller. Kept on the block so hit
    # testing and painting agree on exactly one layout.
    lane: int = 0


@dataclass(frozen=True)
class TimelineCoverage:
    """Read-only detector disposition painted beneath editable clip blocks."""

    start_ms: int
    end_ms: int
    kind: str
    reason: str = ""
    candidate_kind: str = ""
    candidate_index: int | None = None
    review_status: str = ""
    audit_level: str = ""


def block_contains(block: TimelineBlock, position_ms: int) -> bool:
    """Half-open containment: a shared boundary belongs to the later play."""
    return block.start_ms <= position_ms < max(
        block.start_ms + 1, block.end_ms)


def blocks_overlap(first: TimelineBlock, second: TimelineBlock) -> bool:
    """Half-open overlap, so two plays that merely abut do not conflict."""
    return first.start_ms < second.end_ms and second.start_ms < first.end_ms


def assign_lanes(
        blocks: list[TimelineBlock],
        ) -> tuple[list[int], tuple[tuple[int, int], ...]]:
    """Pack plays into overlap lanes and report the conflicting ranges.

    Two plays that share a boundary are not in conflict and stay in the same
    lane, so an ordinary film keeps its single-row timeline. Only genuine
    overlaps are stacked, because a play hidden behind another play is the
    one failure this widget must never produce.
    """
    lanes = [0] * len(blocks)
    if not blocks:
        return lanes, ()

    order = sorted(
        range(len(blocks)),
        key=lambda index: (blocks[index].start_ms, blocks[index].end_ms,
                           index),
    )
    lane_ends: list[int] = []
    open_blocks: list[tuple[int, int]] = []  # (end_ms, lane)
    raw_conflicts: list[tuple[int, int]] = []

    for index in order:
        block = blocks[index]
        start = block.start_ms
        end = max(block.start_ms, block.end_ms)

        open_blocks = [item for item in open_blocks if item[0] > start]
        busy = {lane for _end, lane in open_blocks}
        for other_end, _lane in open_blocks:
            upper = min(end, other_end)
            if upper > start:
                raw_conflicts.append((start, upper))

        lane = next(
            (candidate for candidate, lane_end in enumerate(lane_ends)
             if lane_end <= start and candidate not in busy),
            None,
        )
        if lane is None:
            if len(lane_ends) < MAX_LANES:
                lane_ends.append(end)
                lane = len(lane_ends) - 1
            else:
                # More than MAX_LANES simultaneous plays is pathological. Keep
                # every block visible in the last lane rather than dropping it.
                lane = min(range(MAX_LANES), key=lambda item: lane_ends[item])
        lane_ends[lane] = max(lane_ends[lane], end)
        lanes[index] = lane
        open_blocks.append((end, lane))

    merged: list[tuple[int, int]] = []
    for low, high in sorted(raw_conflicts):
        if merged and low <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return lanes, tuple(merged)


def classify_play_kind(details: dict[str, str], tags: list[str],
                       title: str = "", label: str = "") -> str:
    """Return the timeline category encoded by a clip's football metadata.

    Penalty and RPO are explicit analyst classifications, so they outrank
    inferred text. Existing scoring/negative-event precedence then applies,
    followed by the ordinary run/pass family.
    """
    values = [str(v) for v in details.values() if v]
    text = " ".join([*values, *tags, title, label]).lower()

    structured_penalty = " ".join((
        details.get("result", ""),
        details.get("highlight", ""),
    )).casefold()
    if re.search(r"\bpenalt(?:y|ies)\b", structured_penalty):
        return "penalty"

    play_type = details.get("play_type", "").strip().casefold()
    run_pass = details.get("run_pass", "").strip().casefold()
    if re.search(r"\brpo\b", play_type) or run_pass == "rpo":
        return "rpo"

    if re.search(r"\b(touchdown|pass td|rushing td|receiving td)\b", text):
        return "touchdown"
    if re.search(r"\b(interception|intercepted|pick six|pick-6)\b", text):
        return "interception"
    if re.search(r"\bsack(?:ed)?\b", text):
        return "sack"

    # Prefer the structured Run / Pass field over incidental words in names.
    if run_pass == "run":
        return "run"
    if run_pass == "pass":
        return "pass"

    if re.search(r"\b(run|rush|rushing)\b", text):
        return "run"
    if re.search(r"\b(pass|passing|completion|incompletion|screen)\b", text):
        return "pass"
    return ""


def timeline_category(
        mode: str,
        details: dict[str, str],
        tags: list[str],
        title: str = "",
        label: str = "",
        *,
        enabled: bool = True,
        needs_fix: bool = False,
) -> tuple[str, str]:
    """Return the stable key and human label used by a timeline colour mode."""
    if mode == "play_type":
        key = classify_play_kind(details, tags, title, label)
        return key, PLAY_LABELS.get(key, "Unlabelled")

    if mode == "review_status":
        if not enabled:
            key = "excluded"
        elif needs_fix:
            key = "needs_fix"
        elif details:
            key = "logged"
        else:
            key = "unlogged"
        return key, REVIEW_LABELS[key]

    if mode == "result":
        value = result_service.primary_result(details.get("result", ""))
    elif mode == "primary_tag":
        value = next((tag for tag in tags if tag.strip()), "")
    elif mode == "personnel":
        # Offensive personnel is the common film-analysis grouping. Projects
        # that only log defensive personnel still receive a useful category.
        value = (
            details.get("off_personnel", "")
            or details.get("personnel", "")
            or details.get("def_personnel", "")
        )
    else:
        value = ""

    label_text = " ".join(str(value).strip().split())
    if not label_text:
        return "", "Unlabelled"
    key = re.sub(r"[^a-z0-9]+", "_", label_text.casefold()).strip("_")
    return key, label_text


def build_timeline_legend(
        mode: str,
        categories: dict[str, str],
        color_overrides: dict[str, str] | None = None,
) -> tuple[tuple[str, str, QColor], ...]:
    """Build a deterministic key for the active project and colour mode."""
    if mode == "play_type":
        return PLAY_LEGEND
    if mode == "review_status":
        semantic_entries = tuple(
            (key, PLAY_LABELS[key], PLAY_COLORS[key])
            for key in ("rpo", "penalty")
            if key in categories
        )
        return (*REVIEW_LEGEND, *semantic_entries)

    entries: list[tuple[str, str, QColor]] = []
    labelled = sorted(
        ((key, label) for key, label in categories.items() if key),
        key=lambda item: item[1].casefold(),
    )
    for index, (key, label) in enumerate(labelled):
        override = QColor((color_overrides or {}).get(key, ""))
        color = override if override.isValid() else \
            DYNAMIC_COLORS[index % len(DYNAMIC_COLORS)]
        entries.append((key, label, color))

    # Always explain gray: it makes sparse or not-yet-logged projects legible,
    # and avoids a key that looks broken before any metadata is entered.
    entries.append(("", categories.get("", "Unlabelled"), C_BLOCK))
    return tuple(entries)


def period_label(index: int) -> str:
    """Football period label for a zero-based film segment."""
    if index < 4:
        return f"Q{index + 1}"
    return "OT" if index == 4 else f"{index - 3}OT"


class Timeline(QWidget):
    sliderMoved = Signal(int)
    sliderPressed = Signal()
    sliderReleased = Signal()
    wheel_seek = Signal(int)
    wheel_frame = Signal(int)
    wheel_zoom = Signal(int, int)
    blockActivated = Signal(str, int)
    contextRequested = Signal(str, int, QPoint)
    trimStarted = Signal(str, str, int)
    trimPreview = Signal(str, str, int)
    trimFinished = Signal(str, str, int)
    coverageActivated = Signal(int, int, str)
    #: A click on the unclaimed rail: (start_ms, end_ms) of that gap.
    unclaimedActivated = Signal(int, int)
    #: DENSITY_TEXT / DENSITY_CHIP / DENSITY_SPARSE, when it changes.
    cellDensityChanged = Signal(str)
    visibleRangeChanged = Signal(int, int)

    def __init__(self, parent=None, variant: str = STACKED) -> None:
        super().__init__(parent)
        self.setFixedHeight(HEIGHT)
        self.setMouseTracking(True)
        self._variant = variant if variant in TIMELINE_LAYOUTS else STACKED
        self._viewport = SourceTimeViewport()
        self._pressed = False
        self._pending_block: TimelineBlock | None = None
        self._press_x = 0.0
        self._press_y = 0.0
        self._snapping_enabled = True
        self._trim_original: TimelineBlock | None = None
        self._trim_edge = ""
        self._blocks: list[TimelineBlock] = []
        self._coverage_segments: list[TimelineCoverage] = []
        self._cell_density = DENSITY_SPARSE
        self._edge_scroll_px = 0.0
        self._edge_scroll_timer = QTimer(self)
        self._edge_scroll_timer.setInterval(EDGE_SCROLL_INTERVAL_MS)
        self._edge_scroll_timer.timeout.connect(self._edge_scroll_step)
        self._show_ignored_fragments = False
        self._lane_count = 1
        self._conflicts: tuple[tuple[int, int], ...] = ()
        self._frame_duration_ms = 1000 / 30
        self._period_markers: tuple[int, ...] = ()
        self._period_rail_visible = True
        self._quarter_pylons: tuple[tuple[int, str], ...] = ()
        self._ink_blocks = False
        self._in_ms: int | None = None
        self._out_ms: int | None = None
        self._focus_range: tuple[int, int] | None = None
        self._static: QPixmap | None = None

    def timeline_variant(self) -> str:
        return self._variant

    def set_timeline_variant(self, variant: str) -> None:
        """Switch presentation without touching any source or clip data."""
        normalized = variant if variant in TIMELINE_LAYOUTS else STACKED
        if normalized == self._variant:
            return
        self._variant = normalized
        self._update_focus_range()
        self._apply_lane_height()
        self._invalidate()

    # ---------- QSlider-compatible surface ----------

    def setRange(self, minimum: int, maximum: int) -> None:
        old_visible = self.visible_range()
        self._viewport.set_source_range(minimum, maximum)
        self._update_focus_range()
        self._apply_lane_height()
        self._invalidate()
        self._emit_visible_range_if_changed(old_visible)

    def minimum(self) -> int:
        return self._min

    def maximum(self) -> int:
        return self._max

    def value(self) -> int:
        return self._value

    def setValue(self, value: int) -> None:
        """Move the playhead, repainting only the strips it touches."""
        value = self._viewport.clamp_time(value)
        if value == self._value:
            return
        old_period = self._period_index_for(self._value)
        old_x = self._x_for(self._value)
        old_visible = self.visible_range()
        _playhead_changed, viewport_changed = \
            self._viewport.set_playhead(value)
        if viewport_changed:
            self._invalidate()
            self._emit_visible_range_if_changed(old_visible)
            return
        new_x = self._x_for(value)
        pad = PLAYHEAD_W + 6
        left, right = sorted((old_x, new_x))
        self.update(QRect(
            left - pad, 0, (right - left) + pad * 2, self.height()))
        if old_period != self._period_index_for(value):
            # The white current-period outline is a non-colour cue and spans
            # the whole segment, so repaint the rail when a boundary is crossed.
            self.update(QRect(0, PERIOD_TOP, self.width(), PERIOD_H + 1))

    def isSliderDown(self) -> bool:
        return self._pressed

    def visible_range(self) -> tuple[int, int]:
        return self._viewport.visible_range()

    def set_zoom_factor(self, factor: float, anchor_ms: int | None = None) -> None:
        """Zoom the visible film window around the playhead or given anchor."""
        old_visible = self.visible_range()
        if self._viewport.set_zoom_factor(factor, anchor_ms):
            self._apply_lane_height()
            self._invalidate()
            self._emit_visible_range_if_changed(old_visible)

    def reset_zoom(self) -> None:
        old_visible = self.visible_range()
        if self._viewport.fit_game():
            self._apply_lane_height()
            self._invalidate()
            self._emit_visible_range_if_changed(old_visible)

    def fit_range(
            self, start_ms: int, end_ms: int, *,
            context_before_ms: int = 0,
            context_after_ms: int = 0) -> bool:
        """Fit a real source interval without seeking or changing clip data."""
        old_visible = self.visible_range()
        changed = self._viewport.fit_range(
            start_ms,
            end_ms,
            context_before_ms=context_before_ms,
            context_after_ms=context_after_ms,
        )
        if changed:
            self._apply_lane_height()
            self._invalidate()
            self._emit_visible_range_if_changed(old_visible)
        return changed

    def _update_edge_scroll(self, x: float) -> None:
        """Track how far past an edge the pointer is, and run if it is."""
        if x < 0:
            self._edge_scroll_px = x
        elif x > self.width():
            self._edge_scroll_px = x - self.width()
        else:
            self._edge_scroll_px = 0.0
        if self._edge_scroll_px:
            if not self._edge_scroll_timer.isActive():
                self._edge_scroll_timer.start()
        else:
            self._edge_scroll_timer.stop()

    def _stop_edge_scroll(self) -> None:
        self._edge_scroll_px = 0.0
        self._edge_scroll_timer.stop()

    def edge_scroll_step_ms(self) -> int:
        """How far one tick moves the film, signed by direction."""
        overshoot = self._edge_scroll_px
        if not overshoot:
            return 0
        start, end = self.visible_range()
        span = max(1, end - start)
        seconds = EDGE_SCROLL_INTERVAL_MS / 1000.0
        boost = 1.0 + min(
            EDGE_SCROLL_MAX_BOOST - 1.0,
            abs(overshoot) / EDGE_SCROLL_RAMP_PX * (
                EDGE_SCROLL_MAX_BOOST - 1.0))
        step = span * EDGE_SCROLL_BASE_FRACTION * seconds * boost
        step = max(1.0, step)
        return int(round(step if overshoot > 0 else -step))

    def _edge_scroll_step(self) -> None:
        """Pan the film and drag the playhead along with it."""
        if not self._pressed:
            self._stop_edge_scroll()
            return
        delta = self.edge_scroll_step_ms()
        if not delta or not self.pan_viewport_by(delta):
            # Already against the start or the end of the film.
            self._edge_scroll_timer.stop()
            return
        start, end = self.visible_range()
        self.setValue(end if delta > 0 else start)
        self.sliderMoved.emit(self.value())

    def pan_viewport_by(self, delta_ms: int) -> bool:
        """Move only the visual source-time window."""
        old_visible = self.visible_range()
        changed = self._viewport.pan_by(delta_ms)
        if changed:
            self._invalidate()
            self._emit_visible_range_if_changed(old_visible)
        return changed

    def pan_viewport_fraction(self, fraction: float) -> bool:
        old_visible = self.visible_range()
        changed = self._viewport.pan_fraction(fraction)
        if changed:
            self._invalidate()
            self._emit_visible_range_if_changed(old_visible)
        return changed

    def set_visible_start(self, start_ms: int) -> bool:
        """Set scrollbar position while preserving visible duration."""
        old_visible = self.visible_range()
        changed = self._viewport.set_visible_range(
            start_ms, start_ms + self._viewport.visible_duration_ms)
        if changed:
            self._invalidate()
            self._emit_visible_range_if_changed(old_visible)
        return changed

    def set_follow_playhead(self, enabled: bool) -> None:
        self._viewport.follow_playhead_enabled = bool(enabled)
        if not enabled:
            return
        old_visible = self.visible_range()
        if self._viewport.reveal_time(self._value):
            self._invalidate()
            self._emit_visible_range_if_changed(old_visible)

    def selected_block_range(self) -> tuple[int, int] | None:
        """Return the one selected valid play interval, if present."""
        return next((
            (block.start_ms, block.end_ms)
            for block in self._blocks
            if block.selected and block.end_ms > block.start_ms
        ), None)

    def set_snapping_enabled(self, enabled: bool) -> None:
        self._snapping_enabled = bool(enabled)

    def snapping_enabled(self) -> bool:
        return self._snapping_enabled

    def _set_visible_range(self, start_ms: int, end_ms: int) -> None:
        old_visible = self.visible_range()
        if self._viewport.set_visible_range(start_ms, end_ms):
            self._apply_lane_height()
            self._invalidate()
            self._emit_visible_range_if_changed(old_visible)

    def _pan_to_include(self, value: int) -> bool:
        """Keep a zoomed timeline following playback when it leaves view."""
        old_visible = self.visible_range()
        if not self._viewport.reveal_time(value):
            return False
        self._invalidate()
        self._emit_visible_range_if_changed(old_visible)
        return True

    @property
    def viewport(self) -> SourceTimeViewport:
        """Shared, non-media source-time viewport state."""
        return self._viewport

    @property
    def _min(self) -> int:
        return self._viewport.source_start_ms

    @property
    def _max(self) -> int:
        return self._viewport.source_end_ms

    @property
    def _view_min(self) -> int:
        return self._viewport.visible_start_ms

    @property
    def _view_max(self) -> int:
        return self._viewport.visible_end_ms

    @property
    def _value(self) -> int:
        return self._viewport.playhead_ms

    def _emit_visible_range_if_changed(
            self, old_visible: tuple[int, int]) -> None:
        visible = self.visible_range()
        if visible != old_visible:
            self.visibleRangeChanged.emit(*visible)
        self._emit_density_if_changed()

    # ---------- content ----------

    def set_quarter_pylons(self, starts) -> None:
        markers = tuple((int(ms), str(label)) for ms, label in starts)
        if markers != self._quarter_pylons:
            self._quarter_pylons = markers
            self._invalidate()

    def set_ink_blocks(self, enabled: bool) -> None:
        if self._ink_blocks != bool(enabled):
            self._ink_blocks = bool(enabled)
            self._invalidate()

    def set_blocks(self, blocks: list[TimelineBlock | tuple]) -> None:
        """Set clip ranges, accepting legacy 3-tuples for compatibility."""
        normalized: list[TimelineBlock] = []
        for block in blocks:
            if isinstance(block, TimelineBlock):
                normalized.append(block)
            else:
                start, end, selected = block[:3]
                clip_id = block[3] if len(block) > 3 else ""
                kind = block[4] if len(block) > 4 else ""
                title = block[5] if len(block) > 5 else ""
                colour = block[6] if len(block) > 6 else ""
                category_label = block[7] if len(block) > 7 else ""
                detected = bool(block[8]) if len(block) > 8 else False
                normalized.append(TimelineBlock(
                    start, end, selected, clip_id, kind, title, colour,
                    category_label, detected))
        normalized, conflicts = self._with_lanes(normalized)
        if normalized != self._blocks or conflicts != self._conflicts:
            self._blocks = normalized
            self._conflicts = conflicts
            self._update_focus_range()
            self._apply_lane_height()
            self._invalidate()
            self._emit_density_if_changed()

    @staticmethod
    def _with_lanes(
            blocks: list[TimelineBlock],
            ) -> tuple[list[TimelineBlock], tuple[tuple[int, int], ...]]:
        """Attach a freshly computed lane to every block."""
        lanes, conflicts = assign_lanes(blocks)
        return (
            [block if block.lane == lane else replace(block, lane=lane)
             for block, lane in zip(blocks, lanes)],
            conflicts,
        )

    def _apply_lane_height(self) -> None:
        """Grow only as far as the deepest overlap actually needs."""
        used = max(
            (self._display_lane(block) for block in self._blocks),
            default=0,
        ) + 1
        used = max(1, min(MAX_LANES, used))
        self._lane_count = used
        self.setFixedHeight(
            self._band_top() + used * BAND_H + (used - 1) * LANE_GAP
            + UNCLAIMED_RAIL_GAP + UNCLAIMED_RAIL_H + BAND_BOTTOM_PAD)

    def conflict_ranges(self) -> tuple[tuple[int, int], ...]:
        """Film ranges where two or more plays genuinely overlap."""
        return self._conflicts

    def blocks(self) -> tuple[TimelineBlock, ...]:
        """Normalized blocks for presentation peers such as the overview."""
        return tuple(self._blocks)

    def set_coverage_segments(
            self, segments: list[TimelineCoverage | dict | tuple]) -> None:
        """Set the latest persisted detector ledger for source-gap context."""
        normalized: list[TimelineCoverage] = []
        for segment in segments:
            if isinstance(segment, TimelineCoverage):
                item = segment
            elif isinstance(segment, dict):
                try:
                    raw_index = segment.get("candidate_index")
                    item = TimelineCoverage(
                        start_ms=int(segment["start_ms"]),
                        end_ms=int(segment["end_ms"]),
                        kind=str(segment["kind"]),
                        reason=str(segment.get("reason", "")),
                        candidate_kind=str(
                            segment.get("candidate_kind", "") or ""),
                        candidate_index=int(raw_index)
                        if raw_index is not None else None,
                        review_status=str(
                            segment.get("review_status", "") or ""),
                        audit_level=str(
                            segment.get("audit_level", "") or ""),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
            else:
                try:
                    start_ms, end_ms, kind = segment[:3]
                    reason = segment[3] if len(segment) > 3 else ""
                    item = TimelineCoverage(
                        int(start_ms), int(end_ms), str(kind), str(reason))
                except (TypeError, ValueError):
                    continue
            if item.kind not in COVERAGE_KINDS \
                    or item.end_ms <= item.start_ms:
                continue
            normalized.append(item)
        normalized.sort(key=lambda item: (
            item.start_ms,
            item.end_ms,
            item.kind,
            item.candidate_kind,
            item.candidate_index
            if item.candidate_index is not None else -1,
        ))
        if normalized != self._coverage_segments:
            self._coverage_segments = normalized
            self._invalidate()

    def coverage_segments(self) -> tuple[TimelineCoverage, ...]:
        return tuple(self._coverage_segments)

    def set_show_ignored_fragments(self, visible: bool) -> None:
        """Explicitly reveal preserved unclassified source fragments."""
        visible = bool(visible)
        if visible == self._show_ignored_fragments:
            return
        self._show_ignored_fragments = visible
        self._invalidate()

    @property
    def show_ignored_fragments(self) -> bool:
        return self._show_ignored_fragments

    def lane_count(self) -> int:
        return self._lane_count

    def set_frame_duration_ms(self, value: float) -> None:
        """Frame length used only to draw frame ticks when zoomed right in."""
        value = float(value)
        if value > 0 and value != self._frame_duration_ms:
            self._frame_duration_ms = value
            self._invalidate()

    def set_selected_clip_id(self, clip_id: str | None) -> None:
        """Update only block-selection state, leaving clip data untouched."""
        normalized = [
            replace(block, selected=bool(clip_id and block.clip_id == clip_id))
            for block in self._blocks
        ]
        if normalized != self._blocks:
            self._blocks = normalized
            self._update_focus_range()
            self._apply_lane_height()
            self._invalidate()
            self._emit_density_if_changed()

    def focus_range(self) -> tuple[int, int] | None:
        return self._focus_range

    def _update_focus_range(self) -> None:
        selected = next(
            (block for block in self._blocks if block.selected),
            None,
        )
        if selected is None or self._max <= self._min:
            self._focus_range = None
            return
        duration = max(
            FOCUS_WINDOW_MIN_MS,
            selected.end_ms - selected.start_ms,
        )
        center = (selected.start_ms + selected.end_ms) // 2
        start = center - duration // 2
        start = max(self._min, min(self._max - duration, start))
        end = min(self._max, start + duration)
        if end - start < duration:
            start = max(self._min, end - duration)
        self._focus_range = (start, end)

    def set_period_markers(self, markers_ms: list[int]) -> None:
        """Set film timestamps where Q2, Q3, Q4, and overtime begin."""
        normalized = tuple(sorted({
            int(value) for value in markers_ms if int(value) > self._min
        }))
        if normalized != self._period_markers:
            self._period_markers = normalized
            # Gaining or losing the rail moves the whole strip.
            self._apply_lane_height()
            self._invalidate()

    def set_period_rail_visible(self, visible: bool) -> None:
        """Show or hide the quarter rail without discarding its snap data."""
        visible = bool(visible)
        if visible == self._period_rail_visible:
            return
        self._period_rail_visible = visible
        self._apply_lane_height()
        self._invalidate()

    def set_marks(self, in_ms: int | None, out_ms: int | None) -> None:
        if (in_ms, out_ms) != (self._in_ms, self._out_ms):
            self._in_ms, self._out_ms = in_ms, out_ms
            self._invalidate()

    # ---------- geometry ----------

    def _span(self) -> int:
        return max(1, self._viewport.visible_duration_ms)

    def _rail_visible(self) -> bool:
        """The quarter rail only earns its height once a project has markers."""
        return self._period_rail_visible and bool(self._period_markers)

    def _rail_offset(self) -> int:
        return 0 if self._rail_visible() else RAIL_RESERVE

    def _groove_top(self) -> int:
        return GROOVE_TOP - self._rail_offset()

    def _band_top(self) -> int:
        return BAND_TOP - self._rail_offset()

    def _lane_top(self, lane: int) -> int:
        lane = max(0, min(MAX_LANES - 1, int(lane)))
        return self._band_top() + lane * (BAND_H + LANE_GAP)

    def _display_lane(self, block: TimelineBlock) -> int:
        """The painted row for this zoom mode, independent of stored lanes."""
        if self._variant == COMPACT \
                and self._viewport.zoom_factor < COMPACT_EXPAND_ZOOM:
            return 0
        if self._focus_active():
            assert self._focus_range is not None
            focus_start, focus_end = self._focus_range
            if block.end_ms <= focus_start or block.start_ms >= focus_end:
                return 0
        return block.lane

    def _band_bottom(self) -> int:
        return self._lane_top(self._lane_count - 1) + BAND_H

    def visible_block_count(self) -> int:
        """Plays with any part of themselves on screen right now."""
        return sum(
            1 for b in self._blocks
            if b.end_ms > self._view_min and b.start_ms < self._view_max)

    def cell_width_px(self) -> float:
        """Room one play gets on screen at this zoom.

        The median painted width of the plays in view, not the viewport
        divided by their count. Plays are short and the gaps between
        them are long, so dividing by count claimed room a column never
        actually gets and put text into cells too narrow to hold it.
        """
        start, end = self.visible_range()
        span = max(1, end - start)
        widths = sorted(
            (min(b.end_ms, end) - max(b.start_ms, start)) / span
            * self.width()
            for b in self._blocks
            if b.end_ms > start and b.start_ms < end)
        if not widths:
            return float(max(1, self.width()))
        return max(0.0, widths[len(widths) // 2])

    def cell_density(self) -> str:
        """Whether an aligned surface can show words, chips, or neither."""
        width = self.cell_width_px()
        if width >= CELL_TEXT_MIN_PX:
            return DENSITY_TEXT
        if width >= CELL_CHIP_MIN_PX:
            return DENSITY_CHIP
        return DENSITY_SPARSE

    def zoom_for_readable_cells(self) -> float:
        """The zoom factor at which cells would first hold text."""
        blocks = [
            b for b in self._blocks
            if b.end_ms > self._min and b.start_ms < self._max]
        span = max(1, self._max - self._min)
        if not blocks or self.width() <= 0:
            return 1.0
        # Solve for the visible span at which a typical play is at least
        # CELL_TEXT_MIN_PX wide, from its own duration.
        durations = sorted(max(1, b.end_ms - b.start_ms) for b in blocks)
        typical = durations[len(durations) // 2]
        readable_span = typical * self.width() / float(CELL_TEXT_MIN_PX)
        readable_span = max(1.0, readable_span)
        return max(1.0, span / readable_span)

    def ensure_readable_cells(self, anchor_ms: int | None = None) -> bool:
        """Zoom in just far enough for cells to hold words.

        Zooming out is never blocked - the chip fallback exists so the
        whole game stays reachable. This is the way back in.
        """
        if self.cell_density() == DENSITY_TEXT:
            return False
        target = self.zoom_for_readable_cells()
        if target <= self._viewport.zoom_factor:
            return False
        self.set_zoom_factor(
            target,
            self.value() if anchor_ms is None else int(anchor_ms))
        return True

    def _emit_density_if_changed(self) -> None:
        density = self.cell_density()
        if density != self._cell_density:
            self._cell_density = density
            self.cellDensityChanged.emit(density)

    def _unclaimed_rail_top(self) -> int:
        return self._band_bottom() + UNCLAIMED_RAIL_GAP

    def unclaimed_ranges(self) -> tuple[tuple[int, int], ...]:
        """Source ranges no play covers, merged and in order.

        Derived from the blocks already on screen rather than from new
        data: whatever is not inside a play is film nobody has cut.
        """
        if self._max <= self._min:
            return ()
        spans = sorted(
            (max(self._min, b.start_ms), min(self._max, b.end_ms))
            for b in self._blocks
            if b.end_ms > self._min and b.start_ms < self._max)
        gaps: list[tuple[int, int]] = []
        cursor = self._min
        for start, end in spans:
            if start > cursor:
                gaps.append((cursor, start))
            cursor = max(cursor, end)
        if cursor < self._max:
            gaps.append((cursor, self._max))
        # Sub-second slivers are seams between adjacent plays, not
        # footage anyone wants to reclaim.
        return tuple((a, b) for a, b in gaps if b - a >= 1_000)

    def _unclaimed_at(self, position_ms: int) -> tuple[int, int] | None:
        for start, end in self.unclaimed_ranges():
            if start <= position_ms <= end:
                return (start, end)
        return None

    def _lane_at(self, y: float) -> int | None:
        """Which overlap lane a pointer is in, or None if outside the band."""
        for lane in range(self._lane_count):
            top = self._lane_top(lane)
            if top - 2 <= y <= top + BAND_H + 2:
                return lane
        return None

    def _period_boundaries(self) -> list[int]:
        if self._max <= self._min or not self._period_markers:
            return []
        middle = [
            value for value in self._period_markers
            if self._min < value < self._max
        ]
        return [self._min, *middle, self._max]

    def _visible_period_segments(self) -> list[tuple[int, int, int]]:
        boundaries = self._period_boundaries()
        if not boundaries:
            return []
        segments = []
        for index, (start, end) in enumerate(
                zip(boundaries, boundaries[1:])):
            visible_start = max(start, self._view_min)
            visible_end = min(end, self._view_max)
            if visible_end > visible_start:
                segments.append((index, visible_start, visible_end))
        return segments

    def _period_index_for(self, value: int) -> int:
        boundaries = self._period_boundaries()
        if not boundaries:
            return -1
        return min(
            len(boundaries) - 2,
            sum(value >= boundary for boundary in boundaries[1:-1]),
        )

    def _x_for(self, ms: int) -> int:
        """Pixel column for a timestamp, always inside the widget.

        Clips are handed to the timeline before the video's duration is
        known, so the range can still be empty. Clamping here keeps those
        coordinates finite - an unclamped value overflows the int that Qt's
        drawing calls expect.
        """
        geometry = self._focus_geometry()
        if geometry is None:
            return self._viewport.time_to_position(ms, self.width())
        focus_start, focus_end, lens_left, lens_right = geometry
        value = max(self._view_min, min(self._view_max, int(ms)))
        if value <= focus_start:
            span = max(1, focus_start - self._view_min)
            return round(
                (value - self._view_min) / span * lens_left)
        if value <= focus_end:
            span = max(1, focus_end - focus_start)
            return round(
                lens_left
                + (value - focus_start) / span * (lens_right - lens_left))
        span = max(1, self._view_max - focus_end)
        return round(
            lens_right
            + (value - focus_end) / span
            * (max(1, self.width() - 1) - lens_right))

    def _ms_for(self, x: float) -> int:
        geometry = self._focus_geometry()
        if geometry is None:
            return self._viewport.position_to_time(x, self.width())
        focus_start, focus_end, lens_left, lens_right = geometry
        value = max(0.0, min(float(max(1, self.width() - 1)), float(x)))
        if value <= lens_left:
            span = max(1, lens_left)
            return round(
                self._view_min
                + value / span * (focus_start - self._view_min))
        if value <= lens_right:
            span = max(1, lens_right - lens_left)
            return round(
                focus_start
                + (value - lens_left) / span * (focus_end - focus_start))
        span = max(1, max(1, self.width() - 1) - lens_right)
        return round(
            focus_end
            + (value - lens_right) / span * (self._view_max - focus_end))

    def _focus_active(self) -> bool:
        return (
            self._variant == FOCUS
            and self._focus_range is not None
            and self._view_min <= self._min
            and self._view_max >= self._max
            and self._max > self._min
        )

    def _focus_geometry(self) -> tuple[int, int, int, int] | None:
        if not self._focus_active() or self.width() <= 4:
            return None
        assert self._focus_range is not None
        focus_start, focus_end = self._focus_range
        focus_start = max(self._view_min, min(self._view_max, focus_start))
        focus_end = max(focus_start + 1, min(self._view_max, focus_end))
        width = max(1, self.width() - 1)
        lens_left = round(width * FOCUS_LENS_LEFT_RATIO) \
            if focus_start > self._view_min else 0
        lens_right = round(width * FOCUS_LENS_RIGHT_RATIO) \
            if focus_end < self._view_max else width
        return focus_start, focus_end, lens_left, lens_right

    def _snap_targets(
            self, exclude_clip_id: str = "", exclude_edge: str = ""
            ) -> tuple[int, ...]:
        targets = {self._min, self._max, *self._period_markers}
        for block in self._blocks:
            if not (block.clip_id == exclude_clip_id
                    and exclude_edge == "start"):
                targets.add(block.start_ms)
            if not (block.clip_id == exclude_clip_id
                    and exclude_edge == "end"):
                targets.add(block.end_ms)
            if block.predicted_snap_ms is not None:
                targets.add(int(block.predicted_snap_ms))
        if self._in_ms is not None:
            targets.add(self._in_ms)
        if self._out_ms is not None:
            targets.add(self._out_ms)
        return tuple(sorted(
            value for value in targets
            if self._min <= value <= self._max))

    def _snap_ms(
            self, position_ms: int,
            modifiers=Qt.KeyboardModifier.NoModifier,
            exclude_clip_id: str = "", exclude_edge: str = "") -> int:
        """Snap a pointer seek using a tolerance that stays constant in pixels."""
        position_ms = max(self._min, min(self._max, int(position_ms)))
        if not self._snapping_enabled or \
                modifiers & Qt.KeyboardModifier.AltModifier:
            return position_ms
        center_x = self._x_for(position_ms)
        tolerance_ms = max(
            1,
            abs(self._ms_for(center_x + SNAP_TOLERANCE_PX)
                - self._ms_for(center_x - SNAP_TOLERANCE_PX)) // 2,
        )
        nearest = min(
            self._snap_targets(exclude_clip_id, exclude_edge),
            key=lambda target: abs(target - position_ms),
            default=position_ms,
        )
        return nearest if abs(nearest - position_ms) <= tolerance_ms \
            else position_ms

    def _invalidate(self) -> None:
        self._static = None
        self.update()

    def resizeEvent(self, event) -> None:
        self._invalidate()
        self._emit_density_if_changed()
        super().resizeEvent(event)

    # ---------- painting ----------

    def _build_static(self) -> QPixmap:
        """Time the complete static redraw, including ticks, blocks and marks."""
        with PerfTimer("timeline_redraw"):
            return self._render_static()

    def _fine_tick_interval(self) -> tuple[int, int]:
        """Tick and label spacing for a visible window under a minute."""
        span = self._span()
        if span > 20_000:
            return 5_000, 15_000
        if span > 5_000:
            return 1_000, 5_000
        return 1_000, 1_000

    def _label_fits(self, x: int) -> bool:
        """Keep the last tick label off the total-duration readout beside us.

        The film total sits immediately right of this widget, so a label drawn
        against the right edge collides with it - which is how "25m" ended up
        printed on top of "50:42".
        """
        return x < self.width() - LABEL_RIGHT_MARGIN

    def _draw_ruler(self, p: QPainter) -> None:
        """Minute ticks when zoomed out, seconds and frames when zoomed in."""
        font = p.font()
        font.setPointSize(7)
        p.setFont(font)
        span = self._span()
        groove_top = self._groove_top()

        if span > 60_000:
            minute = 60_000
            m = max(0, (self._view_min - self._min + minute - 1) // minute)
            while self._min + m * minute <= self._view_max:
                x = self._x_for(self._min + m * minute)
                labelled = m % 5 == 0
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(C_TICK)
                p.drawRect(x, groove_top, 1, 4 if labelled else 2)
                if labelled and m and self._label_fits(x):
                    p.setPen(C_TICK_TEXT)
                    p.drawText(x + 3, groove_top + 8, f"{m}m")
                    p.setPen(Qt.PenStyle.NoPen)
                m += 1
            focus_geometry = self._focus_geometry()
            if focus_geometry is not None:
                focus_start, focus_end, _left, _right = focus_geometry
                interval = 15_000
                offset = (focus_start // interval) * interval
                while offset <= focus_end:
                    if offset >= focus_start:
                        x = self._x_for(offset)
                        labelled = offset % 30_000 == 0
                        p.setBrush(C_FOCUS_EDGE if labelled else C_TICK)
                        p.drawRect(x, groove_top, 1, 5 if labelled else 3)
                        if labelled and self._label_fits(x):
                            p.setPen(C_TICK_TEXT)
                            p.drawText(
                                x + 3, groove_top + 8,
                                format_ms(offset - self._min))
                            p.setPen(Qt.PenStyle.NoPen)
                    offset += interval
            return

        # Zoomed in: the user is working on boundaries, so give them a scale
        # they can actually read instead of an empty groove.
        interval, label_every = self._fine_tick_interval()
        first = ((self._view_min - self._min) // interval) * interval
        offset = self._min + first
        while offset <= self._view_max:
            if offset >= self._view_min:
                elapsed = offset - self._min
                labelled = elapsed % label_every == 0
                x = self._x_for(offset)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(C_TICK)
                p.drawRect(x, groove_top, 1, 4 if labelled else 2)
                if labelled and self._label_fits(x):
                    p.setPen(C_TICK_TEXT)
                    p.drawText(x + 3, groove_top + 8, format_ms(elapsed))
                    p.setPen(Qt.PenStyle.NoPen)
            offset += interval

        frame_ms = self._frame_duration_ms
        if frame_ms <= 0:
            return
        frame_px = frame_ms / span * max(1, self.width() - 1)
        if frame_px < MIN_FRAME_TICK_PX:
            return
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C_TICK)
        index = int((self._view_min - self._min) / frame_ms)
        position = self._min + index * frame_ms
        while position <= self._view_max:
            if position >= self._view_min:
                p.drawRect(self._x_for(round(position)), groove_top + 6, 1, 3)
            index += 1
            position = self._min + index * frame_ms

    def _render_static(self) -> QPixmap:
        """Everything that doesn't move, rendered once and reused."""
        pixmap = QPixmap(max(1, self.width()), self.height())
        pixmap.fill(C_BG)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)

        focus_geometry = self._focus_geometry()
        if focus_geometry is not None:
            _focus_start, _focus_end, lens_left, lens_right = focus_geometry
            p.setBrush(C_FOCUS_BG)
            p.drawRect(
                lens_left, 0, max(1, lens_right - lens_left), self.height())
            p.setBrush(C_FOCUS_EDGE)
            p.drawRect(lens_left, 0, 1, self.height())
            p.drawRect(lens_right, 0, 1, self.height())

        groove_top = self._groove_top()
        p.setBrush(C_GROOVE)
        p.drawRect(0, groove_top, self.width(), GROOVE_H)

        # Game-quarter rail. The data is stored at project level because film
        # time and clip metadata are separate concepts.
        period_segments = (
            self._visible_period_segments() if self._rail_visible() else [])
        if period_segments:
            font = p.font()
            font.setPointSize(7)
            font.setBold(True)
            p.setFont(font)
            for index, start, end in period_segments:
                x1, x2 = self._x_for(start), self._x_for(end)
                width = max(2, x2 - x1)
                p.setPen(C_PERIOD_LINE)
                p.setBrush(C_PERIOD_A if index % 2 == 0 else C_PERIOD_B)
                p.drawRect(x1, PERIOD_TOP, width, PERIOD_H)
                if width >= 24:
                    p.setPen(C_PERIOD_TEXT)
                    p.drawText(
                        x1, PERIOD_TOP, width, PERIOD_H,
                        Qt.AlignmentFlag.AlignCenter, period_label(index))
            p.setPen(Qt.PenStyle.NoPen)

        self._draw_ruler(p)
        p.setPen(Qt.PenStyle.NoPen)

        # Nothing below can be placed meaningfully until a duration is known.
        if self._max <= self._min:
            p.end()
            return pixmap

        # The source timeline always shows the complete film as a neutral base.
        # The latest detector ledger sits beneath current editable clips, so a
        # miss remains visible without pretending historical output is a clip.
        if self._variant == COMPACT:
            p.setBrush(C_SOURCE_BASE)
            p.drawRect(
                0,
                self._band_top(),
                self.width(),
                max(BAND_H, self._band_bottom() - self._band_top()),
            )
            self._draw_coverage(p)

        # Every variant gets the rail. Detector coverage above is compact
        # only, but unclaimed footage is a question the dual timeline asks
        # just as often.
        self._draw_unclaimed_rail(p)

        # Clip blocks: what of the film is already covered. Drawn lane by lane
        # and in film order so neighbouring plays can be told apart.
        separators: list[tuple[int, int]] = []
        previous: dict[int, tuple[int, int]] = {}  # lane -> (end_ms, x2)
        for block in sorted(
                self._blocks,
                key=lambda item: (
                    self._display_lane(item),
                    item.selected,
                    item.start_ms,
                    item.end_ms,
                )):
            if block.end_ms < self._view_min or \
                    block.start_ms > self._view_max:
                continue
            visible_start = max(block.start_ms, self._view_min)
            visible_end = min(block.end_ms, self._view_max)
            x1, x2 = self._x_for(visible_start), self._x_for(visible_end)
            width = max(2, x2 - x1)
            display_lane = self._display_lane(block)
            lane_top = self._lane_top(display_lane)
            block_colour = QColor(block.colour) if block.colour else \
                PLAY_COLORS.get(block.kind, C_BLOCK)
            if not block_colour.isValid():
                block_colour = C_BLOCK

            # A play that starts within a pixel of where the previous one
            # ended would otherwise read as one long play. That single
            # ambiguity is what makes correct detections look merged.
            last = previous.get(display_lane)
            if last is not None and last[0] <= block.start_ms and \
                    x1 - last[1] <= BLOCK_SEPARATOR_PX:
                separators.append((max(0, x1 - 1), lane_top))
            previous[display_lane] = (block.end_ms, x1 + width)

            # Decoration earns its place: a full-film view has blocks under
            # ten pixels wide, and drawing a keyline plus a dark bottom plus a
            # detected cap on each fills the interior with texture instead of
            # colour. Full detail only on blocks wide enough to read.
            p.setBrush(block_colour.darker(115) if self._ink_blocks else block_colour)
            p.drawRect(x1, lane_top, width, BAND_H)
            if width >= BLOCK_KEYLINE_MIN_PX:
                p.setBrush(block_colour if self._ink_blocks else block_colour.lighter(140))
                p.drawRect(x1, lane_top, width, 3 if self._ink_blocks else 1)
            if width >= BLOCK_FULL_DETAIL_PX:
                p.setBrush(block_colour.darker(165))
                p.drawRect(x1, lane_top + BAND_H - 1, width, 1)

            if block.selected:
                p.setPen(C_BLOCK_SEL)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(x1, lane_top, width, BAND_H - 1)
                p.setPen(Qt.PenStyle.NoPen)
                # Selected clips expose compact trim handles. Their invisible
                # hit targets are wider than these two-pixel marks.
                p.setBrush(C_BLOCK_SEL)
                if self._view_min <= block.start_ms <= self._view_max:
                    edge_x = self._x_for(block.start_ms)
                    p.drawRect(edge_x - 1, lane_top + 1, 2, BAND_H - 2)
                if self._view_min <= block.end_ms <= self._view_max:
                    edge_x = self._x_for(block.end_ms)
                    p.drawRect(edge_x - 1, lane_top + 1, 2, BAND_H - 2)

            # Endpoint dots make clip boundaries instantly scannable at the
            # editing zooms where users trim. The selected clip receives the
            # stronger white handles from the standard timeline treatment.
            if not self._ink_blocks and width >= BLOCK_KEYLINE_MIN_PX:
                endpoint = (
                    C_BLOCK_SEL if block.selected
                    else block_colour.lighter(125 if self._ink_blocks else 185))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(endpoint)
                radius = 3 if block.selected else 2
                center_y = lane_top + BAND_H // 2
                if self._view_min <= block.start_ms <= self._view_max:
                    p.drawEllipse(QPoint(x1, center_y), radius, radius)
                if self._view_min <= block.end_ms <= self._view_max:
                    p.drawEllipse(
                        QPoint(x1 + width, center_y), radius, radius)

            # Provenance as shape, never as colour: the block's colour is
            # already spoken for by the Color By selector. At tight zoom the
            # cap would eat the whole block, so it earns the same width floor
            # as full detail.
            if block.detected and not block.selected and not self._ink_blocks \
                    and width >= BLOCK_FULL_DETAIL_PX:
                p.setBrush(block_colour.lighter(175))
                p.drawRect(x1, lane_top, min(DETECTED_CAP_W, width), BAND_H)

            # The estimated snap is a slim bookmark, not another boundary.
            # Mint means the estimator passed its gates; amber stays visible
            # but honestly communicates a low-confidence estimate.
            if block.predicted_snap_ms is not None and \
                    self._view_min <= block.predicted_snap_ms <= self._view_max:
                snap_x = self._x_for(block.predicted_snap_ms)
                p.setBrush(
                    C_PREDICTED_SNAP
                    if block.snap_eligible else C_PREDICTED_SNAP_LOW)
                p.drawRect(snap_x - 1, lane_top + 2, 2, BAND_H - 4)
                p.drawPolygon(QPolygon([
                    QPoint(snap_x - 3, lane_top),
                    QPoint(snap_x + 3, lane_top),
                    QPoint(snap_x, lane_top + 4),
                ]))

        p.setBrush(C_BG)
        for x, lane_top in separators:
            p.drawRect(x, lane_top, BLOCK_SEPARATOR_PX, BAND_H)

        # Pylons occupy existing ruler space. They are visual metadata only:
        # no extra rail height, snapping targets, or trim/seek hit areas.
        last_label_end = -1
        for ms, label in self._quarter_pylons:
            if not self._view_min <= ms <= self._view_max:
                continue
            x = self._x_for(ms)
            bottom = self._band_top()
            pylon_x = max(5, min(self.width()-6, x))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#f18b38"))
            p.drawPolygon(QPolygon([QPoint(pylon_x-2, bottom-14), QPoint(pylon_x+2, bottom-14),
                                    QPoint(pylon_x+4, bottom-3), QPoint(pylon_x-4, bottom-3)]))
            p.drawRect(pylon_x-5, bottom-3, 10, 2)
            p.setBrush(QColor("#965325"))
            p.drawRect(x, bottom, 1, max(BAND_H, self._band_bottom()-bottom))
            font = p.font(); font.setPixelSize(9); font.setBold(True); p.setFont(font)
            label_width = p.fontMetrics().horizontalAdvance(label)
            label_x = pylon_x+8 if pylon_x+8+label_width < self.width() else pylon_x-8-label_width
            if label_x > last_label_end and label_x >= 0:
                p.setPen(QColor("#f5b577"))
                p.drawText(label_x, bottom-4, label)
                last_label_end = label_x+label_width+4
            p.setPen(Qt.PenStyle.NoPen)

        self._draw_conflicts(p)

        # In/out marks use the standard endpoint dots: a beginning dot sits at
        # the top of the rail, while an ending dot anchors the bottom.
        for ms, colour, forward in ((self._in_ms, C_IN, True),
                                    (self._out_ms, C_OUT, False)):
            if ms is None:
                continue
            if not self._view_min <= ms <= self._view_max:
                continue
            x = self._x_for(ms)
            p.setBrush(colour)
            p.drawRect(x - 1, 0, 2, self.height() - 2)
            dot_y = 5 if forward else self.height() - 6
            p.drawEllipse(QPoint(x, dot_y), 4, 4)
        p.end()
        return pixmap

    def _draw_coverage(self, p: QPainter) -> None:
        """Paint the latest full-source detector disposition under clips."""
        colours = {
            "play": C_COVERAGE_PLAY,
            "review": C_COVERAGE_REVIEW,
            "possible_missed": C_COVERAGE_MISSED,
            "separator": C_COVERAGE_SEPARATOR,
        }
        top = self._band_top()
        bottom = self._band_bottom()
        for segment in self._coverage_segments:
            if segment.end_ms <= self._view_min \
                    or segment.start_ms >= self._view_max:
                continue
            x1 = self._x_for(max(segment.start_ms, self._view_min))
            x2 = self._x_for(min(segment.end_ms, self._view_max))
            width = max(1, x2 - x1)
            colour = colours[segment.kind]
            if segment.kind == "possible_missed":
                zoomed_for_editing = self._viewport.zoom_factor >= 4.0
                if (
                    not self._show_ignored_fragments
                    and not zoomed_for_editing
                ):
                    # Coverage stays discoverable without turning a full game
                    # into a solid amber rail. Zooming or the Fragments toggle
                    # reveals the complete range.
                    whisper = QColor(C_COVERAGE_RESOLVED)
                    whisper.setAlpha(28)
                    p.setBrush(whisper)
                    p.drawRect(x1, bottom - 1, width, 1)
                    continue
                if segment.review_status == "dismissed":
                    p.setBrush(C_COVERAGE_RESOLVED)
                    p.drawRect(x1, bottom - 1, width, 1)
                    continue
                if segment.review_status == "clip_created":
                    p.setBrush(C_COVERAGE_REVIEW)
                    p.drawRect(x1, bottom - 1, width, 1)
                    continue
                # Pending source coverage becomes readable at editing zoom,
                # but stays subordinate to actual play blocks.
                audit_colour = {
                    "check_first": C_COVERAGE_MISSED_HIGH,
                    "low_signal": C_COVERAGE_MISSED_LOW,
                }.get(segment.audit_level, colour)
                underline_h = {
                    "check_first": 2,
                    "low_signal": 1,
                }.get(segment.audit_level, 1)
                fill = QColor(audit_colour)
                fill.setAlpha({
                    "check_first": 62,
                    "low_signal": 28,
                }.get(segment.audit_level, 42))
                p.setBrush(fill)
                p.drawRect(x1, top, width, max(1, bottom - top))
                p.setBrush(audit_colour)
                p.drawRect(x1, bottom - underline_h, width, underline_h)
            elif segment.kind == "separator":
                p.setBrush(colour)
                p.drawRect(x1, top, width, max(1, bottom - top))
            else:
                # Play/review coverage is context, not a second set of blocks.
                p.setBrush(colour)
                p.drawRect(x1, bottom - 2, width, 2)

    def _draw_unclaimed_rail(self, p: QPainter) -> None:
        """Solid where a play claims the film, amber where none does."""
        top = self._unclaimed_rail_top()
        left = self._x_for(self._view_min)
        right = self._x_for(self._view_max)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#171f24") if self._ink_blocks else C_COVERAGE_PLAY)
        p.drawRect(left, top, max(1, right - left), UNCLAIMED_RAIL_H)
        for start, end in self.unclaimed_ranges():
            if end <= self._view_min or start >= self._view_max:
                continue
            x1 = self._x_for(max(start, self._view_min))
            x2 = self._x_for(min(end, self._view_max))
            p.setBrush(QColor("#60605a") if self._ink_blocks else C_COVERAGE_MISSED_HIGH)
            p.drawRect(x1, top, max(2, x2 - x1), UNCLAIMED_RAIL_H)

    def _draw_conflicts(self, p: QPainter) -> None:
        """Mark film ranges where two plays claim the same time.

        A thin amber underline sits below the band. Overlapping plays already
        stand up in their own lanes; the earlier diagonal hatch across all
        three lanes turned that into three rows of pattern and hid the block
        colours the Color By selector had chosen.
        """
        if not self._conflicts:
            return
        top = self._band_bottom() + 1
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C_CONFLICT)
        for start, end in self._conflicts:
            if end < self._view_min or start > self._view_max:
                continue
            x1 = self._x_for(max(start, self._view_min))
            x2 = self._x_for(min(end, self._view_max))
            width = max(2, x2 - x1)
            height = 2 if (
                self._variant == COMPACT
                and self._viewport.zoom_factor < COMPACT_EXPAND_ZOOM
            ) else CONFLICT_UNDERLINE_H
            p.drawRect(x1, top, width, height)
            if self._variant == COMPACT and width < 8:
                center = x1 + width // 2
                p.drawRect(center - 2, top, 5, height)
        p.setBrush(Qt.BrushStyle.NoBrush)

    def paintEvent(self, event) -> None:
        if self._static is None \
                or self._static.width() != max(1, self.width()) \
                or self._static.height() != self.height():
            self._static = self._build_static()
        p = QPainter(self)
        p.drawPixmap(0, 0, self._static)

        x = self._x_for(self._value)
        boundaries = self._period_boundaries()
        period_index = self._period_index_for(self._value)
        if self._rail_visible() and boundaries and period_index >= 0 and \
                self._view_min <= self._value <= self._view_max:
            start = max(boundaries[period_index], self._view_min)
            end = min(boundaries[period_index + 1], self._view_max)
            x1 = self._x_for(start)
            x2 = self._x_for(end)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(C_PLAYHEAD)
            p.drawRect(
                x1, PERIOD_TOP, max(2, x2 - x1), PERIOD_H)

        # Elapsed fill reads position at a glance.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(C_ELAPSED)
        p.drawRect(0, self._groove_top(), max(0, x), GROOVE_H)

        p.setBrush(C_PLAYHEAD)
        p.drawRect(x - PLAYHEAD_W // 2, 2, PLAYHEAD_W, self.height() - 4)
        p.drawPolygon(QPolygon([QPoint(x - 5, 0), QPoint(x + 5, 0),
                                QPoint(x, 6)]))
        p.end()

    # ---------- interaction ----------

    def _block_at(
            self, x: float, y: float | None = None) -> TimelineBlock | None:
        """Return the intended block when clip ranges overlap.

        Overlapping plays live in separate lanes, so the pointer's row is the
        honest answer to "which play did you mean". Without it a short clip
        contained inside a longer one steals every click and the containing
        play cannot be selected from the timeline at all.

        Inside one lane the selected clip still wins first, so an over-long
        play can be scrubbed and split without a neighbour stealing the click.
        """
        clicked_ms = self._ms_for(x)
        lane = None if y is None else self._lane_at(y)
        hits: list[TimelineBlock] = []
        for block in self._blocks:
            if block.end_ms < self._view_min or \
                    block.start_ms > self._view_max:
                continue
            if lane is not None and self._display_lane(block) != lane:
                continue
            x1 = self._x_for(block.start_ms)
            x2 = max(x1 + 2, self._x_for(block.end_ms))
            if x1 - 2 <= x <= x2 + 2:
                hits.append(block)
        if not hits:
            return None
        temporal_hits = [
            block for block in hits if block_contains(block, clicked_ms)
        ] or [
            block for block in hits
            if block.start_ms <= clicked_ms <= block.end_ms
        ]
        selected = [
            block for block in temporal_hits if block.selected
        ]
        candidates = selected or temporal_hits or hits
        return min(
            candidates,
            key=lambda block: (
                block.end_ms - block.start_ms,
                abs(x - (self._x_for(block.start_ms)
                         + self._x_for(block.end_ms)) / 2),
            ),
        )

    def _coverage_at(self, position_ms: int) -> TimelineCoverage | None:
        """Return the persisted detector interval under a source timestamp."""
        return next((
            segment for segment in self._coverage_segments
            if segment.start_ms <= position_ms < segment.end_ms
        ), None)

    def _trim_edge_at(
            self, x: float, y: float
            ) -> tuple[TimelineBlock, str] | None:
        if not self._band_top() - 3 <= y <= self._band_bottom() + 3:
            return None
        candidates: list[tuple[float, TimelineBlock, str]] = []
        for block in self._blocks:
            if not block.selected or not block.clip_id:
                continue
            if self._view_min <= block.start_ms <= self._view_max:
                distance = abs(x - self._x_for(block.start_ms))
                if distance <= TRIM_HANDLE_HIT_RADIUS:
                    candidates.append((distance, block, "start"))
            if self._view_min <= block.end_ms <= self._view_max:
                distance = abs(x - self._x_for(block.end_ms))
                if distance <= TRIM_HANDLE_HIT_RADIUS:
                    candidates.append((distance, block, "end"))
        if not candidates:
            return None
        _distance, block, edge = min(candidates, key=lambda item: item[0])
        return block, edge

    def _start_trim(
            self, block: TimelineBlock, edge: str,
            modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        self._pending_block = None
        self._trim_original = block
        self._trim_edge = edge
        self._pressed = True
        position = block.start_ms if edge == "start" else block.end_ms
        self.sliderPressed.emit()
        self.trimStarted.emit(block.clip_id, edge, position)
        # Merely pressing a handle must not snap it to a nearby boundary.
        self._preview_trim(position, Qt.KeyboardModifier.AltModifier)

    def _preview_trim(
            self, position_ms: int,
            modifiers=Qt.KeyboardModifier.NoModifier) -> int:
        block = self._trim_original
        edge = self._trim_edge
        if block is None or edge not in {"start", "end"}:
            return int(position_ms)
        position = self._snap_ms(
            position_ms, modifiers, block.clip_id, edge)
        if edge == "start":
            position = max(
                self._min,
                min(position, block.end_ms - MIN_TRIM_DURATION_MS))
            start_ms, end_ms = position, block.end_ms
        else:
            position = min(
                self._max,
                max(position, block.start_ms + MIN_TRIM_DURATION_MS))
            start_ms, end_ms = block.start_ms, position

        normalized = [
            replace(item, start_ms=start_ms, end_ms=end_ms)
            if item.clip_id == block.clip_id else item
            for item in self._blocks
        ]
        # Lanes stay put while the pointer is down - a block jumping rows
        # mid-drag is disorienting - but conflict marks update live so an
        # overlap the drag is creating is visible before it is committed.
        _lanes, conflicts = assign_lanes(normalized)
        if normalized != self._blocks or conflicts != self._conflicts:
            self._blocks = normalized
            self._conflicts = conflicts
            self._invalidate()
        self.setValue(position)
        self.sliderMoved.emit(position)
        self.trimPreview.emit(block.clip_id, edge, position)
        return position

    def _finish_trim(
            self, position_ms: int,
            modifiers=Qt.KeyboardModifier.NoModifier) -> None:
        block = self._trim_original
        edge = self._trim_edge
        if block is None:
            return
        position = self._preview_trim(position_ms, modifiers)
        self._trim_original = None
        self._trim_edge = ""
        self._pressed = False
        self.sliderReleased.emit()
        self.trimFinished.emit(block.clip_id, edge, position)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        clicked_x = event.position().x()
        self._press_x = clicked_x
        self._press_y = event.position().y()
        # The unclaimed rail is its own target: clicking a gap offers to
        # reclaim it rather than moving the playhead there.
        rail_top = self._unclaimed_rail_top()
        if rail_top - 2 <= self._press_y <= rail_top + UNCLAIMED_RAIL_H + 2:
            gap = self._unclaimed_at(self._ms_for(clicked_x))
            if gap is not None:
                self.unclaimedActivated.emit(*gap)
                event.accept()
                return
        trim_hit = self._trim_edge_at(clicked_x, event.position().y())
        if trim_hit is not None:
            block, edge = trim_hit
            self._start_trim(block, edge, event.modifiers())
            event.accept()
            return
        # The visible line is intentionally narrow, but it needs a forgiving
        # invisible grab area even when it crosses a clip block.
        on_playhead = abs(clicked_x - self._x_for(self._value)) <= \
            PLAYHEAD_HIT_RADIUS
        # Delay a block's click action until release. If the pointer moves,
        # the same gesture becomes a scrub instead, so the colored band does
        # not turn into dead space for timeline navigation.
        if not on_playhead and \
                self._lane_at(event.position().y()) is not None:
            block = self._block_at(clicked_x, event.position().y())
            if block is not None and block.clip_id:
                self._pending_block = block
                event.accept()
                return
        self._begin_scrub(self._snap_ms(
            self._ms_for(clicked_x), event.modifiers()))

    def _begin_scrub(self, position_ms: int) -> None:
        self._pending_block = None
        self._pressed = True
        self.sliderPressed.emit()
        self.setValue(position_ms)
        self.sliderMoved.emit(position_ms)

    def mouseMoveEvent(self, event) -> None:
        raw_ms = self._ms_for(event.position().x())
        if self._trim_original is not None:
            self._preview_trim(raw_ms, event.modifiers())
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            return
        ms = self._snap_ms(raw_ms, event.modifiers())
        if self._pending_block is not None and \
                abs(event.position().x() - self._press_x) >= DRAG_START_PX:
            self._begin_scrub(ms)
            return
        trim_hit = self._trim_edge_at(
            event.position().x(), event.position().y())
        block = self._block_at(event.position().x(), event.position().y()) \
            if self._lane_at(event.position().y()) is not None else None
        if trim_hit is not None:
            trim_block, edge = trim_hit
            edge_label = "start" if edge == "start" else "end"
            self.setToolTip(
                f"Drag to trim {edge_label} of "
                f"{trim_block.title or 'selected clip'}")
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif block is not None:
            kind = block.category_label or \
                PLAY_LABELS.get(block.kind, "Unlabelled play")
            title = f"\n{block.title}" if block.title else ""
            source = "Detected" if block.detected else "Manual"
            conflict = "\nOverlaps another play" if any(
                other.clip_id != block.clip_id and blocks_overlap(other, block)
                for other in self._blocks) else ""
            snap = ""
            if block.predicted_snap_ms is not None:
                confidence = "" if block.snap_confidence is None else \
                    f" ({block.snap_confidence:.0%} confidence)"
                qualifier = "Predicted snap" if block.snap_eligible else \
                    "Low-confidence snap estimate"
                snap = (
                    f"\n{qualifier}: "
                    f"{format_ms(block.predicted_snap_ms, show_millis=True)}"
                    f"{confidence}"
                )
            self.setToolTip(
                f"{kind} | {format_ms(block.start_ms)} - "
                f"{format_ms(block.end_ms)} | {source}{title}{conflict}{snap}\n"
                "Click to select this clip | drag to scrub")
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            coverage = self._coverage_at(raw_ms) \
                if self._variant == COMPACT else None
            if coverage is not None and coverage.kind == "possible_missed" \
                    and coverage.review_status in {"", "pending"}:
                priority = {
                    "check_first": "Check first",
                    "review": "Review",
                    "low_signal": "Low signal (still review)",
                }.get(coverage.audit_level, "Possible missed footage")
                self.setToolTip(
                    f"{priority} | Possible missed footage | "
                    f"{format_ms(coverage.start_ms)} - "
                    f"{format_ms(coverage.end_ms)}\n"
                    "Click to zoom in | right-click to add it to a "
                    "neighboring play")
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            elif coverage is not None and coverage.kind == "possible_missed":
                state = "Included in a play" \
                    if coverage.review_status == "clip_created" \
                    else "Reviewed: not a play"
                self.setToolTip(
                    f"{state} | {format_ms(coverage.start_ms)} - "
                    f"{format_ms(coverage.end_ms)}")
                self.setCursor(Qt.CursorShape.ArrowCursor)
            elif coverage is not None and coverage.kind == "separator":
                self.setToolTip(
                    "Verified black separator | "
                    f"{format_ms(coverage.start_ms)} - "
                    f"{format_ms(coverage.end_ms)}")
                self.setCursor(Qt.CursorShape.ArrowCursor)
            else:
                self.setToolTip(format_ms(ms))
                self.setCursor(Qt.CursorShape.ArrowCursor)
        if self._pressed:
            # The playhead follows the pointer immediately. Decoder preview
            # seeks are throttled separately by VideoPlayer.
            self._update_edge_scroll(event.position().x())
            if not self._edge_scroll_timer.isActive():
                self.setValue(ms)
                self.sliderMoved.emit(ms)

    def mouseReleaseEvent(self, event) -> None:
        if self._trim_original is not None:
            if abs(event.position().x() - self._press_x) < DRAG_START_PX:
                block = self._trim_original
                original = block.start_ms if self._trim_edge == "start" \
                    else block.end_ms
                self._finish_trim(
                    original, Qt.KeyboardModifier.AltModifier)
            else:
                self._finish_trim(
                    self._ms_for(event.position().x()), event.modifiers())
            event.accept()
            return
        if self._pending_block is not None:
            block = self._pending_block
            self._pending_block = None
            release_x = event.position().x()
            if abs(release_x - self._press_x) >= DRAG_START_PX:
                self._begin_scrub(self._snap_ms(
                    self._ms_for(release_x), event.modifiers()))
            else:
                clicked_ms = self._ms_for(release_x)
                clicked_ms = max(block.start_ms,
                                 min(block.end_ms, clicked_ms))
                self.blockActivated.emit(block.clip_id, clicked_ms)
                event.accept()
                return
        if self._pressed:
            self._stop_edge_scroll()
            release_x = event.position().x()
            self.setValue(self._snap_ms(
                self._ms_for(release_x), event.modifiers()))
            self._pressed = False
            self.sliderReleased.emit()
            if (
                self._variant == COMPACT
                and abs(release_x - self._press_x) < DRAG_START_PX
                and self._band_top() - 2 <= self._press_y
                <= self._band_bottom() + 2
            ):
                coverage = self._coverage_at(self._ms_for(release_x))
                if coverage is not None \
                        and coverage.kind == "possible_missed" \
                        and coverage.review_status in {"", "pending"}:
                    self.coverageActivated.emit(
                        coverage.start_ms, coverage.end_ms, coverage.kind)

    def contextMenuEvent(self, event) -> None:
        """Hand the clicked clip and timestamp to the workspace menu."""
        clicked_ms = self._ms_for(event.pos().x())
        block = self._block_at(event.pos().x(), event.pos().y())
        clip_id = ""
        if block is not None and block.clip_id:
            clip_id = block.clip_id
            clicked_ms = max(block.start_ms, min(block.end_ms, clicked_ms))
        self.contextRequested.emit(clip_id, clicked_ms, event.globalPos())
        event.accept()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y() or -event.angleDelta().x()
        if delta == 0:
            event.ignore()
            return
        notches = 1 if delta > 0 else -1
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Resolve source time before changing the viewport. The player
            # uses this timestamp as the zoom anchor, so the film under the
            # pointer stays under the pointer instead of jumping toward the
            # playhead.
            self.wheel_zoom.emit(
                notches, self._ms_for(event.position().x()))
        elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.wheel_frame.emit(notches)
        else:
            self.wheel_seek.emit(notches)
        event.accept()
