"""The standard heat-map page, using the existing painter and five writers."""
from __future__ import annotations

from dataclasses import replace
from html import escape
import json
from pathlib import Path

from PySide6.QtCore import QEvent, QIODevice, QRectF, QSaveFile, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QToolTip, QVBoxLayout, QWidget,
)

from tapesift.services.filename_service import sanitize_filename_base
from tapesift.services.heatmap_export import FORMATS, export_all, export_variants
from tapesift.services.heatmap_service import LAYERS, build_heatmap, select_heatmap
from tapesift.ui_v2.heatmap_render import paint_layered_heatmap, heatmap_height, paint_heatmap
from tapesift.ui_v3.heatmap_reports import TEMPLATES
from tapesift.ui_v3.weekly_social import SOCIAL_FIELDS, SOCIAL_TEMPLATES
from tapesift.ui_v3.icons import tinted_icon


def saved_heatmap(session):
    """Read once without saving pending editor/model changes or committing a caller's transaction."""
    if session.conn.in_transaction:
        raise ValueError("A project update is in progress. Open the heat map after it finishes.")
    session.conn.execute("BEGIN")
    try:
        project = session.project_repo.load()
        if project is None:
            raise ValueError("The saved project could not be found")
        clips = session.clip_repo.list_for_project(project.id)
    finally:
        session.conn.rollback()
    data = build_heatmap([c for c in clips if c.enabled],
        title=f"{project.name} — Game Heat Map", layered=True,
        scope="Saved enabled clips · disabled clips excluded · versions count separately. "
              "Cells are clips; player counts are logged primary-player assignments.",
        tag_styles=project.tag_styles)
    data = replace(data, subtitle=f"{data.play_count} clips · {len(data.players)} logged players · "
                                 f"{data.unassigned} unassigned · saved snapshot")
    return data, Path(project.output_folder or session.db_path.parent), f"{project.name}-heat-map"


class _HeatmapCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = build_heatmap([], layered=True)
        self.areas = {}
        self.setMouseTracking(True)
        self.setAccessibleName("Game heat map chart")
        self.quarter_layer = QComboBox(self)
        self.quarter_layer.setObjectName("V3HeatmapQuarterLayer")
        self.quarter_layer.setAccessibleName("Layer shown by quarter")
        self.quarter_layer.setFixedSize(160, 30)

    def set_view(self, data, width):
        self.data = data
        self.areas.clear()
        height = (heatmap_height(data, width=width) if data.report_template else
                  paint_layered_heatmap(None, data, width, header=False, layout=self.areas))
        self.resize(width, height)
        top = self.areas.get("quarter_header")
        self.quarter_layer.setVisible(top is not None)
        if top is not None:
            self.quarter_layer.move(255, int(top) - 7)
        self.setAccessibleDescription(data.subtitle + ". " + data.scope)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        if self.data.report_template:
            paint_heatmap(painter, self.data, self.width())
        else:
            paint_layered_heatmap(painter, self.data, self.width(), header=False)
        painter.end()

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            for layer, rect in self.areas.get("ribbons", []):
                if rect.contains(event.pos()) and self.data.plays:
                    index = min(len(self.data.plays) - 1, int(
                        (event.pos().x() - rect.x()) / rect.width() * len(self.data.plays)))
                    play = self.data.plays[index]
                    lines = [f"Clip {play.clip_number} · {play.quarter_raw or 'Quarter unlogged'}",
                             f"Run / Pass: {play.run_pass or 'Unlogged'}",
                             f"Concept: {play.concept or 'Unlogged'}",
                             f"Play Action: {play.play_action or 'Unlogged'}",
                             f"Situation: {play.down_distance or 'Unlogged'} · {play.ball_on or 'Ball unlogged'}",
                             f"Yards: {play.yards or 'Unknown'} · Action: {play.action or 'Unlogged'}",
                             f"Results: {play.result or 'Unlogged'}",
                             f"Primary player: {play.player or 'Unassigned'}"]
                    QToolTip.showText(event.globalPos(), "<br>".join(escape(x) for x in lines), self)
                    return True
            QToolTip.hideText()
        return super().event(event)


class HeatmapPageV3(QFrame):
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("V3HeatmapPage")
        self.snapshot = build_heatmap([], layered=True)
        self.data = self.snapshot
        self.written = []
        self.zoom = 100
        self._compact = None
        self._draft_path = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        outer.addLayout(body, 1)
        chart = QWidget()
        chart_layout = QVBoxLayout(chart)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(0)
        body.addWidget(chart, 1)
        self.header = QFrame()
        self.header.setObjectName("V3HeatmapHeader")
        self.header_layout = QGridLayout(self.header)
        self.header_layout.setContentsMargins(24, 12, 24, 12)
        self.header_layout.setHorizontalSpacing(24)
        self.back = QPushButton("Back to Review")
        self.back.setIcon(tinted_icon("chevron-left-16.svg"))
        self.back.clicked.connect(self.back_requested)
        self.heading = QWidget()
        heading_layout = QVBoxLayout(self.heading)
        heading_layout.setContentsMargins(0, 0, 0, 0)
        heading_layout.setSpacing(2)
        self.title = QLabel("Game Heat Map")
        self.title.setObjectName("V3HeatmapTitle")
        self.title.setWordWrap(True)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.summary = QLabel()
        self.summary.setObjectName("V3HeatmapSummary")
        self.summary.setWordWrap(True)
        heading_layout.addWidget(self.title)
        heading_layout.addWidget(self.summary)
        self.tools = QWidget()
        self.tools.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        tool_layout = QHBoxLayout(self.tools)
        tool_layout.setContentsMargins(0, 0, 0, 0)
        tool_layout.setSpacing(8)
        tool_layout.addWidget(QLabel("View"))
        self.view = QComboBox()
        self.view.setAccessibleName("Heatmap quarter view")
        self.view.setFixedWidth(138)
        tool_layout.addWidget(self.view)
        self.zoom_out = QPushButton()
        self.zoom_out.setIcon(tinted_icon("zoom-out-20.svg"))
        self.zoom_out.setAccessibleName("Zoom out heat map")
        self.zoom_out.setToolTip("Zoom out")
        self.zoom_out.setFixedWidth(38)
        self.zoom_out.clicked.connect(lambda: self._set_zoom(self.zoom - 25))
        self.zoom_label = QPushButton("100%")
        self.zoom_label.setFixedWidth(56)
        self.zoom_label.setToolTip("Reset zoom to fit the chart width")
        self.zoom_label.clicked.connect(lambda: self._set_zoom(100))
        self.zoom_in = QPushButton()
        self.zoom_in.setIcon(tinted_icon("zoom-in-20.svg"))
        self.zoom_in.setAccessibleName("Zoom in heat map")
        self.zoom_in.setToolTip("Zoom in")
        self.zoom_in.setFixedWidth(38)
        self.zoom_in.clicked.connect(lambda: self._set_zoom(self.zoom + 25))
        for widget in (self.zoom_out, self.zoom_label, self.zoom_in):
            tool_layout.addWidget(widget)
        chart_layout.addWidget(self.header)
        self.chart_scroll = QScrollArea()
        self.chart_scroll.setObjectName("V3HeatmapChartScroll")
        self.chart_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.canvas = _HeatmapCanvas()
        self.chart_scroll.setWidget(self.canvas)
        chart_layout.addWidget(self.chart_scroll, 1)
        self.sidebar = QFrame()
        self.sidebar.setObjectName("V3HeatmapSidebar")
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.sidebar)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_scroll.setWidgetResizable(True)
        settings = QWidget()
        settings.setObjectName("V3HeatmapSettings")
        controls = QVBoxLayout(settings)
        controls.setContentsMargins(28, 28, 28, 16)
        controls.setSpacing(16)
        self.settings_scroll.setWidget(settings)
        side_layout.addWidget(self.settings_scroll, 1)
        heading = QLabel("Export heat map")
        heading.setObjectName("V3HeatmapExportHeading")
        controls.addWidget(heading)
        controls.addWidget(QLabel("Template"))
        self.template = QComboBox()
        self.template.setAccessibleName("Heat map report template")
        for key, label in TEMPLATES.items():
            self.template.addItem(label, key)
        for key, label in SOCIAL_TEMPLATES.items():
            self.template.addItem(label, key)
        self.template.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.template.setMinimumContentsLength(18)
        self.template.addItem("Classic layered map", "")
        controls.addWidget(self.template)
        controls.addWidget(QLabel("Layout"))
        self.orientation = QComboBox()
        self.orientation.setAccessibleName("Heat map report layout")
        self.orientation.addItem("Landscape", "landscape")
        self.orientation.addItem("Portrait / mobile", "portrait")
        controls.addWidget(self.orientation)
        self.social_host = QWidget()
        social = QVBoxLayout(self.social_host)
        social.setContentsMargins(0, 0, 0, 0)
        self.slide = QComboBox()
        self.slide.addItems(["1 · Game overview", "2 · Featured players", "3 · Film takeaways"])
        self.slide.setAccessibleName("Carousel slide preview")
        social.addWidget(self.slide)
        self.social_inputs = {}
        for key, (label, limit) in SOCIAL_FIELDS.items():
            social.addWidget(QLabel(label))
            field = QLineEdit()
            field.setMaxLength(limit)
            field.setAccessibleName(label)
            field.setPlaceholderText("Add your analysis…" if key != "headline" else "Use the game name")
            social.addWidget(field)
            self.social_inputs[key] = field
        social.addWidget(QLabel("Play to revisit"))
        self.social_clip = QComboBox()
        self.social_clip.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.social_clip.setMinimumContentsLength(18)
        self.social_clip.setAccessibleName("Play to revisit")
        social.addWidget(self.social_clip)
        self.save_social = QPushButton("Save post text")
        social.addWidget(self.save_social)
        self.social_note = QLabel("Shared by both formats · 1080 × 1350 PNG · 4:5")
        self.social_note.setWordWrap(True)
        social.addWidget(self.social_note)
        controls.addWidget(self.social_host)
        self.social_host.hide()
        self.player_label = QLabel("Player spotlight")
        self.report_player = QComboBox()
        self.report_player.setAccessibleName("Spotlight player")
        controls.addWidget(self.player_label)
        controls.addWidget(self.report_player)
        self.layer_host = QWidget()
        layer_layout = QVBoxLayout(self.layer_host)
        layer_layout.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(self.layer_host)
        row = QHBoxLayout()
        row.addWidget(QLabel("Layers"))
        row.addStretch(1)
        self.select_all = QPushButton("Select all")
        self.select_all.setObjectName("V3HeatmapSelectAll")
        self.select_all.clicked.connect(self._select_all)
        row.addWidget(self.select_all)
        layer_layout.addLayout(row)
        self.layer_checks = {}
        for key, label in LAYERS.items():
            check = QCheckBox(label)
            check.setChecked(True)
            layer_layout.addWidget(check)
            self.layer_checks[key] = check
        self._rule(controls)
        controls.addWidget(QLabel("Format"))
        format_layout = QGridLayout()
        format_layout.setSpacing(10)
        self.format_buttons = {}
        self.format_labels = {key: label for key, label, _ in FORMATS}
        format_icons = dict(png="image-20.svg", pdf="document-pdf-20.svg", svg="code-20.svg",
                            csv="table-20.svg", json="braces-20.svg")
        for i, key in enumerate(("png", "pdf", "svg", "csv", "json")):
            label = self.format_labels[key]
            button = QPushButton(label)
            button.setIcon(tinted_icon(format_icons[key], size=18))
            button.setIconSize(QSize(18,18))
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setCheckable(True)
            button.setChecked(key == "png")
            button.setMinimumHeight(44)
            button.setProperty("heatmapFormat", True)
            button.toggled.connect(self._destination_changed)
            format_layout.addWidget(button, i // 3, i % 3)
            self.format_buttons[key] = button
        controls.addLayout(format_layout)
        self._rule(controls)
        controls.addWidget(QLabel("Destination"))
        destination = QHBoxLayout()
        self.folder = QLineEdit()
        self.folder.setAccessibleName("Heatmap export destination folder")
        self.browse = QPushButton("Browse…")
        self.browse.clicked.connect(self._browse)
        destination.addWidget(self.folder, 1)
        destination.addWidget(self.browse)
        controls.addLayout(destination)
        controls.addWidget(QLabel("File name"))
        self.filename = QLineEdit()
        self.filename.setAccessibleName("Heatmap export file name")
        controls.addWidget(self.filename)
        self.destination_note = QLabel()
        self.destination_note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.destination_note.setWordWrap(True)
        self.destination_note.setObjectName("V3HeatmapNote")
        controls.addWidget(self.destination_note)
        note = QLabel("Exports use the saved clips in this view. Player Spotlight exports the "
                      "selected player's primary clips. Template and layout are included in the file name.")
        note.setObjectName("V3HeatmapNote")
        note.setWordWrap(True)
        controls.addWidget(note)
        self.status = QLabel()
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setObjectName("V3HeatmapStatus")
        controls.addWidget(self.status)
        self.settings_scroll.verticalScrollBar().rangeChanged.connect(self._reveal_status)
        controls.addStretch(1)
        action_host = QWidget()
        action_layout = QVBoxLayout(action_host)
        action_layout.setContentsMargins(28, 12, 28, 24)
        self.export = QPushButton("Export heat map")
        self.export.setObjectName("V3HeatmapExport")
        self.export.setFixedHeight(50)
        self.export.clicked.connect(self._export)
        action_layout.addWidget(self.export)
        side_layout.addWidget(action_host)
        footer = QFrame()
        footer.setObjectName("V3HeatmapFooter")
        footer.setFixedHeight(55)
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(20, 0, 20, 0)
        foot.addWidget(QLabel("●  Local workspace · saved snapshot"))
        foot.addStretch(1)
        foot.addWidget(QLabel("ESC  Back to Review  ·  CTRL+E  Export Heat Map"))
        foot.addStretch(1)
        self.footer_count = QLabel()
        foot.addWidget(self.footer_count)
        outer.addWidget(footer)
        self.view.currentIndexChanged.connect(self._view_changed)
        self.template.currentIndexChanged.connect(self._view_changed)
        self.orientation.currentIndexChanged.connect(self._view_changed)
        self.report_player.currentIndexChanged.connect(self._view_changed)
        self.slide.currentIndexChanged.connect(self._view_changed)
        for field in self.social_inputs.values():
            field.textChanged.connect(self._social_changed)
        self.social_clip.currentIndexChanged.connect(self._social_changed)
        self.save_social.clicked.connect(self._save_social)
        self.canvas.quarter_layer.currentIndexChanged.connect(self._view_changed)
        for check in self.layer_checks.values():
            check.toggled.connect(self._layers_changed)
        self.folder.textChanged.connect(self._destination_changed)
        self.filename.textChanged.connect(self._destination_changed)
        self._page_shortcuts = []
        for key, slot in (("Escape", self.back_requested.emit), ("Ctrl+E", self._export)):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
            self._page_shortcuts.append(shortcut)

    @staticmethod
    def _rule(layout):
        line = QFrame()
        line.setObjectName("V3HeatmapRule")
        line.setFixedHeight(1)
        layout.addSpacing(14)
        layout.addWidget(line)
        layout.addSpacing(8)

    def set_snapshot(self, data, folder, stem, *, draft_path=None):
        self.snapshot = data
        self._draft_path = Path(draft_path) if draft_path else None
        draft, draft_error = {}, ""
        if self._draft_path and self._draft_path.exists():
            try:
                draft = json.loads(self._draft_path.read_text(encoding="utf-8"))
                if not isinstance(draft, dict):
                    raise ValueError("Post text must be an object")
            except (OSError, ValueError) as error:
                draft = {}
                draft_error = f"Could not load post text: {error}"
        for key, field in self.social_inputs.items():
            field.blockSignals(True)
            field.setText(str(draft.get(key, ""))[:SOCIAL_FIELDS[key][1]])
            field.setCursorPosition(0)
            field.blockSignals(False)
        self.social_clip.blockSignals(True)
        self.social_clip.clear()
        self.social_clip.addItem("Choose a clip…", "")
        for play in data.plays:
            self.social_clip.addItem(f"{play.clip_number:03d} · {play.title}", play.clip_id)
        self.social_clip.setCurrentIndex(max(0, self.social_clip.findData(draft.get("clip_id", ""))))
        self.social_clip.blockSignals(False)
        self.save_social.setEnabled(bool(self._draft_path))
        self.social_note.setText(draft_error or "Shared by both formats · 1080 × 1350 PNG · 4:5")
        self.title.setText(data.title)
        self.title.setToolTip(data.title)
        self.view.blockSignals(True)
        self.view.clear()
        self.view.addItem("Whole game", "")
        for quarter in data.quarters:
            self.view.addItem("Unknown quarter" if quarter == "?" else quarter, quarter)
        self.view.blockSignals(False)
        self.report_player.blockSignals(True)
        self.report_player.clear()
        for player in data.players:
            self.report_player.addItem(player.name, player.key)
        self.report_player.blockSignals(False)
        self.folder.setText(str(folder))
        self.filename.setText(sanitize_filename_base(stem))
        self.written = []
        self.status.clear()
        self._set_zoom(100)
        self._select_all()
        self._layers_changed()
        self._destination_changed()

    def _select_all(self):
        for check in self.layer_checks.values():
            check.setChecked(True)

    def _layers_changed(self):
        combo = self.canvas.quarter_layer
        previous = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        for key, check in self.layer_checks.items():
            if check.isChecked():
                combo.addItem(LAYERS[key], key)
        found = combo.findData(previous)
        combo.setCurrentIndex(max(0, found))
        combo.blockSignals(False)
        self._view_changed()

    def _view_changed(self):
        self.status.clear()
        template = self.template.currentData() or ""
        is_social = template in SOCIAL_TEMPLATES
        if is_social:
            self.orientation.blockSignals(True)
            self.orientation.setCurrentIndex(self.orientation.findData("portrait"))
            self.orientation.blockSignals(False)
        layers = tuple(LAYERS) if template else tuple(
            key for key, check in self.layer_checks.items() if check.isChecked())
        self.data = select_heatmap(self.snapshot, quarter=self.view.currentData() or "",
                                  layers=layers,
                                  quarter_layer=self.canvas.quarter_layer.currentData() or "run_pass",
                                  player_key=(self.report_player.currentData() or "")
                                  if self.template.currentData() == "player_spotlight" else "")
        self.data = replace(self.data, report_template=template,
                            report_orientation="portrait" if is_social else self.orientation.currentData() or "landscape",
                            social_text=self._social_text(), report_slide=self.slide.currentIndex()+1,
                            report_player=(self.report_player.currentData() or "")
                            if template == "player_spotlight" else "",
                            report_player_name=self.report_player.currentText()
                            if template == "player_spotlight" else "")
        self.layer_host.setVisible(not template)
        self.orientation.setEnabled(bool(template) and not is_social)
        self.social_host.setVisible(is_social)
        self.slide.setVisible(template == "weekly_carousel")
        self.player_label.setVisible(template == "player_spotlight")
        self.report_player.setVisible(template == "player_spotlight")
        summary = (f"{self.data.play_count} clips · {len(self.data.players)} logged players · "
                   f"{self.data.unassigned} unassigned")
        self.summary.setText(summary + " · saved snapshot")
        self.footer_count.setText(summary)
        self._resize_canvas()
        self._destination_changed()

    def _social_text(self):
        return tuple((key, field.text().strip()) for key, field in self.social_inputs.items()) + (
            ("clip_id", self.social_clip.currentData() or ""),)

    def _social_changed(self, *_args):
        self.social_note.setText("Post text changed · Save post text to keep it for this project")
        self._view_changed()

    def _save_social(self):
        if not self._draft_path:
            return True
        payload = json.dumps(dict(self._social_text()), ensure_ascii=False, indent=2).encode("utf-8")
        file = QSaveFile(str(self._draft_path))
        if not file.open(QIODevice.OpenModeFlag.WriteOnly):
            self.social_note.setText(f"Could not save post text: {file.errorString()}")
            return False
        if file.write(payload) != len(payload) or not file.commit():
            self.social_note.setText(f"Could not save post text: {file.errorString()}")
            return False
        self.social_note.setText("Post text saved for this project · Shared by both formats")
        return True

    def _set_zoom(self, value):
        self.zoom = max(100, min(400, value))
        self.zoom_label.setText(f"{self.zoom}%")
        self.zoom_out.setEnabled(self.zoom > 100)
        self.zoom_in.setEnabled(self.zoom < 400)
        self._resize_canvas()

    def _resize_canvas(self):
        width = max(720, self.chart_scroll.viewport().width()) * self.zoom // 100
        social = self.data.report_template in SOCIAL_TEMPLATES
        self.chart_scroll.setAlignment(Qt.AlignmentFlag.AlignTop | (
            Qt.AlignmentFlag.AlignHCenter if social else Qt.AlignmentFlag.AlignLeft))
        if social:
            width = max(280, min(720, self.chart_scroll.viewport().width(),
                                  int(self.chart_scroll.viewport().height() / 1.25))) * self.zoom // 100
        elif self.data.report_template and self.data.report_orientation == "portrait":
            width = min(720, max(540, self.chart_scroll.viewport().width())) * self.zoom // 100
        self.canvas.set_view(self.data, width)

    def _export_stem(self):
        stem = sanitize_filename_base(self.filename.text())
        if not stem or not self.data.report_template:
            return stem
        suffix = self.data.report_template.replace("_", "-") + "-" + self.data.report_orientation
        if self.data.report_template == "player_spotlight" and self.report_player.currentText():
            suffix += "-" + sanitize_filename_base(self.report_player.currentText())
        return f"{stem}-{suffix}"

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sidebar.setFixedWidth(max(324, min(416, self.width() // 4)))
        compact = self.width() - self.sidebar.width() < 1080
        if compact != self._compact:
            self._compact = compact
            for widget in (self.back, self.heading, self.tools):
                self.header_layout.removeWidget(widget)
            self.header_layout.addWidget(self.back, 0, 0)
            self.header_layout.addWidget(self.heading, 0, 1)
            self.header_layout.addWidget(self.tools, 1 if compact else 0,
                                         0 if compact else 2, 1, 2 if compact else 1, Qt.AlignmentFlag.AlignRight)
            self.header_layout.setColumnStretch(1, 1)
            self.header.setMinimumHeight(112 if compact else 80)
            for key, button in self.format_buttons.items():
                button.setText(key.upper() if compact else self.format_labels[key])
        QTimer.singleShot(0, self, self._resize_canvas)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Export heat map to", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _destination_changed(self, *_args, clear_status=True):
        if not hasattr(self, "export"):
            return
        if clear_status:
            self.status.clear()
        formats = [key for key, button in self.format_buttons.items() if button.isChecked()]
        stem = self._export_stem()
        has_player = self.data.report_template != "player_spotlight" or bool(self.report_player.currentData())
        self.export.setEnabled(bool(stem and self.folder.text().strip() and formats and has_player))
        names = ", ".join(name for _, name, _ in export_variants(self.data, stem, formats)) if stem else "Enter a file name"
        self.destination_note.setText("Output: " + names)
        if not has_player:
            self.destination_note.setText("No primary player is logged yet. Choose another template or log a player first.")
        self.folder.setToolTip(self.folder.text())

    def _reveal_status(self, *_args):
        # Word-wrapped feedback can change the scroll range after the first
        # queued reveal. Follow the real layout change, not an assumed delay.
        if self.status.text():
            self.settings_scroll.ensureWidgetVisible(self.status)

    def _export(self):
        if not self.export.isEnabled():
            return
        if self.data.report_template in SOCIAL_TEMPLATES and not self._save_social():
            self.status.setText("Export paused: post text could not be saved. See the post text message.")
            return
        self.written = []
        self.export.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            formats = tuple(key for key, button in self.format_buttons.items() if button.isChecked())
            self.written = export_all(self.data, Path(self.folder.text().strip()),
                                      self._export_stem(), formats)
        except (OSError, ValueError) as error:
            self.status.setText(f"Export failed: {error}")
        else:
            self.status.setText(f"Exported {len(self.written)} files to {self.written[0].parent}")
        finally:
            QApplication.restoreOverrideCursor()
            self._destination_changed(clear_status=False)
            QTimer.singleShot(0, self, self._reveal_status)
