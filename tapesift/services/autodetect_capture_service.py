"""Capture-side helpers kept separate from the frozen detector algorithm."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path

from tapesift.services import play_detect_service
from tapesift.services.play_detect_service import DetectionResult

DETECTOR_ID = "tapesift-play-detect"
DETECTOR_NAME = "TapeSift Cadence Segmentation Engine"
DETECTOR_SHORT_NAME = "TapeSift CSE"
DETECTOR_VERSION = "beta-4d"


def detector_source_sha256() -> str:
    """Best-effort build fingerprint; source video identity remains advisory."""
    try:
        path = Path(play_detect_service.__file__)
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, TypeError):
        return ""


def serialize_detection_result(result: DetectionResult) -> dict:
    """Return the complete, JSON-safe detector output captured by projects."""
    return {
        "schema_version": "1.0",
        "coverage_schema_version":
            play_detect_service.COVERAGE_SCHEMA_VERSION,
        "summary": {
            "signal": result.signal,
            "spans_found": result.spans_found,
            "separators_found": result.separators_found,
            "duration_ms": result.duration_ms,
            "coverage_pct": result.coverage_pct,
            "unclassified_ms": result.unclassified_ms,
            "accounted_ms": result.accounted_ms,
            "unaccounted_ms": result.unaccounted_ms,
            "accounted_pct": result.accounted_pct,
            "possible_missed_ms": result.possible_missed_ms,
            "separator_ms": result.separator_ms,
            "review_ms": result.review_ms,
            "coverage_by_kind": result.coverage_by_kind,
            "profile": asdict(result.profile)
            if result.profile is not None else None,
        },
        "plays": [asdict(play) for play in result.plays],
        "unclassified": [
            asdict(segment) for segment in result.unclassified],
        "coverage": [
            asdict(segment) for segment in result.coverage_segments],
    }


def selected_candidate_keys(result: dict, ui_options: dict) -> set[tuple[str, int]] | None:
    """Validate a deliberate partition; None keeps legacy capture semantics."""
    if not isinstance(result, dict) or not isinstance(ui_options, dict):
        raise ValueError("Detector result and UI options must be objects.")
    if "candidate_selection" not in ui_options:
        return None
    selection = ui_options["candidate_selection"]
    if (not isinstance(selection, dict)
            or type(selection.get("schema_version")) is not int
            or selection["schema_version"] != 1):
        raise ValueError("Invalid candidate selection version.")
    roots = set()
    for kind, name in (("play", "plays"), ("unclassified", "unclassified")):
        collection = result.get(name, [])
        if not isinstance(collection, list):
            raise ValueError(f"session.result.{name} is not a list")
        roots.update((kind, index) for index in range(len(collection)))
    parts = []
    for name in ("kept", "dismissed"):
        values = selection.get(name)
        if not isinstance(values, list):
            raise ValueError(f"Candidate selection {name} is not a list.")
        keys = set()
        for value in values:
            if (not isinstance(value, list) or len(value) != 2
                    or not isinstance(value[0], str)
                    or type(value[1]) is not int
                    or (value[0], value[1]) not in roots
                    or (value[0], value[1]) in keys):
                raise ValueError(f"Invalid or repeated candidate selection in {name}.")
            keys.add((value[0], value[1]))
        parts.append(keys)
    kept, dismissed = parts
    if kept & dismissed or kept | dismissed != roots:
        raise ValueError("Candidate selection must partition every detector result.")
    return kept
