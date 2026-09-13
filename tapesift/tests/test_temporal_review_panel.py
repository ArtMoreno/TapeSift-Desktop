"""Focused UI behavior for the label-blind Temporal Review panel."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QLabel,
)

from tapesift.research.run_pass_temporal_review import (  # noqa: E402
    TemporalReviewAngle,
    TemporalReviewItem,
    TemporalReviewSession,
    TemporalReviewSplit,
)
from tapesift.ui_v2.temporal_review_panel import (  # noqa: E402
    TemporalReviewPanel,
)


@dataclass(frozen=True)
class _Angle:
    angle: int
    onset_seconds: float


@dataclass(frozen=True)
class _Item:
    item_id: str
    project_name: str
    clip_number: int
    reasons: tuple[str, ...]
    angles: tuple[_Angle, ...]
    # A sentinel representing fields present in the raw feature manifest.
    # The panel must never surface it.
    private_run_pass_label: str = "SECRET-RUN-PASS-LABEL"


@dataclass
class _FakeSession:
    items: list[_Item]
    current_index: int = 0
    reviews: dict[str, dict[int, str]] = field(default_factory=dict)
    judge_calls: list[tuple[int, str]] = field(default_factory=list)
    cursor_calls: list[int] = field(default_factory=list)

    @property
    def current(self) -> _Item | None:
        return self.items[self.current_index] if self.items else None

    @property
    def completed_count(self) -> int:
        return sum(self._complete(item) for item in self.items)

    @property
    def pending_count(self) -> int:
        return len(self.items) - self.completed_count

    def _complete(self, item: _Item) -> bool:
        saved = self.reviews.get(item.item_id, {})
        return len(item.angles) == 2 and all(
            angle.angle in saved for angle in item.angles
        )

    def review_for(self, item_id: str) -> object:
        item = next(value for value in self.items if value.item_id == item_id)
        saved = self.reviews.get(item_id, {})
        # Mirror the real service: review_for returns an item whose angle
        # objects carry their own persisted judgments.
        return SimpleNamespace(angles=[
            SimpleNamespace(
                angle=angle.angle,
                judgment=saved.get(angle.angle, ""),
            )
            for angle in item.angles
        ])

    def judge_angle(
        self,
        item_id: str,
        angle_number: int,
        judgment: str,
    ) -> None:
        assert self.current is not None
        assert item_id == self.current.item_id
        self.judge_calls.append((angle_number, judgment))
        self.reviews.setdefault(self.current.item_id, {})[
            angle_number
        ] = judgment

    def set_cursor(self, index: int) -> None:
        self.current_index = max(0, min(index, len(self.items) - 1))
        self.cursor_calls.append(self.current_index)

    def navigate(self, direction: int) -> None:
        self.set_cursor(self.current_index + direction)


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def two_items() -> list[_Item]:
    return [
        _Item(
            item_id="virginia:5",
            project_name="Virginia vs Stanford",
            clip_number=5,
            reasons=("weak_post_pre_motion_ratio",),
            angles=(_Angle(1, 4.125), _Angle(2, 13.0)),
        ),
        _Item(
            item_id="virginia:6",
            project_name="Virginia vs Stanford",
            clip_number=6,
            reasons=("cse_reports_more_than_two_angles",),
            angles=(_Angle(1, 2.125), _Angle(2, 3.375)),
        ),
    ]


def _visible_words(panel: TemporalReviewPanel) -> str:
    labels = [label.text() for label in panel.findChildren(QLabel)]
    buttons = [
        button.text() for button in panel.findChildren(QAbstractButton)
    ]
    return "\n".join([*labels, *buttons])


def test_panel_is_label_blind_clear_and_keyboard_accessible(
    qapp: QApplication,
    two_items: list[_Item],
) -> None:
    panel = TemporalReviewPanel(_FakeSession(two_items))

    assert panel.heading_label.text() == "TEMPORAL REVIEW"
    assert panel.item_label.text() == "Virginia vs Stanford | Clip 05"
    assert panel.instruction_label.text() == (
        "Show Proposed Moment; ball not snapped = Early, "
        "play underway = Late"
    )
    assert "weak post pre motion ratio" in panel.reasons_label.text()
    assert "SECRET-RUN-PASS-LABEL" not in _visible_words(panel)
    assert panel.progress_label.text() == \
        "0 of 2 complete | 2 remaining"

    first = panel.angle_cards[1]
    assert first.show_button.accessibleName() == \
        "Show proposed moment for angle 1"
    assert first.play_button.accessibleName() == \
        "Play around proposed moment for angle 1"
    for judgment, button in first.judgment_buttons.items():
        assert f"angle 1 {judgment}".casefold() in \
            button.accessibleName().casefold()
        assert button.accessibleDescription()
        assert button.focusPolicy() != Qt.FocusPolicy.NoFocus

    panel.deleteLater()
    qapp.processEvents()


def test_inspect_buttons_emit_item_angle_and_play_mode(
    qapp: QApplication,
    two_items: list[_Item],
) -> None:
    session = _FakeSession(two_items)
    panel = TemporalReviewPanel(session)
    requested: list[tuple[object, int, bool]] = []
    panel.inspect_requested.connect(
        lambda item, angle, around:
        requested.append((item, angle, around))
    )

    panel.angle_cards[2].show_button.click()
    panel.angle_cards[2].play_button.click()

    assert requested == [
        (two_items[0], 2, False),
        (two_items[0], 2, True),
    ]

    panel.deleteLater()
    qapp.processEvents()


def test_judgments_autosave_and_make_saved_state_explicit(
    qapp: QApplication,
    two_items: list[_Item],
) -> None:
    session = _FakeSession(two_items)
    panel = TemporalReviewPanel(session)

    panel.angle_cards[1].judgment_buttons["correct"].click()

    assert session.judge_calls == [(1, "correct")]
    assert session.reviews["virginia:5"] == {1: "correct"}
    assert panel.angle_cards[1].status_label.text() == "Saved: Correct"
    assert panel.angle_cards[1].judgment_buttons["correct"].isChecked()
    assert panel.progress_label.text() == \
        "0 of 2 complete | 2 remaining"
    assert not panel.save_next_button.isEnabled()

    panel.angle_cards[2].judgment_buttons["late"].click()

    assert session.judge_calls[-1] == (2, "late")
    assert panel.angle_cards[2].status_label.text() == "Saved: Late"
    assert panel.progress_label.text() == \
        "1 of 2 complete | 1 remaining"
    assert panel.save_next_button.isEnabled()

    panel.deleteLater()
    qapp.processEvents()


def test_save_next_and_next_unreviewed_emit_authoritative_item(
    qapp: QApplication,
) -> None:
    items = [
        _Item(
            "game:1", "Game", 1, (),
            (_Angle(1, 2.0), _Angle(2, 3.0)),
        ),
        _Item(
            "game:2", "Game", 2, (),
            (_Angle(1, 2.0), _Angle(2, 3.0)),
        ),
        _Item(
            "game:3", "Game", 3, (),
            (_Angle(1, 2.0), _Angle(2, 3.0)),
        ),
    ]
    session = _FakeSession(
        items,
        reviews={
            "game:1": {1: "correct", 2: "correct"},
            "game:2": {1: "early", 2: "late"},
        },
    )
    panel = TemporalReviewPanel(session)
    requested: list[object] = []
    panel.item_requested.connect(requested.append)

    assert panel.save_next_button.isEnabled()
    panel.save_next_button.click()

    assert session.current_index == 1
    assert requested == [items[1]]
    assert panel.item_label.text() == "Game | Clip 02"

    session.set_cursor(0)
    panel.refresh()
    panel.next_unreviewed_button.click()

    assert session.current_index == 2
    assert requested[-1] is items[2]
    assert panel.item_label.text() == "Game | Clip 03"

    panel.deleteLater()
    qapp.processEvents()


def test_missing_second_angle_has_clear_disabled_state(
    qapp: QApplication,
) -> None:
    item = _Item(
        "game:9",
        "Game",
        9,
        ("one_angle_fallback",),
        (_Angle(1, 5.0),),
    )
    session = _FakeSession([item])
    panel = TemporalReviewPanel(session)
    second = panel.angle_cards[2]

    assert second.status_label.text() == "Unavailable"
    assert second.moment_label.text() == "No proposed moment available"
    assert not second.show_button.isEnabled()
    assert not second.play_button.isEnabled()
    assert not any(
        button.isEnabled()
        for button in second.judgment_buttons.values()
    )

    panel.angle_cards[1].judgment_buttons["unsure"].click()
    assert session.completed_count == 0
    assert panel.progress_label.text() == \
        "0 of 1 complete | 1 remaining"

    panel.deleteLater()
    qapp.processEvents()


def test_panel_matches_real_session_contract_and_autosaves(
    qapp: QApplication,
    tmp_path,
) -> None:
    item = TemporalReviewItem(
        item_id="cohort:clip:5",
        research_cohort_id="cohort",
        project_name="Virginia vs Stanford",
        project_path=str(tmp_path / "virginia.tapesift"),
        project_file_name="virginia.tapesift",
        source_video_path=str(tmp_path / "source.mp4"),
        clip_id="clip-5",
        clip_number=5,
        start_ms=100_000,
        end_ms=130_000,
        reasons=["weak_post_pre_motion_ratio"],
        split=TemporalReviewSplit(),
        angles=[
            TemporalReviewAngle(
                angle=1,
                start_seconds=0.0,
                end_seconds=10.0,
                onset_seconds=4.125,
                proposed_onset_ms=104_125,
            ),
            TemporalReviewAngle(
                angle=2,
                start_seconds=10.625,
                end_seconds=30.0,
                onset_seconds=7.25,
                proposed_onset_ms=117_875,
            ),
        ],
    )
    state_path = tmp_path / "review.state.json"
    judgments_path = tmp_path / "review.judgments.jsonl"
    session = TemporalReviewSession(
        state_path,
        judgments_path,
        "test-review",
        [],
        [item],
    )
    session.save()
    panel = TemporalReviewPanel(session)

    panel.angle_cards[1].judgment_buttons["correct"].click()
    panel.angle_cards[2].judgment_buttons["late"].click()

    assert session.current is item
    assert session.current_index == 0
    assert item.complete
    assert item.angles[0].judgment == "correct"
    assert item.angles[1].judgment == "late"
    assert panel.angle_cards[1].status_label.text() == "Saved: Correct"
    assert panel.angle_cards[2].status_label.text() == "Saved: Late"
    assert "correct" in state_path.read_text(encoding="utf-8")
    assert "late" in judgments_path.read_text(encoding="utf-8")

    panel.deleteLater()
    qapp.processEvents()
