"""Clip list table: overview, reorder, multi-select actions, warnings."""

from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
    QPushButton, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
)

from tapesift.models.clip import Clip, ExportStatus
from tapesift.models.export_settings import BUILTIN_PRESETS
from tapesift.services import filename_service
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_core.first_read_ui import (
    needs_first_read_call, needs_logging, unresolved_count,
)
from tapesift.ui_core.flow_layout import FlowLayout
from tapesift.ui_core.timeline import (
    C_BLOCK, PLAY_COLORS, PLAY_LABELS, classify_play_kind,
)

COLUMNS = ["Play", "Start", "End", "Duration", "Title", "Export filename",
           "Label", "Tags", "Preset", "Review", "Export"]
COL_NUM, COL_START, COL_END, COL_DUR, COL_TITLE, COL_FILE, \
    COL_LABEL, COL_TAGS, COL_PRESET, COL_STATUS, COL_ENABLED = range(11)

VERY_SHORT_MS = 1000
VERY_LONG_MS = 10 * 60 * 1000

STATUS_TEXT = {
    ExportStatus.NOT_EXPORTED: "-",
    ExportStatus.WAITING: "Waiting",
    ExportStatus.PREPARING: "Preparing",
    ExportStatus.EXPORTING: "Exporting…",
    ExportStatus.COMPLETED: "✔ Done",
    ExportStatus.FAILED: "✘ Failed",
    ExportStatus.CANCELLED: "Cancelled",
}

# Brand green is reserved for primary actions. A status is a fact about the
# clip, not something to press, so it uses a muted green.
STATUS_GREEN = "#6fae86"

REVIEW_STATUS = {
    "unlogged": ("○ Unlogged", "#8e9b91"),
    "logged": ("Logged", STATUS_GREEN),
    "needs_fix": ("! Needs Fix", "#f0c46a"),
    "excluded": ("⊘ Excluded", "#737d76"),
    "autodetect_pending": ("! Detect Pending", "#f0c46a"),
    "autodetect_confirmed": ("Detect Checked", STATUS_GREEN),
    "autodetect_no_play": ("⊘ No Play", "#8e9b91"),
    "autodetect_withdrawn": ("⊘ Miss Withdrawn", "#8e9b91"),
}

PLAY_CODES = {
    "run": "R",
    "pass": "P",
    "rpo": "RPO",
    "penalty": "PEN",
    "sack": "S",
    "interception": "I",
    "touchdown": "TD",
}

REVIEW_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1
REVIEW_TOOLTIP_ROLE = int(Qt.ItemDataRole.UserRole) + 2
PLAY_COLOR_ROLE = int(Qt.ItemDataRole.UserRole) + 3
GROUP_ROLE = int(Qt.ItemDataRole.UserRole) + 4
FIRST_READ_CALL_ROLE = int(Qt.ItemDataRole.UserRole) + 5


def quarter_key_for(clip: Clip) -> str:
    """Normalize the loose Quarter detail into a stable ledger heading."""
    value = clip.details.get("quarter", "").strip().upper()
    value = value.replace("QUARTER", "Q").replace(" ", "")
    if value in {"1", "2", "3", "4"}:
        return f"Q{value}"
    if value in {"Q1", "Q2", "Q3", "Q4", "OT"}:
        return value
    return "UNASSIGNED"


class PlayRailDelegate(QStyledItemDelegate):
    """Paint the play-type color as a narrow ledger rail, not row status."""

    def paint(self, painter: QPainter, option, index) -> None:
        super().paint(painter, option, index)
        if index.column() != COL_NUM:
            return
        color = index.data(PLAY_COLOR_ROLE)
        first_read_call = bool(index.data(FIRST_READ_CALL_ROLE))
        if not color and not first_read_call:
            return
        painter.save()
        if color:
            rail = option.rect.adjusted(0, 1, 0, -1)
            rail.setWidth(3)
            painter.fillRect(rail, QColor(color))
        if first_read_call:
            # A quiet second tick survives long-list scrolling without
            # replacing the play-type rail or turning the row into an alert.
            call_rail = option.rect.adjusted(4, 5, 0, -5)
            call_rail.setWidth(2)
            painter.fillRect(call_rail, QColor("#c9b66e"))
        painter.restore()


def review_status_for(
        clip: Clip, warnings: list[str]) -> tuple[str, str, str]:
    """Review state, visible text, and explanation for a clip ledger row."""
    lineage = clip.detection_lineage
    if (
        not clip.enabled
        and lineage.get("candidate_ids")
        and lineage.get("false_positive_confirmed_at")
    ):
        kind = "autodetect_no_play"
        explanation = "Explicitly confirmed as containing no real play"
    elif (
        not clip.enabled
        and lineage.get("recovery_id")
        and lineage.get("recovery_withdrawn_at")
    ):
        kind = "autodetect_withdrawn"
        explanation = "Explicitly withdrew a mistaken missed-play correction"
    elif not clip.enabled:
        kind = "excluded"
        explanation = "Excluded from export"
        if (
            lineage
            and autodetect_review_status_for(clip) == "autodetect_pending"
        ):
            explanation += (
                "\nThis autodetect correction still needs an explicit "
                "review decision")
    elif warnings:
        kind = "needs_fix"
        explanation = "\n".join(warnings)
    elif clip.details:
        kind = "logged"
        explanation = "Play details have been logged"
        detector_status = autodetect_review_status_for(clip)
        if detector_status == "autodetect_pending":
            explanation += (
                "\nAutodetect correction still needs explicit review")
        elif detector_status == "autodetect_confirmed":
            explanation += "\nAutodetect correction explicitly reviewed"
    elif autodetect_review_status_for(clip) == "autodetect_pending":
        kind = "autodetect_pending"
        explanation = "Autodetect correction still needs explicit review"
    elif autodetect_review_status_for(clip) == "autodetect_confirmed":
        kind = "autodetect_confirmed"
        explanation = "Autodetect correction explicitly reviewed"
    else:
        kind = "unlogged"
        explanation = "No play details logged yet"
    text, _colour = REVIEW_STATUS[kind]
    return kind, text, explanation


def autodetect_review_status_for(clip: Clip) -> str | None:
    """Return detector-review state independently of the primary row status.

    A clip can need both a geometry fix and an explicit detector decision.
    Keeping this state separate lets both review filters find it even though
    the ledger has room for only one primary status label.
    """
    lineage = clip.detection_lineage
    if not lineage:
        return None
    if (
        not clip.enabled
        and lineage.get("candidate_ids")
        and lineage.get("false_positive_confirmed_at")
    ):
        return "autodetect_no_play"
    if (
        not clip.enabled
        and lineage.get("recovery_id")
        and lineage.get("recovery_withdrawn_at")
    ):
        return "autodetect_withdrawn"
    if clip.enabled and lineage.get("reviewed_at"):
        return "autodetect_confirmed"
    return "autodetect_pending"


def is_ignored_autodetect_clip(clip: Clip) -> bool:
    """Rows kept for reversibility but omitted from the everyday play list."""
    lineage = clip.detection_lineage
    return bool(
        lineage.get("suppressed_unclassified")
        or (
            not clip.enabled
            and lineage.get("candidate_ids")
            and lineage.get("false_positive_confirmed_at")
        )
        or (
            not clip.enabled
            and lineage.get("recovery_id")
            and lineage.get("recovery_withdrawn_at")
        )
    )


def compute_warnings(clips: list[Clip], duration_ms: int, template: str,
                     project_name: str, separator_style: str) -> dict[str, list[str]]:
    """Per-clip warning texts, keyed by clip id."""
    warnings: dict[str, list[str]] = {c.id: [] for c in clips}
    duplicate_names = filename_service.find_duplicate_bases(
        clips, template, project_name, separator_style)
    seen_ranges: dict[tuple[int, int, str], str] = {}
    ordered = sorted([c for c in clips if c.enabled], key=lambda c: c.start_ms)
    for i, clip in enumerate(ordered):
        if clip.duration_ms < VERY_SHORT_MS:
            warnings[clip.id].append("Very short clip (under 1 second).")
        if clip.duration_ms > VERY_LONG_MS:
            warnings[clip.id].append("Very long clip (over 10 minutes).")
        if duration_ms and clip.end_ms > duration_ms:
            warnings[clip.id].append("Extends past the end of the source video.")
        # Same range with the same title looks accidental; same range with a
        # different title is an intentional version (e.g. per-player copies).
        rng = (clip.start_ms, clip.end_ms, clip.clip_title.strip().lower())
        if rng in seen_ranges:
            warnings[clip.id].append("Duplicate of another clip's exact range.")
        seen_ranges[rng] = clip.id
        if i + 1 < len(ordered) and ordered[i + 1].start_ms < clip.end_ms:
            warnings[clip.id].append("Overlaps the next clip.")
        rendered = filename_service.render_template(
            template, clip, project_name, separator_style).lower()
        if rendered in duplicate_names:
            warnings[clip.id].append(
                "Same export filename as another clip (a _2 suffix will be added).")
    return warnings


class ClipListWidget(QWidget):
    selection_changed = Signal(list)          # list of clip ids
    previous_requested = Signal()
    next_requested = Signal()
    reorder_requested = Signal(int, int)      # from_index, to_index
    delete_requested = Signal(list)
    duplicate_requested = Signal(str)
    preview_requested = Signal(str)
    toggle_enabled_requested = Signal(str, bool)
    toggle_reel_requested = Signal(str, bool)
    bulk_edit_requested = Signal(list)        # clip ids
    mark_reviewed_requested = Signal(list)    # detected clip ids
    mark_missed_requested = Signal(list)      # ordinary clips, explicit misses
    confirm_false_positive_requested = Signal(list)
    view_mode_changed = Signal(bool)          # True when compact
    review_filter_changed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # The heading gets its own band so the panel reads in layers:
        # header on top, content below, separated by a hairline. The row
        # object itself is unchanged - the V2 shell hosts project-level
        # controls on it.
        header_band = QWidget(self)
        header_band.setObjectName("ClipLedgerHeader")
        header_band.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_row = QHBoxLayout(header_band)
        header_row.setContentsMargins(0, 0, 0, 0)
        # Exposed so the V2 shell can host project-level controls on a heading
        # that already exists, instead of adding a header row above it.
        self.header_row = header_row
        heading = QLabel("Clips")
        self.heading_label = heading
        heading.setProperty("role", "heading")
        header_row.addWidget(heading)
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "subtle")
        header_row.addWidget(self.count_label)
        header_row.addStretch()
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("ClipLedgerProgress")
        self.progress_label.setProperty("role", "subtle")
        self.progress_label.hide()
        header_row.addWidget(self.progress_label)
        # The safe-glyph set that renders reliably across Consolas, Rajdhani
        # and the system fallback is small: « » ‹ › − • … . Everything else
        # risks tofu on styled widgets. So no up/down/play/gear symbols here.
        self.previous_btn = QPushButton("‹ Previous Clip")
        self.previous_btn.setProperty("clipNavigation", "true")
        self.previous_btn.setToolTip(
            "Select the previous clip and move playback to its start "
            "(Ctrl+Up or Page Up)")
        self.previous_btn.clicked.connect(self.previous_requested.emit)
        self.next_btn = QPushButton("Next Clip ›")
        self.next_btn.setProperty("clipNavigation", "true")
        self.next_btn.setToolTip(
            "Select the next clip and move playback to its start "
            "(Ctrl+Down or Page Down)")
        self.next_btn.clicked.connect(self.next_requested.emit)
        # With 100+ detected plays, a comfortable density that still fits
        # many rows on screen matters more than a wide, sparse layout.
        self.view_btn = QToolButton()
        self.view_btn.setCheckable(True)
        self.view_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view_btn.setToolTip(
            "Toggle between Comfortable and Compact row heights to fit "
            "more clips on screen.")
        self.view_btn.toggled.connect(self._view_toggled)
        header_row.addWidget(self.view_btn)
        self.up_btn = QPushButton("Move Up")
        self.down_btn = QPushButton("Move Down")
        self.up_btn.clicked.connect(lambda: self._move_selected(-1))
        self.down_btn.clicked.connect(lambda: self._move_selected(1))
        header_row.addWidget(self.up_btn)
        header_row.addWidget(self.down_btn)
        layout.addWidget(header_band)

        # V2 review actions need their own row at sidebar widths. Keeping
        # Detect, Export, and New Clip in the heading row let Qt compress them
        # into unlabeled color swatches when the clip count grew.
        self.header_actions = QWidget()
        self.header_actions.setObjectName("ClipLedgerHeaderActions")
        self.header_actions_row = QHBoxLayout(self.header_actions)
        self.header_actions_row.setContentsMargins(0, 0, 0, 0)
        self.header_actions_row.setSpacing(5)
        self.header_actions.hide()
        layout.addWidget(self.header_actions)

        nav_row = QHBoxLayout()
        nav_row.setSpacing(6)
        nav_row.addWidget(self.previous_btn)
        nav_row.addStretch()
        nav_row.addWidget(self.next_btn)
        layout.addLayout(nav_row)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setItemDelegate(PlayRailDelegate(self.table))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(52)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_FILE, QHeaderView.ResizeMode.Stretch)
        self.table.itemSelectionChanged.connect(self._emit_selection)
        self.table.itemDoubleClicked.connect(self._double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        # Find a clip inside this project without detouring to Library Search.
        filter_row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(
            "Filter clips - name, tag, player, result…")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.filter_count = QLabel("")
        self.filter_count.setProperty("role", "subtle")
        filter_row.addWidget(self.filter_edit, 1)
        filter_row.addWidget(self.filter_count)
        layout.addLayout(filter_row)

        self.filter_buttons = QWidget()
        # A fixed QHBoxLayout squeezed the five chips until their text
        # clipped in a narrow ledger dock. Flow layout wraps them to a
        # second row instead, the same treatment Library chips already get.
        filter_buttons_layout = FlowLayout(self.filter_buttons, spacing=6)
        self.all_filter_btn = self._filter_button("All", "all")
        self.unlogged_filter_btn = self._filter_button(
            "Unlogged", "unlogged")
        self.needs_fix_filter_btn = self._filter_button(
            "Needs Fix", "needs_fix")
        self.detect_pending_filter_btn = self._filter_button(
            "Detect Pending", "autodetect_pending")
        self.ignored_filter_btn = self._filter_button(
            "Ignored", "ignored")
        for button in (
                self.all_filter_btn, self.unlogged_filter_btn,
                self.needs_fix_filter_btn, self.detect_pending_filter_btn,
                self.ignored_filter_btn):
            filter_buttons_layout.addWidget(button)
        self.all_filter_btn.setChecked(True)
        self.filter_buttons.hide()
        layout.addWidget(self.filter_buttons)

        self.table.itemChanged.connect(self._item_changed)
        self.table.cellClicked.connect(self._cell_clicked)
        layout.addWidget(self.table, 1)

        # Shown over the table while a project has no clips yet.
        self.empty_state = QLabel(
            "No clips yet.  Press I and O, then name your first clip - "
            "or choose Detect Plays to scan the full game automatically.")
        self.empty_state.setProperty("role", "subtle")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_state.hide()
        layout.addWidget(self.empty_state)

        self._clips: list[Clip] = []
        self._sidebar_mode = False
        self._filter_mode = "all"
        self._filter_scope_ids: set[str] | None = None
        self._navigation_scope_ids: list[str] | None = None
        self._ignored_fragment_count = 0
        self._ignored_fragment_ms = 0
        self._warnings: dict[str, list[str]] = {}
        self._clip_rows: dict[str, int] = {}
        self._row_to_clip_index: dict[int, int] = {}
        self._group_rows: dict[int, str] = {}
        self._group_members: dict[str, list[str]] = {}
        self._clip_groups: dict[str, str] = {}
        self._collapsed_groups: set[str] = set()

    def _filter_button(self, label: str, mode: str) -> QPushButton:
        button = QPushButton(label)
        button.setCheckable(True)
        button.setProperty("ledgerFilter", "true")
        button.clicked.connect(
            lambda _checked=False, value=mode: self._set_filter_mode(value))
        return button

    # ---------- population ----------

    def set_clips(self, clips: list[Clip], duration_ms: int, template: str,
                  project_name: str, separator_style: str) -> None:
        selected = self.selected_clip_ids()
        current_clip_id = None
        current_item = self.table.item(self.table.currentRow(), COL_NUM)
        if current_item is not None:
            current_clip_id = current_item.data(Qt.ItemDataRole.UserRole)
        vertical_scroll = self.table.verticalScrollBar().value()
        horizontal_scroll = self.table.horizontalScrollBar().value()

        previous_ids = {clip.id for clip in self._clips}
        incoming_ids = {clip.id for clip in clips}
        if previous_ids and incoming_ids and previous_ids.isdisjoint(
                incoming_ids):
            self._collapsed_groups.clear()
        self._clips = clips
        warnings = compute_warnings(clips, duration_ms, template,
                                    project_name, separator_style)
        self._warnings = warnings
        self.empty_state.setVisible(not clips)
        self.table.setVisible(bool(clips))
        self.table.blockSignals(True)
        self.table.clearSpans()
        self._clip_rows.clear()
        self._row_to_clip_index.clear()
        self._group_rows.clear()
        self._group_members.clear()
        self._clip_groups.clear()

        if self._sidebar_mode:
            segments: list[tuple[str, list[tuple[int, Clip]]]] = []
            for clip_index, clip in enumerate(clips):
                quarter = quarter_key_for(clip)
                if not segments or segments[-1][0] != quarter:
                    segments.append((quarter, []))
                segments[-1][1].append((clip_index, clip))
            row_count = len(clips) + len(segments)
            self.table.setRowCount(row_count)
            row = 0
            for segment_index, (quarter, members) in enumerate(segments):
                group_id = f"{quarter}:{segment_index}"
                member_clips = [clip for _index, clip in members]
                self._group_rows[row] = group_id
                self._group_members[group_id] = [
                    clip.id for clip in member_clips]
                self._write_group_row(
                    row, group_id, quarter, member_clips, warnings)
                row += 1
                for clip_index, clip in members:
                    self._clip_rows[clip.id] = row
                    self._row_to_clip_index[row] = clip_index
                    self._clip_groups[clip.id] = group_id
                    self._write_row(
                        row, clip, warnings.get(clip.id, []), template,
                        project_name, separator_style)
                    row += 1
        else:
            self.table.setRowCount(len(clips))
            for row, clip in enumerate(clips):
                self._clip_rows[clip.id] = row
                self._row_to_clip_index[row] = row
                self._write_row(
                    row, clip, warnings.get(clip.id, []), template,
                    project_name, separator_style)
        self.table.blockSignals(False)
        # setRowCount resets heights, so reassert the current density.
        self.set_compact(self.view_btn.isChecked())
        self._apply_filter()      # keep an active filter applied after refresh

        self._update_count_label(warnings)
        # Restore selection by clip identity, not by row. A sort, split, or
        # undo may move the selected clip while the table is being rebuilt.
        if selected:
            self.table.blockSignals(True)
            self.table.clearSelection()
            selection_model = self.table.selectionModel()
            for clip in clips:
                if clip.id in selected:
                    row = self._clip_rows[clip.id]
                    index = self.table.model().index(row, COL_NUM)
                    selection_model.select(
                        index,
                        QItemSelectionModel.SelectionFlag.Select |
                        QItemSelectionModel.SelectionFlag.Rows,
                    )
                    if clip.id == current_clip_id:
                        selection_model.setCurrentIndex(
                            index,
                            QItemSelectionModel.SelectionFlag.NoUpdate,
                        )
            self.table.blockSignals(False)

        # Rebuilding the items must not throw the user back to the top of a
        # long game. Restore both axes after row heights and filters settle.
        self.table.verticalScrollBar().setValue(vertical_scroll)
        self.table.horizontalScrollBar().setValue(horizontal_scroll)
        self._update_navigation_buttons()

    def _write_group_row(
            self, row: int, group_id: str, quarter: str,
            clips: list[Clip],
            warnings: dict[str, list[str]]) -> None:
        clips = [
            clip for clip in clips
            if is_ignored_autodetect_clip(clip)
            == (self._filter_mode == "ignored")
        ]
        logged = sum(1 for clip in clips if clip.enabled and clip.details)
        needs_fix = sum(
            1 for clip in clips if clip.enabled and warnings.get(clip.id))
        disclosure = ">" if group_id in self._collapsed_groups else "v"
        plural = "play" if len(clips) == 1 else "plays"
        text = f"{disclosure}  {quarter}     {len(clips)} {plural}"
        if logged:
            text += f"  •  {logged} logged"
        if needs_fix:
            # "needs fix" everywhere: the filter chip, the row badge and this
            # count all name the same state, so they use the same word.
            text += f"  •  {needs_fix} needs fix"
        item = QTableWidgetItem(text)
        item.setData(GROUP_ROLE, group_id)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        item.setForeground(QBrush(QColor("#cbd5ce")))
        item.setBackground(QBrush(QColor("#111713")))
        self.table.setItem(row, COL_NUM, item)
        self.table.setSpan(row, COL_NUM, 1, len(COLUMNS))

    def _refresh_group_rows(
            self, warnings: dict[str, list[str]]) -> None:
        clips_by_id = {clip.id: clip for clip in self._clips}
        for row, group_id in self._group_rows.items():
            members = [
                clips_by_id[clip_id]
                for clip_id in self._group_members.get(group_id, [])
                if clip_id in clips_by_id
            ]
            quarter = group_id.rsplit(":", 1)[0]
            self._write_group_row(
                row, group_id, quarter, members, warnings)

    @staticmethod
    def _sidebar_title(
        clip: Clip,
        title_text: str,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
    ) -> str:
        """Build the compact ledger copy: who and what, then the situation.

        The headline carries the two facts a row is scanned for - the player
        and what happened - so neither can be pushed off the end by a long
        generated name. The situation line is the play in football terms and
        is short enough that it does not elide.
        """
        start = clip.start_ms if start_ms is None else start_ms
        end = clip.end_ms if end_ms is None else end_ms
        details = clip.details
        player = details.get("player_name", "").strip()
        result = details.get("result", "").strip()

        if player and result:
            headline = f"{player}  ·  {result}"
        else:
            # Nothing logged yet, so the name is all there is to show.
            headline = player or result or title_text

        situation = " ".join(
            part for part in (
                details.get("quarter", "").strip(),
                details.get("down_distance", "").strip(),
                (f"on {details['ball_on'].strip()}"
                 if details.get("ball_on", "").strip() else ""),
            ) if part
        )
        kind = "  ·  ".join(
            part for part in (
                details.get("run_pass", "").strip(),
                details.get("play_type", "").strip(),
            ) if part
        )
        span = (
            f"{format_ms(start, show_millis=True)}"
            f" – {format_ms(end, show_millis=True)}"
            f"  ({max(0, end - start) / 1000:.1f}s)"
        )
        secondary = [part for part in (situation, kind, span) if part]
        return f"{headline}\n{'   ·   '.join(secondary)}"

    def _write_row(self, row: int, clip: Clip, clip_warnings: list[str],
                   template: str, project_name: str,
                   separator_style: str) -> None:
        """Populate a single table row for `clip`. Pulled out of set_clips so
        a single-clip metadata edit can rewrite just its row (see update_row)
        instead of rebuilding the entire list."""
        filename = filename_service.render_template(
            template, clip, project_name, separator_style) + ".mp4"
        title_text = clip.clip_title or "(unnamed)"
        if clip_warnings and not self._sidebar_mode:
            title_text = "⚠ " + title_text
        # In the sidebar the row already carries a NEEDS FIX badge, so a "!"
        # glued to the headline just eats characters saying the same thing.

        kind = classify_play_kind(
            clip.details, clip.tags, clip.clip_title, clip.label)
        code = PLAY_CODES.get(kind, "·")
        number_text = f"{clip.clip_number:02d}" if self._sidebar_mode \
            else f"{code} {clip.clip_number:02d}"
        number_item = self._set_item(
            row, COL_NUM, number_text, clip.id)
        play_color = PLAY_COLORS.get(kind, C_BLOCK)
        number_item.setData(PLAY_COLOR_ROLE, play_color.name())
        first_read_call = needs_first_read_call(clip)
        number_item.setData(FIRST_READ_CALL_ROLE, first_read_call)
        number_item.setForeground(QBrush(
            QColor("#ecf3ee") if self._sidebar_mode else play_color))
        number_tooltip = f"{PLAY_LABELS.get(kind, 'Unlabelled')} play"
        if first_read_call:
            number_tooltip += (
                "\nFirst Read is waiting for your run/pass call (R or P)")
        number_item.setToolTip(number_tooltip)
        number_font = number_item.font()
        number_font.setBold(True)
        number_item.setFont(number_font)
        self._set_item(row, COL_START, format_ms(clip.start_ms, show_millis=True))
        self._set_item(row, COL_END, format_ms(clip.end_ms, show_millis=True))
        self._set_item(row, COL_DUR, format_ms(clip.duration_ms, show_millis=True))
        display_title = title_text
        if self._sidebar_mode:
            display_title = self._sidebar_title(clip, title_text)
        title_item = self._set_item(row, COL_TITLE, display_title)
        title_tooltip = clip.clip_title or "(unnamed clip)"
        if clip_warnings:
            title_tooltip += "\n\n" + "\n".join(clip_warnings)
        title_item.setToolTip(title_tooltip)
        title_item.setData(
            Qt.ItemDataRole.AccessibleTextRole,
            clip.clip_title or "Unnamed clip",
        )
        file_item = self._set_item(row, COL_FILE, filename)
        file_item.setToolTip(filename)
        self._set_item(row, COL_LABEL, clip.label)
        tags_text = ", ".join(clip.tags)
        tags_item = self._set_item(row, COL_TAGS, tags_text)
        tags_item.setToolTip(tags_text)
        preset = BUILTIN_PRESETS.get(clip.export_preset)
        self._set_item(row, COL_PRESET,
                       preset.display_name if preset else "(project default)")
        review_kind, review_text, review_tooltip = review_status_for(
            clip, clip_warnings)
        if self._sidebar_mode:
            # A status is a word. "!" and "DET!" told you something was up
            # without saying what, and "OK" did not distinguish a logged play
            # from one nobody had looked at.
            review_text = {
                "unlogged": "UNLOGGED",
                "logged": "LOGGED",
                "needs_fix": "NEEDS FIX",
                "excluded": "EXCLUDED",
                "autodetect_pending": "PENDING",
                "autodetect_confirmed": "CHECKED",
                "autodetect_no_play": "NO PLAY",
                "autodetect_withdrawn": "WITHDRAWN",
            }[review_kind]
        review_item = self._set_item(row, COL_STATUS, review_text)
        review_item.setData(REVIEW_KIND_ROLE, review_kind)
        review_item.setData(REVIEW_TOOLTIP_ROLE, review_tooltip)
        review_item.setForeground(
            QBrush(QColor(REVIEW_STATUS[review_kind][1])))
        review_item.setToolTip(
            self._status_tooltip(review_tooltip, clip.export_status))
        enabled_item = QTableWidgetItem()
        enabled_item.setFlags(Qt.ItemFlag.ItemIsEnabled |
                              Qt.ItemFlag.ItemIsSelectable |
                              Qt.ItemFlag.ItemIsUserCheckable)
        enabled_item.setCheckState(
            Qt.CheckState.Checked if clip.enabled else Qt.CheckState.Unchecked)
        enabled_item.setToolTip("Include this clip in exports")
        self.table.setItem(row, COL_ENABLED, enabled_item)

    def update_row(self, clip: Clip, duration_ms: int, template: str,
                   project_name: str, separator_style: str) -> None:
        """Rewrite only the row for `clip` in place.

        Used after a metadata edit so the full list (and timeline blocks,
        vocabulary, export panel) is not rebuilt - the table keeps its
        selection and scroll position. No-ops if the clip isn't visible.
        """
        for existing in self._clips:
            if existing.id == clip.id:
                warnings = compute_warnings(self._clips, duration_ms, template,
                                            project_name, separator_style)
                self._warnings = warnings
                group_id = self._clip_groups.get(clip.id, "")
                if self._sidebar_mode and group_id and not group_id.startswith(
                        f"{quarter_key_for(clip)}:"):
                    self.set_clips(
                        self._clips, duration_ms, template, project_name,
                        separator_style)
                    return
                row = self._clip_rows.get(clip.id)
                if row is None:
                    return
                self.table.blockSignals(True)
                self._write_row(row, clip, warnings.get(clip.id, []), template,
                                project_name, separator_style)
                self._refresh_group_rows(warnings)
                self.table.blockSignals(False)
                self._apply_filter()
                self._update_count_label(warnings)
                return

    def preview_bounds(
            self, clip_id: str, start_ms: int, end_ms: int) -> None:
        """Preview trim times in one row without mutating its Clip model."""
        for clip in self._clips:
            if clip.id != clip_id:
                continue
            row = self._clip_rows.get(clip.id)
            if row is None:
                return
            values = {
                COL_START: format_ms(start_ms, show_millis=True),
                COL_END: format_ms(end_ms, show_millis=True),
                COL_DUR: format_ms(
                    max(0, end_ms - start_ms), show_millis=True),
            }
            for column, text in values.items():
                item = self.table.item(row, column)
                if item is not None:
                    item.setText(text)
                    item.setToolTip(
                        f"{text} (trim preview, release to save)")
            if self._sidebar_mode:
                title_item = self.table.item(row, COL_TITLE)
                if title_item is not None:
                    title_text = title_item.text().splitlines()[0]
                    title_item.setText(self._sidebar_title(
                        clip,
                        title_text,
                        start_ms=start_ms,
                        end_ms=end_ms,
                    ))
            return

    # Row heights for the two densities.
    ROW_THUMB = 52
    ROW_COMPACT = 22
    # Option 4's 340 px ledger shows sixteen two-line plays in the canonical
    # height. Forty-two pixels keeps both lines and the status readable while
    # matching that compact scan rhythm.
    ROW_SIDEBAR = 42
    ROW_GROUP = 27

    def set_compact(self, compact: bool) -> None:
        """List view tightens the rows so more clips fit on screen."""
        self.view_btn.blockSignals(True)
        self.view_btn.setChecked(compact)
        self.view_btn.blockSignals(False)
        self.view_btn.setText("Compact" if compact else "Comfortable")
        height = self.ROW_SIDEBAR if self._sidebar_mode else (
            self.ROW_COMPACT if compact else self.ROW_THUMB)
        self.table.verticalHeader().setDefaultSectionSize(height)
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(
                row, self.ROW_GROUP if row in self._group_rows else height)

    def _view_toggled(self, compact: bool) -> None:
        self.set_compact(compact)
        self.view_mode_changed.emit(compact)

    def set_sidebar_mode(self, enabled: bool) -> None:
        """Use a focused four-column ledger when the list is a side panel."""
        self._sidebar_mode = enabled
        self.heading_label.setText("CLIPS" if enabled else "Clips")
        visible = {COL_NUM, COL_TITLE, COL_STATUS}
        for column in range(len(COLUMNS)):
            self.table.setColumnHidden(column, enabled and column not in visible)
        self.view_btn.setVisible(not enabled)
        self.up_btn.setVisible(not enabled)
        self.down_btn.setVisible(not enabled)
        self.progress_label.setVisible(enabled)
        self.filter_buttons.setVisible(enabled)
        self.table.horizontalHeader().setVisible(not enabled)
        self.table.setShowGrid(not enabled)
        self.table.setAlternatingRowColors(not enabled)
        self.table.setWordWrap(enabled)
        self.filter_edit.setPlaceholderText(
            "Search plays..." if enabled
            else "Filter clips - name, tag, player, result…")
        self.previous_btn.setText(
            "‹ Previous" if enabled else "‹ Previous Clip")
        self.next_btn.setText("Next ›" if enabled else "Next Clip ›")
        if enabled:
            self.set_compact(True)
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(
                COL_NUM, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(
                COL_STATUS, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(COL_NUM, 46)
            # Full workflow words are evidence, not decoration. Reserve enough
            # room for UNLOGGED / NEEDS FIX instead of rendering UNLOGG….
            self.table.setColumnWidth(COL_STATUS, 78)

    def _set_filter_mode(
        self,
        mode: str,
        clip_ids: set[str] | None = None,
    ) -> None:
        if mode not in {
            "all", "unlogged", "needs_fix", "autodetect_pending", "ignored",
        }:
            mode = "all"
        self._filter_mode = mode
        self._filter_scope_ids = (
            set(clip_ids) if clip_ids is not None else None)
        buttons = {
            "all": self.all_filter_btn,
            "unlogged": self.unlogged_filter_btn,
            "needs_fix": self.needs_fix_filter_btn,
            "autodetect_pending": self.detect_pending_filter_btn,
            "ignored": self.ignored_filter_btn,
        }
        for key, button in buttons.items():
            button.blockSignals(True)
            button.setChecked(key == mode)
            button.blockSignals(False)
        self._apply_filter()
        self.review_filter_changed.emit(mode)

    @property
    def review_filter_mode(self) -> str:
        return self._filter_mode

    def show_review_filter(
        self,
        mode: str,
        *,
        clip_ids: set[str] | None = None,
    ) -> None:
        """Expose a review filter to the workspace without button internals."""
        self.filter_edit.clear()
        self._set_filter_mode(mode, clip_ids)

    def update_review_filter_scope(self, clip_ids: set[str]) -> None:
        """Refresh a live scoped filter after split/delete/undo changes."""
        self._filter_scope_ids = set(clip_ids)
        self._apply_filter()

    def set_navigation_scope(
        self,
        clip_ids: list[str] | None,
    ) -> None:
        """Limit Previous/Next button boundaries to an ordered clip subset."""
        self._navigation_scope_ids = (
            list(clip_ids) if clip_ids is not None else None)
        self._update_navigation_buttons()

    def _apply_filter(self) -> None:
        """Hide rows that don't match - all terms must appear somewhere."""
        terms = [t for t in self.filter_edit.text().lower().split() if t]
        shown = 0
        matches: dict[str, bool] = {}
        for clip in self._clips:
            haystack = " ".join([
                clip.clip_title, clip.label, " ".join(clip.tags),
                " ".join(clip.details.values()), clip.notes,
            ]).lower()
            warnings = self._warnings.get(clip.id, [])
            review_kind = review_status_for(clip, warnings)[0]
            ignored = is_ignored_autodetect_clip(clip)
            if self._filter_mode == "ignored":
                status_match = ignored
            elif self._filter_mode == "needs_fix":
                status_match = bool(warnings)
            elif self._filter_mode == "autodetect_pending":
                status_match = (
                    self._filter_scope_ids is not None
                    or autodetect_review_status_for(clip)
                    == "autodetect_pending"
                )
            elif self._filter_mode == "unlogged":
                status_match = needs_logging(clip)
            else:
                status_match = (
                    self._filter_mode == "all"
                    or review_kind == self._filter_mode
                )
            if self._filter_mode != "ignored" and ignored:
                status_match = False
            mode_match = (
                status_match
                and (
                    self._filter_scope_ids is None
                    or clip.id in self._filter_scope_ids
                )
            )
            match = mode_match and all(term in haystack for term in terms)
            matches[clip.id] = match
            row = self._clip_rows.get(clip.id)
            if row is None:
                continue
            group_id = self._clip_groups.get(clip.id, "")
            collapsed = group_id in self._collapsed_groups
            self.table.setRowHidden(row, not match or collapsed)
            shown += match
        for group_row, group_id in self._group_rows.items():
            group_match = any(
                matches.get(clip_id, False)
                for clip_id in self._group_members.get(group_id, []))
            self.table.setRowHidden(group_row, not group_match)
        self._refresh_group_rows(self._warnings)
        if terms or self._filter_mode != "all":
            total = sum(
                is_ignored_autodetect_clip(clip)
                == (self._filter_mode == "ignored")
                for clip in self._clips
            )
            self.filter_count.setText(f"{shown} of {total}")
        else:
            self.filter_count.setText("")

    #: Timestamps read far better right-aligned in a tabular font.
    _TIME_COLUMNS = (COL_START, COL_END, COL_DUR)

    def _set_item(self, row: int, col: int, text: str,
                  clip_id: str | None = None) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if clip_id:
            item.setData(Qt.ItemDataRole.UserRole, clip_id)
        if col in self._TIME_COLUMNS:
            item.setTextAlignment(Qt.AlignmentFlag.AlignRight |
                                  Qt.AlignmentFlag.AlignVCenter)
            font = item.font()
            font.setStyleHint(font.StyleHint.TypeWriter)
            font.setFamily("Consolas")
            item.setFont(font)
        self.table.setItem(row, col, item)
        return item

    def update_status(self, clip_id: str, status: ExportStatus) -> None:
        for clip in self._clips:
            if clip.id == clip_id:
                clip.export_status = status
                row = self._clip_rows.get(clip.id)
                if row is None:
                    return
                item = self.table.item(row, COL_STATUS)
                if item:
                    review_tooltip = item.data(REVIEW_TOOLTIP_ROLE) or ""
                    item.setToolTip(
                        self._status_tooltip(review_tooltip, status))
                return

    @staticmethod
    def _status_tooltip(
            review_tooltip: str, export_status: ExportStatus) -> str:
        parts = [review_tooltip] if review_tooltip else []
        if export_status != ExportStatus.NOT_EXPORTED:
            parts.append(
                f"Export: {STATUS_TEXT.get(export_status, export_status.value)}")
        return "\n".join(parts)

    def _update_count_label(
            self, warnings: dict[str, list[str]]) -> None:
        normal_clips = [
            clip for clip in self._clips
            if not is_ignored_autodetect_clip(clip)
        ]
        ignored_rows = len(self._clips) - len(normal_clips)
        unlogged = sum(
            1 for clip in normal_clips
            if clip.enabled and needs_logging(clip))
        needs_fix = sum(
            1 for clip in normal_clips
            if clip.enabled and warnings.get(clip.id))
        logged = sum(
            1 for clip in normal_clips
            if clip.enabled and not needs_logging(clip))
        first_read_calls = unresolved_count(
            clip for clip in normal_clips if clip.enabled)
        detect_pending = sum(
            autodetect_review_status_for(clip) == "autodetect_pending"
            for clip in normal_clips
        )
        text = f"{len(normal_clips)} plays" if self._sidebar_mode \
            else f"{len(normal_clips)} clips"
        if not self._sidebar_mode:
            if unlogged:
                text += f", {unlogged} to log"
            if needs_fix:
                text += f", {needs_fix} need fixes"
        self.count_label.setText(text)
        if self._sidebar_mode:
            progress = f"{logged} logged"
            if first_read_calls:
                suffix = "call" if first_read_calls == 1 else "calls"
                progress += f"  •  {first_read_calls} First Read {suffix}"
            if detect_pending:
                progress += f"  •  {detect_pending} detect pending"
            if needs_fix:
                progress += f"  •  {needs_fix} needs fix"
            if self._ignored_fragment_count:
                progress += (
                    f"  •  {self._ignored_fragment_count} fragments hidden")
            self.progress_label.setText(progress)
        enabled = sum(1 for clip in normal_clips if clip.enabled)
        tooltip = f"{enabled} enabled for export"
        if needs_fix:
            tooltip += f"\n{needs_fix} need fixes"
        if detect_pending:
            tooltip += f"\n{detect_pending} autodetect corrections pending"
        if first_read_calls:
            tooltip += f"\n{first_read_calls} First Read calls still need R or P"
        if self._ignored_fragment_count:
            tooltip += (
                f"\n{self._ignored_fragment_count} unclassified source "
                f"fragments hidden ({self._ignored_fragment_ms / 1000:.1f}s)")
        if ignored_rows:
            tooltip += (
                f"\n{ignored_rows} explicitly ignored clip row"
                f"{'s' if ignored_rows != 1 else ''}; use the Ignored filter")
        self.count_label.setToolTip(tooltip)
        self.ignored_filter_btn.setText(
            f"Ignored {ignored_rows}" if ignored_rows else "Ignored")
        self.ignored_filter_btn.setVisible(
            self._sidebar_mode and bool(ignored_rows))

    def set_ignored_fragment_summary(
            self, count: int, duration_ms: int) -> None:
        """Show preserved detector fragments without adding list rows."""
        self._ignored_fragment_count = max(0, int(count))
        self._ignored_fragment_ms = max(0, int(duration_ms))
        if hasattr(self, "_warnings"):
            self._update_count_label(self._warnings)

    # ---------- selection / actions ----------

    def selected_clip_ids(self) -> list[str]:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        ids = []
        for row in rows:
            item = self.table.item(row, COL_NUM)
            if item:
                clip_id = item.data(Qt.ItemDataRole.UserRole)
                if clip_id:
                    ids.append(clip_id)
        return ids

    def selected_rows(self) -> list[int]:
        return sorted({
            self._row_to_clip_index[index.row()]
            for index in self.table.selectedIndexes()
            if index.row() in self._row_to_clip_index
        })

    def select_clip_id(self, clip_id: str, reveal: bool = True) -> bool:
        """Select one clip by identity and ensure the row is actually visible."""
        row = self._clip_rows.get(clip_id, -1)
        if row < 0:
            return False
        if reveal and self.table.isRowHidden(row):
            self.filter_edit.clear()
            self._set_filter_mode("all")
            group_id = self._clip_groups.get(clip_id)
            if group_id:
                self._collapsed_groups.discard(group_id)
            self._apply_filter()
        # Explicit navigation selects one play even while Ctrl/Shift is held.
        # selectRow() otherwise treats a keyboard command as a range selection.
        self.table.selectionModel().setCurrentIndex(
            self.table.model().index(row, COL_NUM),
            QItemSelectionModel.SelectionFlag.ClearAndSelect |
            QItemSelectionModel.SelectionFlag.Rows)
        item = self.table.item(row, COL_NUM)
        if item is not None:
            self.table.scrollToItem(item)
        return True

    def select_clip_index(self, clip_index: int, reveal: bool = True) -> bool:
        if not 0 <= clip_index < len(self._clips):
            return False
        return self.select_clip_id(
            self._clips[clip_index].id, reveal=reveal)

    def _emit_selection(self) -> None:
        self._update_navigation_buttons()
        self.selection_changed.emit(self.selected_clip_ids())

    def _update_navigation_buttons(self) -> None:
        """Keep the visible navigation controls honest at list boundaries."""
        if self._navigation_scope_ids is not None:
            selected = self.selected_clip_ids()
            if not self._navigation_scope_ids:
                self.previous_btn.setEnabled(False)
                self.next_btn.setEnabled(False)
            elif len(selected) != 1 or \
                    selected[0] not in self._navigation_scope_ids:
                self.previous_btn.setEnabled(False)
                self.next_btn.setEnabled(True)
            else:
                position = self._navigation_scope_ids.index(selected[0])
                self.previous_btn.setEnabled(position > 0)
                self.next_btn.setEnabled(
                    position < len(self._navigation_scope_ids) - 1)
            return
        rows = self.selected_rows()
        if not self._clips:
            self.previous_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
        elif not rows:
            self.previous_btn.setEnabled(False)
            self.next_btn.setEnabled(True)
        elif len(rows) == 1:
            self.previous_btn.setEnabled(rows[0] > 0)
            self.next_btn.setEnabled(rows[0] < len(self._clips) - 1)
        else:
            self.previous_btn.setEnabled(False)
            self.next_btn.setEnabled(False)

    def _double_clicked(self, item: QTableWidgetItem) -> None:
        num_item = self.table.item(item.row(), COL_NUM)
        if num_item:
            clip_id = num_item.data(Qt.ItemDataRole.UserRole)
            if clip_id:
                self.preview_requested.emit(clip_id)

    def _cell_clicked(self, row: int, _column: int) -> None:
        group_id = self._group_rows.get(row)
        if not group_id:
            return
        if group_id in self._collapsed_groups:
            self._collapsed_groups.remove(group_id)
        else:
            self._collapsed_groups.add(group_id)
        self.table.blockSignals(True)
        self._refresh_group_rows(self._warnings)
        self.table.blockSignals(False)
        self.set_compact(self.view_btn.isChecked())
        self._apply_filter()

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() != COL_ENABLED:
            return
        num_item = self.table.item(item.row(), COL_NUM)
        if num_item:
            clip_id = num_item.data(Qt.ItemDataRole.UserRole)
            if clip_id:
                self.toggle_enabled_requested.emit(
                    clip_id, item.checkState() == Qt.CheckState.Checked)

    def _move_selected(self, delta: int) -> None:
        rows = self.selected_rows()
        if len(rows) != 1:
            return
        target = rows[0] + delta
        if 0 <= target < len(self._clips):
            self.reorder_requested.emit(rows[0], target)
            self.select_clip_index(target)

    def _context_menu(self, pos) -> None:
        clicked_index = self.table.indexAt(pos)
        if not clicked_index.isValid():
            return
        clicked_item = self.table.item(clicked_index.row(), COL_NUM)
        clicked_id = (
            clicked_item.data(Qt.ItemDataRole.UserRole)
            if clicked_item is not None else None
        )
        if not clicked_id:
            return
        if clicked_id not in self.selected_clip_ids():
            self.select_clip_id(str(clicked_id), reveal=False)
        ids = self.selected_clip_ids()
        if not ids:
            return
        menu = QMenu(self)
        preview_action = menu.addAction("Preview clip")
        duplicate_action = menu.addAction("Duplicate (Ctrl+D)")
        duplicate_action.setToolTip(
            "New version of this clip - same range, own name and metadata.")
        bulk_action = menu.addAction(f"Edit {len(ids)} clips…")
        bulk_action.setEnabled(len(ids) > 1)
        bulk_action.setToolTip(
            "Apply a tag or detail to every selected clip at once.")
        detected_ids = [
            clip.id for clip in self._clips
            if clip.id in ids and clip.detection_lineage
        ]
        reviewed_action = menu.addAction(
            f"Mark {len(detected_ids)} detected clip"
            f"{'s' if len(detected_ids) != 1 else ''} reviewed as real "
            f"play{'s' if len(detected_ids) != 1 else ''}")
        reviewed_action.setEnabled(bool(detected_ids))
        reviewed_action.setToolTip(
            "Confirm these corrected automatic boundaries as real plays. "
            "OFF clips will ask to be included again.")
        detector_prediction_ids = [
            clip.id for clip in self._clips
            if clip.id in ids
            and clip.detection_lineage.get("candidate_ids")
            and not str(
                clip.detection_lineage.get("derivation", "")
            ).startswith("duplicate")
        ]
        false_positive_action = menu.addAction(
            f"Confirm {len(detector_prediction_ids)} as not a play and exclude")
        false_positive_action.setEnabled(bool(detector_prediction_ids))
        false_positive_action.setToolTip(
            "Record an explicit not-a-play decision, then exclude the "
            "selected detector sections from export.")
        manual_ids = [
            clip.id for clip in self._clips
            if clip.id in ids and not clip.detection_lineage
        ]
        missed_action = menu.addAction(
            f"Mark {len(manual_ids)} as missed autodetect play"
            f"{'s' if len(manual_ids) != 1 else ''}")
        missed_action.setEnabled(bool(manual_ids))
        missed_action.setToolTip(
            "Attach ordinary clips as real plays missed inside the active "
            "short test batch.")
        move_up_action = menu.addAction("Move up")
        move_down_action = menu.addAction("Move down")
        one_row = len(self.selected_rows()) == 1
        move_up_action.setEnabled(one_row and self.selected_rows()[0] > 0)
        move_down_action.setEnabled(
            one_row and self.selected_rows()[0] < len(self._clips) - 1)
        menu.addSeparator()
        reel_on = menu.addAction("Include in combined reel")
        reel_off = menu.addAction("Exclude from combined reel")
        menu.addSeparator()
        delete_action = menu.addAction(f"Delete {len(ids)} clip(s)")
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        if action == preview_action:
            self.preview_requested.emit(ids[0])
        elif action == duplicate_action:
            self.duplicate_requested.emit(ids[0])
        elif action == bulk_action:
            self.bulk_edit_requested.emit(ids)
        elif action == reviewed_action:
            self.mark_reviewed_requested.emit(detected_ids)
        elif action == false_positive_action:
            self.confirm_false_positive_requested.emit(
                detector_prediction_ids)
        elif action == missed_action:
            self.mark_missed_requested.emit(manual_ids)
        elif action == move_up_action:
            self._move_selected(-1)
        elif action == move_down_action:
            self._move_selected(1)
        elif action == reel_on:
            for cid in ids:
                self.toggle_reel_requested.emit(cid, True)
        elif action == reel_off:
            for cid in ids:
                self.toggle_reel_requested.emit(cid, False)
        elif action == delete_action:
            self.delete_requested.emit(ids)
