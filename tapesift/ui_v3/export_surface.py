"""Locked V3 export and package surfaces over the live export authority."""

from __future__ import annotations

from collections.abc import Iterable

from pathlib import Path

from PySide6.QtCore import QSize, Signal, Qt, QTimer
from PySide6.QtGui import QPixmap, QTransform
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QScrollArea, QMenu,
    QWidget,
)

from tapesift.ui_core.layout_ownership import reparent_widget
from tapesift.ui_v3.icons import tinted_icon
from tapesift.services.timestamp_parser import format_ms


def _label(text: str, object_name: str = "") -> QLabel:
    value = QLabel(text)
    if object_name:
        value.setObjectName(object_name)
    return value


def _icon_label(name: str, color: str, size: int = 18) -> QLabel:
    value = QLabel()
    value.setObjectName("V3ExportLaneIcon")
    value.setFixedSize(size + 8, size + 8)
    value.setAlignment(Qt.AlignmentFlag.AlignCenter)
    value.setPixmap(tinted_icon(name, color, size).pixmap(QSize(size, size)))
    return value


def _scroll_body(widget: QWidget) -> QScrollArea:
    scroll = QScrollArea()
    scroll.setObjectName("V3ExportBodyScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setMinimumSize(0, 0)
    scroll.setWidget(widget)
    return scroll


def _destination_row(label: QLabel, button: QPushButton) -> QHBoxLayout:
    label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(label, 1)
    row.addWidget(button)
    return row


class _StageStripV3(QFrame):
    """Configure / work / complete strip shared by both routes."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3ExportStageStrip")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.cells: list[QLabel] = []
        for number, text in ((1, "Configure"), (2, "Export"), (3, "Complete")):
            cell = _label(f"{number}   {text}", "V3ExportStageCell")
            cell.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            cell.setProperty("active", number == 1)
            row.addWidget(cell, 1)
            self.cells.append(cell)

    def set_stage(self, stage: int) -> None:
        stage = max(1, min(3, int(stage)))
        for index, cell in enumerate(self.cells, start=1):
            cell.setProperty("active", index == stage)
            cell.style().unpolish(cell)
            cell.style().polish(cell)

    def set_work_label(self, label: str) -> None:
        self.cells[1].setText(f"2   {label}")


class _QueueCardV3(QFrame):
    cancel_requested = Signal()
    open_path_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3ExportQueueCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        heading = QHBoxLayout()
        heading.addWidget(_label("Queue", "V3ExportCardTitle"))
        heading.addStretch(1)
        self.queue_note = _label("No active job", "V3ExportQuiet")
        heading.addWidget(self.queue_note)
        outer.addLayout(heading)

        self.now_card = QFrame(self)
        self.now_card.setObjectName("V3ExportQueueLane")
        now = QGridLayout(self.now_card)
        now.setContentsMargins(12, 10, 12, 10)
        now.setHorizontalSpacing(10)
        now.setVerticalSpacing(5)
        now_icon = _icon_label("filmstrip-play-20.svg", "#6fdc98")
        self.now_badge = _label("CURRENT", "V3ExportQueueBadge")
        self.now_title = _label("Ready", "V3ExportQueueTitle")
        self.now_state = _label("READY", "V3ExportQueueState")
        self.now_detail = _label(
            "The active export appears here.", "V3ExportQuiet")
        self.now_detail.setWordWrap(True)
        self.now_detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        now.addWidget(now_icon, 0, 0, 2, 1)
        now.addWidget(self.now_badge, 0, 1, 2, 1)
        now.addWidget(self.now_title, 0, 2)
        now.addWidget(self.now_state, 0, 3)
        now.addWidget(self.now_detail, 1, 2, 1, 2)
        now.setColumnStretch(2, 1)
        outer.addWidget(self.now_card)

        self.progress = QProgressBar(self)
        self.progress.setObjectName("V3ExportProgress")
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        outer.addWidget(self.progress)

        self.complete_card = QFrame(self)
        self.complete_card.setObjectName("V3ExportQueueLane")
        self.complete_card.setProperty("completeLane", True)
        self.complete_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        complete = QGridLayout(self.complete_card)
        complete.setContentsMargins(12, 10, 12, 10)
        complete.setHorizontalSpacing(10)
        complete.setVerticalSpacing(5)
        complete.addWidget(
            _icon_label("save-next-20.svg", "#39e07a"), 0, 0, 2, 1)
        complete.addWidget(
            _label("RECENT", "V3ExportQueueBadge"), 0, 1, 2, 1)
        self.complete_title = _label("Complete", "V3ExportQueueTitle")
        self.complete_state = _label("", "V3ExportCompleteState")
        self.complete_detail = _label(
            "Finished clips remain available here.",
            "V3ExportCompleteDetail")
        self.complete_detail.setWordWrap(True)
        self.complete_detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.complete_title.setWordWrap(True)
        self.complete_title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        complete.addWidget(self.complete_title, 0, 2)
        complete.addWidget(self.complete_state, 0, 3)
        complete.addWidget(self.complete_detail, 1, 2, 1, 2)
        self.destination_caption = _label(
            "DESTINATION", "V3ExportCaption")
        complete.addWidget(self.destination_caption, 2, 0, 1, 4)
        self.complete_path = _label("", "V3ExportCompletePath")
        self.complete_path.setWordWrap(True)
        self.complete_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.complete_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        complete.addWidget(self.complete_path, 3, 0, 1, 4)

        self.complete_actions = QWidget(self.complete_card)
        self.complete_actions.setObjectName("V3ExportCompleteActions")
        action_row = QHBoxLayout(self.complete_actions)
        action_row.setContentsMargins(0, 2, 0, 0)
        action_row.setSpacing(8)
        self.open_button = QPushButton("Open Clip", self.complete_actions)
        self.open_button.setObjectName("V3ExportOpenOutput")
        self.open_button.setIcon(tinted_icon(
            "share-20.svg", "#dce5de", 16))
        self.folder_button = QPushButton(
            "Show in Folder", self.complete_actions)
        self.folder_button.setObjectName("V3ExportShowFolder")
        self.folder_button.setIcon(tinted_icon(
            "box-20.svg", "#dce5de", 16))
        action_row.addWidget(self.open_button)
        action_row.addWidget(self.folder_button)
        action_row.addStretch(1)
        complete.addWidget(self.complete_actions, 4, 0, 1, 4)
        complete.setColumnStretch(2, 1)
        complete.setRowStretch(5, 1)
        outer.addWidget(self.complete_card, 1)

        self._completed_output_path = ""
        self.open_button.clicked.connect(self._open_completed_output)
        self.folder_button.clicked.connect(self._show_completed_output)
        self._set_completed_availability()

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton("Cancel export", self)
        self.cancel_button.setObjectName("V3ExportCancel")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.cancel_button.hide()
        actions.addWidget(self.cancel_button)
        outer.addLayout(actions)

    @staticmethod
    def _status_name(job) -> str:
        value = getattr(job, "status", "")
        return str(getattr(value, "value", value)).lower()

    def sync(self, jobs: Iterable[object], *, running: bool) -> None:
        jobs = list(jobs)
        active = next((job for job in jobs if self._status_name(job) in {
            "waiting", "preparing", "exporting"}), None)
        completed = [job for job in jobs if self._status_name(job) == "completed"]
        failed = [job for job in jobs if self._status_name(job) == "failed"]
        cancelled = [job for job in jobs if self._status_name(job) == "cancelled"]

        if active is not None:
            self.now_title.setText(
                str(getattr(active, "display_name", "Export in progress")))
            state = self._status_name(active).upper() or "WORKING"
            self.now_state.setText(state)
            # ExportJob.progress is authoritative percent (0..100), not a
            # normalized fraction. Reset the range after an indeterminate
            # release state so a later job never inherits (0, 0).
            self.progress.setRange(0, 100)
            progress = int(max(0.0, min(100.0, float(
                getattr(active, "progress", 0.0) or 0.0))))
            self.progress.setValue(progress)
            self.progress.show()
            self.now_detail.setText(
                "Preparing source and saved ranges…" if state == "PREPARING"
                else f"{progress}% · {Path(str(getattr(active, 'output_path', ''))).name}")
        elif running:
            self.now_title.setText("Finishing current export")
            self.now_state.setText("WORKING")
            self.now_detail.setText(
                "Finishing the current output…")
            self.progress.setRange(0, 0)
            self.progress.show()
        else:
            self.now_title.setText("Ready")
            self.now_state.setText("READY")
            self.now_detail.setText("The active export appears here.")
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
            self.progress.hide()

        self.queue_note.setText("Active job" if running else "No active job")
        self.cancel_button.setVisible(running)
        self.cancel_button.setEnabled(running)
        counts = " · ".join(f"{len(group)} {name}" for group, name in (
            (completed, "completed"), (failed, "failed"), (cancelled, "cancelled")) if group)
        if completed or failed or cancelled:
            latest = completed[-1] if completed else None
            output_path = str(getattr(latest, "output_path", "") or "")
            self._completed_output_path = output_path
            self.complete_title.setText(
                Path(output_path).name if len(completed) == 1 and not failed and not cancelled else counts)
            self.complete_state.setText("PARTIAL" if completed and (failed or cancelled)
                                        else "FAILED" if failed else "CANCELLED" if cancelled else "COMPLETED")
            detail = str(getattr(failed[0], "error_message", "")) if failed else (
                "Export complete" if output_path and not cancelled else "Export cancelled")
            self.complete_detail.setText(detail or "See Queue for each output and Retry.")
            self.complete_detail.setToolTip(detail)
            self.complete_path.setText(str(Path(output_path).parent) if output_path else "")
            self.complete_path.setToolTip(output_path)
            self._set_completed_availability(output_path, state_visible=True)
        else:
            self._completed_output_path = ""
            self.complete_title.setText("No completed exports")
            self.complete_state.clear()
            self.complete_detail.setText("Completed files and any failures appear here.")
            self._set_completed_availability()

    def _set_completed_availability(
            self, output_path: str = "", *,
            state_visible: bool = False) -> None:
        output = Path(output_path) if output_path else None
        file_available = bool(output and output.is_file())
        folder_available = bool(output and output.parent.is_dir())
        self.complete_path.setVisible(bool(output_path))
        self.destination_caption.setVisible(bool(output_path))
        self.complete_state.setVisible(state_visible)
        self.open_button.setVisible(file_available)
        self.folder_button.setVisible(folder_available)
        self.complete_actions.setVisible(file_available or folder_available)

    def _open_completed_output(self) -> None:
        if self._completed_output_path:
            self.open_path_requested.emit(self._completed_output_path)

    def _show_completed_output(self) -> None:
        if self._completed_output_path:
            self.open_path_requested.emit(
                str(Path(self._completed_output_path).parent))


class _ClipExportSetupV3(QFrame):
    start_requested = Signal()
    destination_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3ClipExportSetup")
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        output = QFrame(self)
        output.setObjectName("V3ExportCard")
        output_shell = QVBoxLayout(output)
        output_shell.setContentsMargins(0, 0, 0, 0)
        form = QWidget(output)
        left = QGridLayout(form)
        left.setContentsMargins(14, 12, 14, 12)
        left.setHorizontalSpacing(10)
        left.setVerticalSpacing(7)
        title = _label("Output", "V3ExportCardTitle")
        self.route_note = _label("Clean single clip", "V3ExportQuiet")
        left.addWidget(title, 0, 0)
        left.addWidget(
            self.route_note, 0, 1, Qt.AlignmentFlag.AlignRight)

        self.format_combo = QComboBox(self)
        self.format_combo.setObjectName("V3ExportFormat")
        self.format_combo.addItem("Individual clip", "individual")
        self.style_combo = QComboBox(self)
        self.style_combo.setObjectName("V3ExportStyle")
        self.style_combo.addItem("Clean 16:9", "clean")
        self.preset_combo = QComboBox(self)
        self.preset_combo.setObjectName("V3ExportPreset")
        self.preset_combo.addItem("Social 1080p", "social_1080p")
        self.preset_combo.addItem("Source quality", "source_quality")
        self.preset_combo.addItem("Vertical 9:16", "vertical_9_16")
        self.preset_combo.currentIndexChanged.connect(lambda: self.style_combo.setItemText(
            0, "Clean 9:16" if self.preset_combo.currentData() == "vertical_9_16"
            else "Clean · source aspect"))
        self.style_combo.setItemText(0, "Clean · source aspect")
        self.accurate_check = QCheckBox("Accurate cuts", self)
        self.accurate_check.setChecked(True)
        self.accurate_check.setObjectName("V3ExportAccurate")
        self.destination = _label("-", "V3ExportDestination")
        self.destination.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)

        # Page10 pairs inline labels/controls; source facts stay visible below.
        left.removeWidget(self.route_note)
        left.addWidget(self.route_note, 0, 1, 1, 3, Qt.AlignmentFlag.AlignRight)
        for text, control, row, column in (
                ("Format", self.format_combo, 1, 0),
                ("Visual style", self.style_combo, 1, 2),
                ("Encoding", self.preset_combo, 2, 0),
                ("Cut", self.accurate_check, 2, 2)):
            left.addWidget(_label(text, "V3ExportFieldLabel"), row, column)
            left.addWidget(control, row, column + 1)
        self.browse_button = QPushButton("Browse…", self)
        self.browse_button.setObjectName("V3ExportBrowse")
        self.browse_button.clicked.connect(self.destination_requested.emit)
        self.filename = _label("Select a clip", "V3ExportFilename")
        self.filename.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.source_range = _label("", "V3ExportSourceRange")
        self.source_range.setWordWrap(True)
        for index, (caption, value) in enumerate((
                ("Destination", self.destination),
                ("Filename preview", self.filename),
                ("Source range", self.source_range)), 3):
            field = QFrame(form)
            field.setObjectName("V3ExportField")
            line = QHBoxLayout(field)
            line.setContentsMargins(9, 5, 9, 5)
            label = _label(caption, "V3ExportFieldLabel")
            label.setFixedWidth(100)
            line.addWidget(label)
            if value is self.destination:
                line.addLayout(_destination_row(value, self.browse_button), 1)
            else:
                line.addWidget(value, 1)
            left.addWidget(field, index, 0, 1, 4)
        output_shell.addWidget(_scroll_body(form), 1)
        tray = QVBoxLayout()
        tray.setContentsMargins(14, 0, 14, 10)
        self.status = _label(
            "Ready · source and range captured at Start", "V3ExportReady")
        self.status.setWordWrap(True)
        tray.addWidget(self.status)
        self.start_button = QPushButton("Start export", self)
        self.start_button.setObjectName("V3ExportStart")
        self.start_button.clicked.connect(self.start_requested.emit)
        self.start_button.setFixedHeight(42)
        self.start_button.setMaximumWidth(320)
        start_row = QHBoxLayout()
        start_row.addStretch(1)
        start_row.addWidget(self.start_button, 5)
        start_row.addStretch(1)
        tray.addLayout(start_row)
        output_shell.addLayout(tray)
        left.setColumnStretch(1, 1)
        left.setColumnStretch(3, 1)
        body.addWidget(output, 50)

        self.queue = _QueueCardV3(self)
        self.queue.layout().removeWidget(self.queue.cancel_button)
        tray.addWidget(self.queue.cancel_button, 0, Qt.AlignmentFlag.AlignHCenter)
        body.addWidget(_scroll_body(self.queue), 50)

    def configure(self, *, clip_title: str, duration_ms: int,
                  destination: str, preset_name: str, accurate: bool) -> None:
        seconds = max(0, int(duration_ms)) / 1000
        self.route_note.setText(f"{clip_title} · {seconds:.1f} seconds")
        self.destination.setText(destination or "-")
        self.destination.setToolTip(destination)
        index = self.preset_combo.findData(preset_name)
        self.preset_combo.setCurrentIndex(index if index >= 0 else 0)
        self.accurate_check.setChecked(bool(accurate))

    def set_ready(self, ready: bool, message: str = "") -> None:
        self.start_button.setEnabled(bool(ready))
        self.start_button.setProperty("ready", bool(ready))
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)
        self.start_button.update()
        self.status.setText(message or (
            "Ready · source and range captured at Start" if ready
            else "Finish or cancel the current export first"))


class _PackagePlayRowV3(QFrame):
    move_requested = Signal(str, int)
    remove_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._clip_id = ""
        self._drag_y: float | None = None
        self._drag_clip_id = ""
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip("Drag to reorder. Keyboard: Alt+Up/Down to move; Delete to remove.")

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.modifiers() == Qt.KeyboardModifier.AltModifier and event.key() in (
                Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.move_requested.emit(self._clip_id, -1 if event.key() == Qt.Key.Key_Up else 1)
            event.accept()
        elif event.key() == Qt.Key.Key_Delete and not event.modifiers():
            self.remove_requested.emit(self._clip_id)
            event.accept()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_y = event.position().y()
            self._drag_clip_id = self._clip_id
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_y is None or not (
                event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        delta = event.position().y() - self._drag_y
        if abs(delta) >= 10:
            self.move_requested.emit(
                self._drag_clip_id, 1 if delta > 0 else -1)
            self._drag_y = event.position().y()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_y = None
        event.accept()


class _PackageSetupV3(QFrame):
    start_requested = Signal()
    cancel_requested = Signal()
    remove_requested = Signal(str)
    order_changed = Signal(list)
    destination_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3PackageSetup")
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        selected = QFrame(self)
        selected.setObjectName("V3ExportCard")
        picked = QVBoxLayout(selected)
        picked.setContentsMargins(14, 12, 14, 12)
        picked.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(_label("Selected plays", "V3ExportCardTitle"))
        head.addStretch(1)
        head.addWidget(_label("Drag to reorder", "V3ExportQuiet"))
        picked.addLayout(head)
        self.play_rows: list[QFrame] = []
        self.rows_body = QWidget(selected)
        self.rows_layout = QVBoxLayout(self.rows_body)
        self.rows_layout.setContentsMargins(0, 0, 4, 0)
        self.rows_layout.setSpacing(3)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.rows_scroll = _scroll_body(self.rows_body)
        picked.addWidget(self.rows_scroll, 1)
        package_footer = QHBoxLayout()
        self.more_label = _label("", "V3PackageMore")
        self.total_label = _label("", "V3PackageTotal")
        package_footer.addWidget(self.more_label)
        package_footer.addStretch(1)
        package_footer.addWidget(self.total_label)
        picked.addLayout(package_footer)
        body.addWidget(selected, 35)

        options = QFrame(self)
        options.setObjectName("V3ExportCard")
        options_shell = QVBoxLayout(options)
        options_shell.setContentsMargins(0, 0, 0, 0)
        options_body = QWidget(options)
        opts = QVBoxLayout(options_body)
        opts.setContentsMargins(14, 12, 14, 12)
        opts.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(_label("Build options", "V3ExportCardTitle"))
        row.addStretch(1)
        row.addWidget(_label("In the order shown", "V3ExportQuiet"))
        opts.addLayout(row)
        opts.addWidget(_label("OUTPUT", "V3ExportCaption"))
        self.mode_combo = QComboBox(self)
        self.mode_combo.setObjectName("V3PackageMode")
        self.mode_combo.addItem("Individual clips + combined reel", "both")
        self.mode_combo.addItem("Combined reel", "reel")
        self.mode_combo.addItem("Individual clips", "individual")
        self.mode_combo.currentIndexChanged.connect(self._refresh_summary)
        opts.addWidget(self.mode_combo)
        opts.addWidget(_label("PRESET", "V3ExportCaption"))
        self.preset_combo = QComboBox(self)
        self.preset_combo.setObjectName("V3PackagePreset")
        self.preset_combo.addItem("Clean · Social 1080p", "social_1080p")
        self.preset_combo.addItem("Clean · Source quality", "source_quality")
        opts.addWidget(self.preset_combo)
        opts.addWidget(_label("DESTINATION", "V3ExportCaption"))
        self.destination = _label("-", "V3ExportDestination")
        self.destination.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.browse_button = QPushButton("Browse…", self)
        self.browse_button.setObjectName("V3ExportBrowse")
        self.browse_button.clicked.connect(self.destination_requested.emit)
        opts.addLayout(_destination_row(self.destination, self.browse_button))
        opts.addWidget(_label("What will be created", "V3ExportSummaryHeading"))
        self.summary = _label("No selected clips", "V3ExportReady")
        self.summary.setWordWrap(True)
        opts.addWidget(self.summary)
        options_shell.addWidget(_scroll_body(options_body), 1)
        tray = QVBoxLayout()
        tray.setContentsMargins(14, 0, 14, 10)
        self.status = _label("Ready", "V3ExportQuiet")
        self.status.setWordWrap(True)
        tray.addWidget(self.status)
        self.start_button = QPushButton("Start package", self)
        self.start_button.setObjectName("V3PackageStart")
        self.start_button.clicked.connect(self.start_requested.emit)
        self.start_button.setFixedHeight(42)
        self.start_button.setMaximumWidth(320)
        start_row = QHBoxLayout()
        start_row.addStretch(1)
        start_row.addWidget(self.start_button, 5)
        start_row.addStretch(1)
        tray.addLayout(start_row)
        self.cancel_button = QPushButton("Cancel export", self)
        self.cancel_button.setObjectName("V3ExportCancel")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.cancel_button.hide()
        tray.addWidget(self.cancel_button)
        options_shell.addLayout(tray)
        body.addWidget(options, 65)
        self._clip_count = 0
        self._clips: list[object] = []
        self._destination = ""

    def _make_row(self) -> None:
        row = _PackagePlayRowV3(self.rows_body)
        row.setObjectName("V3PackagePlayRow")
        row.setFixedHeight(32)
        line = QHBoxLayout(row)
        line.setContentsMargins(6, 2, 6, 2)
        line.setSpacing(6)
        grip = QLabel(row)
        grip.setObjectName("V3PackageGrip")
        grip.setFixedSize(12, 20)
        grip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grip_pixmap = tinted_icon(
            "more-horizontal-16.svg", "#738078", 14
        ).pixmap(QSize(14, 14)).transformed(
            QTransform().rotate(90),
            Qt.TransformationMode.SmoothTransformation,
        )
        grip.setPixmap(grip_pixmap)
        grip.setToolTip("Drag this row to reorder the package")
        number = _label("", "V3PackagePlayNumber")
        thumbnail = QLabel(row)
        thumbnail.setObjectName("V3PackageThumbnail")
        thumbnail.setFixedSize(32, 20)
        thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        copy = _label("", "V3PackagePlayCopy")
        copy.setWordWrap(False)
        remove = QPushButton(row)
        remove.setObjectName("V3PackageRemove")
        remove.setIcon(tinted_icon("dismiss-16.svg", "#9aa69d", 14))
        remove.setIconSize(QSize(14, 14))
        remove.setFixedSize(26, 26)
        remove.setToolTip("Remove this play from the package")
        remove.setAccessibleName("Remove play from package")
        remove.clicked.connect(
            lambda _checked=False, source=row:
            self.remove_requested.emit(
                str(getattr(source, "_clip_id", ""))))
        line.addWidget(grip)
        line.addWidget(number)
        line.addWidget(thumbnail)
        line.addWidget(copy, 1)
        line.addWidget(remove)
        row._thumbnail_label = thumbnail  # type: ignore[attr-defined]
        row._copy_label = copy  # type: ignore[attr-defined]
        row.move_requested.connect(self._move_clip)
        row.remove_requested.connect(self.remove_requested.emit)
        row._number_label = number
        self.rows_layout.addWidget(row)
        self.play_rows.append(row)

    def _move_clip(self, clip_id: str, delta: int) -> None:
        if not self.mode_combo.isEnabled():
            return
        index = next((i for i, clip in enumerate(self._clips)
                      if str(getattr(clip, "id", "")) == clip_id), -1)
        target = index + int(delta)
        if index < 0 or target < 0 or target >= len(self._clips):
            return
        self._clips[index], self._clips[target] = (
            self._clips[target], self._clips[index])
        self._render_rows()
        self.order_changed.emit([
            str(getattr(clip, "id", "")) for clip in self._clips])

    def _refresh_summary(self, *_args) -> None:
        count = len(self._clips)
        reel = sum(bool(getattr(c, "include_in_reel", True)) for c in self._clips)
        mode = self.mode_combo.currentData()
        parts = []
        if mode in ("individual", "both"):
            parts.append(f"{count} individual clips")
        if mode in ("reel", "both"):
            parts.append(f"1 reel · {reel} included plays" if reel else "No reel: no included plays")
        self.summary.setText(" + ".join(parts))

    def configure(self, clips: Iterable[object], *, destination: str) -> None:
        self._clips = list(clips)
        self._destination = destination
        self._render_rows()

    def _render_rows(self) -> None:
        clips = self._clips
        self._clip_count = len(clips)
        while len(self.play_rows) < len(clips):
            self._make_row()
        for index, row in enumerate(self.play_rows):
            if index >= len(clips):
                row.hide()
                continue
            clip = clips[index]
            row._clip_id = str(getattr(clip, "id", ""))  # type: ignore[attr-defined]
            row._number_label.setText(f"{index + 1:02d}")
            title = str(getattr(clip, "clip_title", "") or f"Play {index + 1:03d}")
            row.setAccessibleName(f"Package position {index + 1}: {title}")
            start = max(0, int(getattr(clip, "start_ms", 0))) / 1000
            duration = max(0, int(getattr(clip, "duration_ms", 0))) / 1000
            row._copy_label.setText(  # type: ignore[attr-defined]
                f"{title}\n{format_ms(int(start*1000), show_millis=True)} – {format_ms(int(getattr(clip, 'end_ms', 0)), show_millis=True)} · {duration:.1f}s"
                + (" · outside reel" if not getattr(clip, "include_in_reel", True) else ""))
            row._copy_label.setToolTip(row._copy_label.text())
            row._copy_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            thumb = row._thumbnail_label  # type: ignore[attr-defined]
            thumbnail_path = Path(str(getattr(clip, "thumbnail_path", "")))
            pixmap = QPixmap(str(thumbnail_path)) \
                if thumbnail_path.is_file() else QPixmap()
            if pixmap.isNull():
                thumb.setPixmap(tinted_icon(
                    "filmstrip-play-20.svg", "#6f7c72", 20
                ).pixmap(QSize(20, 20)))
            else:
                thumb.setPixmap(pixmap.scaled(
                    thumb.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation))
            row.show()
        self.more_label.setText(f"{len(clips)} selected plays")
        total_ms = sum(max(0, int(getattr(clip, "duration_ms", 0)))
                       for clip in clips)
        minutes, seconds = divmod(total_ms // 1000, 60)
        self.total_label.setText(
            f"TOTAL  {minutes:02d}:{seconds:02d}  ·  {len(clips)} PLAYS")
        self.destination.setText(self._destination or "-")
        self.destination.setToolTip(self._destination)
        self._refresh_summary()

    def set_ready(self, ready: bool, message: str = "") -> None:
        self.start_button.setEnabled(bool(ready))
        self.start_button.setProperty("ready", bool(ready))
        self.start_button.style().unpolish(self.start_button)
        self.start_button.style().polish(self.start_button)
        self.start_button.update()
        self.status.setText(message or (
            "Ready" if ready else "Finish or cancel the current export first"))

    def set_configuration_enabled(self, enabled: bool) -> None:
        self.mode_combo.setEnabled(enabled)
        self.preset_combo.setEnabled(enabled)
        self.browse_button.setEnabled(enabled)
        for row in self.play_rows:
            row.setEnabled(enabled)
            remove = row.findChild(QPushButton, "V3PackageRemove")
            if remove is not None:
                remove.setEnabled(enabled)


class ExportPageV3(QFrame):
    """A V3-only presentation that never duplicates the export worker."""

    review_requested = Signal()
    clip_start_requested = Signal()
    package_start_requested = Signal()
    cancel_requested = Signal()
    package_remove_requested = Signal(str)
    package_order_changed = Signal(list)
    open_path_requested = Signal(str)
    destination_requested = Signal()
    retry_requested = Signal(str)
    queued_cancel_requested = Signal(str)
    number_clips_requested = Signal()
    cutups_requested = Signal()

    def __init__(self, export_authority: QWidget, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3ExportPage")
        self.setFixedHeight(428)
        self._route = "clip"
        self._running = False
        self._route_started = False
        self._jobs: list[object] = []

        shell = QVBoxLayout(self)
        shell.setContentsMargins(10, 10, 10, 8)
        shell.setSpacing(8)
        header = QHBoxLayout()
        self.title = _label("Export clip", "V3ExportTitle")
        self.subtitle = _label("", "V3ExportSubtitle")
        header.addWidget(self.title)
        header.addWidget(self.subtitle)
        header.addStretch(1)
        self.cutups_button = QPushButton("Build cutups…", self)
        self.cutups_button.setToolTip("Group by player, play type, situation, or result; review clips before exporting")
        self.cutups_button.clicked.connect(self.cutups_requested.emit)
        header.addWidget(self.cutups_button)
        self.number_clips_button = QPushButton("Number clips in film order", self)
        self.number_clips_button.setObjectName("V3ExportNumberClips")
        self.number_clips_button.setToolTip(
            "Number every clip 001, 002, 003… by source time and put the number first "
            "in future filenames. Keeps your names. Ctrl+Z to undo.")
        self.number_clips_button.clicked.connect(self.number_clips_requested.emit)
        header.addWidget(self.number_clips_button)
        self.back_to_review_button = QPushButton("Back to Review", self)
        self.back_to_review_button.setObjectName("V3ExportBackToReview")
        self.back_to_review_button.setAccessibleName("Return to Review")
        self.back_to_review_button.setToolTip(
            "Return to Review. Active exports continue.")
        self.back_to_review_button.clicked.connect(self.review_requested.emit)
        self.queue_button = QPushButton("Queue", self)
        self.queue_button.setObjectName("V3ExportQueueButton")
        self.queue_menu = QMenu(self.queue_button)
        self.queue_menu.aboutToShow.connect(self._populate_queue_menu)
        self.queue_button.setMenu(self.queue_menu)
        header.addWidget(self.queue_button)
        header.addWidget(self.back_to_review_button)
        shell.addLayout(header)

        self.stage_strip = _StageStripV3(self)
        shell.addWidget(self.stage_strip)
        self.pages = QStackedWidget(self)
        self.pages.setObjectName("V3ExportRouteStack")
        self.clip_setup = _ClipExportSetupV3(self.pages)
        self.package_setup = _PackageSetupV3(self.pages)
        self.pages.addWidget(self.clip_setup)
        self.pages.addWidget(self.package_setup)
        self.pages.currentChanged.connect(
            lambda _index: QTimer.singleShot(0, self, self._size_start_buttons))
        shell.addWidget(self.pages, 1)

        self.clip_setup.destination_requested.connect(self.destination_requested.emit)
        self.package_setup.destination_requested.connect(self.destination_requested.emit)
        self.clip_setup.start_requested.connect(self.clip_start_requested.emit)
        self.package_setup.start_requested.connect(
            self.package_start_requested.emit)
        self.clip_setup.queue.cancel_requested.connect(
            self.cancel_requested.emit)
        self.clip_setup.queue.open_path_requested.connect(
            self.open_path_requested.emit)
        self.package_setup.cancel_requested.connect(
            self.cancel_requested.emit)
        self.package_setup.remove_requested.connect(
            self.package_remove_requested.emit)
        self.package_setup.order_changed.connect(
            self.package_order_changed.emit)

        # The original ExportPanel remains the sole owner of job state and
        # signal wiring. V3 does not clone it or its worker; it presents the
        # standard controls and reads the authority when synchronising status.
        self.export_authority = export_authority
        reparent_widget(export_authority, self)
        export_authority.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self, self._size_start_buttons)

    def _size_start_buttons(self) -> None:
        for button in (self.clip_setup.start_button, self.package_setup.start_button,
                       self.clip_setup.queue.cancel_button, self.package_setup.cancel_button):
            # Hidden stack pages have not received their final layout width.
            if not button.isVisible():
                continue
            button.ensurePolished()
            available = max(1, button.parentWidget().width() - 28)
            button.setFixedSize(min(320, available), 42)

    @property
    def route(self) -> str:
        return self._route

    def configure_clip(self, *, clip_title: str, duration_ms: int,
                       destination: str, preset_name: str,
                       accurate: bool) -> None:
        self._route = "clip"
        self._route_started = False
        self.title.setText("Export clip")
        self.stage_strip.set_work_label("Export")
        self.subtitle.setText(f"{clip_title} · {max(0, duration_ms) / 1000:.1f} seconds")
        self.pages.setCurrentWidget(self.clip_setup)
        self.clip_setup.configure(
            clip_title=clip_title,
            duration_ms=duration_ms,
            destination=destination,
            preset_name=preset_name,
            accurate=accurate,
        )

    def configure_package(
            self, clips: Iterable[object], *, destination: str) -> None:
        clips = list(clips)
        self._route = "package"
        self._route_started = False
        self.title.setText("Package / Cut Up")
        self.stage_strip.set_work_label("Build")
        self.subtitle.setText(f"{len(clips)} selected plays · ledger order")
        self.pages.setCurrentWidget(self.package_setup)
        self.package_setup.configure(clips, destination=destination)

    def set_ready(self, ready: bool, message: str = "") -> None:
        self.clip_setup.set_ready(ready, message)
        self.package_setup.set_ready(ready, message)

    def mark_started(self) -> None:
        self._route_started = True

    def sync_jobs(self, jobs: Iterable[object], *, running: bool) -> None:
        jobs = list(jobs)
        self._jobs = jobs
        self.queue_button.setText(f"Queue · {len(jobs)}")
        self.queue_button.setEnabled(bool(jobs))
        self._running = bool(running)
        self.clip_setup.queue.sync(jobs, running=running)
        self.package_setup.cancel_button.setVisible(running)
        self.package_setup.cancel_button.setEnabled(running)
        self.package_setup.start_button.setVisible(not running)
        QTimer.singleShot(0, self, self._size_start_buttons)
        self.clip_setup.start_button.setVisible(not running)
        self.package_setup.set_configuration_enabled(not running)
        for control in (
                self.clip_setup.format_combo,
                self.clip_setup.style_combo,
                self.clip_setup.preset_combo,
                self.clip_setup.accurate_check,
                self.clip_setup.browse_button):
            control.setEnabled(not running)
        statuses = {
            str(getattr(getattr(job, "status", ""), "value",
                        getattr(job, "status", ""))).lower()
            for job in jobs
        }
        if running:
            stage = 2
        elif self._route_started and (
                "completed" in statuses or "failed" in statuses
                or "cancelled" in statuses):
            stage = 3
        else:
            stage = 1
        self.stage_strip.set_stage(stage)
        if running:
            message = "Export in progress"
        elif stage == 3:
            counts = {name: sum(_QueueCardV3._status_name(j) == name for j in jobs)
                      for name in ("completed", "failed", "cancelled", "waiting")}
            message = ("Export complete" if counts["completed"] and not any(
                counts[name] for name in ("failed", "cancelled", "waiting")) else
                " · ".join(f"{count} {name}" for name, count in counts.items() if count))
        else:
            message = ""
        if message:
            self.clip_setup.status.setText(message)
            self.package_setup.status.setText(message)

    def _populate_queue_menu(self) -> None:
        self.queue_menu.clear()
        self._queue_actions = []
        for job in self._jobs:
            state = _QueueCardV3._status_name(job)
            path = str(getattr(job, "output_path", "") or "")
            name = Path(path).name or str(getattr(job, "display_name", "Export"))
            menu = self.queue_menu.addMenu(f"{state.upper()} · {name}")
            # PySide destroys submenus when temporary menuAction wrappers die.
            self._queue_actions.append(menu.menuAction())
            if state == "completed" and path:
                if Path(path).is_file():
                    menu.addAction("Open clip", lambda p=path: self.open_path_requested.emit(p))
                if Path(path).parent.is_dir():
                    menu.addAction("Show folder", lambda p=str(Path(path).parent): self.open_path_requested.emit(p))
            elif state == "failed":
                detail = menu.addAction(str(getattr(job, "error_message", "") or "Export failed"))
                detail.setEnabled(False)
                retry = menu.addAction("Retry original export", lambda jid=job.id: self.retry_requested.emit(jid))
                retry.setEnabled(not self._running)
            elif state == "waiting":
                menu.addAction("Cancel queued output", lambda jid=job.id: self.queued_cancel_requested.emit(jid))
            if menu.isEmpty():
                menu.addAction("No action available").setEnabled(False)
