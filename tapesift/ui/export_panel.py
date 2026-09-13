"""Staged export setup and live Now / Next / Complete queue."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap, QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tapesift.models.export_package import (
    CompositorInput,
    ExportPackageSnapshot,
    ExportStyle,
    ExportTemplateSnapshot,
)
from tapesift.models.export_job import ExportJob, JobStatus, JobType
from tapesift.models.export_settings import (
    BUILTIN_PRESETS,
    FAST_COPY,
    SOURCE_QUALITY,
    VERTICAL_9_16,
)
from tapesift.models.signature_template import SignatureTemplate
@dataclass(frozen=True, slots=True)
class ExportPreviewContext:
    """Small GUI-thread snapshot identifying one selected clip and take.

    ``source_frame_png`` is used only by Clean preview.  Signature and Vertical
    never treat that live player frame as Voiceover playback; their preview is
    supplied asynchronously from the recorded take's audio-frame clock.
    """

    source_frame_png: bytes
    source_video_path: str = ""
    clip_id: str = ""
    ink_event_track: bytes | None = None
    voiceover_audio: bytes | None = None
    presentation_event_track: bytes | None = None
    voiceover_waveform: tuple[float, ...] | None = None
    voiceover_take_id: str = ""
    voiceover_frame_count: int = 0
    voiceover_has_presentation_track: bool = False
    voiceover_label: str = ""
    play_call_situation: str = ""
    play_call_concept: str = ""
    play_call_result: str = ""
    timeline_position: int = 0
    timeline_duration: int = 1

    @classmethod
    def from_image(cls, image: QImage, **kwargs) -> "ExportPreviewContext":
        """Snapshot a detached source image without retaining a video frame."""

        if not isinstance(image, QImage) or image.isNull():
            raise ValueError("A valid current source frame is required.")
        data = QByteArray()
        buffer = QBuffer(data)
        if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
            raise ValueError("The current source frame could not be buffered.")
        try:
            if not image.save(buffer, "PNG"):
                raise ValueError("The current source frame could not be encoded.")
        finally:
            buffer.close()
        return cls(source_frame_png=bytes(data), **kwargs)


@dataclass(frozen=True, slots=True)
class SignatureTemplateChoice:
    """The immutable template revision exposed by the selector."""

    template: SignatureTemplate
    revision: int
    selected: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.template, SignatureTemplate):
            raise TypeError("A SignatureTemplate choice is required.")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Template revision must be at least 1.")


_STYLE_COPY = {
    ExportStyle.CLEAN: (
        "CLEAN",
        "Film only · existing individual and reel queue",
    ),
    ExportStyle.SIGNATURE: (
        "SIGNATURE",
        "16:9 · ink, play call, waveform, and identity",
    ),
    ExportStyle.VERTICAL: (
        "VERTICAL",
        "9:16 · the same template identity, reflowed",
    ),
}


class _ExportStyleCard(QToolButton):
    """One accessible, mutually exclusive presentation choice."""

    def __init__(self, style: ExportStyle, parent=None) -> None:
        super().__init__(parent)
        title, description = _STYLE_COPY[style]
        self.export_style = style
        self.setObjectName(f"ExportStyle{style.value.title()}")
        self.setProperty("exportStyleCard", "true")
        self.setCheckable(True)
        self.setText(f"{title}\n{description}")
        self.setToolTip(description)
        self.setAccessibleName(f"{title.title()} export style")
        self.setAccessibleDescription(description)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(54)


def _clear_layout(layout: QVBoxLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            # removeWidget + deleteLater can leave the old card painted until
            # the next deferred-delete pass while progress signals are busy.
            # Detach it immediately so a queue refresh never produces a ghost
            # title or thumbnail beneath the replacement card.
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        child = item.layout()
        if child is not None:
            _clear_layout(child)  # type: ignore[arg-type]


def _clock(seconds: float) -> str:
    minutes, remaining = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{remaining:02d}"


def _duration(milliseconds: int) -> str:
    seconds = max(0, milliseconds) / 1000
    return f"{seconds:.1f}s"


def _file_size(path: str) -> str:
    try:
        size = Path(path).stat().st_size
    except OSError:
        return ""
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.0f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


class _ExportLane(QFrame):
    """One compact queue lane with a fixed heading and scrolling rows."""

    _EMPTY_PRESENTATION_HEIGHT = 112

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ExportLane")
        self.setProperty("exportLane", "true")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(9, 7, 9, 7)
        outer.setSpacing(6)

        heading_row = QHBoxLayout()
        heading_row.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel(title.upper())
        self.heading.setProperty("role", "exportLaneTitle")
        self.count = QLabel("")
        self.count.setProperty("role", "exportLaneCount")
        heading_row.addWidget(self.heading)
        heading_row.addStretch()
        heading_row.addWidget(self.count)
        outer.addLayout(heading_row)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("ExportLaneScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.body.setObjectName("ExportLaneBody")
        self.rows = QVBoxLayout(self.body)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(5)
        self.rows.addStretch()
        self.scroll.setWidget(self.body)
        outer.addWidget(self.scroll, 1)

    def set_all_empty_presentation(self, enabled: bool) -> None:
        """Compact the three lanes only while the entire queue is empty."""

        compact = bool(enabled)
        self.setProperty("allEmpty", "true" if compact else "false")
        if compact:
            self.setMaximumHeight(self._EMPTY_PRESENTATION_HEIGHT)
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            self.setMaximumHeight(16_777_215)
            self.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.style().unpolish(self)
        self.style().polish(self)
        self.updateGeometry()

    def replace_rows(
            self, widgets: list[QWidget], count_text: str,
            empty_text: str) -> None:
        _clear_layout(self.rows)
        self.count.setText(count_text)
        if widgets:
            for widget in widgets:
                self.rows.addWidget(widget)
        else:
            empty = QLabel(empty_text)
            empty.setProperty("role", "exportEmpty")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.rows.addWidget(empty, 1)
        self.rows.addStretch()


class ExportPanel(QWidget):
    export_requested = Signal(str, str, bool)  # mode, preset_name, accurate
    composited_export_requested = Signal()
    composited_preview_requested = Signal()
    export_style_changed = Signal(str)
    manage_templates_requested = Signal()
    output_folder_change_requested = Signal()
    cancel_current_requested = Signal()
    cancel_all_requested = Signal()
    remove_queued_requested = Signal(str)  # job id
    retry_requested = Signal(str)  # job id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V2ExportWorkspace")
        self.jobs: dict[str, ExportJob] = {}
        self._running = False
        self._clip_thumbnails: dict[str, str] = {}
        self._clip_durations: dict[str, int] = {}
        self._selected_count = 0
        self._composited_clip_ids: tuple[str, ...] = ()
        self._export_style = ExportStyle.CLEAN
        self._clean_preset_name = "social_1080p"
        self._clean_mode_data = "both"
        self._preview_context: ExportPreviewContext | None = None
        self._composited_preview_ready = False
        self._composited_preview_generation = 0
        self._preview_request_pending = False
        self._last_preview_provenance: dict[str, object] = {}
        self._bridge_readiness_error = ""
        self._template_choices: dict[str, SignatureTemplateChoice] = {}
        self._template_load_error = ""
        self._active_progress_widgets: dict[
            str, tuple[QProgressBar, QLabel, QLabel]] = {}
        # Queue rebuilds are coalesced to one per event-loop turn.  Starting
        # an export calls set_clip_context, load_jobs, set_running and
        # on_job_started back to back, and each used to tear down and
        # rebuild every lane row - 4 x (jobs x ~4 widgets) synchronously.
        # Measured at 60 clips: 240 rows built per kickoff, ~120 ms of the
        # 457 ms the button took to respond.  Nothing can paint between
        # those calls anyway, so only the last rebuild was ever visible.
        self._queue_refresh_pending = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(7)

        self._build_stage_strip(layout)
        self._build_setup_stack(layout)

        lanes = QHBoxLayout()
        self._lanes_layout = lanes
        lanes.setContentsMargins(0, 0, 0, 0)
        lanes.setSpacing(7)
        self.now_lane = _ExportLane("Now")
        self.next_lane = _ExportLane("Next")
        self.complete_lane = _ExportLane("Complete")
        lanes.addWidget(self.now_lane, 34)
        lanes.addWidget(self.next_lane, 30)
        lanes.addWidget(self.complete_lane, 36)
        layout.addLayout(lanes, 1)

        footer = QFrame()
        footer.setObjectName("ExportFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(10, 5, 7, 5)
        footer_layout.setSpacing(12)
        self.overall_label = QLabel("Ready to export")
        self.overall_label.setProperty("role", "exportOverall")
        footer_layout.addWidget(self.overall_label)
        self.overall_progress = QProgressBar()
        self.overall_progress.setObjectName("ExportOverallProgress")
        self.overall_progress.setTextVisible(False)
        self.overall_progress.setRange(0, 100)
        footer_layout.addWidget(self.overall_progress, 1)
        self.cancel_all_btn = QPushButton("Cancel Remaining")
        self.cancel_all_btn.setObjectName("ExportCancelRemaining")
        self.cancel_all_btn.clicked.connect(self.cancel_all_requested.emit)
        self.cancel_all_btn.setEnabled(False)
        footer_layout.addWidget(self.cancel_all_btn)
        layout.addWidget(footer)

        self._set_stage("configure")
        self.set_export_style(ExportStyle.CLEAN, notify=False)
        self._rebuild_queue()

    def _build_stage_strip(self, layout: QVBoxLayout) -> None:
        strip = QFrame()
        strip.setObjectName("ExportStageStrip")
        row = QHBoxLayout(strip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._stage_labels: dict[str, QLabel] = {}
        for number, (key, text) in enumerate((
                ("configure", "Configure"),
                ("export", "Export"),
                ("complete", "Complete"),
        ), start=1):
            label = QLabel(f"{number}  {text}")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setProperty("exportStage", "true")
            label.setProperty("active", "false")
            row.addWidget(label, 1)
            self._stage_labels[key] = label
        layout.addWidget(strip)

    def _build_setup_stack(self, layout: QVBoxLayout) -> None:
        self.setup_stack = QStackedWidget()
        self.setup_stack.setObjectName("ExportSetupStack")

        editor = QWidget()
        editor.setObjectName("ExportSetupEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(12, 8, 8, 8)
        editor_layout.setSpacing(7)

        style_heading = QLabel("EXPORT STYLE")
        style_heading.setProperty("role", "exportCaption")
        editor_layout.addWidget(style_heading)
        style_row = QHBoxLayout()
        style_row.setContentsMargins(0, 0, 0, 0)
        style_row.setSpacing(7)
        self.style_group = QButtonGroup(self)
        self.style_group.setExclusive(True)
        self.style_cards: dict[ExportStyle, _ExportStyleCard] = {}
        for style in ExportStyle:
            card = _ExportStyleCard(style, editor)
            self.style_group.addButton(card)
            card.clicked.connect(
                lambda checked=False, selected=style:
                self.set_export_style(selected) if checked else None)
            style_row.addWidget(card, 1)
            self.style_cards[style] = card
        editor_layout.addLayout(style_row)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)

        preview_card = QFrame(editor)
        preview_card.setObjectName("ExportPreviewCard")
        preview_layout = QVBoxLayout(preview_card)
        preview_layout.setContentsMargins(7, 6, 7, 6)
        preview_layout.setSpacing(4)
        preview_caption = QLabel("SELECTED CLIP PREVIEW")
        preview_caption.setProperty("role", "exportCaption")
        preview_layout.addWidget(preview_caption)
        self.preview_label = QLabel("NO SOURCE FRAME")
        self.preview_label.setObjectName("ExportPackagePreview")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumSize(236, 132)
        self.preview_label.setMaximumHeight(168)
        self.preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.preview_label.setAccessibleName("Selected clip export preview")
        preview_layout.addWidget(self.preview_label, 1)
        self.preview_status = QLabel(
            "Preview waits for the current decoded source frame.")
        self.preview_status.setObjectName("ExportPreviewStatus")
        self.preview_status.setWordWrap(True)
        preview_layout.addWidget(self.preview_status)
        body.addWidget(preview_card, 3)

        options = QWidget(editor)
        options.setObjectName("ExportPackageOptions")
        controls = QGridLayout(options)
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setHorizontalSpacing(9)
        controls.setVerticalSpacing(6)

        self.selection_label = self._summary_value("0 clips", "Selected")
        controls.addWidget(self.selection_label, 0, 0)

        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("ExportMode")
        self.mode_combo.addItem("Individual clips", "individual")
        self.mode_combo.addItem("Combined reel", "reel")
        self.mode_combo.addItem("Individual clips + reel", "both")
        self.mode_combo.setCurrentIndex(2)
        self.mode_combo.currentIndexChanged.connect(self._sync_summary)
        controls.addWidget(
            self._labeled_control("Mode", self.mode_combo), 0, 1)

        template_row = QWidget()
        template_layout = QHBoxLayout(template_row)
        template_layout.setContentsMargins(0, 0, 0, 0)
        template_layout.setSpacing(5)
        self.template_combo = QComboBox()
        self.template_combo.setObjectName("ExportSignatureTemplate")
        self.template_combo.setAccessibleName("Signature export template")
        self.template_combo.setMinimumWidth(180)
        self.template_combo.currentIndexChanged.connect(
            self._composition_option_changed)
        template_layout.addWidget(self.template_combo, 1)
        self.manage_templates_btn = QPushButton("Manage")
        self.manage_templates_btn.setObjectName("ExportManageTemplates")
        self.manage_templates_btn.setAccessibleName(
            "Manage Signature export templates")
        self.manage_templates_btn.clicked.connect(
            self.manage_templates_requested.emit)
        template_layout.addWidget(self.manage_templates_btn)
        controls.addWidget(
            self._labeled_control("Template", template_row), 0, 2, 1, 2)

        layer_row = QWidget()
        layer_layout = QHBoxLayout(layer_row)
        layer_layout.setContentsMargins(0, 0, 0, 0)
        layer_layout.setSpacing(10)
        self.ink_check = QCheckBox("Ink")
        self.ink_check.setObjectName("ExportIncludeInk")
        self.voiceover_check = QCheckBox("Voiceover")
        self.voiceover_check.setObjectName("ExportIncludeVoiceover")
        self.play_call_check = QCheckBox("Play call")
        self.play_call_check.setObjectName("ExportIncludePlayCall")
        for check in (
                self.ink_check, self.voiceover_check,
                self.play_call_check):
            check.setChecked(True)
            check.toggled.connect(self._composition_option_changed)
            layer_layout.addWidget(check)
        layer_layout.addStretch(1)
        controls.addWidget(
            self._labeled_control("Presentation layers", layer_row),
            1, 0, 1, 2)

        self.preset_combo = QComboBox()
        self.preset_combo.setObjectName("ExportPreset")
        for preset in BUILTIN_PRESETS.values():
            self.preset_combo.addItem(preset.display_name, preset.name)
            index = self.preset_combo.count() - 1
            self.preset_combo.setItemData(
                index, preset.description, Qt.ItemDataRole.ToolTipRole)
        social_index = self.preset_combo.findData("social_1080p")
        if social_index >= 0:
            self.preset_combo.setCurrentIndex(social_index)
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        self.preset_combo.setAccessibleName("Advanced technical export preset")
        controls.addWidget(
            self._labeled_control("Encoding", self.preset_combo), 1, 2)

        self.accurate_check = QCheckBox("Accurate cuts")
        self.accurate_check.setChecked(True)
        self.accurate_check.setToolTip(
            "Frame-exact cuts. Turn this off only for the Fast Copy preset.")
        self.accurate_check.toggled.connect(self._sync_summary)
        controls.addWidget(
            self._labeled_control("Cutting", self.accurate_check), 1, 3)

        self.output_folder_label = QLabel("-")
        self.output_folder_label.setObjectName("ExportDestination")
        self.output_folder_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        controls.addWidget(self._labeled_control(
            "Destination", self.output_folder_label), 2, 0, 1, 2)

        change_btn = QPushButton("Change")
        change_btn.setObjectName("ExportChangeFolder")
        change_btn.clicked.connect(self.output_folder_change_requested.emit)
        controls.addWidget(change_btn, 2, 2)

        self.export_btn = QPushButton("Start Export")
        self.export_btn.setObjectName("ExportStart")
        self.export_btn.setProperty("accent", "true")
        self.export_btn.setToolTip("Start the configured batch export")
        self.export_btn.clicked.connect(self._start_export)
        controls.addWidget(self.export_btn, 2, 3)
        self.composited_unavailable_label = QLabel("")
        self.composited_unavailable_label.setObjectName(
            "ExportCompositedUnavailable")
        self.composited_unavailable_label.setProperty("role", "warning")
        self.composited_unavailable_label.setWordWrap(True)
        self.composited_unavailable_label.hide()
        controls.addWidget(
            self.composited_unavailable_label, 3, 0, 1, 4)
        controls.setColumnStretch(0, 1)
        controls.setColumnStretch(1, 2)
        controls.setColumnStretch(2, 2)
        controls.setColumnStretch(3, 1)
        body.addWidget(options, 7)
        editor_layout.addLayout(body, 1)
        self.setup_stack.addWidget(editor)

        summary = QWidget()
        summary.setObjectName("ExportSetupSummary")
        summary_row = QHBoxLayout(summary)
        summary_row.setContentsMargins(12, 5, 8, 5)
        summary_row.setSpacing(16)
        self.summary_selection = self._summary_value("0 selected", "Clips")
        self.summary_style = self._summary_value("Clean", "Style")
        self.summary_mode = self._summary_value(
            "Individual clips + reel", "Mode")
        self.summary_preset = self._summary_value("Social 1080p", "Preset")
        self.summary_cutting = self._summary_value("Accurate cuts", "Cutting")
        self.summary_destination = self._summary_value("-", "Destination")
        for index, widget in enumerate((
                self.summary_selection,
                self.summary_style,
                self.summary_mode,
                self.summary_preset,
                self.summary_cutting,
                self.summary_destination,
        )):
            summary_row.addWidget(widget, 1 if index < 5 else 3)
            if index < 5:
                summary_row.addWidget(self._divider())
        self.edit_setup_btn = QPushButton("Edit Setup")
        self.edit_setup_btn.setObjectName("ExportEditSetup")
        self.edit_setup_btn.clicked.connect(self._edit_setup)
        summary_row.addWidget(self.edit_setup_btn)
        self.setup_stack.addWidget(summary)

        layout.addWidget(self.setup_stack)
        self.warning_label = QLabel("")
        self.warning_label.setObjectName("ExportWarning")
        self.warning_label.setProperty("role", "warning")
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        # Kept for compatibility with the original panel API.
        self.cancel_btn = QPushButton()
        self.cancel_btn.hide()
        self.cancel_btn.clicked.connect(self.cancel_current_requested.emit)

    @staticmethod
    def _divider() -> QFrame:
        divider = QFrame()
        divider.setObjectName("ExportSummaryDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        return divider

    @staticmethod
    def _labeled_control(caption: str, control: QWidget) -> QWidget:
        wrapper = QWidget()
        column = QVBoxLayout(wrapper)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        label = QLabel(caption.upper())
        label.setProperty("role", "exportCaption")
        column.addWidget(label)
        column.addWidget(control)
        return wrapper

    @staticmethod
    def _summary_value(value: str, caption: str) -> QLabel:
        label = QLabel(f"{value}\n{caption}")
        label.setProperty("role", "exportSummary")
        label.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        return label

    # ---------- export controls ----------

    @property
    def export_style(self) -> ExportStyle:
        return self._export_style

    def set_export_style(
            self, style: ExportStyle | str, *, notify: bool = True) -> None:
        """Select presentation independently from the encoding preset."""

        try:
            resolved = (
                style if isinstance(style, ExportStyle)
                else ExportStyle(str(style).strip().lower())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unknown export style: {style!r}") from exc
        changed = resolved is not self._export_style
        previous = self._export_style
        if previous is ExportStyle.CLEAN:
            preset_name = self.preset_combo.currentData()
            if isinstance(preset_name, str) and preset_name:
                self._clean_preset_name = preset_name
            mode_data = self.mode_combo.currentData()
            if isinstance(mode_data, str) and mode_data:
                self._clean_mode_data = mode_data
        self._export_style = resolved
        self.style_cards[resolved].setChecked(True)

        composited = resolved is not ExportStyle.CLEAN
        self.template_combo.setEnabled(composited)
        self.manage_templates_btn.setEnabled(composited)
        self._sync_layer_availability()
        if composited:
            if self.preset_combo.currentData() in {
                    FAST_COPY.name, VERTICAL_9_16.name}:
                compatible = self.preset_combo.findData(SOURCE_QUALITY.name)
                if compatible >= 0:
                    self.preset_combo.setCurrentIndex(compatible)
            self.preset_combo.setEnabled(False)
            self.preset_combo.setToolTip(
                "Encoding stays separate from presentation. Composited output "
                "uses this re-encoding preset.")
            individual = self.mode_combo.findData("individual")
            if individual >= 0:
                self.mode_combo.setCurrentIndex(individual)
            self.mode_combo.setEnabled(False)
            self.mode_combo.setToolTip(
                "Signature and Vertical export exactly one selected clip.")
            self.accurate_check.setChecked(True)
            self.accurate_check.setEnabled(False)
            self.export_btn.setText(
                f"Export {resolved.value.title()} Clip")
        else:
            clean_index = self.preset_combo.findData(self._clean_preset_name)
            if clean_index >= 0:
                self.preset_combo.setCurrentIndex(clean_index)
            self.preset_combo.setEnabled(not self._running)
            self.preset_combo.setToolTip(
                "Advanced encoding choice for the existing Clean export queue.")
            clean_mode = self.mode_combo.findData(self._clean_mode_data)
            if clean_mode >= 0:
                self.mode_combo.setCurrentIndex(clean_mode)
            self.mode_combo.setEnabled(not self._running)
            self.mode_combo.setToolTip("")
            self._preset_changed()
            self.export_btn.setText("Start Export")

        self._sync_summary()
        self._invalidate_composited_preview()
        self._refresh_preview()
        self._sync_export_readiness()
        if notify and changed:
            self.export_style_changed.emit(resolved.value)

    def set_signature_templates(
            self, records, *, load_error: str = "") -> None:
        """Populate the selector from immutable store records or choices."""

        choices: list[SignatureTemplateChoice] = []
        for record in records:
            if isinstance(record, SignatureTemplateChoice):
                choices.append(record)
                continue
            template = getattr(record, "template", None)
            revision = getattr(record, "revision", None)
            selected = bool(getattr(record, "is_selected", False))
            choices.append(SignatureTemplateChoice(
                template=template,
                revision=revision,
                selected=selected,
            ))
        current_id = self.template_combo.currentData()
        self._template_choices = {
            choice.template.template_id: choice for choice in choices
        }
        self._template_load_error = load_error.strip()
        self.template_combo.blockSignals(True)
        try:
            self.template_combo.clear()
            for choice in choices:
                self.template_combo.addItem(
                    choice.template.name,
                    choice.template.template_id,
                )
            selected_id = next((
                choice.template.template_id
                for choice in choices if choice.selected
            ), "")
            target = current_id if current_id in self._template_choices \
                else selected_id
            index = self.template_combo.findData(target) if target else -1
            if index < 0 and self.template_combo.count():
                index = 0
            if index >= 0:
                self.template_combo.setCurrentIndex(index)
            elif self._template_load_error:
                self.template_combo.addItem("Template library unavailable", None)
            else:
                self.template_combo.addItem("No templates saved", None)
        finally:
            self.template_combo.blockSignals(False)
        self._sync_summary()
        self._invalidate_composited_preview()
        self._refresh_preview()
        self._sync_export_readiness()

    def set_preview_context(
            self, context: ExportPreviewContext | None) -> None:
        if context is not None and not isinstance(context, ExportPreviewContext):
            raise TypeError("Export preview context must be immutable.")
        previous_take = (
            self._preview_context.voiceover_take_id
            if self._preview_context is not None else ""
        )
        self._preview_context = context
        if context is not None and context.voiceover_take_id \
                and context.voiceover_take_id != previous_take:
            for check, available in (
                (self.voiceover_check, context.voiceover_has_presentation_track),
                (self.ink_check, context.voiceover_has_presentation_track),
                (self.play_call_check, bool(
                    context.play_call_situation.strip()
                    and context.play_call_concept.strip())),
            ):
                if available:
                    check.blockSignals(True)
                    try:
                        check.setChecked(True)
                    finally:
                        check.blockSignals(False)
        self._sync_layer_availability()
        self._invalidate_composited_preview()
        self._refresh_preview()
        self._sync_export_readiness()

    @property
    def preview_context(self) -> ExportPreviewContext | None:
        return self._preview_context

    @property
    def composited_preview_generation(self) -> int:
        """Revision of every input that can change a composited preview."""

        return self._composited_preview_generation

    @property
    def composited_clip_ids(self) -> tuple[str, ...]:
        return self._composited_clip_ids

    def set_composited_selection(self, clips) -> None:
        """Capture only enabled ledger selection for composited readiness."""

        self._composited_clip_ids = tuple(
            str(clip.id) for clip in clips
            if getattr(clip, "enabled", True) and getattr(clip, "id", "")
        )
        self._sync_summary()
        self._invalidate_composited_preview()
        self._refresh_preview()
        self._sync_export_readiness()

    def set_bridge_readiness_error(self, message: str = "") -> None:
        """Expose queue/runtime gates without inventing an available action."""

        self._bridge_readiness_error = str(message).strip()
        self._sync_export_readiness()

    def selected_template_choice(self) -> SignatureTemplateChoice | None:
        template_id = self.template_combo.currentData()
        return self._template_choices.get(template_id)

    def focus_primary_control(self) -> None:
        """Focus an actionable setup control, never a disabled final button."""

        if self.export_btn.isEnabled():
            self.export_btn.setFocus()
        elif self.template_combo.isEnabled():
            self.template_combo.setFocus()
        else:
            self.style_cards[self._export_style].setFocus()

    def export_package_snapshot(self) -> ExportPackageSnapshot:
        """Build the exact presentation snapshot shown by the setup surface."""

        style = self._export_style
        if style is ExportStyle.CLEAN:
            return ExportPackageSnapshot(
                style=style,
                technical_preset=self.preset_combo.currentData(),
                accurate_cut=self.accurate_check.isChecked(),
            ).validate()

        choice = self.selected_template_choice()
        if choice is None:
            reason = self._template_load_error or (
                "Choose or create a Signature template before previewing "
                f"{style.value.title()}.")
            raise ValueError(reason)
        context = self._preview_context
        if context is None or not context.source_video_path.strip():
            raise ValueError("The selected clip has no linked source video.")
        if not self.voiceover_check.isChecked():
            raise ValueError(
                "Signature and Vertical currently require Voiceover. The "
                "soundtrack policy without Voiceover is not approved yet.")
        if not context.voiceover_take_id.strip() \
                or not context.voiceover_has_presentation_track \
                or context.voiceover_frame_count <= 0 \
                or context.voiceover_waveform is None:
            raise ValueError(
                "Select a saved Voiceover take with a recorded presentation "
                "track before exporting Signature or Vertical.")
        take_prefix = f"take:{context.voiceover_take_id}"
        inputs = [CompositorInput(
            "source_video",
            context.source_video_path,
        )]
        if self.ink_check.isChecked():
            inputs.append(CompositorInput(
                "ink_event_track",
                f"{take_prefix}:ink"))
        inputs.append(CompositorInput(
            "voiceover_audio", f"{take_prefix}:audio"))
        inputs.append(CompositorInput(
            "presentation_event_track", f"{take_prefix}:events"))
        if self.play_call_check.isChecked():
            if context.play_call_situation.strip():
                inputs.append(CompositorInput(
                    "play_call_situation",
                    context.play_call_situation.strip()))
            if context.play_call_concept.strip():
                inputs.append(CompositorInput(
                    "play_call_concept",
                    context.play_call_concept.strip()))
            if (
                choice.template.identity.show_result
                and context.play_call_result.strip()
            ):
                inputs.append(CompositorInput(
                    "play_call_result", context.play_call_result.strip()))
        template = ExportTemplateSnapshot(
            template_id=choice.template.template_id,
            revision=choice.revision,
            payload_json=choice.template.to_json(),
        )
        return ExportPackageSnapshot(
            style=style,
            technical_preset=self.preset_combo.currentData(),
            accurate_cut=True,
            template=template,
            compositor_inputs=tuple(inputs),
            include_ink=self.ink_check.isChecked(),
            include_voiceover=self.voiceover_check.isChecked(),
            include_play_call=self.play_call_check.isChecked(),
            # Slate is intentionally absent from the UI and remains false.
            include_slate=False,
        ).validate()

    def _composition_option_changed(self, *_args) -> None:
        self._sync_summary()
        self._invalidate_composited_preview()
        self._refresh_preview()
        self._sync_export_readiness()

    def _sync_layer_availability(self) -> None:
        checks = (
            (
                self.ink_check,
                "ink",
                "The selected Voiceover take has no recorded presentation track.",
            ),
            (
                self.voiceover_check,
                "voiceover",
                "This clip has no selected Voiceover take with an event track.",
            ),
            (
                self.play_call_check,
                "play_call",
                "This clip needs both situation and play-call text.",
            ),
        )
        if self._export_style is ExportStyle.CLEAN:
            for check, _key, _reason in checks:
                check.setEnabled(False)
                check.setToolTip(
                    "Presentation layers are available only for Signature "
                    "and Vertical styles.")
            return
        context = self._preview_context
        if context is None:
            for check, _key, _reason in checks:
                check.setEnabled(False)
                check.setToolTip(
                    "Select a clip and wait for a decoded frame first.")
            return
        available = {
            "ink": context.voiceover_has_presentation_track,
            "voiceover": (
                bool(context.voiceover_take_id)
                and context.voiceover_frame_count > 0
                and context.voiceover_waveform is not None
                and context.voiceover_has_presentation_track
            ),
            "play_call": bool(
                context.play_call_situation.strip()
                and context.play_call_concept.strip()
            ),
        }
        for check, key, reason in checks:
            is_available = available[key]
            if not is_available:
                check.blockSignals(True)
                try:
                    check.setChecked(False)
                finally:
                    check.blockSignals(False)
            check.setEnabled(is_available)
            check.setToolTip("Include this layer" if is_available else reason)

    def _refresh_preview(self) -> None:
        context = self._preview_context
        if self._export_style is ExportStyle.CLEAN:
            self._last_preview_image = QImage()
            self.preview_label.clear()
            if context is None or not context.source_frame_png:
                self.preview_label.setText("NO SOURCE FRAME")
                self.preview_status.setText(
                    "Clean preview waits for the current decoded source frame.")
                return
            source = QImage.fromData(context.source_frame_png)
            if source.isNull():
                self.preview_label.setText("PREVIEW UNAVAILABLE")
                self.preview_status.setText(
                    "Clean preview unavailable: the decoded frame is invalid.")
                return
            self._last_preview_image = source.copy()
            self._apply_preview_image()
            self.preview_status.setText(
                "Current decoded source frame · Clean uses the existing queue.")
            return

        if self._composited_preview_ready \
                and not self._last_preview_image.isNull():
            self._apply_preview_image()
            return
        reason = self._composited_readiness_error(require_preview=False)
        self.preview_label.clear()
        if reason:
            self.preview_label.setText("PREVIEW UNAVAILABLE")
            self.preview_status.setText(reason)
            return
        self.preview_label.setText("PREPARING EXACT TAKE…")
        self.preview_status.setText(
            "Resolving Voiceover audio frame 0, recorded ink, and its painted "
            "source PTS. Playback is not moved.")
        self._schedule_composited_preview()

    def _invalidate_composited_preview(self) -> None:
        self._composited_preview_generation += 1
        if self._export_style is ExportStyle.CLEAN:
            return
        self._composited_preview_ready = False
        self._last_preview_image = QImage()
        self._last_preview_provenance = {}

    def _schedule_composited_preview(self) -> None:
        if self._preview_request_pending:
            return
        self._preview_request_pending = True

        def emit_request() -> None:
            self._preview_request_pending = False
            if self._export_style is not ExportStyle.CLEAN \
                    and not self._composited_preview_ready:
                self.composited_preview_requested.emit()

        QTimer.singleShot(0, self, emit_request)

    def set_composited_preview_pending(self) -> None:
        if self._export_style is ExportStyle.CLEAN:
            return
        self._composited_preview_ready = False
        self._last_preview_image = QImage()
        self.preview_label.clear()
        self.preview_label.setText("PREPARING EXACT TAKE…")
        self.preview_status.setText(
            "Resolving Voiceover audio frame 0, recorded ink, and its painted "
            "source PTS. Playback is not moved.")
        self._sync_export_readiness()

    def set_composited_preview_result(
            self, image: QImage, *, take_id: str, audio_frame: int,
            source_position_ms: int, marks_sha256: str) -> None:
        context = self._preview_context
        if self._export_style is ExportStyle.CLEAN \
                or context is None or context.voiceover_take_id != take_id:
            return
        if not isinstance(image, QImage) or image.isNull():
            self.set_composited_preview_error(
                "The selected-take compositor returned an empty frame.")
            return
        self._last_preview_image = image.copy()
        self._composited_preview_ready = True
        self._last_preview_provenance = {
            "audio_frame": audio_frame,
            "marks_sha256": marks_sha256,
            "source_position_ms": source_position_ms,
            "take_id": take_id,
        }
        self.preview_label.clear()
        self._apply_preview_image()
        take_label = context.voiceover_label.strip() or take_id
        self.preview_status.setText(
            f"{self._export_style.value.title()} · selected take {take_label} · "
            f"audio frame {audio_frame} · painted source "
            f"{source_position_ms / 1000:.3f}s · recorded ink")
        self._sync_export_readiness()

    def set_composited_preview_error(self, message: str) -> None:
        if self._export_style is ExportStyle.CLEAN:
            return
        self._composited_preview_ready = False
        self._last_preview_image = QImage()
        self.preview_label.clear()
        self.preview_label.setText("PREVIEW UNAVAILABLE")
        self.preview_status.setText(f"Exact selected-take preview unavailable: {message}")
        self._sync_export_readiness(preview_error=message)

    def _composited_readiness_error(
            self, *, require_preview: bool = True) -> str:
        if self._bridge_readiness_error:
            return self._bridge_readiness_error
        if self._running:
            return "An export is already running."
        if len(self._composited_clip_ids) != 1:
            return (
                "Signature and Vertical require exactly one enabled selected clip."
            )
        context = self._preview_context
        if context is None or context.clip_id != self._composited_clip_ids[0]:
            return "Select one enabled clip and wait for its export context."
        if not context.source_video_path.strip():
            return "The selected clip has no linked source video."
        choice = self.selected_template_choice()
        if choice is None:
            return self._template_load_error or (
                "Choose or create a linked Signature template.")
        if choice.template.identity.border is not None:
            return (
                "The selected template includes a border. Linked 16:9 and 9:16 "
                "border behavior is not approved yet; choose a border-free template."
            )
        if self.mode_combo.currentData() != "individual":
            return "Signature and Vertical export must use Individual clips."
        if not self.accurate_check.isChecked():
            return "Signature and Vertical require Accurate cuts."
        if not self.voiceover_check.isChecked():
            return (
                "Signature and Vertical currently require Voiceover. The "
                "soundtrack policy without Voiceover is not approved yet."
            )
        if not context.voiceover_take_id.strip() \
                or not context.voiceover_has_presentation_track \
                or context.voiceover_frame_count <= 0 \
                or context.voiceover_waveform is None:
            return (
                "Select a saved Voiceover take with a recorded presentation "
                "track before exporting Signature or Vertical."
            )
        if self.ink_check.isChecked() \
                and not context.voiceover_has_presentation_track:
            return "Recorded ink is unavailable for the selected Voiceover take."
        if self.play_call_check.isChecked() and not (
                context.play_call_situation.strip()
                and context.play_call_concept.strip()):
            return "Play call needs both situation and concept text."
        try:
            self.export_package_snapshot()
        except (TypeError, ValueError) as exc:
            return str(exc)
        if require_preview and not self._composited_preview_ready:
            return "Preparing the exact selected-take preview before export."
        return ""

    def _sync_export_readiness(self, *, preview_error: str = "") -> None:
        if self._export_style is ExportStyle.CLEAN:
            reason = self._bridge_readiness_error
            self.export_btn.setEnabled(not self._running and not reason)
            if reason:
                self.composited_unavailable_label.setText(reason)
                self.composited_unavailable_label.show()
                self.export_btn.setToolTip(reason)
            else:
                self.composited_unavailable_label.clear()
                self.composited_unavailable_label.hide()
                self.export_btn.setToolTip(
                    "Start the configured Clean batch through the existing queue")
            return
        reason = preview_error or self._composited_readiness_error()
        self.export_btn.setEnabled(not reason)
        if reason:
            self.composited_unavailable_label.setText(reason)
            self.composited_unavailable_label.show()
            self.export_btn.setToolTip(reason)
        else:
            self.composited_unavailable_label.clear()
            self.composited_unavailable_label.hide()
            self.export_btn.setToolTip(
                "Queue the immutable selected take through the production compositor")

    def _apply_preview_image(self) -> None:
        image = getattr(self, "_last_preview_image", QImage())
        if image.isNull():
            return
        target = self.preview_label.size()
        if target.width() < 2 or target.height() < 2:
            return
        self.preview_label.setPixmap(QPixmap.fromImage(image).scaled(
            target,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_preview_image()

    def set_clip_context(self, clips) -> None:
        """Provide existing clip thumbnails/durations without owning project data."""
        enabled = [clip for clip in clips if getattr(clip, "enabled", True)]
        self._selected_count = len(enabled)
        self._clip_thumbnails = {
            clip.id: clip.thumbnail_path
            for clip in clips
            if getattr(clip, "thumbnail_path", "")
        }
        self._clip_durations = {
            clip.id: clip.duration_ms for clip in clips
        }
        self._sync_summary()
        self._sync_export_readiness()
        if self.jobs:
            self._refresh_queue()

    def _preset_changed(self) -> None:
        is_fast_copy = self.preset_combo.currentData() == FAST_COPY.name
        if is_fast_copy:
            self.warning_label.setText(
                "Fast Copy follows keyframes, so a cut can begin or end a few "
                "frames outside the marks.")
            self.warning_label.show()
        else:
            self.warning_label.clear()
            self.warning_label.hide()
        self.accurate_check.setEnabled(
            self._export_style is ExportStyle.CLEAN
            and not self._running and not is_fast_copy)
        self._sync_summary()
        self._sync_export_readiness()

    def _start_export(self) -> None:
        if self._export_style is not ExportStyle.CLEAN:
            reason = self._composited_readiness_error()
            if reason:
                self.composited_unavailable_label.setText(reason)
                self.composited_unavailable_label.show()
                self.export_btn.setToolTip(reason)
                return
            self.composited_export_requested.emit()
            return
        self.export_requested.emit(
            self.mode_combo.currentData(),
            self.preset_combo.currentData(),
            self.accurate_check.isChecked(),
        )

    def _edit_setup(self) -> None:
        self.setup_stack.setCurrentIndex(0)
        self._set_stage("configure")
        self.export_btn.setFocus()

    def _sync_summary(self) -> None:
        selected_count = (
            self._selected_count
            if self._export_style is ExportStyle.CLEAN
            else len(self._composited_clip_ids)
        )
        count_text = f"{selected_count} clips"
        self.selection_label.setText(f"{count_text}\nSelected")
        self.summary_selection.setText(
            f"{selected_count} selected\nClips")
        style_text = self._export_style.value.title()
        if self._export_style is not ExportStyle.CLEAN:
            choice = self.selected_template_choice()
            if choice is not None:
                style_text += f" · {choice.template.name}"
        self.summary_style.setText(f"{style_text}\nStyle")
        self.summary_mode.setText(
            f"{self.mode_combo.currentText()}\nMode")
        self.summary_preset.setText(
            f"{self.preset_combo.currentText()}\nPreset")
        cut_text = "Accurate cuts" \
            if self.accurate_check.isChecked() else "Fast keyframe cuts"
        self.summary_cutting.setText(f"{cut_text}\nCutting")
        destination = self.output_folder_label.text()
        self.summary_destination.setText(f"{destination}\nDestination")

    def set_output_folder(self, path: str) -> None:
        value = path or "-"
        self.output_folder_label.setText(value)
        self.output_folder_label.setToolTip(value)
        self.summary_destination.setToolTip(value)
        self._sync_summary()

    def set_running(self, running: bool) -> None:
        self._running = running
        self.cancel_btn.setEnabled(running)
        self.cancel_all_btn.setEnabled(running)
        self.edit_setup_btn.setEnabled(not running)
        if self._export_style is ExportStyle.CLEAN:
            self.mode_combo.setEnabled(not running)
            self.preset_combo.setEnabled(not running)
            self.accurate_check.setEnabled(
                not running
                and self.preset_combo.currentData() != FAST_COPY.name)
        if running:
            self.setup_stack.setCurrentIndex(1)
            self._set_stage("export")
        elif self.jobs and all(
                job.status in {
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                }
                for job in self.jobs.values()
        ):
            self._set_stage("complete")
        self._sync_export_readiness()
        self._refresh_queue()

    def _set_stage(self, stage: str) -> None:
        for key, label in self._stage_labels.items():
            label.setProperty("active", "true" if key == stage else "false")
            label.style().unpolish(label)
            label.style().polish(label)

    # ---------- queue display ----------

    def load_jobs(self, jobs: list[ExportJob]) -> None:
        self.jobs = {job.id: job for job in jobs}
        for job in self.jobs.values():
            job.progress = 0.0
            job.elapsed_seconds = 0.0
        self.setup_stack.setCurrentIndex(1)
        self._set_stage("export")
        self._refresh_queue()

    def load_job_summaries(self, summaries) -> None:
        """Restore queue lanes from metadata-only durable rows."""

        jobs = []
        for summary in summaries:
            jobs.append(ExportJob(
                id=summary.id,
                project_id=summary.project_id,
                clip_id=summary.clip_id,
                clip_ids=list(summary.clip_ids),
                job_type=summary.job_type,
                status=summary.status,
                display_name=summary.display_name,
                output_path=summary.output_path,
                preset_name=summary.preset_name,
                ffmpeg_command=summary.ffmpeg_command,
                error_message=summary.error_message,
            ))
        self.load_jobs(jobs) if jobs else self.clear_jobs()

    def clear_jobs(self) -> None:
        self.jobs.clear()
        self._refresh_queue()

    def upsert_job(self, job: ExportJob) -> None:
        """Add one live job without discarding restored history rows."""

        self.jobs[job.id] = job
        self.setup_stack.setCurrentIndex(1)
        self._set_stage("export")
        self._refresh_queue()

    def remove_job(self, job_id: str) -> None:
        self.jobs.pop(job_id, None)
        self._refresh_queue()

    def on_job_started(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is not None:
            job.status = JobStatus.EXPORTING
        self._refresh_queue()

    def on_job_progress(self, job_id: str, pct: float, elapsed: float) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        job.progress = pct
        job.elapsed_seconds = elapsed
        widgets = self._active_progress_widgets.get(job_id)
        if widgets is not None:
            bar, percent, timing = widgets
            bar.setValue(round(pct))
            percent.setText(f"{round(pct)}%")
            remaining = elapsed * (100 - pct) / pct if pct > 0 else 0.0
            timing.setText(
                f"Elapsed  {_clock(elapsed)}    /    "
                f"Remaining  {_clock(remaining)}")
        self._refresh_overall()

    def on_job_completed(self, job_id: str, output_path: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        job.status = JobStatus.COMPLETED
        job.progress = 100.0
        job.output_path = output_path
        self._refresh_queue()

    def on_job_failed(self, job_id: str, error: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            return
        job.status = JobStatus.FAILED
        job.error_message = error
        self._refresh_queue()

    def on_job_cancelled(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job is not None:
            job.status = JobStatus.CANCELLED
        self._refresh_queue()

    def _refresh_queue(self) -> None:
        if self._queue_refresh_pending:
            return
        self._queue_refresh_pending = True
        QTimer.singleShot(0, self, self.flush_queue_refresh)

    def flush_queue_refresh(self) -> None:
        """Rebuild the lanes now if a refresh is pending.

        The event loop calls this; tests that assert lane contents right
        after a state change call it too, standing in for the paint that
        would otherwise be the next thing to happen.
        """
        if not self._queue_refresh_pending:
            return
        self._queue_refresh_pending = False
        self._rebuild_queue()

    def _rebuild_queue(self) -> None:
        self._active_progress_widgets.clear()
        active = [
            job for job in self.jobs.values()
            if job.status in {JobStatus.PREPARING, JobStatus.EXPORTING}
        ]
        waiting = [
            job for job in self.jobs.values()
            if job.status == JobStatus.WAITING
        ]
        finished = [
            job for job in self.jobs.values()
            if job.status in {
                JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED,
            }
        ]

        all_empty = not active and not waiting and not finished

        self.now_lane.replace_rows(
            [self._active_card(job) for job in active],
            f"{len(active)} active" if active else "",
            "The active export appears here",
        )
        self.next_lane.replace_rows(
            [self._queue_row(job, index + 1) for index, job in enumerate(waiting)],
            f"{len(waiting)} queued" if waiting else "",
            "No queued outputs",
        )
        self.complete_lane.replace_rows(
            [self._complete_row(job) for job in finished],
            f"{len(finished)} finished" if finished else "",
            "Completed outputs appear here",
        )
        for lane in (self.now_lane, self.next_lane, self.complete_lane):
            lane.set_all_empty_presentation(all_empty)
            self._lanes_layout.setAlignment(
                lane,
                Qt.AlignmentFlag.AlignTop
                if all_empty else Qt.AlignmentFlag(0),
            )
        self.cancel_all_btn.setEnabled(self._running or bool(waiting))
        self._refresh_overall()

    def _active_card(self, job: ExportJob) -> QWidget:
        card = QFrame()
        card.setObjectName("ExportActiveCard")
        card.setProperty("exportJob", "active")
        grid = QGridLayout(card)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)

        thumb = self._thumbnail(job, 112, 64)
        grid.addWidget(thumb, 0, 0, 4, 1)
        title = QLabel(Path(job.output_path).name)
        title.setProperty("role", "exportJobTitle")
        title.setToolTip(job.output_path)
        grid.addWidget(title, 0, 1, 1, 3)
        meta = QLabel(self._job_meta(job))
        meta.setProperty("role", "exportJobMeta")
        grid.addWidget(meta, 1, 1, 1, 3)
        bar = QProgressBar()
        bar.setObjectName("ExportJobProgress")
        bar.setRange(0, 100)
        bar.setValue(round(job.progress))
        bar.setTextVisible(False)
        grid.addWidget(bar, 2, 1, 1, 2)
        percent = QLabel(f"{round(job.progress)}%")
        percent.setProperty("role", "exportPercent")
        grid.addWidget(percent, 2, 3)
        remaining = 0.0
        if job.progress > 0:
            remaining = job.elapsed_seconds * (100 - job.progress) / job.progress
        timing = QLabel(
            f"Elapsed  {_clock(job.elapsed_seconds)}    /    "
            f"Remaining  {_clock(remaining)}")
        timing.setProperty("role", "exportJobMeta")
        grid.addWidget(timing, 3, 1, 1, 2)
        cancel = QPushButton("Cancel")
        cancel.setObjectName("ExportCancelCurrent")
        cancel.clicked.connect(self.cancel_current_requested.emit)
        grid.addWidget(cancel, 3, 3)
        grid.setColumnStretch(1, 1)
        self._active_progress_widgets[job.id] = (bar, percent, timing)
        return card

    def _queue_row(self, job: ExportJob, position: int) -> QWidget:
        row = QFrame()
        row.setProperty("exportJob", "queued")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(8)
        index = QLabel(str(position))
        index.setProperty("role", "exportQueueIndex")
        layout.addWidget(index)
        layout.addWidget(self._thumbnail(job, 62, 36))
        text = QVBoxLayout()
        text.setSpacing(0)
        title = QLabel(Path(job.output_path).name)
        title.setProperty("role", "exportJobTitle")
        title.setToolTip(job.output_path)
        meta = QLabel(f"Queued  •  {self._job_duration(job)}")
        meta.setProperty("role", "exportJobMeta")
        text.addWidget(title)
        text.addWidget(meta)
        layout.addLayout(text, 1)
        remove = QPushButton("Remove")
        remove.setToolTip("Remove this output from the remaining queue")
        remove.clicked.connect(
            lambda _checked=False, job_id=job.id:
            self.remove_queued_requested.emit(job_id))
        layout.addWidget(remove)
        return row

    def _complete_row(self, job: ExportJob) -> QWidget:
        row = QFrame()
        row.setProperty("exportJob", "complete")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(7, 5, 7, 5)
        layout.setSpacing(8)

        symbol = QLabel()
        symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        succeeded = job.status == JobStatus.COMPLETED
        symbol.setProperty("result", "success" if succeeded else "error")
        standard_icon = (
            QStyle.StandardPixmap.SP_DialogApplyButton
            if succeeded else QStyle.StandardPixmap.SP_DialogCancelButton
        )
        symbol.setPixmap(self.style().standardIcon(standard_icon).pixmap(13, 13))
        layout.addWidget(symbol)
        layout.addWidget(self._thumbnail(job, 62, 36))
        text = QVBoxLayout()
        text.setSpacing(0)
        title = QLabel(Path(job.output_path).name)
        title.setProperty("role", "exportJobTitle")
        title.setToolTip(job.output_path)
        if job.status == JobStatus.COMPLETED:
            details = "  •  ".join(filter(None, (
                "Completed", _clock(job.elapsed_seconds), _file_size(job.output_path)
            )))
        elif job.status == JobStatus.CANCELLED:
            details = "Cancelled"
        else:
            details = "Failed"
            title.setToolTip(job.error_message or job.output_path)
        meta = QLabel(details)
        meta.setProperty("role", "exportJobMeta")
        text.addWidget(title)
        text.addWidget(meta)
        layout.addLayout(text, 1)
        if job.status == JobStatus.COMPLETED:
            open_btn = QPushButton("Open")
            open_btn.clicked.connect(
                lambda _checked=False, path=job.output_path:
                self._open_path(path))
            folder_btn = QPushButton("Folder")
            folder_btn.clicked.connect(
                lambda _checked=False, path=job.output_path:
                self._open_path(str(Path(path).parent)))
            layout.addWidget(open_btn)
            layout.addWidget(folder_btn)
        elif job.status == JobStatus.FAILED:
            retry_btn = QPushButton("Retry")
            retry_btn.clicked.connect(
                lambda _checked=False, job_id=job.id:
                self.retry_requested.emit(job_id))
            layout.addWidget(retry_btn)
        return row

    def _thumbnail(self, job: ExportJob, width: int, height: int) -> QLabel:
        label = QLabel()
        label.setObjectName("ExportThumbnail")
        label.setFixedSize(width, height)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        clip_id = job.clip_id or (job.clip_ids[0] if job.clip_ids else "")
        path = self._clip_thumbnails.get(clip_id, "")
        pixmap = QPixmap(path) if path and Path(path).is_file() else QPixmap()
        if not pixmap.isNull():
            label.setPixmap(pixmap.scaled(
                width, height,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            ))
        else:
            label.setText("REEL" if job.job_type == JobType.REEL else "CLIP")
        return label

    def _job_duration(self, job: ExportJob) -> str:
        if job.clip_id:
            return _duration(self._clip_durations.get(job.clip_id, 0))
        return _duration(sum(
            self._clip_durations.get(clip_id, 0) for clip_id in job.clip_ids))

    def _job_meta(self, job: ExportJob) -> str:
        preset = BUILTIN_PRESETS.get(job.preset_name)
        preset_text = preset.display_name if preset else job.preset_name
        kind = "Combined reel" if job.job_type == JobType.REEL else "Clip"
        return f"{kind}  •  {self._job_duration(job)}  •  {preset_text}"

    def _refresh_overall(self) -> None:
        jobs = list(self.jobs.values())
        if not jobs:
            self.overall_label.setText("Ready to export")
            self.overall_progress.setValue(0)
            return
        completed = sum(
            job.status == JobStatus.COMPLETED for job in jobs)
        terminal = sum(
            job.status in {
                JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED,
            }
            for job in jobs)
        average = sum(
            100.0 if job.status == JobStatus.COMPLETED else job.progress
            for job in jobs
        ) / len(jobs)
        self.overall_progress.setValue(round(average))
        if terminal == len(jobs):
            failed = sum(job.status == JobStatus.FAILED for job in jobs)
            if failed:
                self.overall_label.setText(
                    f"{completed} of {len(jobs)} outputs complete  •  "
                    f"{failed} needs attention")
            else:
                self.overall_label.setText(
                    f"{completed} of {len(jobs)} outputs complete")
        else:
            self.overall_label.setText(
                f"{completed} of {len(jobs)} outputs complete")

    @staticmethod
    def _open_path(path: str) -> None:
        if Path(path).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))
