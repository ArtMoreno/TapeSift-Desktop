"""Label-blind controls for recording exact snap frames.

This widget intentionally owns no playback or project-writing logic.  It
asks the main window to seek/play video and emits ``exact_snap_requested``
when the reviewer has parked the authoritative player on the correct frame.
Only the separate research session is mutated by the two unavailable-state
buttons.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Signal
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


UNAVAILABLE_STATUSES = ("not_visible", "unsure")
STATUS_LABELS = {
    "not_visible": "Not visible",
    "unsure": "Unsure",
}


def _value(source: object, *names: str, default: Any = None) -> Any:
    """Read a field from either a mapping or a dataclass-like object."""

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


def _milliseconds(value: object) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _source_timestamp(milliseconds: int) -> str:
    """Format an absolute source-film position without losing frame detail."""

    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def _proposed_text(angle: object | None) -> str:
    if angle is None:
        return "No proposed snap available"
    source_ms = _milliseconds(_value(
        angle,
        "proposed_onset_ms",
        "proposed_snap_ms",
        "proposed_source_ms",
        "proposed_onset_source_ms",
    ))
    if source_ms is not None:
        return f"Proposed snap: {_source_timestamp(source_ms)}"

    local_ms = _milliseconds(_value(
        angle,
        "proposed_onset_local_ms",
        "proposed_local_ms",
    ))
    if local_ms is not None:
        return f"Proposed snap: {local_ms / 1000:.3f}s in this angle"

    local_seconds = _value(
        angle,
        "onset_seconds",
        "proposed_seconds",
        "local_onset_seconds",
    )
    try:
        return f"Proposed snap: {float(local_seconds):.3f}s in this angle"
    except (TypeError, ValueError):
        return "Proposed snap ready"


def _review_angle(review: object | None, angle_number: int) -> object | None:
    """Find one angle's saved result across mapping/dataclass contracts."""

    if review is None:
        return None

    angle_method = getattr(review, "angle", None)
    if callable(angle_method):
        try:
            return angle_method(angle_number)
        except (KeyError, IndexError, ValueError):
            pass

    source = _value(
        review,
        "angle_reviews",
        "reviews",
        "angles",
        default=review,
    )
    if isinstance(source, Mapping):
        for key in (angle_number, str(angle_number)):
            if key in source:
                return source[key]
        return None
    if isinstance(source, (list, tuple)):
        for fallback, entry in enumerate(source, start=1):
            if _angle_number(entry, fallback) == angle_number:
                return entry
    return None


def _saved_status(
    review_angle: object | None,
) -> tuple[str, int | None, int | None]:
    if review_angle is None:
        return "", None, None
    status = str(_value(
        review_angle,
        "status",
        "snap_status",
        "review_status",
        default="",
    )).strip().casefold().replace("-", "_").replace(" ", "_")
    exact_ms = _milliseconds(_value(
        review_angle,
        "actual_snap_ms",
        "exact_snap_ms",
        "corrected_snap_ms",
        "marked_snap_ms",
        "snap_ms",
    ))
    raw_delta = _value(
        review_angle,
        "snap_delta_ms",
        "delta_ms",
        default=None,
    )
    try:
        delta_ms = None if raw_delta is None else int(raw_delta)
    except (TypeError, ValueError):
        delta_ms = None
    if delta_ms is None and exact_ms is not None:
        proposed_ms = _milliseconds(_value(
            review_angle,
            "proposed_onset_ms",
            "proposed_snap_ms",
            "proposed_source_ms",
        ))
        if proposed_ms is not None:
            delta_ms = exact_ms - proposed_ms
    if exact_ms is not None:
        return "exact", exact_ms, delta_ms
    if status in {"not_visible", "notvisible"}:
        return "not_visible", None, None
    if status == "unsure":
        return "unsure", None, None
    return "", None, None


class _SnapAngleCard(QFrame):
    """One camera angle's proposed moment and calibration actions."""

    inspect_requested = Signal(int, bool)
    exact_snap_requested = Signal(int)
    unavailable_requested = Signal(int, str)

    def __init__(self, angle_number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.angle_number = angle_number
        self.angle: object | None = None
        self.setObjectName(f"SnapCalibrationAngle{angle_number}")
        self.setProperty("snapCalibrationCard", "true")
        self.setAccessibleName(f"Angle {angle_number} exact snap calibration")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 7, 10, 8)
        layout.setSpacing(5)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.heading_label = QLabel(f"ANGLE {angle_number}")
        self.heading_label.setProperty("role", "eyebrow")
        header.addWidget(self.heading_label)
        header.addStretch(1)
        self.status_label = QLabel("Needs exact frame")
        self.status_label.setProperty("role", "subtle")
        header.addWidget(self.status_label)
        layout.addLayout(header)

        self.proposed_label = QLabel("No proposed snap available")
        self.proposed_label.setProperty("role", "muted")
        self.proposed_label.setAccessibleName(
            f"Angle {angle_number} proposed snap timestamp"
        )
        layout.addWidget(self.proposed_label)

        self.step_instruction_label = QLabel(
            "Pause, then use Left / Right to frame-step."
        )
        self.step_instruction_label.setProperty("role", "subtle")
        self.step_instruction_label.setAccessibleName(
            f"Angle {angle_number} exact frame instructions"
        )
        layout.addWidget(self.step_instruction_label)

        actions = QHBoxLayout()
        actions.setSpacing(6)

        self.show_button = QPushButton("Show Proposed")
        self.show_button.setAccessibleName(
            f"Show proposed snap for angle {angle_number}"
        )
        self.show_button.setToolTip(
            "Pause the player on the detector's proposed snap."
        )
        self.show_button.clicked.connect(
            lambda: self.inspect_requested.emit(self.angle_number, False)
        )
        actions.addWidget(self.show_button)

        self.play_button = QPushButton("Play Around")
        self.play_button.setAccessibleName(
            f"Play around proposed snap for angle {angle_number}"
        )
        self.play_button.setToolTip(
            "Play a short window around the proposed snap."
        )
        self.play_button.clicked.connect(
            lambda: self.inspect_requested.emit(self.angle_number, True)
        )
        actions.addWidget(self.play_button)

        self.mark_button = QPushButton("Mark Current Frame")
        self.mark_button.setProperty("primary", "true")
        self.mark_button.setAccessibleName(
            f"Mark current player frame as angle {angle_number} snap"
        )
        self.mark_button.setToolTip(
            "Save the player's current frame as the exact snap."
        )
        self.mark_button.clicked.connect(
            lambda: self.exact_snap_requested.emit(self.angle_number)
        )
        actions.addWidget(self.mark_button)

        self.unavailable_group = QButtonGroup(self)
        self.unavailable_group.setExclusive(True)
        self.unavailable_buttons: dict[str, QPushButton] = {}
        descriptions = {
            "not_visible": "The snap is not visible in this camera angle.",
            "unsure": "The snap is visible, but an exact frame is uncertain.",
        }
        for status in UNAVAILABLE_STATUSES:
            button = QPushButton(STATUS_LABELS[status])
            button.setCheckable(True)
            button.setProperty("snapUnavailable", "true")
            button.setAccessibleName(
                f"Mark angle {angle_number} {STATUS_LABELS[status]}"
            )
            button.setAccessibleDescription(descriptions[status])
            button.setToolTip(descriptions[status])
            button.clicked.connect(
                lambda _checked=False, value=status:
                self.unavailable_requested.emit(self.angle_number, value)
            )
            self.unavailable_group.addButton(button)
            self.unavailable_buttons[status] = button
            actions.addWidget(button)
        actions.addStretch(1)
        layout.addLayout(actions)

    def set_angle(
        self,
        angle: object | None,
        review_angle: object | None,
    ) -> None:
        self.angle = angle
        available = angle is not None
        self.proposed_label.setText(_proposed_text(angle))

        status, exact_ms, delta_ms = _saved_status(review_angle)
        if not available:
            status_text = "Unavailable"
        elif status == "exact" and exact_ms is not None:
            status_text = f"Saved exact: {_source_timestamp(exact_ms)}"
            if delta_ms is not None:
                status_text += f" | {delta_ms:+d} ms vs proposed"
        elif status in STATUS_LABELS:
            status_text = f"Saved: {STATUS_LABELS[status]}"
        else:
            status_text = "Needs exact frame"
        self.status_label.setText(status_text)
        self.status_label.setAccessibleName(
            f"Angle {self.angle_number} calibration status: {status_text}"
        )

        for button in (
            self.show_button,
            self.play_button,
            self.mark_button,
        ):
            button.setEnabled(available)

        self.unavailable_group.setExclusive(False)
        for name, button in self.unavailable_buttons.items():
            button.setEnabled(available)
            button.setChecked(available and name == status)
        self.unavailable_group.setExclusive(True)


class SnapCalibrationPanel(QFrame):
    """Compact full-width surface for capturing exact snap frames."""

    item_requested = Signal(object)
    inspect_requested = Signal(object, int, bool)
    exact_snap_requested = Signal(object, int)

    def __init__(
        self,
        session: TemporalReviewSession,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.setObjectName("SnapCalibrationPanel")
        self.setProperty("reviewPanel", "true")
        apply_elevation(self)
        self.setAccessibleName("Exact snap frame calibration")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self.setStyleSheet(
            """
            QFrame[snapCalibrationCard="true"] {
                background-color: #0f1511;
                border: 1px solid #2a352d;
                border-radius: 5px;
            }
            QPushButton[snapUnavailable="true"]:checked {
                color: #061109;
                background-color: #39e07a;
                border-color: #39e07a;
                font-weight: 800;
            }
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 9)
        outer.setSpacing(6)

        heading = QHBoxLayout()
        heading.setSpacing(10)
        self.heading_label = QLabel("EXACT SNAP CALIBRATION")
        self.heading_label.setProperty("role", "heading")
        heading.addWidget(self.heading_label)
        self.item_label = QLabel()
        self.item_label.setProperty("role", "eyebrow")
        heading.addWidget(self.item_label)
        heading.addStretch(1)
        self.progress_label = QLabel()
        self.progress_label.setProperty("role", "subtle")
        heading.addWidget(self.progress_label)
        outer.addLayout(heading)

        self.instruction_label = QLabel(
            "Find the first frame the snap begins. Pause, use Left / Right "
            "to frame-step, then Mark Current Frame."
        )
        self.instruction_label.setWordWrap(True)
        self.instruction_label.setProperty("role", "subtle")
        self.instruction_label.setAccessibleName(
            "Exact snap calibration instructions"
        )
        outer.addWidget(self.instruction_label)

        cards = QHBoxLayout()
        cards.setSpacing(8)
        self.angle_cards: dict[int, _SnapAngleCard] = {}
        for number in (1, 2):
            card = _SnapAngleCard(number, self)
            card.inspect_requested.connect(self._inspect)
            card.exact_snap_requested.connect(self._request_exact_snap)
            card.unavailable_requested.connect(self._mark_unavailable)
            self.angle_cards[number] = card
            cards.addWidget(card, 1)
        outer.addLayout(cards)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.save_note_label = QLabel(
            "Exact frames save to research data only"
        )
        self.save_note_label.setProperty("role", "muted")
        footer.addWidget(self.save_note_label)
        footer.addStretch(1)

        self.previous_button = QPushButton("Previous")
        self.previous_button.setAccessibleName(
            "Go to previous snap calibration clip"
        )
        self.previous_button.clicked.connect(self._previous)
        footer.addWidget(self.previous_button)

        self.save_next_button = QPushButton("Save && Next")
        self.save_next_button.setAccessibleName(
            "Go to next clip after both snap angles are calibrated"
        )
        self.save_next_button.clicked.connect(self._save_and_next)
        footer.addWidget(self.save_next_button)

        self.next_unreviewed_button = QPushButton("Next Unreviewed")
        self.next_unreviewed_button.setAccessibleName(
            "Go to next unfinished snap calibration clip"
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
        raw_angles = _value(item, "angles", default=()) or ()
        return {
            _angle_number(angle, fallback): angle
            for fallback, angle in enumerate(raw_angles, start=1)
        }

    def _review_for(self, item: object | None) -> object | None:
        if item is None:
            return None
        item_id = str(_value(item, "item_id", default=""))
        return self.session.review_for(item_id)

    def _item_complete(self, item: object | None) -> bool:
        if item is None:
            return False
        item_id = str(_value(item, "item_id", default=""))
        complete = self.session.item_complete
        try:
            return bool(complete(item))
        except (TypeError, KeyError, AttributeError):
            return bool(complete(item_id))

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
        """Refresh after a session mutation or externally saved exact frame."""

        item = self._current_item()
        total = len(getattr(self.session, "items", ()) or ())
        completed = int(getattr(self.session, "completed_count", 0))
        pending = int(getattr(self.session, "pending_count", 0))
        index = int(getattr(self.session, "current_index", 0))

        self.progress_label.setText(
            f"{completed} of {total} complete | {pending} remaining"
        )
        self.progress_label.setAccessibleName(
            f"Snap calibration progress: {completed} of {total} complete, "
            f"{pending} remaining"
        )

        if item is None:
            self.item_label.setText("No calibration clips")
        else:
            project = str(_value(item, "project_name", default="")).strip()
            clip = _value(item, "clip_number", default="?")
            try:
                clip_text = f"{int(clip):02d}"
            except (TypeError, ValueError):
                clip_text = str(clip)
            self.item_label.setText(
                f"{project} | Clip {clip_text}"
                if project else f"Clip {clip_text}"
            )

        review = self._review_for(item)
        angles = self._item_angles(item)
        for number, card in self.angle_cards.items():
            card.set_angle(
                angles.get(number),
                _review_angle(review, number),
            )

        complete = self._item_complete(item)
        self.previous_button.setEnabled(item is not None and index > 0)
        can_save_next = (
            item is not None and complete and index + 1 < total
        )
        self.save_next_button.setEnabled(can_save_next)
        self.save_next_button.setProperty(
            "primary", "true" if can_save_next else "false"
        )
        self.save_next_button.style().unpolish(self.save_next_button)
        self.save_next_button.style().polish(self.save_next_button)
        self.next_unreviewed_button.setEnabled(
            self._next_unreviewed_index() is not None
        )

        if emit_item and item is not None:
            self.item_requested.emit(item)

    def activate_current(self) -> None:
        """Ask the owning window to select the current clip."""

        item = self._current_item()
        if item is not None:
            self.item_requested.emit(item)

    def _inspect(self, angle_number: int, play_around: bool) -> None:
        item = self._current_item()
        if item is not None and angle_number in self._item_angles(item):
            self.inspect_requested.emit(item, angle_number, play_around)

    def _request_exact_snap(self, angle_number: int) -> None:
        item = self._current_item()
        if item is not None and angle_number in self._item_angles(item):
            self.exact_snap_requested.emit(item, angle_number)

    def _mark_unavailable(self, angle_number: int, status: str) -> None:
        item = self._current_item()
        if item is None or angle_number not in self._item_angles(item):
            return
        item_id = str(_value(item, "item_id", default=""))
        self.session.mark_snap_unavailable(item_id, angle_number, status)
        self.refresh()

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
    "SnapCalibrationPanel",
    "UNAVAILABLE_STATUSES",
]
