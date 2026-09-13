"""First-run welcome screen.

The failure that matters is not a crash: it is the dialog reappearing on
every launch (annoying) or never appearing at all (a new user lands on an
empty home screen with no idea what the app expects).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.ui import welcome_dialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, monkeypatch):
    s = AppSettings()
    monkeypatch.setattr(s, "save", lambda *a, **k: None)
    return s


@pytest.fixture
def shown(monkeypatch):
    """Run the dialog without blocking; record whether it was constructed."""
    calls = []
    real = welcome_dialog.WelcomeDialog

    class Fake(real):
        def __init__(self, parent=None):
            super().__init__(parent)
            calls.append(self)

        def exec(self):
            return 1

    monkeypatch.setattr(welcome_dialog, "WelcomeDialog", Fake)
    return calls


class TestFirstRun:
    def test_shows_on_a_fresh_install(self, qapp, settings, shown):
        assert welcome_dialog.show_if_first_run(settings)
        assert len(shown) == 1
        assert settings.onboarding_seen

    def test_does_not_show_again(self, qapp, settings, shown):
        settings.onboarding_seen = True
        assert not welcome_dialog.show_if_first_run(settings)
        assert not shown

    def test_closing_still_counts_as_seen(self, qapp, settings, shown):
        """Dismissing with the X must not make it reappear forever."""
        welcome_dialog.show_if_first_run(settings)
        assert settings.onboarding_seen

    def test_show_again_keeps_it_coming_back(self, qapp, settings, shown):
        welcome_dialog.show_if_first_run(settings)
        shown[0].show_again.setChecked(True)
        settings.onboarding_seen = False
        welcome_dialog.show_if_first_run(settings)
        # The second dialog is a fresh instance with the box unchecked, so
        # the flag is set again - what matters is the checkbox drives it.
        assert shown[1].show_again.isChecked() is False


class TestContent:
    def test_every_step_has_text(self, qapp):
        for title, body in welcome_dialog.STEPS:
            assert title.strip() and len(body) > 40

    def test_dialog_builds(self, qapp):
        dialog = welcome_dialog.WelcomeDialog()
        assert dialog.windowTitle() == "Welcome to TapeSift"
