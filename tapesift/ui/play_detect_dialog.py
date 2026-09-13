"""Detect Plays dialog: run detection, review the result, create the clips."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTemporaryDir, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QProgressBar,
    QPushButton, QSizePolicy, QStackedWidget, QStyle, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from tapesift.models.clip import Clip
from tapesift.services.play_detect_service import (
    DEFAULT_MAX_PLAY_S, DEFAULT_MIN_PLAY_S, DEFAULT_SCENE_THRESHOLD,
    DEFAULT_SEPARATOR_MAX_S, COVERAGE_POSSIBLE_MISSED, COVERAGE_REVIEW,
    DetectionResult,
)
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_v2.icon_utils import tinted_standard_icon
from tapesift.workers.analysis_preview_worker import AnalysisPreviewWorker
from tapesift.workers.play_detect_worker import PlayDetectWorker
from tapesift.workers.thumbnail_worker import ThumbnailWorker

SIGNAL_LABELS = {
    "black": "black gaps between plays",
    "scene": "hard cuts between plays",
    "mixed": "black gaps (with cuts present)",
    "none": "no boundaries found",
}


def _refresh_style(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class DetectionCoverageRail(QWidget):
    """Compact, complete-source view of detected and uncertain ranges."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DetectCoverageRail")
        self.setMinimumHeight(28)
        self.setMaximumHeight(28)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._result: DetectionResult | None = None
        self._selected: tuple[str, int] | None = None
        self.setAccessibleName(
            "Source film timeline: green is detected, amber needs review")

    def set_result(self, result: DetectionResult | None) -> None:
        self._result = result
        self.update()

    def set_selected(self, key: tuple[str, int] | None) -> None:
        self._selected = key
        self.update()

    def _ranges(self) -> list[tuple[int, int, str, tuple[str, int] | None]]:
        result = self._result
        if result is None or result.duration_ms <= 0:
            return []
        ranges: list[tuple[int, int, str, tuple[str, int] | None]] = []
        if result.coverage_segments:
            for segment in result.coverage_segments:
                key = None
                if (
                    segment.candidate_kind
                    and segment.candidate_index is not None
                ):
                    key = (
                        segment.candidate_kind,
                        segment.candidate_index,
                    )
                kind = (
                    "review"
                    if segment.kind
                    in {COVERAGE_REVIEW, COVERAGE_POSSIBLE_MISSED}
                    else segment.kind
                )
                ranges.append((
                    segment.start_ms,
                    segment.end_ms,
                    kind,
                    key,
                ))
            return ranges

        candidates: list[
            tuple[int, int, str, tuple[str, int]]
        ] = []
        for index, play in enumerate(result.plays):
            candidates.append((
                play.start_ms,
                play.end_ms,
                "review" if play.needs_review else "play",
                ("play", index),
            ))
        for index, segment in enumerate(result.unclassified):
            candidates.append((
                segment.start_ms,
                segment.end_ms,
                "review",
                ("unclassified", index),
            ))
        candidates.sort(key=lambda item: (item[0], item[1]))
        cursor = 0
        for start, end, kind, key in candidates:
            if start > cursor:
                ranges.append((cursor, start, "separator", None))
            ranges.append((start, end, kind, key))
            cursor = max(cursor, end)
        if cursor < result.duration_ms:
            ranges.append((
                cursor, result.duration_ms, "separator", None))
        return ranges

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#0c0f0d"))
        result = self._result
        if result is None or result.duration_ms <= 0:
            painter.setPen(QColor("#565345"))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
            return

        track = QRectF(
            1.0,
            4.0,
            max(1.0, self.width() - 2.0),
            max(1.0, self.height() - 8.0),
        )
        painter.fillRect(track, QColor("#2b2b24"))
        colors = {
            "play": QColor("#52d67f"),
            "review": QColor("#e0b341"),
            "possible_missed": QColor("#e0b341"),
            "separator": QColor("#34372f"),
        }
        for start, end, kind, key in self._ranges():
            left = track.left() + (
                track.width() * start / result.duration_ms)
            right = track.left() + (
                track.width() * end / result.duration_ms)
            width = max(1.0, right - left)
            block = QRectF(left, track.top(), width, track.height())
            painter.fillRect(block, colors.get(kind, QColor("#647169")))
            painter.setPen(QPen(QColor("#111512"), 1))
            painter.drawLine(
                int(block.right()), int(block.top()),
                int(block.right()), int(block.bottom()))
            if key is not None and key == self._selected:
                painter.setPen(QPen(QColor("#f2f5f2"), 2))
                painter.drawRect(block.adjusted(1, 1, -1, -1))

        painter.setPen(QPen(QColor("#565345"), 1))
        painter.drawRect(track)


def _draw_pixmap_cover(
    painter: QPainter,
    pixmap: QPixmap,
    target: QRectF,
) -> None:
    """Draw a pixmap into a target with centered cover-style cropping."""
    source = QRectF(pixmap.rect())
    if source.isEmpty() or target.isEmpty():
        return
    source_ratio = source.width() / source.height()
    target_ratio = target.width() / target.height()
    if source_ratio > target_ratio:
        new_width = source.height() * target_ratio
        source.setLeft(source.center().x() - new_width / 2)
        source.setWidth(new_width)
    elif source_ratio < target_ratio:
        new_height = source.width() / target_ratio
        source.setTop(source.center().y() - new_height / 2)
        source.setHeight(new_height)
    painter.drawPixmap(target, pixmap, source)


class AnalysisFrameViewport(QWidget):
    """Large source frame with a restrained moving scan treatment."""

    def __init__(self, scan_panel: "LiveFrameScanPanel") -> None:
        super().__init__(scan_panel)
        self.scan_panel = scan_panel
        self.setObjectName("DetectAnalysisFrameViewport")
        self.setFixedHeight(310)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        outer = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        image_rect = outer.adjusted(5, 5, -5, -23)
        painter.fillRect(outer, QColor("#0b0d0c"))
        painter.setPen(QPen(QColor("#343a34"), 1))
        painter.drawRect(outer)

        frame = self.scan_panel.current_frame()
        if frame is None:
            painter.setPen(QColor("#72786f"))
            painter.drawText(
                image_rect,
                Qt.AlignmentFlag.AlignCenter,
                "Preparing source frames…",
            )
        else:
            _timestamp_ms, pixmap = frame
            _draw_pixmap_cover(painter, pixmap, image_rect)

            scan_x = (
                image_rect.left()
                + image_rect.width() * self.scan_panel.scan_phase
            )
            scan_band = QRectF(
                scan_x - 13,
                image_rect.top(),
                26,
                image_rect.height(),
            ).intersected(image_rect)
            painter.fillRect(scan_band, QColor(57, 224, 122, 34))
            painter.setPen(QPen(QColor("#53eb91"), 2))
            painter.drawLine(
                int(scan_x),
                int(image_rect.top()),
                int(scan_x),
                int(image_rect.bottom()),
            )

            bracket = 14
            painter.setPen(QPen(QColor("#53eb91"), 2))
            for x, y, sx, sy in (
                (image_rect.left(), image_rect.top(), 1, 1),
                (image_rect.right(), image_rect.top(), -1, 1),
                (image_rect.left(), image_rect.bottom(), 1, -1),
                (image_rect.right(), image_rect.bottom(), -1, -1),
            ):
                painter.drawLine(
                    int(x), int(y), int(x + sx * bracket), int(y))
                painter.drawLine(
                    int(x), int(y), int(x), int(y + sy * bracket))

        ruler_y = outer.bottom() - 12
        painter.setPen(QPen(QColor("#4e6655"), 1))
        for index in range(41):
            x = image_rect.left() + image_rect.width() * index / 40
            height = 7 if index % 5 == 0 else 4
            painter.drawLine(
                int(x),
                int(ruler_y - height / 2),
                int(x),
                int(ruler_y + height / 2),
            )


class AnalysisFilmstrip(QWidget):
    """Three-frame motion strip centered on the current source frame."""

    def __init__(self, scan_panel: "LiveFrameScanPanel") -> None:
        super().__init__(scan_panel)
        self.scan_panel = scan_panel
        self.setObjectName("DetectAnalysisFilmstrip")
        self.setFixedHeight(76)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        frames = self.scan_panel.neighbor_frames()
        gap = 6
        panel_width = (self.width() - gap * 2) / 3
        for slot in range(3):
            target = QRectF(
                slot * (panel_width + gap),
                0,
                panel_width,
                self.height() - 1,
            )
            painter.fillRect(target, QColor("#0b0d0c"))
            if slot < len(frames) and frames[slot] is not None:
                _timestamp_ms, pixmap = frames[slot]
                _draw_pixmap_cover(painter, pixmap, target)
                if slot != 1:
                    painter.fillRect(target, QColor(8, 10, 9, 142))
            painter.setPen(QPen(
                QColor("#42df7d") if slot == 1 else QColor("#2e352f"),
                2 if slot == 1 else 1,
            ))
            painter.drawRect(target.adjusted(1, 1, -1, -1))


class LiveFrameScanPanel(QWidget):
    """Animated, source-backed Analyze visualization."""

    def __init__(self, duration_ms: int, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DetectLiveFrameScan")
        self.setAccessibleName(
            "Live source-film frames moving through play detection")
        self.duration_ms = max(0, int(duration_ms))
        self.scan_phase = 0.0
        self._frames: dict[int, tuple[int, QPixmap]] = {}
        self._current_position = 0
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance_scan)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.viewport = AnalysisFrameViewport(self)
        layout.addWidget(self.viewport)
        self.time_label = QLabel(
            f"Preparing source frames  |  {format_ms(self.duration_ms)} source")
        self.time_label.setObjectName("DetectAnalysisTime")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.time_label)
        self.filmstrip = AnalysisFilmstrip(self)
        layout.addWidget(self.filmstrip)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def clear_frames(self) -> None:
        self._frames.clear()
        self._current_position = 0
        self.scan_phase = 0.0
        self.time_label.setText(
            f"Preparing source frames  |  "
            f"{format_ms(self.duration_ms)} source")
        self.viewport.update()
        self.filmstrip.update()

    def add_frame(
        self,
        sample_index: int,
        timestamp_ms: int,
        pixmap: QPixmap,
    ) -> None:
        if pixmap.isNull():
            return
        self._frames[int(sample_index)] = (int(timestamp_ms), pixmap)
        self._current_position = min(
            self._current_position,
            max(0, len(self._frames) - 1),
        )
        self._update_time_label()
        self.viewport.update()
        self.filmstrip.update()

    def current_frame(self) -> tuple[int, QPixmap] | None:
        frames = self._ordered_frames()
        if not frames:
            return None
        return frames[self._current_position % len(frames)]

    def neighbor_frames(
        self,
    ) -> list[tuple[int, QPixmap] | None]:
        frames = self._ordered_frames()
        if not frames:
            return [None, None, None]
        current = self._current_position % len(frames)
        if len(frames) == 1:
            return [None, frames[0], None]
        return [
            frames[(current - 1) % len(frames)],
            frames[current],
            frames[(current + 1) % len(frames)],
        ]

    def _ordered_frames(self) -> list[tuple[int, QPixmap]]:
        return [self._frames[key] for key in sorted(self._frames)]

    def _advance_scan(self) -> None:
        self.scan_phase += 0.035
        if self.scan_phase >= 1.0:
            self.scan_phase = 0.0
            frames = self._ordered_frames()
            if len(frames) > 1:
                self._current_position = (
                    self._current_position + 1
                ) % len(frames)
                self._update_time_label()
                self.filmstrip.update()
        self.viewport.update()

    def _update_time_label(self) -> None:
        frame = self.current_frame()
        if frame is None:
            return
        timestamp_ms, _pixmap = frame
        self.time_label.setText(
            f"Scanning source  {format_ms(timestamp_ms)}  /  "
            f"{format_ms(self.duration_ms)}")


class AnalysisActivityProgress(QProgressBar):
    """Single-segment indeterminate activity bar without wrap artifacts."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DetectAnalysisProgress")
        self.setAccessibleName("Detect Plays analysis progress")
        self.setRange(0, 0)
        self.setTextVisible(False)
        self.setFixedHeight(8)
        self._phase = 0.08
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _advance(self) -> None:
        self._phase += 0.012
        if self._phase > 1.0:
            self._phase = 0.0
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        track = QRectF(self.rect()).adjusted(0, 1, 0, -1)
        painter.fillRect(track, QColor("#343730"))
        segment_width = max(34.0, track.width() * 0.34)
        travel = max(0.0, track.width() - segment_width)
        segment = QRectF(
            track.left() + travel * self._phase,
            track.top(),
            segment_width,
            track.height(),
        )
        painter.fillRect(segment, QColor("#39e07a"))


class PlayDetectDialog(QDialog):
    """Finds plays in the loaded film and hands the accepted list back."""

    def __init__(self, ffmpeg_path: str, source: Path, duration_ms: int,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Detect Plays (Beta)")
        self.setObjectName("V2DetectPlaysDialog")
        self.setProperty("tapesiftDialog", True)
        self.setModal(True)
        self.setMinimumSize(818, 395)
        self.resize(818, 395)
        self.ffmpeg_path = ffmpeg_path
        self.source = source
        self.duration_ms = duration_ms
        self.result: DetectionResult | None = None
        self.runtime_seconds: float | None = None
        self._run_started: float | None = None
        self._worker: PlayDetectWorker | None = None
        self._stage = "setup"
        self._review_focus = True
        self._review_decisions: dict[tuple[str, int], str] = {}
        self._preview_generation = 0
        self._preview_ids: dict[str, int] = {}
        self._preview_worker: ThumbnailWorker | None = None
        self._pending_preview: dict | None = None
        self._preview_temp = QTemporaryDir("TapeSift-detect-preview-XXXXXX")
        self._analysis_preview_worker: AnalysisPreviewWorker | None = None
        self._analysis_preview_generation = 0
        self._analysis_preview_temp = QTemporaryDir(
            "TapeSift-analysis-preview-XXXXXX")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 12, 20, 12)
        layout.setSpacing(8)

        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        heading = QVBoxLayout()
        heading.setSpacing(1)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel("DETECT PLAYS")
        title.setObjectName("DetectDialogTitle")
        title_row.addWidget(title)
        self.stage_description = QLabel("Set up detection")
        self.stage_description.setObjectName("DetectStageDescription")
        title_row.addWidget(
            self.stage_description, 0, Qt.AlignmentFlag.AlignBottom)
        title_row.addStretch(1)
        heading.addLayout(title_row)
        self.source_label = QLabel(source.stem)
        self.source_label.setObjectName("DetectSourceName")
        self.source_label.setProperty("role", "subtle")
        heading.addWidget(self.source_label)
        header_row.addLayout(heading, 1)
        beta = QLabel("BETA  ·  ALL-22 ONLY")
        beta.setObjectName("DetectBetaBadge")
        beta.setProperty("role", "warning")
        header_row.addWidget(
            beta, 0, Qt.AlignmentFlag.AlignTop)
        self.close_button = QToolButton()
        self.close_button.setObjectName("DetectDialogClose")
        self.close_button.setAccessibleName("Close Detect Plays")
        self.close_button.setToolTip("Close")
        close_icon = (
            Path(__file__).resolve().parents[1]
            / "resources" / "icons" / "tapesift-close.png")
        if close_icon.is_file():
            self.close_button.setIcon(QIcon(str(close_icon)))
            self.close_button.setProperty(
                "iconLibrary", "Segoe Fluent Icons")
            self.close_button.setProperty(
                "iconAsset", "tapesift-close.png")
        else:
            self.close_button.setIcon(tinted_standard_icon(
                self,
                QStyle.StandardPixmap.SP_TitleBarCloseButton,
                size=QSize(14, 14),
            ))
        self.close_button.setIconSize(QSize(14, 14))
        self.close_button.setFixedSize(28, 28)
        self.close_button.clicked.connect(self.reject)
        header_row.addWidget(
            self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        # The native dialog frame already carries the title and close action.
        # Keep these legacy widgets alive for stage/status logic, but remove
        # the duplicate in-panel masthead from the compact locked surface.
        for widget in (
                title, self.stage_description, self.source_label,
                beta, self.close_button):
            widget.hide()
        layout.addLayout(header_row)

        header_rule = QFrame()
        header_rule.setObjectName("DetectHeaderRule")
        header_rule.setFixedHeight(1)
        layout.addWidget(header_rule)

        steps = QHBoxLayout()
        steps.setContentsMargins(40, 0, 40, 0)
        steps.setSpacing(14)
        self.step_labels: dict[str, QLabel] = {}
        for index, (name, text) in enumerate((
            ("setup", "1  Setup"),
            ("analyze", "2  Analyze"),
            ("review", "3  Review"),
        )):
            label = QLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setProperty("detectStep", "true")
            self.step_labels[name] = label
            steps.addWidget(label, 1)
            if index < 2:
                divider = QFrame()
                divider.setProperty("detectStepDivider", "true")
                divider.setFixedSize(1, 18)
                steps.addWidget(divider)
        layout.addLayout(steps)

        step_rule = QFrame()
        step_rule.setObjectName("DetectStepRule")
        step_rule.setFixedHeight(1)
        layout.addWidget(step_rule)

        self.pages = QStackedWidget()
        self.pages.setObjectName("DetectStagePages")
        self.pages.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Ignored,
        )
        layout.addWidget(self.pages, 1)

        # Stage 1: setup. The detector parameters and scope are unchanged.
        setup_page = QWidget()
        setup_page.setObjectName("DetectSetupPage")
        setup = QHBoxLayout(setup_page)
        setup.setContentsMargins(6, 6, 6, 2)
        setup.setSpacing(12)
        guidance = QFrame(setup_page)
        guidance.setObjectName("DetectSetupGuidance")
        guidance_layout = QVBoxLayout(guidance)
        guidance_layout.setContentsMargins(14, 12, 14, 12)
        guidance_layout.setSpacing(8)
        self.scope_notice = QLabel(
            "BETA FEATURE  |  ALL-22 FOOTBALL FILM ONLY\n"
            "Automatic detection is a beta feature designed for cut-up "
            "All-22 footage with "
            "each play separated by a black gap, title card, or hard cut. "
            "Broadcast footage, highlight reels, and sideline recordings are "
            "not supported. Review the detected list before creating clips.")
        self.scope_notice.setObjectName("All22DetectionNotice")
        self.scope_notice.setWordWrap(True)
        self.scope_notice.setProperty("role", "warning")
        guidance_layout.addWidget(self.scope_notice)

        blurb = QLabel(
            "Cut-up film separates every play with a black gap, a graphic "
            "card, or a hard cut. TapeSift finds those boundaries and turns "
            "each play into a clip with its in/out already set.")
        blurb.setWordWrap(True)
        blurb.setProperty("role", "subtle")
        guidance_layout.addWidget(blurb)
        guidance_layout.addStretch(1)

        settings_frame = QFrame()
        settings_frame.setObjectName("DetectSetupSettings")
        settings_frame.setProperty("detectSettings", "true")
        settings_layout = QVBoxLayout(settings_frame)
        settings_layout.setContentsMargins(14, 10, 14, 10)
        settings_layout.setSpacing(7)
        settings_title = QLabel("DETECTION SETTINGS")
        settings_title.setProperty("role", "eyebrow")
        settings_layout.addWidget(settings_title)
        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(6)
        self.separator_spin = QDoubleSpinBox()
        self.separator_spin.setRange(0.5, 20.0)
        self.separator_spin.setSingleStep(0.5)
        self.separator_spin.setSuffix(" s")
        self.separator_spin.setValue(DEFAULT_SEPARATOR_MAX_S)
        self.separator_spin.setToolTip(
            "Anything shorter than this between plays is treated as a "
            "separator (black gap, scoreboard card, bumper) rather than a "
            "play. Raise it if separators are being kept as clips.")
        self.min_spin = QDoubleSpinBox()
        self.min_spin.setRange(1.0, 60.0)
        self.min_spin.setSuffix(" s")
        self.min_spin.setValue(DEFAULT_MIN_PLAY_S)
        self.max_spin = QDoubleSpinBox()
        self.max_spin.setRange(10.0, 600.0)
        self.max_spin.setSuffix(" s")
        self.max_spin.setValue(DEFAULT_MAX_PLAY_S)
        self.max_spin.setToolTip(
            "An absolute cap. TapeSift also works out this film's own typical "
            "play length and will use a tighter ceiling if it finds one - "
            "anything longer is offered for review, never discarded.")
        form.addRow("Separator up to:", self.separator_spin)
        form.addRow("Shortest play:", self.min_spin)
        form.addRow("Never longer than:", self.max_spin)
        settings_layout.addLayout(form)

        settings_action_rule = QFrame()
        settings_action_rule.setObjectName("DetectSettingsActionRule")
        settings_action_rule.setFixedHeight(1)
        settings_layout.addWidget(settings_action_rule)
        run_row = QHBoxLayout()
        run_row.setContentsMargins(0, 2, 0, 0)
        run_hint = QLabel(
            "Detection reads the source film and does not modify it.")
        run_hint.setObjectName("DetectRunHint")
        run_hint.setProperty("role", "subtle")
        run_hint.setWordWrap(True)
        run_row.addWidget(run_hint)
        run_row.addStretch(1)
        self.run_btn = QPushButton("Run Beta Detection")
        self.run_btn.setObjectName("DetectRunButton")
        self.run_btn.setProperty("accent", "true")
        self.run_btn.setProperty("primary", "true")
        self.run_btn.setMinimumWidth(190)
        self.run_btn.clicked.connect(self._run)
        run_row.addWidget(self.run_btn)
        settings_layout.addLayout(run_row)

        self.setup_error = QLabel("")
        self.setup_error.setObjectName("DetectSetupError")
        self.setup_error.setProperty("role", "error")
        self.setup_error.setWordWrap(True)
        self.setup_error.hide()
        settings_layout.addWidget(self.setup_error)
        setup.addWidget(settings_frame, 3)
        setup.addWidget(guidance, 2)
        self.pages.addWidget(setup_page)

        # Stage 2: analysis. This remains the existing background worker.
        analyze_page = QWidget()
        analyze_page.setObjectName("DetectAnalyzePage")
        analyze = QVBoxLayout(analyze_page)
        analyze.setContentsMargins(30, 18, 30, 18)
        analyze.addStretch(1)

        analysis_row = QHBoxLayout()
        analysis_row.addStretch(1)
        self.analysis_card = QFrame()
        self.analysis_card.setObjectName("DetectAnalysisCard")
        self.analysis_card.setProperty("detectAnalysisCard", "true")
        self.analysis_card.setMinimumWidth(650)
        self.analysis_card.setMaximumWidth(720)
        self.analysis_card.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        analysis_card_layout = QVBoxLayout(self.analysis_card)
        analysis_card_layout.setContentsMargins(24, 16, 24, 16)
        analysis_card_layout.setSpacing(8)
        analysis_kicker = QLabel("LIVE FRAME SCAN")
        analysis_kicker.setObjectName("DetectAnalysisKicker")
        analysis_kicker.setProperty("role", "eyebrow")
        analysis_kicker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        analysis_card_layout.addWidget(analysis_kicker)
        self.live_frame_scan = LiveFrameScanPanel(
            duration_ms, self.analysis_card)
        analysis_card_layout.addWidget(self.live_frame_scan)
        self.analysis_status = QLabel(
            "Reading frames and locating scene boundaries")
        self.analysis_status.setObjectName("DetectAnalysisStatus")
        self.analysis_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.analysis_status.setWordWrap(True)
        analysis_card_layout.addWidget(self.analysis_status)
        self.progress = AnalysisActivityProgress(self.analysis_card)
        analysis_card_layout.addWidget(self.progress)
        self.analysis_source = QLabel(
            f"{source.name}  |  {format_ms(duration_ms)} source")
        self.analysis_source.setObjectName("DetectAnalysisSource")
        self.analysis_source.setProperty("role", "micro")
        self.analysis_source.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.analysis_source.setWordWrap(True)
        analysis_card_layout.addWidget(self.analysis_source)
        analysis_note = QLabel(
            "You can cancel safely. No source-film data will be changed.")
        analysis_note.setObjectName("DetectAnalysisNote")
        analysis_note.setProperty("role", "subtle")
        analysis_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        analysis_note.setWordWrap(True)
        analysis_card_layout.addWidget(analysis_note)
        analysis_row.addWidget(self.analysis_card, 1)
        analysis_row.addStretch(1)
        analyze.addLayout(analysis_row)
        analyze.addStretch(1)
        self.pages.addWidget(analyze_page)

        # Stage 3: review. Uncertain ranges are promoted to the top without
        # changing the detector output or silently dropping any source span.
        review_page = QWidget()
        review_page.setObjectName("DetectReviewPage")
        review = QVBoxLayout(review_page)
        review.setContentsMargins(0, 4, 0, 0)
        review.setSpacing(8)

        self.status_label = QLabel("")
        self.status_label.setObjectName("DetectResultSummary")
        review.addWidget(self.status_label)
        coverage_caption = QLabel(
            "Source film timeline  (green = detected, amber = needs review)")
        coverage_caption.setObjectName("DetectCoverageCaption")
        coverage_caption.setProperty("role", "subtle")
        review.addWidget(coverage_caption)
        self.coverage_rail = DetectionCoverageRail()
        review.addWidget(self.coverage_rail)
        times = QHBoxLayout()
        self.coverage_start = QLabel("00:00")
        self.coverage_end = QLabel(format_ms(duration_ms))
        for label in (self.coverage_start, self.coverage_end):
            label.setProperty("role", "micro")
        times.addWidget(self.coverage_start)
        times.addStretch(1)
        times.addWidget(self.coverage_end)
        review.addLayout(times)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.all_filter = QPushButton("All 0")
        self.all_filter.setObjectName("DetectAllFilter")
        self.all_filter.setCheckable(True)
        self.all_filter.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.all_filter.clicked.connect(
            lambda: self._set_review_focus(False))
        filters.addWidget(self.all_filter)
        self.review_filter = QPushButton("Needs review 0")
        self.review_filter.setObjectName("DetectReviewFilter")
        self.review_filter.setCheckable(True)
        self.review_filter.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.review_filter.setProperty("reviewFilter", "true")
        self.review_filter.clicked.connect(
            lambda: self._set_review_focus(True))
        filters.addWidget(self.review_filter)
        filters.addStretch(1)
        review.addLayout(filters)

        self.table = QTableWidget(0, 6)
        self.table.setObjectName("DetectResultsTable")
        self.table.setHorizontalHeaderLabels([
            "Type", "Start", "End", "Length", "Angles",
            "Why it needs review",
        ])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        # The last column stretches to the viewport, so a horizontal bar only
        # creates a dead corner beneath the vertical scrollbar.  Keep that
        # corner out of the compact review stack.
        self.table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.setMinimumHeight(108)
        self.table.setMaximumHeight(148)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.currentCellChanged.connect(
            self._selected_result_changed)
        review.addWidget(self.table, 1)

        self.review_detail = QFrame()
        self.review_detail.setObjectName("DetectReviewDetail")
        self.review_detail.setProperty("reviewDetail", "true")
        detail = QVBoxLayout(self.review_detail)
        detail.setContentsMargins(10, 8, 14, 8)
        detail.setSpacing(7)
        detail_top = QHBoxLayout()
        self.review_range_label = QLabel("")
        self.review_range_label.setObjectName("DetectReviewRange")
        self.review_range_label.setProperty("role", "subtle")
        self.review_range_label.setWordWrap(True)
        self.review_range_label.setMinimumHeight(34)
        detail_top.addWidget(self.review_range_label, 1)
        self.keep_button = QPushButton("Keep as clip")
        self.keep_button.setObjectName("DetectKeepRange")
        self.keep_button.clicked.connect(
            lambda: self._decide_selected("keep"))
        detail_top.addWidget(self.keep_button)
        self.dismiss_button = QPushButton("Dismiss section")
        self.dismiss_button.setObjectName("DetectDismissRange")
        self.dismiss_button.clicked.connect(
            lambda: self._decide_selected("dismiss"))
        detail_top.addWidget(self.dismiss_button)
        detail.addLayout(detail_top)
        self.preview_strip = QWidget()
        self.preview_strip.setObjectName("DetectPreviewStrip")
        preview_layout = QHBoxLayout(self.preview_strip)
        preview_layout.setContentsMargins(0, 0, 2, 0)
        preview_layout.setSpacing(2)
        self.preview_frames: list[QLabel] = []
        for index in range(5):
            frame = QLabel("Loading frame…")
            frame.setObjectName(f"DetectPreviewFrame{index + 1}")
            frame.setProperty("previewFrame", "true")
            frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
            frame.setMinimumHeight(62)
            frame.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Fixed,
            )
            preview_layout.addWidget(frame, 1)
            self.preview_frames.append(frame)
        detail.addWidget(self.preview_strip)
        review.addWidget(self.review_detail)

        self.confident_table = QTableWidget(0, 6)
        self.confident_table.setObjectName("DetectConfidentResultsTable")
        self.confident_table.setHorizontalHeaderLabels([
            "Type", "Start", "End", "Length", "Angles",
            "Why it needs review",
        ])
        self.confident_table.horizontalHeader().hide()
        self.confident_table.verticalHeader().setVisible(False)
        self.confident_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self.confident_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.confident_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self.confident_table.setAlternatingRowColors(True)
        self.confident_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.confident_table.setVerticalScrollMode(
            QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.confident_table.setMinimumHeight(104)
        confident_header = self.confident_table.horizontalHeader()
        for column in range(5):
            confident_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents)
        confident_header.setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch)
        review.addWidget(self.confident_table, 1)

        self.review_settings_bar = QWidget()
        self.review_settings_bar.setObjectName("DetectSettingsBar")
        settings_row = QHBoxLayout(self.review_settings_bar)
        settings_row.setContentsMargins(0, 4, 0, 0)
        settings_row.setSpacing(8)
        self.review_settings_toggle = QToolButton()
        self.review_settings_toggle.setObjectName("DetectSettingsToggle")
        self.review_settings_toggle.setText("Detection settings")
        self.review_settings_toggle.setCheckable(True)
        self.review_settings_toggle.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.review_settings_toggle.setArrowType(
            Qt.ArrowType.RightArrow)
        self.review_settings_toggle.toggled.connect(
            self._toggle_review_settings)
        settings_row.addWidget(self.review_settings_toggle)
        self.review_settings_summary = QLabel("")
        self.review_settings_summary.setObjectName(
            "DetectSettingsSummary")
        self.review_settings_summary.setProperty("role", "subtle")
        self.review_settings_summary.hide()
        settings_row.addWidget(self.review_settings_summary, 1)
        settings_row.addStretch(1)
        self.review_run_btn = QPushButton("Run Again")
        self.review_run_btn.setObjectName("DetectRunAgain")
        self.review_run_btn.clicked.connect(self._run)
        settings_row.addWidget(self.review_run_btn)
        review.addWidget(self.review_settings_bar)

        self.wide_only_check = QCheckBox(
            "First camera angle only (skip the repeat angle)")
        self.wide_only_check.setToolTip(
            "These cut-ups usually show each play twice - a wide angle then a "
            "tight one. Tick this to clip only the first angle.")
        self.wide_only_check.toggled.connect(
            lambda _checked: self._update_create_button())
        review.addWidget(self.wide_only_check)
        self.pages.addWidget(review_page)

        footer_rule = QFrame()
        footer_rule.setObjectName("DetectFooterRule")
        footer_rule.setFixedHeight(1)
        layout.addWidget(footer_rule)
        footer = QHBoxLayout()
        self.back_button = QPushButton("Back")
        self.back_button.setObjectName("DetectBackButton")
        self.back_button.clicked.connect(
            lambda: self._set_stage("setup"))
        footer.addWidget(self.back_button)
        footer.addStretch(1)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        self.create_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Ok)
        self.create_button.setObjectName("DetectCreateClips")
        self.create_button.setProperty("primary", "true")
        self.cancel_button = self.buttons.button(
            QDialogButtonBox.StandardButton.Cancel)
        self.cancel_button.setObjectName("DetectCancel")
        self.create_button.setText("Create Clips")
        self.buttons.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self._cancel_or_reject)
        self._set_ok_enabled(False)
        footer.addWidget(self.buttons)
        layout.addLayout(footer)

        for index in range(self.pages.count()):
            self.pages.widget(index).setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Ignored,
            )
        self._set_stage("setup")

    def _set_ok_enabled(self, enabled: bool) -> None:
        self.create_button.setEnabled(enabled)

    def _set_stage(self, stage: str) -> None:
        self._stage = stage
        stage_size = {
            "setup": (818, 395),
            "analyze": (930, 650),
            "review": (930, 800),
        }[stage]
        self.setMinimumSize(*stage_size)
        self.resize(*stage_size)
        if stage != "review" and (
            self._preview_worker
            and self._preview_worker.isRunning()
        ):
            self._preview_generation += 1
            self._pending_preview = None
            self._preview_worker.stop()
        indexes = {"setup": 0, "analyze": 1, "review": 2}
        descriptions = {
            "setup": "Set up detection",
            "analyze": "Analyze source film",
            "review": "Review detected plays",
        }
        self.pages.setCurrentIndex(indexes[stage])
        self.stage_description.setText(descriptions[stage])
        for name, label in self.step_labels.items():
            label.setProperty(
                "active", "true" if name == stage else "false")
            _refresh_style(label)
        self.back_button.setVisible(stage == "review")
        self.create_button.setVisible(stage == "review")
        self.cancel_button.setVisible(True)
        self.cancel_button.setText(
            "Cancel detection" if stage == "analyze" else "Cancel")
        for button in (
                self.run_btn, self.back_button,
                self.cancel_button, self.create_button):
            button.setAutoDefault(False)
            button.setDefault(False)
        if stage == "setup":
            self.run_btn.setAutoDefault(True)
            self.run_btn.setDefault(True)
        elif stage == "review":
            self.create_button.setAutoDefault(True)
            self.create_button.setDefault(True)
        if stage == "setup":
            self.live_frame_scan.stop()
            self.progress.stop()
            self.run_btn.setFocus(Qt.FocusReason.OtherFocusReason)
        elif stage == "analyze":
            self.live_frame_scan.start()
            self.progress.start()
        elif stage == "review":
            self.live_frame_scan.stop()
            self.progress.stop()
            self._update_create_button()

    def _cancel_or_reject(self) -> None:
        if self._stage != "analyze":
            self.reject()
            return
        worker = self._worker
        if worker and worker.isRunning():
            worker.cancel()
        self._worker = None
        self._stop_analysis_preview_worker()
        self.run_btn.setEnabled(True)
        self.analysis_status.setText(
            "Reading frames and locating scene boundaries")
        self._set_stage("setup")

    def _toggle_review_settings(self, expanded: bool) -> None:
        self.review_settings_toggle.setArrowType(
            Qt.ArrowType.DownArrow
            if expanded else Qt.ArrowType.RightArrow)
        self.review_settings_summary.setText(
            f"Separator {self.separator_spin.value():.1f}s  ·  "
            f"Shortest {self.min_spin.value():.1f}s  ·  "
            f"Maximum {self.max_spin.value():.1f}s  ·  "
            "Back to edit")
        self.review_settings_summary.setVisible(expanded)

    # ---------- detection ----------

    def _run(self) -> None:
        self.run_btn.setEnabled(False)
        self._set_ok_enabled(False)
        self.setup_error.hide()
        self._set_stage("analyze")
        self.analysis_status.setText(
            "Reading frames and locating scene boundaries")
        self.live_frame_scan.clear_frames()
        self._start_analysis_preview()
        self.table.setRowCount(0)
        self.review_detail.hide()
        self.coverage_rail.set_result(None)
        self._review_decisions.clear()
        self._run_started = time.perf_counter()
        self.runtime_seconds = None
        worker = PlayDetectWorker(
            self.ffmpeg_path, self.source, self.duration_ms,
            self.separator_spin.value(), self.min_spin.value(),
            self.max_spin.value(), self)
        worker.finished_ok.connect(self._done)
        worker.failed.connect(self._failed)
        self._worker = worker
        worker.start()

    def _done(self, result: DetectionResult) -> None:
        if self._run_started is not None:
            self.runtime_seconds = time.perf_counter() - self._run_started
        self.result = result
        self.run_btn.setEnabled(True)
        self._worker = None
        self._stop_analysis_preview_worker()
        signal = SIGNAL_LABELS.get(result.signal, result.signal)
        # Film the detector could not classify is reported, never dropped -
        # an empty result with 30 minutes of unexplained footage is how plays
        # used to disappear without a trace.
        review_count = sum(
            1 for play in result.plays if play.needs_review
        ) + len(result.unclassified)
        if not result.plays:
            if review_count:
                summary = (
                    "No plays recognized · "
                    f"{review_count} section"
                    f"{'s' if review_count != 1 else ''} need review")
            else:
                summary = (
                    "No plays recognized · adjust Detection settings "
                    "and run again")
        else:
            summary = (
                f"{len(result.plays)} plays found across "
                f"{result.coverage_pct:.0f}% of the film")
        self.status_label.setText(summary)
        self.status_label.setToolTip(
            f"Boundary signal: {signal}")
        self._set_stage("review")
        self.coverage_rail.set_result(result)
        self._fill_table(result)
        self._update_create_button()

    def _fill_table(self, result: DetectionResult) -> None:
        all_candidates = self.accepted_candidates()
        review_candidates = [
            candidate for candidate in all_candidates
            if candidate["needs_review"]
            or candidate["candidate_kind"] == "unclassified"
        ]
        confident_candidates = [
            candidate for candidate in all_candidates
            if candidate not in review_candidates
        ]
        if self._review_focus:
            primary_candidates = review_candidates
            secondary_candidates = confident_candidates
            self.table.setMinimumHeight(108)
            self.table.setMaximumHeight(148)
        else:
            primary_candidates = all_candidates
            secondary_candidates = []
            self.table.setMinimumHeight(220)
            self.table.setMaximumHeight(16_777_215)

        self.all_filter.setText(f"All {len(all_candidates)}")
        self.review_filter.setText(
            f"Needs review {len(review_candidates)}")
        self.all_filter.setChecked(not self._review_focus)
        self.review_filter.setChecked(self._review_focus)
        _refresh_style(self.all_filter)
        _refresh_style(self.review_filter)

        self._populate_result_table(
            self.table, primary_candidates)
        self._populate_result_table(
            self.confident_table, secondary_candidates)
        self.confident_table.setVisible(bool(secondary_candidates))

        if primary_candidates:
            if self._review_focus:
                target = 0
            else:
                target = next((
                    row for row, candidate
                    in enumerate(primary_candidates)
                    if candidate in review_candidates
                ), 0)
            self.table.setCurrentCell(target, 0)
            self.table.selectRow(target)
            self._selected_result_changed(target, 0, -1, -1)
        else:
            self.review_detail.hide()
            self.coverage_rail.set_selected(None)

    def _populate_result_table(
            self, table: QTableWidget,
            candidates: list[dict]) -> None:
        table.blockSignals(True)
        table.setRowCount(len(candidates))
        for row, candidate in enumerate(candidates):
            start = candidate["detector_start_ms"]
            end = candidate["detector_end_ms"]
            angles = candidate["angle_count"]
            needs_review = (
                candidate["needs_review"]
                or candidate["candidate_kind"] == "unclassified"
            )
            decision = self._decision_for(candidate)
            reason = candidate["review_reason"] if needs_review \
                else "High confidence"
            if decision == "dismiss":
                reason = f"Dismissed · {reason}"
            type_text = (
                "Review section"
                if candidate["candidate_kind"] == "unclassified"
                else "Play")
            texts = (
                type_text,
                format_ms(start),
                format_ms(end),
                f"{(end - start) / 1000:.1f}s",
                str(angles),
                reason,
            )
            for col, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, candidate)
                if needs_review:
                    item.setForeground(QColor("#e0b341"))
                    item.setToolTip(
                        "TapeSift is preserving this range for review.")
                elif col in {0, 5}:
                    item.setForeground(QColor("#52d67f"))
                if decision == "dismiss":
                    item.setForeground(QColor("#77786f"))
                table.setItem(row, col, item)
        table.blockSignals(False)

    @staticmethod
    def _candidate_key(candidate: dict) -> tuple[str, int]:
        return (
            candidate["candidate_kind"],
            candidate["candidate_index"],
        )

    def _decision_for(self, candidate: dict) -> str:
        key = self._candidate_key(candidate)
        if key in self._review_decisions:
            return self._review_decisions[key]
        return (
            "keep"
            if candidate["candidate_kind"] == "play"
            else "dismiss"
        )

    def _set_review_focus(self, focused: bool) -> None:
        self._review_focus = focused
        if self.result is not None:
            self._fill_table(self.result)

    def _selected_candidate(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        candidate = item.data(Qt.ItemDataRole.UserRole)
        return candidate if isinstance(candidate, dict) else None

    def _selected_result_changed(
            self, current_row: int, _current_column: int,
            _previous_row: int, _previous_column: int) -> None:
        if current_row < 0:
            self.review_detail.hide()
            self.coverage_rail.set_selected(None)
            return
        self._sync_review_detail()

    def _sync_review_detail(self) -> None:
        candidate = self._selected_candidate()
        if candidate is None:
            self.review_detail.hide()
            self.coverage_rail.set_selected(None)
            return
        key = self._candidate_key(candidate)
        self.coverage_rail.set_selected(key)
        needs_review = (
            candidate["needs_review"]
            or candidate["candidate_kind"] == "unclassified"
        )
        self.review_detail.setVisible(needs_review)
        if not needs_review:
            return

        start = candidate["detector_start_ms"]
        end = candidate["detector_end_ms"]
        reason = candidate["review_reason"] or "Check this boundary"
        self.review_range_label.setText(
            f"Source range:  {format_ms(start)}  -  {format_ms(end)}  "
            f"({(end - start) / 1000:.1f}s)  ·  "
            f"{candidate['angle_count']} angle"
            f"{'s' if candidate['angle_count'] != 1 else ''}  ·  {reason}")
        decision = self._decision_for(candidate)
        self.keep_button.setProperty(
            "selectedDecision", "true" if decision == "keep" else "false")
        self.dismiss_button.setProperty(
            "selectedDecision",
            "true" if decision == "dismiss" else "false")
        _refresh_style(self.keep_button)
        _refresh_style(self.dismiss_button)
        if self.isVisible():
            self._request_preview(candidate)

    def _decide_selected(self, decision: str) -> None:
        candidate = self._selected_candidate()
        if candidate is None:
            return
        self._review_decisions[
            self._candidate_key(candidate)] = decision
        if self.result is not None:
            selected_key = self._candidate_key(candidate)
            self._fill_table(self.result)
            for row in range(self.table.rowCount()):
                row_candidate = self.table.item(
                    row, 0).data(Qt.ItemDataRole.UserRole)
                if (
                    isinstance(row_candidate, dict)
                    and self._candidate_key(row_candidate) == selected_key
                ):
                    self.table.setCurrentCell(row, 0)
                    self.table.selectRow(row)
                    break
        self._update_create_button()

    def _update_create_button(self) -> None:
        count = len(self.play_candidates())
        self.create_button.setText(
            f"Create {count} Clip{'s' if count != 1 else ''}")
        self._set_ok_enabled(count > 0)

    def _request_preview(self, candidate: dict) -> None:
        self._preview_generation += 1
        generation = self._preview_generation
        for frame in self.preview_frames:
            frame.clear()
            frame.setText("Loading frame…")
        if (
            not self.ffmpeg_path
            or not self.source.is_file()
            or not self._preview_temp.isValid()
        ):
            for frame in self.preview_frames:
                frame.setText("Source preview unavailable")
            return
        payload = {
            "generation": generation,
            "start": candidate["detector_start_ms"],
            "end": candidate["detector_end_ms"],
        }
        if self._preview_worker and self._preview_worker.isRunning():
            self._preview_worker.stop()
            self._pending_preview = payload
            return
        self._start_preview(payload)

    def _start_preview(self, payload: dict) -> None:
        generation = payload["generation"]
        start = payload["start"]
        end = max(start + 1, payload["end"])
        span = max(1, end - start)
        clips: list[Clip] = []
        self._preview_ids = {}
        for index in range(5):
            timestamp = start + round(span * (index + 0.5) / 5)
            clip_start = max(start, timestamp - 350)
            clip_end = min(end, max(clip_start + 1, timestamp + 350))
            clip = Clip(start_ms=clip_start, end_ms=clip_end)
            clips.append(clip)
            self._preview_ids[clip.id] = index
        worker = ThumbnailWorker(
            self.ffmpeg_path,
            self.source,
            clips,
            Path(self._preview_temp.path()),
            self,
        )
        worker.thumbnail_ready.connect(
            lambda clip_id, path, token=generation:
            self._preview_ready(token, clip_id, path))
        worker.finished.connect(
            lambda active=worker: self._preview_finished(active))
        self._preview_worker = worker
        worker.start()

    def _preview_ready(
            self, generation: int, clip_id: str, path: str) -> None:
        if generation != self._preview_generation:
            return
        index = self._preview_ids.get(clip_id)
        if index is None:
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            return
        label = self.preview_frames[index]
        label.setText("")
        label.setPixmap(pixmap.scaled(
            max(120, label.width()),
            label.minimumHeight(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def _preview_finished(self, worker: ThumbnailWorker) -> None:
        if worker is self._preview_worker:
            self._preview_worker = None
        worker.deleteLater()
        pending = self._pending_preview
        self._pending_preview = None
        if pending is not None and self._stage == "review":
            self._start_preview(pending)

    def _failed(self, message: str) -> None:
        self.run_btn.setEnabled(True)
        self._worker = None
        self._stop_analysis_preview_worker()
        self.setup_error.setText(f"Detection failed: {message}")
        self.setup_error.show()
        self._set_stage("setup")
        self._set_ok_enabled(False)

    # ---------- Analyze-stage source preview ----------

    def _start_analysis_preview(self) -> None:
        self._stop_analysis_preview_worker()
        if (
            not self.ffmpeg_path
            or not self.source.is_file()
            or self.duration_ms <= 0
            or not self._analysis_preview_temp.isValid()
        ):
            return
        self._analysis_preview_generation += 1
        generation = self._analysis_preview_generation
        worker = AnalysisPreviewWorker(
            self.ffmpeg_path,
            self.source,
            self.duration_ms,
            Path(self._analysis_preview_temp.path()),
            self,
        )
        worker.frame_ready.connect(
            lambda index, timestamp_ms, path,
            active_generation=generation, active_worker=worker:
            self._analysis_frame_ready(
                active_generation,
                active_worker,
                index,
                timestamp_ms,
                path,
            )
        )
        worker.preview_failed.connect(
            lambda _message, active_generation=generation:
            self._analysis_preview_unavailable(active_generation)
        )
        worker.finished.connect(
            lambda active_worker=worker:
            self._analysis_preview_finished(active_worker)
        )
        self._analysis_preview_worker = worker
        worker.start()

    def _analysis_frame_ready(
        self,
        generation: int,
        worker: AnalysisPreviewWorker,
        sample_index: int,
        timestamp_ms: int,
        path: str,
    ) -> None:
        if (
            generation != self._analysis_preview_generation
            or worker is not self._analysis_preview_worker
            or self._stage != "analyze"
        ):
            return
        pixmap = QPixmap(path)
        if not pixmap.isNull():
            self.live_frame_scan.add_frame(
                sample_index,
                timestamp_ms,
                pixmap,
            )

    def _analysis_preview_unavailable(self, generation: int) -> None:
        if (
            generation == self._analysis_preview_generation
            and self._stage == "analyze"
            and self.live_frame_scan.current_frame() is None
        ):
            self.live_frame_scan.time_label.setText(
                f"Analyzing source  |  "
                f"{format_ms(self.duration_ms)} total")

    def _analysis_preview_finished(
        self,
        worker: AnalysisPreviewWorker,
    ) -> None:
        if worker is self._analysis_preview_worker:
            self._analysis_preview_worker = None
        worker.deleteLater()

    def _stop_analysis_preview_worker(self) -> None:
        self._analysis_preview_generation += 1
        worker = self._analysis_preview_worker
        if worker and worker.isRunning():
            worker.stop()
            if not worker.wait(1500):
                worker.terminate()
                worker.wait(500)
        self._analysis_preview_worker = None

    # ---------- results ----------

    def accepted_candidates(self) -> list[dict]:
        """Return the complete structured detector output for provenance.

        Original and applied bounds are both retained. They differ when the
        user asks for the first camera angle only; that UI choice is not a
        later human correction and must not be scored as one. Unclassified
        footage remains here for research and capture, but is no longer
        materialized as an ordinary play clip.
        """
        if not self.result:
            return []
        candidates: list[dict] = []
        for index, play in enumerate(self.result.plays):
            end_ms = play.end_ms
            if self.wide_only_check.isChecked() and play.angle_count > 1:
                end_ms = play.angle_starts[1] \
                    if len(play.angle_starts) > 1 else play.end_ms
            candidates.append({
                "candidate_kind": "play",
                "candidate_index": index,
                "detector_start_ms": play.start_ms,
                "detector_end_ms": play.end_ms,
                "created_start_ms": play.start_ms,
                "created_end_ms": end_ms,
                "angle_starts_ms": list(play.angle_starts),
                "angle_count": play.angle_count,
                "needs_review": play.needs_review,
                "review_reason": play.review_reason,
            })
        for index, segment in enumerate(self.result.unclassified):
            candidates.append({
                "candidate_kind": "unclassified",
                "candidate_index": index,
                "detector_start_ms": segment.start_ms,
                "detector_end_ms": segment.end_ms,
                "created_start_ms": segment.start_ms,
                "created_end_ms": segment.end_ms,
                "angle_starts_ms": [
                    segment.start_ms, *segment.split_points_ms],
                "angle_count": segment.angle_count,
                "needs_review": True,
                "review_reason": segment.reason,
            })
        return sorted(
            candidates,
            key=lambda item: (
                item["created_start_ms"],
                item["created_end_ms"],
                item["candidate_kind"],
                item["candidate_index"],
            ),
        )

    def play_candidates(self) -> list[dict]:
        """Ranges the user chose to materialize as ordinary play clips.

        Detected plays keep the historical default of being included.
        Unclassified footage keeps the historical default of being excluded,
        but the Review stage now lets the user deliberately keep a range
        instead of having to reconstruct it later.
        """
        return [
            candidate for candidate in self.accepted_candidates()
            if self._decision_for(candidate) == "keep"
        ]

    def capture_parameters(self) -> dict:
        return {
            "separator_max_s": self.separator_spin.value(),
            "min_play_s": self.min_spin.value(),
            "max_play_s": self.max_spin.value(),
            "scene_threshold": DEFAULT_SCENE_THRESHOLD,
        }

    def accepted_ranges(self) -> list[tuple[int, int, bool]]:
        """(start_ms, end_ms, needs_review) for each materialized play.

        The review flag travels with the range so the created clip can be
        marked. Without it, film we were unsure about becomes indistinguishable
        from film we were confident about the moment the clips exist.
        """
        return [
            (
                candidate["created_start_ms"],
                candidate["created_end_ms"],
                candidate["needs_review"],
            )
            for candidate in self.play_candidates()
        ]

    def _stop_preview_worker(self) -> None:
        self._pending_preview = None
        self._preview_generation += 1
        worker = self._preview_worker
        if worker and worker.isRunning():
            worker.stop()
            if not worker.wait(2500):
                worker.terminate()
                worker.wait(1000)
        self._preview_worker = None

    def accept(self) -> None:
        self._stop_analysis_preview_worker()
        self._stop_preview_worker()
        super().accept()

    def reject(self) -> None:
        self._stop_analysis_preview_worker()
        self.live_frame_scan.stop()
        self.progress.stop()
        self._stop_preview_worker()
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
        super().reject()
