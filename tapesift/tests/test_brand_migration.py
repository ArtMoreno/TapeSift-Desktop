"""TapeSift naming: no ClipForge left anywhere.

The rename is finished. Every project file, the app-data folder and the
export folder were converted, so the compatibility shims that accepted
the old name were removed rather than carried for ever. These tests keep
them removed: a stray reintroduction would quietly resurrect a second
supported extension and a second data folder to keep in sync.
"""

from __future__ import annotations

from pathlib import Path

from tapesift.core import config, paths
from tapesift.database.connection import (
    PROJECT_FILE_EXTENSION,
    SUPPORTED_PROJECT_FILE_EXTENSIONS,
)


def test_tapesift_is_the_only_project_extension():
    assert PROJECT_FILE_EXTENSION == ".tapesift"
    assert SUPPORTED_PROJECT_FILE_EXTENSIONS == (".tapesift",)


def test_app_data_folder_is_tapesift_only(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert paths.app_data_dir() == tmp_path / "TapeSift"


def test_a_legacy_app_data_folder_is_ignored(tmp_path, monkeypatch):
    """An old ClipForge folder must not be adopted or copied from."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    legacy = tmp_path / "ClipForge"
    legacy.mkdir()
    (legacy / "settings.json").write_text('{"volume": 47}', encoding="utf-8")

    current = paths.app_data_dir()

    assert current == tmp_path / "TapeSift"
    assert not (current / "settings.json").exists()


def test_default_folders_carry_the_new_name(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    settings = config.AppSettings.load(tmp_path / "settings.json")
    assert "ClipForge" not in settings.default_project_folder
    assert "ClipForge" not in settings.default_output_folder
    assert settings.default_output_folder.endswith("TapeSift Exports")


def test_no_clipforge_string_in_shipped_code():
    """The whole point of the sweep, asserted directly against the package."""
    package = Path(__file__).resolve().parent.parent
    offenders = [
        str(path.relative_to(package))
        for path in package.rglob("*.py")
        if path.parent.name != "tests"
        and "clipforge" in path.read_text(encoding="utf-8",
                                          errors="replace").lower()
    ]
    assert not offenders, f"ClipForge still referenced in: {offenders}"
