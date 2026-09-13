"""Library Search: find, preview, and export clips across every project.

Layout: a filter bar on top, result rows on the left (~65%), and a selected-
clip preview panel on the right (~35%, collapsible). Only ACTIVE filters are
shown as chips; the full tag list lives behind a Browse Tags dropdown.

Queries the central catalog only - never the project files - so it is fast
and works with projects closed. Nothing here runs on the transport path.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta
from pathlib import Path

import shiboken6

from PySide6.QtCore import QSize, Qt, QThread, QTimer, QUrl, Signal, QSignalBlocker
from PySide6.QtGui import (
    QDesktopServices, QKeySequence, QPixmap, QShortcut, QTextDocument,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFileDialog, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPlainTextEdit, QProgressDialog, QPushButton, QSplitter, QStyle,
    QStackedWidget, QStyledItemDelegate, QToolButton, QVBoxLayout, QWidget,
)

from tapesift.core.config import AppSettings
from tapesift.models.clip import Clip
from tapesift.models.export_settings import get_preset
from tapesift.models.project import Project
from tapesift.services import (
    export_service, filename_service, library_service, tag_service,
)
from tapesift.services.detail_service import DETAIL_FIELDS
from tapesift.services.library_service import LibraryRow
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_core.collapsible import CollapsibleSection
from tapesift.ui_core.flow_layout import FlowLayout
from tapesift.ui_core.led_wordmark import wordmark_pixmap
from tapesift.ui_core.minimal_jog_ring import (
    DEFAULT_FRAME_MS, CompactJogRing, format_jog_timecode)
from tapesift.ui_core.tag_edit import (
    DetailEdit, TagLineEdit, configure_detail_edit,
)
from tapesift.ui_v2.icon_utils import tinted_standard_icon

log = logging.getLogger(__name__)

ROW_HEIGHT = 88
THUMB_SIZE = QSize(128, 72)
HIGHLIGHT_STYLE = "background-color:#3d3d50;color:#eef0f4;border-radius:2px;"

DATE_FILTERS = [("Any time", None), ("Today", 1), ("This week", 7),
                ("This month", 31)]
DURATION_FILTERS = [("Any length", None), ("Under 5s", (0, 5)),
                    ("5–15s", (5, 15)), ("Over 15s", (15, 10**9))]
SORTS = ["Relevance", "Recently Modified", "Clip Name", "Project Name",
         "Source Time", "Duration"]


def _highlight(text: str, terms: list[str]) -> str:
    """HTML-escape text and wrap matched terms in a restrained highlight."""
    escaped = html.escape(text)
    lowered = escaped.lower()
    if not terms:
        return escaped
    spans: list[tuple[int, int]] = []
    for term in terms:
        t = html.escape(term).lower()
        start = 0
        while t and (idx := lowered.find(t, start)) >= 0:
            spans.append((idx, idx + len(t)))
            start = idx + len(t)
    if not spans:
        return escaped
    spans.sort()
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, prev = [], 0
    for s, e in merged:
        out.append(escaped[prev:s])
        out.append(f'<span style="{HIGHLIGHT_STYLE}">{escaped[s:e]}</span>')
        prev = e
    out.append(escaped[prev:])
    return "".join(out)


class ResultRowDelegate(QStyledItemDelegate):
    """Three-line result row with thumbnail and match highlighting."""

    HTML_ROLE = Qt.ItemDataRole.UserRole + 1

    def paint(self, painter, option, index) -> None:
        painter.save()
        rect = option.rect
        selected = option.state & QStyle.StateFlag.State_Selected
        if selected:
            painter.fillRect(rect, option.palette.highlight())
        elif index.row() % 2:
            painter.fillRect(rect, option.palette.alternateBase())
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        x = rect.left() + 8
        if icon is not None and not icon.isNull():
            pix = icon.pixmap(THUMB_SIZE)
            painter.drawPixmap(x, rect.top() + (rect.height() -
                                                pix.height()) // 2, pix)
        x += THUMB_SIZE.width() + 12
        doc = QTextDocument()
        doc.setDefaultStyleSheet("body { color:#d8d8de; }")
        doc.setHtml(index.data(self.HTML_ROLE) or "")
        doc.setTextWidth(rect.width() - (x - rect.left()) - 8)
        painter.translate(x, rect.top() + 6)
        doc.drawContents(painter)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, ROW_HEIGHT)


class ReelWorker(QThread):
    """Builds one video from clips spanning several games."""

    progress = Signal(float)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, ffmpeg_path: str, rows: list[LibraryRow],
                 output_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.rows = rows
        self.output_path = output_path
        self.runner = export_service.FFmpegRunner()

    def cancel(self) -> None:
        self.runner.cancel_event.set()

    def run(self) -> None:
        try:
            items = [
                (Path(row.source_video_path),
                 Clip(start_ms=row.start_ms, end_ms=row.end_ms,
                      clip_title=row.clip_title, clip_number=row.clip_number))
                for row in self.rows
            ]
            export_service.export_multi_source_reel(
                self.ffmpeg_path, items, self.output_path,
                get_preset("source_quality"), self.runner,
                self.progress.emit)
            self.finished_ok.emit(str(self.output_path))
        except Exception as exc:
            log.exception("Reel build failed")
            self.failed.emit(str(exc))


class LibraryExportWorker(QThread):
    """Exports library search results to a folder, one clip at a time."""

    progress = Signal(int, int, str)   # done, total, current name
    finished_ok = Signal(int, int)     # exported, failed

    def __init__(self, ffmpeg_path: str, rows: list[LibraryRow], out_dir: Path,
                 separator: str, parent=None) -> None:
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path
        self.rows = rows
        self.out_dir = out_dir
        self.separator = separator
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        preset = get_preset("source_quality")
        exported = failed = 0
        for index, row in enumerate(self.rows, start=1):
            if self._cancel:
                break
            name = row.clip_title or f"Clip_{row.clip_number:03d}"
            self.progress.emit(index, len(self.rows), name)
            try:
                project = Project(name=row.project_name,
                                  source_video_path=row.source_video_path)
                clip = Clip(start_ms=row.start_ms, end_ms=row.end_ms,
                            clip_title=row.clip_title, clip_number=row.clip_number)
                base = filename_service.effective_base(clip, self.separator)
                out = filename_service.unique_path(self.out_dir, base, ".mp4")
                export_service.export_clip(
                    self.ffmpeg_path, project, clip, out, preset, True,
                    export_service.FFmpegRunner())
                exported += 1
            except Exception:
                log.exception("Library export failed for %s", row.clip_uid)
                failed += 1
        self.finished_ok.emit(exported, failed)


class LibrarySearchScreen(QWidget):
    back_requested = Signal()
    open_clip_requested = Signal(str, str)   # project_path, clip_id

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._active_tags: set[str] = set()
        self._results: list[LibraryRow] = []
        self._export_worker: LibraryExportWorker | None = None
        self._editing_row: LibraryRow | None = None
        self._inline_preview_row: LibraryRow | None = None
        # Set by MainWindow: (project_path, clip_id, changes) -> (ok, error).
        # Edits go through it so an open project's session stays consistent.
        self.apply_edit = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(8)

        # ---- header ----
        header = QHBoxLayout()
        back_btn = QPushButton("‹ Back")
        back_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        back_btn.clicked.connect(self.back_requested.emit)
        # The scoreboard wordmark, centred - the Library is its own place in
        # the app, not a sub-page of the workspace.
        title = QLabel()
        title.setPixmap(wordmark_pixmap("LIBRARY", dot=4, scheme="green"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rebuild_btn = QPushButton("Rebuild Index")
        rebuild_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        rebuild_btn.setToolTip("Rescan your recent projects to refresh the "
                               "search index.")
        rebuild_btn.clicked.connect(self._rebuild_index)
        header.addWidget(back_btn, 0, Qt.AlignmentFlag.AlignTop)
        header.addStretch()
        header.addWidget(title)
        header.addStretch()
        header.addWidget(rebuild_btn, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)

        blurb = QLabel(
            "Every clip you've ever cut, across every film. Search by player, "
            "opponent, tag or play type - then preview it, fix its details, "
            "or send a set straight to a reel.")
        blurb.setProperty("role", "subtle")
        blurb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        blurb.setWordWrap(True)
        outer.addWidget(blurb)

        self.stats_label = QLabel("")
        self.stats_label.setProperty("role", "subtle")
        self.stats_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.stats_label)
        outer.addSpacing(6)

        # ---- filter bar ----
        bar = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(
            "Search clips…  (Ctrl+F)  - name, opponent, tag, player, game, notes")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._schedule_search)
        self.search_box.setMinimumHeight(32)
        bar.addWidget(self.search_box, 1)

        self.project_combo = QComboBox()
        self.project_combo.setToolTip("Limit results to one project")
        self.project_combo.currentIndexChanged.connect(self._run_search)
        bar.addWidget(self.project_combo)

        self.tags_button = QToolButton()
        self.tags_button.setText("Tags ▾")
        self.tags_button.setToolTip("Browse every tag in the library")
        self.tags_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tags_menu = QMenu(self.tags_button)
        self.tags_button.setMenu(self.tags_menu)
        bar.addWidget(self.tags_button)

        self.opponent_combo = QComboBox()
        self.opponent_combo.setToolTip("Limit results to one opponent")
        self.opponent_combo.currentIndexChanged.connect(self._run_search)
        bar.addWidget(self.opponent_combo)

        self.player_combo = QComboBox()
        self.player_combo.setToolTip("Limit results to one player")
        self.player_combo.currentIndexChanged.connect(self._run_search)
        bar.addWidget(self.player_combo)

        self.role_combo = QComboBox()
        self.role_combo.addItem("Any role", False)
        self.role_combo.addItem("Highlighted only", True)
        self.role_combo.setToolTip(
            "Any role: every clip the player appears in.\n"
            "Highlighted only: clips where they are the subject.")
        self.role_combo.currentIndexChanged.connect(self._run_search)
        bar.addWidget(self.role_combo)

        self.date_combo = QComboBox()
        for label, days in DATE_FILTERS:
            self.date_combo.addItem(label, days)
        self.date_combo.currentIndexChanged.connect(self._run_search)
        bar.addWidget(self.date_combo)

        self.duration_combo = QComboBox()
        for label, rng in DURATION_FILTERS:
            self.duration_combo.addItem(label, rng)
        self.duration_combo.currentIndexChanged.connect(self._run_search)
        bar.addWidget(self.duration_combo)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(SORTS)
        self.sort_combo.setToolTip(
            "Relevance applies while searching; with an empty search it "
            "falls back to Recently Modified.")
        self.sort_combo.currentIndexChanged.connect(self._run_search)
        bar.addWidget(self.sort_combo)
        outer.addLayout(bar)

        # ---- active filter chips (only what's active, never the whole wall) ----
        chips_host = QWidget()
        self._chips_layout = FlowLayout(chips_host, spacing=6)
        outer.addWidget(chips_host)

        # ---- results | preview split ----
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)

        self.results_list = QListWidget()
        self.results_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self.results_list.setItemDelegate(ResultRowDelegate(self.results_list))
        self.results_list.itemDoubleClicked.connect(
            self._preview_double_clicked)
        self.results_list.itemSelectionChanged.connect(self._selection_changed)
        left_box.addWidget(self.results_list, 1)

        self.empty_label = QLabel("")
        self.empty_label.setProperty("role", "subtle")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.hide()
        left_box.addWidget(self.empty_label, 1)
        split.addWidget(left)

        split.addWidget(self._build_preview_panel())
        split.setStretchFactor(0, 65)
        split.setStretchFactor(1, 35)
        outer.addWidget(split, 1)

        # ---- selection bar ----
        footer = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setProperty("role", "subtle")
        self.clear_sel_btn = QPushButton("Clear selection")
        self.clear_sel_btn.clicked.connect(self.results_list.clearSelection)
        self.open_btn = QPushButton("Open in TapeSift")
        self.open_btn.setToolTip(
            "Open the clip's project and jump to it (Enter)")
        self.open_btn.clicked.connect(self._open_selected)
        self.reel_btn = QPushButton("Build Reel…")
        self.reel_btn.setToolTip(
            "Join the selected clips into one video, in the order shown - a "
            "highlight tape from across every game.")
        self.reel_btn.clicked.connect(self._build_reel)
        self.export_btn = QPushButton("Export selected…")
        self.export_btn.setProperty("accent", "true")
        self.export_btn.setToolTip("Export as individual MP4s (Ctrl+E)")
        self.export_btn.clicked.connect(self._export_selected)
        footer.addWidget(self.count_label)
        footer.addStretch()
        footer.addWidget(self.clear_sel_btn)
        footer.addWidget(self.open_btn)
        footer.addWidget(self.reel_btn)
        footer.addWidget(self.export_btn)
        outer.addLayout(footer)

        # ---- keyboard ----
        QShortcut(QKeySequence("Ctrl+F"), self,
                  activated=lambda: self.search_box.setFocus())
        QShortcut(QKeySequence("/"), self, activated=self._focus_search_slash)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self._export_selected)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self,
                  activated=self._clear_or_back)
        QShortcut(QKeySequence(Qt.Key.Key_Space), self,
                  activated=self._space_preview)
        QShortcut(QKeySequence(Qt.Key.Key_Return), self,
                  activated=self._enter_open)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._run_search)

    # ---------- preview panel ----------

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        box = QVBoxLayout(panel)
        box.setContentsMargins(12, 0, 0, 0)

        head = QHBoxLayout()
        head_label = QLabel("Selected Clip")
        head_label.setProperty("role", "heading")
        self.collapse_btn = QToolButton()
        self.collapse_btn.setText("×")
        self.collapse_btn.setToolTip("Hide the preview panel")
        self.collapse_btn.clicked.connect(lambda: panel.setVisible(False))
        head.addWidget(head_label)
        head.addStretch()
        head.addWidget(self.collapse_btn)
        box.addLayout(head)

        self.preview_thumb = QStackedWidget()
        self.preview_thumb.setObjectName("LibraryPreviewSurface")
        self.preview_thumb.setMinimumHeight(160)
        self.preview_thumb.setStyleSheet(
            "background-color:#141416; border:1px solid #2c2c30; "
            "border-radius:4px;")
        self.preview_placeholder = QLabel()
        self.preview_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_placeholder.setStyleSheet(
            "background-color:#141416; border:0; border-radius:4px;")
        self.preview_video = QVideoWidget()
        self.preview_video.setAspectRatioMode(
            Qt.AspectRatioMode.KeepAspectRatio)
        self.preview_video.setStyleSheet(
            "background-color:#050505; border:0; border-radius:4px;")
        self.preview_thumb.addWidget(self.preview_placeholder)
        self.preview_thumb.addWidget(self.preview_video)
        self.preview_player = QMediaPlayer(self)
        self.preview_audio = QAudioOutput(self)
        self.preview_audio.setVolume(self.settings.volume / 100)
        self.preview_player.setAudioOutput(self.preview_audio)
        self.preview_player.setVideoOutput(self.preview_video)
        self.preview_player.positionChanged.connect(
            self._inline_preview_position_changed)
        self.preview_player.mediaStatusChanged.connect(
            self._inline_preview_media_status)
        box.addWidget(self.preview_thumb)

        # Everything here is editable and writes back to the project file.
        self.preview_title = QLineEdit()
        self.preview_title.setPlaceholderText("Clip title")
        self.preview_title.setStyleSheet("font-size:15px; font-weight:600;")
        box.addWidget(self.preview_title)

        self.preview_meta = QLabel("")
        self.preview_meta.setProperty("role", "subtle")
        self.preview_meta.setWordWrap(True)
        box.addWidget(self.preview_meta)

        self.preview_tags = TagLineEdit()
        self.preview_tags.setPlaceholderText("tags, comma, separated")
        box.addWidget(self.preview_tags)

        self.details_section = CollapsibleSection("Play details")
        grid = QGridLayout()
        self.preview_details: dict[str, DetailEdit] = {}
        for index, (key, label_text) in enumerate(DETAIL_FIELDS):
            row, col = divmod(index, 2)
            cell = QVBoxLayout()
            small = QLabel(label_text)
            small.setProperty("role", "subtle")
            edit = DetailEdit()
            configure_detail_edit(edit, key, self.settings)
            self.preview_details[key] = edit
            cell.addWidget(small)
            cell.addWidget(edit)
            grid.addLayout(cell, row, col)
        self.details_section.add_layout(grid)
        box.addWidget(self.details_section)

        self.preview_notes = QPlainTextEdit()
        self.preview_notes.setPlaceholderText("Notes")
        self.preview_notes.setMaximumHeight(64)
        box.addWidget(self.preview_notes)

        self.save_btn = QPushButton("Save Changes")
        self.save_btn.setProperty("accent", "true")
        self.save_btn.setToolTip(
            "Write these edits into the clip's project file and update the "
            "library index.")
        self.save_btn.clicked.connect(self._save_edit)
        box.addWidget(self.save_btn)
        box.addStretch()

        actions = QHBoxLayout()
        self.preview_rewind_btn = self._preview_transport_button(
            QStyle.StandardPixmap.SP_MediaSeekBackward,
            "Rewind 5 seconds", self._rewind_preview,
            "LibraryPreviewRewind")
        self.preview_play_btn = self._preview_transport_button(
            QStyle.StandardPixmap.SP_MediaPlay,
            "Play preview (Space)", self._space_preview,
            "LibraryPreviewPlayPause")
        self.preview_fast_forward_btn = self._preview_transport_button(
            QStyle.StandardPixmap.SP_MediaSeekForward,
            "Fast-forward 5 seconds", self._fast_forward_preview,
            "LibraryPreviewFastForward")
        self.preview_rewind_btn.setProperty("mediaControl", "true")
        self.preview_play_btn.setProperty("mediaControl", "true")
        self.preview_fast_forward_btn.setProperty("mediaControl", "true")
        # Same dial as the review transport, at preview scale, so frame
        # stepping works the same way on both screens.
        self.preview_jog_ring = CompactJogRing(self.preview_play_btn)
        self.preview_jog_ring.framesRequested.connect(
            self._jog_inline_preview)
        source_btn = QPushButton("Show Source")
        source_btn.setToolTip("Open the source video's folder in Explorer")
        source_btn.clicked.connect(self._show_source)
        actions.addStretch()
        actions.addWidget(
            self.preview_rewind_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        actions.addSpacing(3)
        actions.addWidget(
            self.preview_jog_ring, 0, Qt.AlignmentFlag.AlignVCenter)
        actions.addSpacing(3)
        actions.addWidget(
            self.preview_fast_forward_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        actions.addStretch()
        actions.addWidget(source_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        box.addLayout(actions)

        self._preview_panel = panel
        self._show_preview(None)
        return panel

    def _preview_transport_button(
            self, standard_pixmap: QStyle.StandardPixmap,
            accessible_name: str, slot, object_name: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName(object_name)
        button.setIcon(tinted_standard_icon(
            self, standard_pixmap, size=QSize(18, 18),
            glyph_size=QSize(15, 15)))
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(40, 34)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAccessibleName(accessible_name)
        button.setToolTip(accessible_name)
        button.clicked.connect(slot)
        return button

    def _seek_inline_preview(self, delta_ms: int) -> None:
        row = self._inline_preview_row
        if row is None:
            rows = self._selected_rows()
            if len(rows) != 1:
                return
            self._start_inline_preview(rows[0])
            row = self._inline_preview_row
        if row is None:
            return
        position = self.preview_player.position()
        end = row.end_ms if row.end_ms > row.start_ms else position + delta_ms
        target = max(row.start_ms, min(end, position + delta_ms))
        self.preview_player.setPosition(target)

    def _jog_inline_preview(self, frames: int) -> None:
        """Route dial input through the preview's existing clamped seek."""
        if not frames:
            return
        self.preview_player.pause()
        self._set_preview_play_state(False)
        self._seek_inline_preview(round(frames * DEFAULT_FRAME_MS))

    def _rewind_preview(self) -> None:
        self._seek_inline_preview(-5_000)

    def _fast_forward_preview(self) -> None:
        self._seek_inline_preview(5_000)

    def _set_preview_play_state(
            self, playing: bool, *, replay: bool = False) -> None:
        pixmap = (QStyle.StandardPixmap.SP_MediaPause
                  if playing else QStyle.StandardPixmap.SP_MediaPlay)
        self.preview_play_btn.setIcon(tinted_standard_icon(
            self, pixmap, size=QSize(18, 18), glyph_size=QSize(15, 15)))
        label = "Pause preview (Space)" if playing else (
            "Replay preview (Space)" if replay else "Play preview (Space)")
        self.preview_play_btn.setAccessibleName(label)
        self.preview_play_btn.setToolTip(label)
        self.preview_play_btn.setProperty("playing", "true" if playing
                                          else "false")
        self.preview_play_btn.style().unpolish(self.preview_play_btn)
        self.preview_play_btn.style().polish(self.preview_play_btn)

    def _show_preview(self, row: LibraryRow | None, count: int = 0) -> None:
        self._stop_inline_preview()
        self._editing_row = row
        editable = row is not None
        for widget in (self.preview_title, self.preview_tags,
                       self.preview_notes, self.save_btn,
                       *self.preview_details.values()):
            widget.setEnabled(editable)
        if row is None:
            self.preview_placeholder.setPixmap(QPixmap())
            self.preview_placeholder.setText(
                f"{count} clips selected" if count > 1 else
                "Select a clip to preview it")
            self.preview_thumb.setCurrentWidget(self.preview_placeholder)
            self.preview_title.clear()
            self.preview_meta.clear()
            self.preview_tags.clear()
            self.preview_notes.clear()
            for edit in self.preview_details.values():
                edit.clear()
            return
        if row.thumbnail_path and Path(row.thumbnail_path).is_file():
            pix = QPixmap(row.thumbnail_path)
            if not pix.isNull():
                self.preview_placeholder.setPixmap(pix.scaled(
                    self.preview_thumb.width(), 200,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                self.preview_placeholder.setText("")
        else:
            self.preview_placeholder.setPixmap(QPixmap())
            self.preview_placeholder.setText("No thumbnail")
        self.preview_thumb.setCurrentWidget(self.preview_placeholder)
        self.preview_title.setText(row.clip_title)
        missing = "" if Path(row.source_video_path).is_file() \
            else "\n⚠ source video missing"
        self.preview_meta.setText(
            f"{row.project_name}\n"
            f"{format_ms(row.start_ms)} - {format_ms(row.end_ms)}   ·   "
            f"{row.duration_ms/1000:.1f}s{missing}")
        self.preview_tags.setText(", ".join(row.tags))
        self.preview_notes.setPlainText(row.notes)
        for key, edit in self.preview_details.items():
            edit.setText(row.details.get(key, ""))
        self.save_btn.setText("Save Changes")

    def _save_edit(self) -> None:
        """Write the panel's edits back through the main window."""
        row = getattr(self, "_editing_row", None)
        if row is None or self.apply_edit is None:
            return
        changes = {
            "clip_title": self.preview_title.text(),
            "tags": [t.strip() for t in self.preview_tags.text().split(",")
                     if t.strip()],
            "notes": self.preview_notes.toPlainText(),
            "details": {k: e.text() for k, e in self.preview_details.items()},
        }
        ok, error = self.apply_edit(row.project_path, row.clip_id, changes)
        if not ok:
            QMessageBox.warning(self, "Could not save", error)
            return
        # Re-run the search against the fresh index, keeping this clip selected.
        keep_uid = row.clip_uid
        self._run_search()
        for i in range(self.results_list.count()):
            item = self.results_list.item(i)
            data = item.data(Qt.ItemDataRole.UserRole)
            if data and data.clip_uid == keep_uid:
                self.results_list.setCurrentItem(item)
                break
        self.save_btn.setText("Saved ✓")
        QTimer.singleShot(1500, lambda: self.save_btn.setText("Save Changes"))

    # ---------- lifecycle ----------

    def refresh(self) -> None:
        clips, projects = library_service.stats()
        if clips == 0:
            self.stats_label.setText("Index empty - click Rebuild Index.")
        else:
            self.stats_label.setText(f"{clips} clips · {projects} projects")
        current = self.project_combo.currentData() or ""
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem("All projects", "")
        for name, path in library_service.projects():
            self.project_combo.addItem(name, path)
        index = self.project_combo.findData(current)
        self.project_combo.setCurrentIndex(max(0, index))
        self.project_combo.blockSignals(False)
        self._refill_combo(self.opponent_combo, "All opponents",
                           library_service.opponents())
        self._refill_combo(self.player_combo, "All players",
                           library_service.players())
        self._rebuild_tag_menu()
        self._run_search()

    @staticmethod
    def _refill_combo(combo: QComboBox, all_label: str, values: list[str]) -> None:
        """Repopulate a filter dropdown, keeping the current choice if it
        still exists."""
        current = combo.currentData() or ""
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label, "")
        for value in values:
            combo.addItem(value, value)
        index = combo.findData(current)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _rebuild_tag_menu(self) -> None:
        self.tags_menu.clear()
        for tag in library_service.all_tags()[:60]:
            action = self.tags_menu.addAction(tag)
            action.setCheckable(True)
            action.setChecked(tag in self._active_tags)
            action.toggled.connect(
                lambda checked, t=tag: self._toggle_tag(t, checked))

    def _rebuild_chips(self) -> None:
        """Chips show ACTIVE filters only, each with an × to remove it."""
        self._chips_layout.clear()
        for tag in sorted(self._active_tags):
            chip = QPushButton(f"{tag}  ×")
            chip.setProperty("chip", "true")
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.clicked.connect(lambda _=False, t=tag: self._toggle_tag(t, False))
            self._chips_layout.addWidget(chip)
        if self._active_tags:
            clear = QPushButton("Clear all")
            clear.setProperty("chip", "true")
            clear.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            clear.clicked.connect(self._clear_tags)
            self._chips_layout.addWidget(clear)

    def _toggle_tag(self, tag: str, active: bool) -> None:
        if active:
            self._active_tags.add(tag)
        else:
            self._active_tags.discard(tag)
        self._rebuild_chips()
        self._rebuild_tag_menu()
        self._run_search()

    def _clear_tags(self) -> None:
        self._active_tags.clear()
        self._rebuild_chips()
        self._rebuild_tag_menu()
        self._run_search()

    # ---------- search ----------

    def _schedule_search(self) -> None:
        self._search_timer.start()

    def _run_search(self) -> None:
        text = self.search_box.text().strip()
        rows = library_service.search(
            text, sorted(self._active_tags),
            project_path=self.project_combo.currentData() or "",
            opponent=self.opponent_combo.currentData() or "",
            player=self.player_combo.currentData() or "",
            game_year=(self.year_combo.currentData() or "") if hasattr(self, "year_combo") else "",
            player_highlighted_only=bool(self.role_combo.currentData()))
        rows = self._apply_client_filters(rows)
        rows = self._apply_sort(rows, text)
        self._results = rows
        self._populate_results(text)

    def _apply_client_filters(self, rows: list[LibraryRow]) -> list[LibraryRow]:
        days = self.date_combo.currentData()
        if days is not None:
            cutoff = (datetime.now().astimezone() -
                      timedelta(days=days)).isoformat()
            rows = [r for r in rows if r.updated_at >= cutoff]
        rng = self.duration_combo.currentData()
        if rng is not None:
            lo, hi = rng
            rows = [r for r in rows if lo <= r.duration_ms / 1000 < hi]
        return rows

    def _apply_sort(self, rows: list[LibraryRow], text: str) -> list[LibraryRow]:
        sort = self.sort_combo.currentText()
        if sort == "Relevance":
            return library_service.rank_results(rows, text)
        if sort == "Recently Modified":
            return sorted(rows, key=lambda r: r.updated_at, reverse=True)
        if sort == "Clip Name":
            return sorted(rows, key=lambda r: (r.clip_title or "").lower())
        if sort == "Project Name":
            return sorted(rows, key=lambda r: (r.project_name.lower(),
                                               r.clip_number))
        if sort == "Source Time":
            return sorted(rows, key=lambda r: (r.project_name.lower(),
                                               r.start_ms))
        if sort == "Duration":
            return sorted(rows, key=lambda r: r.duration_ms, reverse=True)
        return rows

    def _populate_results(self, text: str = "") -> None:
        terms = [t for t in text.lower().split() if t]
        # clear() reached QListWidgetItem destruction with a null native
        # pointer array in PySide 6.11.2 on Windows. Detach each item so
        # this local Python reference owns it through explicit destruction.
        # Selection stays quiet until the complete replacement is ready.
        selection_blocker = QSignalBlocker(self.results_list)
        while self.results_list.count():
            item = self.results_list.takeItem(0)
            shiboken6.delete(item)
        total_indexed, _ = library_service.stats()
        if not self._results:
            self.results_list.hide()
            if total_indexed == 0:
                self.empty_label.setText(
                    "Your library is empty.\n\nOpen a project, add some clips, "
                    "then click Rebuild Index.")
            else:
                self.empty_label.setText(
                    "No clips match this search.\n\nTry clearing a filter, or "
                    "press Escape to clear them all.")
            self.empty_label.show()
        else:
            self.empty_label.hide()
            self.results_list.show()
        for row in self._results:
            title = tag_service.display_title(
                row.clip_title, primary_tag=row.tags[0] if row.tags else "",
                filename_base=row.clip_title)
            meta = (f"{row.project_name}   ·   {format_ms(row.start_ms)}"
                    f"   ·   {row.duration_ms/1000:.1f}s")
            if row.source_video_path and \
                    not Path(row.source_video_path).is_file():
                meta += "   ·   ⚠ source missing"
            tags = "   ".join(row.tags[:4])
            html_row = (
                f"<div style='font-size:14px;font-weight:600;'>"
                f"{_highlight(title, terms)}</div>"
                f"<div style='color:#8b8b92;'>{html.escape(meta)}</div>"
                f"<div style='color:#a8a8b4;'>{_highlight(tags, terms)}</div>")
            item = QListWidgetItem()
            item.setData(ResultRowDelegate.HTML_ROLE, html_row)
            item.setData(Qt.ItemDataRole.UserRole, row)
            if row.thumbnail_path and Path(row.thumbnail_path).is_file():
                pix = QPixmap(row.thumbnail_path)
                if not pix.isNull():
                    from PySide6.QtGui import QIcon
                    item.setIcon(QIcon(pix))
            self.results_list.addItem(item)
        noun = "clip" if len(self._results) == 1 else "clips"
        self.count_label.setText(f"{len(self._results)} {noun}")
        selection_blocker.unblock()
        self._selection_changed()

    # ---------- selection ----------

    def _selected_rows(self) -> list[LibraryRow]:
        return [item.data(Qt.ItemDataRole.UserRole)
                for item in self.results_list.selectedItems()]

    def _selection_changed(self) -> None:
        rows = self._selected_rows()
        count = len(rows)
        self.export_btn.setEnabled(count > 0)
        self.open_btn.setEnabled(count == 1)
        self.reel_btn.setEnabled(count > 1)
        self.reel_btn.setText(f"Build Reel ({count})…" if count > 1
                              else "Build Reel…")
        self.clear_sel_btn.setVisible(count > 0)
        self.preview_play_btn.setEnabled(count == 1)
        if count:
            noun = "clip" if count == 1 else "clips"
            self.export_btn.setText(f"Export {count} {noun}…")
            self.count_label.setText(
                f"{count} of {len(self._results)} selected")
        else:
            self.export_btn.setText("Export selected…")
            noun = "clip" if len(self._results) == 1 else "clips"
            self.count_label.setText(f"{len(self._results)} {noun}")
        self._show_preview(rows[0] if count == 1 else None, count)
        if not self._preview_panel.isVisible() and count:
            self._preview_panel.setVisible(True)

    # ---------- keyboard helpers ----------

    def _focus_search_slash(self) -> None:
        if not self.search_box.hasFocus():
            self.search_box.setFocus()

    def _space_preview(self) -> None:
        if self.search_box.hasFocus():
            return
        rows = self._selected_rows()
        if len(rows) == 1:
            self._toggle_inline_preview(rows[0])

    def _enter_open(self) -> None:
        if self.search_box.hasFocus():
            self._run_search()
            return
        if len(self._selected_rows()) == 1:
            self._open_selected()

    def _preview_double_clicked(self, item: QListWidgetItem, _column: int) -> None:
        """Double-clicking a result previews it in the inspector surface."""
        row = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(row, LibraryRow):
            self._quick_preview(row)

    def _clear_or_back(self) -> None:
        """Escape: selection → search text → filters → leave."""
        if self.results_list.selectedItems():
            self.results_list.clearSelection()
        elif self.search_box.text():
            self.search_box.clear()
        elif self._active_tags or any(
                c.currentIndex() > 0 for c in (
                    self.project_combo, self.opponent_combo, self.player_combo,
                    self.role_combo, self.date_combo, self.duration_combo)):
            self._active_tags.clear()
            for combo in (self.project_combo, self.opponent_combo,
                          self.player_combo, self.role_combo,
                          self.date_combo, self.duration_combo):
                combo.setCurrentIndex(0)
            self._rebuild_chips()
            self._rebuild_tag_menu()
            self._run_search()
        else:
            self.back_requested.emit()

    # ---------- actions ----------

    def _open_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        row = rows[0]
        box = QMessageBox(self)
        box.setWindowTitle(row.clip_title or "Open clip")
        box.setText(f"{row.clip_title or 'Untitled clip'}\n{row.project_name}")
        preview_btn = box.addButton("Quick Preview",
                                    QMessageBox.ButtonRole.AcceptRole)
        project_btn = box.addButton("Open in Project",
                                    QMessageBox.ButtonRole.ActionRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(preview_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is preview_btn:
            self._quick_preview(row)
        elif clicked is project_btn:
            self._open_in_project(row)

    def _quick_preview(self, row: LibraryRow) -> None:
        source = Path(row.source_video_path)
        if not source.is_file():
            QMessageBox.warning(
                self, "Source video missing",
                f"The source video for this clip no longer exists:\n{source}\n\n"
                "Open the project and use Relink Source to fix it.")
            return
        self._start_inline_preview(row)

    def _start_inline_preview(self, row: LibraryRow) -> None:
        """Play a selected library clip inside the preview surface."""
        source = Path(row.source_video_path)
        if not source.is_file():
            return
        self._stop_inline_preview()
        self._inline_preview_row = row
        self.preview_thumb.setCurrentWidget(self.preview_video)
        self._set_preview_play_state(True)
        self.preview_player.setSource(QUrl.fromLocalFile(str(source)))
        self.preview_player.setPosition(max(0, row.start_ms))
        self.preview_player.play()

    def _stop_inline_preview(self) -> None:
        self.preview_player.stop()
        self.preview_player.setSource(QUrl())
        self._inline_preview_row = None
        if hasattr(self, "preview_play_btn"):
            self._set_preview_play_state(False)

    def _inline_preview_media_status(self, status) -> None:
        row = self._inline_preview_row
        if row is None:
            return
        if status in {
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
        } and self.preview_player.position() < row.start_ms:
            self.preview_player.setPosition(row.start_ms)

    def _inline_preview_position_changed(self, position_ms: int) -> None:
        try:
            self.preview_jog_ring.set_timecode(
                format_jog_timecode(position_ms))
        except (AttributeError, RuntimeError):
            # positionChanged can arrive before the dial is built and after
            # its C++ side is gone; the readout is not worth a crash.
            pass
        row = self._inline_preview_row
        if row is None:
            return
        if position_ms >= row.end_ms > row.start_ms:
            self.preview_player.pause()
            self._set_preview_play_state(False, replay=True)

    def _toggle_inline_preview(self, row: LibraryRow) -> None:
        if self._inline_preview_row is not row:
            self._quick_preview(row)
            return
        if self.preview_player.playbackState() == \
                QMediaPlayer.PlaybackState.PlayingState:
            self.preview_player.pause()
            self._set_preview_play_state(False)
            return
        if self.preview_player.position() >= row.end_ms:
            self.preview_player.setPosition(row.start_ms)
        self.preview_player.play()
        self._set_preview_play_state(True)

    def hideEvent(self, event) -> None:
        self._stop_inline_preview()
        super().hideEvent(event)

    def _open_in_project(self, row: LibraryRow) -> None:
        if not Path(row.project_path).is_file():
            QMessageBox.warning(
                self, "Project missing",
                f"The project for this clip no longer exists:\n{row.project_path}")
            return
        self.open_clip_requested.emit(row.project_path, row.clip_id)

    def _show_source(self) -> None:
        rows = self._selected_rows()
        if len(rows) == 1 and rows[0].source_video_path:
            folder = Path(rows[0].source_video_path).parent
            if folder.is_dir():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _build_reel(self) -> None:
        """Join the selection into one tape, in the order shown."""
        rows = [r for r in self._selected_rows()
                if Path(r.source_video_path).is_file()]
        if len(rows) < 2:
            QMessageBox.information(
                self, "Select clips for the reel",
                "Choose at least two clips with available source video.")
            return
        # Keep the on-screen order, not click order.
        order = {id(item.data(Qt.ItemDataRole.UserRole)): i
                 for i, item in enumerate(
                     self.results_list.item(n) for n in
                     range(self.results_list.count()))}
        rows.sort(key=lambda r: order.get(id(r), 0))

        suggested = str(Path(self.settings.last_export_folder or
                             self.settings.default_output_folder) / "Reel.mp4")
        target, _ = QFileDialog.getSaveFileName(
            self, "Save reel as…", suggested, "MP4 video (*.mp4)")
        if not target:
            return
        output = Path(target)
        self.settings.last_export_folder = str(output.parent)
        self.settings.save()

        total_s = sum(r.duration_ms for r in rows) / 1000
        dialog = QProgressDialog(
            f"Building a reel from {len(rows)} clips ({total_s:.0f}s)…",
            "Cancel", 0, 100, self)
        dialog.setWindowTitle("Build Reel")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)

        worker = ReelWorker(self.settings.ffmpeg_path, rows, output, self)
        self._reel_worker = worker

        def done(path: str) -> None:
            dialog.close()
            self._reel_worker = None
            QMessageBox.information(
                self, "Reel ready",
                f"Built a {total_s:.0f}s reel from {len(rows)} clips:\n{path}")

        def failed(message: str) -> None:
            dialog.close()
            self._reel_worker = None
            QMessageBox.warning(self, "Could not build the reel", message)

        worker.progress.connect(lambda pct: dialog.setValue(int(pct)))
        worker.finished_ok.connect(done)
        worker.failed.connect(failed)
        dialog.canceled.connect(worker.cancel)
        worker.start()

    def _export_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            QMessageBox.information(
                self, "No clips selected",
                "Select one or more clips in the results, then export.")
            return
        missing = [r for r in rows if not Path(r.source_video_path).is_file()]
        if missing:
            QMessageBox.warning(
                self, "Source video missing",
                f"{len(missing)} of the selected clips point to a source video "
                "that no longer exists and will be skipped.")
            rows = [r for r in rows if Path(r.source_video_path).is_file()]
        if not rows:
            return
        out_dir = QFileDialog.getExistingDirectory(
            self, "Export selected clips to…",
            self.settings.last_export_folder or
            self.settings.default_output_folder)
        if not out_dir:
            return
        # Open here next time.
        self.settings.last_export_folder = out_dir
        self.settings.save()
        self._start_export(rows, Path(out_dir))

    def _start_export(self, rows: list[LibraryRow], out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        dialog = QProgressDialog("Exporting clips…", "Cancel", 0, len(rows), self)
        dialog.setWindowTitle("Library Export")
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)

        worker = LibraryExportWorker(
            self.settings.ffmpeg_path, rows, out_dir,
            self.settings.separator_style, self)
        self._export_worker = worker

        def on_progress(done: int, total: int, name: str) -> None:
            dialog.setValue(done - 1)
            dialog.setLabelText(f"Exporting {done} of {total}:\n{name}")

        def on_finished(exported: int, failed: int) -> None:
            dialog.close()
            self._export_worker = None
            msg = f"Exported {exported} clip(s) to:\n{out_dir}"
            if failed:
                msg += f"\n\n{failed} clip(s) failed - see the log."
            QMessageBox.information(self, "Export complete", msg)

        worker.progress.connect(on_progress)
        worker.finished_ok.connect(on_finished)
        dialog.canceled.connect(worker.cancel)
        worker.start()

    # ---------- index maintenance ----------

    def _rebuild_index(self) -> None:
        # Scan real folders - not just the recents list, which can be empty
        # or stale - so every project on disk gets picked up.
        paths = library_service.discover_project_files(
            [self.settings.default_project_folder,
             self.settings.default_output_folder],
            list(self.settings.recent_projects))
        if not paths:
            QMessageBox.information(
                self, "No projects found",
                "No project files were found in your project or output "
                "folders. Create a project first, or open one so its folder "
                "becomes known.")
            return
        clips, projects, skipped = library_service.rebuild_from_projects(paths)
        self.refresh()
        message = (f"Found {len(paths)} project file(s); indexed {clips} clips "
                   f"from {projects} of them.")
        if skipped:
            # Say so plainly: silently keeping stale rows looks like a bug,
            # and silently dropping them loses clips.
            message += (f"\n\n{skipped} project(s) could not be reached. An "
                        "external drive or network folder may be "
                        "disconnected. Their clips were left in the Library "
                        "rather than removed.")
        QMessageBox.information(self, "Index rebuilt", message)
