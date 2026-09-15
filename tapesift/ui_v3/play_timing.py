"""Optional film-time measurements, staged through the clip editor's save path."""
from __future__ import annotations

from PySide6.QtCore import Signal, QSignalBlocker
from PySide6.QtWidgets import QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QToolButton, QVBoxLayout, QWidget, QSizePolicy

from tapesift.services.snap_prediction_service import cached_prediction
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_v3.icons import tinted_icon


def timing_marks(clip, details):
    """Read source timestamps without turning missing or invalid marks into zero."""
    def timestamp(key):
        try:
            value = int(details[key])
            return value if clip.start_ms <= value < clip.end_ms else None
        except (KeyError, TypeError, ValueError, OverflowError):
            return None

    snap = timestamp("timing_snap_ms")
    confirmed = snap is not None and details.get("timing_snap_confirmed") == "1"
    if "timing_snap_ms" not in details:
        prediction = cached_prediction(clip)
        if prediction:
            source_ms = int(prediction["source_ms"])
            if clip.start_ms <= source_ms < clip.end_ms:
                snap = source_ms
    return snap, timestamp("timing_release_ms"), confirmed


class PlayTimingPanel(QFrame):
    details_changed = Signal(dict)
    mark_requested = Signal(str)
    seek_requested = Signal(int)
    find_snap_requested = Signal()
    display_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("V3PlayTiming")
        self.clip = None
        self.details = {}
        self.position = None
        self.angle_starts = ()
        self.setStyleSheet("""
            QFrame#V3PlayTiming { background:transparent; border:none; border-bottom:1px solid #3b4751; border-radius:0; }
            QFrame#V3PlayTiming QLabel { border:none; background:transparent; }
            QFrame#V3PlayTiming QPushButton { padding:5px 6px; }
            QFrame#V3PlayTiming QCheckBox { font:12px 'IBM Plex Sans'; color:#bcc7d0; background:transparent; }
            QFrame#V3PlayTiming QCheckBox::indicator { width:16px; height:16px; border:1px solid #8797a4; border-radius:3px; background:#27333c; }
            QFrame#V3PlayTiming QCheckBox::indicator:checked { background:#2a9659; border-color:#73d099; }
            QFrame#V3PlayTiming QCheckBox:focus { outline:1px solid #ffc27b; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        heading.setSpacing(8)
        heading.addWidget(QLabel("Play timer"))
        information = QLabel()
        information.setPixmap(tinted_icon("info-16.svg", "#a6b4c0", 16).pixmap(16, 16))
        information.setToolTip("Optional time since snap and time to throw. Enable, confirm the snap, then mark release.")
        heading.addWidget(information)
        heading.addStretch(1)
        self.enabled_check = QCheckBox("Off")
        self.enabled_check.setAccessibleName("Enable timing for this play")
        heading.addWidget(self.enabled_check)
        self.expand_button = QToolButton()
        self.expand_button.setCheckable(True)
        self.expand_button.setIcon(tinted_icon("chevron-right-16.svg", "#a6b4c0", 16))
        self.expand_button.setStyleSheet("QToolButton {background:transparent;border:0;padding:0;min-width:0;min-height:0;} QToolButton:hover {background:#1c262b;} QToolButton:focus {border:1px solid #ffc27b;}")
        self.expand_button.setAccessibleName("Expand play timer")
        self.expand_button.setToolTip("Show snap and time-to-throw controls")
        self.expand_button.setFixedSize(24, 26)
        heading.addWidget(self.expand_button)
        layout.addLayout(heading)
        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        layout.addWidget(self.body)
        self.elapsed_label = QLabel("—")
        self.elapsed_label.setAccessibleName("Time since snap")
        self.elapsed_label.setStyleSheet("font:600 28px 'IBM Plex Mono';color:#ffc27b;")
        body.addWidget(self.elapsed_label)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        body.addWidget(self.status_label)
        self.snap_label = QLabel("Snap: not set")
        body.addWidget(self.snap_label)
        grid = QGridLayout()
        grid.setSpacing(5)
        body.addLayout(grid)
        buttons = (("find_button", "Find snap", self.find_snap_requested.emit),
                   ("snap_button", "Set snap here", lambda: self.mark_requested.emit("snap")),
                   ("jump_snap_button", "Go to snap", lambda: self._jump("snap")),
                   ("confirm_button", "Confirm snap", self._confirm))
        for index, (name, caption, slot) in enumerate(buttons):
            button = QPushButton(caption)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.clicked.connect(slot)
            grid.addWidget(button, index // 2, index % 2)
            setattr(self, name, button)
        self.throw_label = QLabel("Time to throw: —")
        self.throw_label.setWordWrap(True)
        body.addWidget(self.throw_label)
        self.release_label = QLabel("Release: not marked")
        body.addWidget(self.release_label)
        self.release_button = QPushButton("Mark release here")
        self.release_button.setToolTip("Pause on the frame where the ball leaves the quarterback's hand.")
        self.release_button.clicked.connect(lambda: self.mark_requested.emit("release"))
        body.addWidget(self.release_button)
        release_row = QGridLayout()
        self.jump_release_button = QPushButton("Go to release")
        self.jump_release_button.clicked.connect(lambda: self._jump("release"))
        self.clear_release_button = QPushButton("Clear release")
        self.clear_release_button.clicked.connect(lambda: self.details_changed.emit({"timing_release_ms": None}))
        for index, button in enumerate((self.jump_release_button, self.clear_release_button)):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            release_row.addWidget(button, 0, index)
        body.addLayout(release_row)
        self.overlay_check = QCheckBox("Show timer on video")
        self.overlay_check.setToolTip("Playback display only; exports keep their existing appearance.")
        body.addWidget(self.overlay_check)
        self.focus_controls = (self.enabled_check, self.expand_button, self.find_button, self.snap_button,
                               self.jump_snap_button, self.confirm_button, self.release_button,
                               self.jump_release_button, self.clear_release_button, self.overlay_check)
        self.message_label = QLabel("Mark one continuous camera view. Save keeps the marks with this play.")
        self.message_label.setWordWrap(True)
        self.message_label.setStyleSheet("color:#8d949a;font-size:11px;")
        body.addWidget(self.message_label)
        self.expand_button.toggled.connect(self._expand_toggled)
        self.enabled_check.toggled.connect(self.expand_button.setChecked)
        self.enabled_check.toggled.connect(lambda checked: self.details_changed.emit({"timing_enabled": "1" if checked else "0"}))
        self.overlay_check.toggled.connect(lambda checked: self.details_changed.emit({"timing_overlay": "1" if checked else "0"}))
        self.refresh()

    def _expand_toggled(self, expanded):
        if expanded and not self.enabled_check.isChecked():
            self.enabled_check.setChecked(True)
        self.expand_button.setIcon(tinted_icon("chevron-down-16.svg" if expanded else "chevron-right-16.svg", "#a6b4c0", 16))
        self.expand_button.setAccessibleName("Collapse play timer" if expanded else "Expand play timer")
        self.body.setVisible(expanded and self.enabled_check.isChecked())

    def set_clip(self, clip, details):
        self.clip = clip
        self.details = dict(details)
        with QSignalBlocker(self.expand_button):
            self.expand_button.setChecked(self.details.get("timing_enabled") == "1")
        self.position = None
        self.angle_starts = ()
        self.message_label.setText("Mark one continuous camera view. Save keeps the marks with this play.")
        self.refresh()

    def set_position(self, position):
        self.position = position
        self.refresh()

    def _same_view(self, snap, position):
        return not any(snap < boundary <= position for boundary in self.angle_starts)

    def mark(self, kind, position):
        if self.clip is None or position is None or not self.clip.start_ms <= position < self.clip.end_ms:
            self.message_label.setText("Pause or step to a settled frame in this play, then try again.")
            return
        if kind == "snap":
            self.details_changed.emit({"timing_snap_ms": str(position), "timing_snap_confirmed": "1"})
        elif kind == "release":
            snap, _, _ = timing_marks(self.clip, self.details)
            if snap is None or position <= snap or not self._same_view(snap, position):
                self.message_label.setText("Mark release after the snap, within the same camera view.")
                return
            # Freeze a suggested anchor when a measurement is made; a new prediction
            # must never silently change an analyst's saved interval.
            self.details_changed.emit({"timing_snap_ms": str(snap), "timing_release_ms": str(position)})
        self.message_label.setText("Timing changed. Save keeps these marks with the play.")

    def _confirm(self):
        if self.clip:
            snap, _, _ = timing_marks(self.clip, self.details)
            if snap is not None:
                self.details_changed.emit({"timing_snap_ms": str(snap), "timing_snap_confirmed": "1"})

    def _jump(self, kind):
        if self.clip:
            snap, release, _ = timing_marks(self.clip, self.details)
            value = snap if kind == "snap" else release
            if value is not None:
                self.seek_requested.emit(value)

    def refresh(self):
        enabled = bool(self.clip and self.details.get("timing_enabled") == "1")
        with QSignalBlocker(self.enabled_check), QSignalBlocker(self.overlay_check):
            self.enabled_check.setChecked(enabled)
            self.overlay_check.setChecked(self.details.get("timing_overlay") == "1")
        self.enabled_check.setEnabled(self.clip is not None)
        self.enabled_check.setText("On" if enabled else "Off")
        self.expand_button.setEnabled(self.clip is not None)
        expanded = enabled and self.expand_button.isChecked()
        self.expand_button.setIcon(tinted_icon("chevron-down-16.svg" if expanded else "chevron-right-16.svg", "#a6b4c0", 16))
        self.expand_button.setAccessibleName("Collapse play timer" if expanded else "Expand play timer")
        self.body.setVisible(expanded)
        snap, release, confirmed = timing_marks(self.clip, self.details) if self.clip else (None, None, False)
        self.snap_label.setText("Snap: " + (format_ms(snap, show_millis=True) if snap is not None else "not set"))
        self.release_label.setText("Release: " + (format_ms(release, show_millis=True) if release is not None else "not marked"))
        self.jump_snap_button.setEnabled(snap is not None)
        self.confirm_button.setEnabled(snap is not None and not confirmed)
        self.jump_release_button.setEnabled(release is not None)
        self.clear_release_button.setEnabled("timing_release_ms" in self.details)
        self.release_button.setEnabled(snap is not None)
        valid_release = snap is not None and release is not None and release > snap and self._same_view(snap, release)
        throw = f"{(release - snap) / 1000:.2f} s" if valid_release else "—"
        self.throw_label.setText("Time to throw: " + throw + (" · Estimated" if valid_release and not confirmed else ""))
        position = self.position
        valid_position = self.clip is not None and position is not None and self.clip.start_ms <= position < self.clip.end_ms
        clock = "—"
        status = "Set or find the snap to begin."
        if snap is not None:
            status = "Confirmed snap" if confirmed else "Estimated · Confirm or adjust the snap"
            if valid_position and self._same_view(snap, max(snap, position)):
                clock = f"{max(0, position - snap) / 1000:.2f} s"
                if position < snap:
                    status += " · Before snap"
            elif valid_position:
                status = "Different camera view · timing unavailable"
        if "timing_snap_ms" in self.details and snap is None:
            status = "Saved snap is outside this clip. Set it again."
        elif "timing_release_ms" in self.details and not valid_release:
            status += " · Release needs review"
        self.elapsed_label.setText(clock)
        self.status_label.setText(status)
        overlay = ("SNAP +" + clock + (" · Estimated" if not confirmed else "")) if enabled and self.overlay_check.isChecked() and clock != "—" else ""
        self.display_changed.emit(overlay)
