"""Native framed TapeSift Shell V3 window."""

from __future__ import annotations

import logging
from dataclasses import replace
from copy import deepcopy
import sqlite3
from pathlib import Path
from types import MethodType

import shiboken6

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QPoint, QRect, QSize, QTimer, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QKeySequence, QShortcut, QTransform
from PySide6.QtWidgets import (
    QApplication, QDockWidget, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu,
    QMessageBox, QPushButton, QToolButton, QSizePolicy, QStyle, QVBoxLayout, QWidget,
)

from tapesift.ui_core.layout_ownership import reparent_widget
from tapesift.core.exceptions import TapeSiftError
from tapesift.models.export_package import ExportStyle
from tapesift.services import export_service, recovery_service, result_service, detail_service, drive_suggestions
from tapesift.services.autodetect_capture_service import DETECTOR_VERSION
from tapesift.services.project_service import ProjectSession
from tapesift.ui_v2.main_window import MainWindowV2
from tapesift.ui_core.clip_list import (
    COL_NUM, COL_START, COL_STATUS, COL_TITLE, REVIEW_KIND_ROLE,
)
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_v3.application_bar import NativeApplicationBar
from tapesift.ui_v3.clip_details import ClipDetailsV3, GameTeamsDialog
from tapesift.ui_v3.source_photo import painted_snapshot
from tapesift.services.source_photo import make_photo, source_identity
from tapesift.ui_v3.dialog_surface import (
    RecoveryAction,
    RecoveryDialogV3,
    SettingsDialogV3, ProjectSettingsDialogV3, TagColorManagerDialogV3, ResultManagerDialogV3, HowItWorksDialogV3,
)
from tapesift.ui_v3.fonts import MONO_FAMILY, SANS_FAMILY, load_v3_fonts
from tapesift.ui_v3.icons import icon, tinted_icon
from tapesift.ui_v3.library_screen import LazyLibrarySearchScreenV3
from tapesift.ui_v3.ledger_presentation import LedgerDelegateV3
from tapesift.ui_v3.review_workspace import ReviewWorkspaceV3
from tapesift.ui_v3.start_screen import StartScreenV3
from tapesift.ui_v3.theme import V3_REVIEW_CONTROL_CENTER_MATERIAL
from tapesift.ui_v3.transport_surface import V3TransportSurface
from tapesift.ui_v3.jog_console import V3JogConsole
from tapesift.ui_v3.workspace_state import ReviewRailState, WorkspaceStateV3


log = logging.getLogger(__name__)


class _WindowBezelV3(QFrame):
    """Two quiet keylines above the client without consuming layout space."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("V3WindowBezel")
        self.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.band = QFrame(self)
        self.band.setObjectName("V3WindowBezelBand")
        self.band.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.band.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.inner = QFrame(self)
        self.inner.setObjectName("V3WindowBezelInner")
        self.inner.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.inner.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self.band.setGeometry(self.rect().adjusted(1, 1, -1, -1))
        self.inner.setGeometry(self.rect().adjusted(5, 5, -5, -5))
        super().resizeEvent(event)


class MainWindowV3(MainWindowV2):
    project_settings_dialog_class = ProjectSettingsDialogV3

    """Parallel shell that rehosts the existing TapeSift authorities."""

    def __init__(
            self, *args, workspace_state_path=None, **kwargs) -> None:
        self._v3_font_families = load_v3_fonts()
        self._v3_state_store = WorkspaceStateV3(workspace_state_path)
        self._v3_initial_state = self._v3_state_store.load()
        self._v3_review: ReviewWorkspaceV3 | None = None
        self._retired_v2_docks: list[QDockWidget] = []
        self._v3_heatmap = None
        self._v3_heatmap_tag_locks = []
        self._v3_read_only_recovery = False
        self._v3_read_only_locks: list[tuple[object, bool]] = []
        super().__init__(*args, **kwargs)
        self.setObjectName("TapeSiftV3")
        self.clip_editor.result_dialog_class = ResultManagerDialogV3
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setContentsMargins(0, 0, 0, 0)
        self._v3_window_bezel = _WindowBezelV3(self)
        self._sync_v3_page_chrome()
        self._sync_v3_window_bezel()
        self._rewire_v3_clip_export()
        self._rewire_v3_export_routes()
        self._decorate_v3_review_controls()
        self._v3_review_export_button = QPushButton("Export")
        self._v3_review_export_button.setObjectName("V3ReviewExportButton")
        self._v3_review_export_button.setIcon(tinted_icon("share-20.svg", "#a9c3b1", 18))
        self._v3_review_export_button.setToolTip("Export player cutups, this clip, or selected clips")
        export_menu = QMenu(self._v3_review_export_button)
        cutups = export_menu.addAction("Player / group cutups…", self._open_cutup_builder)
        current = export_menu.addAction("Current clip…", lambda: self._open_play_export(self.clip_editor._clip.id))
        selected = export_menu.addAction("Selected clips…", self._configure_selected_package_export)
        def sync_export_menu():
            ready = bool(self.session and not self.session.read_only and self._v3_export_can_start())
            cutups.setEnabled(ready and any(c.enabled for c in self.session.clips))
            current.setEnabled(ready and self.clip_editor._clip is not None and self.clip_editor._clip.enabled)
            selected.setEnabled(ready and bool(self.clip_list.selected_clip_ids()))
        export_menu.aboutToShow.connect(sync_export_menu)
        self._v3_review_export_button.setMenu(export_menu)
        self._centered_menu_shell.add_review_right_widget(self._v3_review_export_button)
        self._v3_review_library_button = QPushButton("Library")
        self._v3_review_library_button.setObjectName("V3ReviewLibraryButton")
        self._v3_review_library_button.setIcon(
            tinted_icon("book-open-24.svg", "#a9c3b1", 18))
        self._v3_review_library_button.setIconSize(QSize(18, 18))
        self._v3_review_library_button.setToolTip("Open Library (Ctrl+Shift+L)")
        self._v3_review_library_button.clicked.connect(self.show_library)
        self._centered_menu_shell.add_review_right_widget(
            self._v3_review_library_button)
        self._rewire_details_shortcut()
        self._tidy_v3_menus()
        self._sync_v3_rail_actions()
        self._bind_source_photos()

    def _build_shortcuts(self) -> None:
        super()._build_shortcuts()
        for key, slot, typing in (
            ("W", lambda: self._keyboard_clip_step(1), False),
            ("B", lambda: self._keyboard_clip_step(-1), False),
            ("E", self._keyboard_edit_clip, False),
            ("Shift+E", self._keyboard_edit_map, False),
            ("Shift+Up", self.player.timeline_zoom_in.click, False),
            ("Shift+Down", self.player.timeline_zoom_out.click, False),
            ("Shift+F", self.player.timeline_fit_play.click, False),
            ("Ctrl+Shift+Return", self.clip_editor.save_next_btn.click, True),
            ("Ctrl+Shift+Enter", self.clip_editor.save_next_btn.click, True),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            shortcut.setAutoRepeat(key in {"Shift+Up", "Shift+Down"})
            shortcut.activated.connect(self._transport_shortcut(slot, allow_while_typing=typing))
            self._transport_shortcuts[key] = shortcut
        self._sync_transport_shortcut_page(self.stack.currentIndex())
        self.player.timeline_zoom_in.setToolTip(
            "Zoom timeline in around the playhead (Shift+Up). Hold to repeat.")
        self.player.timeline_zoom_out.setToolTip(
            "Zoom timeline out around the playhead (Shift+Down). Hold to repeat.")
        self.player.timeline_fit_play.setToolTip(
            "Fit the selected play with surrounding context (Shift+F). Select a clip first.")

    def _toggle_shortcuts_overlay(self) -> None:
        # Keep ? and F1 on one searchable guide instead of two drifting lists.
        self._show_shortcuts()

    def _sync_map_shuttle_shortcuts(self) -> None:
        grid = self.player.attribute_grid
        map_owns_keys = grid.isVisible() and (grid.hasFocus() or grid.property("mapEditorOpen"))
        enabled = self.stack.currentWidget() is self.workspace and not map_owns_keys
        # Global shuttle shortcuts must not compete with the map's local keys.
        for key in "JKL":
            self._transport_shortcuts[key].setEnabled(bool(enabled))

    def _keyboard_edit_map(self) -> None:
        if self._workspace_stage != "review" or self.clip_editor._clip is None:
            return
        grid = self.player.attribute_grid
        if not grid.rows():
            self._v3_review.footer.local_label.setText("Show rows in Tools > Tag Map options")
            return
        self.player.shuttle_stop()
        self.player.set_tag_map_collapsed(False, persist=False)
        self._focus_map_row(getattr(self, "_v3_map_row_key", grid.rows()[0].key))

    def _keyboard_clip_step(self, delta: int) -> None:
        if self._workspace_stage != "review":
            return
        grid = self.player.attribute_grid
        cell = grid.cursor_cell() if grid.hasFocus() else None
        row_key = cell[0].key if cell else None
        editor = self.clip_editor
        if editor.save_state_label.property("state") == "dirty":
            if self.session.read_only or not editor._apply():
                if row_key:
                    self._focus_map_row(row_key)
                return
            self._v3_review.footer.local_label.setText(f"Play {editor._clip.clip_number:03d} saved")
        self._goto_clip(delta)
        if row_key:
            self._focus_map_row(row_key)
        else:
            self._return_focus_to_playback()

    def _focus_map_row(self, key: str) -> None:
        grid = self.player.attribute_grid
        if grid.isHidden() or not grid.rows():
            self._return_focus_to_playback()
            return
        row = next((i for i, r in enumerate(grid.rows()) if r.key == key), 0)
        clips = grid.visible_clips()
        index = next((i for i, c in enumerate(clips) if c.id == self._selected_clip_id), None)
        if index is None:
            clip = self.session.get_clip(self._selected_clip_id) if self.session else None
            if clip is None:
                return
            # Moving beyond the viewport must reveal the new clip before the
            # cursor can be restored; its column index is not stable across pans.
            start, end = self.player.slider.visible_range()
            self.player.slider.set_visible_start(clip.start_ms - (end - start) // 2)
            clips = grid.visible_clips()
            index = next((i for i, c in enumerate(clips) if c.id == clip.id), None)
        if index is not None:
            self._v3_map_row_key = grid.rows()[row].key
            if self.player._shuttle_active():
                self.player.shuttle_stop()
            grid.setFocus(Qt.FocusReason.ShortcutFocusReason)
            grid.set_cursor_cell(row, index)
            self._sync_map_keyboard_hint()

    def _apply_map_cell(self, clip_id: str, values: dict[str, str], notes=None) -> bool:
        if not self.session or self.session.read_only or self._selected_clip_id != clip_id:
            return False
        editor = self.clip_editor
        previous = {key: editor.detail_edits[key].text() for key in values}
        previous_notes = editor.notes_edit.toPlainText()
        # Saving derives tags and names before the database commit. A rejected
        # popup draft must not leak those derived values into a later save.
        derived = {edit: edit.text() for edit in (editor.tags_edit, editor.title_edit, editor.filename_edit)}
        automatic_name = editor._automatic_name
        extra_details = deepcopy(editor._draft_extra_details)
        for key, value in values.items():
            editor.detail_edits[key].setText(value)
        if notes is not None:
            editor.notes_edit.setPlainText(notes)
        if editor._apply():
            self._v3_review.footer.local_label.setText(f"Play {editor._clip.clip_number:03d} saved")
            return True
        for key, value in previous.items():
            editor.detail_edits[key].setText(value)
        editor.notes_edit.setPlainText(previous_notes)
        for edit, text in derived.items():
            edit.setText(text)
        editor._automatic_name = automatic_name
        editor._draft_extra_details = extra_details
        editor._sync_quick_name_label()
        return False

    def _keyboard_edit_clip(self) -> None:
        if self._workspace_stage != "review" or self.clip_editor._clip is None:
            return
        self._set_details_open(True)
        self.clip_editor.quick_play_buttons["run"].setFocus(Qt.FocusReason.ShortcutFocusReason)

    def _rewire_v3_clip_export(self) -> None:
        """Make the visible Export Clip action open its honest setup page.

        Ctrl+E remains the explicit Quick Export shortcut.  The labelled
        button follows the standard V3 flow instead: one selected clip,
        Clean style, visible destination/preset controls, progress, and a
        Back to Review route.  It must never be gated by the Signature or
        Vertical exact-take preview.
        """

        try:
            self.clip_editor.quick_export_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.clip_editor.quick_export_requested.connect(
            self._open_play_export)
        self.clip_editor.quick_export_btn.setToolTip(
            "Configure and export this clip")

    def _open_play_export(self, clip_id: str) -> None:
        if self.session is None or self.session.read_only:
            return
        editor = self.clip_editor
        if editor.save_state_label.property("state") == "dirty":
            answer = QMessageBox.question(
                self, "Save before export", "Save these clip details and open the export preview?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if answer != QMessageBox.StandardButton.Save or not editor._apply():
                return
        clip = self.session.get_clip(clip_id)
        if clip is None or not clip.enabled:
            self.statusBar().showMessage("Enable this clip before exporting it", 4000)
            return
        from tapesift.ui_v3.play_field import PlayFieldWidget
        from tapesift.ui_v3.play_export_dialog import PlayExportDialog
        field = PlayFieldWidget()
        field.set_details(clip.details, self.session.project.game_team_ids)
        dialog = PlayExportDialog(clip, self.session.project, field.to_image(1200, 300),
                                  self.settings.ffmpeg_path, self)
        dialog.video_only_requested.connect(lambda: self._configure_single_clip_export(clip_id))
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
            field.deleteLater()

    def _rewire_v3_export_routes(self) -> None:
        """Bind the two locked routes without adding a second export engine."""

        review = self._v3_review
        if review is None:
            return
        review.export_page.clip_start_requested.connect(
            self._start_v3_clip_export)
        review.export_page.number_clips_requested.connect(self._number_v3_clips)
        review.export_page.cutups_requested.connect(self._open_cutup_builder)
        self.clip_editor.number_clips_requested.connect(self._number_v3_clips)
        review.export_page.package_start_requested.connect(
            self._start_v3_package_export)
        review.export_page.cancel_requested.connect(self._cancel_all)
        review.export_page.destination_requested.connect(self.export_panel.output_folder_change_requested.emit)
        review.export_page.retry_requested.connect(self.export_panel.retry_requested.emit)
        review.export_page.queued_cancel_requested.connect(self.export_panel.remove_queued_requested.emit)
        for combo in (review.export_page.clip_setup.preset_combo,
                      review.export_page.package_setup.mode_combo,
                      review.export_page.package_setup.preset_combo):
            combo.currentIndexChanged.connect(self._refresh_v3_export_preview)
        review.export_page.open_path_requested.connect(
            self.export_panel._open_path)
        review.export_page.package_remove_requested.connect(
            self._remove_v3_package_clip)
        review.export_page.package_order_changed.connect(
            self._reorder_v3_package_clips)
        try:
            self.clip_editor.package_requested.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.clip_editor.package_requested.connect(
            self._configure_package_export)
        button = getattr(self, "review_export_button", None)
        if button is not None:
            try:
                button.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
            button.clicked.connect(self._configure_selected_package_export)

    def _decorate_v3_review_controls(self) -> None:
        """Apply the standard Fluent glyphs to the existing live controls."""

        def bind_family(widget, family: str) -> None:
            font = widget.font()
            font.setFamily(family)
            widget.setFont(font)
            # Keep the bound font while the V3 stylesheet paints the surface.
            widget.setStyleSheet("")

        for button, glyph in (
            (self.clip_editor.quick_export_btn, "share-20.svg"),
            (self.clip_editor.package_btn, "box-20.svg"),
            (self.clip_editor.apply_btn, "save-20.svg"),
            (self.clip_editor.save_next_btn, "save-next-20.svg"),
        ):
            button.setIcon(tinted_icon(glyph))
            button.setIconSize(QSize(16, 16))
        self.clip_editor.advanced_details_section.body.setObjectName(
            "InspectorAdvancedDetailsBody")
        self.clip_editor.details_section.body.setObjectName(
            "InspectorMoreDetailsBody")
        self.clip_editor.heading_label.setText("Clip Details")
        self.clip_editor.heading_label.hide()
        self.clip_editor.save_state_label.hide()
        self.clip_editor.save_state_label.setMaximumWidth(0)
        self.clip_editor.edit_title_btn.hide()
        self.clip_editor.edit_title_btn.setMaximumWidth(0)
        self.clip_editor.review_state_label.setFixedHeight(24)
        self.clip_editor.review_state_label.setAlignment(
            Qt.AlignmentFlag.AlignCenter)
        heading_row = self.clip_editor.heading_label.parentWidget().layout()
        review_index = heading_row.indexOf(
            self.clip_editor.review_state_label)
        heading_row.removeWidget(self.clip_editor.review_state_label)
        self._v3_review_status_badge = QFrame(
            self.clip_editor.heading_label.parentWidget())
        self._v3_review_status_badge.setObjectName("V3ReviewStatusBadge")
        self._v3_review_status_badge.setFixedSize(70, 26)
        review_badge_layout = QHBoxLayout(self._v3_review_status_badge)
        review_badge_layout.setContentsMargins(6, 0, 7, 0)
        review_badge_layout.setSpacing(3)
        self._v3_review_status_icon = QLabel(
            self._v3_review_status_badge)
        self._v3_review_status_icon.setObjectName("V3ReviewStatusIcon")
        self._v3_review_status_icon.setFixedSize(12, 12)
        self._v3_review_status_icon.setPixmap(
            self.style().standardIcon(
                QStyle.StandardPixmap.SP_DialogApplyButton
            ).pixmap(QSize(12, 12)))
        review_badge_layout.addWidget(self._v3_review_status_icon)
        review_badge_layout.addWidget(
            self.clip_editor.review_state_label, 1)
        heading_row.insertWidget(
            max(0, review_index), self._v3_review_status_badge)
        self._v3_details_heading = QWidget(
            self.clip_editor.heading_label.parentWidget())
        self._v3_details_heading.setObjectName("V3DetailsHeading")
        details_heading_layout = QVBoxLayout(self._v3_details_heading)
        details_heading_layout.setContentsMargins(0, 0, 0, 0)
        details_heading_layout.setSpacing(1)
        self.clip_editor.play_rail.setFixedHeight(0)
        details_title = QLabel("CLIP DETAILS", self._v3_details_heading)
        details_title.setObjectName("V3DetailsTitle")
        self._v3_details_subtitle = QLabel(
            "No play selected", self._v3_details_heading)
        self._v3_details_subtitle.setObjectName("V3DetailsSubtitle")
        bind_family(details_title, SANS_FAMILY)
        bind_family(self._v3_details_subtitle, SANS_FAMILY)
        details_heading_layout.addWidget(details_title)
        details_heading_layout.addWidget(self._v3_details_subtitle)
        heading_row.insertWidget(0, self._v3_details_heading, 1)

        summary_body = self.clip_editor.summary_card.layout().itemAt(0).widget()
        self._v3_play_call_chip = QLabel("", summary_body)
        self._v3_play_call_chip.setObjectName("V3PlayCallChip")
        self._v3_play_call_chip.hide()
        summary_body.layout().addWidget(
            self._v3_play_call_chip, 0, Qt.AlignmentFlag.AlignTop)

        # Recompose the same live start/end fields into the locked two-tier
        # Range module. No timing authority is duplicated.
        self._v3_range_card = QFrame(self.clip_editor.primary_container)
        self._v3_range_card.setObjectName("V3RangeCard")
        self.clip_editor.start_edit.setObjectName("V3RangeStart")
        self.clip_editor.end_edit.setObjectName("V3RangeEnd")
        range_grid = QGridLayout(self._v3_range_card)
        range_grid.setContentsMargins(10, 8, 10, 10)
        range_grid.setHorizontalSpacing(8)
        range_grid.setVerticalSpacing(5)
        range_title = QLabel("Range", self._v3_range_card)
        range_title.setObjectName("V3RangeTitle")
        bind_family(range_title, SANS_FAMILY)
        self._v3_edit_points_btn = QPushButton(
            "Edit points", self._v3_range_card)
        self._v3_edit_points_btn.setObjectName("V3EditPoints")
        self._v3_edit_points_btn.clicked.connect(
            self.clip_editor.start_edit.setFocus)
        range_grid.addWidget(range_title, 0, 0)
        range_grid.addWidget(
            self._v3_edit_points_btn, 0, 1, 1, 2,
            Qt.AlignmentFlag.AlignRight)
        for column, caption in enumerate(("Start", "End", "Length")):
            label = QLabel(caption, self._v3_range_card)
            label.setObjectName("V3RangeCaption")
            bind_family(label, SANS_FAMILY)
            range_grid.addWidget(label, 1, column)
        for column, field in enumerate((
                self.clip_editor.start_edit,
                self.clip_editor.end_edit,
                self.clip_editor.range_duration_label)):
            reparent_widget(field, self._v3_range_card)
            range_grid.addWidget(field, 2, column)
        for field in (
                self.clip_editor.start_edit,
                self.clip_editor.end_edit,
                self.clip_editor.range_duration_label,
                self.clip_editor.range_summary_label,
                self.clip_editor.review_state_label):
            bind_family(field, MONO_FAMILY)
        for key in ("player_name", "other_players"):
            bind_family(self.clip_editor.detail_labels[key], SANS_FAMILY)
        bind_family(self.clip_editor.players_box, SANS_FAMILY)
        bind_family(self.clip_editor.notes_box, SANS_FAMILY)
        range_grid.setColumnStretch(0, 1)
        range_grid.setColumnStretch(1, 1)
        self.clip_editor._primary_form.setRowVisible(
            self.clip_editor.time_row, False)
        self.clip_editor._primary_form.insertRow(0, self._v3_range_card)

        self._v3_action_header = QFrame(self.clip_editor.action_bar)
        self._v3_action_header.setObjectName("V3PlayActionsHeader")
        action_header = QHBoxLayout(self._v3_action_header)
        action_header.setContentsMargins(0, 2, 0, 4)
        action_header.addWidget(QLabel("Review state", self._v3_action_header))
        heading_row.removeWidget(self._v3_review_status_badge)
        action_header.addWidget(self._v3_review_status_badge)
        action_header.addStretch(1)
        self._v3_action_state = QLabel("Saved", self._v3_action_header)
        self._v3_action_state.setObjectName("V3PlayActionsState")
        self.clip_editor.save_state_changed.connect(self._v3_action_state.setText)
        action_header.addWidget(self._v3_action_state)
        self.clip_editor._action_layout.insertWidget(
            0, self._v3_action_header)
        self.clip_editor._action_layout.setContentsMargins(10, 10, 10, 8)
        self.clip_list.heading_label.setText("CLIP LEDGER")
        self.clip_list.count_label.hide()
        self.clip_list.filter_edit.setPlaceholderText("Search plays…")
        self.clip_list.filter_edit.addAction(
            tinted_icon("search-16.svg", "#89958c", 16),
            self.clip_list.filter_edit.ActionPosition.LeadingPosition,
        )
        for button in (
            self.clip_list.all_filter_btn,
            self.clip_list.unlogged_filter_btn,
            self.clip_list.needs_fix_filter_btn,
            self.clip_list.detect_pending_filter_btn,
        ):
            button.ensurePolished()
            button.setMinimumWidth(
                button.fontMetrics().horizontalAdvance(button.text()) + 12)
            button.setMaximumWidth(16777215)
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setFixedHeight(32)
        self.clip_list.filter_buttons.setFixedHeight(36)
        previous = getattr(self.clip_list, "previous_btn", None)
        next_button = getattr(self.clip_list, "next_btn", None)
        if previous is not None:
            previous.setText("Previous")
            previous.setIcon(tinted_icon("chevron-left-16.svg", size=16))
        if next_button is not None:
            next_button.setText("Next")
            next_button.setIcon(tinted_icon("chevron-right-16.svg", size=16))
        for button, glyph in (
            (getattr(self, "detect_plays_button", None),
             "scan-object-accent-20.svg"),
            (getattr(self, "review_export_button", None), "share-20.svg"),
            (getattr(self, "new_clip_button", None), "add-square-20.svg"),
        ):
            if button is not None:
                button.setIcon(tinted_icon(glyph, size=16))
                button.setIconSize(QSize(16, 16))
                if hasattr(button, "setToolButtonStyle"):
                    button.setToolButtonStyle(
                        Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._install_v3_ledger_modules()
        ledger_header = self.clip_list.findChild(QWidget, "ClipLedgerHeader")
        if ledger_header is not None:
            ledger_header.setFixedHeight(58)
            if ledger_header.layout() is not None:
                ledger_header.layout().setContentsMargins(14, 0, 40, 0)
        inspector_header = self.clip_editor.findChild(QWidget, "InspectorHeader")
        if inspector_header is not None:
            inspector_header.setFixedHeight(58)
            # The right rail's 28px collapse control owns the boundary gutter.
            # Reserve that gutter in the header so it never masks the title.
            inspector_header.layout().setContentsMargins(40, 0, 12, 0)

    def _install_v3_ledger_modules(self) -> None:
        layout = self.clip_list.layout()
        if layout is None or hasattr(self, "_v3_ledger_project_card"):
            return
        self._v3_ledger_project_card = QFrame(self.clip_list)
        self._v3_ledger_project_card.setObjectName("V3LedgerProjectCard")
        project_row = QHBoxLayout(self._v3_ledger_project_card)
        project_row.setContentsMargins(10, 0, 10, 0)
        project_row.setSpacing(8)
        icon_label = QLabel(self._v3_ledger_project_card)
        icon_label.setPixmap(
            tinted_icon("filmstrip-play-20.svg", "#39e07a", 16)
            .pixmap(QSize(16, 16)))
        project_row.addWidget(icon_label)
        self._v3_ledger_project_name = QLabel(
            "NO PROJECT OPEN", self._v3_ledger_project_card)
        self._v3_ledger_project_name.setObjectName("V3LedgerProjectName")
        project_row.addWidget(self._v3_ledger_project_name, 1)
        self._v3_ledger_project_state = QLabel(
            "", self._v3_ledger_project_card)
        self._v3_ledger_project_state.setObjectName("V3LedgerProjectState")
        project_row.addWidget(self._v3_ledger_project_state)
        arrow = QLabel(self._v3_ledger_project_card)
        arrow.setObjectName("V3LedgerProjectArrow")
        arrow.setPixmap(tinted_icon(
            "chevron-right-16.svg", "#819087", 14).pixmap(QSize(14, 14)))
        project_row.addWidget(arrow)
        layout.insertWidget(1, self._v3_ledger_project_card)
        layout.setSpacing(6)
        self._v3_ledger_project_card.setFixedHeight(40)
        table = self.clip_list.table
        header = table.horizontalHeader()
        header.show()
        header.setFixedHeight(30)
        table.setColumnHidden(COL_START, True)
        header.setSectionResizeMode(COL_NUM, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(COL_NUM, 32)
        header.setSectionResizeMode(COL_TITLE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(COL_STATUS, 76)
        for column, label in ((COL_NUM, "#"), (COL_TITLE, "Game / source"),
                              (COL_STATUS, "Length")):
            table.horizontalHeaderItem(column).setText(label)
        self._v3_ledger_delegate = LedgerDelegateV3(self.clip_list)
        table.setItemDelegate(self._v3_ledger_delegate)

        self._v3_ledger_command_card = QFrame(self.clip_list)
        self._v3_ledger_command_card.setObjectName("V3LedgerCommandCard")
        commands = QVBoxLayout(self._v3_ledger_command_card)
        commands.setContentsMargins(4, 7, 4, 7)
        commands.setSpacing(6)
        # Move the live action, navigation, search and filter items into one
        # raised module. Their signals and object identities are unchanged.
        for _ in range(4):
            item = layout.takeAt(2)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                commands.addWidget(widget)
            elif child_layout is not None:
                child_layout.setContentsMargins(0, 0, 0, 0)
                if child_layout.indexOf(self.clip_list.previous_btn) >= 0:
                    self._v3_review_queue_label = QLabel(
                        "Review queue", self._v3_ledger_command_card)
                    self._v3_review_queue_label.setObjectName(
                        "V3LedgerReviewQueue")
                    child_layout.insertWidget(
                        1, self._v3_review_queue_label, 1,
                        Qt.AlignmentFlag.AlignCenter)
                commands.addLayout(child_layout)
        # FlowLayout let the fourth filter wrap below the locked 36px slot.
        # Keep the original live buttons and signals, but give all four one
        # deliberate horizontal row.
        commands.removeWidget(self.clip_list.filter_buttons)
        self.clip_list.filter_buttons.hide()
        self._v3_ledger_filters = QWidget(self._v3_ledger_command_card)
        self._v3_ledger_filters.setObjectName("V3LedgerFilters")
        filter_row = QHBoxLayout(self._v3_ledger_filters)
        filter_row.setContentsMargins(0, 0, 0, 0)
        filter_row.setSpacing(6)
        old_filter_layout = self.clip_list.filter_buttons.layout()
        for button, stretch in (
                (self.clip_list.all_filter_btn, 53),
                (self.clip_list.unlogged_filter_btn, 86),
                (self.clip_list.needs_fix_filter_btn, 86),
                (self.clip_list.detect_pending_filter_btn, 108)):
            if old_filter_layout is not None:
                old_filter_layout.removeWidget(button)
            reparent_widget(button, self._v3_ledger_filters)
            button.setMinimumWidth(0)
            filter_row.addWidget(button, stretch)
        commands.addWidget(self._v3_ledger_filters)
        layout.insertWidget(2, self._v3_ledger_command_card)

        self._v3_ledger_group_header = QFrame(self.clip_list)
        self._v3_ledger_group_header.setObjectName("V3LedgerGroupHeader")
        group_row = QHBoxLayout(self._v3_ledger_group_header)
        group_row.setContentsMargins(10, 0, 10, 0)
        group_row.setSpacing(6)
        group_chevron = QLabel(self._v3_ledger_group_header)
        group_chevron.setObjectName("V3LedgerGroupChevron")
        group_chevron.setPixmap(tinted_icon(
            "chevron-right-16.svg", "#819087", 12).pixmap(
                QSize(12, 12)).transformed(
                    QTransform().rotate(90),
                    Qt.TransformationMode.SmoothTransformation))
        self._v3_ledger_group_name = QLabel(
            "Unassigned", self._v3_ledger_group_header)
        self._v3_ledger_group_name.setObjectName("V3LedgerGroupName")
        self._v3_ledger_group_count = QLabel(
            "0 plays", self._v3_ledger_group_header)
        self._v3_ledger_group_count.setObjectName("V3LedgerGroupCount")
        self._v3_ledger_group_logged = QLabel(
            "0 logged", self._v3_ledger_group_header)
        self._v3_ledger_group_logged.setObjectName("V3LedgerGroupLogged")
        group_row.addWidget(group_chevron)
        group_row.addWidget(self._v3_ledger_group_name)
        group_row.addWidget(self._v3_ledger_group_count)
        group_row.addStretch(1)
        group_row.addWidget(self._v3_ledger_group_logged)
        table_index = layout.indexOf(self.clip_list.table)
        layout.insertWidget(table_index, self._v3_ledger_group_header)

        self._v3_ledger_footer = QFrame(self.clip_list)
        self._v3_ledger_footer.setObjectName("V3LedgerFooter")
        footer = QHBoxLayout(self._v3_ledger_footer)
        footer.setContentsMargins(10, 0, 10, 0)
        self._v3_ledger_footer_order = QLabel(
            "Review queue · source order", self._v3_ledger_footer)
        self._v3_ledger_footer_order.setObjectName("V3LedgerFooterOrder")
        self._v3_ledger_footer_ready = QLabel(
            "Ready", self._v3_ledger_footer)
        self._v3_ledger_footer_ready.setObjectName("V3LedgerFooterReady")
        footer.addWidget(self._v3_ledger_footer_order)
        footer.addStretch(1)
        footer.addWidget(self._v3_ledger_footer_ready)
        table_index = layout.indexOf(self.clip_list.table)
        layout.insertWidget(table_index + 1, self._v3_ledger_footer)
        zero_body = getattr(self, "_v3_zero_ledger_body", None)
        if zero_body is not None:
            layout.removeWidget(zero_body)
            footer_index = layout.indexOf(self._v3_ledger_footer)
            layout.insertWidget(max(0, footer_index), zero_body, 1)

    def _open_cutup_builder(self) -> None:
        if not self.session or not self._v3_export_can_start():
            return
        editor = self.clip_editor
        if editor._clip is not None and editor.save_state_label.property("state") == "dirty":
            answer = QMessageBox.question(self, "Save clip details",
                "Save your current clip details before choosing cutups?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if answer != QMessageBox.StandardButton.Save or not editor._apply():
                return
        from tapesift.ui_v3.cutup_dialog import CutupDialog
        dialog = CutupDialog(self.session.project, self.session.clips, self)
        session = self.session
        def queue(_dialog):
            if self.session is not session or not self._v3_export_can_start():
                dialog.summary.setText("The project or export queue changed. Reopen Build cutups.")
                return
            if not Path(session.project.source_video_path).is_file():
                dialog.summary.setText("Source video is missing. Relink the source before exporting.")
                return
            for saved in dialog.clips:
                live = session.get_clip(saved.id)
                if live is None or (live.start_ms, live.end_ms, live.enabled, live.details) != (saved.start_ms, saved.end_ms, saved.enabled, saved.details):
                    dialog.summary.setText("Clips changed while reviewing. Reopen Build cutups for the latest film.")
                    return
            try:
                plan = dialog.build_plan(prepare=True)
            except Exception as exc:
                dialog.summary.setText(str(exc))
                return
            if not plan.jobs or not self._try_save():
                return
            if plan.warnings and QMessageBox.question(self, "Export warnings",
                    "\n".join(plan.warnings) + "\n\nContinue?") != QMessageBox.StandardButton.Yes:
                return
            self.export_panel.set_export_style(ExportStyle.CLEAN)
            self._launch_export_plan(plan, True, quick=False)
            if self._export_staging_worker is not None:
                self._v3_review.export_page.configure_package(
                    [c for c in session.clips if any(c.id == j.clip_id or c.id in j.clip_ids for j in plan.jobs)],
                    destination=dialog.destination.text())
                self._v3_review.export_page.title.setText("Cutup export")
                self._v3_review.export_page.mark_started()
                self._set_workspace_stage("export")
                dialog.accept()
                self._sync_v3_export_state()
        dialog.queue_requested.connect(queue)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def _configure_single_clip_export(self, clip_id: str) -> None:
        if self.session is None:
            return
        clip = self.session.get_clip(clip_id)
        if clip is None:
            self.statusBar().showMessage(
                "The selected clip is no longer available", 4000)
            return
        if not clip.enabled:
            self.statusBar().showMessage(
                "This clip is excluded from export. Enable it first.", 5000)
            return
        self._cancel_obsolete_export_preview()
        self._review_export_scope_ids = (clip.id,)
        self._v3_clip_export_scope_ids = (clip.id,)
        self.export_panel.set_export_style(ExportStyle.CLEAN)
        individual = self.export_panel.mode_combo.findData("individual")
        if individual >= 0:
            self.export_panel.mode_combo.setCurrentIndex(individual)
        self.export_panel.set_clip_context([clip])
        self._v3_review.export_page.configure_clip(
            clip_title=clip.clip_title or f"Play {clip.clip_number:03d}",
            duration_ms=clip.duration_ms,
            destination=self.export_panel.output_folder_label.text(),
            preset_name=self.export_panel.preset_combo.currentData(),
            accurate=self.export_panel.accurate_check.isChecked(),
        )
        self._set_workspace_stage("export")
        self._refresh_export_package_context()
        self._sync_v3_export_state()
        self._v3_review.export_page.clip_setup.start_button.setFocus()

    def _cancel_obsolete_export_preview(self) -> None:
        worker = getattr(self, "_export_preview_worker", None)
        if worker is not None:
            worker.cancel()

    def _enabled_session_clips(self) -> list:
        if self.session is None:
            return []
        return [clip for clip in self.session.clips if clip.enabled]

    def _configure_selected_package_export(self) -> None:
        if self.session is None:
            return
        selected = [
            self.session.get_clip(clip_id)
            for clip_id in self.clip_list.selected_clip_ids()
        ]
        clips = [clip for clip in selected
                 if clip is not None and clip.enabled]
        if not clips:
            self.statusBar().showMessage(
                "Select at least one enabled clip to export", 4000)
            return
        self._configure_package_export(clips)

    def _configure_package_export(self, clips=None) -> None:
        if self.session is None:
            return
        self._cancel_obsolete_export_preview()
        chosen = list(clips) if clips is not None else self._enabled_session_clips()
        if not chosen:
            self.statusBar().showMessage(
                "Add or enable at least one play before building a package",
                4000,
            )
            return
        self._review_export_scope_ids = tuple(clip.id for clip in chosen)
        self._v3_package_scope_ids = tuple(clip.id for clip in chosen)
        self.export_panel.set_export_style(ExportStyle.CLEAN)
        both = self.export_panel.mode_combo.findData("both")
        if both >= 0:
            self.export_panel.mode_combo.setCurrentIndex(both)
        self.export_panel.set_clip_context(chosen)
        self._v3_review.export_page.configure_package(
            chosen,
            destination=self.export_panel.output_folder_label.text(),
        )
        self._set_workspace_stage("export")
        self._refresh_export_package_context()
        self._sync_v3_export_state()
        self._v3_review.export_page.package_setup.start_button.setFocus()

    def _remove_v3_package_clip(self, clip_id: str) -> None:
        """Remove one visible package choice without mutating the ledger."""

        remaining = [
            clip for clip in self._v3_route_clips("_v3_package_scope_ids")
            if clip.id != clip_id
        ]
        self._v3_package_scope_ids = tuple(clip.id for clip in remaining)
        self._review_export_scope_ids = self._v3_package_scope_ids
        self.export_panel.set_clip_context(remaining)
        self._v3_review.export_page.configure_package(
            remaining,
            destination=self.export_panel.output_folder_label.text(),
        )
        self._v3_review.export_page.set_ready(
            bool(remaining) and self._v3_export_can_start(),
            "" if remaining else "Add at least one play to build a package.",
        )

    def _reorder_v3_package_clips(self, clip_ids: list[str]) -> None:
        """Keep the visible drag order as the worker's authoritative scope."""

        if self.session is None:
            return
        ordered = [
            self.session.get_clip(clip_id) for clip_id in clip_ids]
        clips = [clip for clip in ordered
                 if clip is not None and clip.enabled]
        self._v3_package_scope_ids = tuple(clip.id for clip in clips)
        self._review_export_scope_ids = self._v3_package_scope_ids
        self.export_panel.set_clip_context(clips)
        self._v3_review.export_page.subtitle.setText(
            f"{len(clips)} selected plays · custom order")

    def _v3_route_clips(self, attribute: str) -> list:
        if self.session is None:
            return []
        return [
            clip for clip_id in getattr(self, attribute, ())
            if (clip := self.session.get_clip(clip_id)) is not None
            and clip.enabled
        ]

    def _v3_export_can_start(self) -> bool:
        running = (
            getattr(self, "_export_staging_worker", None) is not None
            or getattr(self, "_export_retry_worker", None) is not None
            or (self.export_worker is not None
                and self.export_worker.isRunning())
        )
        return bool(getattr(self, "_export_restore_complete", True)) \
            and not running

    def _number_v3_clips(self) -> None:
        if (self.session is None or self.session.read_only or not self.session.clips
                or not self._v3_export_can_start()):
            return
        self.session.sort_clips_by_time(number_prefix=True)
        self._refresh_clip_list()
        # Refresh only derived naming text: leave any unsubmitted detail draft intact.
        editor = self.clip_editor
        if editor._clip is not None:
            editor.heading_label.setText(f"PLAY {editor._clip.clip_number:03d}")
            editor._refresh_preview()
        self._index_current_project()
        self._refresh_v3_export_preview()
        self.statusBar().showMessage(
            f"Numbered all {len(self.session.clips)} clips in film order. "
            "Numbered filenames are visible in Clip Details. "
            "Names kept. Ctrl+Z to undo.", 9000)

    def _start_v3_clip_export(self) -> None:
        if not self._v3_export_can_start():
            self._sync_v3_export_state()
            return
        clips = self._v3_route_clips("_v3_clip_export_scope_ids")
        if len(clips) != 1:
            self._v3_review.export_page.set_ready(
                False, "The selected play is no longer available.")
            return
        setup = self._v3_review.export_page.clip_setup
        self._start_export(
            "individual", setup.preset_combo.currentData(),
            setup.accurate_check.isChecked(), clips=clips, quick=False)
        if self._export_staging_worker is not None or (
                self.export_worker is not None and self.export_worker.isRunning()):
            self._v3_review.export_page.mark_started()
            self._bind_v3_export_progress(self.export_worker)
        self._sync_v3_export_state()

    def _start_v3_package_export(self) -> None:
        if not self._v3_export_can_start():
            self._sync_v3_export_state()
            return
        clips = self._v3_route_clips("_v3_package_scope_ids")
        if not clips:
            self._v3_review.export_page.set_ready(
                False, "The selected plays are no longer available.")
            return
        setup = self._v3_review.export_page.package_setup
        self._start_export(
            setup.mode_combo.currentData(),
            setup.preset_combo.currentData(),
            True, clips=clips, quick=False)
        if self._export_staging_worker is not None or (
                self.export_worker is not None and self.export_worker.isRunning()):
            self._v3_review.export_page.mark_started()
            self._bind_v3_export_progress(self.export_worker)
        self._sync_v3_export_state()

    def _bind_v3_export_progress(self, worker) -> None:
        if worker is None or bool(getattr(worker, "_v3_progress_bound", False)):
            return
        worker.job_progress.connect(self._v3_export_progress)
        worker._v3_progress_bound = True

    def _v3_export_progress(
            self, _job_id: str, _percent: float,
            _elapsed_seconds: float) -> None:
        # This connection is added after ExportPanel.on_job_progress, so the
        # hidden authority owns the value before V3 presents it.
        self._sync_v3_export_state()

    def _sync_v3_export_state(self) -> None:
        review = self._v3_review
        if review is None:
            return
        running = (
            getattr(self, "_export_staging_worker", None) is not None
            or getattr(self, "_export_retry_worker", None) is not None
            or (self.export_worker is not None
                and self.export_worker.isRunning())
        )
        restore_ready = bool(getattr(
            self, "_export_restore_complete", True))
        reason = str(getattr(
            self.export_panel, "_bridge_readiness_error", "") or "")
        ready = restore_ready and not running
        review.export_page.number_clips_button.setEnabled(
            ready and self.session is not None and not self.session.read_only
            and bool(self.session.clips))
        review.export_page.cutups_button.setEnabled(
            ready and self.session is not None and bool(self.session.clips))
        self.clip_editor.number_clips_button.setEnabled(
            review.export_page.number_clips_button.isEnabled())
        if not ready and not reason:
            reason = (
                "Restoring the durable export queue."
                if not restore_ready
                else "Finish or cancel the current export first."
            )
        if not running:
            self._refresh_v3_export_preview()
        review.export_page.set_ready(ready, reason)
        review.export_page.sync_jobs(
            self.export_panel.jobs.values(), running=running)

    def _change_output_folder(self) -> None:
        super()._change_output_folder()
        self._refresh_v3_export_preview()
        self._sync_v3_export_state()

    def _refresh_v3_export_preview(self, *_args) -> None:
        review = getattr(self, "_v3_review", None)
        if review is None or self.session is None:
            return
        page = review.export_page
        setup = page.clip_setup if page.route == "clip" else page.package_setup
        attr = "_v3_clip_export_scope_ids" if page.route == "clip" else "_v3_package_scope_ids"
        clips = self._v3_route_clips(attr)
        destination = self.session.project.output_folder
        for surface in (page.clip_setup, page.package_setup):
            surface.destination.setText(destination or "—")
            surface.destination.setToolTip(destination)
        if not clips:
            return
        plan = None
        try:
            plan = export_service.plan_export(
                self.session.project, clips,
                "individual" if page.route == "clip" else setup.mode_combo.currentData(),
                setup.preset_combo.currentData(), True,
                self.settings.separator_style, prepare=False)
        except (TapeSiftError, OSError) as exc:
            text = str(exc)
        else:
            text = "\n".join(str(Path(job.output_path)) for job in plan.jobs)
        if page.route == "clip":
            setup.filename.setText(Path(text).name if plan is not None and len(plan.jobs) == 1 else text)
            setup.filename.setToolTip(text + "\nPreview; available name is resolved when Start is clicked.")
            clip = clips[0]
            setup.source_range.setText(
                f"{format_ms(clip.start_ms, show_millis=True)} – {format_ms(clip.end_ms, show_millis=True)}"
                f"  ·  {clip.duration_ms / 1000:.3f}s")
        else:
            setup.summary.setToolTip(text)

    def _launch_snapshot_export_worker(self, job) -> None:
        super()._launch_snapshot_export_worker(job)
        self._bind_v3_export_progress(self.export_worker)
        self._sync_v3_export_state()

    def _retry_job(self, job_id: str) -> None:
        super()._retry_job(job_id)
        self._sync_v3_export_state()

    def _export_queue_summaries_ready(self, payload) -> None:
        super()._export_queue_summaries_ready(payload)
        self._sync_v3_export_state()

    def _export_queue_restore_failed(
            self, generation: int, message: str) -> None:
        super()._export_queue_restore_failed(generation, message)
        self._sync_v3_export_state()

    def _make_play_detect_dialog(self, source: Path, duration_ms: int):
        from tapesift.ui_v3.detection_dialog import PlayDetectDialogV3
        return PlayDetectDialogV3(self.settings.ffmpeg_path, source, duration_ms, self)

    def _bind_source_photos(self) -> None:
        self._v3_photo_request = None
        self._v3_photo_captured_request = None
        self._v3_photo_loaded_identity = None
        self._v3_photo_timer = QTimer(self)
        self._v3_photo_timer.setSingleShot(True)
        self._v3_photo_timer.setInterval(3000)
        self._v3_photo_timer.timeout.connect(self._source_photo_timeout)
        panel = self.clip_editor.source_photo_panel
        panel.capture_requested.connect(self._capture_source_photo)
        panel.remove_requested.connect(self._remove_source_photo)
        self.clip_editor.source_photo_context_changed.connect(self._source_photo_context_changed)
        self.player.player.sourceChanged.connect(self._source_photo_media_changed)
        self.player.source_frame_presented.connect(self._source_photo_frame_ready)
        self._source_photo_media_changed()

    def _source_photo_media_changed(self, *_args) -> None:
        self._v3_photo_loaded_identity = None
        if self.session and self.player.player.source().toLocalFile():
            try:
                self._v3_photo_loaded_identity = (
                    source_identity(self.session.project.source_video_path),
                    source_identity(self.player.player.source().toLocalFile()))
            except (OSError, ValueError):
                pass
        self._source_photo_context_changed()

    def _source_photo_context(self):
        session = self.session
        clip = session.get_clip(self._selected_clip_id) if session else None
        if (session is None or session.read_only or clip is None
                or self.clip_editor._clip is None or self.clip_editor._clip.id != clip.id):
            return None
        loaded = self.player.player.source().toLocalFile()
        original = session.project.source_video_path
        if not loaded or not original:
            return None
        return (session, clip.id, clip.start_ms, clip.end_ms, original, loaded,
                self.player._selection_epoch)

    def _cancel_source_photo(self) -> None:
        self._v3_photo_request = None
        self._v3_photo_captured_request = None
        self._v3_photo_timer.stop()
        self.clip_editor.source_photo_panel.set_pending(False)

    def _source_photo_context_changed(self, *_args) -> None:
        self._cancel_source_photo()
        panel = self.clip_editor.source_photo_panel
        panel.set_source(self.session.project.source_video_path if self.session else "")
        writable = bool(self.session and not self.session.read_only and self.clip_editor._clip)
        panel.capture_button.setEnabled(writable)
        panel.remove_action.setEnabled(writable and bool(self.clip_editor._clip.source_photo
            or self.clip_editor._clip.source_photo_png))

    def _source_photo_timeout(self) -> None:
        self._cancel_source_photo()
        self.clip_editor.source_photo_panel.show_message("No settled frame. Pause or step to a frame and try again.")

    def _capture_source_photo(self) -> None:
        if self._v3_photo_request is not None:
            self._cancel_source_photo()
            return
        context = self._source_photo_context()
        panel = self.clip_editor.source_photo_panel
        if context is None:
            panel.show_message("Select a clip in a writable project with a loaded film.")
            return
        try:
            original, loaded = source_identity(context[4]), source_identity(context[5])
            if (original, loaded) != self._v3_photo_loaded_identity:
                raise ValueError("The loaded film changed. Reopen the project before capturing.")
        except (OSError, ValueError) as exc:
            panel.show_message(f"Source frame unavailable: {exc}")
            return
        self._v3_photo_request = (context, original, loaded)
        self._v3_photo_captured_request = None
        panel.set_pending(True)
        self._v3_photo_timer.start()
        self._source_photo_frame_ready()

    def _source_photo_frame_ready(self, *_args) -> None:
        request = self._v3_photo_request
        if request is None or self._v3_photo_captured_request is request:
            return
        if self._source_photo_context() != request[0]:
            self._cancel_source_photo()
            return
        snapshot = painted_snapshot(self.player)
        if snapshot is None:
            return
        image, pts = snapshot
        self._v3_photo_captured_request = request
        # The signal can arrive inside paintEvent; only persistence is deferred.
        QTimer.singleShot(0, lambda: self._save_source_photo(request, image, pts))

    def _save_source_photo(self, request, image, pts: int) -> None:
        if request is not self._v3_photo_request:
            return
        context, original, loaded = request
        panel = self.clip_editor.source_photo_panel
        try:
            if (self._source_photo_context() != context
                    or source_identity(context[4]) != original
                    or source_identity(context[5]) != loaded):
                raise ValueError("Source or selected clip changed. Capture again.")
            data = QByteArray()
            buffer = QBuffer(data)
            buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if not image.save(buffer, "PNG"):
                raise ValueError("Could not encode the source frame.")
            buffer.close()
            png = bytes(data)
            session, clip_id = context[:2]
            clip = session.get_clip(clip_id)
            photo = make_photo(clip, png, pts, original, loaded)
            session.set_source_photo(clip_id, photo, png)
        except Exception as exc:
            self._cancel_source_photo()
            self._refresh_source_photo_binding()
            panel.show_message(f"Photo not saved: {exc}")
            return
        self._cancel_source_photo()
        self._refresh_source_photo_binding()
        panel.show_message("Source photo saved. Undo is available.")

    def _refresh_source_photo_binding(self) -> None:
        clip = self.session.get_clip(self._selected_clip_id) if self.session else None
        # A failed transaction can restore Clip instances. Preserve staged inputs.
        if clip and self.clip_editor._clip and self.clip_editor._clip.id == clip.id:
            self.clip_editor._clip = clip
        self.clip_editor.source_photo_panel.set_clip(clip)
        self._source_photo_context_changed()

    def _remove_source_photo(self) -> None:
        self._cancel_source_photo()
        session = self.session
        clip = session.get_clip(self._selected_clip_id) if session else None
        if session is None or session.read_only or clip is None:
            return
        try:
            session.set_source_photo(clip.id, {}, b"")
        except Exception as exc:
            self._refresh_source_photo_binding()
            self.clip_editor.source_photo_panel.show_message(f"Photo not removed: {exc}")
            return
        self._refresh_source_photo_binding()
        self.clip_editor.source_photo_panel.show_message("Source photo removed. Undo is available.")

    def _make_start_screen(self, settings) -> StartScreenV3:
        return StartScreenV3(settings)

    def _make_clip_editor(self, settings) -> ClipDetailsV3:
        editor = ClipDetailsV3(settings)
        editor.logging_defaults_provider = lambda: (
            dict(self.session.project.logging_defaults) if self.session else {})
        editor.previous_clip_provider = lambda clip: (
            drive_suggestions.previous_clip(self.session.clips, clip) if self.session else None)
        editor.teams_requested.connect(self._edit_game_teams)
        editor.field_draft_changed.connect(self._update_play_field_ribbon)
        return editor

    def _update_play_field_ribbon(self, details: dict) -> None:
        ribbon = getattr(self, "_v3_play_field_ribbon", None)
        if ribbon is not None:
            teams = self.session.project.game_team_ids if self.session else []
            ribbon.set_details(details, teams)
            self._update_field_quarters()
            ribbon.setEnabled(getattr(self.clip_editor, "_clip", None) is not None)

    def _update_field_quarters(self) -> None:
        ribbon = getattr(self, "_v3_play_field_ribbon", None)
        if ribbon is not None:
            grid = self.player.attribute_grid
            ribbon.set_quarters(grid.quarter_spans(join_clip_gaps=True), grid._visible_range)

    def _refresh_timeline_presentation(self) -> None:
        super()._refresh_timeline_presentation()
        from tapesift.services.heatmap_palette import quarter_starts
        grid = self.player.attribute_grid
        clips = {clip.id: clip for clip in grid._clips}
        mode = "play_type"
        # V3 uses the same explicit play-family colors as its Tag Map. A TD
        # in notes/name/results must not change a Pass block's family color.
        if mode == "play_type" and clips:
            blocks, legend = [], {}
            for block in self.player.slider.blocks():
                clip = clips.get(block.clip_id)
                if clip is None:
                    blocks.append(block)
                    continue
                entries = grid._primary_color_entries(clip)
                label, color = entries[0] if entries else ("Unclassified", "#677078")
                key = label.casefold().replace(" ", "_")
                legend[key] = (key, label, QColor(color))
                blocks.append(replace(block, kind=key, category_label=label, colour=color))
            self.player.set_timeline_key(mode, tuple(legend.values()))
            # This strip has one stable meaning, even with an older saved mode.
            for action in self.player.timeline_key_menu.actions():
                if action.isCheckable():
                    action.setVisible(False)
            self.player.set_clip_blocks(blocks)
        grid._quarter_pylons = tuple(quarter_starts(clips.values())[1:])
        grid.update()
        self.player.timeline_key_menu.addAction(
            "Orange pylons mark quarter changes in the Tag Map").setEnabled(False)
        for timeline in (self.player.slider, self.player.timeline_overview):
            if timeline is not None:
                timeline.set_ink_blocks(True)
                timeline.set_quarter_pylons(())

    def _edit_game_teams(self) -> None:
        if self.session is None or self.session.read_only:
            return
        project = self.session.project
        dialog = GameTeamsDialog(project.game_team_ids, self)
        if dialog.exec() != GameTeamsDialog.DialogCode.Accepted:
            return
        values = dialog.selected_ids()
        if values == project.game_team_ids:
            return
        previous = list(project.game_team_ids)
        previous_updated = project.updated_at
        previous_dirty = self.session.dirty
        project.game_team_ids = values
        try:
            self.session.save()
        except Exception as exc:
            project.game_team_ids = previous
            project.updated_at = previous_updated
            self.session.dirty = previous_dirty
            QMessageBox.warning(self, "Could not save game teams", str(exc))
            return
        self.clip_editor.set_game_teams(values)
        self._update_play_field_ribbon(self.clip_editor._collect_details())
        self.statusBar().showMessage("Game teams saved", 3000)

    def _make_library_screen(self, settings) -> LazyLibrarySearchScreenV3:
        return LazyLibrarySearchScreenV3(settings)

    # ---------- V3 dialog family ----------

    def _show_how_it_works(self, section: int = 0) -> None:
        HowItWorksDialogV3(self, section).exec()

    def _open_v3_tag_colors(self) -> None:
        if not self._require_session():
            return
        project = self.session.project
        dialog = TagColorManagerDialogV3(self._known_tags(), project.tag_styles, self)
        while dialog.exec() == TagColorManagerDialogV3.DialogCode.Accepted:
            previous = deepcopy(project.tag_styles)
            project.tag_styles = dialog.tag_styles()
            try:
                self.session.save()
            except Exception as exc:
                project.tag_styles = previous
                QMessageBox.critical(self, "Tag styles were not saved", str(exc))
                continue
            self._refresh_clip_list()
            self.statusBar().showMessage("Timeline tag styles saved", 3000)
            return

    def _open_settings(self, section: int = 0) -> None:
        auto_preview_before = self.settings.scrub_proxy_enabled
        dialog = SettingsDialogV3(self.settings, self)
        dialog.section_nav.setCurrentRow(section)
        if dialog.exec() == SettingsDialogV3.DialogCode.Accepted:
            self.player.audio.setVolume(self.settings.volume / 100)
            self.clip_editor.apply_dropdown_mode()
            self.clip_editor.apply_density(self.settings.inspector_density)
            self.clip_editor.apply_field_layout()
            self._apply_tag_map_labels()
            self._apply_v3_tag_map_rows()
            if getattr(self, "_guided", None):
                self._guided.apply_dropdown_mode()
            if self.session:
                self._refresh_clip_list()
            self.statusBar().showMessage("Settings saved", 3000)
            if auto_preview_before != self.settings.scrub_proxy_enabled:
                if self.settings.scrub_proxy_enabled:
                    self._retry_auto_preview()
                else:
                    self._stop_proxy_worker()
                    self.proxy_banner_widget.hide()

    def _check_recovery(self) -> None:
        if (
            getattr(self, "temporal_review_session", None) is not None
            or getattr(self, "snap_calibration_session", None) is not None
            or getattr(self, "_v2_initializing", False)
        ):
            MainWindowV2._check_recovery(self)
            return
        pending = recovery_service.pending_recovery()
        if not pending:
            return
        db_path, name = pending
        dialog = RecoveryDialogV3(db_path, name, self)
        if dialog.exec() != RecoveryDialogV3.DialogCode.Accepted:
            return
        if dialog.selected_action is RecoveryAction.RESTORE:
            self._open_project(db_path)
        elif dialog.selected_action is RecoveryAction.READ_ONLY:
            self._open_recovery_read_only(db_path)
        elif dialog.selected_action is RecoveryAction.DISCARD:
            recovery_service.mark_closed()

    def _open_recovery_read_only(self, db_path: str) -> bool:
        session = None
        try:
            session = ProjectSession.open_read_only(Path(db_path))
            if not self._activate_session(session):
                return False
            self._set_recovery_read_only_surface(True)
            self.setWindowTitle(
                f"{session.project.name} - TapeSift (Read-Only Recovery)")
            self.statusBar().showMessage(
                "Recovery opened read-only. The project file cannot be changed.")
            return True
        except TapeSiftError as exc:
            if session is not None and session is not self.session:
                session.close()
            QMessageBox.warning(
                self, "Could not open recovery read-only", exc.user_text())
        except Exception as exc:
            if session is not None and session is not self.session:
                try:
                    session.close()
                except Exception:
                    pass
            log.exception("Unexpected read-only recovery failure: %s", db_path)
            QMessageBox.warning(
                self,
                "Could not open recovery read-only",
                f"TapeSift could not inspect this project:\n\n{exc}",
            )
        return False

    @staticmethod
    def _action_text(item) -> str:
        return item.text().replace("&", "").replace("…", "").strip()

    def _set_recovery_read_only_surface(self, enabled: bool) -> None:
        if not enabled:
            for item, was_enabled in self._v3_read_only_locks:
                try:
                    item.setEnabled(was_enabled)
                except RuntimeError:
                    pass
            self._v3_read_only_locks.clear()
            self._v3_read_only_recovery = False
            return
        if self._v3_read_only_recovery:
            return
        self._v3_read_only_recovery = True

        def lock(item) -> None:
            self._v3_read_only_locks.append((item, item.isEnabled()))
            item.setEnabled(False)

        lock(self.clip_editor)
        mutation_actions = {
            "Save Project", "Rename Project", "Project Settings",
            "Undo", "Redo", "Sort Clips by Start Time",
            "Review Detection Coverage",
            "Start Autodetect Test Batch from In/Out Range",
            "Finish Autodetect Test Batch", "Cancel Autodetect Test Batch",
            "Load Video",
        }
        for action in self.findChildren(QAction):
            if self._action_text(action) in mutation_actions:
                lock(action)

        mutation_buttons = {
            "Detect Plays", "New Clip", "+ Clip", "Add Clip",
            "Save Play", "Save + Next", "Relink Source",
            "Load Video", "Cut", "Split", "Merge", "Delete",
        }
        for button in self.workspace.findChildren(QPushButton):
            if self._action_text(button) in mutation_buttons:
                lock(button)

        for key in (
            "I", "O", "A", "Delete", "Ctrl+D", "N", "[", "]",
            "C", "M", "Ctrl+K", "U", "Shift+U", "Ctrl+M", "Ctrl+E",
            "G",
        ):
            shortcut = self._transport_shortcuts.get(key)
            if shortcut is not None:
                lock(shortcut)
        for shortcut in getattr(self, "_quick_tag_shortcuts", ()):
            lock(shortcut)

    def _sync_transport_shortcut_page(self, index: int) -> None:
        super()._sync_transport_shortcut_page(index)
        self._sync_map_shuttle_shortcuts()
        if not getattr(self, "_v3_read_only_recovery", False):
            return
        for key in (
            "I", "O", "A", "Delete", "Ctrl+D", "N", "[", "]",
            "C", "M", "Ctrl+K", "U", "Shift+U", "Ctrl+M", "Ctrl+E",
            "G",
        ):
            shortcut = self._transport_shortcuts.get(key)
            if shortcut is not None:
                shortcut.setEnabled(False)
        for shortcut in getattr(self, "_quick_tag_shortcuts", ()):
            shortcut.setEnabled(False)

    def _export_heatmap(self) -> None:
        if self.session is None:
            QMessageBox.information(self, "Game Heat Map", "Open a project first.")
            return
        from tapesift.ui_v3.heatmap_page import HeatmapPageV3, saved_heatmap
        try:
            data, folder, stem = saved_heatmap(self.session)
        except (OSError, ValueError, sqlite3.Error) as error:
            QMessageBox.warning(self, "Game Heat Map", str(error))
            return
        self.player.shuttle_stop()
        self.control_center.jog_window.close()
        self._exit_player_fullscreen()
        if self._v3_heatmap is None:
            self._v3_heatmap = HeatmapPageV3(self.stack)
            self._v3_heatmap.back_requested.connect(self._return_from_heatmap)
            self.stack.addWidget(self._v3_heatmap)
        self._v3_heatmap.set_snapshot(data, folder, stem,
                                    draft_path=self.session.db_path.with_suffix(".social.json"))
        self.stack.setCurrentWidget(self._v3_heatmap)
        self._v3_heatmap.back.setFocus()

    def _return_from_heatmap(self) -> None:
        self.stack.setCurrentWidget(self.workspace)
        self._set_workspace_stage("review")
        self._return_focus_to_playback()

    def _activate_session(self, session) -> bool:
        self._ensure_v3_review()
        if not getattr(session, "read_only", False):
            self._set_recovery_read_only_surface(False)
        if not super()._activate_session(session):
            return False

        # Shell V3 opens directly into Review. Keep its visually active play,
        # inspector, and transport actions on one authoritative selection from
        # the first painted frame. Without this handoff the ledger could look
        # ready while Find Snap stayed disabled until the user reselected a
        # row. The normal select_clip path remains the only owner of selection
        # state; snap prediction and playback behavior are unchanged.
        selected = self.clip_list.selected_clip_ids()
        target_id = selected[0] if len(selected) == 1 else None
        if target_id is None and session.clips:
            target_id = session.clips[0].id
        if target_id is not None:
            self.select_clip(
                target_id,
                seek=False,
                focus_player=False,
                review_autoplay=False,
            )
        else:
            self._sync_predicted_snap_action()
        return True

    def _close_project(self) -> bool:
        closed = super()._close_project()
        if closed and self.session is None and not self._app_closing:
            self._set_recovery_read_only_surface(False)
        return closed

    def _v3_home_drop_path(self, event):
        if self.stack.currentWidget() is not self.start_screen \
                or self.session is not None:
            return None
        path = StartScreenV3._path_from_event(event)
        if path is None:
            return None
        point = event.position().toPoint()
        origin = self.start_screen.mapTo(self, QPoint(0, 0))
        if not self.start_screen.rect().translated(origin).contains(point):
            return None
        return path

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self.stack.currentWidget() is self.start_screen \
                and self.session is None:
            path = self._v3_home_drop_path(event)
            self.start_screen.set_drag_active(
                path is not None,
                path.name if path is not None else None,
            )
            if path is not None:
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if self.stack.currentWidget() is self.start_screen \
                and self.session is None:
            path = self._v3_home_drop_path(event)
            self.start_screen.set_drag_active(
                path is not None,
                path.name if path is not None else None,
            )
            if path is not None:
                event.acceptProposedAction()
            else:
                event.ignore()
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        if self.stack.currentWidget() is self.start_screen \
                and self.session is None:
            self.start_screen.set_drag_active(False)
            event.accept()
            return
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if self.stack.currentWidget() is self.start_screen \
                and self.session is None:
            path = self._v3_home_drop_path(event)
            self.start_screen.set_drag_active(False)
            if path is not None:
                event.acceptProposedAction()
                self.start_screen.queue_dropped_path(path)
            else:
                event.ignore()
            return
        super().dropEvent(event)

    # ---------- native shell boundary ----------

    def _configure_top_level_surface(self) -> None:
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

    def _create_modal_backdrop_controller(self):
        return None

    def _ensure_native_frame(self) -> None:
        """The operating system already owns V3's frame and hit testing."""

    def paintEvent(self, event) -> None:  # noqa: N802
        QMainWindow.paintEvent(self, event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_v3_window_bezel()
        if getattr(self, "_v3_review", None) is not None:
            self._apply_v3_review_geometry()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_v3_window_bezel()

    def _sync_v3_window_bezel(self) -> None:
        bezel = getattr(self, "_v3_window_bezel", None)
        if bezel is None:
            return
        if not bezel.isVisible():
            return
        self._set_v3_bezel_maximized(
            bool(self.isMaximized() or self.isFullScreen()))
        bezel.setGeometry(self.rect())
        bezel.raise_()

    def _sync_v3_page_chrome(self) -> None:
        """Keep every page flush inside the native client-owned bezel."""
        page = self.stack.currentWidget()
        flush = page in {self.start_screen, self.library_screen}
        margins = (0, 0, 0, 0)
        current = self.contentsMargins()
        if (current.left(), current.top(), current.right(), current.bottom()) \
                != margins:
            self.setContentsMargins(*margins)
        bezel = getattr(self, "_v3_window_bezel", None)
        if bezel is not None:
            bezel.setVisible(not flush)

    def _set_v3_bezel_maximized(self, maximized: bool) -> None:
        bezel = getattr(self, "_v3_window_bezel", None)
        if bezel is None:
            return
        maximized = bool(maximized)
        state = "maximized" if maximized else "restored"
        if bezel.property("bezelState") != state:
            for layer in (bezel, bezel.band, bezel.inner):
                layer.setProperty("bezelState", state)
                layer.style().unpolish(layer)
                layer.style().polish(layer)

    def _install_centered_application_menu(self) -> None:
        menu_bar = QMainWindow.menuBar(self)
        shell = NativeApplicationBar(menu_bar, self)
        self._centered_menu_bar = menu_bar
        self._centered_menu_shell = shell
        self.setMenuWidget(shell)

    # ---------- stable Review composition ----------

    def _decorate_workspace(self) -> None:
        super()._decorate_workspace()

    def _ensure_v3_review(self) -> None:
        if self._v3_review is not None:
            return
        self._adopt_stable_review_surfaces()
        self._rewire_v3_export_routes()
        self._sync_v3_review_state()
        self._sync_v3_rail_actions()

    @staticmethod
    def _release_dock_widget(
            dock: QDockWidget, expected: QWidget) -> QWidget:
        if dock.widget() is not expected:
            raise RuntimeError(
                f"Unexpected V2 dock content for {dock.objectName()}")
        replacement = QWidget(dock)
        dock.setWidget(replacement)
        reparent_widget(expected, None)
        return expected

    def _adopt_stable_review_surfaces(self) -> None:
        outer = self.workspace.layout()
        if not isinstance(outer, QVBoxLayout):
            raise RuntimeError("TapeSift Review root is not a vertical layout")

        player_dock = self._workspace_docks["player"]
        ledger_dock = self._workspace_docks["clips"]
        details_dock = self._workspace_docks["play_details"]
        export_dock = self._export_dock
        player_panel = self._release_dock_widget(
            player_dock, self._player_panel)
        ledger = self._release_dock_widget(
            ledger_dock, self.clip_ledger_scroll)
        details = self._release_dock_widget(
            details_dock, self.clip_details_scroll)
        export_surface = self._release_dock_widget(
            export_dock, self.export_panel)

        for content in (player_panel, ledger, details, export_surface):
            reparent_widget(content, self.workspace)
            content.setMinimumWidth(0)
            content.setMaximumWidth(16777215)
            content.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )

        # V2 pins the inner list to 340px as well as its outer scroll area.
        # Release both constraints for the standard narrower V3 ledger.
        self.clip_list.setMinimumWidth(0)
        self.clip_list.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        self.clip_editor.set_collapsed(False)
        self.clip_editor.collapse_btn.hide()
        self.clip_editor.collapse_btn.setEnabled(False)

        placeholder = getattr(self, "_player_placeholder", None)
        if placeholder is not None:
            outer.removeWidget(placeholder)
            placeholder.hide()
            placeholder.deleteLater()

        review = ReviewWorkspaceV3(
            ledger=ledger,
            workbench=player_panel,
            details=details,
            export_surface=export_surface,
            action_sources={
                "export": self.clip_editor.quick_export_btn,
                "package": self.clip_editor.package_btn,
                "save": self.clip_editor.apply_btn,
                "next": self.clip_editor.save_next_btn,
            },
            film_surface=self.player.video_widget,
            detect_source=self.detect_plays_button,
            new_clip_source=self.new_clip_button,
            initial_state=self._v3_initial_state,
            parent=self.workspace,
        )
        review.rail_state_changed.connect(self._v3_rail_state_changed)
        review.review_requested.connect(self._return_from_v3_export)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(review, 1)
        self._v3_review = review
        self._configure_v3_zero_surfaces()

        retired = list(self._workspace_docks.values())
        retired.extend((self._control_center_dock, self._export_dock))
        for dock in retired:
            self.removeDockWidget(dock)
            dock.hide()
            reparent_widget(dock, self)
            dock.deleteLater()
        self._retired_v2_docks = retired
        self._workspace_docks = {}
        self._default_dock_areas = {}
        self._dock_visibility = {}
        self._control_center_dock = None
        self._export_dock = None
        self._workspace_stage = "review"
        self._sync_v3_ledger_summary()
        self.player.attribute_grid.enable_review_style()
        grid = self.player.attribute_grid
        self._apply_tag_map_labels()
        # A timestamp has one x position in both views, including the focus lens.
        grid.timeline_x_for = lambda ms: grid.plot_left() + self.player.slider._x_for(ms)
        self.player.attribute_grid.tag_styles_provider = lambda: (
            self.session.project.tag_styles if self.session else {})
        self.player.attribute_grid.setProperty("reviewAccent", "#e9aa3f")
        self.player.attribute_grid.heatmap_button.setIcon(
            tinted_icon("share-20.svg", "#b7c2bc", 16))
        self.player.attribute_grid.heatmap_button.setIconSize(QSize(16, 16))
        self.player.attribute_grid.heatmap_button.clicked.connect(
            self.export_heatmap_action.trigger)
        grid = self.player.attribute_grid
        header = self.player._timeline_header_layout
        for control in (grid.color_by, grid.heatmap_button):
            grid.toolbar.layout().removeWidget(control)
            header.insertWidget(header.count() - 1, control)
            control.setFixedHeight(24)
            control.show()
        grid.heatmap_button.setStyleSheet(
            "QPushButton { background:transparent; border:1px solid transparent;"
            "padding:0 3px; color:#e8e5db; font:11px 'IBM Plex Sans'; }"
            "QPushButton:hover { background:#1c271f; }"
            "QPushButton:focus { border-color:#efb047; }"
            "QPushButton:disabled { color:#647067; }")
        grid.color_by.setFixedWidth(86)
        grid.heatmap_button.setText("Heat Map")
        grid.heatmap_button.setFixedWidth(88)
        grid.set_review_toolbar_external()
        collapse = self.player.tag_map_collapse_button
        collapse.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        inherited_tag_presentation = self.player._sync_tag_map_presentation
        def sync_tag_presentation():
            inherited_tag_presentation()
            if not grid.rows() or self._workspace_stage == "export":
                grid.hide()
            # Responsive folds block button signals; follow the state owner.
            collapse.setArrowType(Qt.ArrowType.NoArrow)
            arrow = tinted_icon("chevron-down-16.svg", size=20).pixmap(20, 20)
            collapse.setIcon(QIcon(arrow if collapse.isChecked() else arrow.transformed(QTransform().rotate(180))))
            collapse.setIconSize(QSize(20, 20))
            self._position_map_controls()
        self.player._sync_tag_map_presentation = sync_tag_presentation
        sync_tag_presentation()
        collapse.setFixedSize(32, 30)
        self._install_play_field_ribbon()
        self._install_v3_vertical_rhythm()
        self._apply_v3_tag_map_rows()
        self._apply_v3_review_geometry()

    def _install_play_field_ribbon(self) -> None:
        from tapesift.ui_v3.tag_map_field import TagMapField
        header = self.player._timeline_header_layout
        slot = self.player.quick_tag_slot
        self.player.mount_quick_tags(None)
        self.quick_tag_tray.setParent(self.player)
        self.quick_tag_tray.hide()
        header.removeWidget(slot)
        grid = self.player.attribute_grid
        grid._field_surface = TagMapField()
        grid.footer.hide()
        grid._compact_review_ruler = True
        grid._layout_review_controls()
        tools = QWidget(grid)
        tools.setObjectName("V3TagMapTools")
        tools.setFixedSize(68, 32)
        row = QHBoxLayout(tools)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        heading = self.player.timeline_header.findChild(QLabel, "TagMapHeading")
        header.removeWidget(heading)
        heading.hide()
        manage = self.quick_tag_tray.manage_button
        for control in (grid.color_by, grid.heatmap_button, manage):
            header.removeWidget(control)
            control.setParent(self.quick_tag_tray)
            control.hide()
        menu_button = QToolButton(tools)
        menu_button.setIcon(QIcon(tinted_icon("more-horizontal-16.svg", size=20).pixmap(20, 20).transformed(QTransform().rotate(90))))
        menu_button.setIconSize(QSize(20, 20))
        menu_button.setAccessibleName("Tag Map options")
        menu_button.setToolTip("Color by, Heat Map and Manage tags")
        menu_button.setFixedSize(32, 30)
        map_tool_style = "QToolButton {background:#080c0a; color:#dedfd7; border:1px solid #4b574e; border-radius:3px; padding:0; min-height:0;} QToolButton::menu-indicator {image:none;} QToolButton:hover {background:#233129; border-color:#86998b;} QToolButton:focus {border-color:#efb047;}"
        menu_button.setStyleSheet(map_tool_style)
        menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(menu_button)
        menu.aboutToShow.connect(lambda: self._populate_map_menu(menu))
        menu_button.setMenu(menu)
        self._v3_map_menu_button = menu_button
        row.addWidget(menu_button)
        collapse = self.player.tag_map_collapse_button
        header.removeWidget(collapse)
        collapse.setStyleSheet(map_tool_style)
        row.addWidget(collapse)
        self._v3_tag_map_tools = tools
        grid.installEventFilter(self)
        self.player.timeline_header.installEventFilter(self)
        grid.quarterContextChanged.connect(self._update_field_quarters)
        self._v3_play_field_ribbon = None
        self._position_map_controls()

    def _apply_tag_map_labels(self) -> None:
        grid = self.player.attribute_grid
        # The full-width review shares one origin with the clip timeline.
        # Ignore the legacy label preference so saved profiles cannot restore the inset.
        grid.set_review_labels_visible(False)
        self.player.timeline_scrub_layout.setContentsMargins(grid.plot_left(), 0, 12, 0)
        self.player.timeline_scrub_layout.activate()
        grid.update()

    def _populate_map_menu(self, menu: QMenu) -> None:
        menu.clear()
        grid = self.player.attribute_grid
        colors = menu.addMenu("Color by: " + grid.color_by.currentText())
        colors.setEnabled(grid.color_by.isEnabled())
        for index in range(grid.color_by.count()):
            action = colors.addAction(grid.color_by.itemText(index))
            action.setCheckable(True)
            action.setChecked(index == grid.color_by.currentIndex())
            action.triggered.connect(lambda _checked=False, i=index: grid.color_by.setCurrentIndex(i))
        self.player.timeline_key_menu.setTitle("Clip bar color key")
        menu.addMenu(self.player.timeline_key_menu)
        lanes = menu.addMenu("Tag Map rows")
        for row in grid._all_review_rows:
            action = lanes.addAction(row.label.replace("&", "&&"))
            action.setCheckable(True)
            action.setChecked(row in grid.rows())
            action.triggered.connect(lambda checked, key=row.key: self._set_tag_map_row_visible(key, checked))
        for title, button in (("Heat Map", grid.heatmap_button),
                              ("Manage tags", self.quick_tag_tray.manage_button)):
            action = menu.addAction(title)
            action.setEnabled(button.isEnabled())
            action.triggered.connect(lambda _checked=False, b=button: b.click())

    def _apply_v3_tag_map_rows(self) -> None:
        grid = self.player.attribute_grid
        grid.set_hidden_review_rows(self.settings.hidden_tag_map_rows)
        self.player._sync_tag_map_presentation()
        self._apply_v3_review_geometry()

    def _set_tag_map_row_visible(self, key: str, visible: bool) -> None:
        grid = self.player.attribute_grid
        hidden = {row.key for row in grid._all_review_rows if row not in grid.rows()}
        hidden.discard(key) if visible else hidden.add(key)
        previous = self.settings.hidden_tag_map_rows
        self.settings.hidden_tag_map_rows = [row.key for row in grid._all_review_rows if row.key in hidden]
        try:
            self.settings.save()
        except Exception as exc:
            self.settings.hidden_tag_map_rows = previous
            QMessageBox.critical(self, "Tag Map preferences were not saved", str(exc))
            return
        self._apply_v3_tag_map_rows()

    def _position_map_controls(self) -> None:
        tools = getattr(self, "_v3_tag_map_tools", None)
        if tools is None:
            return
        # The existing collapse control remains the state owner in Tools.
        tools.hide()
        self.player.timeline_header.setFixedHeight(0)
        self.player.timeline_header.hide()

    def _grid_cell_choice_picked(self, clip_id: str, key: str, index: int) -> None:
        if not self.session or self.session.read_only:
            return
        grid = self.player.attribute_grid
        if grid.hasFocus():
            from tapesift.ui_v2.attribute_grid import EDIT_CHOICES, set_down_keeping_distance
            choices = EDIT_CHOICES.get(key, ())
            if 0 <= index < len(choices):
                values = dict(choices[index][1])
                current = self.clip_editor._collect_details()
                if key == "down":
                    values["down_distance"] = set_down_keeping_distance(
                        current.get("down_distance", ""), values["down_distance"])
                if key == "result":
                    values["result"] = result_service.toggle_result(current.get("result", ""), values["result"])
                self._apply_map_cell(clip_id, values)
                self._focus_map_row(key)
            return
        if key != "result":
            return super()._grid_cell_choice_picked(clip_id, key, index)
        from tapesift.ui_v2.attribute_grid import EDIT_CHOICES
        choices = EDIT_CHOICES["result"]
        if not 0 <= index < len(choices) or not self.session:
            return
        if not self.select_clip(clip_id, seek=False):
            return
        clip = self.session.get_clip(clip_id)
        if clip is not None:
            value = choices[index][1]["result"]
            current = clip.details.get("result", "")
            removed = tuple(item for item in result_service.split_results(current)
                            if result_service.has_result(item, value))
            self.clip_editor.apply_quick_details({
                "result": result_service.toggle_result(current, value),
            }, remove_tag_values=removed + ((value,) if removed else ()))

    def _grid_cell_edit_requested(self, clip_id: str, key: str) -> None:
        if not self.session or self.session.read_only:
            return
        grid = self.player.attribute_grid
        row_index = next((i for i, r in enumerate(grid.rows()) if r.key == key), None)
        if row_index is None:
            return
        editor = self.clip_editor
        if editor._clip and editor._clip.id != clip_id and editor.save_state_label.property("state") == "dirty":
            if not editor._apply():
                return
        if not self.select_clip(clip_id, seek=False, focus_player=False, review_autoplay=False):
            return
        self._focus_map_row(key)
        clip = self.session.get_clip(clip_id)
        # Start from the inspector's unsaved draft, so opening and cancelling
        # this popup cannot overwrite work already entered in Clip Details.
        draft = deepcopy(clip)
        draft.details = editor._collect_details()
        draft.notes = editor.notes_edit.toPlainText()
        x1, x2 = grid.clip_span(clip)
        top = grid._row_top(row_index)
        anchor = QRect(grid.mapToGlobal(QPoint(max(0, int(x1)), top)),
                       QSize(max(6, int(x2 - x1)), grid._row_height(grid.rows()[row_index])))
        from tapesift.ui_v3.map_cell_editor import MapCellEditor
        old = getattr(self, "_v3_map_editor", None)
        if old is not None:
            old.close()
            old.deleteLater()
        self._v3_map_editor = MapCellEditor(self, draft, grid.rows()[row_index], anchor)

    def _configure_v3_zero_surfaces(self) -> None:
        """Refine existing empty widgets without replacing their authority."""
        ledger_empty = self.clip_list.empty_state
        ledger_empty.setObjectName("V3ZeroLedgerMessage")
        ledger_empty.setText(
            "Scan the film to build the ledger, or mark the first clip "
            "manually.")
        ledger_empty.setWordWrap(True)
        ledger_layout = self.clip_list.layout()
        if ledger_layout is not None and not hasattr(
                self, "_v3_zero_ledger_body"):
            ledger_layout.removeWidget(ledger_empty)

            self._v3_zero_ledger_body = QFrame(self.clip_list)
            self._v3_zero_ledger_body.setObjectName("V3ZeroLedgerBody")
            zero_body = QVBoxLayout(self._v3_zero_ledger_body)
            zero_body.setContentsMargins(18, 18, 18, 18)
            zero_body.setSpacing(0)
            zero_body.addStretch(1)

            self._v3_zero_ledger_card = QFrame(
                self._v3_zero_ledger_body)
            self._v3_zero_ledger_card.setObjectName("V3ZeroLedgerCard")
            self._v3_zero_ledger_card.setMaximumWidth(292)
            card = QVBoxLayout(self._v3_zero_ledger_card)
            card.setContentsMargins(20, 21, 20, 21)
            card.setSpacing(8)

            scan = QLabel(self._v3_zero_ledger_card)
            scan.setObjectName("V3ZeroLedgerIcon")
            scan.setAlignment(Qt.AlignmentFlag.AlignCenter)
            scan.setPixmap(tinted_icon(
                "scan-object-20.svg", "#89958c", 30
            ).pixmap(QSize(30, 30)))
            card.addWidget(scan)

            title = QLabel("No plays yet", self._v3_zero_ledger_card)
            title.setObjectName("V3ZeroLedgerTitle")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card.addWidget(title)

            reparent_widget(ledger_empty, self._v3_zero_ledger_card)
            ledger_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ledger_empty.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            card.addWidget(ledger_empty)

            self._v3_zero_ledger_detect = QPushButton(
                "Run Detect Plays", self._v3_zero_ledger_card)
            self._v3_zero_ledger_detect.setObjectName(
                "V3ZeroLedgerDetect")
            self._v3_zero_ledger_detect.setIcon(tinted_icon(
                "scan-object-accent-20.svg", size=16))
            self._v3_zero_ledger_detect.setIconSize(QSize(16, 16))
            self._v3_zero_ledger_detect.clicked.connect(
                self.detect_plays_button.click)
            card.addWidget(self._v3_zero_ledger_detect)

            zero_body.addWidget(
                self._v3_zero_ledger_card, 0,
                Qt.AlignmentFlag.AlignHCenter)
            zero_body.addStretch(1)
            footer = getattr(self, "_v3_ledger_footer", None)
            if footer is None:
                ledger_layout.addWidget(self._v3_zero_ledger_body, 1)
            else:
                footer_index = ledger_layout.indexOf(footer)
                ledger_layout.insertWidget(
                    max(0, footer_index), self._v3_zero_ledger_body, 1)

        self._sync_v3_zero_ledger_card()

        empty_panel = self.clip_editor.empty_state
        empty_icon = empty_panel.findChild(QLabel, "ClipEditorEmptyIcon")
        if empty_icon is not None:
            empty_icon.setText("")
            empty_icon.setPixmap(
                icon("filmstrip-play-accent-20.svg").pixmap(QSize(25, 25)))
        for label in empty_panel.findChildren(QLabel):
            if label.text() == "No clip selected":
                label.setText("No play selected")
            elif label.text().startswith("Mark a play on the timeline"):
                label.setText(
                    "Choose a play from the ledger after detection,\n"
                    "or create one directly from the player.")

    def _install_v3_vertical_rhythm(self) -> None:
        """Reserve the measured timeline-label and lower action bands once."""
        player_layout = self.player.layout()
        if player_layout is None or hasattr(self, "_v3_tag_map_footer"):
            return
        self.control_center.setStyleSheet(
            self.control_center.styleSheet()
            + V3_REVIEW_CONTROL_CENTER_MATERIAL)
        self._v3_transport_surface = V3TransportSurface(self.control_center)
        self._v3_jog_console = V3JogConsole(self.control_center)
        for rule in (
                self.player._region_rule_top,
                self.player._region_rule_bottom):
            rule.setStyleSheet("background:#030503;border:none;")
        self._v3_timeline_label_band = QWidget(self.player)
        self._v3_timeline_label_band.setObjectName("V3TimelineLabelBand")
        self._v3_timeline_label_band.setFixedHeight(0)
        self._v3_timeline_label_band.setStyleSheet(self.control_center.styleSheet())
        self._v3_timeline_readout_row = QHBoxLayout(self._v3_timeline_label_band)
        self._v3_timeline_readout_row.setContentsMargins(10, 0, 10, 0)
        self._v3_timeline_readout_row.setSpacing(8)
        self._v3_timeline_readout_row.addStretch(1)

        deck = self.control_center
        self._v3_timeline_zoom_cluster = QFrame(deck)
        self._v3_timeline_zoom_cluster.setObjectName("V3TimelineZoomCluster")
        zoom_row = QHBoxLayout(self._v3_timeline_zoom_cluster)
        zoom_row.setContentsMargins(0, 0, 0, 0)
        zoom_row.setSpacing(0)
        for control in (self.player.timeline_zoom_out, self.player.timeline_zoom_label,
                        self.player.timeline_zoom_in):
            reparent_widget(control, self._v3_timeline_zoom_cluster)
            zoom_row.addWidget(control)
            control.show()
        self.player.timeline_zoom_label.setObjectName("V3TimelineZoomValue")
        for control, asset in ((self.player.timeline_zoom_out, "subtract-24.svg"),
                (self.player.timeline_zoom_in, "add-24.svg"),
                (self.player.timeline_fit_play, "fit-play-20.svg"),
                (self.player.predicted_snap_button, "find-snap-football-24.svg")):
            control.setIcon(tinted_icon(asset, size=16))
            control.setIconSize(QSize(16, 16))
            control.setProperty("iconAsset", asset)
        self.player.timeline_fit_play.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        # Equal expanding flanks keep Play at the video midpoint regardless
        # of the different widths of the trim controls and utility controls.
        while deck._top_row.count():
            item = deck._top_row.takeAt(0)
            if item.widget():
                item.widget().hide()
        self._v3_transport_flanks = []
        for controls in (
                (self.player.timeline_fit_play, deck.in_button, deck.out_button,
                 self._v3_timeline_zoom_cluster),
                (self.player.predicted_snap_button, deck.overflow_button)):
            flank = QWidget(deck._top_row_host)
            flank.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            layout = QHBoxLayout(flank)
            layout.setContentsMargins(0, 0, 0, 0)
            for control in controls:
                reparent_widget(control, flank)
                layout.addWidget(control)
                control.show()
            self._v3_transport_flanks.append(flank)
        left, right = self._v3_transport_flanks
        left.layout().addStretch(1)
        right.layout().addStretch(1)
        deck._top_row.addWidget(left, 1)
        deck._top_row.addWidget(deck.transport_zone)
        deck._top_row.addWidget(right, 1)
        deck.transport_zone.show()
        deck.overflow_button.setText("Tools ▾")
        deck.overflow_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        deck.overflow_button.setIcon(tinted_icon("tool-24.svg", size=16))
        deck.overflow_button.setIconSize(QSize(16, 16))
        deck.overflow_button.setAccessibleName("Playback and Tag Map tools")
        deck.overflow_button.setToolTip("Playback and Tag Map tools")
        self._install_v3_tools_menu()
        from tapesift.ui_v3.tag_map_field import TagMapField
        self._v3_play_type_key = QLabel(deck)
        self._v3_play_type_key.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self._v3_play_type_key.setText("  ".join(
            f'<span style="color:{color}">■</span> {label}'
            for label, color in (("Run", TagMapField.play_colors["run"]),
                ("Pass", TagMapField.play_colors["pass"]),
                ("RPO", TagMapField.play_colors["rpo"]), ("Unclassified", "#677078"))))
        self._v3_play_type_key.setStyleSheet("color:#d6dce0; background:transparent; border:0; font:10px 'Segoe UI';")
        self._v3_play_type_key.setToolTip("Clip colors match play type")
        right.layout().addWidget(self._v3_play_type_key)
        deck.inline_viewport_group = self._v3_timeline_zoom_cluster
        # Replace only V3's layout budget; the shared resize/show paths now
        # call this same callback instead of competing with a second writer.
        deck = self.control_center
        deck.resized.disconnect(deck._update_responsive_state)
        deck._update_responsive_state = self._layout_v3_transport
        deck.resized.connect(deck._update_responsive_state)
        # Shared polish runs on show and can restore V2 sizes afterwards.
        # Reapply only this V3 instance's geometry after that existing owner.
        inherited_geometry = deck._apply_premium_geometry
        inherited_hit_geometry = deck._apply_machined_hit_geometry
        def titanium_hit_geometry():
            inherited_hit_geometry()
            size, gap = (24, 4) if deck.width() < 900 else (30, 6)
            deck.ISLAND_W, deck.ISLAND_H = size*5 + gap*4, size
            deck.PLAY_SIZE = size
            deck.transport_island.setFixedSize(deck.ISLAND_W, size)
            deck.transport_zone.setFixedSize(deck.ISLAND_W, size)
            for index, button in enumerate(self._v3_transport_surface.buttons):
                button.setFixedSize(size, size)
                button.move(index*(size+gap), 0)
        deck._apply_machined_hit_geometry = titanium_hit_geometry
        def titanium_geometry():
            inherited_geometry()
            self._layout_v3_transport(deck.width())
        deck._apply_premium_geometry = titanium_geometry
        titanium_geometry()
        QTimer.singleShot(0, deck, titanium_geometry)
        self.control_center._update_responsive_state(self.control_center.width())
        strip_index = player_layout.indexOf(self.player.control_strip_slot)
        player_layout.insertWidget(
            max(0, strip_index), self._v3_timeline_label_band)

        self._v3_tag_map_footer = QWidget(self.player)
        self._v3_tag_map_footer.setObjectName("V3TagMapFooter")
        self._v3_tag_map_footer.setFixedHeight(0)
        self._v3_tag_map_footer.hide()
        player_layout.addWidget(self._v3_tag_map_footer)
        self.player.timeline_viewport_scroll.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        player = getattr(self, "player", None)
        if player is not None and watched is player.attribute_grid:
            grid = player.attribute_grid
            kind = event.type()
            if kind in {QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick} and event.button() == Qt.MouseButton.LeftButton:
                point = event.position()
                row, clip = grid.row_at(point.y()), grid.clip_at(point.x(), point.y())
                if row is not None and clip is not None and point.x() >= grid.plot_left():
                    editor = self.clip_editor
                    if editor.save_state_label.property("state") == "dirty":
                        if self.session.read_only or not editor._apply():
                            return True
                    if self.select_clip(clip.id, focus_player=False):
                        self._focus_map_row(row.key)
                        if kind == QEvent.Type.MouseButtonDblClick:
                            self._grid_cell_edit_requested(clip.id, row.key)
                    return True
            if kind in {QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress}:
                key = event.key()
                keys = {Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_W, Qt.Key.Key_B,
                        Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
                        Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape}
                if key in keys and event.modifiers() == Qt.KeyboardModifier.NoModifier:
                    event.accept()
                    if kind == QEvent.Type.KeyPress:
                        if key in {Qt.Key.Key_J, Qt.Key.Key_K, Qt.Key.Key_Up, Qt.Key.Key_Down}:
                            grid._move_cursor(1 if key in {Qt.Key.Key_J, Qt.Key.Key_Down} else -1, 0)
                            if grid.cursor_cell():
                                self._v3_map_row_key = grid.cursor_cell()[0].key
                        elif key in {Qt.Key.Key_W, Qt.Key.Key_B, Qt.Key.Key_Left, Qt.Key.Key_Right}:
                            self._keyboard_clip_step(1 if key in {Qt.Key.Key_W, Qt.Key.Key_Right} else -1)
                        elif key == Qt.Key.Key_Escape:
                            self._return_focus_to_playback()
                        else:
                            if grid.cursor_cell() is None:
                                grid._move_cursor(0, 0)
                            cell = grid.cursor_cell()
                            if cell:
                                self._grid_cell_edit_requested(cell[1].id, cell[0].key)
                    return True
            if kind in {QEvent.Type.FocusIn, QEvent.Type.FocusOut}:
                QTimer.singleShot(0, self, self._sync_map_keyboard_hint)
        if player is not None and event.type() == QEvent.Type.Resize and watched in (
                player.attribute_grid, player.timeline_header):
            self._position_map_controls()
        scroll = getattr(
            getattr(self, "player", None), "timeline_viewport_scroll", None)
        if watched is scroll and event.type() in {
                QEvent.Type.Show, QEvent.Type.Hide}:
            QTimer.singleShot(
                0, self, self._apply_v3_review_geometry_if_alive)
        return super().eventFilter(watched, event)

    def _sync_map_keyboard_hint(self) -> None:
        self._sync_map_shuttle_shortcuts()
        if self._v3_review is None or self._workspace_stage != "review":
            return
        label = self._v3_review.footer.shortcut_label
        grid = self.player.attribute_grid
        if grid.hasFocus() or grid.property("mapEditorOpen"):
            label.setText("Map edit · J/K rows · Enter edit · W next / B previous · Esc playback")
        else:
            label.setText("B/W clips · Shift+E map · E details · Esc playback · ? shortcuts")

    def _apply_v3_review_geometry_if_alive(self) -> None:
        """Ignore a queued presentation pass after the native window closes."""
        if not shiboken6.isValid(self):
            return
        player = getattr(self, "player", None)
        if player is None or not shiboken6.isValid(player):
            return
        self._apply_v3_review_geometry()

    def _install_v3_tools_menu(self) -> None:
        deck = self.control_center
        # Keep original QActions and their signal routes, including parked tools.
        legacy = deck.overflow_button.menu()
        self._v3_legacy_transport_menu = legacy
        menu = QMenu(deck.overflow_button)
        menu.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        menu.setStyleSheet("QMenu {background:#1b232b; color:#e4e8eb; border:1px solid #394650; "
                          "padding:6px; font:13px 'Segoe UI';} QMenu::item {padding:9px 28px 9px 12px;} "
                          "QMenu::item:selected {background:#303d48;} QMenu::separator {height:1px; "
                          "background:#46515b; margin:6px 10px;}")
        original = {action.text(): action for action in legacy.actions() if not action.isSeparator()}
        for title in ("Show jog wheel", "Playback speed"):
            action = original.pop(title)
            action.setIcon(tinted_icon("jog-wheel-20.svg" if title == "Show jog wheel" else "gauge-24.svg"))
            menu.addAction(action)
        tag_options = menu.addMenu("Tag Map options")
        tag_options.setIcon(tinted_icon("table-20.svg"))
        tag_options.aboutToShow.connect(lambda: self._populate_map_menu(tag_options))
        more = menu.addMenu("More playback tools")
        for action in original.values():
            more.addAction(action)
        menu.addSeparator()
        collapse = menu.addAction("Collapse Tag Map")
        collapse.setIcon(tinted_icon("arrow-up-24.svg"))
        collapse.triggered.connect(self.player.tag_map_collapse_button.click)
        def sync():
            collapse.setText("Expand Tag Map" if self.player._tag_map_effectively_collapsed()
                             else "Collapse Tag Map")
            collapse.setEnabled(self.player.tag_map_collapse_button.isEnabled())
        menu.aboutToShow.connect(sync)
        deck.overflow_button.setMenu(menu)
        self._v3_tools_menu = menu

    def _layout_v3_transport(self, width: int) -> None:
        """A compact row of the same bound controls at either window size."""
        deck = self.control_center
        compact = width < 900
        deck._apply_machined_hit_geometry()
        deck._top_row.setContentsMargins(4, 0, 4, 0)
        deck._top_row.setSpacing(2 if compact else 6)
        for flank in self._v3_transport_flanks:
            flank.layout().setSpacing(2 if compact else 6)
        for widget in (deck.position_zone, deck.voiceover_zone, deck.marks_group,
                deck.current_label, deck.position_detail, deck.inout_readout,
                deck.rate_group, deck._rate_divider, deck.export_style_zone,
                deck.jog_toggle, self._v3_tag_map_tools):
            widget.hide()
        self._v3_timeline_label_band.setFixedHeight(0)
        for widget, w in ((self.player.timeline_fit_play, 60 if compact else 70),
                (deck.in_button, 32 if compact else 40), (deck.out_button, 34 if compact else 44),
                (self._v3_timeline_zoom_cluster, 68 if compact else 80),
                (self.player.timeline_zoom_out, 20 if compact else 24),
                (self.player.timeline_zoom_label, 28 if compact else 32),
                (self.player.timeline_zoom_in, 20 if compact else 24),
                (self.player.predicted_snap_button, 70 if compact else 90),
                (deck.overflow_button, 58 if compact else 78)):
            widget.ensurePolished()
            widget.setFixedSize(w, 24 if compact else 28)
            widget.show()
        self._v3_play_type_key.setStyleSheet("color:#d6dce0; background:transparent; border:0; "
                                            f"font:{9 if compact else 11}px 'Segoe UI';")
        # In the smaller window the full key does not fit beside a truly
        # centered transport. Its existing tooltip preserves the color guide.
        room = (width - deck.transport_zone.width()) // 2 - 12
        needed = self.player.predicted_snap_button.width() + deck.overflow_button.width() + self._v3_play_type_key.sizeHint().width() + 12
        self._v3_play_type_key.setVisible(room >= needed)
        deck.overflow_button.setToolTip("Playback and Tag Map tools · Clip colors: Run green, Pass blue, RPO teal, Unclassified gray")
        deck._top_row_host.setFixedHeight(34)
        deck.setFixedHeight(36)
        deck._top_row.invalidate()
        deck._top_row.activate()

    def _apply_v3_review_geometry(self) -> None:
        """Apply the locked vertical rhythm to the existing engine widgets."""
        player = getattr(self, "player", None)
        control_center = getattr(self, "control_center", None)
        if player is None or control_center is None:
            return
        # These are presentation constraints only.  The authoritative
        # timeline, transport, JKL controller and AttributeGrid objects are
        # preserved; V3 simply gives them the measured slots from the lock.
        self.proxy_banner_widget.setFixedHeight(33)
        banner_layout = self.proxy_banner_widget.layout()
        if (banner_layout is not None
                and not getattr(self, "_v3_proxy_layout_aligned", False)
                and banner_layout.count() >= 4):
            # The inherited banner ends with an expanding spacer, which leaves
            # both actions stranded beside the warning.  The locked Review
            # bar keeps the warning at the left and the actions at the right.
            banner_layout.takeAt(banner_layout.count() - 1)
            banner_layout.insertStretch(1, 1)
            self._v3_proxy_layout_aligned = True
        control_center._top_row_host.setFixedHeight(34)
        control_center.setFixedHeight(36)
        review = getattr(self, "_v3_review", None)
        if review is not None:
            review.export_page.setFixedHeight(max(230, min(428, self.height() - 360)))
        # The expanded scrollable Tag Map needs its ruler and legend even on
        # short screens. Let film yield height instead of overlapping the deck.
        player.video_widget.setMinimumHeight(120 if self.height() < 720 else 240)
        self.player.timeline_header.setFixedHeight(0)
        # Give the existing lanes the pagebook's lower workspace at full
        # height, then return space to film on short high-DPI screens.
        navigator_visible = not self.player.timeline_viewport_scroll.isHidden()
        grid_height = max(140, min(344, self.height() - 456))
        self.player.attribute_grid._review_height_limit = grid_height if navigator_visible else grid_height + 10
        self.player.attribute_grid._apply_height()
        self.player.attribute_grid._layout_review_controls()
        footer = getattr(self, "_v3_tag_map_footer", None)
        if footer is not None:
            footer.setFixedHeight(0)
            footer.hide()

    def _refresh_clip_list(self) -> None:
        super()._refresh_clip_list()
        if getattr(self, "clip_list", None) is not None:
            table = self.clip_list.table
            table.verticalHeader().setDefaultSectionSize(40)
            table.setColumnWidth(COL_STATUS, 76)
            for row in range(table.rowCount()):
                table.setRowHeight(row, 40)
            self._decorate_v3_ledger_rows()
        self._sync_v3_zero_ledger_card()
        self._sync_v3_review_state()
        self._apply_v3_review_geometry()
        review = getattr(self, "_v3_review", None)
        if review is not None and review.export_page.isVisible() and self._v3_export_can_start():
            self._refresh_v3_export_preview()

    def _sync_v3_zero_ledger_card(self) -> None:
        """Mirror the authoritative empty-state visibility onto its V3 card."""
        body = getattr(self, "_v3_zero_ledger_body", None)
        empty = getattr(getattr(self, "clip_list", None), "empty_state", None)
        if body is None or empty is None:
            return
        show = not empty.isHidden()
        body.setVisible(show)
        empty.setVisible(show)

    def _decorate_v3_ledger_rows(self) -> None:
        # Native painting leaves every item's status/selection semantics intact.
        # Metadata edits reach this path; trim previews already repaint the title.
        delegate = getattr(self, "_v3_ledger_delegate", None)
        if delegate is not None:
            delegate.sync_accessibility()
        self.clip_list.table.viewport().update()

    def _clip_edited(self, clip_id: str) -> None:
        project = self.session.project if self.session else None
        previous = dict(project.logging_defaults) if project else {}
        pending = getattr(self.clip_editor, "pending_logging_defaults", None)
        previous_play = None
        previous_state = None
        if project is not None and pending is not None:
            project.logging_defaults = dict(pending)
        try:
            drive_action = getattr(self.clip_editor, "_pending_drive_action", None)
            clip = self.session.get_clip(clip_id) if self.session else None
            if drive_action and clip:
                mode, previous_id, gain = drive_action
                previous_play = drive_suggestions.previous_clip(self.session.clips, clip)
                if previous_play is None or previous_play.id != previous_id:
                    raise ValueError("The previous play changed. Review the drive suggestion again.")
                previous_state = deepcopy(previous_play.__dict__)
                if mode == "apply" and gain is not None:
                    suggestion = drive_suggestions.suggest(previous_play, clip.details)
                    if suggestion.previous_gain != gain:
                        raise ValueError("The previous play changed. Its saved Gain was preserved.")
                    previous_play.details.update({"yards": str(gain), "yards_inferred_from": clip.id,
                                                  "yards_inferred_value": str(gain)})
                elif mode == "break" and previous_play.details.get("yards_inferred_from") == clip.id:
                    if previous_play.details.get("yards") == previous_play.details.get("yards_inferred_value"):
                        previous_play.details.pop("yards", None)
                    previous_play.details.pop("yards_inferred_from", None)
                    previous_play.details.pop("yards_inferred_value", None)
                detail_service.refresh_generated_title(previous_play)
                previous_play.touch()
            super()._clip_edited(clip_id)
        except Exception as exc:
            if previous_play is not None and previous_state is not None:
                previous_play.__dict__.update(previous_state)
            if project is not None:
                project.logging_defaults = previous
            self.clip_editor._save_commit_error = str(exc) or type(exc).__name__
            log.exception("Could not save clip details")
            return
        if previous_play is not None:
            self.clip_list.update_row(previous_play, project.source_duration_ms, project.naming_template,
                                      project.name, self.settings.separator_style)
            self._index_current_project()
        self._decorate_v3_ledger_rows()
        clip = self.session.get_clip(clip_id) if self.session else None
        self._update_play_field_ribbon(clip.details if clip else {})

    def _build_window_menu(self) -> None:
        self.window_menu = QMenu("&Window", self)
        before = next(
            (
                action for action in self.menuBar().actions()
                if action.text().replace("&", "") == "How TapeSift Works"
            ),
            None,
        )
        if before is None:
            self.menuBar().addMenu(self.window_menu)
        else:
            self.menuBar().insertMenu(before, self.window_menu)

        self.ledger_rail_action = QAction("Clip Ledger", self)
        self.ledger_rail_action.setCheckable(True)
        self.ledger_rail_action.setShortcut(QKeySequence("Ctrl+Shift+B"))
        self.ledger_rail_action.toggled.connect(self._set_ledger_open)
        self.window_menu.addAction(self.ledger_rail_action)

        self.details_rail_action = self.details_action
        for action in self.menuBar().actions():
            if action.menu() is not None:
                action.menu().removeAction(self.details_action)
        self.details_action.setText("Clip Details")
        self.details_action.setShortcuts([QKeySequence("Ctrl+B"), QKeySequence("Ctrl+I")])
        self.window_menu.addAction(self.details_rail_action)
        self.window_menu.addSeparator()
        self.reset_workspace_action = QAction("Reset Workspace", self)
        self.reset_workspace_action.triggered.connect(self._reset_workspace)
        self.window_menu.addAction(self.reset_workspace_action)
        self._dock_toggle_actions = {}
        self.float_player_action = None
        self.fullscreen_player_action = None

    def _rewire_details_shortcut(self) -> None:
        # Window > Clip Details owns both aliases. A second Qt registration
        # makes Ctrl+B ambiguous before either callback can execute.
        shortcut = self._transport_shortcuts.pop("Ctrl+B", None)
        if shortcut is not None:
            shortcut.setEnabled(False)
            shortcut.setKey(QKeySequence())
            shortcut.deleteLater()

    def _tidy_v3_menus(self) -> None:
        menus = {self._action_text(a): a.menu() for a in self.menuBar().actions() if a.menu()}
        file_menu, playback = menus["File"], menus["Playback"]
        first = file_menu.actions()[0]
        for title, slot in (("New Project…", self.start_screen._new_project),
                            ("Open Existing…", self.start_screen._open_project)):
            action = QAction(title, self)
            action.triggered.connect(slot)
            file_menu.insertAction(first, action)
        file_menu.insertSeparator(first)
        self._v3_menu_actions = {
            self._action_text(a): a
            for menu in menus.values() for a in menu.actions() if not a.isSeparator()
        }
        diagnostics = QMenu("Detection diagnostics", self)
        for menu in (file_menu, playback):
            for action in list(menu.actions()):
                if "Autodetect" in action.text():
                    menu.removeAction(action)
                    diagnostics.addAction(action)
        playback.addMenu(diagnostics)
        self._v3_detection_diagnostics = diagnostics
        source = QMenu("Video source", self)
        file_menu.removeAction(self._load_video_action)
        source.addAction(self._load_video_action)
        file_menu.addMenu(source)
        self._v3_source_menu = source
        exit_action = self._v3_menu_actions["Exit"]
        file_menu.removeAction(exit_action)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)
        # Moving existing actions can leave adjacent or trailing separators.
        for menu in menus.values():
            previous_separator = True
            for action in list(menu.actions()):
                if action.isSeparator() and previous_separator:
                    menu.removeAction(action)
                else:
                    previous_separator = action.isSeparator()
            if menu.actions() and menu.actions()[-1].isSeparator():
                menu.removeAction(menu.actions()[-1])
            menu.aboutToShow.connect(self._sync_v3_menu_availability)
        self._sync_v3_menu_availability()

    def _sync_v3_menu_availability(self) -> None:
        actions = getattr(self, "_v3_menu_actions", None)
        if actions is None:
            return
        session = self.session
        editing = bool(session and not session.read_only)
        active = self.stack.currentWidget() is self.workspace
        for name in ("Save Project", "Rename Project", "Project Settings",
                     "Sort Clips by Start Time", "Find Duplicate Clips"):
            actions[name].setEnabled(editing)
        for name in ("Undo", "Redo"):
            actions[name].setEnabled(editing and active)
        actions["Close Project"].setEnabled(session is not None)
        self._v3_source_menu.setEnabled(editing)
        self._load_video_action.setEnabled(editing)
        self.export_heatmap_action.setEnabled(session is not None)
        self.details_action.setEnabled(session is not None and active)
        self.ledger_rail_action.setEnabled(session is not None and active)
        source_ready = bool(session and session.project.has_source)
        self.review_action.setEnabled(source_ready and active)
        self.review_coverage_action.setEnabled(editing and source_ready)
        self._v3_detection_diagnostics.setEnabled(editing and source_ready)
        for name in ("Build Smooth-Scrub Preview", "Use Original Video for Preview"):
            actions[name].setEnabled(source_ready and active)

    def _set_ledger_open(self, opened: bool) -> None:
        if self._v3_review is not None:
            self._v3_review.ledger_rail.set_open(bool(opened))

    def _set_details_open(self, opened: bool) -> None:
        if self._v3_review is not None:
            self._v3_review.details_rail.set_open(bool(opened))

    def _toggle_details_panel(self, _checked: bool = False) -> None:
        if self._v3_review is None:
            return
        self._set_details_open(
            not self._v3_review.details_rail.is_open())

    def _inspector_folded(self, collapsed: bool) -> None:
        if self._v3_review is not None:
            self._set_details_open(not collapsed)

    def _v3_rail_state_changed(
            self, ledger_open: bool, details_open: bool) -> None:
        self._apply_v3_review_geometry()
        self._sync_v3_rail_actions()
        if self._workspace_restored:
            self._v3_state_store.save(
                ReviewRailState(ledger_open, details_open),
                self.saveGeometry(),
            )

    def _sync_v3_rail_actions(self) -> None:
        if self._v3_review is None:
            return
        state = self._v3_review.rail_state()
        for action, checked in (
                (getattr(self, "ledger_rail_action", None),
                 state.ledger_open),
                (getattr(self, "details_rail_action", None),
                 state.details_open),
                (getattr(self, "details_action", None),
                 state.details_open)):
            if action is None:
                continue
            action.blockSignals(True)
            action.setChecked(checked)
            action.blockSignals(False)

    def _sync_v3_ledger_summary(self) -> None:
        self.clip_editor.set_game_teams(
            self.session.project.game_team_ids if self.session else [])
        if self._v3_review is None:
            return
        count = len(self.session.clips) if self.session is not None else 0
        selected = "--"
        ids = self.clip_list.selected_clip_ids()
        if self.session is not None and ids:
            for index, clip in enumerate(self.session.clips, start=1):
                if clip.id == ids[0]:
                    selected = f"{index:02d}"
                    break
        logged = sum(
            1 for clip in self.session.clips
            if clip.enabled and bool(clip.details)
        ) if self.session is not None else 0
        self._v3_review.set_ledger_summary(selected, count, logged)
        project_name = getattr(self, "_v3_ledger_project_name", None)
        project_state = getattr(self, "_v3_ledger_project_state", None)
        if project_name is not None:
            project_name.setText(
                self.session.project.name if self.session is not None
                else "NO PROJECT OPEN")
        if project_state is not None:
            project_state.setText(
                DETECTOR_VERSION if self.session is not None else "")
        self.clip_list.progress_label.setText(
            f"{count} plays · {logged} logged")
        self.clip_list.progress_label.show()
        selected_clip = (
            self.session.get_clip(ids[0])
            if self.session is not None and ids else None
        )
        self.clip_list.filter_count.setText(
            f"{selected} / {count}" if selected_clip is not None else "")
        group = getattr(self, "_v3_ledger_group_header", None)
        if group is not None:
            group.hide()  # The table already owns the real, collapsible group heading.
            self._v3_ledger_group_count.setText(
                f"{count} {'play' if count == 1 else 'plays'}")
            self._v3_ledger_group_logged.setText(f"{logged} logged")
        subtitle = getattr(self, "_v3_details_subtitle", None)
        chip = getattr(self, "_v3_play_call_chip", None)
        if subtitle is not None:
            subtitle.setText(
                f"Play {selected} of {count}"
                if selected_clip is not None else "No play selected")
        if chip is not None:
            call = str(selected_clip.details.get("run_pass", "")).strip() \
                if selected_clip is not None else ""
            chip.setText(call)
            chip.setVisible(bool(call))
        self.clip_editor.players_box.setTitle("People")
        self.clip_editor.notes_box.setTitle("Notes")
        if selected_clip is not None:
            duration_s = selected_clip.duration_ms / 1000
            self.clip_editor.range_summary_label.setText(
                f"{format_ms(selected_clip.start_ms, show_millis=True)}  →  "
                f"{format_ms(selected_clip.end_ms, show_millis=True)}  ·  "
                f"{duration_s:.1f} sec")
            self.clip_editor.range_summary_label.show()
        self.clip_editor.save_state_label.hide()
        self.clip_editor.edit_title_btn.hide()
        action_state = getattr(self, "_v3_action_state", None)
        if action_state is not None:
            action_state.setText(self.clip_editor.save_state_label.text().title())
        review_badge = getattr(self, "_v3_review_status_badge", None)
        review_icon = getattr(self, "_v3_review_status_icon", None)
        if review_badge is not None:
            state = self.clip_editor.review_state_label.text().strip()
            review_badge.setVisible(bool(state))
            if review_icon is not None:
                review_icon.setVisible(state.upper() == "LOGGED")

    def _sync_v3_review_state(self) -> None:
        if self._v3_review is None:
            return
        zero_mode = self.session is not None and not self.session.clips
        self._v3_review.set_zero_mode(zero_mode)
        self._sync_v3_ledger_summary()

    def _sync_review_masthead(self) -> None:
        super()._sync_review_masthead()
        self._sync_v3_ledger_summary()

    # ---------- V3 workspace state, stage and lifecycle ----------

    def _workspace_page_changed(self, _index: int) -> None:
        self._sync_v3_menu_availability()
        page = self.stack.currentWidget()
        if page is self.workspace:
            self._ensure_v3_review()
        self._sync_v3_page_chrome()
        active = page is self.workspace
        heatmap = page is getattr(self, "_v3_heatmap", None)
        # Application shortcuts for the tag tray otherwise remain live behind
        # this read-only export page. Restore each object's previous state.
        if heatmap and not self._v3_heatmap_tag_locks:
            for shortcut in (*getattr(self, "_quick_tag_shortcuts", ()),
                             *getattr(self, "_label_shortcuts", ())):
                self._v3_heatmap_tag_locks.append((shortcut, shortcut.isEnabled()))
                shortcut.setEnabled(False)
        elif not heatmap and getattr(self, "_v3_heatmap_tag_locks", None):
            for shortcut, enabled in self._v3_heatmap_tag_locks:
                shortcut.setEnabled(enabled and not self._v3_read_only_recovery)
            self._v3_heatmap_tag_locks.clear()
        # Review can fit a 150% desktop without placing its footer below the
        # work area. The existing scrollable panels keep their content.
        if active or heatmap:
            self.setMinimumSize(1248, 608)
        elif page is self.library_screen:
            available = self.screen().availableGeometry()
            self.setMinimumSize(1120, min(720, max(560, available.height() - 64)))
        else:
            self.setMinimumSize(1120, 640)
        # Every locked V3 page owns its footer/actions. The inherited playback
        # status row is duplicate chrome on Home and Library.
        self.statusBar().setVisible(False)
        shell = getattr(self, "_centered_menu_shell", None)
        if shell is not None:
            shell.set_mode(
                "review" if active
                else "home" if page is self.start_screen
                else "library" if page is self.library_screen
                else "export" if heatmap
                else ""
            )
        self.stack.setSizePolicy(
            QSizePolicy.Policy.Ignored
            if active else QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Ignored
            if active else QSizePolicy.Policy.Preferred,
        )
        self.stack.updateGeometry()
        self._apply_workspace_visibility()
        self._sync_v3_rail_actions()
        self._sync_v3_window_bezel()

    def _apply_workspace_visibility(self) -> None:
        if self._v3_review is None:
            return
        active = self.stack.currentWidget() is self.workspace
        self._v3_review.setVisible(active)
        self._v3_review.set_stage(self._workspace_stage)

    def _set_workspace_stage(self, stage: str) -> None:
        if stage not in {"review", "export"}:
            return
        self._workspace_stage = stage
        if getattr(self, "workflow_ribbon", None) is not None:
            self.workflow_ribbon.set_active(stage)
        if self._v3_review is not None:
            self._v3_review.set_stage(stage)
        self._sync_v3_stage_chrome(stage)
        if stage == "export":
            self._sync_v3_export_state()
        self._schedule_workspace_save()

    def _sync_v3_stage_chrome(self, stage: str) -> None:
        """Replace only Tag Map; film, timeline and transport stay mounted."""

        exporting = stage == "export"
        hint = getattr(self, "_shortcut_hint", None)
        if hint is not None:
            if not hasattr(self, "_v3_review_shortcut_hint"):
                self._v3_review_shortcut_hint = hint.text()
            hint.setText(
                "ESC  Back to Review  ·  CTRL+E  Quick Export"
                if exporting else self._v3_review_shortcut_hint)
        footer = getattr(self._v3_review, "footer", None)
        review_hint = getattr(footer, "shortcut_label", None)
        if review_hint is not None:
            if not hasattr(self, "_v3_review_footer_shortcuts"):
                self._v3_review_footer_shortcuts = review_hint.text()
            review_hint.setText(
                "ESC  Back to Review  ·  CTRL+E  Quick Export"
                if exporting else self._v3_review_footer_shortcuts)
        self.player.quick_tag_slot.setVisible(
            not exporting
            and not self.player._tag_map_effectively_collapsed()
            and bool(self.player._quick_tags_mounted)
        )
        self.player.attribute_grid.setVisible(
            not exporting
            and bool(self.player.attribute_grid.rows())
            and not self.player._tag_map_effectively_collapsed())
        self._position_map_controls()
        if exporting:
            self.player.timeline_viewport_scroll.hide()
        else:
            self.player._sync_timeline_viewport_controls()
        mode = self.findChild(QLabel, "V3ReviewModeLabel")
        if mode is not None:
            route = self._v3_review.export_page.route \
                if exporting else "review"
            mode.setText(
                "PACKAGE" if route == "package"
                else "EXPORT" if exporting else "REVIEW")
        if not exporting:
            self.player._sync_tag_map_presentation()
            self._apply_v3_review_geometry()
            self._sync_map_keyboard_hint()
        self._position_map_controls()

    def _focus_export(self) -> None:
        """The general Export command opens the independent package route."""

        self._configure_package_export()

    def _job_started(self, job_id: str) -> None:
        super()._job_started(job_id)
        self._sync_v3_export_state()

    def _job_completed(self, job_id: str, output_path: str) -> None:
        super()._job_completed(job_id, output_path)
        self._sync_v3_export_state()

    def _job_failed(self, job_id: str, error: str) -> None:
        super()._job_failed(job_id, error)
        self._sync_v3_export_state()

    def _job_cancelled(self, job_id: str) -> None:
        super()._job_cancelled(job_id)
        self._sync_v3_export_state()

    def _export_finished(self, worker=None) -> None:
        super()._export_finished(worker)
        self._sync_v3_export_state()

    def _snapshot_export_finished(self, worker) -> None:
        super()._snapshot_export_finished(worker)
        self._sync_v3_export_state()

    def _release_export_bridge_worker(self, attribute: str, worker) -> None:
        super()._release_export_bridge_worker(attribute, worker)
        self._sync_v3_export_state()

    def _return_from_v3_export(self) -> None:
        """Leave the Export view without touching its queue or workers."""
        self._set_workspace_stage("review")
        self._return_focus_to_playback()

    def _shortcut_escape(self) -> None:
        """Make a non-editing Escape leave Export; retain every other Esc."""
        if self._workspace_stage == "export" \
                and not self._typing_in_text_field():
            self._return_from_v3_export()
            return
        focus = QApplication.focusWidget()
        if focus is not None and self.clip_editor.isAncestorOf(focus):
            self._return_focus_to_playback()
            return
        super()._shortcut_escape()

    def _save_workspace_now(self) -> None:
        if self._v3_review is None:
            return
        self._v3_state_store.save(
            self._v3_review.rail_state(), self.saveGeometry())
        self.settings.timeline_follow_playhead = \
            self.player.timeline_follow_playhead.isChecked()
        self.settings.save()

    def _restore_workspace(self) -> None:
        if self._workspace_restored:
            return
        self._workspace_restored = True
        if self._v3_review is not None:
            self._v3_review.set_rail_state(self._v3_initial_state, emit=False)
        geometry = self._v3_state_store.geometry()
        if geometry is not None:
            self.restoreGeometry(geometry)
        self.player.timeline_follow_playhead.setChecked(
            bool(self.settings.timeline_follow_playhead))
        self._sync_v3_rail_actions()
        self._sync_v3_review_state()

    def _reset_workspace(self, _checked: bool = False) -> None:
        if self._v3_review is None:
            return
        self._workspace_stage = "review"
        self._v3_review.set_stage("review")
        self._v3_review.set_rail_state(ReviewRailState())
        self.player.timeline_follow_playhead.setChecked(True)
        self._save_workspace_now()
        self.statusBar().showMessage("Shell V3 workspace reset", 3000)

    def _resize_default_docks(self) -> None:
        return

    def _recover_missing_monitor_docks(self) -> None:
        return

    def _restore_floating_window_states(self) -> None:
        return

    def _normalize_transient_workspace_window_states(self) -> None:
        return

    def _sync_workspace_central_surface(self) -> None:
        return

    def _sync_workspace_window_actions(self) -> None:
        self._sync_v3_rail_actions()

    def _exit_player_fullscreen(
            self, *, restore_previous: bool = True) -> None:
        return

    def _toggle_player_floating(self, _checked: bool = False) -> None:
        self.statusBar().showMessage(
            "Shell V3 keeps the film in its stable workbench.", 3000)

    def _size_export_stage(self) -> None:
        return

    def _release_export_stage_minimum(self) -> None:
        return

    def live_v3_objects_valid(self) -> bool:
        """Guardian probe for the authoritative widgets after rail loops."""
        objects = (
            self.player, self.control_center,
            getattr(self.control_center, "jog_window", None),
            self.clip_list, self.clip_editor, self.player.attribute_grid,
            self.export_panel,
        )
        return all(
            item is not None and shiboken6.isValid(item)
            for item in objects
        )
