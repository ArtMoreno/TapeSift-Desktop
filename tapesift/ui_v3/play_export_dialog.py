"""Per-play export preview; rendering uses the saved snapshot passed by Review."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QCloseEvent, QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar,
    QPushButton, QVBoxLayout, QWidget,
)

from tapesift.core.exceptions import ExportCancelledError
from tapesift.models.clip import Clip
from tapesift.models.project import Project
from tapesift.services import ffmpeg_service, filename_service
from tapesift.services.export_service import FFmpegRunner
from tapesift.services.play_summary_export import (
    export_summary_video, render_summary, save_summary_image,
)


class _SummaryExportWorker(QThread):
    progress = Signal(int)
    failed = Signal(str)
    exported = Signal(str)
    cancelled = Signal()

    def __init__(self, project, clip, image, path, ffmpeg_path, parent):
        super().__init__(parent)
        self.project, self.clip, self.image = project, clip, image
        self.path, self.ffmpeg_path = path, ffmpeg_path
        self.runner = FFmpegRunner()

    def run(self):
        try:
            export_summary_video(self.ffmpeg_path, self.project, self.clip,
                                 self.image, self.path, self.runner,
                                 lambda pct: self.progress.emit(round(pct)))
        except ExportCancelledError:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.exported.emit(str(self.path))


class PlayExportDialog(QDialog):
    video_only_requested = Signal()

    def __init__(self, clip: Clip, project: Project, field_image: QImage,
                 ffmpeg_path: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Export this play · TapeSift")
        self.setObjectName("PlayExportDialog")
        self.setMinimumWidth(600)
        self.resize(780, 570)
        self.clip, self.project = deepcopy(clip), deepcopy(project)
        self.summary_image = render_summary(self.clip, self.project, field_image.copy())
        self.ffmpeg_path = ffmpeg_path or ffmpeg_service.find_executable("ffmpeg")
        self.worker: _SummaryExportWorker | None = None
        self.setStyleSheet("""
            QDialog#PlayExportDialog { background: #080c0a; color: #efeee5; }
            QDialog#PlayExportDialog QLabel { color: #d5dcd6; }
            QDialog#PlayExportDialog QPushButton { min-height: 32px; padding: 0 12px;
                background: #101914; color: #efeee5; border: 1px solid #3c5142; border-radius: 3px; }
            QDialog#PlayExportDialog QPushButton:hover { border-color: #73b98a; }
            QDialog#PlayExportDialog QPushButton:disabled { color: #728077; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("Export this play")
        title.setStyleSheet("font-size: 19px; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(QLabel("Saved details · the preview and export use the same play snapshot"))
        self.preview = QLabel()
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(0, 0)
        layout.addWidget(self.preview, 1)
        self.status = QLabel("Video + summary adds this card for two seconds before the film.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)
        actions = QHBoxLayout()
        self.video_button = QPushButton("Video only")
        self.card_button = QPushButton("Video + summary")
        self.image_button = QPushButton("Field summary PNG")
        self.close_button = QPushButton("Close")
        for button in (self.video_button, self.card_button, self.image_button):
            actions.addWidget(button)
        actions.addStretch()
        actions.addWidget(self.close_button)
        layout.addLayout(actions)
        self.video_button.clicked.connect(self._video_only)
        self.card_button.clicked.connect(self._export_video)
        self.image_button.clicked.connect(self._export_image)
        self.close_button.clicked.connect(self.reject)
        self.card_button.setEnabled(bool(self.ffmpeg_path))
        if not self.ffmpeg_path:
            self.card_button.setToolTip("Configure FFmpeg in Settings to export video with a summary.")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "preview"):
            self.preview.setPixmap(QPixmap.fromImage(self.summary_image).scaled(
                max(1, self.width() - 36), max(1, self.height() - 175),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def _destination(self, suffix: str) -> Path | None:
        stem = filename_service.effective_base(self.clip) + "-summary"
        initial = Path(self.project.output_folder or str(Path.home())) / (stem + suffix)
        picker = QFileDialog(self, "Export this play", str(initial))
        picker.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        picker.setNameFilter("PNG image (*.png)" if suffix == ".png" else "MP4 video (*.mp4)")
        picker.setDefaultSuffix(suffix.lstrip("."))
        if picker.exec() != QDialog.DialogCode.Accepted:
            return None
        path = Path(picker.selectedFiles()[0])
        if path.suffix.lower() != suffix:
            QMessageBox.warning(self, "Choose the matching format", f"Use a {suffix} filename for this export.")
            return None
        return path

    def _video_only(self):
        self.accept()
        self.video_only_requested.emit()

    def _export_image(self):
        path = self._destination(".png")
        if path is None:
            return
        try:
            save_summary_image(self.summary_image, path)
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
        else:
            self.status.setText(f"Saved: {path}")

    def _export_video(self):
        if self.worker is not None:
            return
        path = self._destination(".mp4")
        if path is None:
            return
        self.worker = _SummaryExportWorker(self.project, self.clip, self.summary_image.copy(),
                                           path, self.ffmpeg_path, self)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.failed.connect(lambda error: self.status.setText(f"Export failed: {error}"))
        self.worker.cancelled.connect(lambda: self.status.setText("Export cancelled. No output was replaced."))
        self.worker.exported.connect(lambda value: self.status.setText(f"Saved: {value}"))
        self.worker.finished.connect(self._finished)
        for button in (self.video_button, self.card_button, self.image_button):
            button.setEnabled(False)
        self.close_button.setText("Cancel export")
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText("Exporting the saved play and summary…")
        self.worker.start()

    def _finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        for button in (self.video_button, self.card_button, self.image_button):
            button.setEnabled(True)
        self.card_button.setEnabled(bool(self.ffmpeg_path))
        self.close_button.setText("Close")
        self.progress.hide()

    def reject(self):
        if self.worker is not None:
            try:
                self.worker.runner.cancel()
            except OSError:
                self.worker.runner.cancel_event.set()
            self.status.setText("Cancelling export…")
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent):
        if self.worker is not None:
            self.reject()
            event.ignore()
        else:
            super().closeEvent(event)
