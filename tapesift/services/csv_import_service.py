"""CSV import with column auto-detection and mapping.

Recognized columns (case-insensitive, common variants accepted):
    timestamp, start, end, clip_name, title, filename, label, tags, notes
Naming priority: clip_name > title > filename > generated fallback.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from tapesift.core.exceptions import CsvImportError, TimestampParseError, InvalidRangeError
from tapesift.services.timestamp_parser import parse_timestamp

COLUMN_ALIASES: dict[str, list[str]] = {
    "timestamp": ["timestamp", "time", "ts", "moment"],
    "start": ["start", "start_time", "starttime", "in", "in_point", "from", "begin"],
    "end": ["end", "end_time", "endtime", "out", "out_point", "to", "finish", "stop"],
    "clip_name": ["clip_name", "clipname", "name"],
    "title": ["title", "clip_title"],
    "filename": ["filename", "file_name", "output", "output_name"],
    "label": ["label", "category", "type"],
    "tags": ["tags", "tag", "keywords"],
    "notes": ["notes", "note", "description", "comment", "comments"],
}


@dataclass
class CsvRow:
    line_number: int
    timestamp_ms: int | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    name: str = ""
    label: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class CsvImportResult:
    headers: list[str] = field(default_factory=list)
    mapping: dict[str, str] = field(default_factory=dict)  # field -> csv header
    rows: list[CsvRow] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)  # (line#, message)


def detect_mapping(headers: list[str]) -> dict[str, str]:
    """Guess which CSV header supplies each TapeSift field."""
    mapping: dict[str, str] = {}
    normalized = {h.strip().lower().replace(" ", "_"): h for h in headers}
    for our_field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                mapping[our_field] = normalized[alias]
                break
    return mapping


def read_headers(csv_path: Path) -> list[str]:
    try:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            for row in reader:
                return [c.strip() for c in row]
    except OSError as exc:
        raise CsvImportError(f"Could not open CSV file: {exc}",
                             "Check that the file exists and is not locked by another app.")
    raise CsvImportError("The CSV file is empty.", "Add a header row and at least one data row.")


def parse_csv(csv_path: Path, mapping: dict[str, str] | None = None) -> CsvImportResult:
    """Parse a CSV file into importable rows using the given (or detected) mapping."""
    try:
        content = csv_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise CsvImportError(f"Could not read CSV file: {exc}")
    return parse_csv_text(content, mapping)


def parse_csv_text(content: str, mapping: dict[str, str] | None = None) -> CsvImportResult:
    result = CsvImportResult()
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames:
        raise CsvImportError("The CSV file has no header row.")
    result.headers = [h.strip() for h in reader.fieldnames]
    result.mapping = mapping or detect_mapping(result.headers)

    if not any(k in result.mapping for k in ("timestamp", "start")):
        raise CsvImportError(
            "No time column found.",
            "The CSV needs a 'timestamp' column, or 'start' (optionally with 'end').",
        )

    def cell(raw_row: dict, our_field: str) -> str:
        header = result.mapping.get(our_field, "")
        if not header:
            return ""
        value = raw_row.get(header)
        return (value or "").strip()

    for line_number, raw_row in enumerate(reader, start=2):
        row = CsvRow(line_number=line_number)
        try:
            ts_text = cell(raw_row, "timestamp")
            start_text = cell(raw_row, "start")
            end_text = cell(raw_row, "end")

            if start_text:
                row.start_ms = parse_timestamp(start_text)
                if end_text:
                    row.end_ms = parse_timestamp(end_text)
                    if row.end_ms <= row.start_ms:
                        raise InvalidRangeError("End time must be after start time.")
            elif ts_text:
                row.timestamp_ms = parse_timestamp(ts_text)
            else:
                raise TimestampParseError("Row has no timestamp or start time.")
        except (TimestampParseError, InvalidRangeError) as exc:
            result.errors.append((line_number, exc.message))
            continue

        # Naming priority: clip_name > title > filename
        row.name = cell(raw_row, "clip_name") or cell(raw_row, "title") or cell(raw_row, "filename")
        row.label = cell(raw_row, "label")
        tags_text = cell(raw_row, "tags")
        if tags_text:
            row.tags = [t.strip() for t in tags_text.replace(";", ",").split(",") if t.strip()]
        row.notes = cell(raw_row, "notes")
        result.rows.append(row)

    return result
