"""Selected-clip source photos using the existing player's painted frame."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QMenu,
    QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout,
)

from tapesift.services.source_photo import photo_warnings, time_relationship, validate_photo
from tapesift.services.timestamp_parser import format_ms
from tapesift.ui_v3.icons import tinted_icon


def painted_snapshot(player) -> tuple[QImage, int] | None:
    """One synchronous read: a decoded frame is not necessarily painted yet."""
    surface = player.video_widget
    pts = player.displayed_position_ms()
    if (pts is None or pts < 0 or player._next_presented_frame_is_hard_seek
            or player._last_presented_selection_epoch != player._selection_epoch
            or surface._image_serial != surface._presented_serial
            or surface._image_position_ms != pts):
        return None
    image = surface.current_frame_image()
    return None if image.isNull() else (image, pts)


def text_label(text="") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    return label


class SourcePhotoViewer(QDialog):
    def __init__(self, image: QImage, caption: str, parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Tool)
        self.setObjectName("V3SourcePhotoViewer")
        self.setWindowTitle("Source frame")
        self.image = image
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        self.caption = text_label(caption)
        self.caption.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.caption)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.preview = QLabel()
        self.preview.setAccessibleName("Saved source frame")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setWidget(self.preview)
        self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        self.actual_size = QPushButton("100% pixels")
        self.actual_size.setCheckable(True)
        self.actual_size.toggled.connect(self._scale_image)
        row.addWidget(self.actual_size)
        row.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        row.addWidget(buttons)
        layout.addLayout(row)
        available = self.screen().availableGeometry()
        self.resize(min(1100, available.width()-60), min(760, available.height()-60))

    def set_photo(self, image: QImage, caption: str) -> None:
        self.image = image
        self.caption.setText(caption)
        self.actual_size.setEnabled(not image.isNull())
        self._scale_image()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Hide and watched is not self.scroll.viewport():
            self.close()
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._scale_image()
        return super().eventFilter(watched, event)

    def _scale_image(self, *_args) -> None:
        if not hasattr(self, "actual_size"):
            return
        if self.image.isNull():
            self.preview.setText("No source frame available for this clip.")
            self.preview.resize(self.scroll.viewport().size())
            return
        pixmap = QPixmap.fromImage(self.image)
        if not self.actual_size.isChecked():
            pixmap = pixmap.scaled(self.scroll.viewport().size(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pixmap)
        self.preview.resize(pixmap.size())
        self.actual_size.setText("Fit image" if self.actual_size.isChecked() else "100% pixels")


class SourcePhotoPanel(QFrame):
    capture_requested = Signal()
    remove_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3SourcePhoto")
        self._clip = None
        self._source = ""
        self._image = QImage()
        self._caption = ""
        self._pending = False
        self._viewer: SourcePhotoViewer | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(6)
        self.heading = QToolButton()
        self.heading.setText("Source photo")
        self.heading.setCheckable(True)
        self.heading.setChecked(True)
        self.heading.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.heading.setIcon(tinted_icon("chevron-down-16.svg"))
        self.heading.setAccessibleName("Show or hide source photo")
        self.heading.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.heading)
        self.body = QFrame()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        self.preview = QLabel()
        self.preview.setObjectName("V3SourcePhotoPreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setFixedHeight(68)
        self.preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.preview.setAccessibleName("Saved source photo preview")
        body.addWidget(self.preview)
        self.caption = text_label()
        self.caption.setObjectName("V3SourcePhotoCaption")
        body.addWidget(self.caption)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.capture_button = QPushButton("Capture frame")
        self.capture_button.setToolTip("Save the displayed frame for this selected clip, including a frame before it.")
        self.capture_button.clicked.connect(self.capture_requested.emit)
        self.view_button = QPushButton("View source frame")
        self.view_button.setToolTip("Keep a large source frame open while editing Clip Details. Resize it or use 100% pixels to read the scoreboard.")
        self.view_button.clicked.connect(self._view)
        for button in (self.capture_button, self.view_button):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            row.addWidget(button, 1)
        self.more_button = QToolButton()
        self.more_button.setIcon(tinted_icon("more-horizontal-16.svg"))
        self.more_button.setAccessibleName("Source photo actions")
        self.more_button.setToolTip("Source photo actions")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.more_button)
        self.remove_action = menu.addAction("Remove source photo")
        self.remove_action.triggered.connect(self.remove_requested.emit)
        self.more_button.setMenu(menu)
        row.addWidget(self.more_button)
        body.addLayout(row)
        self.message = text_label()
        self.message.setObjectName("V3SourcePhotoMessage")
        body.addWidget(self.message)
        layout.addWidget(self.body)
        self.heading.toggled.connect(self._toggle)
        self.set_clip(None)

    def _toggle(self, expanded: bool) -> None:
        self.body.setVisible(expanded)
        self.heading.setIcon(tinted_icon("chevron-down-16.svg" if expanded else "chevron-right-16.svg"))

    def set_source(self, source: str) -> None:
        self._source = source
        self.refresh()

    def set_clip(self, clip) -> None:
        self._clip = clip
        self.set_pending(False)
        self.refresh()

    def set_pending(self, pending: bool) -> None:
        self._pending = pending
        self.capture_button.setText("Cancel capture" if pending else
            "Replace frame" if self._clip and (self._clip.source_photo or self._clip.source_photo_png)
            else "Capture frame")
        self.message.setText("Waiting for a painted frame…" if pending else "")
        self.message.setVisible(pending)

    def show_message(self, text: str) -> None:
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def refresh(self) -> None:
        clip = self._clip
        self._image = QImage()
        self.preview.clear()
        self._caption = "Select one clip to attach a source frame."
        has_photo = bool(clip and (clip.source_photo or clip.source_photo_png))
        if clip is not None:
            self._caption = "No source photo. Display a scoreboard or another useful frame, then capture it."
        if has_photo:
            photo = clip.source_photo
            error = validate_photo(photo, clip.source_photo_png, clip.id)
            if not error:
                self._image = QImage.fromData(clip.source_photo_png, "PNG")
                if self._image.isNull():
                    error = "Source photo unavailable: image could not be decoded."
            if error:
                self._caption = error
            else:
                relation = time_relationship(photo["position_ms"], clip)
                resolution = f"{photo['width']} × {photo['height']}"
                proxy = photo["frame_source"]["path"] != photo["original_source"]["path"]
                self._caption = (f"{format_ms(photo['position_ms'], show_millis=True)} · {relation}\n"
                    f"{resolution} · {'Preview proxy' if proxy else 'Source film'}")
                warnings = photo_warnings(photo, clip, self._source)
                if warnings:
                    self._caption += "\n" + " ".join(warnings)
        self.caption.setText(self._caption)
        self.preview.setVisible(not self._image.isNull())
        self.view_button.setEnabled(not self._image.isNull())
        self.capture_button.setEnabled(clip is not None)
        self.remove_action.setEnabled(has_photo)
        self.more_button.setEnabled(has_photo)
        self.set_pending(self._pending)
        self._scale_preview()
        if self._viewer is not None:
            # A modeless reference follows selection, replacement and Undo.
            # Never leave another clip's scoreboard beside the current fields.
            self._viewer.set_photo(self._image, self._viewer_caption())
            if clip is None:
                self._viewer.close()

    def hideEvent(self, event) -> None:
        if self._viewer is not None:
            self._viewer.close()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._scale_preview()

    def _scale_preview(self) -> None:
        if not self._image.isNull() and self.preview.width() > 0:
            self.preview.setPixmap(QPixmap.fromImage(self._image).scaled(
                self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def _viewer_caption(self) -> str:
        if self._clip is None:
            return self._caption
        caption = f"Clip {self._clip.clip_number:02d} · {self._caption}"
        if not self._image.isNull():
            photo = self._clip.source_photo
            caption += (f"\nOriginal: {photo['original_source']['path']}"
                        f"\nCaptured pixels: {photo['frame_source']['path']}")
        return caption

    def _view(self) -> None:
        if self._image.isNull() or self._clip is None:
            return
        if self._viewer is None:
            self._viewer = SourcePhotoViewer(self._image, self._viewer_caption(), self)
            # A reference window must close even when its panel is already hidden.
            self.window().installEventFilter(self._viewer)
            # Leave the inspector exposed on the first open; later opens keep
            # the analyst's chosen window position, size and pixel scale.
            available = self.screen().availableGeometry()
            owner = self.window().frameGeometry()
            left = max(available.left() + 12, owner.left() + 12)
            width = min(self._viewer.width(), self.mapToGlobal(QPoint()).x() - left - 16)
            if width >= 480:
                self._viewer.resize(width, self._viewer.height())
                self._viewer.move(left, max(available.top() + 12,
                    min(owner.top() + 64, available.bottom() - self._viewer.height() - 36)))
        self._viewer.show()
        self._viewer.raise_()
        self._viewer.activateWindow()
