"""Per-project settings: opponent, export folder, naming template.

These belong to the film rather than the app, so they live here instead of
in the global Settings dialog.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QSpinBox, QVBoxLayout,
)

from tapesift.models.project import Project
from tapesift.services import filename_service
from tapesift.services.tag_style_service import copy_styles
from tapesift.services.timestamp_parser import format_ms, parse_timestamp
from tapesift.ui.dialog_components import DialogHeader, DialogSection
from tapesift.ui.tag_color_manager_dialog import TagColorManagerDialog


class ProjectSettingsDialog(QDialog):
    tag_color_dialog_class = TagColorManagerDialog

    def __init__(self, project: Project, known_tags: list[str] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.setProperty("tapesiftDialog", True)
        self.project = project
        self._known_tags = list(known_tags or [])
        self._tag_styles = copy_styles(project.tag_styles)
        self.setWindowTitle("Project Settings")
        self.setMinimumWidth(660)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(DialogHeader(
            "Project settings",
            "Film-specific metadata, export destination, and file naming.",
            eyebrow="FILE  /  CURRENT PROJECT",
            badge="PROJECT",
            parent=self,
        ))
        section = DialogSection("Project details")
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)

        name_row = QHBoxLayout()
        name_label = QLabel(project.name)
        name_label.setStyleSheet("font-weight:600;")
        rename_hint = QLabel("(File > Rename Project · F2)")
        rename_hint.setProperty("role", "subtle")
        name_row.addWidget(name_label)
        name_row.addWidget(rename_hint)
        name_row.addStretch()
        form.addRow("Project:", name_row)

        self.opponent_edit = QLineEdit(project.opponent)
        self.opponent_edit.setPlaceholderText("e.g. Florida State")
        self.opponent_edit.setToolTip(
            "The team faced in this film. Every clip inherits it, so the "
            "library can search and filter by opponent.")
        form.addRow("Opponent:", self.opponent_edit)
        self.game_year_edit = QSpinBox()
        self.game_year_edit.setRange(1799, 2199)
        self.game_year_edit.setSpecialValueText("Not set")
        self.game_year_edit.setValue(int(project.game_year) if project.game_year else 1799)
        self.game_year_edit.setAccessibleName("Game year")
        self.game_year_edit.setToolTip("The year this game was played. Applies to every clip in this project.")
        form.addRow("Game year:", self.game_year_edit)

        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(project.output_folder)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(browse)
        form.addRow("Export folder:", folder_row)

        self.template_edit = QLineEdit(project.naming_template)
        self.template_edit.setToolTip(
            "Variables: {project} {clip_number} {clip_name} {label} "
            "{timestamp} {start_time} {end_time} {date} {tags}")
        self.template_edit.textChanged.connect(self._update_preview)
        form.addRow("Filename template:", self.template_edit)
        section.body.addLayout(form)

        self.preview_label = QLabel("")
        self.preview_label.setProperty("mono", "true")
        self.preview_label.setWordWrap(True)
        section.body.addWidget(self.preview_label)
        layout.addWidget(section)
        self._update_preview()

        periods = DialogSection("Game quarter boundaries")
        periods_copy = QLabel(
            "Enter the film timestamp where each quarter begins. Q1 always "
            "starts at 00:00. These markers drive the quarter rail and do not "
            "change any clip times.")
        periods_copy.setProperty("role", "subtle")
        periods_copy.setWordWrap(True)
        periods.body.addWidget(periods_copy)

        marker_form = QFormLayout()
        marker_form.setHorizontalSpacing(18)
        marker_form.setVerticalSpacing(10)
        markers = project.quarter_markers_ms
        self.q2_edit = QLineEdit(
            format_ms(markers[0]) if len(markers) > 0 else "")
        self.q3_edit = QLineEdit(
            format_ms(markers[1]) if len(markers) > 1 else "")
        self.q4_edit = QLineEdit(
            format_ms(markers[2]) if len(markers) > 2 else "")
        self.overtime_edit = QLineEdit(
            ", ".join(format_ms(value) for value in markers[3:]))
        for edit in (self.q2_edit, self.q3_edit, self.q4_edit):
            edit.setPlaceholderText("MM:SS or HH:MM:SS")
        self.overtime_edit.setPlaceholderText(
            "Optional, comma-separated: 50:10, 55:42")
        marker_form.addRow("Q2 starts:", self.q2_edit)
        marker_form.addRow("Q3 starts:", self.q3_edit)
        marker_form.addRow("Q4 starts:", self.q4_edit)
        marker_form.addRow("Overtime starts:", self.overtime_edit)
        periods.body.addLayout(marker_form)
        self.marker_error = QLabel("")
        self.marker_error.setProperty("role", "error")
        self.marker_error.setWordWrap(True)
        self.marker_error.hide()
        periods.body.addWidget(self.marker_error)
        layout.addWidget(periods)
        self._parsed_quarter_markers = list(markers)

        tag_section = DialogSection("Timeline tags")
        tag_copy = QLabel(
            "Set project-specific tag colors, decide which tags lead the "
            "Primary Tag timeline view, and group related tags under one "
            "display category.")
        tag_copy.setProperty("role", "subtle")
        tag_copy.setWordWrap(True)
        tag_section.body.addWidget(tag_copy)
        self.manage_tag_styles_btn = QPushButton("Manage timeline tag colors...")
        self.manage_tag_styles_btn.clicked.connect(self._edit_tag_styles)
        tag_section.body.addWidget(self.manage_tag_styles_btn)
        layout.addWidget(tag_section)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Save project settings")
        buttons.button(QDialogButtonBox.StandardButton.Ok).setProperty("primary", "true")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose the export folder", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def _edit_tag_styles(self) -> None:
        dialog = self.tag_color_dialog_class(
            self._known_tags, self._tag_styles, self)
        if dialog.exec() == TagColorManagerDialog.DialogCode.Accepted:
            self._tag_styles = dialog.tag_styles()

    def _update_preview(self) -> None:
        from tapesift.models.clip import Clip
        sample = Clip(start_ms=1_125_000, end_ms=1_133_000, clip_number=3,
                      clip_title="Damon Wilson Sack")
        name = filename_service.render_template(
            self.template_edit.text(), sample, self.project.name)
        self.preview_label.setText(f"Example:  {name}.mp4")

    def _parse_quarter_markers(self) -> list[int]:
        regular = [
            ("Q2", self.q2_edit.text().strip()),
            ("Q3", self.q3_edit.text().strip()),
            ("Q4", self.q4_edit.text().strip()),
        ]
        markers: list[int] = []
        found_blank = False
        for label, value in regular:
            if not value:
                found_blank = True
                continue
            if found_blank:
                raise ValueError(
                    f"Enter the earlier quarter markers before {label}.")
            markers.append(parse_timestamp(value))
        overtime = [
            value.strip() for value in self.overtime_edit.text().split(",")
            if value.strip()
        ]
        if overtime and len(markers) < 3:
            raise ValueError(
                "Enter Q2, Q3, and Q4 before adding overtime markers.")
        markers.extend(parse_timestamp(value) for value in overtime)
        if any(value <= 0 for value in markers):
            raise ValueError("Quarter markers must be after 00:00.")
        if any(right <= left for left, right in zip(markers, markers[1:])):
            raise ValueError("Quarter markers must be in chronological order.")
        if (self.project.source_duration_ms
                and any(value >= self.project.source_duration_ms
                        for value in markers)):
            raise ValueError(
                "Quarter markers must be before the end of the film.")
        return markers

    def accept(self) -> None:
        try:
            self._parsed_quarter_markers = self._parse_quarter_markers()
        except Exception as exc:
            self.marker_error.setText(str(exc))
            self.marker_error.show()
            return
        self.marker_error.hide()
        super().accept()

    def apply_to(self, project: Project) -> None:
        project.opponent = self.opponent_edit.text().strip()
        project.game_year = str(self.game_year_edit.value()) if self.game_year_edit.value() != 1799 else ""
        project.output_folder = self.folder_edit.text().strip()
        template = self.template_edit.text().strip()
        if template:
            project.naming_template = template
        project.quarter_markers_ms = list(self._parsed_quarter_markers)
        project.tag_styles = copy_styles(self._tag_styles)
