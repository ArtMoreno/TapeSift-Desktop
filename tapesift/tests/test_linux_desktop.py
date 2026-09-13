"""Portable storage, desktop integration, and secure credential regressions."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from tapesift.core import config, paths
from tapesift.ui.export_panel import ExportPanel
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow


def test_linux_credentials_preserve_previous_save_and_never_write_plaintext(tmp_path, monkeypatch):
    saved = {}
    backend = SimpleNamespace(
        set_password=lambda service, reference, value: saved.__setitem__(reference, value),
        get_password=lambda service, reference: saved.get(reference),
        delete_password=lambda service, reference: saved.pop(reference, None),
    )
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setattr(config, "_linux_keyring", lambda: backend)
    target = tmp_path / "settings.json"
    settings = config.AppSettings(first_read_api_key="example-test-credential")
    settings.save(target)
    before = target.read_bytes()
    reference = json.loads(before)[config._PROTECTED_API_KEY]
    assert reference.startswith("keyring:")
    assert b"example-test-credential" not in before
    assert config.AppSettings.load(target).first_read_api_key == "example-test-credential"
    real_replace = config.os.replace
    def fail(*args):
        raise OSError("Test write failure")
    monkeypatch.setattr(config.os, "replace", fail)
    settings.first_read_api_key = "replacement-test-credential"
    with pytest.raises(OSError, match="Test write failure"):
        settings.save(target)
    assert target.read_bytes() == before
    assert saved == {reference: "example-test-credential"}
    monkeypatch.setattr(config.os, "replace", real_replace)
    settings.first_read_api_key = ""
    settings.save(target)
    assert saved == {} and config._PROTECTED_API_KEY not in json.loads(target.read_text())
    backend.set_password = fail
    settings.first_read_api_key = "locked-test-credential"
    before = target.read_bytes()
    with pytest.raises(OSError, match="Unlock a Secret Service"):
        settings.save(target)
    assert target.read_bytes() == before


def test_desktop_open_actions_dispatch_local_file_urls(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url) or True)
    monkeypatch.setattr(paths, "logs_dir", lambda: tmp_path)
    MainWindowWorkflow._open_logs(None)
    ExportPanel._open_path(str(tmp_path))
    ExportPanel._open_path(str(tmp_path / "missing"))
    assert len(opened) == 2
    assert all(url.isLocalFile() and Path(url.toLocalFile()) == tmp_path for url in opened)


@pytest.mark.skipif(config.os.name == "nt", reason="XDG paths are Linux-specific")
def test_linux_uses_xdg_and_preserves_existing_legacy_settings(tmp_path, monkeypatch):
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert paths.app_data_dir() == tmp_path / "data" / "tapesift"
    legacy = tmp_path / ".tapesift"
    legacy.mkdir()
    assert paths.app_data_dir() == legacy


@pytest.mark.skipif(config.os.name == "nt", reason="Desktop entries are Linux-specific")
def test_linux_launcher_uses_this_environment_and_checkout(tmp_path):
    from scripts.install_linux_desktop import install
    import sys
    entry = install(tmp_path)
    text = entry.read_text(encoding="utf-8")
    assert entry == tmp_path / "applications/tapesift.desktop"
    assert sys.executable in text and "run_tapesift_v3.py" in text
    assert "Terminal=false" in text and "tapesift-app-icon.svg" in text
