"""Focused behavior for the label-blind exact-snap calibration panel."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QLabel,
)

from tapesift.ui_v2.snap_calibration_panel import (  # noqa: E402
    SnapCalibrationPanel,
)


@dataclass
class _Angle:
    angle: int
    proposed_onset_ms: int
    actual_snap_ms: int | None = None
    snap_status: str = ""
    private_run_pass_label: str = "SECRET-PASS-LABEL"

    @property
    def snap_delta_ms(self) -> int | None:
        if self.actual_snap_ms is None:
            return None
        return self.actual_snap_ms - self.proposed_onset_ms


@dataclass
class _Item:
    item_id: str
    project_name: str
    clip_number: int
    angles: tuple[_Angle, ...]
    private_play_call: str = "SECRET-RPO-PASS"


@dataclass
class _FakeSession:
    items: list[_Item]
    current_index: int = 0
    unavailable_calls: list[tuple[str, int, str]] = field(
        default_factory=list
    )
    cursor_calls: list[int] = field(default_factory=list)

    @property
    def current(self) -> _Item | None:
        return self.items[self.current_index] if self.items else None

    @property
    def completed_count(self) -> int:
        return sum(self.item_complete(item) for item in self.items)

    @property
    def pending_count(self) -> int:
        return len(self.items) - self.completed_count

    def review_for(self, item_id: str) -> _Item:
        return next(item for item in self.items if item.item_id == item_id)

    def item_complete(self, item: _Item) -> bool:
        return bool(item.angles) and all(
            angle.snap_status in {"marked", "not_visible", "unsure"}
            for angle in item.angles
        )

    def mark_snap_unavailable(
        self,
        item_id: str,
        angle_number: int,
        status: str,
    ) -> None:
        item = self.review_for(item_id)
        angle = next(
            value for value in item.angles
            if value.angle == angle_number
        )
        angle.actual_snap_ms = None
        angle.snap_status = status
        self.unavailable_calls.append((item_id, angle_number, status))

    def set_cursor(self, index: int) -> None:
        self.current_index = max(0, min(index, len(self.items) - 1))
        self.cursor_calls.append(self.current_index)

    def navigate(self, direction: int) -> None:
        self.set_cursor(self.current_index + direction)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def items() -> list[_Item]:
    return [
        _Item(
            "virginia:5",
            "Virginia vs Stanford",
            5,
            (
                _Angle(1, 104_125),
                _Angle(2, 117_875),
            ),
        ),
        _Item(
            "alabama:17",
            "Alabama vs Auburn",
            17,
            (
                _Angle(1, 367_250),
                _Angle(2, 381_625),
            ),
        ),
    ]


def _visible_words(panel: SnapCalibrationPanel) -> str:
    labels = [label.text() for label in panel.findChildren(QLabel)]
    buttons = [
        button.text() for button in panel.findChildren(QAbstractButton)
    ]
    return "\n".join([*labels, *buttons])


def test_panel_is_compact_label_blind_and_explains_frame_step(
    qapp: QApplication,
    items: list[_Item],
) -> None:
    panel = SnapCalibrationPanel(_FakeSession(items))

    assert panel.heading_label.text() == "EXACT SNAP CALIBRATION"
    assert panel.item_label.text() == "Virginia vs Stanford | Clip 05"
    assert panel.instruction_label.text() == (
        "Find the first frame the snap begins. Pause, use Left / Right "
        "to frame-step, then Mark Current Frame."
    )
    assert panel.progress_label.text() == \
        "0 of 2 complete | 2 remaining"
    assert panel.angle_cards[1].proposed_label.text() == \
        "Proposed snap: 01:44.125"
    assert panel.angle_cards[2].proposed_label.text() == \
        "Proposed snap: 01:57.875"
    assert "SECRET-PASS-LABEL" not in _visible_words(panel)
    assert "SECRET-RPO-PASS" not in _visible_words(panel)

    first = panel.angle_cards[1]
    assert first.mark_button.accessibleName() == \
        "Mark current player frame as angle 1 snap"
    assert first.show_button.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert first.play_button.focusPolicy() != Qt.FocusPolicy.NoFocus
    assert first.mark_button.focusPolicy() != Qt.FocusPolicy.NoFocus

    panel.deleteLater()
    qapp.processEvents()


def test_playback_and_exact_frame_buttons_emit_authoritative_item(
    qapp: QApplication,
    items: list[_Item],
) -> None:
    session = _FakeSession(items)
    panel = SnapCalibrationPanel(session)
    inspections: list[tuple[object, int, bool]] = []
    exact: list[tuple[object, int]] = []
    panel.inspect_requested.connect(
        lambda item, angle, around:
        inspections.append((item, angle, around))
    )
    panel.exact_snap_requested.connect(
        lambda item, angle: exact.append((item, angle))
    )

    panel.angle_cards[2].show_button.click()
    panel.angle_cards[2].play_button.click()
    panel.angle_cards[2].mark_button.click()

    assert inspections == [
        (items[0], 2, False),
        (items[0], 2, True),
    ]
    assert exact == [(items[0], 2)]
    # The owner reads the player and persists this signal; the panel must not
    # invent a source timestamp by itself.
    assert items[0].angles[1].snap_status == ""

    panel.deleteLater()
    qapp.processEvents()


def test_exact_saved_frame_and_delta_are_visible_after_owner_refresh(
    qapp: QApplication,
    items: list[_Item],
) -> None:
    session = _FakeSession(items)
    panel = SnapCalibrationPanel(session)
    angle = items[0].angles[0]

    angle.actual_snap_ms = 104_250
    angle.snap_status = "marked"
    panel.refresh()

    assert panel.angle_cards[1].status_label.text() == (
        "Saved exact: 01:44.250 | +125 ms vs proposed"
    )
    assert panel.progress_label.text() == \
        "0 of 2 complete | 2 remaining"

    panel.deleteLater()
    qapp.processEvents()


def test_unavailable_states_save_and_completion_controls_navigation(
    qapp: QApplication,
    items: list[_Item],
) -> None:
    session = _FakeSession(items)
    panel = SnapCalibrationPanel(session)
    requested: list[object] = []
    panel.item_requested.connect(requested.append)

    panel.angle_cards[1].unavailable_buttons["not_visible"].click()
    panel.angle_cards[2].unavailable_buttons["unsure"].click()

    assert session.unavailable_calls == [
        ("virginia:5", 1, "not_visible"),
        ("virginia:5", 2, "unsure"),
    ]
    assert panel.angle_cards[1].status_label.text() == \
        "Saved: Not visible"
    assert panel.angle_cards[2].status_label.text() == "Saved: Unsure"
    assert panel.progress_label.text() == \
        "1 of 2 complete | 1 remaining"
    assert panel.save_next_button.isEnabled()

    panel.save_next_button.click()

    assert session.current_index == 1
    assert requested == [items[1]]
    assert panel.item_label.text() == "Alabama vs Auburn | Clip 17"

    panel.deleteLater()
    qapp.processEvents()


def test_next_unreviewed_wraps_past_completed_items(
    qapp: QApplication,
) -> None:
    records = [
        _Item(
            f"game:{number}",
            "Game",
            number,
            (
                _Angle(1, number * 10_000),
                _Angle(2, number * 10_000 + 5_000),
            ),
        )
        for number in (1, 2, 3)
    ]
    for angle in records[0].angles + records[1].angles:
        angle.snap_status = "not_visible"
    session = _FakeSession(records)
    panel = SnapCalibrationPanel(session)
    requested: list[object] = []
    panel.item_requested.connect(requested.append)

    panel.next_unreviewed_button.click()

    assert session.current_index == 2
    assert session.cursor_calls == [2]
    assert requested == [records[2]]

    panel.deleteLater()
    qapp.processEvents()


def test_missing_second_angle_has_clear_disabled_state(
    qapp: QApplication,
) -> None:
    item = _Item(
        "game:9",
        "Game",
        9,
        (_Angle(1, 90_000),),
    )
    panel = SnapCalibrationPanel(_FakeSession([item]))
    second = panel.angle_cards[2]

    assert second.status_label.text() == "Unavailable"
    assert second.proposed_label.text() == "No proposed snap available"
    assert not second.show_button.isEnabled()
    assert not second.play_button.isEnabled()
    assert not second.mark_button.isEnabled()
    assert not any(
        button.isEnabled()
        for button in second.unavailable_buttons.values()
    )

    panel.deleteLater()
    qapp.processEvents()
