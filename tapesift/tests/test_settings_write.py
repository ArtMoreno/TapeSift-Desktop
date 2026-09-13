"""Settings survive a write that does not finish, and a save that fails.

Both of these are about the same thing: this app can die natively mid-write
(see docs/TESTING.md), and neither the user's preferences nor the running session
should be what pays for it.
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from tapesift.core.config import AppSettings
from tapesift.core.exceptions import DatabaseError
from tapesift.services import recovery_service
from tapesift.services.project_service import ProjectSession
from tapesift.ui_v2.main_window import MainWindowV2


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class TestSettingsAreWrittenAtomically:
    def test_removed_vocabulary_choice_stays_removed_and_can_be_restored(
            self, qapp, tmp_path, monkeypatch):
        from tapesift.ui.settings_dialog import FixedListsDialog
        from tapesift.services.football_vocab import category_for
        target = tmp_path / "choices.json"
        monkeypatch.setattr("tapesift.core.paths.settings_file", lambda: target)
        settings = AppSettings()
        dialog = FixedListsDialog(settings)
        dialog.field_combo.setCurrentIndex(dialog.field_combo.findData("result"))
        dialog.values_edit.setPlainText("\n".join(
            value for value in settings.fixed_details["result"] if value != "Targeting"))
        dialog._save()
        loaded = AppSettings.load(target)
        assert "Targeting" not in loaded.fixed_details["result"]
        assert "False Start" in loaded.fixed_details["result"]
        assert category_for("result", "Targeting") == "Penalty"
        from tapesift.ui_core.clip_editor import ClipEditor
        editor = ClipEditor(loaded)
        editor.set_result_choices([])
        assert "Targeting" not in editor._result_vocabulary
        editor.set_result_choices(["Targeting"])
        assert "Targeting" in editor._result_vocabulary
        editor.close()
        restored = FixedListsDialog(loaded)
        restored.field_combo.setCurrentIndex(restored.field_combo.findData("result"))
        restored.values_edit.appendPlainText("Targeting")
        restored._save()
        assert "Targeting" in AppSettings.load(target).fixed_details["result"]

    def test_a_normal_save_round_trips(self, tmp_path):
        target = tmp_path / "settings.json"
        settings = AppSettings(volume=42, my_team="Miami")

        settings.save(target)

        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["volume"] == 42
        assert data["my_team"] == "Miami"

    def test_a_failed_write_leaves_the_previous_file_intact(
            self, tmp_path, monkeypatch):
        # write_text truncated the real file first, so an interrupted write
        # left unparseable JSON - and load() treats that as "no settings",
        # silently resetting every preference.
        target = tmp_path / "settings.json"
        AppSettings(volume=42).save(target)
        good = target.read_text(encoding="utf-8")

        def half_then_die(self, data, encoding=None, **kwargs):
            # Truncate and write part of it, exactly like a write cut short
            # by a power loss or a native crash. Whatever path this lands on
            # is left holding invalid JSON.
            with open(self, "w", encoding=encoding or "utf-8") as handle:
                handle.write(data[: len(data) // 2])
            raise OSError("no space left on device")

        monkeypatch.setattr("pathlib.Path.write_text", half_then_die)
        with pytest.raises(OSError):
            AppSettings(volume=99).save(target)

        assert target.read_text(encoding="utf-8") == good
        assert AppSettings.load(target).volume == 42

    def test_a_failed_rename_does_not_leave_litter(
            self, tmp_path, monkeypatch):
        target = tmp_path / "settings.json"
        AppSettings(volume=42).save(target)

        monkeypatch.setattr(
            os, "replace",
            lambda *a, **k: (_ for _ in ()).throw(OSError("locked")))
        with pytest.raises(OSError):
            AppSettings(volume=99).save(target)

        assert list(tmp_path.glob("*.tmp")) == []
        assert AppSettings.load(target).volume == 42

    def test_the_temp_file_is_beside_the_target(self, tmp_path, monkeypatch):
        # os.replace is only atomic within one volume, and the system temp
        # dir is routinely on another.
        target = tmp_path / "nested" / "settings.json"
        target.parent.mkdir()
        seen = []
        real = os.replace
        monkeypatch.setattr(
            os, "replace",
            lambda src, dst: (seen.append(src), real(src, dst))[1])

        AppSettings().save(target)

        assert seen and os.path.dirname(str(seen[0])) == str(target.parent)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
    def test_api_key_round_trips_without_plaintext(self, tmp_path):
        target = tmp_path / "settings.json"
        secret = "sk-openrouter-secret"

        AppSettings(first_read_api_key=secret).save(target)

        raw = target.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert secret not in raw
        assert "first_read_api_key" not in data
        assert data["first_read_api_key_dpapi"]
        assert AppSettings.load(target).first_read_api_key == secret

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")
    def test_plaintext_api_key_is_migrated_on_load(self, tmp_path):
        target = tmp_path / "settings.json"
        secret = "sk-legacy-secret"
        target.write_text(json.dumps({
            "volume": 47,
            "first_read_api_key": secret,
        }), encoding="utf-8")

        settings = AppSettings.load(target)

        raw = target.read_text(encoding="utf-8")
        assert settings.first_read_api_key == secret
        assert settings.volume == 47
        assert secret not in raw
        assert "first_read_api_key" not in json.loads(raw)
        assert AppSettings.load(target).first_read_api_key == secret


class TestASaveThatFailsDoesNotKillTheApp:
    """PySide terminates the process when an exception escapes a slot.

    _autosave runs on a QTimer every thirty seconds. A project file locked
    by a backup agent, or a full disk, used to take the whole app down with
    it - losing far more than the save.
    """

    @pytest.fixture
    def window(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        settings = AppSettings(onboarding_seen=True, recent_projects=[])
        settings.default_project_folder = str(tmp_path)
        settings.default_output_folder = str(tmp_path / "out")
        monkeypatch.setattr(settings, "save", lambda *a, **k: None)
        recovery_service.mark_closed()
        window = MainWindowV2(settings)
        session = ProjectSession.create("Vs Duke", tmp_path, tmp_path / "out")
        window._activate_session(session)
        return window

    def _make_saves_fail(self, window, monkeypatch, exc):
        monkeypatch.setattr(
            window.session, "save",
            lambda *a, **k: (_ for _ in ()).throw(exc))

    @pytest.mark.parametrize("exc", [
        DatabaseError("Project is locked.", "Close it elsewhere."),
        OSError("no space left on device"),
    ])
    def test_autosave_reports_instead_of_raising(
            self, window, monkeypatch, exc):
        window.session.dirty = True
        self._make_saves_fail(window, monkeypatch, exc)

        window._autosave()   # must not raise

        assert "Could not save" in window.statusBar().currentMessage()

    def test_the_session_stays_dirty_so_the_next_autosave_retries(
            self, window, monkeypatch):
        window.session.dirty = True
        self._make_saves_fail(window, monkeypatch, OSError("locked"))

        window._autosave()

        assert window.session.dirty is True

    def test_a_failed_close_save_cancels_the_close(self, window, monkeypatch):
        # They answered "yes, save my changes". Closing anyway would discard
        # exactly what they asked to keep.
        window.session.dirty = True
        self._make_saves_fail(window, monkeypatch, OSError("locked"))
        monkeypatch.setattr(
            QMessageBox, "question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        warnings = []
        monkeypatch.setattr(
            QMessageBox, "warning",
            staticmethod(lambda *a, **k: warnings.append(a)))

        assert window._close_project() is False
        assert window.session is not None
        assert warnings
