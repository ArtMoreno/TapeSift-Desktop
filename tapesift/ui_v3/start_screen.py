"""Shell V3 Home surface built around the existing project authorities."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
    QGraphicsColorizeEffect, QGraphicsOpacityEffect, QSizePolicy, QStyle,
    QVBoxLayout, QWidget, QProgressBar, QScrollArea,
)

from tapesift.ui_core.start_screen import ProjectInfo, VIDEO_FORMATS
from tapesift.ui_v3.icons import brand_pixmap, tinted_icon
from tapesift.ui_v2.start_screen import (
    VIDEO_EXTENSIONS,
    NewProjectDialog as NewProjectDialogV2,
    ProjectCardV2,
    StartScreenV2,
    _icon_path,
)


PROJECT_EXTENSION = ".tapesift"
_UNBOUNDED = 16777215


class ResumeCardV3(ProjectCardV2):
    """Real project actions over the user's cached film, without card chrome."""

    def __init__(self, info, parent=None, *, featured=False):
        QFrame.__init__(self, parent)
        self.info, self.featured = info, featured
        self.setObjectName("V3CinematicProject")
        self.setProperty("featured", featured)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setToolTip(str(info.path))
        self._source = QPixmap(info.thumbnail) if info.thumbnail else QPixmap()
        self._title_label = QLabel(info.name + (" · Missing" if not info.exists else ""), self)
        self._title_label.setWordWrap(True)
        self._title_label.setTextFormat(Qt.TextFormat.PlainText)
        self._counts = QLabel(f"{info.logged_count} of {info.clip_count if info.clip_count is not None else '—'} plays logged", self)
        self._counts.setStyleSheet("color:#c6c4bd;font:18px 'Segoe UI';background:transparent;")
        self._resume_button = QPushButton("Open project" if featured else "", self)
        self._resume_button.setObjectName("V3FilmOpen")
        self._resume_button.setProperty("filmPrimary", featured)
        self._resume_button.setIcon(tinted_icon("arrow-right-24.svg", "#161610" if featured else "#eeeade", 24))
        self._resume_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._resume_button.setIconSize(QSize(24, 24))
        self._resume_button.setAccessibleName(f"{'Continue reviewing' if info.unlogged else 'Open'} {info.name}")
        self._resume_button.setEnabled(info.exists)
        self._resume_button.clicked.connect(lambda: (self.resume_requested if info.unlogged else self.open_requested).emit(str(info.path)))
        self._overflow_button = QPushButton(self)
        self._overflow_button.setIcon(tinted_icon("more-horizontal-16.svg", "#c6c4bd"))
        self._overflow_button.setFixedSize(24, 24)
        self._overflow_button.setObjectName("V3FilmOverflow")
        self._overflow_button.setAccessibleName(f"Project actions for {info.name}")
        self._overflow_button.clicked.connect(lambda: self.menu_requested.emit(str(info.path), self._overflow_button.mapToGlobal(self._overflow_button.rect().bottomLeft())))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if featured:
            self._eyebrow = QLabel("LAST OPENED", self)
            self._eyebrow.setStyleSheet("color:#bba36a;font:16px 'Segoe UI';background:transparent;")
            self._resume_button.setFixedSize(212, 50)
            self._thumbnail = QLabel("Preview unavailable", self)
            self._thumbnail.setStyleSheet("color:#aaa79d;background:transparent;font:16px 'Segoe UI';")
            self._thumbnail.setVisible(self._source.isNull())
            from tapesift.ui_v3.home_preview import HomePreviewLoader
            self._preview_loader = HomePreviewLoader(self)
            self._preview_loader.ready.connect(self._set_preview)
        else:
            self._title_label.setStyleSheet("color:#eeeade;background:transparent;font:21px 'Segoe UI';")
            self._counts.setStyleSheet("color:#9e9c95;background:transparent;font:16px 'Segoe UI';")
            row = QHBoxLayout()
            row.setSpacing(26)
            self._thumbnail = QLabel("Preview unavailable")
            self._thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._thumbnail.setStyleSheet("color:#a19d92;background:#171714;border:0;font:12px 'Segoe UI';")
            self._thumbnail.setFixedSize(280, 110)
            row.addWidget(self._thumbnail)
            details = QVBoxLayout()
            details.setSpacing(10)
            details.addStretch()
            details.addWidget(self._title_label)
            details.addWidget(self._counts)
            details.addStretch()
            row.addLayout(details, 1)
            self._resume_button.setFixedSize(44, 44)
            row.addWidget(self._resume_button)
            layout.addLayout(row)
            layout.setContentsMargins(0, 18, 16, 18)

    def _set_preview(self, pixmap):
        self._source = pixmap
        self._thumbnail.hide()
        self.update()

    def resizeEvent(self, event):
        QFrame.resizeEvent(self, event)
        if self.featured:
            gutter = max(32, round(self.width() * .034))
            font_size = max(32, min(64, round(self.width() * .0375)))
            self._title_label.setStyleSheet(f"color:#f3f0e6;background:transparent;font:700 {font_size}px 'Segoe UI';")
            text_width = self.width() - 2 * gutter - 240
            title_height = self._title_label.heightForWidth(text_width)
            title_height = max(font_size + 12, title_height)
            bottom = self.height() - 28
            self._counts.setGeometry(gutter, bottom - 30, text_width, 28)
            self._title_label.setGeometry(gutter, bottom - 40 - title_height, text_width, title_height)
            self._eyebrow.setGeometry(gutter, self._title_label.y() - 34, text_width, 25)
            self._resume_button.move(self.width() - gutter - self._resume_button.width(), bottom - 50)
            self._thumbnail.setGeometry(gutter, 36, 300, 28)
            self._overflow_button.move(self.width() - gutter - 24, 20)
        else:
            self._thumbnail.setFixedWidth(max(140, min(290, round(self.width() * .39))))
            if not self._source.isNull():
                size = self._thumbnail.size()
                pix = self._source.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
                self._thumbnail.setPixmap(pix.copy((pix.width()-size.width())//2, (pix.height()-size.height())//2, size.width(), size.height()))
            self._overflow_button.move(self.width() - 28, 0)

    def paintEvent(self, event):
        if self.featured:
            from tapesift.ui_v3.home_surface import paint_film_hero
            paint_film_hero(self, self._source)
        else:
            QFrame.paintEvent(self, event)


class NewProjectDialog(NewProjectDialogV2):
    """Pagebook intake, retaining the shared fields and validation."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setProperty("shellV3Dialog", "true")
        outer = self.layout()
        outer.setContentsMargins(45, 30, 35, 24)
        outer.setSpacing(26)
        header = outer.itemAt(0).layout().itemAt(0).layout()
        header.itemAt(0).widget().hide()
        title = header.itemAt(1).widget()
        title.setText("New project")
        title.setFixedHeight(62)
        subtitle = header.itemAt(2).widget()
        subtitle.setText(
            "Create a new project to detect and cut all-22 football film.")
        subtitle.setFixedHeight(32)
        self.close_button.hide()  # The native window already has Close.
        self.findChild(QFrame, "NewProjectHeaderRule").hide()

        body = outer.itemAt(2).layout()
        body.setSpacing(28)
        body.setStretch(0, 43)
        body.setStretch(2, 57)
        left = self.findChild(QWidget, "NewProjectFilmColumn").layout()
        right = self.findChild(QWidget, "NewProjectDetailsColumn").layout()
        left.itemAt(0).widget().setText("CHOOSE GAME FILM")
        right.itemAt(0).widget().setText("PROJECT DETAILS")
        right.setSpacing(32)
        right.itemAt(0).widget().setContentsMargins(0, 0, 0, 6)
        for index in (1, 2, 3):
            right.itemAt(index).layout().setSpacing(8)
        self.drop_zone.setMinimumHeight(250)
        self.drop_zone.layout().setContentsMargins(8, 16, 8, 16)
        self.drop_zone.layout().setSpacing(16)
        self.drop_zone.layout().setStretch(self.drop_zone.layout().count() - 1, 3)
        self._film_heading = self.findChild(QLabel, "NewProjectFilmHeading")
        self._film_heading.setWordWrap(True)
        camera = self.findChild(QLabel, "NewProjectFilmIcon")
        camera.setPixmap(QIcon(str(_icon_path("tapesift-video.png"))).pixmap(64, 64))
        self._film_heading.setMinimumWidth(0)
        self.drop_zone.browse_button.setFixedWidth(216)
        self.drop_zone.layout().setAlignment(
            self.drop_zone.browse_button, Qt.AlignmentFlag.AlignHCenter)

        # Move the existing local-workspace note into the standard footer.
        privacy = right.takeAt(4).layout()
        self.findChild(QLabel, "NewProjectPrivacyText").setText(
            "Local workspace · Source film stays unchanged")
        footer = outer.itemAt(3).layout()
        footer.insertLayout(0, privacy, 1)
        footer.takeAt(1)
        rule = QFrame()
        rule.setObjectName("NewProjectFooterRule")
        rule.setFixedHeight(1)
        outer.insertWidget(3, rule)
        self.cancel_button.setFixedWidth(142)
        self.create_button.setFixedWidth(176)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.create_button.setAutoDefault(True)
        self.create_button.setDefault(True)
        self.drop_zone.browse_button.setIcon(tinted_icon("pencil-24.svg"))
        self.drop_zone.browse_button.setIconSize(QSize(20, 20))
        for button in (self.project_folder_browse, self.output_folder_browse):
            button.setIcon(tinted_icon("folder-open-24.svg", "#e6e8e6"))
            button.setIconSize(QSize(20, 20))
            button.setFixedWidth(120)
        self.setMinimumSize(840, 540)
        self.resize(960, 680)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # Windows may rescale an unshown dialog when its parent is on another
        # DPI screen. Fit after native placement, using logical coordinates.
        QTimer.singleShot(0, self, self._fit_available_screen)

    def _fit_available_screen(self) -> None:
        area = self.screen().availableGeometry()
        compact = area.height() < 760
        self.setProperty("compactIntake", "true" if compact else "false")
        outer = self.layout()
        outer.setContentsMargins(32, 18, 32, 18) if compact else outer.setContentsMargins(45, 30, 35, 24)
        outer.setSpacing(16 if compact else 26)
        header = outer.itemAt(0).layout().itemAt(0).layout()
        header.itemAt(1).widget().setFixedHeight(48 if compact else 62)
        header.itemAt(2).widget().setFixedHeight(26 if compact else 32)
        self.findChild(QWidget, "NewProjectDetailsColumn").layout().setSpacing(14 if compact else 32)
        for widget in [self, *self.findChildren(QWidget)]:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.resize(min(960, area.width() - 32), min(680, area.height() - 56))
        frame = self.frameGeometry()
        self.move(area.center() - QPoint(frame.width() // 2, frame.height() // 2))

    def set_video_path(self, value) -> None:
        super().set_video_path(value)
        selected = self.video_path
        self._film_heading.setText(selected.name if selected else "Choose game film")
        self._film_heading.setToolTip(str(selected) if selected else "")
        self.drop_zone.browse_button.setText("Change film" if selected else "Browse film…")
        self.film_status.setVisible(selected is None)


class ProjectCardV3(ProjectCardV2):
    """Spacious film rows with the existing open, resume and overflow actions."""

    def __init__(self, info: ProjectInfo, parent=None) -> None:
        super().__init__(info, parent)
        self._row = self.layout()
        self._identity = self._row.itemAt(1).layout()
        metrics = self._identity.takeAt(3).layout()
        for label in self.findChildren(QLabel):
            if label.property("role") in {"metricValue", "metricAccent", "metricLabel"}:
                label.hide()
        metrics.deleteLater()
        total = str(info.clip_count) if info.clip_count is not None else "—"
        self._counts = QLabel(
            f'<span style="color:#39e07a">{total} plays</span>'
            f'  /  {info.logged_count} logged')
        self._counts.setObjectName("V3HomeProjectCounts")
        self._counts.setAccessibleName(f"{total} plays, {info.logged_count} logged")
        self._counts.setToolTip(f"{info.unlogged} plays still to log")
        self._identity.insertWidget(3, self._counts)
        self._identity.setSpacing(9)
        self._identity.setContentsMargins(0, 0, 0, 24)
        self._updated_panel.hide()
        self._progress_label = self._progress_panel.findChild(QLabel)
        self._progress_label.setText(f"{info.progress_pct}% logged")
        bar = self._progress_panel.findChild(QProgressBar)
        bar.setFixedHeight(5)
        bar.setAccessibleName(f"{info.logged_count} of {total} plays logged")
        progress = self._progress_panel.layout()
        progress.removeWidget(bar)
        progress.removeWidget(self._progress_label)
        while progress.count():
            progress.takeAt(0)
        line = QHBoxLayout()
        line.setSpacing(14)
        line.addWidget(bar, 1)
        line.addWidget(self._progress_label)
        progress.addLayout(line)
        self._progress_bar = bar
        self._row.setStretch(1, 1)
        self._row.setStretch(2, 0)
        self._row.itemAt(4).layout().setSpacing(16)
        self._resume_button.setFixedSize(156, 40)
        self._overflow_button.setFixedSize(30, 30)
        self._title_label = next(
            label for label in self.findChildren(QLabel)
            if label.property("role") == "projectTitle")
        self._compact_progress = False
        self._apply_v3_thumbnail_geometry()

    def _apply_v3_thumbnail_geometry(self) -> None:
        compact = self.width() < 1000
        stacked_progress = self.width() < 880
        size = QSize(192, 114) if compact else QSize(296, 176)
        self.setFixedHeight(160 if compact else 215)
        self._identity.setContentsMargins(0, 0, 0, 16 if compact else 24)
        self._title_label.setMinimumHeight(self._title_label.fontMetrics().height())
        self._row.setContentsMargins(0, 0, 0, 25 if compact else 38)
        self._row.setSpacing(20 if compact else 42)
        self._progress_bar.setMinimumWidth(80 if compact else 160)
        self._progress_panel.setFixedWidth(190 if compact else 260)
        if stacked_progress != self._compact_progress:
            if stacked_progress:
                self._row.removeWidget(self._progress_panel)
                self._identity.insertWidget(4, self._progress_panel)
            else:
                self._identity.removeWidget(self._progress_panel)
                self._row.insertWidget(2, self._progress_panel)
            self._compact_progress = stacked_progress
        if self._thumbnail.size() == size:
            return
        source = QPixmap(self.info.thumbnail) if self.info.thumbnail else QPixmap()
        self._thumbnail.setFixedSize(size)
        if not source.isNull():
            self._thumbnail.setPixmap(source.scaled(
                size, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event) -> None:  # noqa: N802
        QFrame.resizeEvent(self, event)
        self._apply_v3_thumbnail_geometry()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._title_label.setStyleSheet('color: #39e07a; font-family: "Segoe UI";')
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._title_label.setStyleSheet('color: #e6e8e6; font-family: "Segoe UI";')
        super().leaveEvent(event)


class _HomeFirstRunActionButton(QPushButton):
    """Two-line action that preserves the layout's type hierarchy."""

    def __init__(self, title: str, body: str, parent=None) -> None:
        super().__init__("", parent)
        self.setFixedHeight(115)
        row = QHBoxLayout(self)
        row.setContentsMargins(11, 8, 4, 8)
        row.setSpacing(0)

        self.action_icon = QLabel(self)
        self.action_icon.setObjectName("V3HomeActionIcon")
        self.action_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.action_icon.setFixedSize(48, 48)
        self.action_icon.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self.action_icon)
        row.addSpacing(17)

        copy = QVBoxLayout()
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(3)
        copy.addStretch(1)
        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("V3HomeActionTitle")
        self.title_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        copy.addWidget(self.title_label)
        self.body_label = QLabel(body, self)
        self.body_label.setObjectName("V3HomeActionBody")
        self.body_label.setWordWrap(True)
        self.body_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        copy.addWidget(self.body_label)
        copy.addStretch(1)
        row.addLayout(copy, 1)
        row.addSpacing(11)

        self.chevron = QLabel(self)
        self.chevron.setObjectName("V3HomeActionChevron")
        self.chevron.setProperty("iconAsset", "segoe-mdl2-chevron-right")
        self.chevron.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        # Use Windows' real icon font instead of Qt's filled triangle. E76C is
        # ChevronRight in Segoe MDL2 Assets, matching the locked thin stroke.
        self.chevron.setText("\ue76c")
        self.chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chevron.setFont(QFont("Segoe MDL2 Assets", 11))
        self.chevron.setFixedSize(18, 18)
        row.addWidget(self.chevron)

    def enterEvent(self, event) -> None:  # noqa: N802
        self.setProperty("interaction", "hover")
        self.style().unpolish(self)
        self.style().polish(self)
        self.title_label.setStyleSheet(
            'color: #39e07a; font-family: "Segoe UI";')
        self.chevron.setStyleSheet(
            'color: #39e07a; font-family: "Segoe MDL2 Assets";')
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.setProperty("interaction", "")
        self.style().unpolish(self)
        self.style().polish(self)
        self.title_label.setStyleSheet(
            'color: #e6e8e6; font-family: "Segoe UI";')
        self.chevron.setStyleSheet(
            'color: #8d949a; font-family: "Segoe MDL2 Assets";')
        super().leaveEvent(event)

    def set_action_icon(
            self, icon: QIcon, size: QSize, asset_name: str,
            opacity: float = 1.0, *, canvas_size: QSize | None = None,
            content_size: QSize | None = None,
            content_offset: QPoint | None = None) -> None:
        source = icon.pixmap(size)
        canvas_size = canvas_size or self.action_icon.size()
        content_offset = content_offset or QPoint(0, 0)
        if content_size is not None and not source.isNull():
            image = source.toImage()
            min_x, min_y = image.width(), image.height()
            max_x = max_y = -1
            for y in range(image.height()):
                for x in range(image.width()):
                    if image.pixelColor(x, y).alpha() <= 8:
                        continue
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)
            if max_x >= min_x and max_y >= min_y:
                source = source.copy(QRect(
                    min_x,
                    min_y,
                    max_x - min_x + 1,
                    max_y - min_y + 1,
                )).scaled(
                    content_size,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
        canvas = QPixmap(canvas_size)
        canvas.fill(Qt.GlobalColor.transparent)
        painter = QPainter(canvas)
        painter.drawPixmap(content_offset, source)
        painter.end()
        self.action_icon.setFixedSize(canvas_size)
        self.action_icon.setPixmap(canvas)
        self.action_icon.setProperty("iconAsset", asset_name)
        effect = QGraphicsOpacityEffect(self.action_icon)
        effect.setOpacity(opacity)
        self.action_icon.setGraphicsEffect(effect)


class StartScreenV3(StartScreenV2):
    """The locked Home states, reusing V2 project and persistence behavior."""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#11110f"))

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setProperty("shellV3Home", "true")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAccessibleName("TapeSift Home")
        self._body.setObjectName("V3HomeBody")
        # Home is flush to the native frame. These insets restore the locked
        # x=29 left anchor and x=520 drop target on the 1708px client canvas.
        self._outer_layout.setContentsMargins(38, 30, 39, 14)
        body_index = self._outer_layout.indexOf(self._body)
        if body_index >= 0:
            self._outer_layout.insertSpacing(body_index + 1, 9)
        self._first_run_left_layout.setSpacing(36)
        self._first_run_actions.setFixedHeight(257)
        self._first_run_left_layout.setAlignment(
            self._first_run_actions, Qt.AlignmentFlag.AlignLeft)
        self._workflow.setFixedHeight(0)
        self._workflow.setObjectName("V3HomeWorkflow")
        self._workflow.layout().setSpacing(0)
        workflow_row = self._workflow.layout().itemAt(1).layout()
        if workflow_row is not None:
            workflow_row.setContentsMargins(0, 5, 0, 8)
            self._lock_workflow_connectors(workflow_row)
        for badge in self._workflow.findChildren(QLabel):
            if badge.property("workflowNumber") == "true":
                badge.setFixedSize(60, 60)
            elif badge.property("role") == "workflowHeading":
                badge.setFixedHeight(38)
                badge.setContentsMargins(0, 10, 0, 0)
            elif badge.property("role") == "subtle":
                badge.setFixedHeight(37)
                badge.setContentsMargins(0, 0, 0, 12)
        self._footer_status = next(
            (
                label for label in self.findChildren(QLabel)
                if label.property("role") == "localStatus"
            ),
            None,
        )
        self._footer_build = self.findChild(QLabel, "HomeBuildLabel")
        self._footer_status_overlay = QLabel(
            self._footer_status.text() if self._footer_status is not None else "",
            self,
        )
        self._footer_status_overlay.setObjectName("V3HomeFooterStatusOverlay")
        self._footer_status_overlay.setFixedSize(180, 24)
        self._footer_status_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._footer_status_overlay.setStyleSheet(
            'color: #8d949a; background: transparent; '
            'font-family: "Segoe UI"; font-size: 13px; font-weight: 400;')
        status_font = self._footer_status_overlay.font()
        status_font.setStretch(105)
        self._footer_status_overlay.setFont(status_font)
        self._footer_build_overlay = QLabel(
            self._footer_build.text() if self._footer_build is not None else "",
            self,
        )
        self._footer_build_overlay.setObjectName("V3HomeFooterBuildOverlay")
        self._footer_build_overlay.setFixedSize(305, 21)
        self._footer_build_overlay.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._footer_build_overlay.setStyleSheet(
            'color: #8d949a; background: transparent; '
            'font-family: "Segoe UI"; font-size: 13px; font-weight: 400; '
            'letter-spacing: 0.2px;')
        build_font = self._footer_build_overlay.font()
        build_font.setStretch(105)
        self._footer_build_overlay.setFont(build_font)
        for placeholder in (self._footer_status, self._footer_build):
            if placeholder is not None:
                effect = QGraphicsOpacityEffect(placeholder)
                effect.setOpacity(0.0)
                placeholder.setGraphicsEffect(effect)
        self._recent_col.setObjectName("V3HomeReturningProjects")
        self._recent_col.layout().setSpacing(29)
        self.cards_layout.setSpacing(0)
        self._first_run_divider.setParent(self)
        self._footer_rule = QFrame(self)
        self._footer_rule.setObjectName("V3HomeFooterRule")
        self._footer_rule.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._footer_build_overlay.hide()
        self._footer_status_overlay.setText('<span style="color:#39e07a">●</span>  Local workspace')
        self._configure_locked_masthead()
        self.setAcceptDrops(True)
        self.drop_zone.setAcceptDrops(True)
        self.drop_zone.installEventFilter(self)
        self._sync_drop_action_visibility()
        self._repolish_locked_surface()
        self._apply_locked_font_families()

        self._install_resume_home()

    def _install_resume_home(self):
        from tapesift.ui_v3.home_surface import EmptyFilmRoom
        self._home_resume_ready = True
        # Retire the old composition but retain its shared loading/actions.
        def hide_items(layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().hide()
                elif item.layout():
                    hide_items(item.layout())
        hide_items(self._outer_layout)
        self._outer_layout.setContentsMargins(0, 0, 0, 52)
        self._outer_layout.setSpacing(0)
        self._home_actions = QWidget(self)
        actions = QHBoxLayout(self._home_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(24)
        for caption, callback, name, icon in (
                ("New project", self._new_project, "V3HomeNewProject", "add-24.svg"),
                ("Open existing…", self._open_project, "V3HomeOpenExisting", "folder-open-24.svg")):
            button = QPushButton(caption)
            button.setObjectName(name)
            button.setProperty("filmAction", True)
            button.setFixedSize(182, 50)
            button.setIcon(tinted_icon(icon, "#eeeade", 24))
            button.setIconSize(QSize(24, 24))
            button.clicked.connect(callback)
            actions.addWidget(button)
        self._outer_layout.addWidget(self._home_actions)
        self._home_scroll = QScrollArea()
        self._home_scroll.setWidgetResizable(True)
        self._home_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._home_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._home_board = QWidget()
        self._home_columns = QVBoxLayout(self._home_board)
        self._home_columns.setContentsMargins(0, 0, 0, 0)
        self._home_columns.setSpacing(0)
        self._home_feature = QWidget()
        self._home_feature_layout = QVBoxLayout(self._home_feature)
        self._home_feature_layout.setContentsMargins(0, 0, 0, 0)
        self._home_columns.addWidget(self._home_feature)
        self._home_columns.addWidget(self._recent_col)
        self._recent_col.layout().setContentsMargins(56, 18, 56, 0)
        self._recent_col.layout().setSpacing(6)
        recent_header = self._recent_col.layout().itemAt(0).layout()
        recent_title = recent_header.itemAt(0).widget()
        recent_title.setText("Recent projects")
        recent_title.setStyleSheet("color:#eeeade;font:600 23px 'Segoe UI';background:transparent;")
        self._view_all_projects = self._recent_col.findChild(QPushButton)
        self._view_all_projects.setText("View library")
        self._view_all_projects.setObjectName("V3FilmLibrary")
        self._view_all_projects.setIcon(tinted_icon("arrow-right-24.svg", "#bba36a"))
        self._view_all_projects.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._view_all_projects.show()
        # One scrolling page, rather than a scrolling list nested inside it.
        self.cards_scroll.takeWidget()
        self.cards_scroll.hide()
        self._recent_col.layout().replaceWidget(self.cards_scroll, self.cards_host)
        self.cards_host.show()
        self._recent_divider = QFrame(self.cards_host)
        self._recent_divider.setStyleSheet("background:#282722;border:0;")
        self.cards_host.installEventFilter(self)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self._home_scroll.setWidget(self._home_board)
        self._outer_layout.addWidget(self._home_scroll, 1)
        self.drop_zone.hide()
        self.drop_zone = EmptyFilmRoom()
        self.drop_zone.installEventFilter(self)
        empty_layout = QVBoxLayout(self.drop_zone)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        self._drop_default = QWidget()
        self._drop_default.setObjectName("V3FilmEmptyCopy")
        copy = QVBoxLayout(self._drop_default)
        copy.setContentsMargins(0, 0, 0, 0)
        copy.setSpacing(0)
        eyebrow = QLabel("YOUR FILM ROOM")
        eyebrow.setStyleSheet("color:#bba36a;background:transparent;font:18px 'Segoe UI';")
        eyebrow.setFixedHeight(26)
        copy.addWidget(eyebrow)
        self._drop_heading = QLabel('<p style="line-height:76%;margin:0">Start with<br>your film.</p>')
        self._drop_heading.setObjectName("V3FilmEmptyTitle")
        self._drop_heading.setWordWrap(True)
        self._drop_heading.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        copy.addWidget(self._drop_heading)
        copy.addSpacing(16)
        subtitle = QLabel("Create a project to turn game film into organized clips.")
        subtitle.setObjectName("V3FilmEmptySubtitle")
        subtitle.setWordWrap(True)
        copy.addWidget(subtitle)
        copy.addSpacing(44)
        buttons = QHBoxLayout()
        buttons.setSpacing(24)
        self._drop_new_button = QPushButton("New project")
        self._drop_new_button.setProperty("filmPrimary", True)
        self._drop_new_button.setIcon(tinted_icon("add-24.svg", "#141410", 26))
        self._drop_new_button.clicked.connect(self._new_project)
        self._empty_open_button = QPushButton("Open existing")
        self._empty_open_button.setIcon(tinted_icon("folder-open-24.svg", "#eeeade", 26))
        self._empty_open_button.clicked.connect(self._open_project)
        for button in (self._drop_new_button, self._empty_open_button):
            button.setProperty("filmAction", True)
            button.setIconSize(QSize(26, 26))
            button.setFixedSize(238, 66)
            buttons.addWidget(button)
        buttons.addStretch()
        copy.addLayout(buttons)
        copy.addSpacing(16)
        self._drop_project_hint = QLabel("Your projects will appear here.")
        self._drop_project_hint.setStyleSheet("color:#96958f;background:transparent;font:18px 'Segoe UI';")
        copy.addWidget(self._drop_project_hint)
        self._accepted_file = QLabel()
        self._accepted_file_name = self._accepted_file
        self._accepted_file.setStyleSheet("color:#c8b780;background:transparent;font:18px 'Segoe UI';")
        self._accepted_file.hide()
        copy.addWidget(self._accepted_file)
        self._drop_default.setParent(self.drop_zone)
        self._drop_default.show()
        self._home_columns.insertWidget(0, self.drop_zone)
        self._drop_status.setParent(self)
        self._outer_layout.insertWidget(0, self._drop_status)
        self._drop_status.hide()
        self._footer_status_overlay.setStyleSheet("color:#96958f;background:transparent;font:13px 'Segoe UI';")
        self._footer_status_overlay.show()
        self._footer_build_overlay.hide()
        self.refresh_recent()

    def _refresh_resume_home(self):
        from tapesift.ui_core.start_screen import load_project_info
        for layout in (self._home_feature_layout, self.cards_layout):
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    loader = getattr(item.widget(), "_preview_loader", None)
                    if loader is not None:
                        loader.cancel()
                    item.widget().hide()
                    item.widget().deleteLater()
        for row in range(self.cards_layout.rowCount()):
            self.cards_layout.setRowStretch(row, 0)
        if self.settings.prune_missing_recents():
            self.settings.save()
        infos = [load_project_info(Path(path)) for path in self.settings.recent_projects]
        self._film_cards = []
        for index, info in enumerate(infos):
            card = ResumeCardV3(info, featured=index == 0)
            card.open_requested.connect(self.open_project_requested.emit)
            card.resume_requested.connect(self.resume_project_requested.emit)
            card.menu_requested.connect(self._card_menu)
            self._film_cards.append(card)
            if index == 0:
                self._home_feature_layout.addWidget(card)
                card._preview_loader.start(info, self.settings.ffmpeg_path)
            else:
                self.cards_layout.addWidget(card, index - 1, 0)
        self.empty_recent.hide()
        self.summary_label.hide()
        self._home_feature.setVisible(bool(infos))
        self._recent_col.setVisible(len(infos) > 1)
        self.drop_zone.setVisible(not infos)
        self._apply_responsive()

    # ---------- locked Home composition ----------

    def _repolish_locked_surface(self) -> None:
        """Apply ancestor-dependent Home selectors after the V3 flag exists."""
        for widget in (self, *self.findChildren(QWidget)):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _apply_locked_font_families(self) -> None:
        """Qt keeps the app font family through QSS; bind the locked faces."""
        if getattr(self, "_home_resume_ready", False):
            return
        for widget in self.findChildren(QWidget):
            if not isinstance(widget, (QLabel, QPushButton)):
                continue
            if widget in {
                getattr(self, "_footer_status_overlay", None),
                getattr(self, "_footer_build_overlay", None),
            }:
                continue
            font = widget.font()
            if widget.objectName() == "V3HomeActionChevron":
                font.setFamily("Segoe MDL2 Assets")
                font.setPointSize(11)
                widget.setFont(font)
                widget.setStyleSheet("")
                continue
            is_mono = widget.objectName() == "HomeBuildLabel" \
                or widget.property("role") == "localStatus"
            family = "Consolas" if is_mono else "Segoe UI"
            font.setFamily(family)
            if widget.property("role") == "projectMeta":
                font.setStretch(100)
            elif widget.property("role") == "dropHeading":
                font.setStretch(95)
            elif widget.objectName() == "V3HomeFormats":
                font.setStretch(103)
            elif widget.property("role") == "subtle" \
                    and self._workflow.isAncestorOf(widget):
                font.setStretch(92)
            widget.setFont(font)
            # Direct font-only QSS prevents the flat ancestor rules painting.
            widget.setStyleSheet("")

    def _lock_workflow_connectors(self, workflow_row) -> None:
        """Preserve step layout while pinning the two visible reference lines."""
        connectors = []
        for index in range(workflow_row.count()):
            widget = workflow_row.itemAt(index).widget()
            if widget is not None \
                    and widget.property("workflowConnector") == "true":
                connectors.append((index, widget))
        for index, connector in reversed(connectors):
            workflow_row.removeWidget(connector)
            workflow_row.insertSpacing(index, 80)
            connector.setParent(self._workflow)
            connector.show()
            connector.raise_()
        self._workflow_connectors = [item[1] for item in connectors]
        QTimer.singleShot(0, self, self._position_workflow_connectors)

    def _position_workflow_connectors(self) -> None:
        self._position_locked_footer()
        connectors = getattr(self, "_workflow_connectors", ())
        if len(connectors) != 2:
            return
        scale = self._workflow.width() / 1640.0
        for connector, x, width in zip(
                connectors, (443, 1029), (118, 111)):
            connector.setGeometry(
                round(x * scale), 44,
                max(1, round(width * scale)), 1,
            )
            connector.raise_()

    def _position_locked_footer(self) -> None:
        """Pin the wide Home workflow and footer to the locked baseline."""
        if getattr(self, "_home_resume_ready", False):
            self._footer_rule.setGeometry(52, self.height() - 52, max(0, self.width() - 104), 1)
            self._footer_rule.setStyleSheet("background:#282722;border:0;")
            self._footer_rule.show()
            self._first_run_divider.hide()
            self._footer_status_overlay.move(52, self.height() - 35)
            return
        if self.width() < 1600:
            if self._footer_status is not None:
                self._footer_status_overlay.move(self._footer_status.pos())
            if self._footer_build is not None:
                self._footer_build_overlay.move(self._footer_build.pos())
        else:
            self._footer_status_overlay.move(24, self.height() - 44)
            self._footer_build_overlay.move(1364, self.height() - 55)
        self._footer_rule.setGeometry(0, self.height() - 62, self.width(), 1)
        self._footer_rule.show()
        self._footer_status_overlay.raise_()
        self._position_first_run_divider(bool(self.settings.recent_projects))
        self._footer_build_overlay.hide()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_locked_font_families()
        self._position_workflow_connectors()
        QTimer.singleShot(0, self, self._apply_locked_font_families)
        QTimer.singleShot(0, self, self._position_workflow_connectors)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_workflow_connectors()
        QTimer.singleShot(0, self, self._position_workflow_connectors)

    def _build_statement(self) -> QWidget:
        statement = super()._build_statement()
        layout = statement.layout()
        while layout.count() > 1:
            item = layout.takeAt(1)
            if item.layout() is not None:
                for index in range(item.layout().count()):
                    widget = item.layout().itemAt(index).widget()
                    if widget is not None:
                        widget.hide()
                item.layout().deleteLater()
        room = layout.itemAt(0).layout()
        room.setSpacing(18)
        for index, height in enumerate((18, 42, 28)):
            room.itemAt(index).widget().setFixedHeight(height)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        statement.setObjectName("V3HomeStatement")
        statement.setFixedHeight(160)
        self._statement_widget = statement
        return statement

    def _configure_locked_masthead(self) -> None:
        icon_path = _icon_path("tapesift.png")
        wordmark_path = _icon_path("tapesift-wordmark-white.png")
        if icon_path is not None and wordmark_path is not None \
                and self.home_brand_lockup is not None:
            lockup = QPixmap(131, 30)
            lockup.fill(Qt.GlobalColor.transparent)
            painter = QPainter(lockup)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            icon = QPixmap(str(icon_path)).scaled(
                30,
                30,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            wordmark = QPixmap(str(wordmark_path)).scaled(
                87,
                22,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.drawPixmap(-1, 0, icon)
            painter.drawPixmap(36, 6, wordmark)
            painter.end()
            self.home_brand_lockup.setPixmap(lockup)
            self.home_brand_lockup.setFixedSize(lockup.size())

        self.home_brand_divider.hide()
        self.home_section_label.hide()
        self.home_navigation_chip = QPushButton("HOME")
        self.home_navigation_chip.setObjectName("V3HomeNavigationChip")
        self.home_navigation_chip.setAccessibleName("Home")
        self.home_navigation_chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.home_navigation_chip.setFixedSize(102, 38)
        home_icon = QPixmap(18, 18)
        home_icon.fill(Qt.GlobalColor.transparent)
        painter = QPainter(home_icon)
        painter.setPen(QColor("#39e07a"))
        painter.setFont(QFont("Segoe MDL2 Assets", 13))
        painter.drawText(
            home_icon.rect(), Qt.AlignmentFlag.AlignCenter, "\ue80f")
        painter.end()
        self.home_navigation_chip.setIcon(QIcon(home_icon))
        self.home_navigation_chip.setIconSize(QSize(18, 18))
        self.home_navigation_chip.setProperty(
            "iconAsset", "segoe-mdl2-home-outline")
        self.library_button.setText("Library")
        self.library_button.setObjectName("V3HomeLibraryLink")
        self.library_button.setIcon(QIcon())
        self.library_button.setAccessibleName("Open Library")
        self.library_button.setToolTip("Open Library · Ctrl+Shift+L")
        self.library_button.setFixedSize(76, 38)
        self.settings_button.setIcon(QIcon())
        self.settings_button.setFixedSize(82, 38)

    def refresh_recent(self) -> None:
        if getattr(self, "_home_resume_ready", False):
            self._refresh_resume_home()
            return
        super().refresh_recent()
        if not bool(self.settings.recent_projects):
            self._room_title.setText("Start your first breakdown.")

    def move_masthead_to(self, shell) -> None:
        """Keep the wired navigation widgets in the standard horizontal masthead."""
        self.home_brand_lockup.setPixmap(brand_pixmap("ifi-full-name.jpg", 64))
        self.home_brand_lockup.setFixedSize(self.home_brand_lockup.pixmap().size())
        self.home_brand_lockup.show()
        shell._left_layouts["home"].setSpacing(40)
        shell.add_mode_left_widget("home", self.home_brand_lockup)
        self.home_brand_divider.setFixedSize(1, 48)
        shell.add_mode_left_widget("home", self.home_brand_divider)
        self.home_brand_divider.show()
        self.home_navigation_chip.setObjectName("V3FilmWordmark")
        self.home_navigation_chip.setText("")
        wordmark = brand_pixmap("tapesift-wordmark-white.png", 38)
        self.home_navigation_chip.setIcon(QIcon(wordmark))
        self.home_navigation_chip.setIconSize(wordmark.size())
        self.home_navigation_chip.setFixedSize(wordmark.width(), 50)
        shell.add_mode_left_widget("home", self.home_navigation_chip)
        shell.add_mode_left_stretch("home")
        nav = QWidget(shell)
        nav.setObjectName("V3FilmNavigation")
        row = QHBoxLayout(nav)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(30)
        home = QPushButton("Home")
        home.setObjectName("V3FilmHomeTab")
        home.setIcon(tinted_icon("home-24.svg", "#eeeade", 24))
        self.library_button.setObjectName("V3FilmLibraryTab")
        self.library_button.setProperty("libraryEntry", False)
        self.settings_button.setObjectName("V3FilmSettingsTab")
        self.library_button.setIcon(tinted_icon("folder-open-24.svg", "#eeeade", 24))
        self.settings_button.setIcon(tinted_icon("settings-24.svg", "#eeeade", 24))
        for button in (home, self.library_button, self.settings_button):
            button.setProperty("filmNav", True)
            button.setIconSize(QSize(24, 24))
            button.setFixedSize(114, 54)
            row.addWidget(button)
            button.show()
        self.home_section_label.hide()
        shell._film_navigation = nav
        shell.add_mode_right_widget("home", self._home_actions)
        self._home_actions.show()
        self._masthead_gap.changeSize(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        shell._place_children()

    def _build_start_column(self) -> QWidget:
        """Build both returning and first-run hosts around one live drop zone."""
        returning = QWidget()
        returning.setObjectName("V3HomeReturningStart")
        returning_layout = QVBoxLayout(returning)
        returning_layout.setContentsMargins(0, 0, 0, 0)
        returning_layout.setSpacing(10)

        self.drop_zone = self._build_drop_zone()
        self._enhance_drop_zone()
        returning_layout.addWidget(self.drop_zone)
        self._returning_open_button = self._build_open_project_link()
        returning_layout.addWidget(
            self._returning_open_button,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )
        returning_layout.addStretch(1)

        self._first_run_actions = self._build_first_run_actions()
        self._first_run_left = QWidget()
        self._first_run_left.setObjectName("V3HomeFirstRunLeft")
        self._first_run_left_layout = QVBoxLayout(self._first_run_left)
        self._first_run_left_layout.setContentsMargins(0, 0, 0, 0)
        self._first_run_left_layout.setSpacing(28)
        self._first_run_left_layout.addWidget(self._first_run_actions)
        self._first_run_left_layout.addStretch(1)
        self._first_run_divider = QFrame(self._first_run_left)
        self._first_run_divider.setObjectName("V3HomeFirstRunDivider")
        self._first_run_divider.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._first_run_divider.raise_()

        self._first_run_drop_host = QWidget()
        self._first_run_drop_host.setObjectName("V3HomeFirstRunDropHost")
        self._first_run_drop_layout = QVBoxLayout(self._first_run_drop_host)
        self._first_run_drop_layout.setContentsMargins(0, 14, 0, 0)
        self._first_run_drop_layout.setSpacing(0)
        return returning

    def _enhance_drop_zone(self) -> None:
        """Add the accepted-file treatment from the locked drag-over state."""
        self.drop_zone.setObjectName("V3HomeDropZone")
        self._drop_new_button = self.drop_zone.findChild(
            QPushButton, "HomeNewProject")
        self._drop_formats = next(
            (
                label for label in self._drop_default.findChildren(QLabel)
                if label.text() == VIDEO_FORMATS
            ),
            None,
        )
        self._drop_icon = next(
            (
                label for label in self._drop_default.findChildren(QLabel)
                if label.pixmap() is not None and not label.pixmap().isNull()
            ),
            None,
        )
        if self._drop_icon is not None:
            self._drop_icon.setObjectName("V3HomeDropIcon")
            self._returning_drop_icon = self._drop_icon.pixmap()
            self._drop_icon_asset = _icon_path("tapesift-clapperboard-v3.png")
            self._drop_icon_effect = QGraphicsColorizeEffect(self._drop_icon)
            self._drop_icon_effect.setColor(QColor("#39e07a"))
            self._drop_icon_effect.setStrength(1.0)
            self._drop_icon_effect.setEnabled(False)
            self._drop_icon.setGraphicsEffect(self._drop_icon_effect)

        self._drop_heading.setFixedHeight(50)
        self._drop_heading.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)

        accepted = QFrame()
        accepted.setObjectName("V3HomeAcceptedFile")
        accepted.setProperty("homeAcceptedFile", "true")
        accepted_row = QHBoxLayout(accepted)
        accepted_row.setContentsMargins(14, 8, 12, 8)
        accepted_row.setSpacing(10)

        film_icon = QLabel()
        film_icon.setObjectName("V3HomeAcceptedFileIcon")
        film_icon.setAccessibleName("Accepted game film")
        video_icon = _icon_path("tapesift-video.png")
        if video_icon is not None:
            film_icon.setPixmap(QPixmap(str(video_icon)).scaled(
                22,
                22,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            film_icon.setProperty("iconAsset", "tapesift-video.png")
        accepted_row.addWidget(film_icon)

        self._accepted_file_name = QLabel("")
        self._accepted_file_name.setObjectName("V3HomeAcceptedFileName")
        self._accepted_file_name.setMinimumWidth(0)
        self._accepted_file_name.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        accepted_row.addWidget(self._accepted_file_name, 1)

        accepted_check = QLabel()
        accepted_check.setObjectName("V3HomeAcceptedCheck")
        accepted_check.setAccessibleName("File accepted")
        accepted_check.setPixmap(self.style().standardIcon(
            QStyle.StandardPixmap.SP_DialogApplyButton).pixmap(18, 18))
        accepted_row.addWidget(accepted_check)
        accepted.hide()
        self._accepted_file = accepted

        drop_layout = self._drop_default.layout()
        if isinstance(drop_layout, QVBoxLayout):
            drop_layout.setContentsMargins(0, 0, 46, 0)
            top_spacer = drop_layout.itemAt(0).spacerItem()
            if top_spacer is not None:
                top_spacer.changeSize(
                    0,
                    133,
                    QSizePolicy.Policy.Minimum,
                    QSizePolicy.Policy.Fixed,
                )
            if self._drop_formats is not None:
                self._drop_formats.setObjectName("V3HomeFormats")
                self._drop_formats.setText(
                    f"Start a new project from {VIDEO_FORMATS}")
                self._drop_formats.setFixedHeight(22)
            self._drop_project_hint = QLabel(
                "Drop a .tapesift project to open it")
            self._drop_project_hint.setObjectName("V3HomeProjectDropHint")
            self._drop_project_hint.setProperty("role", "subtle")
            self._drop_project_hint.setAlignment(
                Qt.AlignmentFlag.AlignCenter)
            self._drop_project_hint.setWordWrap(False)
            self._drop_project_hint.setMinimumWidth(360)
            self._drop_project_hint.setFixedHeight(42)
            format_index = drop_layout.indexOf(self._drop_formats)
            drop_layout.insertWidget(
                max(0, format_index + 1),
                self._drop_project_hint,
                0,
                Qt.AlignmentFlag.AlignHCenter,
            )
            drop_layout.insertWidget(
                max(0, drop_layout.count() - 1),
                accepted,
                0,
                Qt.AlignmentFlag.AlignHCenter,
            )
            drop_layout.insertSpacing(
                max(0, drop_layout.count() - 1), 19)
            icon_index = drop_layout.indexOf(self._drop_icon)
            if icon_index >= 0:
                drop_layout.insertSpacing(icon_index + 1, 7)
            heading_index = drop_layout.indexOf(self._drop_heading)
            drop_layout.insertSpacing(heading_index + 1, 14)
            formats_index = drop_layout.indexOf(self._drop_formats)
            drop_layout.insertSpacing(formats_index + 1, 6)

    def _build_first_run_actions(self) -> QFrame:
        card = QFrame()
        card.setObjectName("V3HomeFirstRunActions")
        card.setProperty("homeFirstRunActions", "true")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 13, 0, 13)
        layout.setSpacing(0)

        new_project = _HomeFirstRunActionButton(
            "New Project", "Start a new project from a game film.")
        new_project.setObjectName("V3HomeFirstRunNewProject")
        new_project.setProperty("homeFirstRunAction", "true")
        new_project.setAccessibleName(
            "New Project. Start a new project from a game film.")
        self._new_project_action = new_project
        add_icon = _icon_path("v3/add-24.svg")
        if add_icon is not None:
            new_project.set_action_icon(
                QIcon(str(add_icon)), QSize(32, 32),
                "v3/add-24.svg",
                canvas_size=QSize(48, 48),
                content_size=QSize(30, 30),
                content_offset=QPoint(7, 14),
            )
            new_project.setProperty(
                "iconAsset", "v3/add-24.svg")
            effect = QGraphicsColorizeEffect(new_project.action_icon)
            effect.setColor(QColor("#39e07a"))
            effect.setStrength(1.0)
            new_project.action_icon.setGraphicsEffect(effect)
        new_project.clicked.connect(self._new_project)
        layout.addWidget(new_project)

        rule = QFrame()
        rule.setObjectName("V3HomeFirstRunActionRule")
        rule.setFixedHeight(1)
        layout.addWidget(rule)

        open_existing = _HomeFirstRunActionButton(
            "Open Existing…", "Open a .tapesift project.")
        open_existing.setObjectName("V3HomeFirstRunOpenExisting")
        open_existing.setProperty("homeFirstRunAction", "true")
        open_existing.setAccessibleName(
            "Open Existing. Open a TapeSift project.")
        open_existing.set_action_icon(
            QIcon(str(_icon_path("v3/folder-open-24.svg"))),
            QSize(40, 40),
            "v3/folder-open-24.svg",
            canvas_size=QSize(48, 48),
            content_size=QSize(32, 32),
            content_offset=QPoint(4, 11),
        )
        effect = QGraphicsColorizeEffect(open_existing.action_icon)
        effect.setColor(QColor("#39e07a"))
        effect.setStrength(1.0)
        open_existing.action_icon.setGraphicsEffect(effect)
        open_existing.clicked.connect(self._open_project)
        layout.addWidget(open_existing)
        return card

    def _build_add_project_row(self) -> QPushButton:
        action = _HomeFirstRunActionButton(
            "Bring in another game",
            "Start a separate project, or drop a film above.",
        )
        action.setObjectName("V3HomeAddProject")
        action.setProperty("addProjectRow", "true")
        action.setAccessibleName(
            "Bring in another game. Start a separate project.")
        action.layout().setContentsMargins(0, 8, 4, 8)
        icon_path = _icon_path("v3/add-24.svg")
        if icon_path is not None:
            action.set_action_icon(
                QIcon(str(icon_path)), QSize(32, 32), "v3/add-24.svg",
                canvas_size=QSize(48, 48),
                content_size=QSize(30, 30),
                content_offset=QPoint(7, 14),
            )
            effect = QGraphicsColorizeEffect(action.action_icon)
            effect.setColor(QColor("#39e07a"))
            effect.setStrength(1.0)
            action.action_icon.setGraphicsEffect(effect)
        action.chevron.hide()
        action.clicked.connect(self._new_project)
        action.hide()
        return action

    def _make_card(self, info: ProjectInfo) -> ProjectCardV3:
        card = ResumeCardV3(info, featured=self.cards_layout.count() == 0) if getattr(self, "_home_resume_ready", False) else ProjectCardV3(info)
        card.open_requested.connect(self.open_project_requested.emit)
        card.resume_requested.connect(self.resume_project_requested.emit)
        card.menu_requested.connect(self._card_menu)
        return card

    @staticmethod
    def _remove_from(layout, widget: QWidget) -> None:
        if layout is not None:
            layout.removeWidget(widget)

    def _place_statement_in_outer(self) -> None:
        self._remove_from(self._first_run_left_layout, self._statement_widget)
        self._remove_from(self._outer_layout, self._statement_widget)
        self._statement_widget.setParent(self)
        self._outer_layout.insertWidget(2, self._statement_widget)
        self._statement_widget.show()

    def _place_statement_in_first_run(self) -> None:
        self._remove_from(self._outer_layout, self._statement_widget)
        self._remove_from(self._first_run_left_layout, self._statement_widget)
        self._statement_widget.setParent(self._first_run_left)
        self._first_run_left_layout.insertWidget(0, self._statement_widget)
        self._statement_widget.show()

    def _place_drop_in_returning(self) -> None:
        self._remove_from(self._first_run_drop_layout, self.drop_zone)
        layout = self._start_col.layout()
        self._remove_from(layout, self.drop_zone)
        self.drop_zone.setParent(self._start_col)
        layout.insertWidget(0, self.drop_zone)

    def _place_drop_in_first_run(self) -> None:
        self._remove_from(self._first_run_drop_layout, self._recent_col)
        self._recent_col.hide()
        self._remove_from(self._start_col.layout(), self.drop_zone)
        self._remove_from(self._first_run_drop_layout, self.drop_zone)
        self.drop_zone.setParent(self._first_run_drop_host)
        self._first_run_drop_layout.addWidget(
            self.drop_zone,
            0,
            Qt.AlignmentFlag.AlignTop,
        )
        self.drop_zone.show()

    def _place_recent_in_locked_host(self) -> None:
        self._remove_from(self._first_run_drop_layout, self.drop_zone)
        self._remove_from(self._body_layout, self._recent_col)
        self._remove_from(self._first_run_drop_layout, self._recent_col)
        self._recent_col.setParent(self._first_run_drop_host)
        self._first_run_drop_layout.addWidget(self._recent_col)
        self._first_run_drop_layout.setAlignment(
            self._recent_col, Qt.AlignmentFlag.AlignTop)

    def _set_drop_icon_size(self, size: int, *, clapper: bool) -> None:
        icon = getattr(self, "_drop_icon", None)
        if icon is None:
            return
        if clapper:
            asset = getattr(self, "_drop_icon_asset", None)
            if asset is None:
                return
            source_height = round(size * 1.125) if size >= 120 else size
            pixmap = QPixmap(str(asset)).scaled(
                120,
                source_height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            faded = QPixmap(120, size)
            faded.fill(Qt.GlobalColor.transparent)
            painter = QPainter(faded)
            painter.setOpacity(0.56)
            painter.drawPixmap(0, -2 if size >= 120 else 0, pixmap)
            painter.end()
            pixmap = faded
            icon.setProperty("iconAsset", "tapesift-clapperboard-v3.png")
        else:
            pixmap = getattr(self, "_returning_drop_icon", QPixmap())
            icon.setProperty("iconAsset", "qt-arrow-down")
        if clapper:
            icon.setPixmap(pixmap)
        else:
            icon.setPixmap(pixmap.scaled(
                size,
                size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))

    def _clear_body_layout(self) -> None:
        for widget in (
            self._start_col,
            self._recent_col,
            self._first_run_left,
            self._first_run_drop_host,
        ):
            self._body_layout.removeWidget(widget)

    def _apply_responsive(self) -> None:
        """Match returning and first-run locks without replacing behavior."""
        if getattr(self, "_home_resume_ready", False):
            columns = 2 if self.width() >= 1150 else 1
            cards = getattr(self, "_film_cards", [])
            for card in cards[1:]:
                self.cards_layout.removeWidget(card)
            self.cards_layout.setHorizontalSpacing(54)
            self.cards_layout.setVerticalSpacing(8)
            self.cards_layout.setColumnStretch(0, 1)
            self.cards_layout.setColumnStretch(1, 1 if columns == 2 else 0)
            for index, card in enumerate(cards[1:]):
                self.cards_layout.addWidget(card, index // columns, index % columns)
                card.setFixedHeight(154)
            available = max(380, self._home_scroll.height())
            hero_height = max(330, available - (194 if len(cards) > 1 else 0))
            if cards:
                cards[0].setFixedHeight(hero_height)
            self.drop_zone.setFixedHeight(available)
            gutter = round(self.width() * .0615)
            self._drop_default.setFixedWidth(min(730, self.width() - 2 * gutter))
            font = max(48, min(84, round(self.width() * .0492)))
            if self._load_state == "dragover":
                font = 32
            self._drop_heading.setStyleSheet(f"color:#f4f0e5;background:transparent;font:700 {font}px 'Segoe UI';")
            # A fixed descent allowance keeps tight rich-text leading from
            # clipping "your", without accumulating minimum heights on resize.
            self._drop_heading.setFixedHeight(round(font * 2.1) + 14)
            self._drop_default.adjustSize()
            self._drop_default.move(gutter, max(28, min(round(available * .225), available - self._drop_default.height() - 25)))
            self._position_locked_footer()
            return
        wide = self.width() >= 1000
        has_projects = bool(self.settings.recent_projects)
        spacious = self.width() >= 1600
        compact_height = self.height() < 700
        sidebar = min(444, max(320, round(self.width() * 0.286) - 34))
        key = (wide, spacious, compact_height, has_projects, sidebar)
        self.cards_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        if has_projects:
            self.cards_scroll.setMinimumHeight(min(647, max(240, self.height() - 174)))
        if key == getattr(self, "_layout_key", None):
            return
        self._layout_key = key
        self._clear_body_layout()

        if has_projects:
            self._place_statement_in_first_run()
            self._place_recent_in_locked_host()
            self._first_run_left.show()
            self._first_run_drop_host.show()
            self._start_col.hide()
            self._recent_col.show()
            self._recent_col.setMinimumHeight(260)
            self.cards_scroll.setMaximumHeight(_UNBOUNDED)
            self._first_run_left.setMinimumWidth(sidebar if wide else 0)
            self._first_run_left.setMaximumWidth(sidebar if wide else _UNBOUNDED)
            self._first_run_actions.setFixedWidth(min(423, sidebar - 21))
            self._first_run_drop_layout.setContentsMargins(0, 0, 0, 0)
            self.summary_label.hide()
            self._recent_col.layout().itemAt(0).layout().itemAt(3).widget().hide()
            self._first_run_drop_host.setMinimumHeight(0)
            self._first_run_drop_host.setMaximumHeight(
                _UNBOUNDED)
            self._recent_col.setMaximumHeight(_UNBOUNDED)
            self.drop_zone.hide()
            self._body_layout.setDirection(
                QBoxLayout.Direction.LeftToRight
                if wide else QBoxLayout.Direction.TopToBottom)
            self._body_layout.setSpacing(38 if wide else 18)
            self._body_layout.addWidget(self._first_run_left, 0)
            self._body_layout.addWidget(self._first_run_drop_host, 1)
        else:
            self.cards_scroll.setMinimumHeight(0)
            self.cards_scroll.setMaximumHeight(_UNBOUNDED)
            self._place_statement_in_first_run()
            self._place_drop_in_first_run()
            self._start_col.hide()
            self._recent_col.hide()
            self._first_run_left.show()
            self._first_run_drop_host.show()
            self._first_run_left.setMinimumWidth(sidebar if wide else 0)
            self._first_run_left.setMaximumWidth(sidebar if wide else _UNBOUNDED)
            self._first_run_actions.setFixedWidth(min(423, sidebar - 21))
            self._first_run_drop_layout.setContentsMargins(0, 14, 0, 0)
            self.drop_zone.setMinimumHeight(
                340 if compact_height else 607 if wide else 340)
            self.drop_zone.setMaximumHeight(
                _UNBOUNDED if compact_height else 607 if wide else _UNBOUNDED)
            self._set_drop_icon_size(
                120 if wide else 84,
                clapper=True,
            )
            self._drop_project_hint.setVisible(self._load_state == "idle")
            self._body_layout.setDirection(
                QBoxLayout.Direction.LeftToRight
                if wide else QBoxLayout.Direction.TopToBottom)
            self._body_layout.setSpacing(42 if wide else 18)
            self._body_layout.addWidget(self._first_run_left, 0)
            self._body_layout.addWidget(self._first_run_drop_host, 1)

        self._workflow.setVisible(False)
        self._position_first_run_divider(wide and has_projects)
        self._sync_drop_action_visibility()

    def _position_first_run_divider(self, visible: bool) -> None:
        divider = getattr(self, "_first_run_divider", None)
        if divider is None:
            return
        divider.setVisible(visible)
        if divider.parent() is self:
            edge = self._first_run_left.mapTo(self, QPoint(self._first_run_left.width() - 1, 0))
            divider.setGeometry(edge.x(), 0, 1, max(0, self.height() - 62))

    # ---------- first-run drop behavior ----------

    @staticmethod
    def _path_from_event(event) -> Path | None:
        urls = event.mimeData().urls()
        if not urls:
            return None
        path = Path(urls[0].toLocalFile())
        if not path.is_file():
            return None
        if path.suffix.lower() in VIDEO_EXTENSIONS | {PROJECT_EXTENSION}:
            return path
        return None

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is getattr(self, "cards_host", None) and event.type() == QEvent.Type.Resize:
            divider = getattr(self, "_recent_divider", None)
            if divider is not None:
                divider.setGeometry(watched.width() // 2, 18, 1, max(0, watched.height() - 36))
                divider.setVisible(self.width() >= 1150 and len(getattr(self, "_film_cards", [])) > 2)
        if watched is not self.drop_zone:
            return super().eventFilter(watched, event)
        event_type = event.type()
        if event_type in {
            QEvent.Type.DragEnter,
            QEvent.Type.DragMove,
        }:
            path = self._path_from_event(event)
            if path is None:
                return False
            self.set_drag_active(True, path.name)
            event.acceptProposedAction()
            return True
        if event_type == QEvent.Type.DragLeave:
            self.set_drag_active(False)
            event.accept()
            return True
        if event_type == QEvent.Type.Drop:
            path = self._path_from_event(event)
            self.set_drag_active(False)
            if path is None:
                return False
            event.acceptProposedAction()
            self.queue_dropped_path(path)
            return True
        return super().eventFilter(watched, event)

    def queue_dropped_path(self, path: Path) -> None:
        """Leave the native drop event before opening a project or dialog."""
        if path.suffix.lower() == PROJECT_EXTENSION:
            QTimer.singleShot(
                0, self,
                lambda value=str(path): self.open_project_requested.emit(value),
            )
        else:
            QTimer.singleShot(
                0, self,
                lambda value=path: self._new_project_prefilled(value))

    def _new_project(self) -> None:
        self._new_project_prefilled()

    def _new_project_prefilled(self, video_path: Path | None = None) -> None:
        dialog = NewProjectDialog(self.settings, self)
        if video_path is not None:
            dialog.set_video_path(video_path)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = dialog.video_path
        if selected is None:
            return
        self.new_project_with_video_requested.emit(
            dialog.project_name,
            dialog.project_folder,
            dialog.output_folder,
            str(selected),
        )

    def set_drag_active(
            self, active: bool, filename: str | None = None) -> None:
        super().set_drag_active(active)
        action = getattr(self, "_new_project_action", None)
        if action is not None:
            action.setProperty("dropTarget", "true" if active else "false")
            action.style().unpolish(action)
            action.style().polish(action)
        if self._load_state not in {"idle", "dragover"}:
            return
        if active:
            self._drop_heading.setText("RELEASE TO START A NEW PROJECT")
            self._accepted_file_name.setText(filename or "Supported file")
            self._accepted_file_name.setToolTip(filename or "Supported file")
            self._accepted_file.show()
        else:
            self._drop_heading.setText('<p style="line-height:76%;margin:0">Start with<br>your film.</p>' if getattr(self, "_home_resume_ready", False) else "DROP A GAME FILM HERE")
            self._accepted_file.hide()
        self._drop_heading.setProperty(
            "dragover", "true" if active else "false")
        self._drop_heading.style().unpolish(self._drop_heading)
        self._drop_heading.style().polish(self._drop_heading)
        if not bool(self.settings.recent_projects):
            self._drop_project_hint.setVisible(not active)
        effect = getattr(self, "_drop_icon_effect", None)
        if effect is not None:
            effect.setEnabled(active)
        self._sync_drop_action_visibility()
        if getattr(self, "_home_resume_ready", False):
            self._apply_responsive()

    def _sync_drop_action_visibility(self) -> None:
        button = getattr(self, "_drop_new_button", None)
        if button is None:
            return
        has_projects = bool(self.settings.recent_projects)
        button.setVisible(
            (not has_projects if getattr(self, "_home_resume_ready", False) else has_projects)
            and self._load_state == "idle")

    def clear_load_state(self) -> None:
        super().clear_load_state()
        if getattr(self, "_home_resume_ready", False):
            self._drop_heading.setText('<p style="line-height:76%;margin:0">Start with<br>your film.</p>')
        self._accepted_file.hide()
        if not bool(self.settings.recent_projects):
            self._drop_project_hint.show()
        self._sync_drop_action_visibility()
        if getattr(self, "_home_resume_ready", False):
            self._apply_responsive()
