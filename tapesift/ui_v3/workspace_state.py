"""V3-only window and rail persistence.

This file deliberately does not reuse V2 dock-state blobs and does not touch
project data. Tests redirect APPDATA, so lifecycle checks cannot overwrite the
user's real workspace choice.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from tapesift.core.paths import app_data_dir

from PySide6.QtCore import QByteArray


@dataclass(frozen=True)
class ReviewRailState:
    ledger_open: bool = False
    details_open: bool = False


class WorkspaceStateV3:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_dir() / "shell-v3.json"
        self._document: dict[str, object] = {}

    def load(self) -> ReviewRailState:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            self._document = document if isinstance(document, dict) else {}
        except (OSError, ValueError, TypeError):
            self._document = {}
        return ReviewRailState(
            ledger_open=bool(self._document.get("ledger_open", False)),
            details_open=bool(self._document.get("details_open", False)),
        )

    def save(
            self, rail_state: ReviewRailState,
            geometry: QByteArray | None = None) -> None:
        document = dict(self._document)
        document["ledger_open"] = bool(rail_state.ledger_open)
        document["details_open"] = bool(rail_state.details_open)
        if geometry is not None and not geometry.isEmpty():
            document["geometry"] = base64.b64encode(
                bytes(geometry)).decode("ascii")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)
        self._document = document

    def geometry(self) -> QByteArray | None:
        value = self._document.get("geometry")
        if not isinstance(value, str) or not value:
            return None
        try:
            return QByteArray(base64.b64decode(value))
        except (ValueError, TypeError):
            return None
