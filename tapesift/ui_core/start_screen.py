"""Home screen: welcome block, drop zone, and a recent-project dashboard.

Recent projects are compact cards - thumbnail, name, source file, clip count,
modified time - the whole row double-clickable, with a visible ⋯ menu (plus
right-click) for Open / Rename / Duplicate / Show in Explorer / Remove /
Delete. All actions are wired to real behavior in MainWindow.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget,
)

from tapesift import AUTHOR_LINE, COPYRIGHT_LINE, __version__ as APP_VERSION
from tapesift.core.config import AppSettings
from tapesift.database.connection import (
    PROJECT_FILE_EXTENSION,
    SUPPORTED_PROJECT_FILE_EXTENSIONS,
)
from tapesift.services import library_service
from tapesift.ui_core.led_wordmark import wordmark_pixmap

log = logging.getLogger(__name__)

VIDEO_FORMATS = "MP4, MOV, MKV, AVI, M4V and WebM"


@dataclass
class ProjectInfo:
    path: Path
    name: str = ""
    source_name: str = ""
    clip_count: int | None = None
    logged_count: int = 0
    opponent: str = ""
    modified: str = ""
    modified_ts: float = 0.0
    thumbnail: str = ""
    exists: bool = True
    tags: list[str] = field(default_factory=list)

    @property
    def unlogged(self) -> int:
        return max(0, (self.clip_count or 0) - self.logged_count)

    @property
    def progress_pct(self) -> int:
        if not self.clip_count:
            return 0
        return int(self.logged_count / self.clip_count * 100)


def _relative_time(stamp: float) -> str:
    delta = datetime.now() - datetime.fromtimestamp(stamp)
    seconds = int(delta.total_seconds())
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} minutes ago"
    if seconds < 86400 * 2:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago" \
            if seconds < 86400 else "yesterday"
    if seconds < 86400 * 30:
        return f"{seconds // 86400} days ago"
    return datetime.fromtimestamp(stamp).strftime("%b %d, %Y")


def load_project_info(path: Path) -> ProjectInfo:
    """Light read-only peek into a project file for its dashboard card."""
    info = ProjectInfo(path=path, name=path.stem)
    if not path.is_file():
        info.exists = False
        return info
    try:
        info.modified_ts = path.stat().st_mtime
        info.modified = _relative_time(info.modified_ts)
    except OSError:
        pass
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True,
                               timeout=2.0)
        try:
            row = conn.execute(
                "SELECT name, source_video_path FROM projects LIMIT 1"
            ).fetchone()
            if row:
                info.name = row[0] or path.stem
                if row[1]:
                    info.source_name = Path(row[1]).name
            try:
                opp = conn.execute(
                    "SELECT opponent FROM projects LIMIT 1").fetchone()
                info.opponent = (opp[0] or "") if opp else ""
            except sqlite3.DatabaseError:
                info.opponent = ""      # project predates the opponent column
            info.clip_count = conn.execute(
                "SELECT COUNT(*) FROM clips").fetchone()[0]
            # "Logged" means the clip has play details on it - that is what
            # turns the card into a work queue rather than a file list.
            try:
                info.logged_count = conn.execute(
                    "SELECT COUNT(*) FROM clips WHERE details_json NOT IN "
                    "('', '{}')").fetchone()[0]
            except sqlite3.DatabaseError:
                info.logged_count = 0
            # Use the most visually informative cached frame. Detection often
            # begins on a black separator, so blindly taking clip 1 gives the
            # home screen a black thumbnail even when good field frames exist.
            thumbs = conn.execute(
                "SELECT thumbnail_path FROM clips WHERE thumbnail_path != ''"
            ).fetchall()
            candidates = [Path(row[0]) for row in thumbs
                          if row[0] and Path(row[0]).is_file()]
            if candidates:
                try:
                    info.thumbnail = str(max(
                        candidates, key=lambda path: path.stat().st_size))
                except OSError:
                    info.thumbnail = str(candidates[0])
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        log.debug("Could not read project card info for %s", path)
    return info


class ProjectCard(QFrame):
    """One recent-project row. Entirely clickable; ⋯ opens the action menu."""

    open_requested = Signal(str)
    resume_requested = Signal(str)
    menu_requested = Signal(str, object)   # path, global QPoint

    def __init__(self, info: ProjectInfo, parent=None) -> None:
        super().__init__(parent)
        self.info = info
        self.setProperty("card", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(str(info.path))
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 8, 8)
        row.setSpacing(12)

        thumb = QLabel()
        thumb.setFixedSize(96, 54)
        thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb.setStyleSheet("background-color:#141416;border-radius:3px;")
        if info.thumbnail:
            pix = QPixmap(info.thumbnail)
            if not pix.isNull():
                thumb.setPixmap(pix.scaled(
                    96, 54, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
        else:
            thumb.setText("")
        row.addWidget(thumb)

        text = QVBoxLayout()
        text.setSpacing(1)
        name = QLabel(info.name + ("  (missing)" if not info.exists else ""))
        name.setStyleSheet("font-size:14px;font-weight:600;")
        text.addWidget(name)
        if info.source_name:
            source = QLabel(info.source_name)
            source.setProperty("role", "subtle")
            text.addWidget(source)
        bits = []
        if info.opponent:
            bits.append(f"vs {info.opponent}")
        if info.modified:
            bits.append(info.modified)
        if bits:
            meta = QLabel(" · ".join(bits))
            meta.setProperty("role", "subtle")
            text.addWidget(meta)

        # Logging progress: which films still need work, at a glance.
        if info.clip_count:
            progress_row = QHBoxLayout()
            progress_row.setSpacing(8)
            bar = QProgressBar()
            bar.setProperty("logging", "true")
            bar.setTextVisible(False)
            bar.setFixedHeight(5)
            bar.setMaximum(info.clip_count)
            bar.setValue(info.logged_count)
            if info.unlogged == 0:
                bar.setProperty("complete", "true")
            progress_row.addWidget(bar, 1)
            state = QLabel(
                f"{info.logged_count} of {info.clip_count} logged"
                if info.unlogged else f"all {info.clip_count} logged")
            state.setProperty("role", "subtle")
            progress_row.addWidget(state)
            text.addLayout(progress_row)
        row.addLayout(text, 1)

        if info.unlogged:
            resume = QPushButton("Resume")
            resume.setToolTip(
                f"Open and jump straight to the first of {info.unlogged} "
                "clips still needing details.")
            resume.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            resume.clicked.connect(
                lambda: self.resume_requested.emit(str(self.info.path)))
            row.addWidget(resume)

        dots = QToolButton()
        dots.setText("…")
        dots.setToolTip("Project actions")
        dots.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        dots.clicked.connect(
            lambda: self.menu_requested.emit(
                str(self.info.path),
                dots.mapToGlobal(dots.rect().bottomLeft())))
        row.addWidget(dots)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.info.exists:
            self.open_requested.emit(str(self.info.path))

    def contextMenuEvent(self, event) -> None:
        self.menu_requested.emit(str(self.info.path), event.globalPos())


class StartScreen(QWidget):
    new_project_requested = Signal(str, str, str)  # name, folder, output_folder
    open_project_requested = Signal(str)           # db path
    settings_requested = Signal()
    library_requested = Signal()
    rename_project_requested = Signal(str, str)    # db path, new name
    delete_project_requested = Signal(str)         # db path
    duplicate_project_requested = Signal(str)      # db path
    resume_project_requested = Signal(str)         # open + jump to first unlogged

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(16)

        # ---- header: identity left, tools right (one row, no duplication) ----
        header = QHBoxLayout()
        header.setSpacing(10)
        icon_path = (Path(__file__).resolve().parent.parent / "resources"
                     / "icons" / "tapesift.ico")
        if icon_path.is_file():
            icon_label = QLabel()
            icon_label.setPixmap(QPixmap(str(icon_path)).scaled(
                30, 30, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
            header.addWidget(icon_label)
        wordmark = QLabel()
        wordmark.setPixmap(wordmark_pixmap("TAPESIFT", dot=3, scheme="silver"))
        header.addWidget(wordmark)
        # "1.0.0" reads like a changelog; "1.0" reads like a product.
        version = QLabel(f"Version {'.'.join(APP_VERSION.split('.')[:2])}")
        version.setProperty("badge", "true")
        version.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(version)
        byline = QLabel(AUTHOR_LINE)
        byline.setProperty("role", "subtle")
        byline.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(byline)
        header.addStretch()
        library_btn = QPushButton("Library Search")
        library_btn.setToolTip("Find clips by tag, player, or play type "
                               "across every project")
        library_btn.clicked.connect(self.library_requested.emit)
        settings_btn = QPushButton("Settings")
        settings_btn.clicked.connect(self.settings_requested.emit)
        header.addWidget(library_btn)
        header.addWidget(settings_btn)
        outer.addLayout(header)

        # ---- actions: one compact row, drop zone inline beside them ----
        actions = QHBoxLayout()
        actions.setSpacing(8)
        new_btn = QPushButton("New Project")
        new_btn.setProperty("accent", "true")
        new_btn.setMinimumHeight(34)
        new_btn.setMinimumWidth(130)
        new_btn.clicked.connect(self._new_project)
        open_btn = QPushButton("Open Project…")
        open_btn.setMinimumHeight(34)
        open_btn.clicked.connect(self._open_project)
        actions.addWidget(new_btn)
        actions.addWidget(open_btn)

        # A slim strip rather than a big box: you drop a film once, but you
        # open a recent project every session.
        self.drop_zone = QFrame()
        self.drop_zone.setProperty("dropzone", "true")
        self.drop_zone.setFixedHeight(34)
        zone_box = QHBoxLayout(self.drop_zone)
        zone_box.setContentsMargins(12, 0, 12, 0)
        zone_title = QLabel("Drop a video here to begin")
        zone_sub = QLabel(VIDEO_FORMATS)
        zone_sub.setProperty("role", "subtle")
        zone_box.addWidget(zone_title)
        zone_box.addSpacing(10)
        zone_box.addWidget(zone_sub)
        zone_box.addStretch()
        actions.addWidget(self.drop_zone, 1)
        outer.addLayout(actions)

        # ---- recent projects: the main content ----
        recent_header = QHBoxLayout()
        recent_label = QLabel("Recent Projects")
        recent_label.setProperty("role", "heading")
        recent_header.addWidget(recent_label)
        self.summary_label = QLabel("")
        self.summary_label.setProperty("role", "subtle")
        recent_header.addWidget(self.summary_label)
        recent_header.addStretch()
        all_btn = QPushButton("All Projects…")
        all_btn.setToolTip("Every project found on disk, with search and sort.")
        all_btn.clicked.connect(self._show_all_projects)
        recent_header.addWidget(all_btn)
        outer.addLayout(recent_header)

        panel = QWidget()
        panel.setProperty("panel", "true")
        panel_box = QVBoxLayout(panel)
        panel_box.setContentsMargins(10, 10, 10, 10)
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.cards_scroll.setStyleSheet("background: transparent;")
        self.cards_host = QWidget()
        # Two columns, so the width is actually used.
        self.cards_layout = QGridLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.cards_layout.setColumnStretch(0, 1)
        self.cards_layout.setColumnStretch(1, 1)
        self.cards_scroll.setWidget(self.cards_host)
        panel_box.addWidget(self.cards_scroll)

        self.empty_recent = QLabel(
            "No recent projects yet.\nCreate one, or drop a video above to "
            "start straight from the film.")
        self.empty_recent.setProperty("role", "subtle")
        self.empty_recent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_box.addWidget(self.empty_recent)
        outer.addWidget(panel, 1)

        # Quiet footer: present on the first screen every session, but it
        # never competes with the work.
        footer = QLabel(COPYRIGHT_LINE)
        footer.setProperty("role", "subtle")
        footer.setStyleSheet("font-size: 11px;")
        footer.setAlignment(Qt.AlignmentFlag.AlignRight)
        outer.addWidget(footer)

        self.refresh_recent()

    # ---------- drag feedback ----------

    def set_drag_active(self, active: bool) -> None:
        self.drop_zone.setProperty("dragover", "true" if active else "false")
        self.drop_zone.style().unpolish(self.drop_zone)
        self.drop_zone.style().polish(self.drop_zone)

    # ---------- recent cards ----------

    def refresh_recent(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        paths = self.settings.recent_projects
        self.empty_recent.setVisible(not paths)
        self.cards_scroll.setVisible(bool(paths))

        infos = [load_project_info(Path(p)) for p in paths]
        for index, info in enumerate(infos):
            card = self._make_card(info)
            self.cards_layout.addWidget(card, index // 2, index % 2)
        self.cards_layout.setRowStretch(
            (len(infos) + 1) // 2, 1)             # keep cards top-aligned

        total = sum(i.clip_count or 0 for i in infos)
        unlogged = sum(i.unlogged for i in infos)
        self.summary_label.setText(
            f"· {total} clips, {unlogged} still to log" if total else "")

    def _make_card(self, info: ProjectInfo) -> "ProjectCard":
        card = ProjectCard(info)
        card.open_requested.connect(self.open_project_requested.emit)
        card.resume_requested.connect(self.resume_project_requested.emit)
        card.menu_requested.connect(self._card_menu)
        return card

    def _show_all_projects(self) -> None:
        dialog = AllProjectsDialog(self.settings, self)
        dialog.open_requested.connect(self.open_project_requested.emit)
        dialog.exec()

    def _card_menu(self, path_str: str, global_pos) -> None:
        path = Path(path_str)
        exists = path.is_file()
        menu = QMenu(self)
        open_action = menu.addAction("Open")
        rename_action = menu.addAction("Rename…")
        duplicate_action = menu.addAction("Duplicate")
        reveal_action = menu.addAction("Show in Explorer")
        menu.addSeparator()
        remove_action = menu.addAction("Remove from Recent")
        delete_action = menu.addAction("Delete Project…")
        for action in (open_action, rename_action, duplicate_action,
                       reveal_action, delete_action):
            action.setEnabled(exists)

        chosen = menu.exec(global_pos)
        if chosen == open_action:
            self.open_project_requested.emit(path_str)
        elif chosen == rename_action:
            self._rename(path)
        elif chosen == duplicate_action:
            self.duplicate_project_requested.emit(path_str)
        elif chosen == reveal_action:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        elif chosen == remove_action:
            if path_str in self.settings.recent_projects:
                self.settings.recent_projects.remove(path_str)
                self.settings.save()
            self.refresh_recent()
        elif chosen == delete_action:
            self._confirm_delete(path)

    def _rename(self, path: Path) -> None:
        new_name, ok = QInputDialog.getText(
            self, "Rename Project", "New project name:", text=path.stem)
        new_name = (new_name or "").strip()
        if ok and new_name and new_name != path.stem:
            self.rename_project_requested.emit(str(path), new_name)

    def _confirm_delete(self, path: Path) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Delete project")
        box.setText(f"Delete the project '{path.stem}'?")
        box.setInformativeText(
            "This permanently deletes the project file - its clips, tags and "
            "play details.\n\nYour source video and any exported clips are "
            "NOT touched.")
        delete_btn = box.addButton("Delete Project",
                                   QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(box.button(QMessageBox.StandardButton.Cancel))
        box.exec()
        if box.clickedButton() is delete_btn:
            self.delete_project_requested.emit(str(path))

    # ---------- create / open ----------

    def _new_project(self) -> None:
        name, ok = QInputDialog.getText(self, "New Project", "Project name:")
        name = (name or "").strip()
        if not ok or not name:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Choose where to save the project",
            self.settings.default_project_folder)
        if not folder:
            return
        output_folder = QFileDialog.getExistingDirectory(
            self, "Choose the export output folder",
            self.settings.default_output_folder)
        if not output_folder:
            output_folder = str(Path(self.settings.default_output_folder) / name)
        self.new_project_requested.emit(name, folder, output_folder)

    def _open_project(self) -> None:
        patterns = " ".join(f"*{ext}" for ext in SUPPORTED_PROJECT_FILE_EXTENSIONS)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", self.settings.default_project_folder,
            f"TapeSift projects ({patterns});;All files (*)")
        if path:
            self.open_project_requested.emit(path)


class AllProjectsDialog(QDialog):
    """Every project found on disk, with search and sort.

    The recent list only holds a handful; once a season is broken down
    there are far more, and they need finding rather than scrolling.
    """

    open_requested = Signal(str)

    SORTS = ["Recently modified", "Name", "Opponent", "Most unlogged"]

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("All Projects")
        self.setMinimumSize(760, 560)

        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search projects - name, film, opponent…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._refresh)
        bar.addWidget(self.search, 1)
        self.sort = QComboBox()
        self.sort.addItems(self.SORTS)
        self.sort.currentIndexChanged.connect(self._refresh)
        bar.addWidget(self.sort)
        layout.addLayout(bar)

        self.count_label = QLabel("")
        self.count_label.setProperty("role", "subtle")
        layout.addWidget(self.count_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.host = QWidget()
        self.host_layout = QVBoxLayout(self.host)
        self.host_layout.setContentsMargins(0, 0, 0, 0)
        self.host_layout.setSpacing(6)
        scroll.setWidget(self.host)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._infos = [
            load_project_info(Path(p))
            for p in library_service.discover_project_files(
                [settings.default_project_folder, settings.default_output_folder],
                list(settings.recent_projects))
        ]
        self._refresh()

    def _refresh(self) -> None:
        while self.host_layout.count():
            item = self.host_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        terms = [t for t in self.search.text().lower().split() if t]
        rows = [
            i for i in self._infos
            if all(t in f"{i.name} {i.source_name} {i.opponent}".lower()
                   for t in terms)
        ]
        sort = self.sort.currentText()
        if sort == "Name":
            rows.sort(key=lambda i: i.name.lower())
        elif sort == "Opponent":
            rows.sort(key=lambda i: (i.opponent.lower() or "zzz", i.name.lower()))
        elif sort == "Most unlogged":
            rows.sort(key=lambda i: i.unlogged, reverse=True)
        else:
            rows.sort(key=lambda i: i.modified_ts, reverse=True)

        for info in rows:
            card = ProjectCard(info)
            card.open_requested.connect(self._open)
            card.resume_requested.connect(self._open)
            self.host_layout.addWidget(card)
        self.host_layout.addStretch()
        self.count_label.setText(
            f"{len(rows)} of {len(self._infos)} projects"
            if terms else f"{len(self._infos)} projects")

    def _open(self, path: str) -> None:
        self.open_requested.emit(path)
        self.accept()
