"""Crash recovery: a marker file records which project is open.

If the app exits cleanly the marker is removed. If it is present at startup,
the previous session ended unexpectedly and we offer to reopen the project.
The project database itself is saved continuously, so no clip data is lost -
recovery only needs to point back at the right file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from tapesift.core import paths

log = logging.getLogger(__name__)

_MARKER = "open_project.json"


def _marker_path() -> Path:
    return paths.recovery_dir() / _MARKER


def mark_open(project_db_path: Path, project_name: str) -> None:
    data = {"db_path": str(project_db_path), "name": project_name}
    _marker_path().write_text(json.dumps(data), encoding="utf-8")


def mark_closed() -> None:
    marker = _marker_path()
    if marker.exists():
        marker.unlink()


def pending_recovery() -> tuple[str, str] | None:
    """Return (db_path, project_name) if the last session did not exit cleanly."""
    marker = _marker_path()
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        db_path = data.get("db_path", "")
        if db_path and Path(db_path).is_file():
            return db_path, data.get("name", "")
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Unreadable recovery marker: %s", exc)
    marker.unlink(missing_ok=True)
    return None
