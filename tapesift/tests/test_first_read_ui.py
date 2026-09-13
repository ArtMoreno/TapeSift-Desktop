"""First Read is visible and navigable without becoming analyst truth."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit  # noqa: E402

from tapesift.core.config import AppSettings  # noqa: E402
from tapesift.models.clip import Clip  # noqa: E402
from tapesift.services.first_read_service import (  # noqa: E402
    DEFAULT_MODEL, FirstRead, store,
)
from tapesift.ui.settings_dialog import SettingsDialog  # noqa: E402
from tapesift.ui_core.clip_editor import ClipEditor  # noqa: E402
from tapesift.ui_core.clip_list import (  # noqa: E402
    COL_NUM, FIRST_READ_CALL_ROLE, ClipListWidget,
)
from tapesift.ui_core.first_read_ui import (  # noqa: E402
    needs_first_read_call, needs_logging, presentation_for,
)
from tapesift.ui_core.main_window_workflow import MainWindowWorkflow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def _clip(read: FirstRead, *, details=None, number=1) -> Clip:
    clip = Clip(
        start_ms=0, end_ms=10_000, clip_number=number,
        details=dict(details or {}),
    )
    clip.analysis = json.loads(store("", read))
    return clip


def test_settings_exposes_opt_in_masked_key_model_and_security_promise(qapp):
    settings = AppSettings()
    settings.save = lambda *args, **kwargs: None
    dialog = SettingsDialog(settings)

    assert dialog.first_read_enabled_check.isChecked() is False
    assert dialog.first_read_api_key_edit.echoMode() == \
        QLineEdit.EchoMode.Password
    assert dialog.first_read_model_edit.placeholderText() == DEFAULT_MODEL
    assert dialog.first_read_api_key_edit.isEnabled() is False
    promises = [label.text() for label in dialog.findChildren(QLabel)]
    assert any(
        "Play detection stays fully offline" in text
        and "only when you run it" in text
        for text in promises
    )

    dialog.first_read_enabled_check.setChecked(True)
    dialog.first_read_api_key_edit.setText("sk-user-key")
    dialog.first_read_model_edit.setText("provider/model")
    dialog._save()

    assert settings.first_read_enabled is True
    assert settings.first_read_api_key == "sk-user-key"
    assert settings.first_read_model == "provider/model"


@pytest.mark.parametrize(
    "read,state,chip,message",
    [
        (FirstRead(label="run", agreement=True, views=("run", "run")),
         "suggested", "RUN", "Suggestion only"),
        (FirstRead(label=None, agreement=False, views=("run", "pass")),
         "disagreed", "", "The two views disagree."),
        (FirstRead(label=None, agreement=False, error="Could not render."),
         "frames_failed", "", "Couldn't read frames."),
    ],
)
def test_play_details_has_three_non_editing_first_read_states(
        qapp, read, state, chip, message):
    clip = _clip(read, details={"quarter": "Q1"})
    before = dict(clip.details)
    editor = ClipEditor(AppSettings())
    editor.setObjectName("V2ReviewInspector")
    editor.set_analyst_mode(True)
    editor.set_clip(clip)

    assert not editor.first_read_card.isHidden()
    assert editor.first_read_card.property("state") == state
    assert editor.first_read_chip.text() == chip
    assert editor.first_read_chip.isHidden() is (not bool(chip))
    assert message in editor.first_read_message.text()
    assert clip.details == before
    assert "run_pass" not in clip.details


def test_agreed_suggestion_still_needs_an_explicit_analyst_call():
    clip = _clip(
        FirstRead(label="pass", agreement=True, views=("pass", "pass")),
        details={"quarter": "Q2"},
    )
    assert presentation_for(clip).chip_text == "PASS"
    assert needs_first_read_call(clip) is True
    assert needs_logging(clip) is True

    clip.details["run_pass"] = "Pass"
    assert needs_first_read_call(clip) is False
    assert needs_logging(clip) is False


def test_clip_ledger_uses_a_quiet_rail_and_counts_calls(qapp):
    unresolved = _clip(
        FirstRead(label="run", agreement=True, views=("run", "run")),
        details={"quarter": "Q1"}, number=1,
    )
    accepted = _clip(
        FirstRead(label="pass", agreement=True, views=("pass", "pass")),
        details={"quarter": "Q1", "run_pass": "Pass"}, number=2,
    )
    widget = ClipListWidget()
    widget.set_sidebar_mode(True)
    widget.set_clips(
        [unresolved, accepted], 60_000, "{clip_number}", "Game", "-")

    unresolved_item = widget.table.item(
        widget._clip_rows[unresolved.id], COL_NUM)
    accepted_item = widget.table.item(
        widget._clip_rows[accepted.id], COL_NUM)
    assert unresolved_item.data(FIRST_READ_CALL_ROLE) is True
    assert accepted_item.data(FIRST_READ_CALL_ROLE) is False
    assert "waiting for your run/pass call" in unresolved_item.toolTip()
    assert "1 First Read call" in widget.progress_label.text()

    widget.unlogged_filter_btn.click()
    assert not widget.table.isRowHidden(widget._clip_rows[unresolved.id])
    assert widget.table.isRowHidden(widget._clip_rows[accepted.id])


class _StatusBar:
    def __init__(self):
        self.message = ""

    def showMessage(self, message, *_args):
        self.message = message


def test_u_and_status_count_an_unresolved_first_read(qapp):
    resolved = Clip(
        start_ms=0, end_ms=4_000, clip_number=1,
        details={"quarter": "Q1", "run_pass": "Run"},
    )
    unresolved = _clip(
        FirstRead(label=None, agreement=False, views=("run", "pass")),
        details={"quarter": "Q1"}, number=2,
    )
    later_logged = Clip(
        start_ms=10_000, end_ms=14_000, clip_number=3,
        details={"quarter": "Q1", "run_pass": "Pass"},
    )
    landed = []
    fake = SimpleNamespace(
        session=SimpleNamespace(clips=[resolved, unresolved, later_logged]),
        settings=SimpleNamespace(review_mode=False, review_loop=False),
        review_label=QLabel(""),
        _current_row=lambda: 0,
        _land_on_row=landed.append,
        statusBar=lambda: _StatusBar(),
    )

    MainWindowWorkflow._goto_unlogged(fake, 1)
    assert landed == [1]

    MainWindowWorkflow._update_review_label(fake)
    assert "1 unlogged" in fake.review_label.text()
    assert "1 First Read call" in fake.review_label.text()


def test_security_policy_names_first_read_as_the_single_opt_in_exception():
    policy = (Path(__file__).resolve().parents[2] / "SECURITY.md").read_text(
        encoding="utf-8")
    assert "First Read is the single exception" in policy
    assert "off by default" in policy
    assert "user's own API key" in policy
    assert "still-frame contact sheets" in policy
    assert "do not pass through TapeSift" in policy
