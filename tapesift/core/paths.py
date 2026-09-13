"""Well-known filesystem locations for TapeSift."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


APP_DATA_FOLDER = "TapeSift"


def app_data_dir() -> Path:
    """Per-user application data directory (settings, logs, recovery)."""
    base = os.environ.get("APPDATA")
    if base:
        d = Path(base) / APP_DATA_FOLDER
    elif os.name != "nt":
        legacy = Path.home() / ".tapesift"
        xdg = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        d = legacy if legacy.is_dir() else xdg / "tapesift"
    else:
        d = Path.home() / ".tapesift"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = app_data_dir() / "Logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def recovery_dir() -> Path:
    d = app_data_dir() / "Recovery"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings_file() -> Path:
    return app_data_dir() / "settings.json"


def crash_dir() -> Path:
    d = app_data_dir() / "Crashes"
    d.mkdir(parents=True, exist_ok=True)
    return d


# Where projects and exports go when the user has not chosen one. Film work
# is large - a season of proxies and exports runs to hundreds of gigabytes -
# so the default belongs on the roomy data drive rather than the system
# drive. Documents is also routinely inside a OneDrive/backup scope, which is
# the wrong place for multi-gigabyte project databases.
_PREFERRED_DATA_ROOTS = ("D:/", "E:/")


def preferred_data_root() -> Path | None:
    """First large data drive that exists, or None on a single-drive box."""
    if os.name != "nt":
        return None
    for candidate in _PREFERRED_DATA_ROOTS:
        root = Path(candidate)
        try:
            if root.is_dir():
                return root
        except OSError:
            continue
    return None


def legacy_projects_dir() -> Path:
    """The pre-data-drive default, kept only to recognise a stale setting."""
    return Path.home() / "Documents" / "TapeSift Projects"


def legacy_exports_dir() -> Path:
    return Path.home() / "Videos" / "TapeSift Exports"


def _usable(directory: Path) -> Path | None:
    """A drive letter can exist and still be unwritable; prove it is not."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return directory


def default_projects_dir() -> Path:
    root = preferred_data_root()
    if root is not None:
        chosen = _usable(root / "TapeSift Projects")
        if chosen is not None:
            return chosen
    fallback = legacy_projects_dir()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def default_exports_dir() -> Path:
    root = preferred_data_root()
    if root is not None:
        chosen = _usable(root / "TapeSift Exports")
        if chosen is not None:
            return chosen
    fallback = legacy_exports_dir()
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def project_output_structure(output_root: Path) -> dict[str, Path]:
    """Standard subfolders inside a project's output folder."""
    return {
        "individual": output_root / "Individual Clips",
        "reels": output_root / "Combined Reels",
        "thumbnails": output_root / "Thumbnails",
        "data": output_root / "Project Data",
        "logs": output_root / "Logs",
    }


def ensure_output_structure(output_root: Path) -> dict[str, Path]:
    structure = project_output_structure(output_root)
    for p in structure.values():
        p.mkdir(parents=True, exist_ok=True)
    return structure
