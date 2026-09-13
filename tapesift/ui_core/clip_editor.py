"""Clip detail editor panel: edit title, filename base, times, metadata."""

from __future__ import annotations

import re
from copy import deepcopy

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import (
    QAction, QKeySequence, QRegularExpressionValidator, QShortcut,
)
from PySide6.QtWidgets import (
    QBoxLayout, QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QMenu, QMessageBox, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from tapesift.ui_core.layout_ownership import detach_widget
from tapesift.core.config import (
    AppSettings, INSPECTOR_DENSITY_COMFORTABLE,
    INSPECTOR_DENSITY_COMPACT, INSPECTOR_DENSITY_SPACIOUS,
)
from tapesift.core.exceptions import TapeSiftError
from tapesift.models.clip import Clip
from tapesift.models.export_settings import BUILTIN_PRESETS
from tapesift.services import (
    detail_service, filename_service, result_service, tag_service,
)
from tapesift.services.detail_service import DETAIL_FIELDS
from tapesift.services.football_vocab import (
    DEFAULT_EXPLOSIVE_PASS_YARDS, DEFAULT_EXPLOSIVE_RUSH_YARDS,
    DEFAULT_RESULT_FAVORITES, is_explosive, lookup, parse_yards, short_for, values_for,
)
from tapesift.services.football_context import down_distance, is_successful, parse_down_distance
from tapesift.services.roster_service import strip_jersey_number
from tapesift.services.timestamp_parser import format_ms, parse_range
from tapesift.ui.result_manager_dialog import (
    MAX_RESULT_FAVORITES, STANDARD_RESULT_CHOICES, ResultManagerDialog,
    unique_results,
)
from tapesift.ui_core.collapsible import CollapsibleSection
from tapesift.ui_core.first_read_ui import presentation_for
from tapesift.ui_core.flow_layout import FlowLayout
from tapesift.ui_v2.attribute_row_widget import (
    AttributeRowsPanel, AttributeRowWidget)
from tapesift.ui_core.tag_edit import (
    AddTagCombo, DetailEdit, TagLineEdit, configure_detail_edit,
)

ANALYST_DETAIL_GROUPS = {
    "situation": ("quarter", "down_distance", "ball_on", "yards"),
    "classification": ("play_type", "action", "result"),
    "players": ("player_name", "other_players", "quarterback"),
}

#: Fields answered by a row of chips in the analyst inspector. They keep their
#: text editor - it just moves into More details, so anything the chips do not
#: offer is still typeable. Logging a play should be clicks, not tabbing
#: through text fields, when the same handful of answers come up every time.
CHIP_DRIVEN_FIELDS = ("quarter", "down_distance", "result")

#: Folded width: enough for the handle and nothing else.
COLLAPSED_WIDTH = 26
#: Rajdhani carries no guillemets or arrows - they render as boxes, which
#: is how the chevrons in the timeline ended up drawn as polygons. These
#: two it does have.
COLLAPSE_OPEN_GLYPH = ">"
COLLAPSE_SHUT_GLYPH = "<"

QUARTER_CHOICES = tuple(values_for("quarter"))
DOWN_CHOICES = ("1st", "2nd", "3rd", "4th")
#: Shown until a project has its own vocabulary to offer.
DEFAULT_RESULT_CHOICES = DEFAULT_RESULT_FAVORITES
MAX_RESULT_CHIPS = MAX_RESULT_FAVORITES


class ClipEditor(QWidget):
    result_dialog_class = ResultManagerDialog

    #: Folded or unfolded. The window owns the dock's width, so it has to
    #: be told when the panel wants it back.
    collapse_changed = Signal(bool)
    edit_started = Signal(str)     # clip id, emitted before the model is mutated
    clip_edited = Signal(str)      # clip id
    quick_export_requested = Signal(str)  # clip id
    package_requested = Signal()   # existing package/session ExportPanel
    save_and_advance = Signal()    # Enter in review mode
    return_to_playback = Signal()  # Ctrl+Enter: save and hand focus back

    def __init__(self, settings: AppSettings | None = None, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._clip: Clip | None = None
        self._template = "{clip_number}_{clip_name}"
        self._project_name = ""
        self._separator = "hyphen"
        self._duration_ms = 0
        self._analyst_mode = False
        self._loading_clip = False
        self._automatic_name = False
        self._updating_name = False
        self._remembered_player_names: list[str] = []
        self.setObjectName("ClipEditor")

        outer = QVBoxLayout(self)
        self._outer_layout = outer
        outer.setContentsMargins(0, 0, 0, 0)

        # Empty state - shown instead of a form full of disabled fields.
        # A designed blank panel: it teaches the three marking keys while
        # it waits, instead of spending the space on one muted sentence.
        self.empty_state = self._build_empty_state()
        outer.addWidget(self.empty_state)

        # A strip that stays when everything else folds away. Logging runs
        # in the grid under the timeline now, so during that pass this
        # panel is 300px of dead width - but it still owns the title, the
        # range, the note and export, so it folds rather than closing.
        self.collapse_btn = QToolButton()
        self.collapse_btn.setObjectName("InspectorCollapse")
        self.collapse_btn.setProperty("quiet", "true")
        self.collapse_btn.setCheckable(True)
        self.collapse_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.collapse_btn.setToolTip("Fold Play Details away  (Ctrl+B)")
        self.collapse_btn.setText(COLLAPSE_OPEN_GLYPH)
        self.collapse_btn.toggled.connect(self.set_collapsed)
        collapse_row = QHBoxLayout()
        collapse_row.setContentsMargins(0, 0, 0, 0)
        collapse_row.setSpacing(0)
        collapse_row.addStretch(1)
        collapse_row.addWidget(self.collapse_btn)
        outer.addLayout(collapse_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self.form_area = scroll
        layout = QVBoxLayout(inner)
        self._form_layout = layout
        # The heading gets its own band, mirroring the clip ledger: header
        # on top, form below, separated by a hairline.
        heading_band = QWidget(inner)
        heading_band.setObjectName("InspectorHeader")
        heading_band.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        heading_row = QHBoxLayout(heading_band)
        heading_row.setContentsMargins(0, 0, 0, 0)
        self.heading_label = QLabel("Clip Inspector")
        self.heading_label.setProperty("role", "heading")
        heading_row.addWidget(self.heading_label)
        heading_row.addStretch()
        self.review_state_label = QLabel("")
        self.review_state_label.setObjectName("InspectorReviewState")
        self.review_state_label.setProperty("role", "subtle")
        heading_row.addWidget(self.review_state_label)
        self.save_state_label = QLabel("")
        self.save_state_label.setObjectName("InspectorSaveState")
        self.save_state_label.setProperty("role", "subtle")
        heading_row.addWidget(self.save_state_label)
        self.edit_title_btn = QPushButton("EDIT")
        self.edit_title_btn.setObjectName("InspectorEditTitle")
        self.edit_title_btn.setToolTip(
            "Open More details and edit the clip title")
        self.edit_title_btn.clicked.connect(self._edit_title)
        self.edit_title_btn.hide()
        heading_row.addWidget(self.edit_title_btn)
        layout.addWidget(heading_band)

        self.summary_card = QFrame()
        self.summary_card.setObjectName("InspectorSummaryCard")
        self.summary_card.setFrameShape(QFrame.Shape.StyledPanel)
        self.summary_card.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        summary_layout = QVBoxLayout(self.summary_card)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        summary_layout.setSpacing(0)
        self.play_rail = QWidget()
        self.play_rail.setObjectName("InspectorPlayRail")
        self.play_rail.setFixedHeight(4)
        self.play_rail.setProperty("playKind", "other")
        layout.insertWidget(0, self.play_rail)
        self.play_rail.hide()
        summary_body = QWidget()
        summary_body_layout = QHBoxLayout(summary_body)
        summary_body_layout.setContentsMargins(0, 0, 0, 0)
        summary_body_layout.setSpacing(0)
        summary_copy = QWidget()
        summary_copy_layout = QVBoxLayout(summary_copy)
        summary_copy_layout.setContentsMargins(10, 8, 8, 8)
        summary_copy_layout.setSpacing(2)
        self.title_summary_label = QLabel("Untitled play")
        self.title_summary_label.setObjectName("InspectorTitleSummary")
        self.title_summary_label.setWordWrap(True)
        summary_copy_layout.addWidget(self.title_summary_label)
        self.situation_summary_label = QLabel("")
        self.situation_summary_label.setObjectName(
            "InspectorSituationSummary")
        self.situation_summary_label.setWordWrap(True)
        summary_copy_layout.addWidget(self.situation_summary_label)
        self.range_summary_label = QLabel("")
        self.range_summary_label.setObjectName("InspectorRangeSummary")
        self.range_summary_label.setWordWrap(True)
        summary_copy_layout.addWidget(self.range_summary_label)
        summary_body_layout.addWidget(summary_copy, 1)
        summary_layout.addWidget(summary_body)
        self.summary_card.hide()
        layout.addWidget(self.summary_card)

        # Machine analysis stays beside the film as a read-only advisory.
        # It never occupies, fills, or disables the analyst's Run / Pass
        # control.  The only accepting action remains pressing R or P.
        self.first_read_card = QFrame()
        self.first_read_card.setObjectName("FirstReadAdvisory")
        self.first_read_card.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True)
        first_read_layout = QVBoxLayout(self.first_read_card)
        first_read_layout.setContentsMargins(10, 8, 10, 8)
        first_read_layout.setSpacing(4)
        first_read_heading = QHBoxLayout()
        first_read_heading.setContentsMargins(0, 0, 0, 0)
        self.first_read_title = QLabel("FIRST READ")
        self.first_read_title.setObjectName("FirstReadTitle")
        first_read_heading.addWidget(self.first_read_title)
        first_read_heading.addStretch(1)
        self.first_read_chip = QLabel("")
        self.first_read_chip.setObjectName("FirstReadSuggestionChip")
        self.first_read_chip.setAccessibleName("First Read suggestion")
        first_read_heading.addWidget(self.first_read_chip)
        first_read_layout.addLayout(first_read_heading)
        self.first_read_message = QLabel("")
        self.first_read_message.setObjectName("FirstReadMessage")
        self.first_read_message.setWordWrap(True)
        first_read_layout.addWidget(self.first_read_message)
        self.first_read_card.hide()
        layout.addWidget(self.first_read_card)

        self.primary_container = QWidget()
        self.primary_container.setObjectName("InspectorPrimaryFields")
        form = QFormLayout(self.primary_container)
        self._primary_form = form
        self.title_edit = QLineEdit()
        self.title_edit.setToolTip("Human-readable clip name shown in the list")
        self.filename_edit = QLineEdit()
        self.filename_edit.setToolTip(
            "File-safe name used for export. Leave empty to derive it from the title.")
        self.start_edit = QLineEdit()
        self.end_edit = QLineEdit()
        self.label_edit = QLineEdit()
        self.tags_edit = TagLineEdit()
        self.tags_edit.setToolTip("Comma-separated tags - autocompletes from "
                                  "tags already used in this project")
        self.add_tag_combo = AddTagCombo(self.tags_edit)
        self.add_tag_combo.hide()  # shown in fixed-dropdown mode
        tags_row = QWidget()
        tags_layout = QHBoxLayout(tags_row)
        tags_layout.setContentsMargins(0, 0, 0, 0)
        tags_layout.addWidget(self.tags_edit, 1)
        tags_layout.addWidget(self.add_tag_combo)
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(64)
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("(project default)", "")
        for preset in BUILTIN_PRESETS.values():
            self.preset_combo.addItem(preset.display_name, preset.name)
        self.reel_check = QCheckBox("Include in combined reel")
        self.enabled_check = QCheckBox("Enabled for export")

        # Primary fields only - the things you change on most clips.
        form.addRow("Title:", self.title_edit)
        # Keep the two timestamps on one line. They are reviewed together,
        # and this gives Play Details more room for the football fields.
        self.time_row = QWidget()
        self.time_row.setObjectName("InspectorRangeRow")
        time_layout = QHBoxLayout(self.time_row)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(6)
        start_label = QLabel("Start")
        end_label = QLabel("End")
        self._start_caption = start_label
        self._end_caption = end_label
        start_label.setBuddy(self.start_edit)
        end_label.setBuddy(self.end_edit)
        self.start_edit.setAccessibleName("Clip start time")
        self.end_edit.setAccessibleName("Clip end time")
        time_layout.addWidget(start_label)
        time_layout.addWidget(self.start_edit, 1)
        time_layout.addWidget(end_label)
        time_layout.addWidget(self.end_edit, 1)
        form.addRow("Range:", self.time_row)
        self._title_form_label = form.labelForField(self.title_edit)
        self._range_form_label = form.labelForField(self.time_row)
        self.range_duration_label = QLabel("")
        self.range_duration_label.setObjectName("InspectorRangeDuration")
        self.range_duration_label.setProperty("role", "subtle")
        time_layout.addWidget(self.range_duration_label)
        layout.addWidget(self.primary_container)

        self.essentials_box = QGroupBox("GAME SITUATION")
        self.essentials_box.setObjectName("ClipEditorEssentials")
        self._essentials_grid = QGridLayout(self.essentials_box)
        self.essentials_box.hide()

        self.classification_box = QGroupBox("CLASSIFICATION")
        self.classification_box.setObjectName("InspectorClassification")
        self._classification_grid = QGridLayout(self.classification_box)
        self.classification_chips = QWidget()
        self.classification_chips.setObjectName("InspectorClassificationChips")
        chip_layout = QGridLayout(self.classification_chips)
        chip_layout.setContentsMargins(0, 0, 0, 0)
        chip_layout.setSpacing(4)
        self.classification_buttons: dict[str, QPushButton] = {}
        for key, text, row, column, column_span in (
                ("run", "RUN", 0, 0, 2),
                ("pass", "PASS", 0, 2, 2),
                ("screen", "SCREEN", 0, 4, 2),
                ("rpo_run", "RPO RUN", 1, 0, 3),
                ("rpo_pass", "RPO PASS", 1, 3, 3),
                ("play_action", "PLAY ACTION", 2, 0, 2),
                ("scramble", "SCRAMBLE", 2, 2, 2),
                ("sack", "SACK", 2, 4, 2),
                ("special", "SPECIAL", 3, 0, 3),
                ("no_play", "NO PLAY", 3, 3, 3)):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setProperty("inspectorTag", "true")
            button.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(
                lambda checked=False, value=key:
                    self._set_classification_from_chip(value, checked))
            self.classification_buttons[key] = button
            chip_layout.addWidget(button, row, column, 1, column_span)
        self._classification_grid.addWidget(
            self.classification_chips, 0, 0, 1, 1)
        self.classification_box.hide()

        self.players_box = QGroupBox("PLAYERS AND OUTCOME")
        self.players_box.setObjectName("InspectorPlayers")
        self._players_grid = QGridLayout(self.players_box)
        self.players_box.hide()

        self.notes_box = QGroupBox("FILM NOTES")
        self.notes_box.setObjectName("InspectorNotes")
        self._notes_layout = QVBoxLayout(self.notes_box)
        self.notes_box.hide()
        self.notes_edit.setPlaceholderText("Add film note")

        # Notes held ~96px on every play whether or not anything was typed,
        # which is what pushed the details below the fold. It starts at two
        # lines and grows on demand.
        self.notes_expand_btn = QToolButton()
        self.notes_expand_btn.setObjectName("InspectorNotesExpand")
        self.notes_expand_btn.setProperty("quiet", "true")
        self.notes_expand_btn.setCheckable(True)
        self.notes_expand_btn.setText("Expand")
        self.notes_expand_btn.setToolTip("Give film notes more room")
        self.notes_expand_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.notes_expand_btn.toggled.connect(self._notes_expanded_toggled)
        # A one-word link was inheriting the full 39px button metrics and
        # costing more height than half the note field it governs.
        self.notes_expand_btn.setFixedHeight(16)
        notes_tools = QHBoxLayout()
        notes_tools.setContentsMargins(0, 0, 0, 0)
        notes_tools.setSpacing(0)
        notes_tools.addStretch(1)
        notes_tools.addWidget(self.notes_expand_btn)
        self._notes_layout.addLayout(notes_tools)

        # V2's analyst view uses the same live fields in a responsive scouting
        # ledger. At normal inspector widths this is a two-column split; at
        # narrow widths it stacks without stealing space from the player.
        self.analyst_ledger = QWidget()
        self.analyst_ledger.setObjectName("InspectorLedger")
        self.analyst_ledger.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._analyst_ledger_layout = QBoxLayout(
            QBoxLayout.Direction.LeftToRight, self.analyst_ledger)
        self._analyst_ledger_layout.setContentsMargins(0, 0, 0, 0)
        self._analyst_ledger_layout.setSpacing(9)

        self.analyst_left_column = QWidget()
        self.analyst_left_column.setObjectName("InspectorLedgerLeft")
        self._analyst_left_layout = QVBoxLayout(
            self.analyst_left_column)
        self._analyst_left_layout.setContentsMargins(0, 0, 0, 0)
        self._analyst_left_layout.setSpacing(5)
        self._analyst_left_layout.addWidget(self.essentials_box)
        self._analyst_left_layout.addWidget(self.classification_box)
        self._analyst_left_layout.addStretch(1)

        self.analyst_vertical_divider = QFrame()
        self.analyst_vertical_divider.setObjectName(
            "InspectorLedgerDivider")
        self.analyst_vertical_divider.setFrameShape(QFrame.Shape.VLine)
        self.analyst_vertical_divider.setFixedWidth(1)

        self.analyst_horizontal_divider = QFrame()
        self.analyst_horizontal_divider.setObjectName(
            "InspectorLedgerDivider")
        self.analyst_horizontal_divider.setFrameShape(QFrame.Shape.HLine)
        self.analyst_horizontal_divider.setFixedHeight(1)
        self.analyst_horizontal_divider.hide()

        self.analyst_right_column = QWidget()
        self.analyst_right_column.setObjectName("InspectorLedgerRight")
        self.analyst_right_column.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._analyst_right_layout = QVBoxLayout(
            self.analyst_right_column)
        self._analyst_right_layout.setContentsMargins(0, 0, 0, 0)
        self._analyst_right_layout.setSpacing(5)
        self._analyst_right_layout.addWidget(self.players_box)
        self._analyst_right_layout.addWidget(self.notes_box)
        self._analyst_right_layout.addStretch(1)

        self._analyst_ledger_layout.addWidget(
            self.analyst_left_column, 1)
        self._analyst_ledger_layout.addWidget(
            self.analyst_vertical_divider)
        self._analyst_ledger_layout.addWidget(
            self.analyst_horizontal_divider)
        self._analyst_ledger_layout.addWidget(
            self.analyst_right_column, 1)
        self._analyst_ledger_wide: bool | None = None
        self.analyst_ledger.hide()
        self._build_quick_pickers()
        layout.addWidget(self.quick_pickers)
        layout.addWidget(self.analyst_ledger)

        # Everything secondary lives behind a disclosure.
        self.metadata_section = CollapsibleSection("Metadata")
        meta_form = QFormLayout()
        self._metadata_form = meta_form
        meta_form.addRow("Filename base:", self.filename_edit)
        meta_form.addRow("Label:", self.label_edit)
        meta_form.addRow("Tags:", tags_row)
        meta_form.addRow("Notes:", self.notes_edit)
        self._notes_form_label = meta_form.labelForField(self.notes_edit)
        self.metadata_section.add_layout(meta_form)
        layout.addWidget(self.metadata_section)

        self.export_section = CollapsibleSection("Export settings")
        export_form = QFormLayout()
        self._export_form = export_form
        export_form.addRow("Preset:", self.preset_combo)
        export_form.addRow("", self.reel_check)
        export_form.addRow("", self.enabled_check)
        self.export_section.add_layout(export_form)
        layout.addWidget(self.export_section)

        # Domain-specific fields stay available but out of the default view.
        details_box = QGroupBox("Play Details")
        self.details_box = details_box
        details_box.setObjectName("ClipEditorDetails")
        grid = QGridLayout(details_box)
        self._details_grid = grid
        self.detail_edits: dict[str, DetailEdit] = {}
        self.detail_cells: dict[str, QWidget] = {}
        self.detail_cell_layouts: dict[str, QBoxLayout] = {}
        self.detail_labels: dict[str, QLabel] = {}
        for index, (key, label_text) in enumerate(DETAIL_FIELDS):
            row, col = divmod(index, 2)
            cell_widget = QWidget()
            cell_widget.setProperty("detailField", "true")
            cell = QBoxLayout(
                QBoxLayout.Direction.TopToBottom, cell_widget)
            cell.setContentsMargins(0, 0, 0, 0)
            small = QLabel(label_text)
            small.setProperty("role", "subtle")
            edit = DetailEdit()
            edit.setToolTip(f"{label_text} - autocompletes from values you've "
                            "used before")
            edit.setAccessibleName(label_text)
            self.detail_edits[key] = edit
            self.detail_cells[key] = cell_widget
            self.detail_cell_layouts[key] = cell
            self.detail_labels[key] = small
            cell.addWidget(small)
            cell.addWidget(edit)
            grid.addWidget(cell_widget, row, col)
        other_players_line = self.detail_edits["other_players"].lineEdit()
        if other_players_line is not None:
            other_players_line.setPlaceholderText("Name or number")
        primary_player_line = self.detail_edits["player_name"].lineEdit()
        if primary_player_line is not None:
            # "Primary Player" is already the field label. Keep the prompt
            # short enough to remain readable in the half-width inspector.
            # The field takes a jersey number as well as a name, and the
            # roster resolves it - but nothing said so, so nobody found it.
            primary_player_line.setPlaceholderText("Name or number")
        auto_row = row + 1
        self.autoname_btn = QPushButton("Auto-Name from Details")
        self.autoname_btn.setToolTip(
            "Keep the name and export filename updated from details and tags.\n"
            "Typing a name stops automatic updates. Click here to turn them back on.")
        self.autoname_btn.clicked.connect(self._auto_name)
        grid.addWidget(self.autoname_btn, auto_row, 0)
        self.details_to_tags_check = QCheckBox("Add details to tags")
        self.details_to_tags_check.setChecked(True)
        self.details_to_tags_check.setToolTip(
            "On Apply, each filled detail value is also added as a tag -\n"
            "useful with tag-based export folders.")
        grid.addWidget(self.details_to_tags_check, auto_row, 1)
        self.details_section = CollapsibleSection("Play details")
        self.details_section.setObjectName("InspectorMoreDetails")
        self.details_section.toggle.setObjectName("InspectorMoreDetailsToggle")
        self.details_section.toggle.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.details_section.toggle.toggled.connect(
            self._update_details_toggle_text)
        self.details_section.add_widget(details_box)

        # Analyst mode keeps the everyday logging surface short.  The complete
        # football schema remains available, but it opens only when requested
        # and is separate from the always-reachable Naming Export controls.
        self.advanced_details_section = CollapsibleSection(
            "Additional Play Details")
        self.advanced_details_section.setObjectName(
            "InspectorAdvancedDetails")
        self.advanced_details_section.toggle.setObjectName(
            "InspectorAdvancedDetailsToggle")
        self.advanced_details_section.hide()
        layout.addWidget(self.advanced_details_section)

        # Details nobody has filled collapse into one wrapping row of add
        # chips. Nine always-rendered rows meant seven dashes to scroll past
        # on a typical play. Values are untouched: a collapsed field still
        # loads and saves, this only changes what is on screen.
        self._revealed_details: set[str] = set()
        self._advanced_add_row = QWidget()
        self._advanced_add_row.setObjectName("InspectorDetailAddRow")
        self._advanced_add_layout = FlowLayout(
            self._advanced_add_row, spacing=5)
        self._advanced_add_layout.setContentsMargins(0, 5, 0, 0)
        self._advanced_add_row.hide()
        self.advanced_details_section.add_widget(self._advanced_add_row)

        layout.addWidget(self.details_section)

        self.preview_container = QWidget()
        preview_layout = QVBoxLayout(self.preview_container)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(2)
        self.preview_label = QLabel("")
        self.preview_label.setProperty("role", "subtle")
        self.preview_label.setWordWrap(True)
        preview_layout.addWidget(QLabel("Export filename preview:"))
        preview_layout.addWidget(self.preview_label)
        layout.addWidget(self.preview_container)

        # Analyst mode turns the disclosure into a focused Naming & Export
        # drawer.  It is built once and receives the existing live controls
        # when modes change, so there is never a second copy of clip data.
        self.analyst_naming_container = QWidget()
        self.analyst_naming_container.setObjectName(
            "InspectorNamingExportBody")
        self._analyst_naming_layout = QVBoxLayout(
            self.analyst_naming_container)
        self._analyst_naming_layout.setContentsMargins(4, 6, 4, 6)
        self._analyst_naming_layout.setSpacing(8)
        self._analyst_title_form = QFormLayout()
        self._analyst_naming_layout.addLayout(self._analyst_title_form)
        self._analyst_auto_layout = QVBoxLayout()
        self._analyst_auto_layout.setSpacing(6)
        self._analyst_naming_layout.addLayout(self._analyst_auto_layout)
        self._analyst_preview_layout = QVBoxLayout()
        self._analyst_preview_layout.setSpacing(4)
        self._analyst_naming_layout.addLayout(self._analyst_preview_layout)
        self._analyst_option_layout = QVBoxLayout()
        self._analyst_option_layout.setSpacing(5)
        self._analyst_naming_layout.addLayout(self._analyst_option_layout)
        self.analyst_naming_container.hide()
        self.details_section.add_widget(self.analyst_naming_container)

        self._build_attribute_rows()

        self.error_label = QLabel("")
        self.error_label.setProperty("role", "error")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        self.apply_btn = QPushButton("Apply changes")
        self.apply_btn.setProperty("accent", "true")
        self.apply_btn.clicked.connect(self._apply_button_clicked)
        layout.addWidget(self.apply_btn)
        self.save_next_btn = QPushButton("Save + Next")
        self.save_next_btn.setObjectName("InspectorSaveNext")
        self.save_next_btn.setProperty("primary", "true")
        self.save_next_btn.clicked.connect(self._apply_from_key)
        self.save_next_btn.hide()
        self.quick_export_btn = QPushButton("Export Clip")
        self.quick_export_btn.setObjectName("InspectorQuickExport")
        self.quick_export_btn.setToolTip(
            "Export this clip immediately with its assigned preset (Ctrl+E)")
        self.quick_export_btn.clicked.connect(self._request_quick_export)
        layout.addWidget(self.quick_export_btn)
        self.package_btn = QPushButton("Package / Cut Up")
        self.package_btn.setObjectName("InspectorPackageExport")
        self.package_btn.setToolTip(
            "Open the existing package and cut-up export workspace")
        self.package_btn.clicked.connect(self.package_requested.emit)
        self.package_btn.hide()
        layout.addStretch()

        self.action_bar = QWidget()
        self.action_bar.setObjectName("InspectorActionBar")
        action_layout = QVBoxLayout(self.action_bar)
        action_layout.setContentsMargins(10, 5, 12, 6)
        action_layout.setSpacing(4)
        self._action_layout = action_layout
        self._action_button_row = QHBoxLayout()
        self._action_button_row.setSpacing(6)
        action_layout.addLayout(self._action_button_row)
        # Save + Next drops onto its own line when the three no longer fit.
        self._action_button_row_2 = QHBoxLayout()
        self._action_button_row_2.setSpacing(6)
        action_layout.addLayout(self._action_button_row_2)
        self.shortcut_label = QLabel(
            "ENTER save + next  /  CTRL+ENTER save  /  ESC return to playback")
        self.shortcut_label.setObjectName("InspectorShortcutHint")
        self.shortcut_label.setWordWrap(True)
        action_layout.addWidget(self.shortcut_label)
        self.action_bar.hide()
        outer.addWidget(self.action_bar)

        for edit in (self.title_edit, self.filename_edit):
            edit.textChanged.connect(self._name_edited)
            edit.textChanged.connect(self._refresh_preview)
        self.tags_edit.textChanged.connect(self._refresh_auto_name)
        for edit in (self.title_edit, self.start_edit, self.end_edit):
            edit.textChanged.connect(self._refresh_analyst_summary)
        for edit in (
                self.title_edit, self.filename_edit, self.start_edit,
                self.end_edit, self.label_edit, self.tags_edit):
            edit.textChanged.connect(self._mark_unsaved)
        self.notes_edit.textChanged.connect(self._mark_unsaved)
        self.preset_combo.currentIndexChanged.connect(self._mark_unsaved)
        self.reel_check.toggled.connect(self._mark_unsaved)
        self.enabled_check.toggled.connect(self._mark_unsaved)
        for edit in self.detail_edits.values():
            line = edit.lineEdit()
            if line is not None:
                line.textEdited.connect(self._mark_unsaved)
                line.textChanged.connect(self._refresh_analyst_summary)
            edit.currentTextChanged.connect(self._detail_value_changed)

        # Enter anywhere in the inspector applies; in review mode the main
        # window then advances to the next clip.
        for edit in (self.title_edit, self.filename_edit, self.start_edit,
                     self.end_edit, self.label_edit, self.tags_edit):
            edit.returnPressed.connect(self._apply_from_key)
        for combo in self.detail_edits.values():
            line = combo.lineEdit()
            if line is not None:
                line.returnPressed.connect(self._apply_from_key)

        QShortcut(QKeySequence("Ctrl+Return"), self,
                  activated=self._apply_only)
        self.apply_field_layout()
        self.apply_density(
            settings.inspector_density if settings else INSPECTOR_DENSITY_COMPACT)
        self.apply_dropdown_mode()
        self.set_clip(None)

    def _build_empty_state(self) -> QWidget:
        """The panel while no clip is selected.

        The inspector is the biggest blank surface in the app, so it earns
        a real empty state: what this panel is for, and the three keys that
        put a clip in it.
        """
        panel = QWidget()
        panel.setObjectName("ClipEditorEmptyState")
        column = QVBoxLayout(panel)
        column.setContentsMargins(24, 40, 24, 24)
        column.setSpacing(0)

        icon = QLabel("✂")
        icon.setObjectName("ClipEditorEmptyIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(60, 60)
        column.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        column.addSpacing(16)

        heading = QLabel("No clip selected")
        heading.setProperty("role", "sectionTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(heading)
        column.addSpacing(6)

        sub = QLabel(
            "Mark a play on the timeline,\nor pick one from the ledger.")
        sub.setProperty("role", "subtle")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        column.addWidget(sub)
        column.addSpacing(22)

        for key, action in (
                ("I", "mark in-point"),
                ("O", "mark out-point"),
                ("A", "create clip from marks")):
            row = QHBoxLayout()
            row.setSpacing(10)
            chip = QLabel(key)
            chip.setObjectName("ClipEditorEmptyKey")
            chip.setFixedSize(30, 22)
            chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            text = QLabel(action)
            text.setObjectName("ClipEditorEmptyHint")
            row.addStretch(1)
            row.addWidget(chip)
            row.addWidget(text)
            row.addStretch(1)
            column.addLayout(row)
            column.addSpacing(8)
        column.addStretch(1)
        return panel

    def _apply_from_key(self) -> None:
        """Enter: save, then let the window decide whether to advance."""
        if self._clip is None:
            return
        if self._apply():
            self.save_and_advance.emit()

    def _apply_button_clicked(self) -> None:
        """Keep the V1 Apply behavior while V2 Save returns to playback."""
        if self._analyst_mode:
            self._apply_only()
        else:
            self._apply()

    def _apply_only(self) -> None:
        """Ctrl+Enter: save without moving on, then return focus to playback."""
        if self._clip is not None and self._apply():
            self.return_to_playback.emit()

    def _request_quick_export(self) -> None:
        """Ask the main window to export only the clip in this inspector."""
        if self._clip is not None:
            self.quick_export_requested.emit(self._clip.id)

    def apply_quick_details(
            self, values: dict[str, str], *,
            remove_tag_values: tuple[str, ...] = ()) -> bool:
        """Set or clear football fields and save through the normal undo flow.

        Quick-tag values are also mirrored into the comma-separated tag list
        when that option is enabled. When a quick tag is toggled off, remove
        those mirrored values before saving so the label does not appear to
        survive after its structured detail has been cleared.
        """
        if self._clip is None:
            return False
        accepted = {
            key: str(value).strip()
            for key, value in values.items()
            if key in self.detail_edits
        }
        if not accepted:
            return False
        previous_values = {
            key: self.detail_edits[key].text()
            for key in accepted
        }
        previous_tags = self.tags_edit.text()
        for key, value in accepted.items():
            self.detail_edits[key].setText(value)
        remove_keys = {
            value.strip().casefold()
            for value in remove_tag_values
            if value.strip()
        }
        remove_keys.difference_update(
            value.casefold() for value in detail_service.details_to_tags(self._collect_details()))
        if remove_keys:
            retained = [
                tag.strip()
                for tag in self.tags_edit.text().split(",")
                if tag.strip() and tag.strip().casefold() not in remove_keys
            ]
            self.tags_edit.setText(", ".join(retained))
        if self._apply():
            self.return_to_playback.emit()
            return True
        for key, value in previous_values.items():
            self.detail_edits[key].setText(value)
        self.tags_edit.setText(previous_tags)
        return False

    #: label, choices, whether it ends in a typed slot, and the handler
    #: that already owns the field. Every one of these writes through an
    #: existing setter, so the rows are a new way to reach the same edit
    #: rather than a second path into the clip.
    ATTRIBUTE_ROWS = (
        ("SITUATION", (
            ("quarter", "Quarter", QUARTER_CHOICES, False),
            ("down", "Down", DOWN_CHOICES, False),
            ("to_go", "To go", ("5", "10", "15"), True),
            ("ball_on", "Ball on", ("Own", "Mid", "Opp"), True),
        )),
        ("RESULT", (
            ("result_a", "Outcome", tuple(short_for("result", value)
             for value in ("No Gain", "First Down", "Reception")),
             False),
            ("result_b", "More", tuple(short_for("result", value)
             for value in ("Touchdown", "Sack", "Interception")), False),
        )),
        ("PLAY", (
            ("run_pass", "Run/Pass", ("Run", "Pass", "RPO", "Screen"),
             False),
            ("action", "Action", ("Block", "Pressure", "Missed Tackle"),
             False),
        )),
    )
    #: The rows replace the chip pickers rather than deleting them: set
    #: this False and the old pickers come back untouched.
    USE_ATTRIBUTE_ROWS = True
    #: Whether the inspector carries a dedicated picker for the situation
    #: at all. The grid under the timeline shows quarter, down and result
    #: across every play at once and can be logged from the keyboard, so
    #: the panel stopped being the place to do it. Nothing is deleted:
    #: set this True and the rows come straight back. With it off those
    #: three fall back to ordinary detail fields rather than vanishing,
    #: because a field you can see and cannot reach is worse than either.
    LOG_ATTRIBUTES_IN_INSPECTOR = False

    def _chips_own(self, key: str) -> bool:
        """Does a dedicated picker own this field right now?"""
        return (self.LOG_ATTRIBUTES_IN_INSPECTOR
                and key in CHIP_DRIVEN_FIELDS)
    #: Results are stored under the project's own vocabulary, but a row
    #: cell is ~60px wide, so the long names get a short face here. The
    #: saved value is always the long one.
    RESULT_SHORT = {
        value: short_for("result", value)
        for value in values_for("result")
        if short_for("result", value) != value
    }
    #: The compact result labels the rows show, and what they save as.
    RESULT_ALIASES = {
        short: long for long, short in RESULT_SHORT.items()}

    def _build_attribute_rows(self) -> None:
        """One shape for every attribute, so the panel works when narrow.

        The result grid, the quarter and down chip rows and the compact
        distance input were four layouts answering the same kind of
        question. At the inspector's real width none of them fitted well.
        """
        self.attribute_rows_panel = AttributeRowsPanel()
        self.attribute_rows_panel.setObjectName("InspectorAttributeRows")
        outer = QVBoxLayout(self.attribute_rows_panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(5)
        self.attribute_rows: dict[str, AttributeRowWidget] = {}
        for section, rows in self.ATTRIBUTE_ROWS:
            caption = QLabel(section)
            caption.setProperty("role", "pickerTitle")
            if section == "RESULT":
                # Results are the one vocabulary a project owns, so the
                # controls that edit it move here with the rows rather
                # than staying stranded on the hidden pickers.
                header = QHBoxLayout()
                header.setContentsMargins(0, 0, 0, 0)
                header.setSpacing(6)
                header.addWidget(caption)
                header.addStretch(1)
                detach_widget(self.more_results_button)
                detach_widget(self.manage_results_button)
                header.addWidget(self.more_results_button)
                header.addWidget(self.manage_results_button)
                outer.addLayout(header)
            else:
                outer.addWidget(caption)
            for key, label, choices, entry in rows:
                row = AttributeRowWidget(
                    label, tuple(choices), entry=entry,
                    entry_mode=("distance" if key == "to_go" else "text"),
                    placeholder="#" if key == "to_go" else "yd")
                row.valueChanged.connect(
                    lambda value, name=key: self._attribute_row_changed(
                        name, value))
                outer.addWidget(row)
                self.attribute_rows[key] = row
        # Seed from the vocabulary that already exists rather than the
        # literal tuple above, which is only a starting shape.
        self._rebuild_result_rows()
        # Every wide row exists by now, so this is the point the panel can
        # be told it is allowed to be narrow.
        self._allow_narrow_layout()
        self.attribute_rows_panel.hide()
        parent = self.quick_pickers.parentWidget()
        layout = parent.layout() if parent is not None else None
        if layout is not None:
            layout.insertWidget(
                layout.indexOf(self.quick_pickers) + 1,
                self.attribute_rows_panel)

    def _attribute_row_changed(self, key: str, value: str) -> None:
        """Route a row into the setter that already owns its field."""
        if self._loading_clip:
            return
        if key == "quarter":
            self._set_quarter_from_chip(value)
        elif key == "down":
            self._set_down_from_chip(value)
        elif key == "to_go":
            self._set_to_go_from_input(value)
        elif key == "ball_on":
            self.detail_edits["ball_on"].setText(value)
        elif key in ("result_a", "result_b"):
            self._set_result_from_chip(
                self.RESULT_ALIASES.get(value, value))
        elif key == "run_pass":
            if value == "RPO":
                self.detail_edits["play_type"].setText(value)
            else:
                self._set_classification_from_chip(value.casefold())
        elif key == "action":
            self.detail_edits["action"].setText(value)
        self._refresh_analyst_summary()

    def _sync_attribute_rows(self) -> None:
        """Show what the clip already says, without writing anything back."""
        if not hasattr(self, "attribute_rows"):
            return
        details = {
            key: edit.text().strip()
            for key, edit in self.detail_edits.items()
        }
        combined = details.get("down_distance", "")
        values = {
            "quarter": details.get("quarter", ""),
            "down": self._stored_down(combined),
            "to_go": self._distance_to_go(combined),
            "ball_on": details.get("ball_on", ""),
            "run_pass": details.get("play_type") or details.get(
                "run_pass", ""),
            "action": details.get("action", ""),
        }
        result = details.get("result", "")
        for name in ("result_a", "result_b"):
            row = self.attribute_rows[name]
            shown = ""
            for choice in row.buttons:
                saved = self.RESULT_ALIASES.get(choice, choice)
                if result_service.has_result(result, saved):
                    shown = choice
                    break
            values[name] = shown
        for name, row in self.attribute_rows.items():
            row.blockSignals(True)
            row.set_value(values.get(name, ""))
            row.blockSignals(False)

    def _rebuild_result_rows(self) -> None:
        """Point the two result rows at the project's current favourites.

        The pickers already rebuild from settings.result_favorites; the
        rows read the same list so Manage changes both, and a project
        that renames its vocabulary is not left editing a stale one.
        """
        if not hasattr(self, "attribute_rows"):
            return
        chosen = list(self.result_buttons)
        half = (len(chosen) + 1) // 2
        for name, values in (
                ("result_a", chosen[:half]), ("result_b", chosen[half:])):
            row = self.attribute_rows.get(name)
            if row is None:
                continue
            row.blockSignals(True)
            row.set_choices(tuple(
                self.RESULT_SHORT.get(value, value) for value in values))
            row.blockSignals(False)

    #: Widgets whose own text set a floor the dock could not get under.
    #: QWidgetItem takes a widget's layout minimum from minimumSizeHint
    #: unless the size policy has no shrink flag, so Ignored is the only
    #: thing that lets a text-bearing widget go narrower than its text.
    def _allow_narrow_layout(self) -> None:
        """Let the panel reflow when the dock is dragged in.

        Without this the inspector had a hard floor around 254px: below
        it the scroll area stopped shrinking and the panel was clipped by
        the window edge instead of restacking. Every widget here still
        gets its full width whenever there is room; it just stops
        demanding it.
        """
        shrinkable = [
            self.primary_container, self.time_row,
            self.advanced_details_section.toggle,
            self.details_section.toggle,
            self.more_results_button, self.manage_results_button,
            self.analyst_right_column, self.players_box,
            self.notes_box, self.summary_card,
            self.attribute_rows_panel,
        ]
        for widget in shrinkable:
            if widget is None:
                continue
            policy = widget.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            widget.setSizePolicy(policy)
            widget.setMinimumWidth(0)
        # A field that shrinks to nothing is worse than one that clips:
        # Ignored alone collapsed the clip's in and out points to empty
        # boxes. An explicit minimum still counts under Ignored, so these
        # keep enough width to read a timecode.
        for field in (self.start_edit, self.end_edit):
            policy = field.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            field.setSizePolicy(policy)
            field.setMinimumWidth(58)
        # Not the heading: Ignored collapsed PLAY 04 to nothing, and the
        # play number is the one thing the panel must always say.
        # The scroll area's content widget inherits the worst of its
        # children, so it has to be told as well.
        for area in self.findChildren(QScrollArea):
            area.setMinimumWidth(0)
            content = area.widget()
            if content is not None:
                content.setMinimumWidth(0)

    #: Below this the panel stops being a two-column surface and every
    #: header that shared a line has to give one up.
    NARROW_WIDTH = 252

    def _apply_narrow_mode(self, narrow: bool) -> None:
        """Drop the parts that only earn their space when there is space.

        Captions go before controls. Start and End sit beside fields that
        already read as timecodes, and RANGE labels a row titled RANGE, so
        those are the first things worth losing - not the buttons.
        """
        if narrow == getattr(self, "_narrow_mode", None):
            return
        self._narrow_mode = narrow
        for caption in (
                getattr(self, "_start_caption", None),
                getattr(self, "_end_caption", None),
                getattr(self, "_range_form_label", None)):
            if caption is not None:
                caption.setVisible(not narrow)
        # LOGGED and SAVED restate what the ledger already shows, so they
        # are what the heading line gives up to keep EDIT reachable.
        for chip in (self.review_state_label, self.save_state_label):
            chip.setVisible(not narrow)
        # Option 4 already gives each scope a stable two-by-two position:
        # exports above, saves below. Narrow mode may compress their faces but
        # never reshuffles the meaning under the pointer.
        if self._analyst_mode:
            self.package_btn.show()
            self.save_next_btn.show()

    def open_width(self) -> int:
        """The width to give the panel back when it unfolds."""
        return max(int(getattr(self, "_open_width", 0)), self.minimumWidth())

    def is_collapsed(self) -> bool:
        return bool(getattr(self, "_collapsed", False))

    def set_collapsed(self, collapsed: bool) -> None:
        """Fold the panel to a strip, or bring it back.

        Folding rather than closing: the panel still owns the title, the
        range, the film note and export, so it has to stay reachable. A
        closed dock is a thing you have to remember exists.
        """
        collapsed = bool(collapsed)
        self._collapsed = collapsed
        self.form_area.setVisible(not collapsed and self._clip is not None)
        self.empty_state.setVisible(not collapsed and self._clip is None)
        self.action_bar.setVisible(
            not collapsed and self._analyst_mode and self._clip is not None)
        self.collapse_btn.setText(
            COLLAPSE_SHUT_GLYPH if collapsed else COLLAPSE_OPEN_GLYPH)
        self.collapse_btn.setToolTip(
            "Show Play Details  (Ctrl+B)" if collapsed
            else "Fold Play Details away  (Ctrl+B)")
        if self.collapse_btn.isChecked() != collapsed:
            self.collapse_btn.blockSignals(True)
            self.collapse_btn.setChecked(collapsed)
            self.collapse_btn.blockSignals(False)
        if collapsed:
            # Remember the floor and the width, separately. Restoring only
            # the floor reopens the panel at its narrowest, which is not
            # where the user left it.
            self._floor_width = max(self.minimumWidth(), COLLAPSED_WIDTH)
            self._open_width = max(self.width(), self._floor_width)
            self.setFixedWidth(COLLAPSED_WIDTH)
        else:
            self.setMinimumWidth(
                getattr(self, "_floor_width", COLLAPSED_WIDTH))
            self.setMaximumWidth(16777215)
        self.updateGeometry()
        self.collapse_changed.emit(collapsed)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self.is_collapsed())

    def _sync_inspector_panels(self) -> None:
        """Rows or chip pickers - one of them, never both.

        Also the one place the fold is enforced. set_clip and
        apply_field_layout both set these visibilities for their own
        reasons and would quietly unfold the panel underneath the user.

        Three places used to decide this and they had drifted apart, which
        is how the panel ended up showing neither before a clip loaded.
        """
        if self.is_collapsed():
            self.form_area.hide()
            self.empty_state.hide()
            self.action_bar.hide()
        if not hasattr(self, "attribute_rows_panel"):
            return
        live = (self._analyst_mode and self._clip is not None
                and self.LOG_ATTRIBUTES_IN_INSPECTOR)
        rows = live and self.USE_ATTRIBUTE_ROWS
        self.attribute_rows_panel.setVisible(rows)
        # The pickers still answer these questions; the rows answer them
        # in one shape. Flip USE_ATTRIBUTE_ROWS to get them back.
        self.quick_pickers.setVisible(live and not self.USE_ATTRIBUTE_ROWS)

    def _build_quick_pickers(self) -> None:
        """Chip rows for the answers that repeat on every play."""
        self.quick_pickers = QWidget()
        self.quick_pickers.setObjectName("InspectorQuickPickers")
        outer = QVBoxLayout(self.quick_pickers)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        self.result_buttons: dict[str, QPushButton] = {}
        self.quarter_buttons: dict[str, QPushButton] = {}
        self.down_buttons: dict[str, QPushButton] = {}
        self._project_result_values: list[str] = []
        self._result_vocabulary: list[str] = []

        result_header = QHBoxLayout()
        result_caption = QLabel("RESULT")
        result_caption.setProperty("role", "pickerTitle")
        result_header.addWidget(result_caption)
        result_header.addStretch(1)
        self.more_results_button = QToolButton()
        self.more_results_button.setObjectName("InspectorMoreResults")
        self.more_results_button.setText("More Results")
        self.more_results_button.setPopupMode(
            QToolButton.ToolButtonPopupMode.InstantPopup)
        self.result_menu = QMenu(self.more_results_button)
        self.more_results_button.setMenu(self.result_menu)
        result_header.addWidget(self.more_results_button)
        self.manage_results_button = QPushButton("Manage")
        self.manage_results_button.setObjectName("InspectorManageResults")
        self.manage_results_button.setProperty("quiet", "true")
        self.manage_results_button.clicked.connect(self._manage_results)
        result_header.addWidget(self.manage_results_button)
        outer.addLayout(result_header)

        self._result_grid = QGridLayout()
        self._result_grid.setHorizontalSpacing(4)
        self._result_grid.setVerticalSpacing(4)
        for index, choice in enumerate(DEFAULT_RESULT_CHOICES):
            self._add_picker_button(
                self._result_grid, index, choice, self.result_buttons,
                self._set_result_from_chip, 3)
        outer.addLayout(self._result_grid)
        self._result_vocabulary = unique_results(
            STANDARD_RESULT_CHOICES, DEFAULT_RESULT_CHOICES)
        self._rebuild_result_menu()

        row = QHBoxLayout()
        row.setSpacing(12)
        quarter_column = QVBoxLayout()
        quarter_column.setSpacing(4)
        self._picker_section(
            quarter_column, "QUARTER", QUARTER_CHOICES, self.quarter_buttons,
            self._set_quarter_from_chip, columns=4)
        row.addLayout(quarter_column, 1)
        row.setAlignment(
            quarter_column, Qt.AlignmentFlag.AlignTop)
        down_column = QVBoxLayout()
        down_column.setSpacing(4)
        self._picker_section(
            down_column, "DOWN", DOWN_CHOICES, self.down_buttons,
            self._set_down_from_chip, columns=4)
        to_go_row = QHBoxLayout()
        to_go_row.setSpacing(5)
        to_go_caption = QLabel("TO GO")
        to_go_caption.setProperty("role", "pickerTitle")
        self.to_go_edit = QLineEdit()
        self.to_go_edit.setObjectName("InspectorToGoEdit")
        self.to_go_edit.setAccessibleName("Distance to go")
        self.to_go_edit.setPlaceholderText("10")
        self.to_go_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.to_go_edit.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"(?i:\d*|goal|inches)"),
            self.to_go_edit,
        ))
        self.to_go_edit.setToolTip(
            "Distance needed for a first down. Enter yards, Goal or Inches.")
        self.to_go_edit.textEdited.connect(self._set_to_go_from_input)
        self.to_go_edit.editingFinished.connect(
            self._commit_to_go_input)
        self.to_go_edit.returnPressed.connect(self._apply_from_key)
        to_go_suffix = QLabel("YDS / GOAL")
        to_go_suffix.setObjectName("InspectorToGoSuffix")
        to_go_suffix.setProperty("role", "subtle")
        to_go_row.addWidget(to_go_caption)
        to_go_row.addWidget(self.to_go_edit)
        to_go_row.addWidget(to_go_suffix)
        to_go_row.addStretch(1)
        down_column.addLayout(to_go_row)
        row.addLayout(down_column, 1)
        row.setAlignment(
            down_column, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(row)
        self.quick_pickers.hide()

    def _picker_section(self, target, title, choices, registry, handler,
                        columns: int) -> QGridLayout:
        caption = QLabel(title)
        caption.setProperty("role", "pickerTitle")
        target.addWidget(caption)
        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)
        for index, choice in enumerate(choices):
            self._add_picker_button(
                grid, index, choice, registry, handler, columns)
        target.addLayout(grid)
        return grid

    def _add_picker_button(self, grid, index, choice, registry, handler,
                           columns: int) -> None:
        button = QPushButton(choice.replace("&", "&&"))
        button.setCheckable(True)
        button.setProperty("inspectorTag", "true")
        button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.clicked.connect(
            lambda _checked=False, value=choice: handler(value))
        registry[choice] = button
        grid.addWidget(button, index // columns, index % columns)

    def set_result_choices(self, values: list[str]) -> None:
        """Refresh the six favorites and the lossless overflow vocabulary."""
        self._project_result_values = unique_results(values)
        configured_results = (
            self.settings.fixed_details.get("result", [])
            if self.settings else [])
        hidden = {str(value).strip().casefold() for value in (
            self.settings.hidden_fixed_details.get("result", [])
            if self.settings else [])}
        standard_results = [value for value in STANDARD_RESULT_CHOICES
                            if value.casefold() not in hidden]
        self._result_vocabulary = unique_results(
            standard_results, configured_results,
            self._project_result_values)
        configured_favorites = (
            self.settings.result_favorites if self.settings else [])
        chosen = unique_results(
            configured_favorites or list(DEFAULT_RESULT_CHOICES))
        chosen = chosen[:MAX_RESULT_CHIPS]
        if list(self.result_buttons) == chosen:
            self._rebuild_result_menu()
            return
        while self._result_grid.count():
            item = self._result_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.result_buttons = {}
        for index, value in enumerate(chosen):
            self._add_picker_button(
                self._result_grid, index, value, self.result_buttons,
                self._set_result_from_chip, 3)
        self._rebuild_result_menu()
        self._rebuild_result_rows()
        self._sync_picker_state()

    def _rebuild_result_menu(self) -> None:
        self.result_menu.clear()
        current_result = ""
        if hasattr(self, "detail_edits") and "result" in self.detail_edits:
            current_result = self.detail_edits["result"].text().strip()
        favorites = {
            result_service.result_key(value)
            for value in self.result_buttons
        }
        overflow = [
            value for value in self._result_vocabulary
            if result_service.result_key(value) not in favorites
        ]
        if not overflow:
            empty = QAction("No additional results", self.result_menu)
            empty.setEnabled(False)
            self.result_menu.addAction(empty)
            return
        for value in overflow:
            action = self.result_menu.addAction(value)
            action.setCheckable(True)
            action.setChecked(
                result_service.has_result(current_result, value))
            action.triggered.connect(
                lambda _checked=False, result=value:
                    self._set_result_from_chip(result))

    def _manage_results(self) -> None:
        dialog = self.result_dialog_class(
            list(self.result_buttons), self._result_vocabulary, self)
        while dialog.exec() == ResultManagerDialog.DialogCode.Accepted:
            if self.settings is not None:
                candidate = deepcopy(self.settings)
                candidate.result_favorites = dialog.favorites()
                candidate.fixed_details["result"] = unique_results(
                    STANDARD_RESULT_CHOICES, dialog.results())
                try:
                    candidate.save()
                except Exception as exc:
                    QMessageBox.critical(self, "Results were not saved", str(exc))
                    continue
                self.settings.result_favorites = candidate.result_favorites
                self.settings.fixed_details["result"] = candidate.fixed_details["result"]
            self.set_result_choices(self._project_result_values)
            return

    def _set_result_from_chip(self, value: str) -> None:
        value = self.RESULT_ALIASES.get(value, value)
        current = self.detail_edits["result"].text().strip()
        self.detail_edits["result"].setText(
            result_service.toggle_result(current, value))
        self._rebuild_result_menu()
        self._refresh_analyst_summary()

    def _set_quarter_from_chip(self, value: str) -> None:
        current = self._leading_number(self.detail_edits["quarter"].text())
        already = bool(current) and current == self._leading_number(value)
        self.detail_edits["quarter"].setText("" if already else value)
        self._refresh_analyst_summary()

    def _set_down_from_chip(self, value: str) -> None:
        """Set the down without discarding a distance already recorded.

        Down and distance share one field, so replacing the whole value would
        silently drop the "& 10" half of it.
        """
        current = self.detail_edits["down_distance"].text().strip()
        to_go = self._distance_to_go(current)
        already = self._leading_number(current) == self._leading_number(value)
        if already:
            self.detail_edits["down_distance"].setText(
                f"To Go {to_go}" if to_go else "")
        else:
            self.detail_edits["down_distance"].setText(
                f"{value} & {to_go}" if to_go else value)
        self._refresh_analyst_summary()

    def _set_to_go_from_input(self, value: str) -> None:
        """Merge the compact distance input into the stored combined field."""
        current = self.detail_edits["down_distance"].text().strip()
        down = self._stored_down(current)
        to_go = self._normalize_to_go(value)
        # The validator permits G/Go/Goa while "Goal" is being typed. Do not
        # replace a valid saved distance with an incomplete intermediate.
        if to_go and not (to_go.isdigit() or to_go in {"Goal", "Inches"}):
            return
        if down and to_go:
            combined = f"{down} & {to_go}"
        elif down:
            combined = down
        elif to_go:
            combined = f"To Go {to_go}"
        else:
            combined = ""
        self.detail_edits["down_distance"].setText(combined)
        self._refresh_analyst_summary()

    def _commit_to_go_input(self) -> None:
        """Normalize a completed distance and discard unfinished Goal text."""
        raw = self.to_go_edit.text().strip()
        value = self._normalize_to_go(raw)
        if value and not (value.isdigit() or value in {"Goal", "Inches"}):
            value = ""
        if self.to_go_edit.text() != value:
            self.to_go_edit.blockSignals(True)
            self.to_go_edit.setText(value)
            self.to_go_edit.blockSignals(False)
        self._set_to_go_from_input(value)

    @staticmethod
    def _normalize_to_go(value: str) -> str:
        text = " ".join(str(value or "").strip().split())
        if text.casefold() in {"goal", "inches"}:
            return text.title()
        if text.isdigit():
            return str(int(text))
        return text

    @classmethod
    def _stored_down(cls, value: str) -> str:
        """The down portion of a combined value, excluding To Go-only text."""
        return down_distance(parse_down_distance(value)[0], "")

    @staticmethod
    def _distance_to_go(value: str) -> str:
        """Distance from legacy/current combined Down & Distance spellings."""
        return parse_down_distance(value)[1]

    @staticmethod
    def _leading_number(value: str) -> str:
        """First digit of a value, so "2 & 10", "2nd & 10" and "Q2" all match.

        Projects are inconsistent about this: detected clips and hand-typed
        ones use different forms of the same answer, and a chip that fails to
        light up on a play that is already logged looks broken.
        """
        match = re.match(r"\s*(?:q)?\s*(\d)", value.strip(), re.IGNORECASE)
        return match.group(1) if match else ""

    def _sync_picker_state(self) -> None:
        """Reflect the stored values back onto the chips."""
        result = self.detail_edits["result"].text().strip()
        for value, button in self.result_buttons.items():
            button.setChecked(result_service.has_result(result, value))
        quarter = self._leading_number(self.detail_edits["quarter"].text())
        for value, button in self.quarter_buttons.items():
            button.setChecked(
                bool(quarter) and self._leading_number(value) == quarter)
        down = self._leading_number(
            self.detail_edits["down_distance"].text())
        for value, button in self.down_buttons.items():
            button.setChecked(
                bool(down) and self._leading_number(value) == down)
        to_go = self._distance_to_go(
            self.detail_edits["down_distance"].text())
        if self.to_go_edit.text() != to_go:
            self.to_go_edit.blockSignals(True)
            self.to_go_edit.setText(to_go)
            self.to_go_edit.blockSignals(False)

    def _set_classification_from_chip(
            self, key: str, checked: bool = True) -> None:
        """Stage an inspector classification without saving unrelated edits."""
        if key == "play_action":
            self.detail_edits["play_action"].setText(
                "Play Action" if checked else "")
            self._refresh_analyst_summary()
            return
        values = {
            "run": {"run_pass": "Run"},
            "pass": {"run_pass": "Pass"},
            "screen": {"run_pass": "Pass", "play_type": "Screen"},
            "rpo_run": {"run_pass": "Run", "play_type": "RPO"},
            "rpo_pass": {"run_pass": "Pass", "play_type": "RPO"},
            "scramble": {"run_pass": "Run", "play_type": "Scramble"},
            "sack": {"run_pass": "Pass", "result": "Sack"},
            "special": {"run_pass": "Special"},
            "no_play": {"run_pass": "No Play"},
        }.get(key, {})
        if not checked:
            for field, value in values.items():
                if field == "result":
                    current = self.detail_edits["result"].text().strip()
                    self.detail_edits["result"].setText(
                        result_service.remove_result(current, value))
                    continue
                if self.detail_edits[field].text().strip().casefold() \
                        == value.casefold():
                    self.detail_edits[field].clear()
            self._refresh_analyst_summary()
            return
        current_type = self.detail_edits["play_type"].text().strip().casefold()
        if key == "sack" and current_type == "scramble":
            self.detail_edits["play_type"].clear()
        for field, value in values.items():
            if field == "result":
                current = self.detail_edits["result"].text().strip()
                self.detail_edits["result"].setText(
                    result_service.add_results(current, value))
            else:
                self.detail_edits[field].setText(value)
        self._refresh_analyst_summary()

    def set_analyst_mode(self, enabled: bool) -> None:
        """Expose the core football fields and pin actions for the V2 ledger."""
        enabled = bool(enabled)
        if self._analyst_mode == enabled:
            return
        self._analyst_mode = enabled
        if enabled:
            self.heading_label.setText("PLAY DETAILS")
            self.essentials_box.setTitle("SITUATION")
            self.players_box.setTitle("PEOPLE")
            self.notes_box.setTitle("NOTES")
            self.details_section.toggle.setText("Naming Export")
            self.details_section.toggle.setStyleSheet("")
            self.details_box.setTitle("")
            self.details_box.setProperty("analystMore", "true")
            self.primary_container.setProperty("analystRange", "true")
            self._range_form_label.setText("RANGE")
            title_row = self._primary_form.takeRow(self.title_edit)
            if title_row.labelItem is not None:
                self._title_form_label = title_row.labelItem.widget()
            if title_row.fieldItem is not None:
                self._analyst_title_form.addRow(
                    self._title_form_label, title_row.fieldItem.widget())
            self._move_widget(
                self.preview_container, self._form_layout,
                self._analyst_preview_layout)
            self._move_widget(
                self.details_box, self.details_section.body_layout,
                self.advanced_details_section.body_layout)
            self.advanced_details_section.body_layout.setContentsMargins(
                0, 2, 0, 0)
            self._export_form.removeWidget(self.reel_check)
            self._export_form.removeWidget(self.enabled_check)
            self._details_grid.removeWidget(self.autoname_btn)
            self._details_grid.removeWidget(self.details_to_tags_check)
            self._analyst_auto_layout.addWidget(self.autoname_btn)
            self._analyst_option_layout.addWidget(self.details_to_tags_check)
            self._analyst_option_layout.addWidget(self.enabled_check)
            self._analyst_option_layout.addWidget(self.reel_check)
            notes_row = self._metadata_form.takeRow(self.notes_edit)
            if notes_row.labelItem is not None:
                self._notes_form_label = notes_row.labelItem.widget()
            if notes_row.fieldItem is not None:
                self._notes_layout.addWidget(notes_row.fieldItem.widget())
            self.metadata_section.hide()
            self.export_section.hide()
            self.analyst_naming_container.show()
            self.summary_card.show()
            self.range_summary_label.hide()
            self.play_rail.show()
            self.edit_title_btn.show()
            self.analyst_ledger.show()
            self.analyst_left_column.hide()
            self.analyst_vertical_divider.hide()
            self.analyst_horizontal_divider.hide()
            self.notes_box.show()
            self.notes_box.setSizePolicy(
                QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
            self.advanced_details_section.show()
            self.advanced_details_section.set_expanded(False)
            self.details_section.set_expanded(False)
            self._form_layout.removeWidget(self.apply_btn)
            self._form_layout.removeWidget(self.quick_export_btn)
            for row in (self._action_button_row,
                        self._action_button_row_2):
                for button in (
                        self.quick_export_btn, self.package_btn,
                        self.apply_btn, self.save_next_btn):
                    row.removeWidget(button)
            # The two export scopes occupy one line and the two save scopes
            # occupy the next. This mirrors Option 4 and prevents either
            # selected-clip export from being mistaken for package/cut-up.
            self._action_button_row.addWidget(self.quick_export_btn, 1)
            self._action_button_row.addWidget(self.package_btn, 2)
            self._action_button_row_2.addWidget(self.apply_btn, 1)
            self._action_button_row_2.addWidget(self.save_next_btn, 2)
            for button in (
                    self.quick_export_btn, self.package_btn, self.apply_btn,
                    self.save_next_btn):
                button.setMinimumWidth(0)
                # Ignored, not Expanding: setMinimumWidth(0) does not beat
                # a button's own minimumSizeHint, so the bar kept its text
                # width and overflowed the panel instead of shrinking.
                button.setSizePolicy(
                    QSizePolicy.Policy.Ignored,
                    QSizePolicy.Policy.Fixed)
            self.apply_btn.setText("Save Play")
            self.apply_btn.setProperty("accent", "false")
            self.apply_btn.setProperty("quiet", "true")
            self.quick_export_btn.setProperty("quiet", "true")
            self.quick_export_btn.setText("Export Clip")
            self.package_btn.show()
            self.save_next_btn.show()
            self._refresh_widget_style(self.apply_btn)
            self._refresh_widget_style(self.quick_export_btn)
            self._update_analyst_ledger_direction(self.width())
        else:
            self.heading_label.setText("Clip Inspector")
            self.essentials_box.setTitle("GAME SITUATION")
            self.players_box.setTitle("PLAYERS AND OUTCOME")
            self.notes_box.setTitle("FILM NOTES")
            self.details_section.toggle.setText("Play details")
            self.details_section.toggle.setStyleSheet(
                "QToolButton { border: none; font-weight: 600; }")
            self.details_box.setTitle("Play Details")
            self.details_box.setProperty("analystMore", "false")
            self.primary_container.setProperty("analystRange", "false")
            self._range_form_label.setText("Range:")
            title_row = self._analyst_title_form.takeRow(self.title_edit)
            if title_row.labelItem is not None:
                self._title_form_label = title_row.labelItem.widget()
            if title_row.fieldItem is not None:
                self._primary_form.insertRow(
                    0, self._title_form_label, title_row.fieldItem.widget())
            self._move_widget(
                self.preview_container, self._analyst_preview_layout,
                self._form_layout,
                insert_at=max(1, self._form_layout.count() - 3))
            self._move_widget(
                self.details_box, self.advanced_details_section.body_layout,
                self.details_section.body_layout, insert_at=0)
            self._analyst_auto_layout.removeWidget(self.autoname_btn)
            self._analyst_option_layout.removeWidget(
                self.details_to_tags_check)
            self._analyst_option_layout.removeWidget(self.enabled_check)
            self._analyst_option_layout.removeWidget(self.reel_check)
            self._export_form.addRow("", self.reel_check)
            self._export_form.addRow("", self.enabled_check)
            self._notes_layout.removeWidget(self.notes_edit)
            self._metadata_form.addRow(
                self._notes_form_label, self.notes_edit)
            self.metadata_section.show()
            self.export_section.show()
            self.analyst_naming_container.hide()
            self.summary_card.hide()
            self.range_summary_label.show()
            self.play_rail.hide()
            self.edit_title_btn.hide()
            self.analyst_ledger.hide()
            self.analyst_left_column.show()
            self.classification_box.hide()
            self.players_box.hide()
            self.notes_box.hide()
            self.advanced_details_section.hide()
            self.advanced_details_section.set_expanded(False)
            self.apply_btn.setText("Apply changes")
            self.apply_btn.setProperty("accent", "true")
            self.apply_btn.setProperty("quiet", "false")
            self.quick_export_btn.setProperty("quiet", "false")
            self.save_next_btn.hide()
            self.package_btn.hide()
            for row in (self._action_button_row,
                        self._action_button_row_2):
                for button in (
                        self.quick_export_btn, self.package_btn,
                        self.apply_btn, self.save_next_btn):
                    row.removeWidget(button)
            self._refresh_widget_style(self.apply_btn)
            self._refresh_widget_style(self.quick_export_btn)
            insert_at = max(0, self._form_layout.count() - 1)
            self._form_layout.insertWidget(insert_at, self.apply_btn)
            self._form_layout.insertWidget(
                insert_at + 1, self.quick_export_btn)
        self.analyst_ledger.setVisible(
            enabled and self._clip is not None)
        self.essentials_box.hide()
        self.classification_box.hide()
        self.advanced_details_section.setVisible(
            enabled and self._clip is not None)
        self.action_bar.setVisible(enabled and self._clip is not None)
        self.apply_field_layout()
        self.apply_density(
            self.settings.inspector_density if self.settings
            else INSPECTOR_DENSITY_COMPACT)
        self.set_clip(self._clip)

    @staticmethod
    def _move_layout(child, source, destination) -> None:
        for index in range(source.count()):
            item = source.itemAt(index)
            if item is not None and item.layout() is child:
                source.takeAt(index)
                destination.addLayout(child)
                return

    @staticmethod
    def _move_widget(widget, source, destination,
                     insert_at: int | None = None) -> None:
        source.removeWidget(widget)
        if insert_at is None:
            destination.addWidget(widget)
        else:
            destination.insertWidget(insert_at, widget)

    @staticmethod
    def _refresh_widget_style(widget: QWidget) -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "analyst_ledger"):
            self._update_analyst_ledger_direction(event.size().width())
        if hasattr(self, "_action_button_row_2"):
            self._apply_narrow_mode(
                event.size().width() < self.NARROW_WIDTH)

    def _update_analyst_ledger_direction(self, width: int) -> None:
        """Keep People and Notes as the single everyday analyst column."""
        if self._analyst_mode:
            self._analyst_ledger_wide = False
            self._analyst_ledger_layout.setDirection(
                QBoxLayout.Direction.TopToBottom)
            self.analyst_left_column.hide()
            self.analyst_vertical_divider.hide()
            self.analyst_horizontal_divider.hide()
            self._analyst_ledger_layout.setSpacing(5)
            self._analyst_ledger_layout.setStretch(0, 0)
            self._analyst_ledger_layout.setStretch(1, 0)
            self._analyst_ledger_layout.setStretch(2, 0)
            # Keep People and Notes content-sized so the disclosures follow
            # Notes directly. The form's final stretch still owns all spare
            # height above the separately pinned action bar.
            self._analyst_ledger_layout.setStretch(3, 0)
            self.analyst_ledger.updateGeometry()
            return
        wide = int(width) >= 500
        if self._analyst_ledger_wide == wide:
            return
        self._analyst_ledger_wide = wide
        self._analyst_ledger_layout.setDirection(
            QBoxLayout.Direction.LeftToRight
            if wide else QBoxLayout.Direction.TopToBottom)
        self.analyst_vertical_divider.setVisible(wide)
        self.analyst_horizontal_divider.setVisible(not wide)
        self._analyst_ledger_layout.setSpacing(9 if wide else 5)
        self._analyst_ledger_layout.setStretch(0, 1 if wide else 0)
        self._analyst_ledger_layout.setStretch(1, 0)
        self._analyst_ledger_layout.setStretch(2, 0)
        self._analyst_ledger_layout.setStretch(3, 1 if wide else 0)
        self.analyst_ledger.updateGeometry()

    def _edit_title(self) -> None:
        self.details_section.set_expanded(True)
        self.title_edit.setFocus()
        self.title_edit.selectAll()

    def _mark_unsaved(self, *_args) -> None:
        if self._loading_clip or not self._analyst_mode or self._clip is None:
            return
        self._set_save_state("UNSAVED", "dirty")

    def _detail_value_changed(self, *_args) -> None:
        self._mark_unsaved()
        self._refresh_auto_name()
        self._refresh_analyst_summary()
        self._update_advanced_details_toggle_text()

    def _set_save_state(self, text: str, state: str = "saved") -> None:
        self.save_state_label.setText(text)
        self.save_state_label.setProperty("state", state)
        style = self.save_state_label.style()
        style.unpolish(self.save_state_label)
        style.polish(self.save_state_label)

    def _set_review_state(self, logged: bool) -> None:
        self.review_state_label.setText("LOGGED" if logged else "UNLOGGED")
        self.review_state_label.setProperty(
            "state", "logged" if logged else "unlogged")
        style = self.review_state_label.style()
        style.unpolish(self.review_state_label)
        style.polish(self.review_state_label)

    def toggle_details(self) -> bool:
        """Open or close Play details. Returns the new state.

        Opening also takes the cursor, because the only reason to open it is
        to type in it; closing hands focus back so transport keys work again.
        """
        opening = not self.details_section.is_expanded()
        self.details_section.set_expanded(opening)
        if opening:
            if self._analyst_mode:
                self.title_edit.setFocus()
                self.title_edit.selectAll()
            else:
                first = self._first_visible_detail()
                if first is not None:
                    first.setFocus()
        else:
            self.setFocus()
        return opening

    def details_are_open(self) -> bool:
        return self.details_section.is_expanded()

    def begin_review_entry(self) -> None:
        """Review mode landed on this clip - open details and take the cursor.

        The inspector sits beside the video, so typing here never covers what
        is playing.
        """
        if self._analyst_mode:
            self.details_section.set_expanded(False)
            first = self._first_visible_analyst_detail()
        else:
            self.details_section.set_expanded(True)
            first = self._first_visible_detail()
        if first is not None:
            first.setFocus()
            line = first.lineEdit()
            if line is not None:
                line.selectAll()
        elif self._analyst_mode:
            self.notes_edit.setFocus()

    #: Player fields take their list from the project's roster rather than
    #: from settings, because a squad is per project and changes yearly.
    PLAYER_FIELDS = ("player_name", "other_players")

    def set_player_options(self, options: list[str]) -> None:
        """Offer the roster in the player fields, numbers included."""
        self._player_options = list(options)
        self.apply_dropdown_mode()

    def apply_dropdown_mode(self) -> None:
        """Switch details/tags between fixed dropdowns and learned autocomplete."""
        s = self.settings
        fixed = bool(s and s.use_fixed_dropdowns)
        roster = list(getattr(self, "_player_options", ()) or ())
        for key, edit in self.detail_edits.items():
            if key in (*self.PLAYER_FIELDS, "quarterback"):
                configure_detail_edit(edit, key, s, roster=roster)
            else:
                configure_detail_edit(edit, key, s)
        self.add_tag_combo.setVisible(fixed)
        if fixed and s:
            self.add_tag_combo.set_fixed_tags(s.fixed_tags)
            self.tags_edit.set_known_tags(s.fixed_tags)

    def set_context(self, template: str, project_name: str, separator: str,
                    duration_ms: int) -> None:
        self._template = template
        self._project_name = project_name
        self._separator = separator
        self._duration_ms = duration_ms

    def set_clip(self, clip: Clip | None) -> None:
        self._loading_clip = True
        try:
            self._clip = clip
            self._automatic_name = bool(clip and clip.uses_auto_name)
            # Revealing a field is a choice about this play, not a setting.
            self._revealed_details = set()
            enabled = clip is not None
            # Empty state instead of a form full of dead fields.
            self.empty_state.setVisible(not enabled)
            self.form_area.setVisible(enabled)
            self.action_bar.setVisible(self._analyst_mode and enabled)
            visible_keys = set(self.visible_detail_keys())
            self.analyst_ledger.setVisible(
                self._analyst_mode and enabled)
            self.essentials_box.hide()
            self.classification_box.hide()
            self.players_box.setVisible(
                self._analyst_mode and enabled
                and bool(visible_keys & set(
                    ANALYST_DETAIL_GROUPS["players"])))
            self.notes_box.setVisible(self._analyst_mode and enabled)
            self.advanced_details_section.setVisible(
                self._analyst_mode and enabled)
            for w in (
                    self.title_edit, self.filename_edit, self.start_edit,
                    self.end_edit, self.label_edit, self.tags_edit,
                    self.notes_edit, self.preset_combo, self.reel_check,
                    self.enabled_check, self.apply_btn, self.save_next_btn,
                    self.quick_export_btn, self.package_btn, self.autoname_btn,
                    self.details_to_tags_check,
                    *self.detail_edits.values(),
                    *self.classification_buttons.values()):
                w.setEnabled(enabled)
            self.error_label.hide()
            if clip is None:
                for edit in (
                        self.title_edit, self.filename_edit, self.start_edit,
                        self.end_edit, self.label_edit, self.tags_edit,
                        *self.detail_edits.values()):
                    edit.clear()
                self.notes_edit.clear()
                self.preview_label.setText("Select a clip to edit it.")
            else:
                self.title_edit.setText(clip.clip_title)
                self.filename_edit.setText(clip.output_filename_base)
                self.start_edit.setText(
                    format_ms(clip.start_ms, show_millis=True))
                self.end_edit.setText(
                    format_ms(clip.end_ms, show_millis=True))
                self.label_edit.setText(clip.label)
                self.tags_edit.setText(", ".join(clip.tags))
                self.notes_edit.setPlainText(clip.notes)
                index = self.preset_combo.findData(clip.export_preset)
                self.preset_combo.setCurrentIndex(max(0, index))
                self.reel_check.setChecked(clip.include_in_reel)
                self.enabled_check.setChecked(clip.enabled)
                for key, edit in self.detail_edits.items():
                    edit.setText(clip.details.get(key, ""))
                self._refresh_preview()
                self._remember_player_names(clip.details)
        finally:
            self._loading_clip = False

        self._refresh_auto_name()
        self._refresh_first_read_advisory()

        if not self._analyst_mode:
            return
        if clip is None:
            self.heading_label.setText("PLAY DETAILS")
            self.review_state_label.clear()
            self.save_state_label.clear()
            self.title_summary_label.setText("Untitled play")
            self.situation_summary_label.clear()
            self.range_summary_label.clear()
            self._set_play_kind("other")
            for button in self.classification_buttons.values():
                button.setChecked(False)
            # With no clip open neither shape has anything to answer.
            self._sync_attribute_rows()
            self._sync_inspector_panels()
            return
        self.heading_label.setText(f"PLAY {clip.clip_number:03d}")
        logged = bool(clip.details)
        self._set_review_state(logged)
        self._set_save_state("SAVED")
        self._refresh_analyst_summary()
        # Which details are filled is only known once this clip's values are
        # in the edits, so the collapse runs here rather than in
        # apply_field_layout, which fires before any clip is loaded.
        self._sync_advanced_detail_visibility()
        self._sync_attribute_rows()
        self._sync_inspector_panels()
        self._update_advanced_details_toggle_text()
        self.quick_export_btn.setEnabled(bool(clip.enabled))
        self.quick_export_btn.setToolTip(
            "Export this clip immediately with its assigned preset (Ctrl+E)"
            if clip.enabled else
            "This clip is excluded from export. Enable it first.")

    def set_vocabulary(self, detail_values: dict[str, list[str]],
                       all_tags: list[str]) -> None:
        """Feed autocomplete with values already used in this project.

        In fixed-dropdown mode the fixed lists win; learned values still feed
        any detail field that has no fixed list of its own.
        """
        fixed = bool(self.settings and self.settings.use_fixed_dropdowns)
        if not fixed:
            self.tags_edit.set_known_tags(all_tags)
        player_variants = [
            value.strip()
            for value in detail_values.get("player_name", [])
            if value.strip()
        ]
        for value in detail_values.get("other_players", []):
            player_variants.extend(detail_service.split_players(value))
        player_names = list(
            tag_service.merge_player_variants(player_variants).values())
        self._remembered_player_names = player_names
        for key, edit in self.detail_edits.items():
            values = detail_values.get(key, [])
            if key == "player_name":
                values = player_names
            elif key == "other_players":
                values = list(values) + list(player_names)
                values = list(
                    tag_service.merge_player_variants(values).values())
            edit.set_known_values(values)
        self.set_result_choices(sorted(detail_values.get("result", [])))

    def apply_density(self, density: str) -> None:
        """Change inspector spacing without changing fields or metadata."""
        presets = {
            INSPECTOR_DENSITY_COMPACT: (8, 5, 5, 52),
            INSPECTOR_DENSITY_COMFORTABLE: (11, 8, 8, 68),
            INSPECTOR_DENSITY_SPACIOUS: (15, 12, 12, 88),
        }
        density = density if density in presets else INSPECTOR_DENSITY_COMPACT
        margin, primary_spacing, grid_spacing, notes_height = presets[density]
        self.setProperty("density", density)
        self._form_layout.setContentsMargins(margin, margin, margin, margin)
        self._form_layout.setSpacing(primary_spacing)
        self._primary_form.setHorizontalSpacing(primary_spacing + 3)
        self._primary_form.setVerticalSpacing(primary_spacing)
        self._details_grid.setHorizontalSpacing(grid_spacing)
        self._details_grid.setVerticalSpacing(grid_spacing)
        self._essentials_grid.setHorizontalSpacing(grid_spacing)
        self._essentials_grid.setVerticalSpacing(grid_spacing)
        self._classification_grid.setHorizontalSpacing(grid_spacing)
        self._classification_grid.setVerticalSpacing(grid_spacing)
        self._players_grid.setHorizontalSpacing(grid_spacing)
        self._players_grid.setVerticalSpacing(grid_spacing)
        analyst_margin = max(3, margin // 2)
        for group_layout in (
                self._essentials_grid, self._classification_grid,
                self._players_grid, self._notes_layout):
            group_layout.setContentsMargins(
                analyst_margin, analyst_margin,
                analyst_margin, analyst_margin)
        if self._analyst_mode:
            notes_height = {
                INSPECTOR_DENSITY_COMPACT: 64,
                INSPECTOR_DENSITY_COMFORTABLE: 84,
                INSPECTOR_DENSITY_SPACIOUS: 108,
            }[density]
            self.notes_edit.setMinimumHeight(notes_height)
        else:
            self.notes_edit.setMinimumHeight(0)
        # Density owns the collapsed height; the expand toggle raises the cap
        # above it. Setting the maximum directly here would undo the toggle.
        self._notes_density_height = notes_height
        self._notes_expanded_toggled(
            self._analyst_mode and self.notes_expand_btn.isChecked())
        style = self.style()
        style.unpolish(self)
        style.polish(self)
        self.update()

    def apply_field_layout(self) -> None:
        """Apply Settings-driven field order, labels, and visibility.

        This only rearranges editor widgets. Hidden values remain loaded and
        saved with the clip, so changing the layout cannot discard metadata.
        """
        defaults = dict(DETAIL_FIELDS)
        known = list(defaults)
        requested = list(self.settings.detail_field_order) \
            if self.settings else []
        order = [key for key in requested if key in defaults]
        order.extend(key for key in known if key not in order)
        hidden = {
            key for key in (
                self.settings.hidden_detail_fields if self.settings else [])
            if key in defaults
        }
        labels = self.settings.detail_field_labels if self.settings else {}

        for key in known:
            cell = self.detail_cells[key]
            self._details_grid.removeWidget(cell)
            self._essentials_grid.removeWidget(cell)
            self._classification_grid.removeWidget(cell)
            self._players_grid.removeWidget(cell)
            analyst_defaults = {
                "player_name": "Primary Player",
                "other_players": "Involved",
                "result": "Result",
            }
            display = labels.get(key, "").strip() or (
                analyst_defaults.get(key, defaults[key])
                if self._analyst_mode else defaults[key])
            self.detail_labels[key].setText(display)
            self.detail_edits[key].setAccessibleName(display)
            self.detail_edits[key].setToolTip(
                f"{display} - autocompletes from values you've used before")
            cell.setVisible(
                key not in hidden
                and not (self._analyst_mode and self._chips_own(key)))

        self._detail_order = order
        self._hidden_detail_fields = hidden
        visible = [key for key in order if key not in hidden]
        grouped: set[str] = set()
        ledger_positions: dict[str, int] = {}
        # Two fields to a line. Quarter, down and ball-on hold a handful of
        # characters each, so a full-width row per field spent most of the
        # panel on whitespace and pushed Notes below the fold.
        analyst_layouts = (
            ("players", self._players_grid, self.players_box, 2, 0),
        )
        if self._analyst_mode:
            for name, target_grid, box, columns, row_offset in analyst_layouts:
                group_keys = [
                    key for key in visible
                    if key in ANALYST_DETAIL_GROUPS[name]
                    and not self._chips_own(key)
                ]
                grouped.update(group_keys)
                for index, key in enumerate(group_keys):
                    row, col = divmod(index, columns)
                    ledger_positions[key] = index
                    target_grid.addWidget(
                        self.detail_cells[key], row + row_offset, col)
                box.setVisible(bool(group_keys) and self._clip is not None)
        else:
            for _name, _grid, box, _columns, _row_offset in (
                    ("situation", self._essentials_grid,
                     self.essentials_box, 2, 0),
                    ("classification", self._classification_grid,
                     self.classification_box, 2, 1),
                    *analyst_layouts):
                box.hide()
        self.essentials_box.hide()
        self.classification_box.hide()
        secondary = [
            key for key in visible
            if key not in grouped
            and (not self._analyst_mode or not self._chips_own(key))
        ]
        for key in known:
            in_ledger = self._analyst_mode and key in grouped
            in_advanced = self._analyst_mode and key in secondary
            cell_layout = self.detail_cell_layouts[key]
            cell_layout.setDirection(
                QBoxLayout.Direction.LeftToRight
                if in_advanced else QBoxLayout.Direction.TopToBottom)
            if in_ledger:
                cell_layout.setContentsMargins(3, 1, 3, 1)
            elif in_advanced:
                cell_layout.setContentsMargins(0, 1, 0, 1)
            else:
                cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(6 if in_advanced else (
                2 if in_ledger else 0))
            cell_layout.setStretch(0, 0)
            cell_layout.setStretch(
                1, 1 if in_ledger or in_advanced else 0)

            label = self.detail_labels[key]
            label.setProperty(
                "role", "ledgerLabel" if in_ledger else (
                    "advancedLabel" if in_advanced else "subtle"))
            label.setMinimumWidth(92 if in_advanced else 0)
            cell = self.detail_cells[key]
            cell.setProperty("ledgerRow", "true" if in_ledger else "false")
            cell.setProperty(
                "ledgerStripe",
                "true" if in_ledger
                and ledger_positions.get(key, 0) % 2 == 0 else "false")
            cell.setProperty(
                "advancedRow", "true" if in_advanced else "false")
            edit = self.detail_edits[key]
            edit.setProperty(
                "ledgerField", "true" if in_ledger else "false")
            edit.setProperty(
                "advancedField", "true" if in_advanced else "false")
            line = edit.lineEdit()
            if line is not None:
                line.setAlignment(
                    Qt.AlignmentFlag.AlignLeft
                    if key in ANALYST_DETAIL_GROUPS["players"]
                    or not in_ledger
                    else Qt.AlignmentFlag.AlignRight)
            for widget in (cell, label, edit):
                self._refresh_widget_style(widget)
        for index, key in enumerate(secondary):
            if self._analyst_mode:
                self._details_grid.addWidget(
                    self.detail_cells[key], index, 0, 1, 2)
            else:
                row, col = divmod(index, 2)
                self._details_grid.addWidget(
                    self.detail_cells[key], row, col)

        self._details_grid.removeWidget(self.autoname_btn)
        self._details_grid.removeWidget(self.details_to_tags_check)
        self._analyst_auto_layout.removeWidget(self.autoname_btn)
        self._analyst_option_layout.removeWidget(
            self.details_to_tags_check)
        if self._analyst_mode:
            self._analyst_auto_layout.addWidget(self.autoname_btn)
            self._analyst_option_layout.insertWidget(
                0, self.details_to_tags_check)
        else:
            auto_row = (len(secondary) + 1) // 2
            self._details_grid.addWidget(self.autoname_btn, auto_row, 0)
            self._details_grid.addWidget(
                self.details_to_tags_check, auto_row, 1)
        self.notes_box.setVisible(
            self._analyst_mode and self._clip is not None)
        self.notes_expand_btn.setVisible(self._analyst_mode)
        self._sync_advanced_detail_visibility()
        self._sync_attribute_rows()
        self._sync_inspector_panels()
        self._update_advanced_details_toggle_text()

    def visible_detail_keys(self) -> list[str]:
        """Visible Play Details keys in their current display order."""
        return [
            key for key in self._detail_order
            if key not in self._hidden_detail_fields
        ]

    def _first_visible_detail(self) -> DetailEdit | None:
        visible = self.visible_detail_keys()
        return self.detail_edits[visible[0]] if visible else None

    def _first_visible_analyst_detail(self) -> DetailEdit | None:
        visible = set(self.visible_detail_keys())
        for keys in ANALYST_DETAIL_GROUPS.values():
            for key in keys:
                if key in visible:
                    return self.detail_edits[key]
        return None

    def _collect_details(self) -> dict[str, str]:
        # Unknown fields belong to the project, not to this editor's schema.
        # Read the live clip so explicitly removed keys cannot be resurrected.
        details = {
            key: value
            for key, value in (self._clip.details if self._clip else {}).items()
            if key not in self.detail_edits
        }
        details.update({
            key: edit.text().strip()
            for key, edit in self.detail_edits.items()
            if edit.text().strip()
        })
        # The roster offers "4 Mark Fletcher Jr." so a number can be typed
        # or picked, but the number is how you find him, not his name. Left
        # in, every player would exist twice - once with a number glued on.
        for key in self.PLAYER_FIELDS:
            if key in details:
                details[key] = strip_jersey_number(
                    details[key], getattr(self, "_player_options", ()) or ())
        for key in ("result", "action"):
            if key in details:
                details[key] = result_service.join_results(
                    result_service.split_results(details[key]))
        return details

    def _auto_name(self) -> None:
        self.error_label.hide()
        self.filename_edit.clear()  # filename re-derives from the new title
        self._automatic_name = True
        self._refresh_auto_name()
        self._mark_unsaved()

    def _name_edited(self, *_args) -> None:
        if self._loading_clip or self._updating_name:
            return
        self._automatic_name = not (
            self.title_edit.text().strip() or self.filename_edit.text().strip())

    def _refresh_auto_name(self, *_args) -> None:
        if self._loading_clip or self._clip is None or self._updating_name:
            return
        if self._automatic_name:
            self._updating_name = True
            try:
                details = self._collect_details()
                tags = detail_service.sync_detail_tags(
                    [t.strip() for t in self.tags_edit.text().split(",") if t.strip()],
                    self._clip.details, details)
                self.title_edit.setText(detail_service.compose_auto_name(details, tags))
            finally:
                self._updating_name = False
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if self._clip is None:
            return
        probe = self._clip.copy_as_new()
        probe.clip_number = self._clip.clip_number
        probe.clip_title = self.title_edit.text()
        probe.output_filename_base = self.filename_edit.text()
        probe.tags = [t.strip() for t in self.tags_edit.text().split(",") if t.strip()]
        name = filename_service.render_template(
            self._template, probe, self._project_name, self._separator)
        self.preview_label.setText(name + ".mp4")
        self._update_details_toggle_text()

    def _update_details_toggle_text(self, *_args) -> None:
        if not self._analyst_mode:
            self.details_section.toggle.setText("Play details")
            return
        self.details_section.toggle.setText("Naming Export")

    def _advanced_detail_keys(self) -> list[str]:
        """Fields kept behind Additional Play Details in analyst mode."""
        player_keys = set(ANALYST_DETAIL_GROUPS["players"])
        return [
            key for key in self.visible_detail_keys()
            if key not in player_keys and key not in CHIP_DRIVEN_FIELDS
        ]

    NOTES_HEIGHT_EXPANDED = 168

    def _notes_expanded_toggled(self, expanded: bool) -> None:
        collapsed = getattr(self, "_notes_density_height", 64)
        self.notes_edit.setMaximumHeight(
            self.NOTES_HEIGHT_EXPANDED if expanded else collapsed)
        self.notes_expand_btn.setText("Collapse" if expanded else "Expand")

    def _sync_advanced_detail_visibility(self) -> None:
        """Show filled details; offer the empty ones as add chips."""
        if not hasattr(self, "_advanced_add_row"):
            return
        keys = self._advanced_detail_keys() if self._analyst_mode else []
        empty: list[str] = []
        for key in keys:
            filled = bool(self.detail_edits[key].text().strip())
            show = filled or key in self._revealed_details
            self.detail_cells[key].setVisible(show)
            if not show:
                empty.append(key)

        while self._advanced_add_layout.count():
            item = self._advanced_add_layout.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        for key in empty:
            label = self.detail_labels[key].text()
            chip = QPushButton(f"+ {label}")
            chip.setProperty("detailAdd", "true")
            chip.setToolTip(f"Add {label} to this play")
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.clicked.connect(
                lambda _checked=False, field=key: self._reveal_detail(field))
            self._advanced_add_layout.addWidget(chip)
        self._advanced_add_row.setVisible(bool(empty) and self._analyst_mode)

    def _reveal_detail(self, key: str) -> None:
        """Turn an add chip into its field and put the cursor in it."""
        self._revealed_details.add(key)
        self._sync_advanced_detail_visibility()
        edit = self.detail_edits.get(key)
        if edit is not None:
            edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _update_advanced_details_toggle_text(self, *_args) -> None:
        title = "Additional Play Details"
        if self._analyst_mode:
            keys = self._advanced_detail_keys()
            filled = sum(
                bool(self.detail_edits[key].text().strip())
                for key in keys)
            if keys:
                title = f"{title}    {filled} of {len(keys)}"
        self.advanced_details_section.toggle.setText(title)

    def _remember_player_names(self, details: dict[str, str]) -> None:
        """Make saved project players available on the very next clip."""
        variants = list(self._remembered_player_names)
        primary = details.get("player_name", "").strip()
        if details.get("quarterback", "").strip():
            variants.append(details["quarterback"].strip())
        if primary:
            variants.append(primary)
        variants.extend(
            detail_service.split_players(details.get("other_players", "")))
        self._remembered_player_names = list(
            tag_service.merge_player_variants(variants).values())
        values = sorted(self._remembered_player_names, key=str.casefold)
        for key in ANALYST_DETAIL_GROUPS["players"]:
            self.detail_edits[key].set_known_values(values)

    def _refresh_first_read_advisory(self) -> None:
        presentation = presentation_for(self._clip)
        if presentation is None:
            self.first_read_chip.clear()
            self.first_read_message.clear()
            self.first_read_card.hide()
            return
        self.first_read_card.setProperty("state", presentation.state)
        self.first_read_chip.setText(presentation.chip_text)
        self.first_read_chip.setVisible(bool(presentation.chip_text))
        self.first_read_message.setText(presentation.message)
        self.first_read_card.show()
        style = self.first_read_card.style()
        style.unpolish(self.first_read_card)
        style.polish(self.first_read_card)
        self.first_read_card.update()

    def _refresh_analyst_summary(self, *_args) -> None:
        if not self._analyst_mode or self._clip is None:
            return
        details = self._collect_details()
        title = self.title_edit.text().strip()
        if not title:
            title = detail_service.compose_clip_name(details) or "Untitled play"
        self.title_summary_label.setText(title)
        rush = getattr(self.settings, "explosive_rush_yards", DEFAULT_EXPLOSIVE_RUSH_YARDS)
        passing = getattr(self.settings, "explosive_pass_yards", DEFAULT_EXPLOSIVE_PASS_YARDS)
        family = lookup("run_pass", details.get("run_pass"))
        explosive = (is_explosive(details, rush, passing)
                     if parse_yards(details) is not None and family
                     and family.canonical in {"Run", "Pass"} else None)
        labels = {True: "Yes", False: "No", None: "Unknown"}
        self.summary_card.setToolTip(
            f"Derived from recorded details\nExplosive: {labels[explosive]} "
            f"(Run {rush}+ / Pass {passing}+ yards)\n"
            f"Successful: {labels[is_successful(details)]}")

        situation = []
        quarter = details.get("quarter", "").strip()
        if quarter:
            situation.append(
                quarter.upper() if quarter.casefold().startswith("q")
                else f"Q{quarter}")
        down_distance = details.get("down_distance", "").strip()
        if down_distance:
            situation.append(down_distance)
        ball_on = details.get("ball_on", "").strip()
        if ball_on:
            situation.append(f"BALL ON {ball_on}")
        family = details.get("run_pass", "").strip()
        if family:
            situation.append(family.upper())
        result = details.get("result", "").strip()
        if result:
            situation.append(result.upper())
        self.situation_summary_label.setText("  |  ".join(situation))
        self.situation_summary_label.setVisible(bool(situation))
        self._sync_picker_state()

        try:
            start_ms, end_ms = parse_range(
                self.start_edit.text(), self.end_edit.text())
        except TapeSiftError:
            start_ms, end_ms = self._clip.start_ms, self._clip.end_ms
        duration_s = max(0, end_ms - start_ms) / 1000
        self.range_duration_label.setText(f"{duration_s:.1f}s")
        timing = (
            f"{format_ms(start_ms, show_millis=True)} to "
            f"{format_ms(end_ms, show_millis=True)} | {duration_s:.1f}s")
        player = details.get("player_name", "").strip()
        self.range_summary_label.setText(
            f"{player} | {timing}" if player else timing)
        self._set_play_kind(self._play_kind(details))
        family_entry = lookup("run_pass", details.get("run_pass"))
        concept_entry = lookup("play_type", details.get("play_type"))
        action_entry = lookup("play_action", details.get("play_action"))
        run_pass = family_entry.canonical.casefold() if family_entry else ""
        play_type = concept_entry.canonical.casefold() if concept_entry else ""
        play_action = action_entry.canonical.casefold() if action_entry else ""
        result = details.get("result", "")
        active = {
            "run": run_pass == "run" and play_type not in {
                "rpo", "scramble"},
            "pass": run_pass == "pass" and play_type != "rpo",
            "screen": play_type == "screen",
            "rpo_run": play_type == "rpo" and run_pass == "run",
            "rpo_pass": play_type == "rpo" and run_pass == "pass",
            "play_action": play_action == "play action",
            "scramble": play_type == "scramble",
            "sack": result_service.has_result(result, "Sack"),
            "special": run_pass == "special",
            "no_play": run_pass == "no play",
        }
        for key, button in self.classification_buttons.items():
            button.blockSignals(True)
            button.setChecked(active[key])
            button.blockSignals(False)

    @staticmethod
    def _play_kind(details: dict[str, str]) -> str:
        results = [entry for value in result_service.split_results(details.get("result", ""))
                   if (entry := lookup("result", value)) is not None]
        if any(entry.category == "Penalty" for entry in results):
            return "penalty"
        concept = lookup("play_type", details.get("play_type"))
        if concept and concept.canonical == "RPO":
            return "rpo"
        for value in ("Touchdown", "Interception", "Sack"):
            if any(entry.canonical == value for entry in results):
                return value.casefold()
        family = lookup("run_pass", details.get("run_pass"))
        if family and family.canonical in {"Run", "Pass"}:
            return family.canonical.casefold()
        if concept and concept.canonical == "Screen":
            return "screen"
        return "other"

    def _set_play_kind(self, kind: str) -> None:
        self.play_rail.setProperty("playKind", kind)
        style = self.play_rail.style()
        style.unpolish(self.play_rail)
        style.polish(self.play_rail)
        self.play_rail.update()

    def _apply(self) -> bool:
        if self._clip is None:
            return False
        yards = self.detail_edits["yards"].text().strip()
        if yards != self._clip.details.get("yards", "") and yards and parse_yards(yards) is None:
            self.error_label.setText("Use whole yards, including negative gains, or leave Yards blank.")
            self.error_label.show()
            return False
        yac = self.detail_edits["yac"].text().strip()
        if yac != self._clip.details.get("yac", "") and yac and parse_yards(yac) is None:
            self.error_label.setText("Use whole yards after catch, including negative yards, or leave YAC blank.")
            self.error_label.show()
            return False
        try:
            start_ms, end_ms = parse_range(self.start_edit.text(), self.end_edit.text())
        except TapeSiftError as exc:
            self.error_label.setText(exc.user_text())
            self.error_label.show()
            return False
        if self._duration_ms and start_ms >= self._duration_ms:
            self.error_label.setText(
                "The start time is beyond the end of the source video.")
            self.error_label.show()
            return False
        self.error_label.hide()
        clip = self._clip
        # MainWindow uses this signal to capture the undo checkpoint while the
        # clip still contains its original values. Emitting after the mutation
        # made metadata Undo restore the already-edited state.
        self.edit_started.emit(clip.id)
        previous_details = dict(clip.details)
        clip.details = self._collect_details()
        self._remember_player_names(clip.details)
        clip.clip_title = self.title_edit.text().strip()
        clip.output_filename_base = filename_service.sanitize_filename_base(
            self.filename_edit.text(), self._separator)
        self.filename_edit.setText(clip.output_filename_base)
        clip.start_ms = start_ms
        clip.end_ms = min(end_ms, self._duration_ms) if self._duration_ms else end_ms
        clip.label = self.label_edit.text().strip()
        clip.tags = detail_service.sync_detail_tags(
            [t.strip() for t in self.tags_edit.text().split(",") if t.strip()],
            previous_details, clip.details)
        self.tags_edit.setText(", ".join(clip.tags))
        if self.details_to_tags_check.isChecked() and clip.details:
            clip.tags = detail_service.merge_tags(
                clip.tags, detail_service.details_to_tags(clip.details))
            self.tags_edit.setText(", ".join(clip.tags))
        clip.notes = self.notes_edit.toPlainText()
        clip.generated_title = clip.clip_title if self._automatic_name else None
        detail_service.refresh_generated_title(clip)
        self.title_edit.setText(clip.clip_title)
        self._automatic_name = clip.uses_auto_name
        clip.export_preset = self.preset_combo.currentData() or ""
        clip.include_in_reel = self.reel_check.isChecked()
        clip.enabled = self.enabled_check.isChecked()
        clip.touch()
        self._refresh_preview()
        self.clip_edited.emit(clip.id)
        if self._analyst_mode:
            self._set_review_state(bool(clip.details))
            self._set_save_state("SAVED")
            self._refresh_analyst_summary()
        return True
