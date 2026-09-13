"""Build groups, review membership, then hand ordinary jobs to Export."""
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QSize, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from tapesift.services import cutup_service as cutups
from tapesift.services.football_context import team_name
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_v3.result_picker import SURFACE_STYLE


class CutupDialog(QDialog):
    queue_requested = Signal(object)

    def __init__(self, project, clips, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Build cutups — review before export")
        self.resize(1120, 780)
        self.setStyleSheet(SURFACE_STYLE)
        self.project = deepcopy(project)
        self.clips = cutups.film_order(deepcopy(clips))
        self.groups = []
        self._loading = False
        self._preview_clip = None
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("BUILD CUTUPS   ·   Choose groups → Review clips → Queue export"))
        top = QHBoxLayout()
        self.game = QLineEdit(project.name)
        self.game.setPlaceholderText("Game / matchup")
        self.year = QLineEdit(project.game_year)
        self.year.setPlaceholderText("Game year")
        self.year.setMaxLength(4)
        self.year.setFixedWidth(86)
        self.layout_choice = QComboBox()
        self.layout_choice.addItem("Game first", "game")
        self.layout_choice.addItem("Player / group first", "group")
        self.mode = QComboBox()
        for caption, value in (("Individual clips + reel", "both"), ("Individual clips", "individual"), ("One reel per group", "reel")):
            self.mode.addItem(caption, value)
        for widget in (QLabel("Game"), self.game, self.year, self.layout_choice, self.mode):
            top.addWidget(widget)
        outer.addLayout(top)
        destination = QHBoxLayout()
        self.destination = QLineEdit(project.output_folder)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        destination.addWidget(QLabel("Destination"))
        destination.addWidget(self.destination, 1)
        destination.addWidget(browse)
        outer.addLayout(destination)
        splitter = QSplitter()
        outer.addWidget(splitter, 1)
        left = QWidget()
        sidebar = QVBoxLayout(left)
        sidebar.setContentsMargins(0, 0, 6, 0)
        self.kind = QComboBox()
        for key, text in cutups.KINDS.items():
            self.kind.addItem("By " + text.lower(), key)
        sidebar.addWidget(self.kind)
        self.primary_only = QCheckBox("Primary player only")
        self.primary_only.setToolTip("Unchecked includes primary, secondary, and assigned quarterback.")
        sidebar.addWidget(self.primary_only)
        sidebar.addWidget(QLabel("Combine filters (all must match)"))
        self.filters = {}
        filter_grid = QGridLayout()
        for row, kind in enumerate(("player", "play_type", "down", "result", "action", "quarter", "situation")):
            combo = QComboBox()
            combo.addItem("Any", "")
            for group in cutups.catalog(self.clips, kind, team_ids=project.game_team_ids):
                combo.addItem(group.value, group.value)
            combo.currentIndexChanged.connect(self._catalog)
            self.filters[kind] = combo
            filter_grid.addWidget(QLabel(cutups.KINDS[kind]), row, 0)
            filter_grid.addWidget(combo, row, 1)
        sidebar.addLayout(filter_grid)
        self.choices = QListWidget()
        self.choices.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        sidebar.addWidget(self.choices, 1)
        add = QPushButton("Add selected groups →")
        add.clicked.connect(self._add_groups)
        sidebar.addWidget(add)
        splitter.addWidget(left)
        right = QWidget()
        review = QVBoxLayout(right)
        review.setContentsMargins(6, 0, 0, 0)
        group_row = QHBoxLayout()
        self.group_choice = QComboBox()
        self.group_choice.currentIndexChanged.connect(self._review)
        remove = QPushButton("Remove group")
        remove.clicked.connect(self._remove_group)
        group_row.addWidget(self.group_choice, 1)
        group_row.addWidget(remove)
        review.addLayout(group_row)
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder label"))
        self.folder_label = QLineEdit()
        self.folder_label.setPlaceholderText("Player / group folder name")
        self.folder_label.setToolTip("Edit freely, e.g. Kellen Wiley — Miami Defense vs Stanford — 2026")
        self.folder_label.textEdited.connect(self._rename_group)
        folder_row.addWidget(self.folder_label, 1)
        self.role = QComboBox()
        self.role.addItem("Add player role…", "")
        if len(project.game_team_ids) == 2:
            offense, defense = (team_name(t) for t in project.game_team_ids)
            self.role.addItem(f"{offense} offense", f"{offense} Offense vs {defense}")
            self.role.addItem(f"{defense} defense", f"{defense} Defense vs {offense}")
        self.role.currentIndexChanged.connect(self._set_role)
        folder_row.addWidget(self.role)
        review.addLayout(folder_row)
        preview_row = QHBoxLayout()
        self.video = QVideoWidget()
        self.video.setMinimumSize(240, 135)
        self.video.setMaximumHeight(180)
        preview_row.addWidget(self.video, 1)
        playback = QVBoxLayout()
        self.preview_title = QLabel("Select a clip to preview")
        self.preview_title.setWordWrap(True)
        playback.addWidget(self.preview_title)
        play = QPushButton("Play / pause")
        play.clicked.connect(self._play_pause)
        playback.addWidget(play)
        self.preview_error = QLabel("")
        self.preview_error.setWordWrap(True)
        playback.addWidget(self.preview_error)
        preview_row.addLayout(playback, 1)
        review.addLayout(preview_row)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._position_changed)
        self.player.mediaStatusChanged.connect(self._media_ready)
        self.player.errorOccurred.connect(lambda _error, message: self.preview_error.setText(message))
        toggles = QHBoxLayout()
        for text, state in (("Select all", True), ("Clear all", False)):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, checked=state: self._select_all(checked))
            toggles.addWidget(button)
        toggles.addStretch(1)
        review.addLayout(toggles)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Use", "Play", "Clip name", "Situation", "Players", "Results"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setIconSize(QSize(64, 36))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._checked)
        self.table.currentCellChanged.connect(self._preview)
        review.addWidget(self.table, 1)
        self.paths = QListWidget()
        self.paths.setMaximumHeight(100)
        review.addWidget(self.paths)
        splitter.addWidget(right)
        splitter.setSizes([285, 790])
        self.summary = QLabel("Add a group to review matching clips.")
        self.summary.setWordWrap(True)
        outer.addWidget(self.summary)
        footer = QHBoxLayout()
        footer.addWidget(QLabel("Clean film · film order · checks affect this export only"), 1)
        close = QPushButton("Cancel")
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        self.queue_button = QPushButton("Queue export")
        self.queue_button.setProperty("primary", "true")
        self.queue_button.clicked.connect(self._queue)
        footer.addWidget(self.queue_button)
        outer.addLayout(footer)
        self.kind.currentIndexChanged.connect(self._catalog)
        self.primary_only.toggled.connect(self._catalog)
        for control in (self.game, self.year, self.destination):
            control.textChanged.connect(self._refresh_plan)
        self.layout_choice.currentIndexChanged.connect(self._refresh_plan)
        self.mode.currentIndexChanged.connect(self._refresh_plan)
        self._catalog()
        self._refresh_plan()

    def _catalog(self, *_args):
        if not hasattr(self, "choices"):
            return
        conditions = {key: combo.currentData() for key, combo in self.filters.items()}
        self.candidates = cutups.catalog(self.clips, self.kind.currentData(), conditions,
            primary_only=self.primary_only.isChecked(), team_ids=self.project.game_team_ids)
        self.choices.clear()
        for index, group in enumerate(self.candidates):
            item = QListWidgetItem(f"{group.value} · {len(group.clip_ids)} plays")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.choices.addItem(item)

    def _add_groups(self):
        for item in self.choices.selectedItems():
            group = deepcopy(self.candidates[item.data(Qt.ItemDataRole.UserRole)])
            if any(g.kind == group.kind and g.value == group.value and g.clip_ids == group.clip_ids for g in self.groups):
                continue
            self.groups.append(group)
            self.group_choice.addItem(f"{group.value} · {len(group.clip_ids)} plays")
        self.group_choice.setCurrentIndex(len(self.groups) - 1)
        self._review()

    def _remove_group(self):
        index = self.group_choice.currentIndex()
        if index >= 0:
            self.groups.pop(index)
            self.group_choice.removeItem(index)
            self._review()

    def _review(self, *_args):
        self._loading = True
        self.table.setRowCount(0)
        index = self.group_choice.currentIndex()
        if index < 0 or index >= len(self.groups):
            self.folder_label.clear()
            self._loading = False
            self._refresh_plan()
            return
        group = self.groups[index]
        self.folder_label.setText(group.folder_name or group.value)
        self.role.blockSignals(True)
        self.role.setCurrentIndex(0)
        self.role.setEnabled(group.kind == "player")
        self.role.blockSignals(False)
        by_id = {c.id: c for c in self.clips}
        for row, cid in enumerate(group.clip_ids):
            clip = by_id[cid]
            self.table.insertRow(row)
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
            use.setCheckState(Qt.CheckState.Unchecked if cid in group.excluded else Qt.CheckState.Checked)
            use.setData(Qt.ItemDataRole.UserRole, cid)
            self.table.setItem(row, 0, use)
            values = [str(clip.clip_number or self.clips.index(clip) + 1), clip.clip_title or "Untitled play",
                      " · ".join(clip.details.get(k, "") for k in ("quarter", "down_distance")),
                      ", ".join(cutups.values_for_clip(clip, "player")), clip.details.get("result", "")]
            for column, value in enumerate(values, 1):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if column == 2 and clip.thumbnail_path:
                    item.setIcon(QIcon(clip.thumbnail_path))
                self.table.setItem(row, column, item)
        self._loading = False
        if self.table.rowCount():
            self.table.selectRow(0)
        self._refresh_plan()

    def _rename_group(self, value):
        index = self.group_choice.currentIndex()
        if index >= 0:
            self.groups[index].folder_name = value
            self._refresh_plan()

    def _set_role(self):
        index = self.group_choice.currentIndex()
        if index >= 0 and self.role.currentData():
            value = f"{self.groups[index].value} — {self.role.currentData()} — {self.year.text() or 'Year unknown'}"
            self.folder_label.setText(value)
            self._rename_group(value)

    def _checked(self, item):
        if self._loading or item.column() != 0:
            return
        group = self.groups[self.group_choice.currentIndex()]
        cid = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            group.excluded.discard(cid)
        else:
            group.excluded.add(cid)
        self._refresh_plan()

    def _select_all(self, checked):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def build_plan(self, *, prepare=False):
        self.project.output_folder = self.destination.text().strip()
        return cutups.plan_cutups(self.project, self.clips, self.groups, mode=self.mode.currentData(),
            layout=self.layout_choice.currentData(), game_name=self.game.text(), year=self.year.text(),
            preset=self.project.default_preset, prepare=prepare)

    def _refresh_plan(self, *_args):
        if not hasattr(self, "queue_button"):
            return
        self.paths.clear()
        try:
            plan = self.build_plan()
            for job in plan.jobs:
                item = QListWidgetItem(job.output_path)
                item.setToolTip(job.output_path)
                self.paths.addItem(item)
            unique = {cid for g in self.groups for cid in g.clip_ids if cid not in g.excluded}
            self.summary.setText(f"{len(self.groups)} groups · {len(unique)} unique plays · {len(plan.jobs)} files. A shared play appears once in each selected group.")
            self.queue_button.setEnabled(bool(plan.jobs))
        except Exception as exc:
            self.summary.setText(str(exc))
            self.queue_button.setEnabled(False)

    def _queue(self):
        # The owner rechecks the live session and stages through the durable queue.
        self.queue_requested.emit(self)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Export destination", self.destination.text())
        if path:
            self.destination.setText(path)

    def _preview(self, row, *_args):
        if self._loading or row < 0:
            return
        cid = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        self._preview_clip = next(c for c in self.clips if c.id == cid)
        self.preview_title.setText(f"{self._preview_clip.clip_title or 'Play'}\n{format_ms(self._preview_clip.start_ms)} – {format_ms(self._preview_clip.end_ms)}")
        if self.player.source().isEmpty():
            self.player.setSource(QUrl.fromLocalFile(self.project.source_video_path))
        else:
            self.player.pause()
            self.player.setPosition(self._preview_clip.start_ms)

    def _media_ready(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia and self._preview_clip:
            self.player.setPosition(self._preview_clip.start_ms)

    def _play_pause(self):
        if not self._preview_clip:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            if not self._preview_clip.start_ms <= self.player.position() < self._preview_clip.end_ms:
                self.player.setPosition(self._preview_clip.start_ms)
            self.player.play()

    def _position_changed(self, position):
        if self._preview_clip and position >= self._preview_clip.end_ms:
            self.player.pause()

    def done(self, code):
        self.player.stop()
        self.player.setSource(QUrl())
        super().done(code)
