"""Parser for multi-line pasted timestamp lists.

Supported line shapes:
    03:14 | Opening sequence            (single timestamp + name)
    03:09 - 03:22 | Opening sequence    (explicit range + name)
    03:14                               (timestamp only)
    03:14 Opening sequence              (space-separated name)
Separators accepted between time and name: |  ,  tab  or just whitespace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from tapesift.core.exceptions import TimestampParseError, InvalidRangeError
from tapesift.services.timestamp_parser import parse_timestamp

_TIME_TOKEN = r"\d{1,3}(?::\d{1,3}){0,2}(?:\.\d{1,3})?"
_RANGE_RE = re.compile(
    rf"^\s*(?P<start>{_TIME_TOKEN})\s*(?:-|–|to)\s*(?P<end>{_TIME_TOKEN})\s*(?:[|,\t]\s*(?P<name>.*))?$"
)
_SINGLE_RE = re.compile(
    rf"^\s*(?P<time>{_TIME_TOKEN})\s*(?:[|,\t]\s*(?P<name>.*)|\s+(?P<name2>\S.*))?$"
)


@dataclass
class ParsedRow:
    line_number: int
    raw: str
    start_ms: int = 0
    end_ms: int | None = None       # None → single timestamp (apply pre/post-roll)
    timestamp_ms: int | None = None  # set for single-timestamp rows
    name: str = ""


@dataclass
class BulkParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    errors: list[tuple[int, str, str]] = field(default_factory=list)  # (line#, raw, message)

    @property
    def ok(self) -> bool:
        return bool(self.rows) and not self.errors


def parse_bulk_text(text: str) -> BulkParseResult:
    """Parse pasted text. Keeps valid rows, collects readable errors for bad ones."""
    result = BulkParseResult()
    for idx, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        m = _RANGE_RE.match(line)
        if m:
            try:
                start_ms = parse_timestamp(m.group("start"))
                end_ms = parse_timestamp(m.group("end"))
                if end_ms <= start_ms:
                    raise InvalidRangeError("End time must be after start time.")
            except (TimestampParseError, InvalidRangeError) as exc:
                result.errors.append((idx, raw_line, exc.message))
                continue
            result.rows.append(ParsedRow(
                line_number=idx, raw=raw_line, start_ms=start_ms, end_ms=end_ms,
                name=(m.group("name") or "").strip(),
            ))
            continue

        m = _SINGLE_RE.match(line)
        if m:
            try:
                ts = parse_timestamp(m.group("time"))
            except TimestampParseError as exc:
                result.errors.append((idx, raw_line, exc.message))
                continue
            name = (m.group("name") or m.group("name2") or "").strip()
            result.rows.append(ParsedRow(
                line_number=idx, raw=raw_line, start_ms=ts, end_ms=None,
                timestamp_ms=ts, name=name,
            ))
            continue

        result.errors.append((
            idx, raw_line,
            "Could not read this line. Use formats like '03:14 | Name' or '03:09 - 03:22 | Name'.",
        ))
    return result
