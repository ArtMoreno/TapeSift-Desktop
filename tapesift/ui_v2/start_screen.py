"""Scoreboard-style V2 home dashboard built from the selected layout."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpacerItem, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from tapesift import AUTHOR, __version__
from tapesift.core.config import AppSettings
from tapesift.ui_core.start_screen import (
    VIDEO_FORMATS, ProjectCard, ProjectInfo, StartScreen, load_project_info,
)

ICON_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons"
# Qt's "no maximum" sentinel, used to release a temporary max-width cap.
_UNBOUNDED = 16777215
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}


def _icon_path(name: str) -> Path | None:
    """Icons are optional: a missing file must not take the screen down."""
    path = ICON_DIR / name
    return path if path.is_file() else None


def _brand_image_label(
        name: str,
        height: int,
        *,
        object_name: str = "",
        accessible_name: str = "TapeSift",
) -> QLabel | None:
    """Load an official rasterized brand master at a restrained UI height."""
    path = _icon_path(name)
    if path is None:
        return None
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return None
    scaled = pixmap.scaledToHeight(
        height, Qt.TransformationMode.SmoothTransformation)
    label = QLabel()
    if object_name:
        label.setObjectName(object_name)
    label.setAccessibleName(accessible_name)
    label.setProperty("brandAsset", name)
    label.setPixmap(scaled)
    label.setFixedSize(scaled.size())
    return label


class ElidedLabel(QLabel):
    """Single-line label that cannot force a dashboard row wider than its view."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self._full_text = text
        self.setAccessibleName(text)
        self.setToolTip(text)
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        elided = self.fontMetrics().elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            max(1, event.size().width()),
        )
        if super().text() != elided:
            super().setText(elided)


class ProjectCardV2(ProjectCard):
    """A full-width recent-project row styled like a film-room queue."""

    def __init__(self, info: ProjectInfo, parent=None) -> None:
        QFrame.__init__(self, parent)
        self.info = info
        self.setProperty("projectCard", "true")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(str(info.path))
        self.setMinimumHeight(128)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 8, 8, 8)
        row.setSpacing(16)

        self._thumbnail = QLabel("NO PREVIEW")
        self._thumbnail.setProperty("projectThumb", "true")
        # Sized so the whole row fits at the app's minimum window width
        # (~920 px) without clipping the Resume/Open action on the right.
        self._thumbnail.setFixedSize(168, 94)
        self._thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if info.thumbnail:
            pixmap = QPixmap(info.thumbnail)
            if not pixmap.isNull():
                self._thumbnail.setText("")
                self._thumbnail.setPixmap(pixmap.scaled(
                    self._thumbnail.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation))
        row.addWidget(self._thumbnail)

        identity = QVBoxLayout()
        identity.setSpacing(5)
        identity.addStretch(1)
        title = ElidedLabel(
            info.name + ("  ·  MISSING" if not info.exists else ""))
        title.setProperty("role", "projectTitle")
        title.setWordWrap(False)
        identity.addWidget(title)
        source = ElidedLabel(info.source_name or info.path.name)
        source.setProperty("role", "projectSource")
        source.setWordWrap(False)
        identity.addWidget(source)

        stats = QHBoxLayout()
        stats.setSpacing(14)
        self._add_metric(stats, str(info.clip_count or 0), "CLIPS")
        self._add_metric(stats, str(info.logged_count), "LOGGED")
        self._add_metric(stats, str(info.unlogged), "STILL TO LOG", accent=True)
        stats.addStretch(1)
        identity.addLayout(stats)
        identity.addStretch(1)
        row.addLayout(identity, 2)

        self._progress_panel = QWidget()
        progress = QVBoxLayout(self._progress_panel)
        progress.setContentsMargins(0, 0, 0, 0)
        progress.setSpacing(5)
        progress.addStretch(1)
        progress_label = QLabel(f"{info.progress_pct}% LOGGED")
        progress_label.setProperty("role", "projectMeta")
        progress.addWidget(progress_label)
        bar = QProgressBar()
        bar.setProperty("logging", "true")
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setMinimumWidth(160)
        bar.setMaximum(max(1, info.clip_count or 1))
        bar.setValue(info.logged_count)
        if info.clip_count and info.unlogged == 0:
            bar.setProperty("complete", "true")
        progress.addWidget(bar)
        progress.addStretch(1)
        row.addWidget(self._progress_panel, 2)

        self._updated_panel = QWidget()
        updated = QVBoxLayout(self._updated_panel)
        updated.setContentsMargins(0, 0, 0, 0)
        updated.setSpacing(2)
        updated.addStretch(1)
        updated_label = QLabel("UPDATED")
        updated_label.setProperty("role", "projectMeta")
        updated.addWidget(updated_label)
        modified = QLabel(info.modified or "ready")
        modified.setProperty("role", "subtle")
        updated.addWidget(modified)
        updated.addStretch(1)
        row.addWidget(self._updated_panel)

        actions = QHBoxLayout()
        # "Continue reviewing" says what resuming actually does; a fully logged
        # project just re-opens. Keyboard-focusable so tab order reaches the
        # card's main action (the start screen is not playback-mode).
        self._resume_button = QPushButton(
            "Continue reviewing" if info.unlogged else "Open")
        self._resume_button.setObjectName("ProjectPrimaryAction")
        self._resume_button.setProperty("projectAction", "true")
        self._resume_button.setMinimumWidth(132 if info.unlogged else 102)
        self._resume_button.setAccessibleName(
            f"{self._resume_button.text()} {info.name}")
        if info.exists:
            if info.unlogged:
                self._resume_button.clicked.connect(
                    lambda: self.resume_requested.emit(str(self.info.path)))
            else:
                self._resume_button.clicked.connect(
                    lambda: self.open_requested.emit(str(self.info.path)))
        else:
            self._resume_button.setEnabled(False)
        actions.addWidget(self._resume_button)
        self._overflow_button = QToolButton()
        self._overflow_button.setText("•••")
        self._overflow_button.setObjectName("ProjectOverflowMenu")
        self._overflow_button.setProperty("projectMenu", "true")
        overflow_name = f"Project actions for {info.name}"
        self._overflow_button.setAccessibleName(overflow_name)
        self._overflow_button.setToolTip(overflow_name)
        self._overflow_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._overflow_button.clicked.connect(
            lambda: self.menu_requested.emit(
                str(self.info.path),
                self._overflow_button.mapToGlobal(
                    self._overflow_button.rect().bottomLeft())))
        actions.addWidget(self._overflow_button)
        row.addLayout(actions)
        self._card_density: str | None = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = event.size().width()
        density = "narrow" if width < 900 else "wide"
        if density == self._card_density:
            return
        self._card_density = density
        show_detail_columns = density == "wide"
        self._progress_panel.setVisible(show_detail_columns)
        self._updated_panel.setVisible(show_detail_columns)
        thumb_size = QSize(144, 81) if width < 700 else QSize(168, 94)
        if self._thumbnail.size() != thumb_size:
            source = QPixmap(self.info.thumbnail) if self.info.thumbnail else QPixmap()
            self._thumbnail.setFixedSize(thumb_size)
            if not source.isNull():
                self._thumbnail.setPixmap(source.scaled(
                    thumb_size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                ))

    @staticmethod
    def _add_metric(layout: QHBoxLayout, value: str, label: str, accent: bool = False) -> None:
        metric = QVBoxLayout()
        metric.setSpacing(0)
        number = QLabel(value)
        number.setProperty("role", "metricAccent" if accent else "metricValue")
        metric.addWidget(number)
        caption = QLabel(label)
        caption.setProperty("role", "metricLabel")
        metric.addWidget(caption)
        layout.addLayout(metric)


class WorkflowStep(QWidget):
    def __init__(self, number: str, title: str, body: str, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        badge = QLabel(number)
        badge.setProperty("workflowNumber", "true")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(48, 48)
        row.addWidget(badge)
        copy = QVBoxLayout()
        copy.setSpacing(2)
        title_label = QLabel(title)
        title_label.setProperty("role", "workflowHeading")
        copy.addWidget(title_label)
        description = QLabel(body)
        description.setProperty("role", "subtle")
        description.setWordWrap(True)
        copy.addWidget(description)
        row.addLayout(copy, 1)


class ProjectFilmDropZone(QFrame):
    """File intake target used by the New Project dialog."""

    video_selected = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("NewProjectFilmDropZone")
        self.setProperty("newProjectDropzone", "true")
        self.setAcceptDrops(True)
        self.setMinimumHeight(226)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(7)
        layout.addStretch(1)

        icon = QLabel()
        icon.setObjectName("NewProjectFilmIcon")
        icon.setAccessibleName("Game film file")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        video_icon = _icon_path("tapesift-video.png")
        if video_icon is not None:
            icon.setPixmap(QPixmap(str(video_icon)).scaled(
                48, 48,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            icon.setProperty("iconLibrary", "Segoe Fluent Icons")
            icon.setProperty("iconAsset", "tapesift-video.png")
        else:
            icon.setPixmap(self.style().standardIcon(
                QStyle.StandardPixmap.SP_FileIcon).pixmap(48, 48))
        layout.addWidget(icon)

        heading = QLabel("Choose game film")
        heading.setObjectName("NewProjectFilmHeading")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)

        formats = QLabel(VIDEO_FORMATS)
        formats.setObjectName("NewProjectFilmFormats")
        formats.setProperty("role", "subtle")
        formats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        formats.setWordWrap(True)
        layout.addWidget(formats)

        layout.addSpacing(10)
        self.browse_button = QPushButton("Browse…")
        self.browse_button.setObjectName("NewProjectFilmBrowse")
        self.browse_button.setAccessibleName("Browse for a game film")
        layout.addWidget(
            self.browse_button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    @staticmethod
    def _video_from_event(event) -> Path | None:
        urls = event.mimeData().urls()
        if not urls:
            return None
        path = Path(urls[0].toLocalFile())
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
            return path
        return None

    def _set_dragover(self, active: bool) -> None:
        self.setProperty("dragover", "true" if active else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:
        if self._video_from_event(event) is not None:
            self._set_dragover(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._set_dragover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        self._set_dragover(False)
        path = self._video_from_event(event)
        if path is None:
            return
        self.video_selected.emit(str(path))
        event.acceptProposedAction()


class NewProjectDialog(QDialog):
    """One safe project intake flow matching the standard Iteration 4 layout."""

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setObjectName("V2NewProjectDialog")
        self.setProperty("tapesiftDialog", True)
        self.setWindowTitle("New Project")
        self.setModal(True)
        self.setMinimumSize(820, 520)
        self.resize(880, 545)
        self._video_path: Path | None = None
        self._name_manually_edited = False
        self._output_custom = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(14)

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        header = QVBoxLayout()
        header.setSpacing(3)
        eyebrow = QLabel("NEW PROJECT")
        eyebrow.setProperty("role", "eyebrow")
        header.addWidget(eyebrow)
        title = QLabel("Bring in a game")
        title.setProperty("role", "dialogTitle")
        header.addWidget(title)
        subtitle = QLabel(
            "Your source film stays on your Windows machine. "
            "Optional First Read sends selected frames only when you run it.")
        subtitle.setProperty("role", "subtle")
        subtitle.setWordWrap(True)
        header.addWidget(subtitle)
        header_row.addLayout(header, 1)
        self.close_button = QToolButton()
        self.close_button.setObjectName("NewProjectClose")
        self.close_button.setAccessibleName("Close New Project")
        self.close_button.setToolTip("Close")
        self.close_button.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_TitleBarCloseButton))
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setFixedSize(30, 30)
        self.close_button.clicked.connect(self.reject)
        header_row.addWidget(
            self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header_row)

        rule = QFrame()
        rule.setObjectName("NewProjectHeaderRule")
        rule.setFixedHeight(1)
        outer.addWidget(rule)

        body = QHBoxLayout()
        body.setSpacing(20)
        outer.addLayout(body, 1)

        left = QWidget()
        left.setObjectName("NewProjectFilmColumn")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(9)
        left_layout.addWidget(self._step_label("1. CHOOSE GAME FILM"))
        self.drop_zone = ProjectFilmDropZone()
        self.drop_zone.video_selected.connect(self.set_video_path)
        self.drop_zone.browse_button.clicked.connect(self._browse_video)
        left_layout.addWidget(self.drop_zone, 1)
        self.film_status = QLabel("No film selected")
        self.film_status.setObjectName("NewProjectFilmStatus")
        self.film_status.setProperty("role", "subtle")
        self.film_status.setWordWrap(True)
        left_layout.addWidget(self.film_status)
        body.addWidget(left, 4)

        divider = QFrame()
        divider.setObjectName("NewProjectColumnDivider")
        divider.setFixedWidth(1)
        body.addWidget(divider)

        right = QWidget()
        right.setObjectName("NewProjectDetailsColumn")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(9)
        right_layout.addWidget(
            self._step_label("2. PROJECT DETAILS & STORAGE"))

        self.project_name_edit = QLineEdit()
        self.project_name_edit.setObjectName("NewProjectName")
        self.project_name_edit.setPlaceholderText("Game or project name")
        self.project_name_edit.setAccessibleName("Project name")
        self.project_name_edit.textEdited.connect(self._project_name_edited)
        self.project_name_edit.textChanged.connect(self._validate)
        right_layout.addLayout(self._labeled_field(
            "PROJECT NAME", self.project_name_edit))

        self.project_folder_edit = QLineEdit(
            str(Path(settings.default_project_folder)))
        self.project_folder_edit.setObjectName("NewProjectLocation")
        self.project_folder_edit.setAccessibleName("Project location")
        self.project_folder_edit.textChanged.connect(self._validate)
        self.project_folder_browse = QPushButton("Browse…")
        self.project_folder_browse.setObjectName(
            "NewProjectLocationBrowse")
        self.project_folder_browse.clicked.connect(
            self._browse_project_folder)
        right_layout.addLayout(self._labeled_browse_field(
            "PROJECT LOCATION",
            self.project_folder_edit,
            self.project_folder_browse,
        ))

        self.output_folder_edit = QLineEdit(
            str(Path(settings.default_output_folder)))
        self.output_folder_edit.setObjectName("NewProjectOutputFolder")
        self.output_folder_edit.setAccessibleName("Export folder")
        self.output_folder_edit.textEdited.connect(
            self._output_folder_edited)
        self.output_folder_edit.textChanged.connect(self._validate)
        self.output_folder_browse = QPushButton("Browse…")
        self.output_folder_browse.setObjectName("NewProjectOutputBrowse")
        self.output_folder_browse.clicked.connect(self._browse_output_folder)
        right_layout.addLayout(self._labeled_browse_field(
            "EXPORT FOLDER",
            self.output_folder_edit,
            self.output_folder_browse,
        ))

        privacy = QHBoxLayout()
        privacy.setSpacing(7)
        privacy_icon = QLabel()
        privacy_icon.setObjectName("NewProjectPrivacyIcon")
        privacy_icon.setAccessibleName("Local-only workspace")
        privacy_icon.setPixmap(self.style().standardIcon(
            QStyle.StandardPixmap.SP_DialogApplyButton).pixmap(14, 14))
        privacy.addWidget(privacy_icon)
        privacy_text = QLabel(
            "Local-only workspace. Your film stays on this Windows device.")
        privacy_text.setObjectName("NewProjectPrivacyText")
        privacy_text.setProperty("role", "subtle")
        privacy_text.setWordWrap(True)
        privacy.addWidget(privacy_text, 1)
        right_layout.addLayout(privacy)

        self.validation_label = QLabel("")
        self.validation_label.setObjectName("NewProjectValidation")
        self.validation_label.setProperty("role", "error")
        self.validation_label.setWordWrap(True)
        right_layout.addWidget(self.validation_label)
        right_layout.addStretch(1)
        body.addWidget(right, 5)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        footer.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("NewProjectCancel")
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        self.create_button = QPushButton("Create project")
        self.create_button.setObjectName("NewProjectCreate")
        self.create_button.setProperty("primary", "true")
        self.create_button.setDefault(True)
        self.create_button.clicked.connect(self.accept)
        footer.addWidget(self.create_button)
        outer.addLayout(footer)
        self.project_folder_edit.setCursorPosition(0)
        self.output_folder_edit.setCursorPosition(0)
        self._validate()

    @staticmethod
    def _step_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "newProjectStep")
        return label

    @staticmethod
    def _labeled_field(label_text: str, field: QWidget) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(4)
        label = QLabel(label_text)
        label.setProperty("role", "newProjectFieldLabel")
        layout.addWidget(label)
        layout.addWidget(field)
        return layout

    @classmethod
    def _labeled_browse_field(
            cls, label_text: str, field: QWidget,
            browse: QPushButton) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(4)
        label = QLabel(label_text)
        label.setProperty("role", "newProjectFieldLabel")
        layout.addWidget(label)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(field, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        return layout

    def _browse_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose game film",
            "",
            "Video files (*.mp4 *.mov *.mkv *.avi *.m4v *.webm);;"
            "All files (*)",
        )
        if path:
            self.set_video_path(path)

    def set_video_path(self, value: str | Path) -> None:
        path = Path(value)
        if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
            self._video_path = None
            self.film_status.setText(
                "Choose an MP4, MOV, MKV, AVI, M4V, or WebM game film.")
            self.film_status.setProperty("state", "error")
            self._refresh(self.film_status)
            self._validate()
            return
        self._video_path = path
        self.film_status.setText(f"Selected: {path.name}")
        self.film_status.setToolTip(str(path))
        self.film_status.setProperty("state", "selected")
        self._refresh(self.film_status)
        if not self._name_manually_edited:
            self.project_name_edit.setText(path.stem)
        self._sync_output_folder()
        self._validate()

    def _project_name_edited(self, _text: str) -> None:
        self._name_manually_edited = True
        self._sync_output_folder()

    def _output_folder_edited(self, _text: str) -> None:
        self._output_custom = True

    def _sync_output_folder(self) -> None:
        if self._output_custom:
            return
        name = self.project_name_edit.text().strip()
        base = Path(self.settings.default_output_folder)
        self.output_folder_edit.setText(str(base / name) if name else str(base))
        self.output_folder_edit.setCursorPosition(0)

    def _browse_project_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose where to save the project",
            self.project_folder_edit.text().strip(),
        )
        if folder:
            self.project_folder_edit.setText(folder)
            self.project_folder_edit.setCursorPosition(0)

    def _browse_output_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose the export output folder",
            self.output_folder_edit.text().strip(),
        )
        if folder:
            self._output_custom = True
            self.output_folder_edit.setText(folder)
            self.output_folder_edit.setCursorPosition(0)

    def _validate(self) -> None:
        message = ""
        if self._video_path is None:
            message = "Choose a game film to continue."
        elif not self.project_name_edit.text().strip():
            message = "Enter a project name."
        elif not self.project_folder_edit.text().strip():
            message = "Choose a project location."
        elif not self.output_folder_edit.text().strip():
            message = "Choose an export folder."
        self.validation_label.setText(message)
        self.validation_label.setVisible(bool(message))
        self.create_button.setEnabled(not message)

    @staticmethod
    def _refresh(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    @property
    def video_path(self) -> Path | None:
        return self._video_path

    @property
    def project_name(self) -> str:
        return self.project_name_edit.text().strip()

    @property
    def project_folder(self) -> str:
        return self.project_folder_edit.text().strip()

    @property
    def output_folder(self) -> str:
        return self.output_folder_edit.text().strip()


class StartScreenV2(StartScreen):
    """A functional command-desk homepage matching the selected layout."""

    # Emitted by the drop-zone error page (see the state machine below).
    retry_video_requested = Signal()
    open_logs_requested = Signal()
    new_project_with_video_requested = Signal(str, str, str, str)

    # Below this window width the two columns stack instead of sitting side
    # by side. 1200 leaves the player-panel headroom the app already needs.
    BREAKPOINT_PX = 1200

    def __init__(self, settings: AppSettings, parent=None) -> None:
        QWidget.__init__(self, parent)
        self.settings = settings
        self.setObjectName("V2StartScreen")
        self._load_state = "idle"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(30, 18, 30, 14)
        outer.setSpacing(10)
        self._outer_layout = outer
        self._masthead_layout = self._build_header()
        outer.addLayout(self._masthead_layout)
        # The selected direction gives the masthead and Film Room statement
        # distinct breathing room before the working dashboard begins.
        self._masthead_gap = QSpacerItem(
            0, 18, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        outer.addItem(self._masthead_gap)
        outer.addWidget(self._build_statement())
        outer.addSpacing(10)

        # Responsive body: a start column (drop zone) and a recent column.
        # Wide windows lay them side by side; narrow windows stack them. When
        # projects exist (a returning user), Recent leads - resume-first.
        self._body = QWidget()
        self._body_layout = QBoxLayout(
            QBoxLayout.Direction.LeftToRight, self._body)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(18)
        self._start_col = self._build_start_column()
        self._recent_col = self._build_recent_column()
        self._body_layout.addWidget(self._start_col, 2)
        self._body_layout.addWidget(self._recent_col, 3)
        outer.addWidget(self._body, 1)

        # The workflow rail is footer context, shown only when there is room.
        self._workflow = QWidget()
        wf = QVBoxLayout(self._workflow)
        wf.setContentsMargins(0, 0, 0, 0)
        wf.setSpacing(10)
        workflow_rule = QFrame()
        workflow_rule.setProperty("homeRule", "true")
        workflow_rule.setFixedHeight(1)
        wf.addWidget(workflow_rule)
        wf.addLayout(self._build_workflow())
        outer.addWidget(self._workflow)

        footer_rule = QFrame()
        footer_rule.setProperty("homeRule", "true")
        footer_rule.setFixedHeight(1)
        outer.addWidget(footer_rule)

        footer = QHBoxLayout()
        footer.setSpacing(12)
        status = QLabel("•  LOCAL-ONLY WORKSPACE")
        status.setProperty("role", "localStatus")
        footer.addWidget(status)
        footer.addStretch(1)
        build = QLabel(f"TapeSift v{__version__}  •  Your film. Your edge.")
        build.setObjectName("HomeBuildLabel")
        build.setProperty("role", "subtle")
        footer.addWidget(build)
        outer.addLayout(footer)

        self._layout_key: tuple[bool, bool] | None = None
        self.refresh_recent()

    def _build_start_column(self) -> QWidget:
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self._build_drop_zone())
        lay.addWidget(
            self._build_open_project_link(),
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        lay.addStretch(1)
        return col

    def _new_project(self) -> None:
        """Open the standard one-surface project intake workflow."""
        dialog = NewProjectDialog(self.settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        video_path = dialog.video_path
        if video_path is None:
            return
        self.new_project_with_video_requested.emit(
            dialog.project_name,
            dialog.project_folder,
            dialog.output_folder,
            str(video_path),
        )

    def _build_recent_column(self) -> QWidget:
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(9)

        recent_header = QHBoxLayout()
        recent_header.setSpacing(10)
        recent_title = QLabel("RECENT PROJECTS")
        recent_title.setProperty("role", "queueHeading")
        recent_header.addWidget(recent_title)
        self.summary_label = QLabel("")
        self.summary_label.setProperty("badge", "true")
        recent_header.addWidget(self.summary_label)
        recent_header.addStretch(1)
        all_btn = QPushButton("View all projects  ›")
        all_btn.setProperty("quiet", "true")
        all_btn.clicked.connect(self._show_all_projects)
        recent_header.addWidget(all_btn)
        lay.addLayout(recent_header)

        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.cards_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cards_scroll.setSizeAdjustPolicy(
            QScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.cards_scroll.setMinimumWidth(0)
        self.cards_scroll.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.cards_host = QWidget()
        self.cards_host.setMinimumWidth(0)
        self.cards_host.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.cards_layout = QGridLayout(self.cards_host)
        self.cards_layout.setContentsMargins(0, 0, 4, 0)
        self.cards_layout.setSpacing(7)
        self.cards_layout.setColumnStretch(0, 1)
        self.cards_scroll.setWidget(self.cards_host)
        lay.addWidget(self.cards_scroll, 1)

        self.empty_recent = self._build_empty_state()
        lay.addWidget(self.empty_recent, 1)
        return col

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_responsive()

    def _apply_responsive(self) -> None:
        """Flip the body between side-by-side and stacked, resume-first."""
        wide = self.width() >= self.BREAKPOINT_PX
        has_projects = bool(self.settings.recent_projects)
        key = (wide, has_projects)
        if key == self._layout_key:
            return
        self._layout_key = key

        # Two columns only make sense for a returning user on a wide window -
        # there needs to be something to resume on the right. First run stays
        # a single column with a prominent drop zone.
        two_column = wide and has_projects
        self._recent_col.setVisible(has_projects)
        self._body_layout.setSpacing(44 if two_column else 18)
        self._body_layout.setDirection(
            QBoxLayout.Direction.LeftToRight if two_column
            else QBoxLayout.Direction.TopToBottom)
        self._body_layout.removeWidget(self._start_col)
        self._body_layout.removeWidget(self._recent_col)
        if two_column:
            # The drop zone is a compact sidebar so the recent column keeps the
            # width the full project card needs - resume-first, no clipping.
            self._start_col.setMinimumWidth(378)
            self._start_col.setMaximumWidth(400)
            self._body_layout.addWidget(self._start_col, 0)
            self._body_layout.addWidget(self._recent_col, 1)
        elif has_projects:
            # Stacked, returning user: Recent on top, drop zone below.
            self._start_col.setMinimumWidth(0)
            self._start_col.setMaximumWidth(_UNBOUNDED)
            self._body_layout.addWidget(self._recent_col, 1)
            self._body_layout.addWidget(self._start_col, 0)
        else:
            # Stacked, first run: the drop zone is the whole story.
            self._start_col.setMinimumWidth(0)
            self._start_col.setMaximumWidth(_UNBOUNDED)
            self._body_layout.addWidget(self._start_col, 0)

        # The workflow rail only earns space on a wide window.
        self._workflow.setVisible(wide)

    def _set_statement_mode(self, has_projects: bool) -> None:
        """The Film Room headline reflects whether there is work to resume."""
        self._room_title.setText(
            "Resume the latest breakdown" if has_projects
            else "Start your first breakdown")

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setSpacing(12)
        self.home_brand_lockup = _brand_image_label(
            "tapesift-logo.png",
            31,
            object_name="HomeBrandLockup",
        )
        if self.home_brand_lockup is not None:
            header.addWidget(self.home_brand_lockup)
        self.home_brand_divider = QFrame()
        self.home_brand_divider.setObjectName("HomeBrandDivider")
        self.home_brand_divider.setFrameShape(QFrame.Shape.VLine)
        self.home_brand_divider.setFixedSize(1, 26)
        header.addWidget(self.home_brand_divider)
        self.home_section_label = QLabel("HOME")
        self.home_section_label.setObjectName("HomeSectionLabel")
        self.home_section_label.setProperty("role", "eyebrow")
        self.home_section_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        header.addWidget(self.home_section_label)
        header.addStretch(1)
        self.library_button = QPushButton("Search Library")
        # Searching every clip you have ever cut is the thing that separates
        # this from a clipping tool, so it does not sit at the same weight as
        # Settings. A familiar search glyph makes that action obvious without
        # repeating the product mark inside the product header.
        self.library_button.setProperty("libraryEntry", "true")
        self.library_button.setMinimumHeight(32)
        library_icon = _icon_path("tapesift-search.png")
        if library_icon:
            self.library_button.setIcon(QIcon(str(library_icon)))
            self.library_button.setIconSize(QSize(16, 16))
            self.library_button.setProperty(
                "iconLibrary", "Segoe Fluent Icons")
            self.library_button.setProperty(
                "iconAsset", "tapesift-search.png")
        self.library_button.clicked.connect(self.library_requested.emit)

        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("ApplicationSettingsButton")
        self.settings_button.setProperty("quiet", "true")
        settings_icon = _icon_path("tapesift-settings.png")
        if settings_icon:
            self.settings_button.setIcon(QIcon(str(settings_icon)))
            self.settings_button.setIconSize(QSize(16, 16))
            self.settings_button.setProperty(
                "iconLibrary", "Segoe Fluent Icons")
            self.settings_button.setProperty(
                "iconAsset", "tapesift-settings.png")
        self.settings_button.clicked.connect(self.settings_requested.emit)
        header.addWidget(self.library_button)
        header.addWidget(self.settings_button)
        return header

    def move_masthead_to(self, shell) -> None:
        """Move the live Home identity/actions into the shared app shell."""
        if self.home_brand_lockup is not None:
            shell.add_mode_left_widget("home", self.home_brand_lockup)
        shell.add_mode_left_widget("home", self.home_brand_divider)
        shell.add_mode_left_widget("home", self.home_section_label)
        shell.add_mode_left_stretch("home")
        shell.add_mode_right_widget("home", self.library_button)
        shell.add_mode_right_widget("home", self.settings_button)
        self._masthead_gap.changeSize(
            0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._outer_layout.invalidate()

    def _build_statement(self) -> QWidget:
        """The Film Room framing. Its headline adapts to whether there are
        projects to resume (see _set_statement_mode)."""
        widget = QWidget()
        col = QVBoxLayout(widget)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)

        room = QVBoxLayout()
        room.setContentsMargins(0, 0, 0, 0)
        room.setSpacing(4)          # air between the eyebrow and the headline
        eyebrow = QLabel("FILM ROOM")
        eyebrow.setProperty("role", "eyebrow")
        room.addWidget(eyebrow)
        greeting = QLabel(f"Welcome back, {AUTHOR.split()[0]}.")
        greeting.setObjectName("HomeWelcomeTitle")
        greeting.setProperty("role", "roomTitle")
        room.addWidget(greeting)
        self._room_title = QLabel("Resume the latest breakdown")
        self._room_title.setProperty("role", "roomSubtitle")
        room.addWidget(self._room_title)
        col.addLayout(room)

        col.addSpacing(6)           # and above the flags row
        flags = QHBoxLayout()
        flags.setSpacing(10)
        for index, text in enumerate(("WINDOWS DESKTOP", "RUNS LOCALLY", "YOUR FILM STAYS HERE")):
            label = QLabel(text)
            label.setProperty("role", "eyebrow" if index < 2 else "projectMeta")
            flags.addWidget(label)
            if index < 2:
                dot = QLabel("•")
                dot.setProperty("role", "eyebrow")
                flags.addWidget(dot)
        flags.addStretch(1)
        col.addLayout(flags)
        return widget

    def _build_open_project_link(self) -> QPushButton:
        """A secondary file action, intentionally lighter than New project."""
        button = QPushButton("Open an existing project…")
        button.setObjectName("HomeOpenProject")
        button.setProperty("homeOpenLink", "true")
        button.setAccessibleName("Open an existing TapeSift project")
        button.setToolTip("Open an existing TapeSift project")
        button.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_DirOpenIcon))
        button.setIconSize(QSize(18, 18))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFixedWidth(220)
        button.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        button.clicked.connect(self._open_project)
        return button

    def _build_drop_zone(self) -> QFrame:
        # Stacked vertically so it reads whether it is a wide first-run bar or
        # the narrow sidebar in the two-column returning layout. The frame
        # carries three pages - default / loading / error - shown one at a
        # time via set_load_state(); only the default page is built here.
        self.drop_zone = QFrame()
        self.drop_zone.setProperty("homeDropzone", "true")
        self.drop_zone.setMinimumHeight(212)
        outer = QVBoxLayout(self.drop_zone)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(0)

        self._drop_default = QWidget()
        col = QVBoxLayout(self._drop_default)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addStretch(1)
        icon = QLabel()
        icon.setPixmap(self.style().standardIcon(
            QStyle.StandardPixmap.SP_ArrowDown).pixmap(28, 28))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(icon)
        self._drop_heading = QLabel("DROP A GAME FILM HERE")
        self._drop_heading.setProperty("role", "dropHeading")
        self._drop_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop_heading.setWordWrap(True)
        col.addWidget(self._drop_heading)
        formats = QLabel(VIDEO_FORMATS)
        formats.setProperty("role", "subtle")
        formats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        formats.setWordWrap(True)
        col.addWidget(formats)
        col.addSpacing(12)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        new_btn = QPushButton("New project")
        new_btn.setObjectName("HomeNewProject")
        new_btn.setProperty("primary", "true")
        new_btn.clicked.connect(self._new_project)
        actions.addWidget(new_btn)
        actions.addStretch(1)
        col.addLayout(actions)
        col.addStretch(1)
        outer.addWidget(self._drop_default)

        self._drop_status = self._build_drop_status()
        self._drop_status.hide()
        outer.addWidget(self._drop_status)
        return self.drop_zone

    def _build_drop_status(self) -> QWidget:
        """The loading / error page shown over the drop zone during open."""
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addStretch(1)

        self._status_title = QLabel("")
        self._status_title.setObjectName("HomeDropStatusTitle")
        self._status_title.setProperty("role", "dropHeading")
        self._status_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_title.setWordWrap(True)
        col.addWidget(self._status_title)

        self._status_detail = QLabel("")
        self._status_detail.setProperty("role", "subtle")
        self._status_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_detail.setWordWrap(True)
        col.addWidget(self._status_detail)

        self._status_bar = QProgressBar()
        self._status_bar.setProperty("logging", "true")
        self._status_bar.setTextVisible(False)
        self._status_bar.setFixedHeight(6)
        self._status_bar.setRange(0, 0)   # indeterminate "busy" while reading
        col.addWidget(self._status_bar)

        col.addSpacing(10)
        self._status_actions = QHBoxLayout()
        self._status_actions.setSpacing(8)
        self._status_actions.addStretch(1)
        self._retry_btn = QPushButton("Choose another video")
        self._retry_btn.setObjectName("HomeDropRetry")
        self._retry_btn.setProperty("primary", "true")
        self._retry_btn.clicked.connect(self.retry_video_requested.emit)
        self._status_actions.addWidget(self._retry_btn)
        self._logs_btn = QPushButton("Open logs")
        self._logs_btn.setObjectName("HomeDropOpenLogs")
        self._logs_btn.clicked.connect(self.open_logs_requested.emit)
        self._status_actions.addWidget(self._logs_btn)
        self._status_actions.addStretch(1)
        col.addLayout(self._status_actions)
        col.addStretch(1)
        return page

    # ---------- drop-zone state machine ----------

    def set_drag_active(self, active: bool) -> None:
        # "Release to start" is a stronger affordance than a border tint alone.
        super().set_drag_active(active)
        if self._load_state in ("idle", "dragover"):
            self._load_state = "dragover" if active else "idle"
            self._drop_heading.setText(
                "RELEASE TO START A PROJECT" if active
                else "DROP A GAME FILM HERE")

    def begin_loading(self, filename: str) -> None:
        self._load_state = "loading"
        self._status_title.setText(f"Reading {filename}")
        self._status_detail.setText(
            "Checking duration, frame rate and codecs…")
        self._status_bar.show()
        self._retry_btn.hide()
        self._logs_btn.hide()
        self._drop_default.hide()
        self._drop_status.show()

    def show_load_error(self, filename: str, message: str) -> None:
        self._load_state = "error"
        self._status_title.setText(f"Couldn't open {filename}")
        self._status_detail.setText(
            message or "The file could not be read. Choose another video or "
            "review the application log.")
        self._status_bar.hide()
        self._retry_btn.show()
        self._logs_btn.show()
        self._drop_default.hide()
        self._drop_status.show()

    def clear_load_state(self) -> None:
        self._load_state = "idle"
        self._drop_heading.setText("DROP A GAME FILM HERE")
        self._drop_status.hide()
        self._drop_default.show()

    def _build_workflow(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(38, 6, 38, 7)
        row.setSpacing(14)
        steps = (
            ("01", "DETECT PLAYS", "Find each play from the gaps in your film."),
            ("02", "REVIEW & LOG", "Add player, result, formation, and tags."),
            ("03", "EXPORT CUTUPS", "Create individual clips or a combined reel."),
        )
        for index, step in enumerate(steps):
            row.addWidget(WorkflowStep(*step), 1)
            if index < len(steps) - 1:
                connector = QFrame()
                connector.setProperty("workflowConnector", "true")
                connector.setFixedHeight(1)
                connector.setMinimumWidth(80)
                row.addWidget(connector)
        return row

    def _build_empty_state(self) -> QWidget:
        empty = QWidget()
        layout = QVBoxLayout(empty)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("NO PROJECTS YET")
        title.setProperty("role", "queueHeading")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignCenter)
        body = QLabel("Drop a game film above, or create your first project.")
        body.setProperty("role", "subtle")
        layout.addWidget(body, 0, Qt.AlignmentFlag.AlignCenter)
        button = QPushButton("New project")
        button.setProperty("primary", "true")
        button.clicked.connect(self._new_project)
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)
        return empty

    def _build_add_project_row(self) -> QFrame:
        card = QFrame()
        card.setProperty("addProjectRow", "true")
        card.setMinimumHeight(76)
        row = QHBoxLayout(card)
        row.setContentsMargins(18, 12, 18, 12)
        row.setSpacing(16)
        add = QLabel("+")
        add.setProperty("role", "addMark")
        add.setAlignment(Qt.AlignmentFlag.AlignCenter)
        add.setFixedSize(56, 50)
        row.addWidget(add)
        copy = QVBoxLayout()
        copy.setSpacing(2)
        title = QLabel("Bring in another game")
        title.setProperty("role", "projectTitle")
        copy.addWidget(title)
        body = QLabel("Start a separate project, or drop a film above.")
        body.setProperty("role", "subtle")
        copy.addWidget(body)
        row.addLayout(copy, 1)
        button = QPushButton("New project")
        button.setObjectName("RecentNewProject")
        button.clicked.connect(self._new_project)
        row.addWidget(button)
        return card

    def refresh_recent(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        # A recent card pointing at a file that no longer exists can never
        # open. Leaving it on the home screen makes "opening projects is
        # unreliable" the obvious reading, when the real story is that the
        # file moved or was deleted.
        if self.settings.prune_missing_recents():
            self.settings.save()

        infos = [load_project_info(Path(path)) for path in self.settings.recent_projects]
        self.empty_recent.setVisible(not infos)
        self.cards_scroll.setVisible(bool(infos))
        for row, info in enumerate(infos):
            self.cards_layout.addWidget(self._make_card(info), row, 0)
        if infos:
            self.cards_layout.addWidget(self._build_add_project_row(), len(infos), 0)
            self.cards_layout.setRowStretch(len(infos) + 1, 1)

        total = sum(info.clip_count or 0 for info in infos)
        unlogged = sum(info.unlogged for info in infos)
        self.summary_label.setText(
            f"{total} CLIPS  •  {unlogged} TO LOG" if total else "")
        self.summary_label.setVisible(bool(total))

        # Statement framing and column order both depend on whether projects
        # exist, so re-evaluate the responsive layout after the list changes.
        self._set_statement_mode(bool(infos))
        self._layout_key = None
        self._apply_responsive()

    def _make_card(self, info: ProjectInfo) -> ProjectCardV2:
        card = ProjectCardV2(info)
        card.open_requested.connect(self.open_project_requested.emit)
        card.resume_requested.connect(self.resume_project_requested.emit)
        card.menu_requested.connect(self._card_menu)
        return card
