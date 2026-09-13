"""Focused UI tests for the research-only segmentation verifier."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tapesift.research.segmentation_verification import (  # noqa: E402
    VerificationItem,
    VerificationSession,
)
from tapesift.research.segmentation_verifier_window import (  # noqa: E402
    SegmentationVerifierWindow,
)
from tapesift.ui_v2.control_center import ControlCenterDeck  # noqa: E402


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _item(index: int, *, film_id: str = "film") -> VerificationItem:
    start_ms = 1_000 + index * 10_000
    return VerificationItem(
        item_id=f"{film_id}:play:{index:04d}",
        film_id=film_id,
        film_name=film_id,
        source_file=f"C:/{film_id}.mp4",
        analysis_source=f"C:/{film_id}.mp4",
        frame_rate=30.0,
        film_duration_ms=120_000,
        start_ms=start_ms,
        end_ms=start_ms + 8_000,
        original_start_ms=start_ms,
        original_end_ms=start_ms + 8_000,
        candidate_kind="play",
        candidate_index=index,
        prediction_indices=[index],
    )


def _window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pair_mode: bool = False,
    items: list[VerificationItem] | None = None,
) -> SegmentationVerifierWindow:
    session = VerificationSession.create(
        tmp_path / "state.json",
        tmp_path / "truth.jsonl",
        "verifier-test",
        items or [_item(0)],
    )
    monkeypatch.setattr(
        SegmentationVerifierWindow,
        "_load_current_source",
        lambda _self: None,
    )
    return SegmentationVerifierWindow(session, pair_mode=pair_mode)


@pytest.mark.parametrize("pair_mode", [False, True])
def test_loop_defaults_off_for_every_review_mode(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    pair_mode: bool,
) -> None:
    window = _window(tmp_path, monkeypatch, pair_mode=pair_mode)

    assert window.loop_box.isChecked() is False
    assert isinstance(window.control_center, ControlCenterDeck)
    assert window.player.control_center is window.control_center
    assert window.control_center.bound_player is window.player
    assert not window.player.isAncestorOf(window.control_center)

    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_action_error_stays_visible_until_success_or_navigation(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(tmp_path, monkeypatch)

    def fail() -> None:
        raise ValueError("Split point must be inside the current segment")

    window._run_action(fail)
    assert window.action_error_label.isVisibleTo(window)
    assert "Split point" in window.action_error_label.text()

    window._run_action(lambda: None)
    assert window.action_error_label.isHidden()

    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_merge_buttons_follow_session_preflight(
    qapp: QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = _window(
        tmp_path,
        monkeypatch,
        items=[_item(0), _item(1)],
    )

    assert window.merge_previous_button.isEnabled() is False
    assert window.merge_next_button.isEnabled() is True
    assert "no adjacent" in window.merge_previous_button.toolTip().casefold()

    window._select_index(1)
    assert window.merge_previous_button.isEnabled() is True
    assert window.merge_next_button.isEnabled() is False

    window.close()
    window.deleteLater()
    qapp.processEvents()
