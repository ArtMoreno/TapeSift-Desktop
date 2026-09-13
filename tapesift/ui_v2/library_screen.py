"""V2 Library presentation over the production Library behavior."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QSizePolicy, QStyle, QStyleOptionViewItem,
    QVBoxLayout, QWidget,
)

from tapesift.ui_core.layout_ownership import detach_widget, reparent_widget
from tapesift.core.config import AppSettings
from tapesift.services.library_service import LibraryRow
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_core.library_screen import LibrarySearchScreen, ResultRowDelegate
from tapesift.ui_v2.start_screen import _brand_image_label


class NotesResizeHandle(QFrame):
    """Small vertical drag handle that resizes one notes editor."""

    height_committed = Signal(int)

    def __init__(self, editor: QPlainTextEdit, parent=None) -> None:
        super().__init__(parent)
        self.editor = editor
        self.setObjectName("LibraryNotesResizeHandle")
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setFixedWidth(36)
        self.setFixedHeight(7)
        self.setToolTip("Drag to resize Notes")
        self._drag_y: float | None = None
        self._start_height = 72

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_y = event.globalPosition().y()
            self._start_height = self.editor.maximumHeight()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_y is None:
            super().mouseMoveEvent(event)
            return
        height = self._start_height + int(
            event.globalPosition().y() - self._drag_y)
        self.set_editor_height(height)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_y is not None:
            self._drag_y = None
            self.height_committed.emit(self.editor.maximumHeight())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def set_editor_height(self, height: int) -> int:
        height = max(44, min(240, int(height)))
        self.editor.setMinimumHeight(height)
        self.editor.setMaximumHeight(height)
        return height


class ResultRowDelegateV2(ResultRowDelegate):
    """Paint Library results as a compact, aligned scouting ledger."""

    PLAY_WIDTH = 200
    GAME_WIDTH = 270
    TIME_WIDTH = 88
    DURATION_WIDTH = 82
    TYPE_WIDTH = 92
    FORMATION_WIDTH = 112
    RESULT_WIDTH = 112
    PLAYER_WIDTH = 112
    BACKGROUND_SELECTED = "#2c4a31"
    BACKGROUND_EVEN = "#1b1c19"
    BACKGROUND_ODD = "#20211d"
    DIVIDER = "#3a3b33"

    @staticmethod
    def _draw_text(painter, rect, value: str, font: QFont,
                   color: QColor, alignment=Qt.AlignmentFlag.AlignVCenter) -> None:
        painter.setFont(font)
        painter.setPen(color)
        metrics = QFontMetrics(font)
        text = metrics.elidedText(value or "", Qt.TextElideMode.ElideRight,
                                  max(1, rect.width() - 10))
        painter.drawText(rect.adjusted(5, 0, -5, 0), alignment, text)

    def paint(self, painter, option, index) -> None:
        row: LibraryRow | None = index.data(Qt.ItemDataRole.UserRole)
        if row is None:
            return super().paint(painter, option, index)

        painter.save()
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        background = QColor(self.BACKGROUND_SELECTED) if selected else (
            QColor(self.BACKGROUND_ODD) if index.row() % 2
            else QColor(self.BACKGROUND_EVEN))
        painter.fillRect(rect, background)
        if selected:
            painter.fillRect(rect.left(), rect.top(), 3, rect.height(),
                             QColor("#39e07a"))
        painter.setPen(QPen(QColor(self.DIVIDER), 1))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        x = rect.left() + 9
        thumb_rect = rect.adjusted(9, 6, 0, -6)
        thumb_rect.setWidth(64)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon is not None and not icon.isNull():
            pixmap = icon.pixmap(QSize(96, 54))
            painter.drawPixmap(thumb_rect, pixmap, pixmap.rect())
        else:
            painter.fillRect(thumb_rect, QColor("#151613"))
            painter.setPen(QPen(QColor("#34352e"), 1))
            painter.drawRect(thumb_rect)
        x += 76

        title_font = QFont("Rajdhani", 11, QFont.Weight.Bold)
        cell_font = QFont("Rajdhani", 9, QFont.Weight.DemiBold)
        title_color = QColor("#39e07a") if selected else QColor("#edf2ee")
        text_color = QColor("#c0c9c2")
        dim_color = QColor("#9ba69e")

        details = row.details or {}
        values = [
            (row.clip_title or f"Play {row.clip_number:03d}",
             self.PLAY_WIDTH, title_font, title_color),
            (row.project_name, self.GAME_WIDTH, cell_font, text_color),
            (format_ms(row.start_ms), self.TIME_WIDTH, cell_font, dim_color),
            (f"{row.duration_ms / 1000:.1f}s", self.DURATION_WIDTH,
             cell_font, dim_color),
            (row.play_type or details.get("run_pass", ""), self.TYPE_WIDTH,
             cell_font, text_color),
            (details.get("off_formation", ""), self.FORMATION_WIDTH,
             cell_font, text_color),
            (row.result, self.RESULT_WIDTH, cell_font, text_color),
            (row.player_name, self.PLAYER_WIDTH, cell_font, text_color),
        ]
        for value, width, font, color in values:
            cell = rect.adjusted(x - rect.left(), 0, 0, 0)
            cell.setWidth(width)
            self._draw_text(painter, cell, value, font, color)
            x += width

        action = rect.adjusted(x - rect.left(), 0, -8, 0)
        self._draw_text(painter, action, "OPEN", cell_font,
                        QColor("#39e07a"),
                        Qt.AlignmentFlag.AlignCenter)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(0, 50)


class LibrarySearchScreenV2(LibrarySearchScreen):
    """Selected-play workbench over a full-width clip ledger."""

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(settings, parent)
        self.setObjectName("V2LibraryScreen")
        self.notes_resize_handle = NotesResizeHandle(self.preview_notes, self)
        self.notes_resize_handle.height_committed.connect(
            self._save_notes_height)
        self._rebuild_selected_layout()
        self._decorate_filters()
        self._decorate_results()
        self._decorate_inspector()
        self._refresh_dynamic_styles()

    def _build_masthead(self, header: QHBoxLayout) -> None:
        """Build the standard left-aligned Library masthead."""
        buttons = {b.text(): b for b in self.findChildren(QPushButton)}
        back = buttons.get("‹ Back")
        rebuild = buttons.get("Rebuild Index")

        while header.count():
            item = header.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            reparent_widget(widget, self)
            widget.hide()

        if back is not None:
            back.hide()

        self.library_brand_lockup = _brand_image_label(
            "tapesift-logo.png",
            30,
            object_name="LibraryBrandLockup",
        )
        if self.library_brand_lockup is not None:
            header.addWidget(
                self.library_brand_lockup, 0, Qt.AlignmentFlag.AlignVCenter)
        self.library_brand_divider = QFrame()
        self.library_brand_divider.setObjectName("LibraryBrandDivider")
        self.library_brand_divider.setFrameShape(QFrame.Shape.VLine)
        self.library_brand_divider.setFixedSize(1, 26)
        header.addWidget(self.library_brand_divider)
        self.library_section_label = QLabel("LIBRARY")
        self.library_section_label.setObjectName("LibrarySectionLabel")
        self.library_section_label.setAccessibleName("Library")
        self.library_section_label.setProperty("role", "librarySection")
        self.library_section_label.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        header.addWidget(
            self.library_section_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self.masthead_stats = QLabel("")
        self.masthead_stats.setObjectName("LibraryMastheadStats")
        self.masthead_stats.setProperty("role", "libraryStats")
        self.masthead_stats.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        header.addWidget(
            self.masthead_stats, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        if rebuild is not None:
            self.rebuild_index_button = rebuild
            rebuild.show()
            header.addWidget(rebuild, 0, Qt.AlignmentFlag.AlignVCenter)
        else:
            self.rebuild_index_button = None

    def _rebuild_selected_layout(self) -> None:
        outer = self.layout()
        outer.setContentsMargins(22, 10, 22, 12)
        outer.setSpacing(7)
        self._outer_layout = outer

        # Keep the production header, filters, footer, and their Qt ownership
        # intact. Only the central split surface is replaced.
        header = outer.itemAt(0).layout()
        old_blurb = outer.itemAt(1).widget()
        filter_row = outer.itemAt(4).layout()
        old_splitter = outer.itemAt(6).widget()
        footer = outer.itemAt(7).layout()
        old_blurb.hide()

        buttons = self.findChildren(QPushButton)
        source_btn = next(button for button in buttons
                          if button.text() == "Show Source")
        self._rebuild_inspector_layout(source_btn)

        self._build_masthead(header)
        self.stats_label.setProperty("role", "libraryStats")
        self.stats_label.hide()

        # Search receives its own command line. The existing refinements stay
        # together on the next row.
        filter_row.removeWidget(self.search_box)
        outer.insertWidget(4, self.search_box)

        # Selected-play workbench and ledger replace the old side-by-side
        # splitter as one vertically ordered surface.
        main_surface = QWidget()
        main_box = QVBoxLayout(main_surface)
        main_box.setContentsMargins(0, 0, 0, 0)
        main_box.setSpacing(7)

        workbench = QFrame()
        workbench.setObjectName("V2LibraryWorkbench")
        workbench.setProperty("libraryWorkbench", "true")
        workbench.setMaximumHeight(390)
        workbench_row = QHBoxLayout(workbench)
        workbench_row.setContentsMargins(6, 6, 6, 6)
        workbench_row.setSpacing(10)

        preview_column = QFrame()
        preview_column.setObjectName("V2LibraryFilmPanel")
        preview_box = QVBoxLayout(preview_column)
        preview_box.setContentsMargins(0, 0, 0, 0)
        preview_box.setSpacing(5)
        reparent_widget(self.preview_thumb, preview_column)
        preview_box.addWidget(self.preview_thumb, 1)
        transport = QHBoxLayout()
        transport.setSpacing(4)
        left_spacer = QWidget(preview_column)
        right_actions = QWidget(preview_column)
        right_actions_layout = QHBoxLayout(right_actions)
        right_actions_layout.setContentsMargins(0, 0, 0, 0)
        right_actions_layout.addStretch(1)
        reparent_widget(source_btn, right_actions)
        right_actions_layout.addWidget(source_btn)
        for button in (
                self.preview_rewind_btn, self.preview_fast_forward_btn):
            reparent_widget(button, preview_column)
        # Move the dial, not the Play button: the dial's layout owns Play and
        # paints its seat. Reparenting Play directly pulls it out of the dial,
        # which then has nothing to centre on and no layout to place it.
        reparent_widget(self.preview_jog_ring, preview_column)
        mid = Qt.AlignmentFlag.AlignVCenter
        transport.addWidget(left_spacer, 1)
        transport.addWidget(self.preview_rewind_btn, 0, mid)
        transport.addSpacing(3)
        transport.addWidget(self.preview_jog_ring, 0, mid)
        transport.addSpacing(3)
        transport.addWidget(self.preview_fast_forward_btn, 0, mid)
        transport.addWidget(right_actions, 1)
        preview_box.addLayout(transport)
        workbench_row.addWidget(preview_column, 46)

        reparent_widget(self._preview_panel, workbench)
        workbench_row.addWidget(self._preview_panel, 54)
        main_box.addWidget(workbench, 9)

        ledger_title = QHBoxLayout()
        title = QLabel("CLIP LEDGER")
        title.setProperty("role", "librarySection")
        ledger_title.addWidget(title)
        ledger_title.addStretch(1)
        hint = QLabel("DOUBLE-CLICK A ROW TO OPEN")
        hint.setProperty("role", "libraryHint")
        ledger_title.addWidget(hint)
        main_box.addLayout(ledger_title)
        main_box.addWidget(self._build_ledger_header())

        reparent_widget(self.results_list, main_surface)
        reparent_widget(self.empty_label, main_surface)
        main_box.addWidget(self.results_list, 10)
        main_box.addWidget(self.empty_label, 10)

        outer.replaceWidget(old_splitter, main_surface)
        reparent_widget(old_splitter, None)
        old_splitter.deleteLater()

        total_label = QLabel("SELECTED CLIPS STAY IN LEDGER ORDER")
        total_label.setProperty("role", "libraryHint")
        footer.insertWidget(1, total_label)

    def move_masthead_to(self, shell) -> None:
        """Move the live Library identity/actions into the shared app shell."""
        if self.library_brand_lockup is not None:
            shell.add_mode_left_widget(
                "library", self.library_brand_lockup)
        shell.add_mode_left_widget("library", self.library_brand_divider)
        shell.add_mode_left_widget("library", self.library_section_label)
        shell.add_mode_left_widget("library", self.masthead_stats)
        shell.add_mode_left_stretch("library")
        if self.rebuild_index_button is not None:
            shell.add_mode_right_widget(
                "library", self.rebuild_index_button)
        self._outer_layout.invalidate()

    def _build_ledger_header(self) -> QFrame:
        header = QFrame()
        header.setObjectName("V2LibraryLedgerHeader")
        header.setProperty("ledgerHeader", "true")
        row = QHBoxLayout(header)
        row.setContentsMargins(86, 0, 8, 0)
        row.setSpacing(0)
        columns = [
            ("PLAY", ResultRowDelegateV2.PLAY_WIDTH),
            ("GAME", ResultRowDelegateV2.GAME_WIDTH),
            ("SOURCE TIME", ResultRowDelegateV2.TIME_WIDTH),
            ("DURATION", ResultRowDelegateV2.DURATION_WIDTH),
            ("TYPE", ResultRowDelegateV2.TYPE_WIDTH),
            ("FORMATION", ResultRowDelegateV2.FORMATION_WIDTH),
            ("RESULT", ResultRowDelegateV2.RESULT_WIDTH),
            ("PLAYER", ResultRowDelegateV2.PLAYER_WIDTH),
        ]
        for text, width in columns:
            label = QLabel(text)
            label.setProperty("role", "ledgerColumn")
            label.setFixedWidth(width)
            row.addWidget(label)
        action = QLabel("ACTIONS")
        action.setProperty("role", "ledgerColumn")
        action.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(action, 1)
        return header

    def _rebuild_inspector_layout(self, source_btn: QPushButton) -> None:
        panel_box = self._preview_panel.layout()
        for label in self._preview_panel.findChildren(
                QLabel, "", Qt.FindChildOption.FindDirectChildrenOnly):
            if label.text() == "Selected Clip":
                label.hide()
        while panel_box.count():
            panel_box.takeAt(0)
        panel_box.setContentsMargins(14, 8, 10, 8)
        panel_box.setSpacing(6)

        heading = QLabel("SELECTED PLAY")
        heading.setProperty("role", "libraryInspectorHeading")
        panel_box.addWidget(heading)
        panel_box.addWidget(self.preview_title)
        panel_box.addWidget(self.preview_meta)
        panel_box.addWidget(self.preview_tags)

        details_heading = QLabel("PLAY DETAILS")
        details_heading.setProperty("role", "librarySubheading")
        panel_box.addWidget(details_heading)

        details_grid = QGridLayout()
        details_grid.setContentsMargins(0, 0, 0, 0)
        details_grid.setHorizontalSpacing(8)
        details_grid.setVerticalSpacing(4)
        detail_fields = [
            ("run_pass", "Play Type"),
            ("result", "Result"),
            ("off_formation", "Formation"),
            ("action", "Action"),
            ("down_distance", "Down & Distance"),
            ("off_personnel", "Personnel"),
            ("ball_on", "Ball On"),
            ("def_formation", "Def. Front"),
        ]
        self._v2_detail_edits: dict[str, QLineEdit] = {}
        for index, (key, label_text) in enumerate(detail_fields):
            edit = QLineEdit()
            self._v2_detail_edits[key] = edit
            label = QLabel(label_text)
            label.setProperty("role", "libraryFieldLabel")
            row, pair = divmod(index, 2)
            col = pair * 2
            details_grid.addWidget(label, row, col)
            details_grid.addWidget(edit, row, col + 1)
        panel_box.addLayout(details_grid)

        notes_label = QLabel("NOTES")
        notes_label.setProperty("role", "librarySubheading")
        panel_box.addWidget(notes_label)
        panel_box.addWidget(self.preview_notes)
        panel_box.addWidget(
            self.notes_resize_handle, 0, Qt.AlignmentFlag.AlignRight)

        actions = QHBoxLayout()
        actions.addWidget(self.save_btn, 1)
        detach_widget(self.open_btn)
        actions.addWidget(self.open_btn, 1)
        panel_box.addLayout(actions)
        source_btn.setProperty("quiet", "true")

    def _decorate_filters(self) -> None:
        self.search_box.setObjectName("V2LibrarySearch")
        self.search_box.setMinimumHeight(34)
        for combo in self.findChildren(QComboBox):
            combo.setProperty("libraryFilter", "true")
            combo.setMinimumHeight(31)
        self.tags_button.setProperty("libraryFilter", "true")
        self.tags_button.setMinimumHeight(31)

    def _decorate_results(self) -> None:
        self.results_list.setObjectName("V2LibraryResults")
        self.results_list.setSpacing(0)
        palette = self.results_list.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor("#0a0e0b"))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#0e130f"))
        palette.setColor(QPalette.ColorRole.Highlight, QColor("#164d2c"))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#f5fff8"))
        self.results_list.setPalette(palette)
        self.results_list.setItemDelegate(ResultRowDelegateV2(self.results_list))
        self.count_label.setProperty("role", "libraryCount")
        self.reel_btn.setProperty("projectAction", "true")

    def _decorate_inspector(self) -> None:
        self._preview_panel.setObjectName("V2LibraryInspector")
        self.preview_thumb.setObjectName("V2LibraryPreview")
        self.preview_thumb.setStyleSheet("")
        self.preview_thumb.setMinimumHeight(190)
        self.preview_thumb.setMaximumHeight(245)
        self.preview_thumb.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.preview_placeholder.setScaledContents(False)
        self.preview_placeholder.setMinimumHeight(190)
        self.preview_placeholder.setMaximumHeight(245)
        self.preview_video.setMinimumHeight(190)
        self.preview_video.setMaximumHeight(245)
        self.preview_title.setObjectName("V2LibraryClipTitle")
        self.preview_title.setStyleSheet("")
        self.preview_meta.setProperty("role", "libraryMeta")
        self.notes_resize_handle.set_editor_height(
            getattr(self.settings, "library_notes_height", 72))
        self.details_section.hide()
        self.collapse_btn.hide()

        self.export_btn.setProperty("primary", "true")
        self.save_btn.setProperty("primary", "true")
        self.open_btn.setProperty("projectAction", "true")
        self.preview_play_btn.setProperty("projectAction", "true")
        for button in (
                self.preview_rewind_btn, self.preview_fast_forward_btn):
            button.setProperty("mediaControl", "true")
            button.setFixedSize(40, 34)
            button.show()
        # Play is sized by the dial that hosts it; forcing 40x34 here would
        # blow out the seat the dial paints around it.
        self.preview_play_btn.setProperty("mediaControl", "true")
        self.preview_play_btn.show()
        self.preview_jog_ring.show()
        for button in self.findChildren(QPushButton):
            if button.text() == "Clear selection":
                button.setProperty("quiet", "true")

    def _save_notes_height(self, height: int) -> None:
        height = self.notes_resize_handle.set_editor_height(height)
        if self.settings.library_notes_height == height:
            return
        self.settings.library_notes_height = height
        self.settings.save()

    def _refresh_dynamic_styles(self) -> None:
        for widget in [self, *self.findChildren(QWidget)]:
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def refresh(self) -> None:
        super().refresh()
        value = self.stats_label.text()
        if "clips" in value.lower():
            value = value.upper().replace("·", "/")
            self.stats_label.setText(value.replace("1 PROJECTS", "1 PROJECT"))
        self.masthead_stats.setText(self.stats_label.text())

    def _show_preview(self, row: LibraryRow | None, count: int = 0) -> None:
        super()._show_preview(row, count)
        for key, edit in getattr(self, "_v2_detail_edits", {}).items():
            edit.setEnabled(row is not None)
            edit.setText(self.preview_details[key].text() if row else "")
        if row and row.thumbnail_path and Path(row.thumbnail_path).is_file():
            pixmap = QPixmap(row.thumbnail_path)
            if not pixmap.isNull():
                self.preview_thumb.setPixmap(pixmap.scaled(
                    self.preview_thumb.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))

    def _save_edit(self) -> None:
        for key, edit in self._v2_detail_edits.items():
            self.preview_details[key].setText(edit.text())
        super()._save_edit()
