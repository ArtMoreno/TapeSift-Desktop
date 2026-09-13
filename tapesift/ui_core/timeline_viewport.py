"""Pure source-time viewport math shared by timeline projections.

The viewport changes only how source time is mapped onto a widget. It never
seeks media and never changes clip or project timestamps.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


DEFAULT_MINIMUM_VISIBLE_DURATION_MS = 1_000


@dataclass(slots=True)
class SourceTimeViewport:
    """One clamped visual window over an immutable source-time range."""

    minimum_visible_duration_ms: int = DEFAULT_MINIMUM_VISIBLE_DURATION_MS
    follow_playhead_enabled: bool = True
    source_start_ms: int = field(init=False, default=0)
    source_end_ms: int = field(init=False, default=0)
    visible_start_ms: int = field(init=False, default=0)
    visible_end_ms: int = field(init=False, default=0)
    playhead_ms: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.minimum_visible_duration_ms = max(
            1, int(self.minimum_visible_duration_ms))

    @property
    def source_duration_ms(self) -> int:
        return max(0, self.source_end_ms - self.source_start_ms)

    @property
    def visible_duration_ms(self) -> int:
        return max(0, self.visible_end_ms - self.visible_start_ms)

    @property
    def zoom_factor(self) -> float:
        visible = self.visible_duration_ms
        return self.source_duration_ms / visible if visible > 0 else 1.0

    def source_range(self) -> tuple[int, int]:
        return self.source_start_ms, self.source_end_ms

    def visible_range(self) -> tuple[int, int]:
        return self.visible_start_ms, self.visible_end_ms

    def clamp_time(self, position_ms: int | float) -> int:
        value = int(position_ms)
        return max(self.source_start_ms, min(self.source_end_ms, value))

    def set_source_range(self, start_ms: int, end_ms: int) -> bool:
        """Set or switch source and return whether visible geometry changed.

        A source or duration change intentionally fits the full source. This
        preserves TapeSift's existing load behavior and prevents a viewport
        from one film leaking into the next.
        """
        start = int(start_ms)
        end = max(start, int(end_ms))
        old_visible = self.visible_range()
        self.source_start_ms = start
        self.source_end_ms = end
        self.visible_start_ms = start
        self.visible_end_ms = end
        self.playhead_ms = self.clamp_time(self.playhead_ms)
        return self.visible_range() != old_visible

    def set_visible_range(self, start_ms: int, end_ms: int) -> bool:
        """Set a clamped non-empty visual range without changing source time."""
        old = self.visible_range()
        source_duration = self.source_duration_ms
        if source_duration <= 0:
            self.visible_start_ms = self.source_start_ms
            self.visible_end_ms = self.source_end_ms
            return self.visible_range() != old

        requested_duration = int(end_ms) - int(start_ms)
        duration = max(
            self.minimum_visible_duration_ms, requested_duration)
        duration = min(source_duration, duration)
        start = max(
            self.source_start_ms,
            min(self.source_end_ms - duration, int(start_ms)),
        )
        self.visible_start_ms = start
        self.visible_end_ms = start + duration
        return self.visible_range() != old

    def fit_game(self) -> bool:
        """Show the full source without moving the playhead."""
        return self.set_visible_range(
            self.source_start_ms, self.source_end_ms)

    def fit_range(
            self, start_ms: int, end_ms: int, *,
            context_before_ms: int = 0,
            context_after_ms: int = 0) -> bool:
        """Fit a valid source interval plus optional surrounding context."""
        start = int(start_ms)
        end = int(end_ms)
        if end <= start or self.source_duration_ms <= 0:
            return False
        start = self.clamp_time(start - max(0, int(context_before_ms)))
        end = self.clamp_time(end + max(0, int(context_after_ms)))
        if end <= start:
            return False
        return self.set_visible_range(start, end)

    def set_zoom_factor(
            self, factor: float, anchor_ms: int | None = None,
            anchor_ratio: float | None = None) -> bool:
        """Zoom around a source timestamp while preserving its screen ratio."""
        source_duration = self.source_duration_ms
        if source_duration <= 0:
            return False
        normalized = float(factor)
        if not math.isfinite(normalized):
            normalized = 1.0
        normalized = max(1.0, normalized)
        visible_duration = max(
            self.minimum_visible_duration_ms,
            round(source_duration / normalized),
        )
        visible_duration = min(source_duration, visible_duration)
        anchor = self.clamp_time(
            self.playhead_ms if anchor_ms is None else anchor_ms)
        if anchor_ratio is None:
            current_duration = max(1, self.visible_duration_ms)
            ratio = (
                anchor - self.visible_start_ms
            ) / current_duration
        else:
            ratio = float(anchor_ratio)
        if not math.isfinite(ratio):
            ratio = 0.5
        ratio = max(0.0, min(1.0, ratio))
        start = round(anchor - ratio * visible_duration)
        return self.set_visible_range(start, start + visible_duration)

    def pan_by(self, delta_ms: int | float) -> bool:
        """Move the visual window without moving playhead or source data."""
        if self.visible_duration_ms >= self.source_duration_ms:
            return False
        delta = int(delta_ms)
        return self.set_visible_range(
            self.visible_start_ms + delta,
            self.visible_end_ms + delta,
        )

    def pan_fraction(self, fraction: float) -> bool:
        if not math.isfinite(float(fraction)):
            return False
        return self.pan_by(round(self.visible_duration_ms * float(fraction)))

    def reveal_time(
            self, position_ms: int | float, placement: float = 0.5) -> bool:
        """Reveal a time only when it is outside the current visual window."""
        if self.visible_duration_ms >= self.source_duration_ms:
            return False
        value = self.clamp_time(position_ms)
        if self.visible_start_ms <= value <= self.visible_end_ms:
            return False
        ratio = float(placement)
        if not math.isfinite(ratio):
            ratio = 0.5
        ratio = max(0.0, min(1.0, ratio))
        start = round(value - self.visible_duration_ms * ratio)
        return self.set_visible_range(
            start, start + self.visible_duration_ms)

    def set_playhead(
            self, position_ms: int | float, *,
            reveal: bool | None = None) -> tuple[bool, bool]:
        """Update playhead and optionally reveal it.

        Returns ``(playhead_changed, viewport_changed)`` so a widget can keep
        its narrow playhead repaint fast path unless static geometry moved.
        """
        value = self.clamp_time(position_ms)
        playhead_changed = value != self.playhead_ms
        self.playhead_ms = value
        should_reveal = self.follow_playhead_enabled \
            if reveal is None else bool(reveal)
        viewport_changed = self.reveal_time(value) if should_reveal else False
        return playhead_changed, viewport_changed

    def time_to_position(self, position_ms: int | float, width_px: int) -> int:
        """Map source time to a clamped pixel column."""
        if self.visible_duration_ms <= 0 or int(width_px) <= 1:
            return 0
        fraction = (
            float(position_ms) - self.visible_start_ms
        ) / self.visible_duration_ms
        usable_width = int(width_px) - 1
        return int(max(0.0, min(1.0, fraction)) * usable_width)

    def position_to_time(self, position_px: int | float, width_px: int) -> int:
        """Map a pixel column back to clamped source time."""
        if self.visible_duration_ms <= 0 or int(width_px) <= 1:
            return self.visible_start_ms
        usable_width = int(width_px) - 1
        fraction = max(
            0.0, min(1.0, float(position_px) / usable_width))
        return int(
            self.visible_start_ms + fraction * self.visible_duration_ms)
