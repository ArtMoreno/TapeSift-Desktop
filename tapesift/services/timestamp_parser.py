"""Timestamp parsing and formatting.

Accepted input formats:
    HH:MM:SS        01:03:14
    HH:MM:SS.mmm    01:03:14.250
    MM:SS           03:14
    MM:SS.mmm       03:14.500
    raw seconds     194
    decimal seconds 194.5

All times are normalized internally to integer milliseconds.
"""

from __future__ import annotations

import re

from tapesift.core.exceptions import InvalidRangeError, TimestampParseError

_TIME_RE = re.compile(
    r"""^\s*
        (?:(?P<h>\d{1,3}):)?          # optional hours
        (?P<m>\d{1,3}):               # minutes
        (?P<s>\d{1,2}(?:\.\d{1,3})?)  # seconds with optional fraction
        \s*$""",
    re.VERBOSE,
)
_SECONDS_RE = re.compile(r"^\s*(?P<s>\d+(?:\.\d{1,6})?)\s*$")


def parse_timestamp(text: str) -> int:
    """Parse a timestamp string to milliseconds. Raises TimestampParseError."""
    if text is None:
        raise TimestampParseError("Timestamp is empty.", "Enter a time like 03:14 or 01:03:14.250.")
    raw = text.strip()
    if not raw:
        raise TimestampParseError("Timestamp is empty.", "Enter a time like 03:14 or 01:03:14.250.")

    m = _SECONDS_RE.match(raw)
    if m:
        return round(float(m.group("s")) * 1000)

    m = _TIME_RE.match(raw)
    if m:
        hours = int(m.group("h")) if m.group("h") else 0
        minutes = int(m.group("m"))
        seconds = float(m.group("s"))
        if m.group("h") is not None and minutes > 59:
            raise TimestampParseError(
                f"'{raw}' is not a valid time: minutes must be 0-59 when hours are given."
            )
        if seconds >= 60:
            raise TimestampParseError(
                f"'{raw}' is not a valid time: seconds must be below 60."
            )
        if m.group("h") is None and minutes > 59:
            # Treat MM:SS with large minutes as total minutes (e.g. 90:30) - still valid.
            pass
        total_ms = round(((hours * 3600) + (minutes * 60) + seconds) * 1000)
        return total_ms

    if raw.startswith("-"):
        raise TimestampParseError(f"'{raw}' is negative. Times must be zero or greater.")
    raise TimestampParseError(
        f"'{raw}' is not a recognized time format.",
        "Use HH:MM:SS, MM:SS, or plain seconds (decimals allowed).",
    )


def parse_range(start_text: str, end_text: str) -> tuple[int, int]:
    """Parse a start/end pair; end must be after start."""
    start_ms = parse_timestamp(start_text)
    end_ms = parse_timestamp(end_text)
    if end_ms <= start_ms:
        raise InvalidRangeError(
            f"End time ({format_ms(end_ms)}) must be after start time ({format_ms(start_ms)})."
        )
    return start_ms, end_ms


def clamp_range(start_ms: int, end_ms: int, duration_ms: int) -> tuple[int, int, bool]:
    """Clamp a range to [0, duration]. Returns (start, end, was_clamped)."""
    clamped = False
    if start_ms < 0:
        start_ms = 0
        clamped = True
    if duration_ms > 0 and end_ms > duration_ms:
        end_ms = duration_ms
        clamped = True
    if duration_ms > 0 and start_ms > duration_ms:
        start_ms = max(0, duration_ms - 1000)
        clamped = True
    return start_ms, end_ms, clamped


def format_ms(ms: int, always_hours: bool = False, show_millis: bool = False) -> str:
    """Format milliseconds as HH:MM:SS[.mmm] or MM:SS[.mmm]."""
    ms = max(0, int(ms))
    total_seconds, millis = divmod(ms, 1000)
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours or always_hours:
        base = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        base = f"{minutes:02d}:{seconds:02d}"
    if show_millis and millis:
        base += f".{millis:03d}"
    return base


def format_ms_filename(ms: int) -> str:
    """Format for use inside filenames (no colons): HH-MM-SS."""
    ms = max(0, int(ms))
    total_seconds = ms // 1000
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    return f"{hours:02d}-{minutes:02d}-{seconds:02d}"
