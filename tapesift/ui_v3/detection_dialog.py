"""V3 presentation of the existing detector and its review decisions."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from tapesift.models.clip import Clip
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui.play_detect_dialog import PlayDetectDialog, _refresh_style
from tapesift.ui_v3.icons import tinted_icon
from tapesift.workers.thumbnail_worker import ThumbnailWorker


def boundary_sample_times(start: int, end: int, duration: int) -> list[int]:
    # Requests show context on both sides; FFmpeg does not return painted PTS.
    return [max(0, min(max(0, duration-1), value))
            for value in (start-1000, start-250, start, start+250, end-250, end)]


class SourceFramePreview(QWidget):
    """Paint existing sampled frames without implying detector progress."""
    def __init__(self, panel, *, strip=False):
        super().__init__(panel)
        self.panel = panel
        self.strip = strip
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(125 if strip else 350)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        frames = self.panel.neighbor_frames() if self.strip else [self.panel.current_frame()]
        width = (self.width() - 6 * (len(frames)-1)) / len(frames)
        for index, frame in enumerate(frames):
            rect = QRectF(index*(width+6), 0, width, self.height()).adjusted(1, 1, -1, -1)
            painter.fillRect(rect, QColor('#080c0d'))
            if frame:
                pixmap = frame[1]
                size = pixmap.size().scaled(rect.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
                target = QRectF(0, 0, size.width(), size.height())
                target.moveCenter(rect.center())
                painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))
            else:
                painter.setPen(QColor('#a5b0a8'))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, 'Preparing source frames…')
            painter.setPen(QPen(QColor('#59bd83' if self.strip and index == 1 else '#2c3730'), 1))
            painter.drawRect(rect)


class PlayDetectDialogV3(PlayDetectDialog):
    def __init__(self, ffmpeg_path: str, source: Path, duration_ms: int, parent=None):
        self._v3_ready = False
        self._v3_preview_pixmaps = {}
        super().__init__(ffmpeg_path, source, duration_ms, parent)
        self.setProperty("shellV3Dialog", True)
        self.setWindowTitle(f"Detect Plays — {source.stem}")
        self.layout().setContentsMargins(26, 18, 26, 18)
        self.layout().setSpacing(12)
        title = self.findChild(QLabel, "DetectDialogTitle")
        title.setText("Detect Plays")
        title.show()
        beta = self.findChild(QLabel, "DetectBetaBadge")
        beta.setText("Beta")
        beta.show()
        header = self.layout().itemAt(0).layout()
        header.removeWidget(beta)
        header.itemAt(0).layout().itemAt(0).layout().insertWidget(1, beta, 0, Qt.AlignmentFlag.AlignVCenter)
        header.itemAt(0).layout().setSpacing(10)
        self._v3_heading = (title, beta)
        self.source_label.setText("Find clip boundaries in cut-up All-22 film.")
        self.source_label.show()
        self.stage_description.hide()
        self.findChild(QFrame, "DetectHeaderRule").hide()
        steps = self.layout().itemAt(2).layout()
        dividers = [steps.itemAt(i).widget() for i in (1, 3)]
        while steps.count():
            steps.takeAt(0)
        self.step_numbers = {}
        self.step_dividers = dividers
        for index, (name, label) in enumerate(self.step_labels.items()):
            group = QHBoxLayout()
            group.setSpacing(10)
            number = QLabel(str(index+1))
            number.setObjectName('V3DetectStepNumber')
            number.setFixedSize(36, 36)
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.step_numbers[name] = number
            group.addWidget(number)
            label.setMinimumHeight(42)
            group.addWidget(label)
            steps.addLayout(group, 1)
            if index < 2:
                divider = dividers[index]
                divider.setObjectName('V3DetectStepConnector')
                divider.setProperty('detectStepDivider', None)
                divider.setMinimumWidth(0)
                divider.setMaximumWidth(16777215)
                divider.setFixedHeight(1)
                steps.addWidget(divider, 1)

        setup = self.pages.widget(0)
        setup.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        source_bar = QFrame()
        source_bar.setObjectName("V3DetectSource")
        source_bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        source_bar.setMinimumHeight(80)
        source_row = QHBoxLayout(source_bar)
        source_row.setContentsMargins(16, 12, 16, 12)
        source_icon = QLabel()
        source_icon.setPixmap(tinted_icon("filmstrip-play-20.svg").pixmap(QSize(24, 24)))
        source_row.addWidget(source_icon)
        self.source_identity_label = QLabel(f"Source\n{source.name} · {format_ms(duration_ms)}")
        self.source_identity_label.setTextFormat(Qt.TextFormat.PlainText)
        self.source_identity_label.setWordWrap(True)
        self.source_identity_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.source_identity_label.setToolTip(str(source))
        source_row.addWidget(self.source_identity_label, 1)
        wrapper = QWidget()
        setup_layout = QVBoxLayout(wrapper)
        setup_layout.setContentsMargins(0, 0, 0, 0)
        setup_layout.setSpacing(30)
        self.pages.removeWidget(setup)
        setup_layout.addWidget(source_bar)
        setup_layout.addWidget(setup, 1)
        setup.show()  # QStackedWidget.removeWidget explicitly hides the page.
        self.pages.insertWidget(0, wrapper)
        self.scope_notice.setText("TapeSift works with cut-up All-22 film.\n\n"
            "A separator is identified by a black gap, an information card, or a hard cut.\n\n"
            "Generated clip boundaries will be reviewed on the next step before clips are added.")
        guidance = self.findChild(QFrame, "DetectSetupGuidance")
        for label in guidance.findChildren(QLabel):
            if label is not self.scope_notice:
                label.hide()
        self.run_btn.setText("Run detection")
        self.run_btn.parentWidget().layout().removeWidget(self.run_btn)
        self.layout().itemAt(self.layout().count()-1).layout().addWidget(self.run_btn)
        for spin in (self.separator_spin, self.min_spin, self.max_spin):
            spin.setMinimumHeight(54)
            spin.setMinimumWidth(145)
        settings = self.findChild(QFrame, "DetectSetupSettings")
        form = settings.layout().itemAt(1).layout()
        form.labelForField(self.max_spin).setText("Maximum play:")
        form.setHorizontalSpacing(36)
        form.setVerticalSpacing(22)
        for spin in (self.separator_spin, self.min_spin, self.max_spin):
            spin.setMaximumWidth(300)
            form.labelForField(spin).setMinimumWidth(210)
        settings.layout().setSpacing(28)
        hint = self.findChild(QLabel, 'DetectRunHint')
        hint.setText('Detection reads the source film without changing it.')
        hint.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        settings.layout().itemAt(3).layout().setStretch(0, 1)
        settings.layout().itemAt(3).layout().setStretch(1, 0)
        settings.layout().addStretch(1)

        self.analysis_card.setMinimumWidth(0)
        self.analysis_card.setMaximumWidth(820)
        self.analysis_card.layout().setContentsMargins(0, 0, 0, 0)
        self.analysis_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        analysis_row = self.pages.widget(1).layout().itemAt(1).layout()
        analysis_row.setStretch(0, 0)
        analysis_row.setStretch(1, 1)
        analysis_row.setStretch(2, 0)
        for name in ('viewport', 'filmstrip'):
            previous = getattr(self.live_frame_scan, name)
            preview = SourceFramePreview(self.live_frame_scan, strip=name == 'filmstrip')
            self.live_frame_scan.layout().replaceWidget(previous, preview)
            setattr(self.live_frame_scan, name, preview)
            previous.hide()
            previous.deleteLater()
        self.findChild(QLabel, "DetectAnalysisKicker").setText("SOURCE FRAME PREVIEW")
        self.live_frame_scan.setAccessibleName("Sampled source frames; independent of detector progress")
        self.live_frame_scan.time_label.hide()
        self.findChild(QLabel, "DetectAnalysisNote").setText(
            "Results are ready for review before clips are added.")
        self._v3_activity_timer = QTimer(self)
        self._v3_activity_timer.setInterval(250)
        self._v3_activity_timer.timeout.connect(self._update_activity)
        self.progress.setToolTip("Activity indicator. The detector does not report a completion percentage.")

        detail = self.review_detail.layout()
        actions = QHBoxLayout()
        for button in (self.keep_button, self.dismiss_button):
            detail.itemAt(0).layout().removeWidget(button)
            actions.addWidget(button)
        actions.addStretch()
        detail.addLayout(actions)
        self.preview_strip.layout().setSpacing(6)
        for frame in self.preview_frames:
            self.preview_strip.layout().removeWidget(frame)
        extra = QLabel("Loading frame…")
        extra.setObjectName("DetectPreviewFrame6")
        extra.setProperty("previewFrame", "true")
        self.preview_frames.append(extra)
        self.sample_times = []
        for index, frame in enumerate(self.preview_frames):
            tile = QWidget()
            tile.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            column = QVBoxLayout(tile)
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(4)
            frame.setAlignment(Qt.AlignmentFlag.AlignCenter)
            frame.setMinimumWidth(0)
            frame.setFixedHeight(88)
            frame.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            frame.setProperty("boundarySample", index == 2)
            column.addWidget(frame)
            caption = QLabel("Requested sample")
            caption.setObjectName("V3DetectSampleTime")
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setWordWrap(True)
            column.addWidget(caption)
            self.sample_times.append(caption)
            self.preview_strip.layout().addWidget(tile, 1)
        self.review_settings_bar.layout().removeWidget(self.review_run_btn)
        footer = self.layout().itemAt(self.layout().count()-1).layout()
        footer.insertWidget(2, self.review_run_btn)
        self.confident_table.hide()
        self.status_label.setWordWrap(True)
        self.coverage_caption = self.findChild(QLabel, "DetectCoverageCaption")
        self.coverage_caption.setWordWrap(True)
        self.coverage_caption.setTextFormat(Qt.TextFormat.PlainText)
        self.review_note = QLabel("Review candidate boundaries before creating clips. "
            "Unassigned footage remains in the coverage timeline; no play has been human-reviewed here.")
        self.review_note.setObjectName("V3DetectReviewNote")
        self.review_note.setWordWrap(True)
        self.pages.widget(2).layout().addWidget(self.review_note)

        # Only the body scrolls; steps and footer retain usable hit targets.
        self.stage_scrolls = []
        for index in range(3):
            page = self.pages.widget(index)
            self.pages.removeWidget(page)
            page.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            scroll = QScrollArea()
            scroll.setObjectName("V3DetectStageScroll")
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidgetResizable(True)
            scroll.setWidget(page)
            self.pages.insertWidget(index, scroll)
            self.stage_scrolls.append(scroll)
        self._v3_ready = True
        self._set_stage("setup")

    def _set_stage(self, stage):
        super()._set_stage(stage)
        if not self._v3_ready:
            return
        available = self.screen().availableGeometry()
        self.setMinimumSize(min(820, available.width()-40), min(460, available.height()-60))
        self.resize(min(1160, available.width()-40), min(860, available.height()-60))
        self.layout().setContentsMargins(26, 16 if stage == 'setup' else 18, 26, 18)
        self.layout().setSpacing(20 if stage == 'setup' else 12)
        self.findChild(QFrame, 'DetectStepRule').setVisible(stage != 'setup')
        steps = self.layout().itemAt(2).layout()
        steps.setContentsMargins(130 if stage == 'setup' else 40, 0, 70 if stage == 'setup' else 40, 0)
        self.run_btn.setVisible(stage == "setup")
        self.review_run_btn.setVisible(stage == "review")
        self.source_label.setVisible(stage == "setup")
        for widget in self._v3_heading:
            widget.setVisible(stage == 'setup')
        for index, (name, label) in enumerate(self.step_labels.items()):
            number = self.step_numbers[name]
            number.setVisible(stage == 'setup')
            number.setProperty('active', name == stage)
            _refresh_style(number)
            label.setText(name.title() if stage == 'setup' else f'{index+1}  {name.title()}')
            label.setAlignment(Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignLeft if stage == 'setup' else Qt.AlignmentFlag.AlignHCenter))
            label.setProperty('setupStep', stage == 'setup')
            _refresh_style(label)
        for divider in self.step_dividers:
            divider.setVisible(stage == 'setup')
        self.stage_scrolls[self.pages.currentIndex()].verticalScrollBar().setValue(0)
        if stage == "analyze":
            self._v3_activity_timer.start()
            self._update_activity()
        else:
            self._v3_activity_timer.stop()
        self._resize_previews()

    def _update_activity(self):
        elapsed = max(0, int((time.perf_counter() - self._run_started)*1000)) if self._run_started else 0
        frame = self.live_frame_scan.current_frame()
        preview = f"Preview requested near {format_ms(frame[0], show_millis=True)}" if frame else "Preparing source previews"
        self.analysis_source.setText(f"Elapsed {format_ms(elapsed)} · Source {format_ms(self.duration_ms)}\n{preview}")

    def _fill_table(self, result):
        if self._v3_ready and not (result.unclassified or any(p.needs_review for p in result.plays)):
            self._review_focus = False
        super()._fill_table(result)
        if not self._v3_ready:
            return
        self.confident_table.hide()
        self.table.setMinimumHeight(145)
        self.table.setMaximumHeight(260)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.review_filter.setEnabled(bool(result.unclassified or any(p.needs_review for p in result.plays)))
        flagged = sum(p.needs_review for p in result.plays) + len(result.unclassified)
        gaps = sum(segment.kind == "possible_missed" for segment in result.coverage_segments)
        self.status_label.setText(f"{len(result.plays)} candidate plays · {flagged} flagged ranges · {gaps} coverage gaps")
        self.coverage_caption.setText(f"Film coverage · Candidate ranges {result.coverage_pct:.1f}% · "
            "Green: candidate · Amber: flagged or unassigned · Gray: separator")
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 5)
            if item.text() == "High confidence":
                item.setText("No detector warning")

    def _sync_review_detail(self):
        super()._sync_review_detail()
        if not self._v3_ready:
            return
        candidate = self._selected_candidate()
        if candidate is None:
            return
        self.review_detail.show()
        if not candidate["needs_review"] and candidate["candidate_kind"] != "unclassified":
            self.review_range_label.setText(
                f"Selected range: {format_ms(candidate['detector_start_ms'], show_millis=True)} – "
                f"{format_ms(candidate['detector_end_ms'], show_millis=True)} · "
                f"{candidate['angle_count']} angles · Check both boundaries")
            decision = self._decision_for(candidate)
            for button, value in ((self.keep_button, "keep"), (self.dismiss_button, "dismiss")):
                button.setProperty("selectedDecision", "true" if decision == value else "false")
                _refresh_style(button)
            if self.isVisible():
                self._request_preview(candidate)

    def _request_preview(self, candidate):
        self._v3_preview_pixmaps.clear()
        for caption in getattr(self, "sample_times", []):
            caption.setText("Pending sample")
        super()._request_preview(candidate)

    def _start_preview(self, payload):
        generation = payload["generation"]
        times = boundary_sample_times(payload["start"], payload["end"], self.duration_ms)
        clips = [Clip(start_ms=t, end_ms=t+1) for t in times]
        self._preview_ids = {clip.id:index for index, clip in enumerate(clips)}
        for caption, timestamp in zip(self.sample_times, times):
            caption.setText(f"Requested\n{format_ms(timestamp, show_millis=True)}")
        worker = ThumbnailWorker(self.ffmpeg_path, self.source, clips, Path(self._preview_temp.path()), self)
        worker.thumbnail_ready.connect(lambda clip_id, path, token=generation: self._preview_ready(token, clip_id, path))
        worker.finished.connect(lambda active=worker: self._preview_finished(active))
        self._preview_worker = worker
        worker.start()

    def _preview_ready(self, generation, clip_id, path):
        if generation != self._preview_generation:
            return
        index = self._preview_ids.get(clip_id)
        pixmap = QPixmap(path)
        if index is None or pixmap.isNull():
            return
        self._v3_preview_pixmaps[index] = pixmap
        self._resize_previews()

    def _resize_previews(self):
        self.live_frame_scan.viewport.setFixedHeight(max(180, min(350, self.height()-410)))
        for index, pixmap in self._v3_preview_pixmaps.items():
            frame = self.preview_frames[index]
            frame.setText("")
            frame.setPixmap(pixmap.scaled(frame.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._v3_ready:
            self._resize_previews()

    def reject(self):
        if self._v3_ready:
            self._v3_activity_timer.stop()
        super().reject()
