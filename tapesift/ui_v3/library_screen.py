"""Shell V3 Library presentation over the existing Library authority."""

from __future__ import annotations

from pathlib import Path

import shiboken6

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal, QSignalBlocker, QItemSelectionModel
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QScrollArea,
    QSpacerItem,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tapesift.ui_core.layout_ownership import reparent_widget
from tapesift.services import library_service
from tapesift.services.football_vocab import lookup
from tapesift.services.library_service import LibraryRow
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_v3.icons import tinted_icon, brand_pixmap
from tapesift.ui_v3.material import paint_slate
from tapesift.ui_v3.transport_surface import V3TransportSurface
from tapesift.ui_core.library_screen import LibrarySearchScreen
from tapesift.ui_v2.library_screen import (
    LibrarySearchScreenV2,
    ResultRowDelegateV2,
)
from tapesift.ui_v2.icon_utils import tinted_standard_icon
from tapesift.ui_v2.start_screen import _icon_path


def _tinted_asset_icon(name: str, size: QSize, colour: str) -> QIcon:
    """Tint one real bundled icon asset for the dark application bar."""
    path = _icon_path(name)
    if path is None:
        return QIcon()
    source = QPixmap(str(path)).scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    if source.isNull():
        return QIcon()
    tinted = QPixmap(source.size())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(
        QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), QColor(colour))
    painter.end()
    return QIcon(tinted)


class _FilmSearchGlyph(QWidget):
    """Layer two real Windows icon-font glyphs used by the locked empty state."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3LibraryFilmSearchGlyph")
        self.setProperty("iconAsset", "segoe-mdl2-movies-search")
        self.setFixedSize(98, 76)

        movie = QLabel("\ue8b2", self)
        movie.setObjectName("V3LibraryFilmGlyph")
        movie.setFont(QFont("Segoe MDL2 Assets", 45))
        movie.setAlignment(Qt.AlignmentFlag.AlignCenter)
        movie.setGeometry(0, 0, 74, 68)

        search = QLabel("\ue721", self)
        search.setObjectName("V3LibrarySearchGlyph")
        search.setFont(QFont("Segoe MDL2 Assets", 29))
        search.setAlignment(Qt.AlignmentFlag.AlignCenter)
        search.setGeometry(52, 34, 46, 42)


def _recorded_play_family(row: LibraryRow) -> str:
    recorded = str((row.details or {}).get("run_pass") or "").strip()
    if recorded:
        return recorded
    # Older index rows sometimes stored a family here; a concept is not one.
    legacy = lookup("run_pass", row.play_type)
    return legacy.canonical if legacy is not None else ""


class ResultRowDelegateV3(ResultRowDelegateV2):
    """Keep five complete ledger rows visible in a restored window."""

    COLUMNS = (("PLAY", 23), ("GAME", 20), ("SOURCE TIME", 16),
               ("TYPE", 9), ("RESULT", 14), ("PLAYER", 18))
    BACKGROUND_SELECTED = "#1a2a20"
    BACKGROUND_EVEN = "#111214"
    BACKGROUND_ODD = "#111214"
    DIVIDER = "#262a2f"

    def __init__(self, screen: QWidget, parent=None) -> None:
        super().__init__(parent)
        self._screen = screen

    @classmethod
    def column_widths(cls, width: int) -> list[int]:
        available = max(0, width - 94)
        widths = [available * weight // 100 for _, weight in cls.COLUMNS]
        widths[-1] += available - sum(widths)
        return widths

    def paint(self, painter, option, index) -> None:
        row = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(row, LibraryRow):
            return super().paint(painter, option, index)
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.fillRect(rect, QColor(self.BACKGROUND_SELECTED if selected else
                         self.BACKGROUND_EVEN if index.row() % 2 == 0 else self.BACKGROUND_ODD))
        if selected:
            painter.fillRect(QRect(rect.left(), rect.top(), 3, rect.height()), QColor("#e9aa3f"))
        painter.setPen(QColor(self.DIVIDER))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        thumbnail = QRect(rect.left() + 10, rect.top() + 5, 66, rect.height() - 10)
        painter.fillRect(thumbnail, QColor("#0a0b0c"))
        if isinstance(icon, QIcon) and not icon.isNull():
            image = icon.pixmap(132, 74).scaled(thumbnail.size(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            image.setDevicePixelRatio(1)
            painter.drawPixmap(thumbnail.center() - image.rect().center(), image)
        font = QFont("Segoe UI")
        font.setPixelSize(13)
        title_font = QFont(font)
        title_font.setWeight(QFont.Weight.DemiBold)
        values = (row.clip_title or f"Play {row.clip_number:03d}", row.game_label,
                  f"{format_ms(row.start_ms)} – {format_ms(row.end_ms)}",
                  _recorded_play_family(row), row.result, row.player_name)
        x = rect.left() + 86
        for i, (value, width) in enumerate(zip(values, self.column_widths(rect.width()))):
            if i == 1:
                self._draw_text(painter, QRect(x, rect.top() + 3, width, 21),
                                row.project_name, font, QColor("#c3cbc8"))
                detail_font = QFont(font)
                detail_font.setPixelSize(11)
                identity = f"{row.game_year or 'Year not set'} · {row.opponent or 'Opponent not set'}"
                self._draw_text(painter, QRect(x, rect.top() + 24, width, 18),
                                identity, detail_font, QColor("#8d949a"))
                x += width
                continue
            self._draw_text(painter, QRect(x, rect.top(), width, rect.height()),
                            value or "—", title_font if i == 0 else font,
                            QColor("#e6e8e6" if i == 0 else "#8d949a"))
            x += width
        painter.restore()

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(0, 46)


class LibrarySearchScreenV3(LibrarySearchScreenV2):
    """The locked populated, empty, and restored Library states."""

    def __init__(self, *args, **kwargs) -> None:
        self._library_catalog_empty = True
        self._library_game_scope = None
        super().__init__(*args, **kwargs)
        self.setProperty("shellV3Library", "true")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAccessibleName("TapeSift Library")
        self._outer_layout.setContentsMargins(14, 10, 14, 10)
        self.results_list.setItemDelegate(ResultRowDelegateV3(
            self, self.results_list))
        self._install_compact_transport()
        self._install_empty_states()
        self.year_combo = QComboBox()
        self.year_combo.addItem("All years", "")
        self.year_combo.addItem("Year not set", library_service.UNSET_GAME_YEAR)
        self.year_combo.setAccessibleName("Filter Library by game year")
        self.year_combo.setProperty("libraryFilter", True)
        self.year_combo.currentIndexChanged.connect(self._run_search)
        self._outer_layout.itemAt(5).layout().insertWidget(1, self.year_combo)
        self._lock_library_geometry()
        self._group_selected_details()
        self._rebuild_chips()
        self._sync_library_geometry()
        self._show_preview(None)
        for widget in (self, *self.findChildren(QWidget)):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self._apply_locked_font_families()
        self._install_summary_presentation()
        self._install_browsing_layout()

    def _install_browsing_layout(self):
        """Keep the live ledger beside a persistent preview and editing column."""
        filters = self._outer_layout.itemAt(5).layout()
        while filters.count():
            filters.takeAt(0)
        filter_groups = QVBoxLayout()
        filter_groups.setSpacing(10)
        filters.addLayout(filter_groups)
        identity_row = QHBoxLayout()
        identity_row.setSpacing(12)
        filter_groups.addLayout(identity_row)
        self._library_identity_controls = (self.year_combo, self.opponent_combo, self.project_combo)
        for title, control in zip(("YEAR", "OPPONENT", "GAME"), self._library_identity_controls):
            group = QVBoxLayout()
            group.setSpacing(4)
            caption = QLabel(title)
            caption.setStyleSheet("color:#a8b5ad;font-size:11px;font-weight:600;")
            caption.setBuddy(control)
            group.addWidget(caption)
            group.addWidget(control)
            control.setAccessibleName("Filter Library by " + title.lower())
            identity_row.addLayout(group, 1 if title == "YEAR" else 2)
        self.project_combo.setItemText(0, "All games")
        self._library_filter_grid = QGridLayout()
        self._library_filter_grid.setSpacing(8)
        filter_groups.addLayout(self._library_filter_grid)
        main = self._library_main_box
        main.removeWidget(self._library_workbench)
        self._library_ledger = QWidget()
        self._library_ledger.setObjectName("V3LibraryLedger")
        ledger = QVBoxLayout(self._library_ledger)
        ledger.setContentsMargins(0, 0, 0, 0)
        ledger.setSpacing(0)
        while main.count():
            item = main.takeAt(0)
            if item.widget() is not None:
                ledger.addWidget(item.widget(), 1 if item.widget() in (self.results_list, self.empty_label) else 0)
            elif item.layout() is not None:
                ledger.addLayout(item.layout())
            else:
                ledger.addItem(item)
        self._library_ledger_title.setText("CLIPS")
        self._library_ledger_title.show()
        self._library_open_hint.show()
        self._library_workbench.setMinimumSize(360, 0)
        self._library_workbench.setMaximumSize(16777215, 16777215)
        column = self._library_workbench.layout()
        column.setDirection(QHBoxLayout.Direction.TopToBottom)
        column.setContentsMargins(16, 16, 16, 12)
        column.setSpacing(12)
        column.setStretch(0, 0)
        column.setStretch(1, 1)
        self._preview_panel.layout().setContentsMargins(0, 0, 0, 8)
        actions = self._library_inspector_actions.layout()
        while actions.count():
            actions.takeAt(0)
        actions.setDirection(QHBoxLayout.Direction.TopToBottom)
        actions.setContentsMargins(0, 10, 0, 0)
        actions.setSpacing(8)
        actions.addWidget(self.game_year_row)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for button in (self._library_review_button, self.open_btn, self._library_source_button, self.save_btn):
            if button is not None:
                buttons.addWidget(button)
        actions.addLayout(buttons)
        self._library_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._library_splitter.setObjectName("V3LibraryBrowseSplit")
        self._library_splitter.setChildrenCollapsible(False)
        self._library_splitter.setHandleWidth(12)
        self._library_splitter.addWidget(self._library_ledger)
        self._library_splitter.addWidget(self._library_workbench)
        self._library_splitter.setStretchFactor(0, 1)
        self._library_splitter.setStretchFactor(1, 0)
        self._library_splitter.setSizes([1000, 440])
        self._library_splitter.splitterMoved.connect(lambda *_: self._sync_library_geometry())
        main.addWidget(self._library_splitter, 1)
        self.preview_title.setStyleSheet("font-size:17px;font-weight:600;color:#e6e8e6;background:transparent;")
        self._sync_library_geometry()

    def _install_summary_presentation(self):
        self.save_btn.setProperty("primary", "false")
        self.save_btn.setProperty("accent", "false")
        self.save_btn.setProperty("projectAction", "true")
        self.save_btn.style().unpolish(self.save_btn)
        self.save_btn.style().polish(self.save_btn)
        actions = self._library_inspector_actions.layout()
        actions.insertWidget(1, self._library_review_button)
        self._library_review_button.setText("Review Clip")
        self._library_review_button.setMinimumWidth(0)
        self._library_review_button.setFixedHeight(34)
        self._library_review_button.setStyleSheet("font-size:12px;padding:3px 7px;min-width:0;min-height:24px;max-height:24px;min-width:0;")
        self.open_btn.setText("More")
        self.open_btn.setAccessibleName("Open clip options")
        self.open_btn.setToolTip("Quick Preview or Open in Project")
        self.open_btn.setStyleSheet("font-size:11px;padding:0;min-width:0;max-width:36px;")
        self.open_btn.setFixedWidth(36)
        self.preview_title.setStyleSheet(
            "font-size:20px;font-weight:600;color:#e6e8e6;background:transparent;")
        self.preview_meta.setStyleSheet(
            "font-size:13px;color:#8d949a;background:transparent;")
        self._library_source_time.setStyleSheet(
            "font-size:13px;color:#e9aa3f;background:transparent;")

    # ---------- shared application bar ----------

    def _build_masthead(self, header: QHBoxLayout) -> None:
        self._library_header_layout = header
        buttons = {b.text(): b for b in self.findChildren(QPushButton)}
        back = buttons.get("‹ Back")
        rebuild = buttons.get("Rebuild Index")

        while header.count():
            item = header.takeAt(0)
            widget = item.widget()
            if widget is not None:
                reparent_widget(widget, self)
                widget.hide()

        if back is None:
            back = QPushButton()
            back.clicked.connect(self.back_requested.emit)
        back.setObjectName("V3LibraryBack")
        back.setText("")
        back.setIcon(tinted_standard_icon(
            self,
            QStyle.StandardPixmap.SP_ArrowBack,
            colour="#e6e8e6",
            size=QSize(17, 17),
            glyph_size=QSize(12, 12),
        ))
        back.setIconSize(QSize(18, 18))
        back.setFixedSize(35, 34)
        back.setAccessibleName("Back")
        back.setToolTip("Back")
        back.show()
        self.library_back_button = back
        header.addWidget(back, 0, Qt.AlignmentFlag.AlignVCenter)

        home = QToolButton()
        home.setObjectName("V3LibraryHome")
        home_icon = _tinted_asset_icon(
            "tapesift-home.png", QSize(22, 22), "#e6e8e6")
        if home_icon.isNull():
            home_icon = self.style().standardIcon(
                QStyle.StandardPixmap.SP_DirHomeIcon)
            home.setProperty("iconAsset", "qt-home-fallback")
        else:
            home.setProperty("iconAsset", "tapesift-home.png")
        home.setIcon(home_icon)
        home.setIconSize(QSize(21, 21))
        home.setFixedSize(35, 34)
        home.setAccessibleName("Home")
        home.setToolTip("Home")
        home.clicked.connect(self.back_requested.emit)
        self.library_home_button = home
        header.addWidget(home, 0, Qt.AlignmentFlag.AlignVCenter)

        self.library_brand_lockup = QWidget()
        brand = QHBoxLayout(self.library_brand_lockup)
        brand.setContentsMargins(8, 0, 8, 0)
        brand.setSpacing(18)
        ifi = QLabel()
        ifi.setPixmap(brand_pixmap("ifi-full-name.jpg", 50))
        ifi.setFixedSize(ifi.pixmap().size())
        ifi.setAccessibleName("Independent Football Intelligence")
        brand.addWidget(ifi)
        self.library_brand_divider = QFrame()
        self.library_brand_divider.setObjectName("V3LibraryNavDivider")
        self.library_brand_divider.setFrameShape(QFrame.Shape.VLine)
        self.library_brand_divider.setFixedSize(1, 26)
        brand.addWidget(self.library_brand_divider)
        wordmark = QLabel()
        wordmark.setPixmap(brand_pixmap("tapesift-wordmark-white.png", 28))
        wordmark.setFixedSize(wordmark.pixmap().size())
        wordmark.setAccessibleName("TapeSift")
        brand.addWidget(wordmark)
        header.addWidget(self.library_brand_lockup)

        self.library_section_label = QLabel("LIBRARY")
        self.library_section_label.setObjectName("LibrarySectionLabel")
        self.library_section_label.setAccessibleName("Library")
        self.library_section_label.setProperty("role", "librarySection")
        self.library_section_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        header.addWidget(
            self.library_section_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self.masthead_stats = QLabel("0 CLIPS / 0 PROJECTS")
        self.masthead_stats.setObjectName("LibraryMastheadStats")
        self.masthead_stats.setProperty("role", "libraryStats")
        self.masthead_stats.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        header.addWidget(
            self.masthead_stats, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)

        self.rebuild_index_button = rebuild
        if rebuild is not None:
            rebuild.setObjectName("V3LibraryRebuild")
            rebuild.setFixedSize(120, 34)
            rebuild.show()
            header.addWidget(rebuild, 0, Qt.AlignmentFlag.AlignVCenter)

    def move_masthead_to(self, shell) -> None:
        shell.add_mode_left_widget("library", self.library_back_button)
        self._library_nav_gap = QWidget()
        self._library_nav_gap.setFixedWidth(1)
        shell.add_mode_left_widget("library", self._library_nav_gap)
        shell.add_mode_left_widget("library", self.library_brand_lockup)
        self.library_home_button.setText("Home")
        self.library_home_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.library_home_button.setFixedSize(64, 36)
        shell.add_mode_left_widget("library", self.library_home_button)
        self.library_section_label.setText("Library")
        shell.add_mode_left_widget("library", self.library_section_label)
        self.masthead_stats.hide()
        settings = QPushButton("Settings")
        settings.setObjectName("V3LibrarySettings")
        settings.setFixedSize(78, 36)
        settings.clicked.connect(lambda: shell.window()._open_settings())
        shell.add_mode_left_widget("library", settings)
        shell.add_mode_left_stretch("library")
        if self.rebuild_index_button is not None:
            shell.add_mode_right_widget(
                "library", self.rebuild_index_button)
        book = QToolButton()
        book.setObjectName("V3LibraryBook")
        book.setIcon(tinted_icon("book-open-24.svg", "#8d949a", 24))
        book.setIconSize(QSize(24, 24))
        book.setFixedSize(36, 36)
        book.setAccessibleName("Search Library")
        book.setToolTip("Search Library (Ctrl+F)")
        book.clicked.connect(self.search_box.setFocus)
        shell.add_mode_right_widget("library", book)
        self._outer_layout.invalidate()
        QTimer.singleShot(0, self, self._apply_locked_font_families)

    # ---------- selected-play workbench ----------

    def _install_compact_transport(self) -> None:
        """The standard faces, on Library's own preview and selection actions."""
        column = self.preview_jog_ring.parentWidget()
        transport = column.layout().itemAt(1).layout()
        source = next(b for b in self.findChildren(QPushButton) if b.text() == "Show Source")
        while transport.count():
            item = transport.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
        self.preview_jog_ring.layout().removeWidget(self.preview_play_btn)
        self.preview_jog_ring.hide()
        owner = QWidget(column)
        owner.setObjectName("V3LibraryTransport")
        owner.setFixedSize(224, 44)
        # Inherited preview styles repolish the hidden hit targets on playback
        # changes. Lock their heights here so the painted circle cannot flatten.
        owner.setStyleSheet(
            "QToolButton { min-height: 38px; max-height: 38px; border: none; padding: 0; margin: 0; }"
            "QToolButton#LibraryPreviewPlayPause { min-height: 38px; max-height: 38px; }")
        owner.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        owner.transport_island = owner
        owner._playing = False
        owner.step_back_btn = QToolButton(owner)
        owner.step_fwd_btn = QToolButton(owner)
        owner.rewind_btn = self.preview_rewind_btn
        owner.play_btn = self.preview_play_btn
        owner.fast_forward_btn = self.preview_fast_forward_btn
        row = QHBoxLayout(owner)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for button, size in zip((owner.step_back_btn, owner.rewind_btn, owner.play_btn,
                                  owner.fast_forward_btn, owner.step_fwd_btn),
                                 ((39, 38), (43, 38), (44, 38), (43, 38), (39, 38))):
            reparent_widget(button, owner)
            button.setFixedSize(*size)
            effect = QGraphicsOpacityEffect(button)
            effect.setOpacity(0)
            button.setGraphicsEffect(effect)
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            button.show()
        for button, direction, title in ((owner.step_back_btn, -1, "Previous Library clip"),
                                         (owner.step_fwd_btn, 1, "Next Library clip")):
            button.setToolTip(title)
            button.setAccessibleName(title)
            button.clicked.connect(lambda _checked=False, step=direction: self._navigate_library_clip(step))
        self._library_transport = owner
        self._library_transport_faces = V3TransportSurface(owner)
        self._library_preview_time = QLabel("00:00.000")
        self._library_preview_time.setObjectName("V3LibraryPreviewTime")
        self._library_preview_time.setToolTip("Source time · Quick preview")
        self._library_review_button = QPushButton("Review clip")
        self._library_review_button.setObjectName("V3LibraryReviewClip")
        self._library_review_button.setToolTip("Open this clip in Review for precise J/K/L, frame stepping, Find Snap and the jog wheel")
        self._library_review_button.clicked.connect(self._review_library_clip)
        transport.addWidget(self._library_preview_time)
        transport.addStretch(1)
        transport.addWidget(owner)
        transport.addStretch(1)
        transport.addWidget(self._library_review_button)
        transport.addWidget(source)
        source.show()
        self.preview_player.playbackStateChanged.connect(self._sync_preview_transport_state)
        self.preview_player.positionChanged.connect(
            lambda position: self._library_preview_time.setText(format_ms(position)))

    def _sync_preview_transport_state(self, state) -> None:
        self._library_transport._playing = state == self.preview_player.PlaybackState.PlayingState
        self._library_transport.update()

    def _navigate_library_clip(self, direction: int) -> None:
        if len(self._selected_rows()) != 1:
            return
        index = self.results_list.row(self.results_list.selectedItems()[0]) + direction
        if 0 <= index < self.results_list.count():
            self.results_list.setCurrentRow(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def _review_library_clip(self) -> None:
        rows = self._selected_rows()
        if len(rows) == 1:
            self._open_in_project(rows[0])

    def _install_empty_states(self) -> None:
        self._library_workbench = self.findChild(
            QFrame, "V2LibraryWorkbench")
        self._library_main_surface = self.results_list.parentWidget()
        self._library_main_surface.setObjectName("V3LibraryMainSurface")
        self._library_source_button = next(
            (
                button for button in self.findChildren(QPushButton)
                if button.text() == "Show Source"
            ),
            None,
        )
        panel_layout = self._preview_panel.layout()
        self._library_details_heading = next(
            (
                label for label in self._preview_panel.findChildren(QLabel)
                if label.text() == "PLAY DETAILS"
            ),
            None,
        )
        self._library_detail_labels = [
            label for label in self._preview_panel.findChildren(QLabel)
            if label.property("role") == "libraryFieldLabel"
        ]

        empty_preview = QWidget()
        empty_preview.setObjectName("V3LibraryEmptyPreview")
        empty_preview_box = QVBoxLayout(empty_preview)
        empty_preview_box.setContentsMargins(24, 60, 35, 24)
        empty_preview_box.setSpacing(13)
        empty_preview_box.addStretch(1)
        preview_icon = _FilmSearchGlyph()
        preview_icon.setObjectName("V3LibraryEmptyPreviewIcon")
        empty_preview_box.addWidget(
            preview_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        preview_title = QLabel("No clip selected")
        preview_title.setObjectName("V3LibraryEmptyPreviewTitle")
        preview_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_preview_box.addWidget(preview_title)
        empty_preview_box.addSpacing(9)
        preview_copy = QLabel("Select a clip to preview and view details.")
        preview_copy.setObjectName("V3LibraryEmptyPreviewCopy")
        preview_copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_copy.setWordWrap(True)
        empty_preview_box.addWidget(preview_copy)
        empty_preview_box.addStretch(1)
        self.preview_thumb.addWidget(empty_preview)
        self._library_empty_preview = empty_preview

        guidance = QFrame()
        guidance.setObjectName("V3LibraryEmptyInspector")
        guidance_box = QVBoxLayout(guidance)
        guidance_box.setContentsMargins(0, 2, 0, 6)
        guidance_box.setSpacing(0)
        placeholder = QLabel("---")
        placeholder.setObjectName("V3LibraryEmptyInspectorPlaceholder")
        placeholder.setFixedHeight(14)
        guidance_box.addWidget(placeholder)
        guidance_box.addSpacing(1)
        copy = QLabel(
            "The Library will collect clips from your TapeSift projects.")
        copy.setObjectName("V3LibraryEmptyInspectorCopy")
        copy.setWordWrap(True)
        copy.setFixedHeight(24)
        guidance_box.addWidget(copy)
        guidance_box.addSpacing(12)
        rebuild = QPushButton("Rebuild Index")
        rebuild.setObjectName("V3LibraryEmptyRebuild")
        rebuild.setProperty("projectAction", "true")
        rebuild.setFixedSize(240, 46)
        rebuild.clicked.connect(
            lambda: self.rebuild_index_button.click()
            if self.rebuild_index_button is not None else None)
        rebuild_row = QHBoxLayout()
        rebuild_row.setContentsMargins(0, 0, 0, 0)
        rebuild_row.setSpacing(0)
        rebuild_row.addStretch(46)
        rebuild_row.addWidget(rebuild)
        rebuild_row.addStretch(54)
        guidance_box.addLayout(rebuild_row)
        guidance_box.addSpacing(25)
        hint = QLabel(
            "Rebuild the index to find clips from your projects.")
        hint.setObjectName("V3LibraryEmptyInspectorHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setWordWrap(True)
        hint.setFixedHeight(24)
        guidance_box.addWidget(hint)
        guidance_box.addStretch(1)
        divider = QFrame()
        divider.setObjectName("V3LibraryEmptyInspectorDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFixedHeight(1)
        panel_layout.insertWidget(3, divider)
        panel_layout.insertWidget(4, guidance)
        self._library_empty_divider = divider
        self._library_inspector_placeholder = placeholder
        self._library_empty_inspector = guidance
        self._library_empty_inspector.setFixedHeight(177)

        ledger_empty = QFrame()
        ledger_empty.setObjectName("V3LibraryLedgerEmpty")
        ledger_box = QVBoxLayout(ledger_empty)
        ledger_box.setContentsMargins(20, 22, 20, 22)
        ledger_box.setSpacing(8)
        ledger_box.addStretch(1)
        icon = QLabel()
        icon.setObjectName("V3LibraryEmptyIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_path = (
            Path(__file__).resolve().parents[1]
            / "resources" / "icons" / "tapesift-library-empty-lock.png"
        )
        icon_source = QPixmap(str(icon_path))
        # ImageGen keeps generous transparent padding around the standard
        # lock asset; trim that padding before fitting the 58x50 slot.
        icon_source = icon_source.copy(210, 348, 837, 557)
        icon.setPixmap(icon_source.scaled(
            58,
            50,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        icon.setProperty("iconAsset", "tapesift-library-empty-lock")
        icon.setFixedHeight(60)
        ledger_box.addWidget(icon)
        title = QLabel("No clips in the Library yet")
        title.setObjectName("V3LibraryLedgerEmptyTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ledger_box.addWidget(title)
        body = QLabel("Clips from your TapeSift projects will appear here.")
        body.setObjectName("V3LibraryLedgerEmptyBody")
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setWordWrap(True)
        ledger_box.addWidget(body)
        help_text = QLabel(
            "Create or open a project and add clips, or rebuild the index "
            "to find existing clips.")
        help_text.setObjectName("V3LibraryLedgerEmptyHint")
        help_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        help_text.setWordWrap(True)
        ledger_box.addWidget(help_text)
        ledger_box.addStretch(1)
        ledger_box.setStretch(0, 1)
        ledger_box.setStretch(ledger_box.count() - 1, 2)
        self._library_main_surface.layout().addWidget(ledger_empty, 10)
        self._library_ledger_empty = ledger_empty
        self.empty_label.hide()

    def _lock_library_geometry(self) -> None:
        """Bind the 1708px Library composition without changing behavior."""
        self._outer_layout.setContentsMargins(18, 8, 18, 22)
        self._outer_layout.setSpacing(11)
        initial_spacer = self._outer_layout.itemAt(3).spacerItem()
        if initial_spacer is not None:
            initial_spacer.changeSize(
                0,
                0,
                QSizePolicy.Policy.Minimum,
                QSizePolicy.Policy.Fixed,
            )
        self.search_box.setFixedHeight(43)
        self.search_box.addAction(tinted_icon("search-16.svg", "#8d949a", 16), QLineEdit.ActionPosition.LeadingPosition)
        filter_widths = (
            (self.project_combo, 206),
            (self.year_combo, 108),
            (self.tags_button, 92),
            (self.opponent_combo, 236),
            (self.player_combo, 211),
            (self.role_combo, 168),
            (self.date_combo, 170),
            (self.duration_combo, 181),
            (self.sort_combo, 167),
        )
        self._library_filter_widths = filter_widths
        self._library_filter_arrows: dict[QComboBox, QToolButton] = {}
        for control, width in filter_widths:
            control.setFixedSize(width, 34)
            if isinstance(control, QComboBox):
                arrow = QToolButton(control)
                arrow.setObjectName("V3LibraryFilterArrow")
                arrow.setArrowType(Qt.ArrowType.DownArrow)
                arrow.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                arrow.setAttribute(
                    Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                arrow.setGeometry(width - 23, 1, 22, 32)
                arrow.show()
                self._library_filter_arrows[control] = arrow
        self.tags_button.setFixedHeight(34)
        self.tags_button.setText("Tags")
        filter_row = self._outer_layout.itemAt(5).layout()
        filter_row.setSpacing(8)
        self.clear_filters_button = QPushButton("Clear filters")
        self.clear_filters_button.setObjectName("V3LibraryClearFilters")
        self.clear_filters_button.setFixedSize(94, 34)
        self.clear_filters_button.clicked.connect(self._clear_library_filters)
        filter_row.addWidget(self.clear_filters_button)

        workbench_row = self._library_workbench.layout()
        workbench_row.setContentsMargins(12, 10, 6, 0)
        workbench_row.setSpacing(16)
        # The body yields to a fixed action row, including with tall Notes.
        workbench_row.removeWidget(self._preview_panel)
        self._library_inspector = QWidget(self._library_workbench)
        self._library_inspector.setObjectName("V3LibraryInspectorColumn")
        self._library_inspector.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._library_inspector.setStyleSheet(
            "QWidget#V3LibraryInspectorColumn { background: transparent; border: none; }")
        self._library_inspector.setMinimumWidth(0)
        inspector_column = QVBoxLayout(self._library_inspector)
        inspector_column.setContentsMargins(0, 0, 0, 0)
        inspector_column.setSpacing(0)
        self._library_inspector_scroll = QScrollArea(self._library_inspector)
        self._library_inspector_scroll.setObjectName("V3LibraryInspectorScroll")
        self._library_inspector_scroll.setWidgetResizable(True)
        self._library_inspector_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._library_inspector_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._library_inspector_scroll.setWidget(self._preview_panel)
        inspector_column.addWidget(self._library_inspector_scroll, 1)
        self._library_inspector_actions = QWidget(self._library_inspector)
        self._library_inspector_actions.setObjectName("V3LibraryInspectorActions")
        self._library_inspector_actions.setStyleSheet(
            "QWidget#V3LibraryInspectorActions { background: transparent; border: none; }")
        self._library_inspector_actions.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        inspector_column.addWidget(self._library_inspector_actions)
        workbench_row.addWidget(self._library_inspector)
        workbench_row.setStretch(0, 49)
        workbench_row.setStretch(1, 51)
        inspector = self._preview_panel.layout()
        inspector.setContentsMargins(18, 0, 16, 0)
        inspector.setSpacing(0)

        self._library_inspector_heading = next(
            label for label in self._preview_panel.findChildren(QLabel)
            if label.text() == "SELECTED PLAY"
        )
        self._library_notes_heading = next(
            label for label in self._preview_panel.findChildren(QLabel)
            if label.text() == "NOTES"
        )
        self._library_inspector_heading.setFixedHeight(25)
        self.preview_title.setFixedHeight(31)
        self.preview_meta.setFixedHeight(36)
        self._library_notes_heading.setFixedHeight(25)

        self._empty_panel_spacers: list[tuple[QSpacerItem, int]] = []
        for widget, height in (
            (self._library_inspector_heading, 2),
            (self.preview_title, 0),
            (self.preview_meta, 16),
            (self._library_empty_inspector, 16),
        ):
            index = inspector.indexOf(widget)
            spacer = QSpacerItem(
                0,
                height,
                QSizePolicy.Policy.Minimum,
                QSizePolicy.Policy.Fixed,
            )
            inspector.insertItem(index + 1, spacer)
            self._empty_panel_spacers.append((spacer, height))

        main_box = self._library_main_surface.layout()
        main_box.setContentsMargins(0, 0, 0, 7)
        main_box.setSpacing(0)
        main_box.insertSpacing(1, 13)
        self._library_main_box = main_box
        self._library_ledger_title = next(
            label for label in self.findChildren(QLabel)
            if label.text() == "CLIP LEDGER"
        )
        self._library_ledger_title.hide()
        title_row = main_box.itemAt(2).layout()
        if title_row is not None:
            title_row.setContentsMargins(10, 0, 0, 0)
        self._library_open_hint = next(
            (
                label for label in self.findChildren(QLabel)
                if label.text() == "DOUBLE-CLICK A ROW TO OPEN"
            ),
            None,
        )
        if self._library_open_hint is not None:
            self._library_open_hint.hide()
        self._library_ledger_header = self.findChild(
            QFrame, "V2LibraryLedgerHeader")
        self._library_ledger_header.setFixedHeight(32)

        self.count_label.setFixedHeight(44)
        self.clear_sel_btn.setObjectName("V3LibraryClearSelection")
        self.reel_btn.setObjectName("V3LibraryBuildReel")
        self.export_btn.setObjectName("V3LibraryExport")
        self.reel_btn.setFixedSize(142, 44)
        self.export_btn.setFixedSize(154, 44)
        self.clear_sel_btn.setFixedSize(142, 44)
        footer = self._outer_layout.itemAt(8).layout()
        footer.insertSpacing(1, 17)
        export_index = footer.indexOf(self.export_btn)
        footer.insertSpacing(export_index, 14)

    def _apply_locked_font_families(self) -> None:
        """Use the locked neutral Windows typography across the Library."""
        controls = (QLabel, QPushButton, QLineEdit, QComboBox,
                    QPlainTextEdit, QToolButton)
        for widget in self.findChildren(QWidget):
            if not isinstance(widget, controls):
                continue
            summary = getattr(self, "_selected_summary", None)
            if (summary is not None and summary.isAncestorOf(widget)) or (
                    hasattr(self, "_library_inspector_actions") and self._library_inspector_actions.isAncestorOf(widget)):

                continue
            if widget.objectName() in {
                "V3LibraryFilmGlyph",
                "V3LibrarySearchGlyph",
            }:
                family = "Segoe MDL2 Assets"
            else:
                family = "Segoe UI"
            font = widget.font()
            font.setFamily(family)
            if widget.objectName() == "V3LibraryFilmGlyph":
                font.setPixelSize(56)
                direct_style = (
                    'font-family: "Segoe MDL2 Assets"; font-size: 56px; '
                    'color: #8d949a; background: transparent; border: none;')
            elif widget.objectName() == "V3LibrarySearchGlyph":
                font.setPixelSize(38)
                direct_style = (
                    'font-family: "Segoe MDL2 Assets"; font-size: 38px; '
                    'color: #8d949a; background: transparent; border: none;')
            elif widget.objectName() == "V3LibraryEmptyIcon":
                direct_style = (
                    'background: transparent; border: none;')
            elif widget.objectName() == "V3LibraryLedgerEmptyTitle":
                font.setStretch(103)
                direct_style = ""
            elif widget.objectName() == "V3LibraryEmptyPreviewTitle":
                font.setStretch(96)
                direct_style = ""
            elif widget.objectName() == "V3LibraryEmptyPreviewCopy":
                font.setStretch(107)
                direct_style = ""
            else:
                direct_style = ""
            # Re-polishing an unchanged sheet resets the resizable Notes minimum.
            if widget.styleSheet() != direct_style:
                widget.setStyleSheet(direct_style)
            widget.setFont(font)

    def _set_empty_inspector_geometry(self, active: bool) -> None:
        for spacer, locked_height in self._empty_panel_spacers:
            spacer.changeSize(
                0,
                locked_height if active else 0,
                QSizePolicy.Policy.Minimum,
                QSizePolicy.Policy.Fixed,
            )
        inspector = self._preview_panel.layout()
        inspector.setSpacing(0 if active else (
            3 if self.height() < 760 else 6))
        if active:
            self.preview_notes.setFixedHeight(33)
            self.notes_resize_handle.hide()
        else:
            self.notes_resize_handle.show()
            self.notes_resize_handle.set_editor_height(
                getattr(self.settings, "library_notes_height", 72))
        inspector.invalidate()

    def _rebuild_chips(self) -> None:
        super()._rebuild_chips()
        self._chips_layout.parentWidget().setVisible(bool(self._active_tags))

    def _clear_library_filters(self) -> None:
        controls = (self.search_box, self.project_combo, self.year_combo, self.opponent_combo,
                    self.player_combo, self.role_combo, self.date_combo, self.duration_combo)
        blockers = [QSignalBlocker(control) for control in controls]
        self._search_timer.stop()
        self.search_box.clear()
        for combo in controls[1:]:
            combo.setCurrentIndex(0)
        self._active_tags.clear()
        self._rebuild_chips()
        self._rebuild_tag_menu()
        del blockers
        self._run_search()

    def _group_selected_details(self) -> None:
        """Keep one editable value per field, with common clip fields first."""
        panel = self._preview_panel.layout()
        while panel.count():
            item = panel.takeAt(0)
            if item.layout() is not None:
                shiboken6.delete(item.layout())
        self._empty_panel_spacers = []
        self._library_details_heading.hide()
        self._library_empty_divider.hide()
        self._library_notes_heading.hide()
        for label in self._library_detail_labels:
            label.hide()
        self._library_detail_labels = []
        for edit in self._v2_detail_edits.values():
            edit.hide()
        # These native combo editors own the same full detail map saved by V1/V2.
        self._v2_detail_edits = {}
        self._library_play_edits = {
            key: self.preview_details[key] for key in ("play_type", "play_action")}
        self._library_play_labels = {}
        self._library_group_widgets = []
        self._selected_summary = QWidget()
        self._selected_summary.setObjectName("V3LibrarySelectedSummary")
        summary = QVBoxLayout(self._selected_summary)
        summary.setContentsMargins(0, 0, 0, 4)
        summary.setSpacing(4)
        self._library_inspector_heading.setText("SELECTED CLIP")
        self._library_inspector_heading.setFixedHeight(20)
        self.preview_title.setFixedHeight(32)
        self.preview_meta.setFixedHeight(22)
        self._library_source_time = QLabel()
        self._library_source_time.setFixedHeight(20)
        for widget in (self._library_inspector_heading, self.preview_title,
                       self.preview_meta, self._library_source_time):
            summary.addWidget(widget)
        panel.addWidget(self._selected_summary)
        panel.addWidget(self._library_empty_inspector)

        def add_field(grid, key, caption, row, column):
            edit = self.preview_details[key]
            edit.setMinimumWidth(0)
            edit.setMinimumContentsLength(1)
            edit.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            edit.setFixedHeight(30)
            edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            edit.setProperty("libraryFilter", False)
            edit.setObjectName("V3LibraryDetail_" + key)
            edit.setAccessibleName("Play concept" if key == "play_type" else caption)
            edit.lineEdit().setPlaceholderText(caption)
            label = QLabel(caption)
            label.setProperty("role", "libraryFieldLabel")
            label.setFixedHeight(18)
            label.setBuddy(edit)
            grid.addWidget(label, row * 2, column)
            grid.addWidget(edit, row * 2 + 1, column)
            self._library_group_widgets.extend((label, edit))
            self._library_play_labels[key] = label
            return edit

        self._library_quick_fields = QWidget()
        self._library_quick_fields.setObjectName("V3LibraryQuickFields")
        quick = QGridLayout(self._library_quick_fields)
        quick.setContentsMargins(0, 0, 0, 8)
        quick.setHorizontalSpacing(18)
        quick.setVerticalSpacing(3)
        for key, caption, row, column in (
                ("run_pass", "Type", 0, 0),
                ("player_name", "Primary Player", 0, 1),
                ("result", "Result", 1, 0),
                ("other_players", "Secondary Players", 1, 1)):
            add_field(quick, key, caption, row, column)
        panel.addWidget(self._library_quick_fields)
        self._library_group_widgets.append(self._library_quick_fields)

        self.game_year_row = QWidget()
        self.game_year_row.setObjectName("V3LibraryGameYearActions")
        year_row = QHBoxLayout(self.game_year_row)
        year_row.setContentsMargins(0, 0, 0, 0)
        year_row.setSpacing(6)
        year_row.addWidget(QLabel("Game year"))
        self.game_year_edit = QSpinBox()
        self.game_year_edit.setRange(1799, 2199)
        self.game_year_edit.setSpecialValueText("Not set")
        self.game_year_edit.setFixedWidth(92)
        self.game_year_edit.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self.game_year_edit.setStyleSheet("QSpinBox {padding:2px 4px;}")
        self.game_year_edit.setAccessibleName("Selected game's year")
        self.game_year_edit.setToolTip("Applies to every clip in this game. Not the import year.")
        year_row.addWidget(self.game_year_edit)
        self.game_year_save = QPushButton("Apply to game")
        self.game_year_save.clicked.connect(self._save_game_year)
        year_row.addWidget(self.game_year_save)
        year_row.addStretch(1)
        self.game_year_status = QLabel()
        self.game_year_status.setWordWrap(True)
        panel.addWidget(self.game_year_status)

        rule = QFrame()
        rule.setObjectName("V3LibraryDetailsRule")
        rule.setFixedHeight(1)
        panel.addWidget(rule)
        extra = QGridLayout()
        extra.setHorizontalSpacing(18)
        extra.setVerticalSpacing(3)
        for index, (key, caption) in enumerate((
                ("quarter", "Quarter"), ("down_distance", "Down & Distance"),
                ("ball_on", "Ball On"), ("yards", "Gain · yards"),
                ("yac", "YAC · yards"), ("quarterback", "Quarterback"),
                ("play_type", "Concept"), ("play_action", "Play action"),
                ("off_formation", "Formation"), ("def_formation", "Defensive front"),
                ("off_personnel", "Offensive personnel"), ("def_personnel", "Defensive personnel"),
                ("action", "Actions"))):
            add_field(extra, key, caption, index // 2, index % 2)
        panel.addLayout(extra)
        self._library_group_widgets.append(rule)
        for caption, editor in (("Notes", self.preview_notes), ("Tags", self.preview_tags)):
            label = QLabel(caption)
            label.setProperty("role", "libraryFieldLabel")
            panel.addWidget(label)
            panel.addWidget(editor)
            self._library_group_widgets.extend((label, editor))
            if editor is self.preview_notes:
                panel.addWidget(self.notes_resize_handle)
        self.preview_tags.setMinimumWidth(0)
        panel.addStretch(1)

        actions = QHBoxLayout(self._library_inspector_actions)
        actions.setContentsMargins(18, 6, 16, 6)
        actions.setSpacing(6)
        actions.addWidget(self.game_year_row)
        actions.addWidget(self.open_btn)
        if self._library_source_button is not None:
            actions.addWidget(self._library_source_button)
        actions.addWidget(self.save_btn)
        for button in (self.open_btn, self._library_source_button, self.save_btn, self.game_year_save):
            if button is not None:
                button.setMinimumWidth(0)
                button.setFixedHeight(34)
                button.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
                button.setStyleSheet("font-size:12px;padding:3px 7px;min-width:0;min-height:24px;max-height:24px;")

    def _build_ledger_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("V2LibraryLedgerHeader")
        header.setProperty("ledgerHeader", "true")
        row = QHBoxLayout(header)
        row.setContentsMargins(91, 0, 3, 0)
        row.setSpacing(0)
        self._library_column_labels = []
        for text, _weight in ResultRowDelegateV3.COLUMNS:
            label = QLabel(text)
            label.setProperty("role", "ledgerColumn")
            row.addWidget(label)
            self._library_column_labels.append(label)
        return header

    def _sync_library_geometry(self) -> None:
        restored = self.height() < 760
        self.masthead_stats.hide()
        self.library_brand_lockup.layout().itemAt(2).widget().setVisible(self.width() >= 1100)
        self._outer_layout.setContentsMargins(18, 8, 18, 8 if restored else 22)
        self._outer_layout.setSpacing(8 if restored else 11)
        browsing = hasattr(self, "_library_splitter")
        if not browsing:
            self._library_workbench.setFixedHeight(290 if self.height() < 660 else 310 if restored else 340)
            self._library_main_box.itemAt(1).spacerItem().changeSize(
                0, 6 if restored else 13, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        for label, width in zip(self._library_column_labels,
                                ResultRowDelegateV3.column_widths(self.results_list.viewport().width())):
            label.setFixedWidth(width)
        base_widths = getattr(self, "_library_filter_widths", ())
        if hasattr(self, "_library_filter_grid"):
            primary = self._library_identity_controls
            for control in primary:
                control.setMinimumWidth(0)
                control.setMaximumWidth(16777215)
                control.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
                arrow = self._library_filter_arrows.get(control)
                if arrow is not None:
                    arrow.setGeometry(control.width() - 23, 1, 22, 32)
            controls = [control for control, _ in base_widths if control not in primary] + [self.clear_filters_button]
            columns = 4 if self.width() < 1500 else 7
            for index, control in enumerate(controls):
                self._library_filter_grid.removeWidget(control)
                self._library_filter_grid.addWidget(control, index // columns, index % columns)
                control.setFixedWidth(max(80, (self.width() - 36 - 8 * (columns - 1)) // columns))
                arrow = self._library_filter_arrows.get(control)
                if arrow is not None:
                    arrow.setGeometry(control.width() - 23, 1, 22, 32)
            for index in range(10):
                self._library_filter_grid.setColumnStretch(index, 1 if index < columns else 0)
        elif base_widths:
            spacing_total = 8 * (len(base_widths) - 1)
            available = max(1, self.width() - 36 - 102)
            base_total = sum(width for _control, width in base_widths)
            scale = min(1.0, max(0.1, (
                available - spacing_total) / base_total))
            fitted = [round(width * scale) for _control, width in base_widths]
            fitted[-1] += available - spacing_total - sum(fitted)
            for (control, _base_width), width in zip(base_widths, fitted):
                control.setFixedWidth(width)
                arrow = getattr(self, "_library_filter_arrows", {}).get(
                    control)
                if arrow is not None:
                    arrow.setGeometry(width - 23, 1, 22, 32)
        # A shorter preview leaves editing room in restored windows.
        preview_height = (min(220 if self.height() >= 800 else 120,
                              max(112, round((self._library_workbench.width() - 32) * 9 / 16)))
                          if browsing else self._library_workbench.height() - 74)
        self.preview_thumb.setMinimumHeight(preview_height)
        self.preview_thumb.setMaximumHeight(preview_height)
        self.preview_placeholder.setMinimumHeight(preview_height)
        self.preview_placeholder.setMaximumHeight(preview_height)
        self.preview_video.setMinimumHeight(preview_height)
        self.preview_video.setMaximumHeight(preview_height)
        inspector = self._preview_panel.layout()
        if not self._library_catalog_empty:
            inspector.setSpacing(3 if restored else 6)
        if not self._library_catalog_empty:
            self.notes_resize_handle.set_editor_height(
                getattr(self.settings, "library_notes_height", 72))

    def paintEvent(self, event):
        super().paintEvent(event)
        paint_slate(self)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_library_workbench"):
            self._sync_library_geometry()
            self._rescale_selected_thumbnail()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_locked_font_families()
        QTimer.singleShot(0, self, self._apply_locked_font_families)

    def _rescale_selected_thumbnail(self) -> None:
        source = getattr(self, "_v3_thumbnail_source", None)
        if source is None or source.isNull() \
                or self.preview_thumb.currentWidget() is not \
                self.preview_placeholder:
            return
        available = self.preview_placeholder.size()
        target = QSize(
            available.width(),
            available.height(),
        )
        scaled = source.scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - target.width()) // 2)
        y = max(0, (scaled.height() - target.height()) // 2)
        self.preview_placeholder.setPixmap(
            scaled.copy(x, y, target.width(), target.height()))

    # ---------- state and behavior parity ----------

    def refresh(self) -> None:
        if hasattr(self, "year_combo"):
            self._refill_game_years()
        self._library_game_scope = None
        super().refresh()
        clips, projects = library_service.stats()
        self.stats_label.setText(f"{clips} CLIPS / {projects} PROJECTS")
        self.masthead_stats.setText(self.stats_label.text())
        self.library_section_label.setToolTip(self.stats_label.text())
        self._sync_empty_state(clips == 0)

    def _refill_game_years(self) -> None:
        current = self.year_combo.currentData()
        with QSignalBlocker(self.year_combo):
            self.year_combo.clear()
            self.year_combo.addItem("All years", "")
            for year in library_service.game_years():
                self.year_combo.addItem(year, year)
            self.year_combo.addItem("Year not set", library_service.UNSET_GAME_YEAR)
            self.year_combo.setCurrentIndex(max(0, self.year_combo.findData(current)))

    def _run_search(self) -> None:
        if hasattr(self, "year_combo"):
            scope = (self.year_combo.currentData() or "", self.opponent_combo.currentData() or "")
            if scope != self._library_game_scope:
                games = library_service.projects(game_year=scope[0], opponent=scope[1]) if any(scope) else library_service.projects()
                current = self.project_combo.currentData()
                with QSignalBlocker(self.project_combo):
                    self.project_combo.clear()
                    self.project_combo.addItem("All games", "")
                    for name, path in games:
                        self.project_combo.addItem(name, path)
                    self.project_combo.setCurrentIndex(max(0, self.project_combo.findData(current)))
                self._library_game_scope = scope
        super()._run_search()

    def _save_game_year(self):
        row = getattr(self, "_editing_row", None)
        apply_year = getattr(self, "apply_game_year", None)
        if row is None or apply_year is None:
            return
        value = self.game_year_edit.value()
        year = "" if value == 1799 else str(value)
        ok, error = apply_year(row.project_path, year)
        if not ok:
            self.game_year_status.setText(error)
            return
        # Changing game identity must not commit or discard a clip's draft.
        edits = [self.preview_title, self.preview_tags, *self.preview_details.values()]
        draft = [(edit, edit.text()) for edit in edits]
        notes = self.preview_notes.toPlainText()
        filtered_year = self.year_combo.currentData()
        self._refill_game_years()
        with QSignalBlocker(self.year_combo):
            if filtered_year:
                self.year_combo.setCurrentIndex(max(0, self.year_combo.findData(year or library_service.UNSET_GAME_YEAR)))
        self._library_game_scope = None
        self._search_timer.stop()
        self._run_search()
        if not any(result.clip_uid == row.clip_uid for result in self._results):
            # A text search containing the old year can exclude the edited game.
            # Keep that game in view so its unsaved clip draft remains accessible.
            self.search_box.clear()
            self._search_timer.stop()
            self._run_search()
        for index, result in enumerate(self._results):
            if result.clip_uid == row.clip_uid:
                self.results_list.setCurrentRow(index)
                for edit, text in draft:
                    edit.setText(text)
                self.preview_notes.setPlainText(notes)
                break
        self.game_year_status.setText(f"Game year {'set to ' + year if year else 'cleared'} for every clip in this project.")

    def _show_preview(self, row: LibraryRow | None, count: int = 0) -> None:
        # Call the behavioral authority directly. V2's final thumbnail repaint
        # targets its QStackedWidget instead of the QLabel inside that stack.
        LibrarySearchScreen._show_preview(self, row, count)
        if hasattr(self, "game_year_row"):
            self.game_year_row.setVisible(row is not None)
            self.game_year_status.clear()
            self.game_year_edit.setValue(int(row.game_year) if row and row.game_year else 1799)
            if row:
                self.preview_meta.setText(self.preview_meta.text().replace(row.project_name, row.game_label, 1))
        detail_edits = getattr(self, "_v2_detail_edits", None)
        if detail_edits is None:
            return
        for key, edit in detail_edits.items():
            edit.setEnabled(row is not None)
            edit.setText(self.preview_details[key].text() if row else "")
        self._v3_thumbnail_source = None
        if row and row.thumbnail_path and Path(row.thumbnail_path).is_file():
            pixmap = QPixmap(row.thumbnail_path)
            if not pixmap.isNull():
                self._v3_thumbnail_source = pixmap
                self._rescale_selected_thumbnail()
        if row is not None and self._v3_thumbnail_source is None:
            self.preview_placeholder.setPixmap(QPixmap())
            self.preview_placeholder.setText("No thumbnail")
        if row is None:
            self.preview_title.setText("No clip selected")
            self.preview_meta.setText(
                "Select a clip from the Library to view details.")
        for key, edit in getattr(self, "_library_play_edits", {}).items():
            edit.setEnabled(row is not None)
            value = str((row.details or {}).get(key, "")) if row else ""
            label = "Play concept" if key == "play_type" else "Play action"
            edit.setToolTip(f"{label}: {value or 'Not recorded'}")
        self._sync_selection_presentation(row)
        if hasattr(self, "_library_source_time"):
            self.preview_title.setToolTip(self.preview_title.text())
            if not self.preview_title.hasFocus():
                self.preview_title.setCursorPosition(0)
            self.preview_meta.setText(row.game_label if row else "Select a clip from the Library to view details.")
            self._library_source_time.setText(
                f"{format_ms(row.start_ms)} – {format_ms(row.end_ms)} · {row.duration_ms/1000:.1f}s" if row else "")
            self._library_source_time.setToolTip(
                "Source video missing" if row and not Path(row.source_video_path).is_file() else "Source time")
            self._library_preview_time.setText(format_ms(row.start_ms) if row else "00:00.000")

    def _sync_selection_presentation(self, row: LibraryRow | None) -> None:
        selected = row is not None
        empty_library = not bool(getattr(self, "_results", ())) \
            and self._library_catalog_empty
        self._library_empty_inspector.setVisible(empty_library)
        self._set_empty_inspector_geometry(empty_library and not selected)
        if empty_library and not selected:
            self.preview_thumb.setCurrentWidget(self._library_empty_preview)
        elif not selected:
            self.preview_thumb.setCurrentWidget(self.preview_placeholder)
        self.preview_tags.setVisible(selected)
        if self._library_details_heading is not None:
            self._library_details_heading.hide()
        for label in self._library_detail_labels:
            label.setVisible(selected)
        for edit in self._v2_detail_edits.values():
            edit.setVisible(selected)
        self.save_btn.setVisible(selected)
        self.open_btn.setVisible(selected)
        self._library_inspector_actions.setVisible(selected)
        self.preview_rewind_btn.setVisible(selected)
        self.preview_play_btn.setVisible(selected)
        self.preview_fast_forward_btn.setVisible(selected)
        if self._library_source_button is not None:
            self._library_source_button.setEnabled(selected)
        for widget in getattr(self, "_library_group_widgets", ()):
            widget.setVisible(selected)
        if hasattr(self, "_library_transport"):
            self._library_transport.setVisible(selected)
            self._library_review_button.setEnabled(selected)
            index = self.results_list.row(self.results_list.selectedItems()[0]) if selected else -1
            self._library_transport.step_back_btn.setEnabled(selected and index > 0)
            self._library_transport.step_fwd_btn.setEnabled(selected and index < self.results_list.count() - 1)

    def _sync_empty_state(self, empty_library: bool) -> None:
        self._library_catalog_empty = bool(empty_library)
        self._library_ledger_empty.setVisible(empty_library)
        if empty_library:
            self.results_list.hide()
            self.empty_label.hide()
            self._library_empty_inspector.show()
            self.export_btn.setEnabled(False)
            self.reel_btn.setEnabled(False)
            self.open_btn.setEnabled(False)
            self.clear_sel_btn.show()
            self.clear_sel_btn.setEnabled(False)
            self.count_label.setText("0 selected")
            self.export_btn.setText("Export 0 clips...")
        else:
            self._library_ledger_empty.hide()
            self.clear_sel_btn.setEnabled(True)
        self._sync_selection_presentation(
            getattr(self, "_editing_row", None))


class LazyLibrarySearchScreenV3(QWidget):
    """Stable V3 page that constructs the Library on its first request."""

    back_requested = Signal()
    open_clip_requested = Signal(str, str)

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._screen: LibrarySearchScreenV3 | None = None
        self._masthead_shell = None
        self.apply_edit = None
        self.apply_game_year = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

    def _ensure_screen(self) -> LibrarySearchScreenV3:
        if self._screen is None:
            screen = LibrarySearchScreenV3(self._settings, self)
            screen.back_requested.connect(self.back_requested.emit)
            screen.open_clip_requested.connect(self.open_clip_requested.emit)
            screen.apply_edit = self.apply_edit
            screen.apply_game_year = self.apply_game_year
            self.layout().addWidget(screen)
            if self._masthead_shell is not None:
                screen.move_masthead_to(self._masthead_shell)
            self._screen = screen
        return self._screen

    def move_masthead_to(self, shell) -> None:
        self._masthead_shell = shell

    def refresh(self) -> None:
        self._ensure_screen().refresh()

    def _stop_inline_preview(self) -> None:
        if self._screen is not None:
            self._screen._stop_inline_preview()

    def __getattr__(self, name):
        return getattr(self._ensure_screen(), name)
