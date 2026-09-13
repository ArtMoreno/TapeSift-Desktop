"""Settings dialog with General / Playback / Clip Defaults / Export / FFmpeg tabs."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from tapesift.core import paths
from tapesift.core.config import (
    AppSettings, DETAILS_AFTER_OUT, DETAILS_AT_IN, DETAILS_OFF,
    INSPECTOR_DENSITY_COMFORTABLE, INSPECTOR_DENSITY_COMPACT,
    INSPECTOR_DENSITY_SPACIOUS,
    SEPARATOR_HYPHEN, SEPARATOR_UNDERSCORE,
)
from tapesift.models.export_settings import BUILTIN_PRESETS
from tapesift.services import ffmpeg_service
from tapesift.services.detail_service import DETAIL_FIELDS
from tapesift.services.first_read_service import DEFAULT_MODEL
from tapesift.ui.dialog_components import DialogHeader


class FixedListsDialog(QDialog):
    """Edit the fixed dropdown values: one list per field, one value per line."""

    TAGS_KEY = "__tags__"

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("tapesiftDialog", True)
        self.settings = settings
        self.setWindowTitle("Fixed Dropdown Lists")
        self.setMinimumSize(560, 540)
        # Working copy so Cancel discards edits.
        self._lists: dict[str, list[str]] = {
            self.TAGS_KEY: list(settings.fixed_tags),
            **{key: list(values) for key, values in settings.fixed_details.items()},
        }
        self._current_key = self.TAGS_KEY

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(DialogHeader(
            "Fixed dropdown lists",
            "Keep football terminology consistent while you log clips.",
            eyebrow="SETTINGS  /  CLIP DEFAULTS",
            parent=self,
        ))
        self.field_combo = QComboBox()
        self.field_combo.addItem("Tags", self.TAGS_KEY)
        for key, label in DETAIL_FIELDS:
            self.field_combo.addItem(label, key)
        self.field_combo.currentIndexChanged.connect(self._field_changed)
        layout.addWidget(self.field_combo)

        hint = QLabel("One value per line. Leave a field's list empty to keep "
                      "free typing (with autocomplete) for that field.")
        hint.setProperty("role", "subtle")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.values_edit = QPlainTextEdit()
        layout.addWidget(self.values_edit, 1)
        self._load_current()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save lists")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", "true")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_current(self) -> None:
        self.values_edit.setPlainText(
            "\n".join(self._lists.get(self._current_key, [])))

    def _store_current(self) -> None:
        values = [line.strip() for line in
                  self.values_edit.toPlainText().splitlines() if line.strip()]
        self._lists[self._current_key] = values

    def _field_changed(self) -> None:
        self._store_current()
        self._current_key = self.field_combo.currentData()
        self._load_current()

    def _save(self) -> None:
        self._store_current()
        from tapesift.services.football_vocab import lookup, values_for, VOCAB_FIELDS

        self.settings.hidden_fixed_details = {}
        for key in VOCAB_FIELDS:
            present = {item.canonical for value in self._lists.get(key, [])
                       if (item := lookup(key, value)) is not None}
            hidden = [value for value in values_for(key) if value not in present]
            if hidden:
                self.settings.hidden_fixed_details[key] = hidden
        self.settings.fixed_tags = self._lists.pop(self.TAGS_KEY)
        self.settings.fixed_details = {
            key: values for key, values in self._lists.items() if values}
        self.settings.save()
        self.accept()


class DetailFieldLayoutDialog(QDialog):
    """Reorder, hide, and relabel built-in fields without changing keys."""

    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("tapesiftDialog", True)
        self.settings = settings
        self.defaults = dict(DETAIL_FIELDS)
        self.setWindowTitle("Customize Clip Details")
        self.setMinimumSize(600, 650)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(DialogHeader(
            "Customize Clip Details",
            "Drag fields into your preferred order, uncheck fields you do "
            "not use, and change the labels you see in the inspector.",
            eyebrow="SETTINGS  /  CLIP DETAILS",
            parent=self,
        ))

        safe_note = QLabel(
            "This changes the interface only. Hidden fields and existing "
            "values remain in every clip.")
        safe_note.setProperty("role", "subtle")
        safe_note.setWordWrap(True)
        layout.addWidget(safe_note)

        self.fields_list = QListWidget()
        self.fields_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove)
        self.fields_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.fields_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.fields_list, 1)

        move_row = QHBoxLayout()
        self.up_btn = QPushButton("Move Up")
        self.down_btn = QPushButton("Move Down")
        self.up_btn.clicked.connect(lambda: self._move_current(-1))
        self.down_btn.clicked.connect(lambda: self._move_current(1))
        move_row.addWidget(self.up_btn)
        move_row.addWidget(self.down_btn)
        move_row.addStretch()
        layout.addLayout(move_row)

        label_form = QFormLayout()
        self.label_edit = QLineEdit()
        self.label_edit.setPlaceholderText("Visible label")
        self.label_edit.editingFinished.connect(self._commit_current_label)
        self.key_label = QLabel("")
        self.key_label.setProperty("role", "subtle")
        label_form.addRow("Display label:", self.label_edit)
        label_form.addRow("Internal key:", self.key_label)
        layout.addLayout(label_form)

        footer = QHBoxLayout()
        reset_btn = QPushButton("Restore Defaults")
        reset_btn.clicked.connect(self._restore_defaults)
        footer.addWidget(reset_btn)
        footer.addStretch()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            "Save layout")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty(
            "primary", "true")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)

        self.fields_list.currentItemChanged.connect(self._current_changed)
        self._populate()

    def _normalized_order(self) -> list[str]:
        known = list(self.defaults)
        order = [
            key for key in self.settings.detail_field_order
            if key in self.defaults
        ]
        order.extend(key for key in known if key not in order)
        return order

    def _populate(self, defaults: bool = False) -> None:
        self.fields_list.clear()
        hidden = set() if defaults else set(
            self.settings.hidden_detail_fields)
        labels = {} if defaults else self.settings.detail_field_labels
        order = list(self.defaults) if defaults else self._normalized_order()
        for key in order:
            item = QListWidgetItem(
                labels.get(key, "").strip() or self.defaults[key])
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setFlags(
                item.flags() |
                Qt.ItemFlag.ItemIsUserCheckable |
                Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(
                Qt.CheckState.Unchecked if key in hidden
                else Qt.CheckState.Checked)
            item.setToolTip(
                "Checked fields appear in Clip Details. Drag to reorder.")
            self.fields_list.addItem(item)
        if self.fields_list.count():
            self.fields_list.setCurrentRow(0)

    def _current_changed(
            self, current: QListWidgetItem | None,
            previous: QListWidgetItem | None) -> None:
        if previous is not None:
            self._set_item_label(previous, self.label_edit.text())
        if current is None:
            self.label_edit.clear()
            self.key_label.clear()
            return
        self.label_edit.setText(current.text())
        self.key_label.setText(current.data(Qt.ItemDataRole.UserRole))

    def _set_item_label(self, item: QListWidgetItem, value: str) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        item.setText(value.strip() or self.defaults[key])

    def _commit_current_label(self) -> None:
        item = self.fields_list.currentItem()
        if item is not None:
            self._set_item_label(item, self.label_edit.text())
            self.label_edit.setText(item.text())

    def _move_current(self, offset: int) -> None:
        self._commit_current_label()
        row = self.fields_list.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.fields_list.count():
            return
        item = self.fields_list.takeItem(row)
        self.fields_list.insertItem(target, item)
        self.fields_list.setCurrentItem(item)

    def _restore_defaults(self) -> None:
        self._populate(defaults=True)

    def _save(self) -> None:
        self._commit_current_label()
        order: list[str] = []
        hidden: list[str] = []
        labels: dict[str, str] = {}
        for row in range(self.fields_list.count()):
            item = self.fields_list.item(row)
            key = item.data(Qt.ItemDataRole.UserRole)
            order.append(key)
            if item.checkState() != Qt.CheckState.Checked:
                hidden.append(key)
            if item.text() != self.defaults[key]:
                labels[key] = item.text()
        self.settings.detail_field_order = order
        self.settings.hidden_detail_fields = hidden
        self.settings.detail_field_labels = labels
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setProperty("tapesiftDialog", True)
        self.settings = settings
        self.setWindowTitle("TapeSift Settings")
        self.setMinimumSize(760, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(DialogHeader(
            "Settings",
            "Tune playback, First Read, clip defaults, exports, and local media tools.",
            eyebrow="TAPESIFT  /  PREFERENCES",
            badge="LOCAL",
            parent=self,
        ))
        tabs = QTabWidget()
        tabs.setObjectName("SettingsTabs")
        tabs.setDocumentMode(True)
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._playback_tab(), "Playback")
        tabs.addTab(self._first_read_tab(), "First Read")
        tabs.addTab(self._clip_tab(), "Clip Defaults")
        tabs.addTab(self._export_tab(), "Export")
        tabs.addTab(self._ffmpeg_tab(), "FFmpeg")
        for index in range(tabs.count()):
            page = tabs.widget(index)
            page.setProperty("settingsPage", "true")
            page_layout = page.layout()
            if page_layout is not None:
                page_layout.setContentsMargins(20, 18, 20, 18)
                page_layout.setSpacing(11)
        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save settings")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", "true")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _folder_row(self, edit: QLineEdit) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        browse = QPushButton("Browse…")

        def pick() -> None:
            folder = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text())
            if folder:
                edit.setText(folder)

        browse.clicked.connect(pick)
        h.addWidget(browse)
        return w

    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        s = self.settings
        self.project_folder_edit = QLineEdit(s.default_project_folder)
        self.output_folder_edit = QLineEdit(s.default_output_folder)
        self.autosave_spin = QSpinBox()
        self.autosave_spin.setRange(5, 600)
        self.autosave_spin.setSuffix(" s")
        self.autosave_spin.setValue(s.autosave_interval_seconds)
        self.recent_spin = QSpinBox()
        self.recent_spin.setRange(1, 30)
        self.recent_spin.setValue(s.recent_project_count)
        # No Theme control. It offered Dark/Light and stored the answer, and
        # nothing ever read it: ui_v2.theme.stylesheet() takes no argument
        # and there is one palette. Picking Light did nothing at all, and a
        # control that lies is worse than a missing one. A real light theme
        # needs the ~100 hardcoded colours in theme.py moved into tokens.py
        # first; when that lands, this row comes back.
        self.confirm_delete_check = QCheckBox()
        self.confirm_delete_check.setChecked(s.confirm_before_delete)
        form.addRow("Default project folder:", self._folder_row(self.project_folder_edit))
        form.addRow("Default output folder:", self._folder_row(self.output_folder_edit))
        form.addRow("Autosave interval:", self.autosave_spin)
        form.addRow("Recent projects kept:", self.recent_spin)
        form.addRow("Confirm before delete:", self.confirm_delete_check)
        return w

    def _playback_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        s = self.settings
        self.jump_back_spin = QDoubleSpinBox()
        self.jump_back_spin.setRange(0.5, 120)
        self.jump_back_spin.setSuffix(" s")
        self.jump_back_spin.setValue(s.jump_backward_seconds)
        self.jump_fwd_spin = QDoubleSpinBox()
        self.jump_fwd_spin.setRange(0.5, 120)
        self.jump_fwd_spin.setSuffix(" s")
        self.jump_fwd_spin.setValue(s.jump_forward_seconds)
        self.speed_combo = QComboBox()
        for speed in (0.25, 0.5, 1.0, 1.5, 2.0):
            self.speed_combo.addItem(f"{speed:g}x", speed)
        index = self.speed_combo.findData(s.default_playback_speed)
        self.speed_combo.setCurrentIndex(max(0, index))
        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(0, 100)
        self.volume_spin.setValue(s.volume)
        self.proxy_check = QCheckBox("Automatically build smooth-scrub previews")
        self.proxy_check.setChecked(s.scrub_proxy_enabled)
        self.proxy_check.setToolTip(
            "Builds a lightweight preview when a video loads, without asking. "
            "Existing previews are reused. Uses processing time and disk space; "
            "turn off for manual builds. Exports use the original file.")
        form.addRow("Jump backward:", self.jump_back_spin)
        form.addRow("Jump forward:", self.jump_fwd_spin)
        form.addRow("Default playback speed:", self.speed_combo)
        form.addRow("Volume:", self.volume_spin)
        form.addRow("Smooth scrubbing:", self.proxy_check)
        hint = QLabel("Frame stepping uses the source frame rate automatically.")
        hint.setProperty("role", "subtle")
        form.addRow(hint)
        return w

    def _first_read_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setSpacing(12)
        s = self.settings

        self.first_read_enabled_check = QCheckBox("Enable First Read")
        self.first_read_enabled_check.setChecked(s.first_read_enabled)
        self.first_read_enabled_check.setToolTip(
            "Allow the First Read command to send still-frame contact sheets "
            "to your configured vision provider.")
        layout.addWidget(self.first_read_enabled_check)

        form = QFormLayout()
        self.first_read_api_key_edit = QLineEdit(s.first_read_api_key)
        self.first_read_api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.first_read_api_key_edit.setPlaceholderText("OpenRouter API key")
        self.first_read_api_key_edit.setAccessibleName("First Read API key")
        self.first_read_model_edit = QLineEdit(s.first_read_model)
        self.first_read_model_edit.setPlaceholderText(DEFAULT_MODEL)
        self.first_read_model_edit.setAccessibleName("First Read model")
        self.first_read_model_edit.setToolTip(
            "Leave empty to use TapeSift's measured default model.")
        form.addRow("API key:", self.first_read_api_key_edit)
        form.addRow("Model:", self.first_read_model_edit)
        layout.addLayout(form)

        security = QLabel(
            "Play detection stays fully offline; only First Read contacts "
            "the network, and only when you run it.")
        security.setObjectName("FirstReadSecurityPromise")
        security.setProperty("role", "subtle")
        security.setWordWrap(True)
        layout.addWidget(security)

        analyst_note = QLabel(
            "First Read never fills Run / Pass for you. Its suggestion stays "
            "beside the film until you press R or P to make the analyst call.")
        analyst_note.setObjectName("FirstReadAnalystPromise")
        analyst_note.setProperty("role", "subtle")
        analyst_note.setWordWrap(True)
        layout.addWidget(analyst_note)
        layout.addStretch()

        self.first_read_enabled_check.toggled.connect(
            self._sync_first_read_fields)
        self._sync_first_read_fields()
        return w

    def _sync_first_read_fields(self) -> None:
        enabled = self.first_read_enabled_check.isChecked()
        self.first_read_api_key_edit.setEnabled(enabled)
        self.first_read_model_edit.setEnabled(enabled)

    def _clip_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        s = self.settings
        self.pre_roll_spin = QDoubleSpinBox()
        self.pre_roll_spin.setRange(0, 600)
        self.pre_roll_spin.setSuffix(" s")
        self.pre_roll_spin.setValue(s.default_pre_roll_seconds)
        self.post_roll_spin = QDoubleSpinBox()
        self.post_roll_spin.setRange(0, 600)
        self.post_roll_spin.setSuffix(" s")
        self.post_roll_spin.setValue(s.default_post_roll_seconds)
        self.separator_combo = QComboBox()
        self.separator_combo.addItem("Hyphens (My-Clip-Name)", SEPARATOR_HYPHEN)
        self.separator_combo.addItem("Underscores (My_Clip_Name)", SEPARATOR_UNDERSCORE)
        self.separator_combo.setCurrentIndex(
            0 if s.separator_style == SEPARATOR_HYPHEN else 1)
        self.dup_warn_check = QCheckBox()
        self.dup_warn_check.setChecked(s.warn_duplicate_names)
        self.details_prompt_combo = QComboBox()
        for text, value in [
            ("After the play - O opens it (recommended)", DETAILS_AFTER_OUT),
            ("At the snap - I opens it", DETAILS_AT_IN),
            ("Never - mark with I/O, add with A", DETAILS_OFF),
        ]:
            self.details_prompt_combo.addItem(text, value)
        index = self.details_prompt_combo.findData(s.details_prompt)
        self.details_prompt_combo.setCurrentIndex(max(0, index))
        self.details_prompt_combo.setToolTip(
            "When the Clip Details form appears.\n\n"
            "After the play: I marks the snap silently, you watch the play, "
            "then O opens the form - by then you know the down, result and "
            "who made it. Enter saves.\n\n"
            "At the snap: the old behavior - the form opens on I, before the "
            "play has happened.")
        self.pause_details_check = QCheckBox()
        self.pause_details_check.setChecked(s.pause_on_details)
        self.my_team_edit = QLineEdit(s.my_team)
        self.my_team_edit.setPlaceholderText("e.g. Miami")
        self.my_team_edit.setToolTip(
            "Your team's name. Used to work out the opponent from a film's "
            "filename - 'MIAMI O VS. FLORIDA STATE D' becomes opponent "
            "'Florida State'.")
        self.chronological_check = QCheckBox()
        self.chronological_check.setChecked(s.insert_chronologically)
        self.chronological_check.setToolTip(
            "New clips slot into the list by their start time instead of "
            "landing at the bottom, so the list mirrors the game.")
        self.resume_after_save_check = QCheckBox()
        self.resume_after_save_check.setChecked(s.resume_after_save)
        self.resume_after_save_check.setToolTip(
            "After saving a clip from the mark bar or the details form, "
            "playback continues automatically so you keep your momentum.")
        self.fixed_dropdown_check = QCheckBox()
        self.fixed_dropdown_check.setChecked(s.use_fixed_dropdowns)
        self.fixed_dropdown_check.setToolTip(
            "On: detail fields and tags offer a predefined dropdown list "
            "(editable below) instead of learning values as you type.")
        edit_lists_btn = QPushButton("Edit fixed lists…")
        self.inspector_density_combo = QComboBox()
        for text, value in [
            ("Compact (recommended)", INSPECTOR_DENSITY_COMPACT),
            ("Comfortable", INSPECTOR_DENSITY_COMFORTABLE),
            ("Spacious", INSPECTOR_DENSITY_SPACIOUS),
        ]:
            self.inspector_density_combo.addItem(text, value)
        index = self.inspector_density_combo.findData(s.inspector_density)
        self.inspector_density_combo.setCurrentIndex(max(0, index))
        self.inspector_density_combo.setToolTip(
            "Changes the spacing and field height in Clip Details. "
            "Clip data and field order stay the same.")
        customize_details_btn = QPushButton("Customize Clip Details...")
        customize_details_btn.clicked.connect(
            self._edit_detail_field_layout)
        edit_lists_btn.clicked.connect(self._edit_fixed_lists)
        form.addRow("Default pre-roll:", self.pre_roll_spin)
        form.addRow("Default post-roll:", self.post_roll_spin)
        form.addRow("Filename separator:", self.separator_combo)
        form.addRow("Warn on duplicate names:", self.dup_warn_check)
        form.addRow("Ask for play details:", self.details_prompt_combo)
        form.addRow("Pause when details open:", self.pause_details_check)
        form.addRow("Resume playback after saving:", self.resume_after_save_check)
        form.addRow("Your team:", self.my_team_edit)
        form.addRow("Keep clips in time order:", self.chronological_check)
        self.explosive_rush_spin = QSpinBox()
        self.explosive_rush_spin.setRange(1, 99)
        self.explosive_rush_spin.setSuffix(" yd")
        self.explosive_rush_spin.setValue(s.explosive_rush_yards)
        self.explosive_rush_spin.setToolTip(
            "A run is explosive when Yards meets this threshold.")
        self.explosive_pass_spin = QSpinBox()
        self.explosive_pass_spin.setRange(1, 99)
        self.explosive_pass_spin.setSuffix(" yd")
        self.explosive_pass_spin.setValue(s.explosive_pass_yards)
        self.explosive_pass_spin.setToolTip(
            "A pass is explosive when Yards meets this threshold.")
        form.addRow("Fixed dropdowns for details/tags:", self.fixed_dropdown_check)
        form.addRow("Explosive rush:", self.explosive_rush_spin)
        form.addRow("Explosive pass:", self.explosive_pass_spin)
        form.addRow("Clip Details density:", self.inspector_density_combo)
        form.addRow("", customize_details_btn)
        form.addRow("", edit_lists_btn)
        return w

    def _edit_detail_field_layout(self) -> None:
        DetailFieldLayoutDialog(self.settings, self).exec()

    def _edit_fixed_lists(self) -> None:
        FixedListsDialog(self.settings, self).exec()

    def _export_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        s = self.settings
        self.preset_combo = QComboBox()
        for preset in BUILTIN_PRESETS.values():
            self.preset_combo.addItem(preset.display_name, preset.name)
        index = self.preset_combo.findData(s.default_preset)
        self.preset_combo.setCurrentIndex(max(0, index))
        self.accurate_check = QCheckBox("Accurate (frame-exact, re-encodes)")
        self.accurate_check.setChecked(s.accurate_cut)
        self.template_edit = QLineEdit(s.naming_template)
        self.template_edit.setToolTip(
            "Variables: {project} {clip_number} {clip_name} {label} {timestamp} "
            "{start_time} {end_time} {date} {tags}")
        self.organization_combo = QComboBox()
        for text, value in [("Structured folders", "structured"), ("Flat folder", "flat"),
                            ("By label", "by_label"), ("By tag", "by_tag"),
                            ("By preset", "by_preset"),
                            ("Custom folders from details…", "template")]:
            self.organization_combo.addItem(text, value)
        from tapesift.services.filename_service import folder_template_tokens
        self.folder_template_edit = QLineEdit()
        self.folder_template_edit.setPlaceholderText(
            "{player}/{quarter}/{down_distance}")
        self.folder_template_edit.setToolTip(
            "Folder levels separated by / - each level can mix text and tokens.\n"
            "Missing values become 'Unspecified'.\nTokens: "
            + " ".join("{" + t + "}" for t in folder_template_tokens()))
        if "{" in s.output_organization:
            index = self.organization_combo.findData("template")
            self.folder_template_edit.setText(s.output_organization)
        else:
            index = self.organization_combo.findData(s.output_organization)
        self.organization_combo.setCurrentIndex(max(0, index))
        self.hw_combo = QComboBox()
        self.hw_combo.addItem("CPU encoding (default)", "cpu")
        detected = ffmpeg_service.detect_hw_encoders(s.ffmpeg_path)
        for encoder in detected:
            self.hw_combo.addItem(ffmpeg_service.HW_ENCODERS[encoder], encoder)
        index = self.hw_combo.findData(s.hardware_acceleration)
        self.hw_combo.setCurrentIndex(max(0, index))
        defaults_note = QLabel("Defaults for new projects. Existing projects keep their saved export choices. Hardware encoder selection applies to all exports.")
        defaults_note.setWordWrap(True)
        form.addRow(defaults_note)
        form.addRow("Default preset:", self.preset_combo)
        form.addRow("Cutting mode:", self.accurate_check)
        form.addRow("Naming template:", self.template_edit)
        form.addRow("Output organization:", self.organization_combo)
        form.addRow("Folder template:", self.folder_template_edit)
        form.setRowVisible(self.folder_template_edit,
                           self.organization_combo.currentData() == "template")
        self.organization_combo.currentIndexChanged.connect(
            lambda: form.setRowVisible(self.folder_template_edit,
                self.organization_combo.currentData() == "template"))
        form.addRow("Hardware encoder:", self.hw_combo)
        if not detected:
            hint = QLabel("No hardware encoders detected in this FFmpeg build.")
            hint.setProperty("role", "subtle")
            form.addRow(hint)
        return w

    def _ffmpeg_tab(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        s = self.settings
        self.ffmpeg_edit = QLineEdit(s.ffmpeg_path)
        self.ffprobe_edit = QLineEdit(s.ffprobe_path)
        form.addRow("FFmpeg path:", self._file_row(self.ffmpeg_edit))
        form.addRow("FFprobe path:", self._file_row(self.ffprobe_edit))
        layout.addLayout(form)
        self.version_label = QLabel("")
        self.version_label.setWordWrap(True)
        layout.addWidget(self.version_label)
        row = QHBoxLayout()
        test_btn = QPushButton("Test installation")
        test_btn.clicked.connect(self._test_ffmpeg)
        logs_btn = QPushButton("Open Logs Folder")
        logs_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.logs_dir()))))
        row.addWidget(test_btn)
        row.addWidget(logs_btn)
        row.addStretch()
        layout.addLayout(row)
        layout.addStretch()
        self._test_ffmpeg()
        return w

    def _file_row(self, edit: QLineEdit) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        browse = QPushButton("Browse…")

        def pick() -> None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Locate executable", "", "Executables (*.exe);;All files (*)")
            if path:
                edit.setText(path)

        browse.clicked.connect(pick)
        h.addWidget(browse)
        return w

    def _test_ffmpeg(self) -> None:
        ffmpeg_v = ffmpeg_service.get_version(self.ffmpeg_edit.text().strip())
        ffprobe_v = ffmpeg_service.get_version(self.ffprobe_edit.text().strip())
        self.version_label.setText(
            f"FFmpeg: {ffmpeg_v or 'not working'}\nFFprobe: {ffprobe_v or 'not working'}")

    def _save(self) -> None:
        s = self.settings
        s.default_project_folder = self.project_folder_edit.text().strip()
        s.default_output_folder = self.output_folder_edit.text().strip()
        s.autosave_interval_seconds = self.autosave_spin.value()
        s.recent_project_count = self.recent_spin.value()
        s.confirm_before_delete = self.confirm_delete_check.isChecked()
        s.jump_backward_seconds = self.jump_back_spin.value()
        s.jump_forward_seconds = self.jump_fwd_spin.value()
        s.default_playback_speed = self.speed_combo.currentData()
        s.volume = self.volume_spin.value()
        s.scrub_proxy_enabled = self.proxy_check.isChecked()
        s.first_read_enabled = self.first_read_enabled_check.isChecked()
        s.first_read_api_key = self.first_read_api_key_edit.text().strip()
        s.first_read_model = self.first_read_model_edit.text().strip()
        s.default_pre_roll_seconds = self.pre_roll_spin.value()
        s.default_post_roll_seconds = self.post_roll_spin.value()
        s.separator_style = self.separator_combo.currentData()
        s.warn_duplicate_names = self.dup_warn_check.isChecked()
        s.details_prompt = self.details_prompt_combo.currentData()
        s.pause_on_details = self.pause_details_check.isChecked()
        s.use_fixed_dropdowns = self.fixed_dropdown_check.isChecked()
        s.explosive_rush_yards = self.explosive_rush_spin.value()
        s.explosive_pass_yards = self.explosive_pass_spin.value()
        s.resume_after_save = self.resume_after_save_check.isChecked()
        s.my_team = self.my_team_edit.text().strip()
        s.insert_chronologically = self.chronological_check.isChecked()
        s.inspector_density = self.inspector_density_combo.currentData()
        s.default_preset = self.preset_combo.currentData()
        s.accurate_cut = self.accurate_check.isChecked()
        s.naming_template = self.template_edit.text().strip() or "{clip_number}_{clip_name}"
        if self.organization_combo.currentData() == "template":
            s.output_organization = self.folder_template_edit.text().strip() \
                or "structured"
        else:
            s.output_organization = self.organization_combo.currentData()
        s.hardware_acceleration = self.hw_combo.currentData()
        s.ffmpeg_path = self.ffmpeg_edit.text().strip()
        s.ffprobe_path = self.ffprobe_edit.text().strip()
        s.save()
        self.accept()
