"""Label-blind controls for reviewing proposed snap moments.

The panel is deliberately only a presentation/controller layer.  Review
decisions are written by ``TemporalReviewSession``; the open TapeSift project
and its Run/Pass fields are never edited here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from tapesift.ui_v2.elevation import apply_elevation

if TYPE_CHECKING:
    from tapesift.research.run_pass_temporal_review import (
        TemporalReviewSession,
    )


JUDGMENTS = ("correct", "early", "late", "unsure")
JUDGMENT_LABELS = {
    "correct": "Correct",
    "early": "Early",
    "late": "Late",
    "unsure": "Unsure",
}


def _value(source: object, *names: str, default: Any = None) -> Any:
    """Read one field from either a dataclass-like object or a mapping."""

    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
        return default
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    return default


def _angle_number(angle: object, fallback: int) -> int:
    raw = _value(angle, "angle", "angle_number", "index", default=fallback)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return fallback


def _judgment_text(value: object) -> str:
    if isinstance(value, str):
        return value.strip().casefold()
    nested = _value(value, "judgment", "verdict", "decision", default="")
    return str(nested).strip().casefold()


def _review_judgments(review: object) -> dict[int, str]:
    """Normalize the service's persisted review into angle -> judgment."""

    if review is None:
        return {}
    source = _value(
        review,
        "angle_judgments",
        "judgments",
        "angles",
        default=review,
    )
    result: dict[int, str] = {}
    if isinstance(source, Mapping):
        for raw_angle, raw_judgment in source.items():
            try:
                number = int(raw_angle)
            except (TypeError, ValueError):
                number = _angle_number(raw_judgment, 0)
            judgment = _judgment_text(raw_judgment)
            if number > 0 and judgment in JUDGMENTS:
                result[number] = judgment
        return result
    if isinstance(source, (list, tuple)):
        for fallback, entry in enumerate(source, start=1):
            number = _angle_number(entry, fallback)
            judgment = _judgment_text(entry)
            if judgment in JUDGMENTS:
                result[number] = judgment
    return result


def _moment_text(angle: object | None) -> str:
    if angle is None:
        return "No proposed moment available"
    local_ms = _value(angle, "proposed_onset_local_ms")
    try:
        milliseconds = int(local_ms)
    except (TypeError, ValueError):
        milliseconds = -1
    if milliseconds >= 0:
        return f"Proposed at {milliseconds / 1000:.3f}s in this angle"
    local_seconds = _value(
        angle,
        "onset_seconds",
        "local_onset_seconds",
        "proposed_seconds",
    )
    try:
        seconds = float(local_seconds)
    except (TypeError, ValueError):
        return "Proposed moment ready"
    return f"Proposed at {seconds:.3f}s in this angle"


class _AngleReviewCard(QFrame):
    """One angle's playback commands and mutually-exclusive judgment."""

    inspect_requested = Signal(int, bool)
    judgment_requested = Signal(int, str)

    def __init__(self, angle_number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.angle_number = angle_number
        self.angle: object | None = None
        self.setObjectName(f"TemporalReviewAngle{angle_number}")
        self.setProperty("temporalAngleCard", "true")
        self.setAccessibleName(f"Angle {angle_number} snap review")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 9)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.heading_label = QLabel(f"ANGLE {angle_number}")
        self.heading_label.setProperty("role", "eyebrow")
        header.addWidget(self.heading_label)
        header.addStretch(1)
        self.status_label = QLabel("Not reviewed")
        self.status_label.setProperty("role", "subtle")
        self.status_label.setAccessibleName(
            f"Angle {angle_number} review status: Not reviewed"
        )
        header.addWidget(self.status_label)
        layout.addLayout(header)

        self.moment_label = QLabel("No proposed moment available")
        self.moment_label.setProperty("role", "muted")
        self.moment_label.setAccessibleName(
            f"Angle {angle_number} proposed moment"
        )
        layout.addWidget(self.moment_label)

        playback_row = QHBoxLayout()
        playback_row.setSpacing(7)
        self.show_button = QPushButton("Show Proposed Moment")
        self.show_button.setAccessibleName(
            f"Show proposed moment for angle {angle_number}"
        )
        self.show_button.setToolTip(
            "Pause on the exact moment proposed by the extractor."
        )
        self.show_button.clicked.connect(
            lambda: self.inspect_requested.emit(self.angle_number, False)
        )
        playback_row.addWidget(self.show_button)

        self.play_button = QPushButton("Play Around")
        self.play_button.setAccessibleName(
            f"Play around proposed moment for angle {angle_number}"
        )
        self.play_button.setToolTip(
            "Play a short window before and after the proposed moment."
        )
        self.play_button.clicked.connect(
            lambda: self.inspect_requested.emit(self.angle_number, True)
        )
        playback_row.addWidget(self.play_button)
        playback_row.addStretch(1)
        layout.addLayout(playback_row)

        judgment_row = QHBoxLayout()
        judgment_row.setSpacing(6)
        self.judgment_group = QButtonGroup(self)
        self.judgment_group.setExclusive(True)
        self.judgment_buttons: dict[str, QPushButton] = {}
        descriptions = {
            "correct": "The proposed moment is at the snap.",
            "early": "The ball has not been snapped at the proposed moment.",
            "late": "The play is already underway at the proposed moment.",
            "unsure": "The footage does not support a confident judgment.",
        }
        for judgment in JUDGMENTS:
            label = JUDGMENT_LABELS[judgment]
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("temporalVerdict", "true")
            button.setAccessibleName(
                f"Mark angle {angle_number} {label}"
            )
            button.setAccessibleDescription(descriptions[judgment])
            button.setToolTip(descriptions[judgment])
            button.clicked.connect(
                lambda _checked=False, value=judgment:
                self.judgment_requested.emit(self.angle_number, value)
            )
            self.judgment_group.addButton(button)
            self.judgment_buttons[judgment] = button
            judgment_row.addWidget(button)
        judgment_row.addStretch(1)
        layout.addLayout(judgment_row)

    def set_angle(self, angle: object | None, judgment: str = "") -> None:
        self.angle = angle
        available = angle is not None
        self.moment_label.setText(_moment_text(angle))
        self.show_button.setEnabled(available)
        self.play_button.setEnabled(available)

        self.judgment_group.setExclusive(False)
        for name, button in self.judgment_buttons.items():
            button.setEnabled(available)
            button.setChecked(available and name == judgment)
        self.judgment_group.setExclusive(True)

        if not available:
            state = "Unavailable"
        elif judgment in JUDGMENT_LABELS:
            state = f"Saved: {JUDGMENT_LABELS[judgment]}"
        else:
            state = "Not reviewed"
        self.status_label.setText(state)
        self.status_label.setAccessibleName(
            f"Angle {self.angle_number} review status: {state}"
        )


class TemporalReviewPanel(QFrame):
    """Compact, full-width Temporal Review surface for the V2 workspace."""

    item_requested = Signal(object)
    inspect_requested = Signal(object, int, bool)

    def __init__(
        self,
        session: TemporalReviewSession,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("TemporalReviewPanel")
        self.setProperty("reviewPanel", "true")
        apply_elevation(self)
        self.setAccessibleName("Temporal snap timing review")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.setStyleSheet(
            """
            QFrame[temporalAngleCard="true"] {
                background-color: #0f1511;
                border: 1px solid #2a352d;
                border-radius: 5px;
            }
            QPushButton[temporalVerdict="true"]:checked {
                color: #061109;
                background-color: #39e07a;
                border-color: #39e07a;
                font-weight: 800;
            }
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 9, 12, 10)
        outer.setSpacing(7)

        heading_row = QHBoxLayout()
        heading_row.setSpacing(10)
        self.heading_label = QLabel("TEMPORAL REVIEW")
        self.heading_label.setProperty("role", "heading")
        heading_row.addWidget(self.heading_label)
        self.item_label = QLabel()
        self.item_label.setProperty("role", "eyebrow")
        heading_row.addWidget(self.item_label)
        heading_row.addStretch(1)
        self.progress_label = QLabel()
        self.progress_label.setProperty("role", "subtle")
        heading_row.addWidget(self.progress_label)
        outer.addLayout(heading_row)

        self.instruction_label = QLabel(
            "Show Proposed Moment; ball not snapped = Early, "
            "play underway = Late"
        )
        self.instruction_label.setWordWrap(True)
        self.instruction_label.setProperty("role", "subtle")
        self.instruction_label.setAccessibleName(
            "Snap timing review instructions"
        )
        outer.addWidget(self.instruction_label)

        self.reasons_label = QLabel()
        self.reasons_label.setWordWrap(True)
        self.reasons_label.setProperty("role", "muted")
        self.reasons_label.setAccessibleName(
            "Why this clip needs temporal review"
        )
        outer.addWidget(self.reasons_label)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(8)
        self.angle_cards: dict[int, _AngleReviewCard] = {}
        for number in (1, 2):
            card = _AngleReviewCard(number, self)
            card.inspect_requested.connect(self._inspect)
            card.judgment_requested.connect(self._judge)
            self.angle_cards[number] = card
            cards_row.addWidget(card, 1)
        outer.addLayout(cards_row)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.autosave_label = QLabel("Choices save immediately")
        self.autosave_label.setProperty("role", "muted")
        self.autosave_label.setAccessibleName(
            "Temporal review choices save immediately"
        )
        footer.addWidget(self.autosave_label)
        footer.addStretch(1)

        self.previous_button = QPushButton("Previous")
        self.previous_button.setAccessibleName(
            "Go to previous temporal review clip"
        )
        self.previous_button.clicked.connect(self._previous)
        footer.addWidget(self.previous_button)

        self.save_next_button = QPushButton("Save && Next")
        self.save_next_button.setProperty("primary", "false")
        self.save_next_button.setAccessibleName(
            "Save completed judgments and go to next clip"
        )
        self.save_next_button.clicked.connect(self._save_and_next)
        footer.addWidget(self.save_next_button)

        self.next_unreviewed_button = QPushButton("Next Unreviewed")
        self.next_unreviewed_button.setAccessibleName(
            "Go to next unreviewed temporal clip"
        )
        self.next_unreviewed_button.clicked.connect(self._next_unreviewed)
        footer.addWidget(self.next_unreviewed_button)
        outer.addLayout(footer)

        self.refresh()

    def _current_item(self) -> object | None:
        return getattr(
            self.session,
            "current",
            getattr(self.session, "current_item", None),
        )

    def _item_angles(self, item: object | None) -> dict[int, object]:
        if item is None:
            return {}
        raw_angles = _value(item, "angles", default=())
        return {
            _angle_number(angle, fallback): angle
            for fallback, angle in enumerate(raw_angles or (), start=1)
        }

    def _review_for(self, item: object | None) -> object | None:
        if item is None:
            return None
        return self.session.review_for(_value(item, "item_id", default=""))

    def _item_complete(self, item: object | None) -> bool:
        complete = _value(item, "complete", default=None)
        if complete is not None:
            return bool(complete)
        angles = self._item_angles(item)
        if not angles:
            return False
        judgments = _review_judgments(self._review_for(item))
        return all(judgments.get(number) in JUDGMENTS for number in angles)

    def _next_unreviewed_index(self) -> int | None:
        items = list(getattr(self.session, "items", ()) or ())
        if len(items) < 2:
            return None
        current = int(getattr(self.session, "current_index", 0))
        for offset in range(1, len(items)):
            index = (current + offset) % len(items)
            if not self._item_complete(items[index]):
                return index
        return None

    def refresh(self, *, emit_item: bool = False) -> None:
        """Refresh visible state after a service action or external resume."""

        item = self._current_item()
        total = len(getattr(self.session, "items", ()) or ())
        completed = int(getattr(self.session, "completed_count", 0))
        pending = int(getattr(self.session, "pending_count", 0))
        index = int(getattr(self.session, "current_index", 0))

        self.progress_label.setText(
            f"{completed} of {total} complete | {pending} remaining"
        )
        self.progress_label.setAccessibleName(
            f"Temporal review progress: {completed} of {total} complete, "
            f"{pending} remaining"
        )

        if item is None:
            self.item_label.setText("No review clips")
            self.reasons_label.setText("No Temporal Review item is available.")
        else:
            project = str(_value(item, "project_name", default="")).strip()
            clip = _value(item, "clip_number", default="?")
            try:
                clip_text = f"{int(clip):02d}"
            except (TypeError, ValueError):
                clip_text = str(clip)
            self.item_label.setText(
                f"{project} | Clip {clip_text}" if project else
                f"Clip {clip_text}"
            )
            reasons = _value(item, "reasons", default=()) or ()
            reason_text = ", ".join(
                str(reason).replace("_", " ") for reason in reasons
            )
            self.reasons_label.setText(
                f"Why review: {reason_text}"
                if reason_text else
                "Why review: proposed timing needs a visual check."
            )

        judgments = _review_judgments(self._review_for(item))
        angles = self._item_angles(item)
        for number, card in self.angle_cards.items():
            card.set_angle(angles.get(number), judgments.get(number, ""))

        complete = self._item_complete(item)
        self.previous_button.setEnabled(item is not None and index > 0)
        can_save_next = (
            item is not None and complete and index + 1 < total
        )
        self.save_next_button.setEnabled(can_save_next)
        self.save_next_button.setProperty(
            "primary", "true" if can_save_next else "false")
        self.save_next_button.style().unpolish(self.save_next_button)
        self.save_next_button.style().polish(self.save_next_button)
        self.next_unreviewed_button.setEnabled(
            self._next_unreviewed_index() is not None
        )

        if emit_item and item is not None:
            self.item_requested.emit(item)

    def activate_current(self) -> None:
        """Ask the owning window to select the panel's current clip."""

        item = self._current_item()
        if item is not None:
            self.item_requested.emit(item)

    def _inspect(self, angle_number: int, play_around: bool) -> None:
        item = self._current_item()
        if item is not None and angle_number in self._item_angles(item):
            self.inspect_requested.emit(item, angle_number, play_around)

    def _judge(self, angle_number: int, judgment: str) -> None:
        item = self._current_item()
        if item is None or angle_number not in self._item_angles(item):
            return
        item_id = str(_value(item, "item_id", default=""))
        self.session.judge_angle(item_id, angle_number, judgment)
        current = self._current_item()
        self.refresh(emit_item=(
            current is not None
            and _value(current, "item_id", default="") != item_id
        ))

    def _previous(self) -> None:
        before = int(getattr(self.session, "current_index", 0))
        self.session.navigate(-1)
        self.refresh(emit_item=(
            int(getattr(self.session, "current_index", 0)) != before
        ))

    def _save_and_next(self) -> None:
        item = self._current_item()
        if not self._item_complete(item):
            return
        before = int(getattr(self.session, "current_index", 0))
        self.session.navigate(1)
        self.refresh(emit_item=(
            int(getattr(self.session, "current_index", 0)) != before
        ))

    def _next_unreviewed(self) -> None:
        index = self._next_unreviewed_index()
        if index is None:
            return
        self.session.set_cursor(index)
        self.refresh(emit_item=True)


__all__ = [
    "JUDGMENTS",
    "TemporalReviewPanel",
]
