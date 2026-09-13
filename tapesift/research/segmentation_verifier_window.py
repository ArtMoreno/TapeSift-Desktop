"""Research-only screen for verifying detected play boundaries."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from tapesift.core.config import AppSettings
from tapesift.research.segmentation_verification import (
    VerificationItem,
    VerificationSession,
)
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_core.video_player import VideoPlayer
from tapesift.ui_core.timeline import TimelineBlock
from tapesift.ui_v2.control_center import ControlCenterDeck


STATUS_COLORS = {
    "pending": "#7e8a82",
    "verified": "#46e485",
    "excluded": "#59615b",
    "review": "#f0c46a",
    "unclassified": "#df8534",
}

VERIFIER_QSS = """
#VerifierRoot {
    background: #07100b;
}
#VerifierHeader, #QueuePanel, #InspectorPanel, #PlayerPanel {
    background: #0c1510;
    border: 1px solid #223128;
    border-radius: 6px;
}
#VerifierHeader {
    border-left: 3px solid #46e485;
}
QLabel[verifierEyebrow="true"] {
    color: #46e485;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 2px;
}
QLabel[verifierTitle="true"] {
    color: #f4f7f5;
    font-family: "Rajdhani";
    font-size: 25px;
    font-weight: 700;
}
QLabel[verifierSection="true"] {
    color: #f4f7f5;
    font-family: "Rajdhani";
    font-size: 17px;
    font-weight: 700;
}
QLabel[verifierValue="true"] {
    color: #eef5f0;
    font-size: 13px;
}
QLabel[verifierMono="true"] {
    color: #b8c8bd;
    font-family: "Consolas";
    font-size: 12px;
}
QLabel[verifierSafety="true"] {
    color: #9aaca0;
    font-size: 11px;
}
QLabel[verifierError="true"] {
    color: #ffb3a7;
    background: #24130f;
    border: 1px solid #713a31;
    border-radius: 4px;
    padding: 7px;
    font-size: 11px;
}
QPushButton[verifierPrimary="true"] {
    color: #06130b;
    background: #46e485;
    border: 1px solid #46e485;
    font-weight: 700;
    min-height: 34px;
}
QPushButton[verifierPrimary="true"]:hover {
    background: #65ee9a;
}
QPushButton[verifierDanger="true"] {
    color: #f1c2b8;
    border-color: #824539;
}
QTableWidget#VerifierQueue {
    background: #09110d;
    alternate-background-color: #0d1711;
    border: 0;
    gridline-color: #1e2c24;
    selection-background-color: #183e29;
    selection-color: #ffffff;
}
QTableWidget#VerifierQueue::item {
    padding: 6px 5px;
}
QHeaderView::section {
    color: #8fa296;
    background: #0c1510;
    border: 0;
    border-bottom: 1px solid #29372f;
    padding: 6px;
    font-size: 10px;
    font-weight: 700;
}
"""


class SegmentationVerifierWindow(QMainWindow):
    """Fast keyboard-first review of research segmentation predictions."""

    def __init__(
        self,
        session: VerificationSession,
        settings: AppSettings | None = None,
        parent=None,
        *,
        pair_mode: bool = False,
    ) -> None:
        super().__init__(parent)
        self.session = session
        self.pair_mode = pair_mode
        self.settings = settings or AppSettings()
        self.settings.scrub_proxy_enabled = False
        self._loaded_source = ""
        self._pending_play = False
        self._shortcuts: list[QShortcut] = []

        self.setWindowTitle(
            "TapeSift Pair Review"
            if self.pair_mode else "TapeSift Segmentation Verifier")
        self.resize(1680, 960)
        self.setMinimumSize(1180, 720)

        root = QWidget(self)
        root.setObjectName("VerifierRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 12, 14, 12)
        root_layout.setSpacing(10)
        root_layout.addWidget(self._build_header())
        root_layout.addWidget(self._build_workspace(), 1)
        self.setCentralWidget(root)
        self.setStyleSheet(VERIFIER_QSS)

        self.player.player.mediaStatusChanged.connect(
            self._media_status_changed)
        self.player.clip_block_activated.connect(
            self._timeline_block_activated)
        self.player.clicked.connect(self.player.focus_transport)
        self._install_shortcuts()
        self._refresh_queue()
        self._select_index(self.session.cursor, save=False)

    def _build_header(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("VerifierHeader")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(16, 11, 16, 11)

        identity = QVBoxLayout()
        eyebrow = QLabel("TAPESIFT RESEARCH")
        eyebrow.setProperty("verifierEyebrow", True)
        title = QLabel(
            "TWO-ANGLE PAIR REVIEW"
            if self.pair_mode else "SEGMENTATION VERIFIER")
        title.setProperty("verifierTitle", True)
        identity.addWidget(eyebrow)
        identity.addWidget(title)
        layout.addLayout(identity)
        layout.addStretch()

        progress_box = QVBoxLayout()
        self.progress_label = QLabel()
        self.progress_label.setProperty("verifierValue", True)
        self.progress_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        safety = QLabel(
            "Research files only. TapeSift projects are never modified.")
        safety.setProperty("verifierSafety", True)
        safety.setAlignment(Qt.AlignmentFlag.AlignRight)
        progress_box.addWidget(self.progress_label)
        progress_box.addWidget(safety)
        layout.addLayout(progress_box)
        return panel

    def _build_workspace(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_queue_panel())
        splitter.addWidget(self._build_player_panel())
        splitter.addWidget(self._build_inspector_panel())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 6)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([380, 890, 390])
        return splitter

    def _build_queue_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("QueuePanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        title = QLabel("PAIR QUEUE" if self.pair_mode else "PILOT QUEUE")
        title.setProperty("verifierSection", True)
        layout.addWidget(title)
        self.queue_summary = QLabel()
        self.queue_summary.setProperty("verifierSafety", True)
        layout.addWidget(self.queue_summary)

        self.queue_table = QTableWidget(0, 5)
        self.queue_table.setObjectName("VerifierQueue")
        self.queue_table.setHorizontalHeaderLabels(
            ["#", "Film", "In", "Out", "State"])
        self.queue_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.queue_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.verticalHeader().hide()
        self.queue_table.horizontalHeader().setStretchLastSection(False)
        self.queue_table.setColumnWidth(0, 36)
        self.queue_table.setColumnWidth(1, 96)
        self.queue_table.setColumnWidth(2, 52)
        self.queue_table.setColumnWidth(3, 52)
        self.queue_table.setColumnWidth(4, 64)
        self.queue_table.itemSelectionChanged.connect(
            self._queue_selection_changed)
        layout.addWidget(self.queue_table, 1)

        hint = QLabel("Ctrl+Up / Ctrl+Down  Previous / Next")
        hint.setProperty("verifierMono", True)
        layout.addWidget(hint)
        return panel

    def _build_player_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("PlayerPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)

        row = QHBoxLayout()
        title = QLabel("SOURCE PLAYER")
        title.setProperty("verifierSection", True)
        row.addWidget(title)
        row.addStretch()
        self.loop_box = QCheckBox("Loop segment")
        self.loop_box.setChecked(False)
        self.loop_box.toggled.connect(self.player_loop_changed)
        row.addWidget(self.loop_box)
        replay = self._button("Replay  R", self._replay)
        row.addWidget(replay)
        layout.addLayout(row)

        self.player = VideoPlayer(self.settings, panel)
        self.control_center = ControlCenterDeck(panel)
        self.player.attach_control_center(self.control_center)
        self.player.timeline_legend.hide()
        self.player.name_edit.hide()
        self.player.add_clip_btn.hide()
        self.player.filename_preview.hide()
        self.player.error_label.hide()
        layout.addWidget(self.player, 1)
        layout.addWidget(self.control_center)

        hint = QLabel(
            "Space play/pause   J/K/L shuttle   ←/→ frame or jump   "
            "I set start   O set end   Ctrl+K different snaps"
            if self.pair_mode else
            "Space play/pause   J/K/L shuttle   ←/→ frame or jump   "
            "I set start   O set end   Ctrl+K split")
        hint.setProperty("verifierMono", True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        return panel

    def _build_inspector_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("InspectorPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 12, 14, 12)

        title = QLabel("CURRENT PREDICTION")
        title.setProperty("verifierSection", True)
        layout.addWidget(title)
        self.film_label = QLabel()
        self.film_label.setProperty("verifierValue", True)
        self.film_label.setWordWrap(True)
        layout.addWidget(self.film_label)
        self.source_label = QLabel()
        self.source_label.setProperty("verifierSafety", True)
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)

        details = QGridLayout()
        details.setContentsMargins(0, 10, 0, 6)
        details.setHorizontalSpacing(12)
        details.setVerticalSpacing(7)
        self.detail_values: dict[str, QLabel] = {}
        for row, (key, label) in enumerate((
            ("range", "RANGE"),
            ("duration", "DURATION"),
            ("candidate", "SOURCE"),
            ("signal", "SIGNAL"),
            ("angles", "ANGLES"),
            ("status", "STATUS"),
        )):
            name = QLabel(label)
            name.setProperty("verifierEyebrow", True)
            value = QLabel()
            value.setProperty("verifierMono", True)
            value.setWordWrap(True)
            details.addWidget(name, row, 0)
            details.addWidget(value, row, 1)
            self.detail_values[key] = value
        layout.addLayout(details)

        reason_title = QLabel("DETECTOR NOTE")
        reason_title.setProperty("verifierEyebrow", True)
        layout.addWidget(reason_title)
        self.reason_label = QLabel()
        self.reason_label.setProperty("verifierSafety", True)
        self.reason_label.setWordWrap(True)
        self.reason_label.setMinimumHeight(44)
        layout.addWidget(self.reason_label)

        self.edit_label = QLabel()
        self.edit_label.setProperty("verifierSafety", True)
        self.edit_label.setWordWrap(True)
        layout.addWidget(self.edit_label)
        self.action_error_label = QLabel()
        self.action_error_label.setProperty("verifierError", True)
        self.action_error_label.setWordWrap(True)
        self.action_error_label.hide()
        layout.addWidget(self.action_error_label)
        if self.pair_mode:
            pair_guide = QLabel(
                "PAIR REVIEW\n"
                "Enter: both angles show the same snap\n"
                "I / O, then Enter: same snap, adjust outer bounds\n"
                "Ctrl+K: the clips are different snaps\n"
                "X: no valid logical play")
            pair_guide.setProperty("verifierSafety", True)
            pair_guide.setWordWrap(True)
            layout.addWidget(pair_guide)
        layout.addStretch()

        accept = self._button(
            "Accept && Next   Enter", self._accept, primary=True)
        accept.setMinimumHeight(42)
        layout.addWidget(accept)

        cut_row = QHBoxLayout()
        cut_row.addWidget(self._button("Set Start  I", self._set_start))
        cut_row.addWidget(self._button("Set End  O", self._set_end))
        layout.addLayout(cut_row)
        layout.addWidget(self._button(
            "Different Snaps: Split   Ctrl+K"
            if self.pair_mode else "Split at Playhead   Ctrl+K",
            self._split))

        if not self.pair_mode:
            merge_row = QHBoxLayout()
            self.merge_previous_button = self._button(
                "Merge Previous", lambda: self._merge(-1))
            self.merge_next_button = self._button(
                "Merge Next  M", lambda: self._merge(1))
            merge_row.addWidget(self.merge_previous_button)
            merge_row.addWidget(self.merge_next_button)
            layout.addLayout(merge_row)

        bottom = QHBoxLayout()
        bottom.addWidget(self._button(
            "Exclude  X", self._exclude, danger=True))
        self.undo_button = self._button("Undo  Ctrl+Z", self._undo)
        bottom.addWidget(self.undo_button)
        layout.addLayout(bottom)
        return panel

    def _button(
        self,
        text: str,
        callback: Callable,
        *,
        primary: bool = False,
        danger: bool = False,
    ) -> QPushButton:
        button = QPushButton(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(callback)
        if primary:
            button.setProperty("verifierPrimary", True)
        if danger:
            button.setProperty("verifierDanger", True)
        return button

    def _shortcut(self, keys: str, callback: Callable) -> None:
        shortcut = QShortcut(QKeySequence(keys), self)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        shortcut.activated.connect(callback)
        self._shortcuts.append(shortcut)

    def _install_shortcuts(self) -> None:
        bindings = [
            ("Space", self.player.toggle_play),
            ("J", self.player.shuttle_reverse),
            ("K", self.player.shuttle_stop),
            ("L", self.player.shuttle_forward),
            ("Left", lambda: self.player.step_or_jump(-1)),
            ("Right", lambda: self.player.step_or_jump(1)),
            ("Shift+Left", self.player.jump_backward),
            ("Shift+Right", self.player.jump_forward),
            ("Ctrl+Left", lambda: self.player.seek_relative(-5_000)),
            ("Ctrl+Right", lambda: self.player.seek_relative(5_000)),
            ("R", self._replay),
            ("Return", self._accept),
            ("Enter", self._accept),
            ("Ctrl+K", self._split),
            ("X", self._exclude),
            ("I", self._set_start),
            ("O", self._set_end),
            ("Ctrl+Z", self._undo),
            ("Ctrl+Up", lambda: self._navigate(-1)),
            ("Ctrl+Down", lambda: self._navigate(1)),
            ("PgUp", lambda: self._navigate(-1)),
            ("PgDown", lambda: self._navigate(1)),
            ("Esc", self._pause_and_focus),
        ]
        if not self.pair_mode:
            bindings.extend([
                ("M", lambda: self._merge(1)),
                ("Shift+M", lambda: self._merge(-1)),
            ])
        for keys, callback in bindings:
            self._shortcut(keys, callback)

    def _refresh_queue(self) -> None:
        self.queue_table.blockSignals(True)
        self.queue_table.setRowCount(len(self.session.items))
        for row, item in enumerate(self.session.items):
            state = (
                "Review" if item.status == "pending"
                and item.detector_needs_review else item.status.title())
            values = (
                f"{row + 1:02d}",
                self._short_film_name(item),
                format_ms(item.start_ms),
                format_ms(item.end_ms),
                state,
            )
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setData(Qt.ItemDataRole.UserRole, item.item_id)
                if column == 4:
                    color_key = (
                        "review" if state == "Review" else item.status)
                    cell.setForeground(QColor(
                        STATUS_COLORS.get(color_key, "#b8c8bd")))
                self.queue_table.setItem(row, column, cell)
        self.queue_table.blockSignals(False)
        self._update_progress()

    def _update_progress(self) -> None:
        total = len(self.session.items)
        self.progress_label.setText(
            f"{self.session.completed_count} OF {total} VERIFIED  •  "
            f"{self.session.pending_count} REMAINING")
        films = len({item.film_id for item in self.session.items})
        noun = "pair proposals" if self.pair_mode else "predictions"
        self.queue_summary.setText(
            f"{total} {noun} from {films} films  •  autosaves every action")
        self.undo_button.setEnabled(self.session.can_undo)

    def _queue_selection_changed(self) -> None:
        rows = self.queue_table.selectionModel().selectedRows()
        if rows:
            self._select_index(rows[0].row())

    def _select_index(self, index: int, *, save: bool = True) -> None:
        if not self.session.items:
            return
        index = max(0, min(index, len(self.session.items) - 1))
        if save and index != self.session.cursor:
            self.session.set_cursor(index)
        else:
            self.session.cursor = index

        self.queue_table.blockSignals(True)
        self.queue_table.selectRow(index)
        self.queue_table.scrollToItem(
            self.queue_table.item(index, 0),
            QAbstractItemView.ScrollHint.PositionAtCenter,
        )
        self.queue_table.blockSignals(False)
        self._clear_action_error()
        self._refresh_current()
        self._load_current_source()

    def _refresh_current(self) -> None:
        item = self.session.current
        if item is None:
            return
        self.film_label.setText(self._short_film_name(item))
        self.source_label.setText(Path(item.analysis_source).name)
        self.detail_values["range"].setText(
            f"{format_ms(item.start_ms)}  →  {format_ms(item.end_ms)}")
        self.detail_values["duration"].setText(
            f"{item.duration_ms / 1000:.2f} sec")
        self.detail_values["candidate"].setText(
            item.candidate_kind.replace("_", " ").upper())
        self.detail_values["signal"].setText(
            item.detector_signal.upper() or "UNKNOWN")
        self.detail_values["angles"].setText(str(item.angle_count))
        self.detail_values["status"].setText(item.status.upper())
        self.reason_label.setText(
            item.detector_reason or "No detector warning on this prediction.")
        self.edit_label.setText(
            "EDIT HISTORY\n" + "\n".join(item.edit_history[-4:])
            if item.edit_history else "EDIT HISTORY\nNo manual edits")
        self._update_merge_buttons()
        self.player.in_point_ms = item.start_ms
        self.player.out_point_ms = item.end_ms
        self.player._update_marks_label()
        self._set_timeline_blocks()
        self._update_progress()

    def _set_timeline_blocks(self) -> None:
        current = self.session.current
        if current is None:
            self.player.set_clip_blocks([])
            return
        blocks = []
        for item in self.session.items:
            if item.film_id != current.film_id:
                continue
            if item.status == "excluded":
                key = "excluded"
            elif item.status == "verified":
                key = "verified"
            elif item.candidate_kind == "unclassified":
                key = "unclassified"
            elif item.detector_needs_review:
                key = "review"
            else:
                key = "pending"
            blocks.append(TimelineBlock(
                start_ms=item.start_ms,
                end_ms=item.end_ms,
                selected=item.item_id == current.item_id,
                clip_id=item.item_id,
                kind=key,
                title=f"{key.title()} prediction",
                colour=STATUS_COLORS[key],
                category_label=key.title(),
            ))
        self.player.set_clip_blocks(blocks)

    def _load_current_source(self) -> None:
        item = self.session.current
        if item is None:
            return
        source = Path(item.analysis_source)
        if not source.is_file():
            self._show_error(f"Video source is missing:\n{source}")
            return
        if str(source) != self._loaded_source:
            self._loaded_source = str(source)
            self._pending_play = True
            self.player.load(source, item.frame_rate)
        else:
            self._play_current()

    def _media_status_changed(
        self, status: QMediaPlayer.MediaStatus
    ) -> None:
        if self._pending_play and status in {
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        }:
            self._pending_play = False
            QTimer.singleShot(0, self._play_current)

    def _play_current(self) -> None:
        item = self.session.current
        if item is None:
            return
        if (
            self.player.player.mediaStatus()
            == QMediaPlayer.MediaStatus.EndOfMedia
        ):
            self.player.stop()
        self.player.in_point_ms = item.start_ms
        self.player.out_point_ms = item.end_ms
        self.player._update_marks_label()
        self.player.play_clip_range(
            item.start_ms, item.end_ms, self.loop_box.isChecked())
        self.player.focus_transport()

    def player_loop_changed(self, checked: bool) -> None:
        self.player.set_range_loop(checked)

    def _timeline_block_activated(
        self, item_id: str, timestamp_ms: int
    ) -> None:
        index = next((
            row for row, item in enumerate(self.session.items)
            if item.item_id == item_id
        ), None)
        if index is None:
            return
        self._select_index(index)
        QTimer.singleShot(0, lambda: self.player.seek_to(timestamp_ms))

    def _navigate(self, direction: int) -> None:
        self._select_index(self.session.cursor + direction)

    def _run_action(self, action: Callable[[], None]) -> None:
        try:
            action()
        except ValueError as exc:
            self._show_action_error(str(exc))
            return
        self._clear_action_error()
        self._refresh_queue()
        self._select_index(self.session.cursor, save=False)
        if self.session.last_action:
            self.statusBar().showMessage(
                f"Autosaved: {self.session.last_action}", 3_000)

    def _show_action_error(self, message: str) -> None:
        self.action_error_label.setText(
            f"Could not apply that action: {message}")
        self.action_error_label.show()
        self.statusBar().showMessage(message)

    def _clear_action_error(self) -> None:
        self.action_error_label.clear()
        self.action_error_label.hide()
        self.statusBar().clearMessage()

    def _update_merge_buttons(self) -> None:
        if self.pair_mode:
            return
        for button, direction in (
            (self.merge_previous_button, -1),
            (self.merge_next_button, 1),
        ):
            error = self.session.merge_error(direction)
            button.setEnabled(error is None)
            button.setToolTip(
                error or "Merge this segment with the adjacent sample.")

    def _accept(self) -> None:
        self._run_action(self.session.accept)

    def _exclude(self) -> None:
        self._run_action(self.session.exclude)

    def _split(self) -> None:
        self._run_action(
            lambda: self.session.split(self.player.position_ms()))

    def _merge(self, direction: int) -> None:
        self._run_action(lambda: self.session.merge(direction))

    def _set_start(self) -> None:
        self._run_action(
            lambda: self.session.set_start(self.player.position_ms()))

    def _set_end(self) -> None:
        self._run_action(
            lambda: self.session.set_end(self.player.position_ms()))

    def _undo(self) -> None:
        if not self.session.can_undo:
            self._show_action_error("Nothing to undo in this session.")
            return
        self._run_action(self.session.undo)

    def _replay(self) -> None:
        self._play_current()

    def _pause_and_focus(self) -> None:
        self.player.shuttle_stop()
        self.player.focus_transport()

    def _show_error(self, text: str) -> None:
        QMessageBox.warning(self, "TapeSift Verifier", text)

    @staticmethod
    def _short_film_name(item: VerificationItem) -> str:
        name = item.film_name
        replacements = {
            "BETHUNE COOKMAN O VS. MIAMI D": "Bethune O vs Miami D",
            "PITTSBURGH O VS. MIAMI D": "Pittsburgh O vs Miami D",
        }
        return replacements.get(name.upper(), name)

    def closeEvent(self, event) -> None:
        self.session.save()
        self.player.unload()
        super().closeEvent(event)
