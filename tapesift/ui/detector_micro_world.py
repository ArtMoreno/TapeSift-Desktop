"""Interactive teaching sandbox for TapeSift's primary detector rules.

This deliberately operates on a tiny synthetic film rather than project data.
It teaches the separator/run/duration mental model without claiming to model
the detector's guarded, population-level recovery branches.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QDoubleSpinBox, QFrame,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton, QSizePolicy,
    QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)


@dataclass(frozen=True)
class TeachingSpan:
    """One synthetic span between edit boundaries."""

    label: str
    duration_s: float


@dataclass(frozen=True)
class TeachingDecision:
    """One complete primary-pass decision covering one or more spans."""

    kind: str
    start_s: float
    end_s: float
    span_indices: tuple[int, ...]
    reason: str

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


TEACHING_SPANS: tuple[TeachingSpan, ...] = (
    TeachingSpan("Wide A", 12.0),
    TeachingSpan("End-zone A", 10.5),
    TeachingSpan("Score card", 3.0),
    TeachingSpan("Wide B", 7.0),
    TeachingSpan("End-zone B", 6.5),
    TeachingSpan("Transition", 0.5),
    TeachingSpan("Sideline", 1.5),
    TeachingSpan("Wide C", 16.0),
    TeachingSpan("End-zone C", 15.0),
    TeachingSpan("Long card", 5.5),
    TeachingSpan("Short play", 8.0),
)


def classify_teaching_spans(
        spans: tuple[TeachingSpan, ...] = TEACHING_SPANS,
        *,
        separator_max_s: float = 5.0,
        min_play_s: float = 4.0,
        max_play_s: float = 90.0,
) -> tuple[TeachingDecision, ...]:
    """Apply the primary grouping mental model to a synthetic film.

    A span strictly shorter than ``separator_max_s`` is a separator and ends
    the current content run. Consecutive remaining spans form one run. The
    run's total duration then decides whether it is a play or needs review.
    Every input span appears in exactly one returned decision.
    """

    decisions: list[TeachingDecision] = []
    run_indices: list[int] = []
    starts: list[float] = []
    cursor_s = 0.0

    def flush_run(end_s: float) -> None:
        if not run_indices:
            return
        start_s = starts[run_indices[0]]
        duration_s = end_s - start_s
        if duration_s < min_play_s:
            kind = "review"
            reason = (
                f"{duration_s:.1f}s is shorter than the "
                f"{min_play_s:.1f}s minimum"
            )
        elif duration_s > max_play_s:
            kind = "review"
            reason = (
                f"{duration_s:.1f}s is longer than the "
                f"{max_play_s:.1f}s maximum"
            )
        else:
            kind = "play"
            reason = (
                f"{duration_s:.1f}s is inside the "
                f"{min_play_s:.1f}-{max_play_s:.1f}s play range"
            )
        decisions.append(TeachingDecision(
            kind=kind,
            start_s=start_s,
            end_s=end_s,
            span_indices=tuple(run_indices),
            reason=reason,
        ))
        run_indices.clear()

    for index, span in enumerate(spans):
        starts.append(cursor_s)
        end_s = cursor_s + span.duration_s
        if span.duration_s < separator_max_s:
            flush_run(cursor_s)
            decisions.append(TeachingDecision(
                kind="separator",
                start_s=cursor_s,
                end_s=end_s,
                span_indices=(index,),
                reason=(
                    f"{span.duration_s:.1f}s is strictly under the "
                    f"{separator_max_s:.1f}s separator setting"
                ),
            ))
        else:
            run_indices.append(index)
        cursor_s = end_s

    flush_run(cursor_s)
    return tuple(decisions)


class DetectorFilmStrip(QWidget):
    """Two-row visualization of raw spans and interpreted decisions."""

    _RAW_COLORS = (
        QColor("#26312c"),
        QColor("#303a45"),
    )
    _DECISION_COLORS = {
        "play": QColor("#39e07a"),
        "review": QColor("#ffb547"),
        "separator": QColor("#50606c"),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DetectorMicroWorldStrip")
        self.setMinimumHeight(238)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.decisions = classify_teaching_spans()

    def set_decisions(
            self, decisions: tuple[TeachingDecision, ...]) -> None:
        self.decisions = decisions
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0a0c0b"))

        total_s = sum(span.duration_s for span in TEACHING_SPANS)
        left = 18.0
        width = max(1.0, self.width() - 36.0)
        top_y = 42.0
        row_h = 58.0
        decision_y = 142.0

        painter.setPen(QColor("#9eaaa4"))
        painter.drawText(
            QRectF(left, 10, width, 24),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "SOURCE SPANS  |  lines are edit boundaries",
        )
        cursor_s = 0.0
        for index, span in enumerate(TEACHING_SPANS):
            x = left + width * cursor_s / total_s
            span_w = max(2.0, width * span.duration_s / total_s)
            rect = QRectF(x, top_y, span_w, row_h)
            painter.fillRect(rect, self._RAW_COLORS[index % 2])
            painter.setPen(QPen(QColor("#75817b"), 1))
            painter.drawRect(rect)
            painter.setPen(QColor("#f2f5f2"))
            if span_w >= 58:
                text = f"{span.label}\n{span.duration_s:g}s"
            elif span_w >= 30:
                text = f"{span.duration_s:g}s"
            else:
                text = ""
            if text:
                painter.drawText(
                    rect.adjusted(3, 2, -3, -2),
                    Qt.AlignmentFlag.AlignCenter
                    | Qt.TextFlag.TextWordWrap,
                    text,
                )
            cursor_s += span.duration_s

        painter.setPen(QColor("#9eaaa4"))
        painter.drawText(
            QRectF(left, 110, width, 24),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            "PRIMARY-PASS DECISION",
        )
        counts = {"play": 0, "review": 0, "separator": 0}
        for decision in self.decisions:
            counts[decision.kind] += 1
            x = left + width * decision.start_s / total_s
            decision_w = max(
                2.0, width * decision.duration_s / total_s)
            rect = QRectF(x, decision_y, decision_w, row_h)
            color = self._DECISION_COLORS[decision.kind]
            painter.fillRect(rect, color)
            painter.setPen(QPen(QColor("#0a0c0b"), 1))
            painter.drawRect(rect)
            painter.setPen(
                QColor("#07110b")
                if decision.kind in {"play", "review"}
                else QColor("#f2f5f2")
            )
            label = {
                "play": f"PLAY {counts['play']}",
                "review": f"REVIEW {counts['review']}",
                "separator": "SEPARATOR",
            }[decision.kind]
            if decision_w >= 86:
                text = f"{label}\n{decision.duration_s:.1f}s"
            elif decision_w >= 38:
                short = {
                    "play": f"P{counts['play']}",
                    "review": f"R{counts['review']}",
                    "separator": "SEP",
                }[decision.kind]
                text = f"{short}\n{decision.duration_s:.1f}s"
            else:
                text = ""
            if text:
                painter.drawText(
                    rect.adjusted(3, 2, -3, -2),
                    Qt.AlignmentFlag.AlignCenter
                    | Qt.TextFlag.TextWordWrap,
                    text,
                )


class DetectorMicroWorldDialog(QDialog):
    """Safe, project-independent detector teaching tool."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DetectorMicroWorldDialog")
        self.setWindowTitle("Detector Micro-World - TapeSift")
        self.setMinimumSize(980, 720)
        self.resize(1120, 780)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("DETECTOR MICRO-WORLD")
        title.setProperty("role", "heading")
        layout.addWidget(title)

        intro = QLabel(
            "This synthetic cut-up film shows the detector's primary mental "
            "model. Adjust the three controls and watch the same source spans "
            "become plays, review ranges, or separators. No project or video "
            "is opened, changed, or saved."
        )
        intro.setWordWrap(True)
        intro.setProperty("role", "subtle")
        layout.addWidget(intro)

        controls = QFrame()
        controls.setProperty("reviewPanel", "true")
        controls_layout = QGridLayout(controls)
        controls_layout.setContentsMargins(12, 10, 12, 10)
        controls_layout.setHorizontalSpacing(12)
        controls_layout.setVerticalSpacing(6)

        self.separator_spin = self._add_parameter(
            controls_layout,
            0,
            "Separator under",
            "Short spans end the current run and become separators.",
            minimum=0.5,
            maximum=20.0,
            step=0.5,
            value=5.0,
        )
        self.minimum_spin = self._add_parameter(
            controls_layout,
            1,
            "Minimum play",
            "A grouped content run below this duration needs review.",
            minimum=1.0,
            maximum=30.0,
            step=0.5,
            value=4.0,
        )
        self.maximum_spin = self._add_parameter(
            controls_layout,
            2,
            "Maximum play",
            "A grouped content run above this duration needs review.",
            minimum=10.0,
            maximum=120.0,
            step=5.0,
            value=90.0,
        )
        reset = QPushButton("Reset to 5 / 4 / 90")
        reset.setProperty("quiet", "true")
        reset.clicked.connect(self._reset_defaults)
        controls_layout.addWidget(
            reset, 0, 3, 3, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(controls)

        self.strip = DetectorFilmStrip()
        layout.addWidget(self.strip)

        legend = QHBoxLayout()
        for color, text in (
                ("#39e07a", "PLAY"),
                ("#ffb547", "REVIEW"),
                ("#50606c", "SEPARATOR")):
            swatch = QLabel("  ")
            swatch.setStyleSheet(
                f"background: {color}; border: 1px solid #75817b;")
            swatch.setFixedSize(18, 12)
            legend.addWidget(swatch)
            legend.addWidget(QLabel(text))
            legend.addSpacing(12)
        legend.addStretch(1)
        self.summary_label = QLabel()
        self.summary_label.setProperty("role", "subtle")
        legend.addWidget(self.summary_label)
        layout.addLayout(legend)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("DetectorMicroWorldDecisionTable")
        self.table.setHorizontalHeaderLabels(
            ["Decision", "Range", "Source span(s)", "Why"])
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        note = QLabel(
            "Teaching boundary: the live detector also learns each film's "
            "typical angle count and duration, then applies guarded recovery "
            "rules. This sandbox intentionally isolates the three controls "
            "so their effect stays visible. Current-build detail: Separator "
            "and Maximum match the live primary pass; the live primary "
            "minimum is still fixed at 4.0s. Moving Minimum here demonstrates "
            "its intended behavior, while the app's Minimum setting currently "
            "affects recovery and coverage-review paths."
        )
        note.setWordWrap(True)
        note.setProperty("role", "muted")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for spin in (
                self.separator_spin,
                self.minimum_spin,
                self.maximum_spin):
            spin.valueChanged.connect(self._refresh)
        self._refresh()

    def _add_parameter(
            self,
            layout: QGridLayout,
            row: int,
            label: str,
            tooltip: str,
            *,
            minimum: float,
            maximum: float,
            step: float,
            value: float,
    ) -> QDoubleSpinBox:
        name = QLabel(label)
        name.setToolTip(tooltip)
        layout.addWidget(name, row, 0)

        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setDecimals(1)
        spin.setSuffix(" s")
        spin.setValue(value)
        spin.setToolTip(tooltip)
        spin.setAccessibleName(label)

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(round(minimum * 2), round(maximum * 2))
        slider.setSingleStep(max(1, round(step * 2)))
        slider.setValue(round(value * 2))
        slider.setToolTip(tooltip)
        slider.setAccessibleName(f"{label} slider")
        slider.valueChanged.connect(
            lambda raw, target=spin: target.setValue(raw / 2))
        spin.valueChanged.connect(
            lambda current, target=slider:
            target.setValue(round(current * 2)))

        layout.addWidget(slider, row, 1)
        layout.addWidget(spin, row, 2)
        return spin

    def _reset_defaults(self) -> None:
        self.separator_spin.setValue(5.0)
        self.minimum_spin.setValue(4.0)
        self.maximum_spin.setValue(90.0)

    def _refresh(self, _value: float | None = None) -> None:
        decisions = classify_teaching_spans(
            separator_max_s=self.separator_spin.value(),
            min_play_s=self.minimum_spin.value(),
            max_play_s=self.maximum_spin.value(),
        )
        self.strip.set_decisions(decisions)
        self.table.setRowCount(len(decisions))

        counts = {"play": 0, "review": 0, "separator": 0}
        total_s = sum(span.duration_s for span in TEACHING_SPANS)
        for row, decision in enumerate(decisions):
            counts[decision.kind] += 1
            decision_label = {
                "play": f"PLAY {counts['play']}",
                "review": f"REVIEW {counts['review']}",
                "separator": "SEPARATOR",
            }[decision.kind]
            span_labels = ", ".join(
                TEACHING_SPANS[index].label
                for index in decision.span_indices
            )
            values = (
                decision_label,
                f"{decision.start_s:05.1f} - {decision.end_s:05.1f}",
                span_labels,
                decision.reason,
            )
            for column, value in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(value))

        self.summary_label.setText(
            f"{counts['play']} plays  |  {counts['review']} review  |  "
            f"{counts['separator']} separators  |  "
            f"{total_s:.1f}/{total_s:.1f}s accounted"
        )


__all__ = [
    "DetectorMicroWorldDialog",
    "TEACHING_SPANS",
    "TeachingDecision",
    "TeachingSpan",
    "classify_teaching_spans",
]
