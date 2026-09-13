"""Standalone manager for reusable Signature export identities.

The manager edits only the identity fields owned by :mod:`signature_template`.
It deliberately does not expose export-layer, typography, safe-area, waveform,
ink, or encoder controls.  One saved identity drives both linked 16:9 and 9:16
treatments through the same production composition-plan/compositor path.

Border assets are an explicit contract boundary.  Existing snapshots are
loaded, duplicated, renamed, and saved without alteration, but there is no
border editor and no fallback render.  The linked aspect-ratio semantics have
not been chosen, so inventing stretch/crop behavior here would corrupt the
meaning of an existing user asset.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
import re
import uuid

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QRegularExpression,
    QSignalBlocker,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QCloseEvent,
    QColor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QRegularExpressionValidator,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QCheckBox,
    QColorDialog,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tapesift.models.composition_plan import (
    IdentityAssetRole,
    LockedTemplateIdentity,
    PixelSize,
    build_composition_plan,
)
from tapesift.models.export_package import (
    CompositorInput,
    ExportPackageSnapshot,
    ExportStyle,
    ExportTemplateSnapshot,
)
from tapesift.models.export_settings import SOCIAL_1080P
from tapesift.models.signature_template import (
    ImageAssetSnapshot,
    SignatureIdentity,
    SignatureTemplate,
    SignatureTemplateValidationError,
)
from tapesift.services.preview_compositor import (
    CompositionAssetPayload,
    CompositionFrameCompositor,
    CompositionFramePayloads,
    PreviewCompositorError,
)
from tapesift.services.signature_template_repository import (
    SignatureTemplateRepositoryError,
)
from tapesift.services.signature_template_store import (
    SQLiteSignatureTemplateRepository,
    SignatureTemplateRecord,
)
from tapesift.ui_v2.tokens import COLORS, RADIUS_LG, RADIUS_MD


__all__ = (
    "LinkedSignaturePreviewBuilder",
    "SignatureTemplateManagerDialog",
    "UnsavedTemplateChangesError",
)


NO_AUDIO_PREVIEW_MESSAGE = (
    "Identity preview uses a silent placeholder frame. Voiceover and waveform "
    "appear only when an export take is selected."
)
_IMAGE_FILTER = "Identity images (*.png *.jpg *.jpeg *.webp)"
_ID_COMPONENT = re.compile(r"[^a-z0-9._-]+")


class UnsavedTemplateChangesError(RuntimeError):
    """The authoring selection cannot change until draft edits are resolved."""


class _PreviewCanvas(QLabel):
    """A label that retains the full-resolution compositor result."""

    def __init__(self, accessible_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = QImage()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(180, 150)
        self.setText("Create a template to preview it.")
        self.setAccessibleName(accessible_name)
        self.setAccessibleDescription(
            "A linked export preview rendered from the current identity fields."
        )

    def set_image(self, image: QImage) -> None:
        self._image = image.copy()
        self.setToolTip("")
        self._update_pixmap()

    def set_message(self, message: str) -> None:
        self._image = QImage()
        self.clear()
        self.setText(message)
        self.setToolTip(message)

    def full_resolution_image(self) -> QImage:
        return self._image.copy()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._update_pixmap()

    def _update_pixmap(self) -> None:
        if self._image.isNull():
            return
        available = self.contentsRect().size() - QSize(16, 16)
        if available.width() <= 0 or available.height() <= 0:
            return
        pixmap = QPixmap.fromImage(self._image).scaled(
            available,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setText("")
        self.setPixmap(pixmap)


class _AssetEditor(QFrame):
    chooseRequested = Signal(str)
    removeRequested = Signal(str)

    def __init__(
        self,
        role: str,
        title: str,
        description: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.role = role
        self._asset: ImageAssetSnapshot | None = None
        self.setObjectName(f"Signature{role.title().replace('_', '')}Editor")
        self.setProperty("assetEditor", True)
        self.setAccessibleName(f"{title} image")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 9, 10, 9)
        row.setSpacing(10)

        self.thumbnail = QLabel("None")
        self.thumbnail.setObjectName(f"Signature{role.title().replace('_', '')}Thumb")
        self.thumbnail.setFixedSize(60, 52)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setProperty("assetThumb", True)
        self.thumbnail.setAccessibleName(f"{title} preview")
        row.addWidget(self.thumbnail)

        copy = QVBoxLayout()
        copy.setSpacing(2)
        heading = QLabel(title)
        heading.setProperty("managerSection", True)
        detail = QLabel(description)
        detail.setWordWrap(True)
        detail.setProperty("managerMuted", True)
        self.metadata = QLabel("No image selected")
        self.metadata.setProperty("managerMeta", True)
        copy.addWidget(heading)
        copy.addWidget(detail)
        copy.addWidget(self.metadata)
        row.addLayout(copy, 1)

        buttons = QVBoxLayout()
        buttons.setSpacing(6)
        self.choose_button = QPushButton("Choose…")
        self.choose_button.setObjectName(
            f"ChooseSignature{role.title().replace('_', '')}"
        )
        self.choose_button.setAccessibleName(f"Choose {title.lower()} image")
        self.choose_button.setAccessibleDescription(
            "Embed a PNG, JPEG, or WebP image into this reusable identity."
        )
        self.remove_button = QToolButton()
        self.remove_button.setText("Remove")
        self.remove_button.setProperty("quiet", True)
        self.remove_button.setAccessibleName(f"Remove {title.lower()} image")
        self.remove_button.setEnabled(False)
        self.choose_button.clicked.connect(lambda: self.chooseRequested.emit(self.role))
        self.remove_button.clicked.connect(lambda: self.removeRequested.emit(self.role))
        buttons.addWidget(self.choose_button)
        buttons.addWidget(self.remove_button)
        buttons.addStretch(1)
        row.addLayout(buttons)

    def set_asset(self, asset: ImageAssetSnapshot | None) -> None:
        self._asset = asset
        self.remove_button.setEnabled(asset is not None)
        if asset is None:
            self.thumbnail.setPixmap(QPixmap())
            self.thumbnail.setText("None")
            self.metadata.setText("No image selected")
            self.thumbnail.setAccessibleDescription("No image selected")
            return
        image = QImage.fromData(asset.data)
        pixmap = QPixmap.fromImage(image).scaled(
            self.thumbnail.size() - QSize(8, 8),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.thumbnail.setText("")
        self.thumbnail.setPixmap(pixmap)
        kind = asset.mime_type.removeprefix("image/").upper()
        self.metadata.setText(f"{kind} · {asset.width} × {asset.height}")
        self.thumbnail.setAccessibleDescription(
            f"Embedded {kind} image, {asset.width} by {asset.height} pixels"
        )


def _encode_png(image: QImage) -> bytes:
    encoded = QByteArray()
    buffer = QBuffer(encoded)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise PreviewCompositorError("Placeholder preview buffer could not be opened.")
    try:
        if not image.save(buffer, "PNG"):
            raise PreviewCompositorError("Placeholder preview could not be encoded.")
    finally:
        buffer.close()
    return bytes(encoded)


def _placeholder_film_frame() -> bytes:
    """Return a deterministic 16:9 frame clearly distinct from real film."""

    image = QImage(640, 360, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#203027"))
    painter = QPainter(image)
    if not painter.isActive():
        raise PreviewCompositorError("Placeholder preview painter could not start.")
    try:
        painter.fillRect(0, 0, 640, 52, QColor("#18231D"))
        painter.fillRect(0, 308, 640, 52, QColor("#18231D"))
        pen = QPen(QColor(214, 223, 207, 84), 2)
        painter.setPen(pen)
        for x in range(80, 640, 80):
            painter.drawLine(x, 52, x, 308)
        painter.drawLine(0, 180, 640, 180)
        painter.setPen(QPen(QColor(57, 224, 122, 130), 3))
        painter.drawRect(254, 116, 132, 128)
    finally:
        painter.end()
    return _encode_png(image)


class LinkedSignaturePreviewBuilder:
    """Render the two fixed output treatments from one immutable identity.

    This authoring preview intentionally has no audio input.  That absence is
    explicit rather than simulated: waveform layers require a real selected
    Voiceover take and therefore belong to Export Package preview, not to an
    identity editor.
    """

    def __init__(self) -> None:
        self._compositor = CompositionFrameCompositor()
        self._source_frame = _placeholder_film_frame()

    def render(
        self,
        template: SignatureTemplate,
        revision: int,
    ) -> dict[ExportStyle, QImage]:
        if type(revision) is not int or revision < 1:
            raise SignatureTemplateValidationError(
                "preview revision must be a positive integer"
            )
        snapshot = ExportTemplateSnapshot(
            template_id=template.template_id,
            revision=revision,
            payload_json=template.to_json(),
        )
        locked = LockedTemplateIdentity.capture(snapshot, template)
        inputs = [
            CompositorInput("source_video", "identity-preview-placeholder"),
            CompositorInput("play_call_situation", "3RD & 7 / FSU 38"),
            CompositorInput("play_call_concept", "BECK FLEA FLICKER"),
        ]
        if template.identity.show_result:
            inputs.append(CompositorInput("play_call_result", "TOUCHDOWN"))

        assets: list[CompositionAssetPayload] = []
        if template.identity.wordmark_logo is not None:
            assets.append(CompositionAssetPayload(
                IdentityAssetRole.WORDMARK_LOGO,
                template.identity.wordmark_logo.data,
            ))
        if template.identity.profile_photo is not None:
            assets.append(CompositionAssetPayload(
                IdentityAssetRole.PROFILE_PHOTO,
                template.identity.profile_photo.data,
            ))

        results: dict[ExportStyle, QImage] = {}
        for style in (ExportStyle.SIGNATURE, ExportStyle.VERTICAL):
            package = ExportPackageSnapshot(
                style=style,
                technical_preset=SOCIAL_1080P.name,
                template=snapshot,
                compositor_inputs=tuple(inputs),
                include_play_call=True,
                include_voiceover=False,
            )
            plan = build_composition_plan(
                package,
                PixelSize(640, 360),
                template_identity=locked,
            )
            results[style] = self._compositor.render(
                plan,
                CompositionFramePayloads(
                    source_video=self._source_frame,
                    assets=tuple(assets),
                    play_call_situation=b"3RD & 7 / FSU 38",
                    play_call_concept=b"BECK FLEA FLICKER",
                    play_call_result=(
                        b"TOUCHDOWN" if template.identity.show_result else None
                    ),
                    timeline_position=2,
                    timeline_duration=5,
                ),
            )
        return results


class SignatureTemplateManagerDialog(QDialog):
    """Manage the global, revisioned library of Signature identities."""

    defaultTemplateChanged = Signal(str)
    templateSaved = Signal(str, int)
    libraryChanged = Signal()

    def __init__(
        self,
        app_data_path: str | Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SignatureTemplateManager")
        self.setWindowTitle("Signature Template Manager")
        self.setModal(True)
        self.setMinimumSize(1060, 720)
        self.resize(1380, 850)
        self.setAccessibleName("Signature template manager")
        self.setAccessibleDescription(
            "Create and edit reusable export identities shared by Signature "
            "16 by 9 and Vertical 9 by 16 outputs."
        )

        self._repository = SQLiteSignatureTemplateRepository(app_data_path)
        self._current_record: SignatureTemplateRecord | None = None
        self._records: dict[str, SignatureTemplateRecord] = {}
        self._loading = False
        self._dirty = False
        self._closed_repository = False
        self._wordmark_asset: ImageAssetSnapshot | None = None
        self._profile_asset: ImageAssetSnapshot | None = None
        self._preview_builder = LinkedSignaturePreviewBuilder()
        self._preview_render_count = 0

        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(140)
        self._preview_timer.timeout.connect(self.refresh_previews)

        self._build_ui()
        self._connect_editor()
        self.setStyleSheet(_manager_stylesheet())
        self.refresh_library()

    @property
    def repository(self) -> SQLiteSignatureTemplateRepository:
        return self._repository

    @property
    def current_record(self) -> SignatureTemplateRecord | None:
        return self._current_record

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def preview_render_count(self) -> int:
        return self._preview_render_count

    def preview_image(self, style: ExportStyle) -> QImage:
        style = ExportStyle(style)
        canvas = (
            self.signature_preview
            if style is ExportStyle.SIGNATURE
            else self.vertical_preview
        )
        return canvas.full_resolution_image()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        eyebrow = QLabel("EXPORT IDENTITY")
        eyebrow.setProperty("managerEyebrow", True)
        title = QLabel("Template Manager")
        title.setProperty("managerTitle", True)
        subtitle = QLabel(
            "One reusable identity drives linked Signature 16:9 and Vertical 9:16 exports."
        )
        subtitle.setProperty("managerMuted", True)
        subtitle.setWordWrap(True)
        titles.addWidget(eyebrow)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles, 1)
        self.close_button = QToolButton()
        self.close_button.setText("Close")
        self.close_button.setProperty("quiet", True)
        self.close_button.setAccessibleName("Close template manager")
        self.close_button.clicked.connect(self.close)
        header.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("SignatureTemplateManagerSplitter")
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        library_panel = QFrame()
        library_panel.setObjectName("SignatureTemplateLibraryPanel")
        library_panel.setProperty("managerPanel", True)
        library_panel.setMinimumWidth(252)
        library_panel.setMaximumWidth(330)
        library_layout = QVBoxLayout(library_panel)
        library_layout.setContentsMargins(12, 12, 12, 12)
        library_layout.setSpacing(9)
        library_heading = QLabel("Saved identities")
        library_heading.setProperty("managerSection", True)
        library_layout.addWidget(library_heading)

        self.library_list = QListWidget()
        self.library_list.setObjectName("SignatureTemplateLibrary")
        self.library_list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self.library_list.setAccessibleName("Saved Signature identities")
        self.library_list.setAccessibleDescription(
            "Selects which identity is being edited. Selection does not change "
            "the export default."
        )
        self.library_list.currentItemChanged.connect(self._on_library_item_changed)
        library_layout.addWidget(self.library_list, 1)

        create_row = QHBoxLayout()
        create_row.setSpacing(6)
        self.new_button = QPushButton("New")
        self.new_button.setObjectName("NewSignatureTemplate")
        self.new_button.setAccessibleName("Create new Signature identity")
        self.duplicate_button = QPushButton("Duplicate")
        self.duplicate_button.setObjectName("DuplicateSignatureTemplate")
        self.duplicate_button.setAccessibleName("Duplicate selected Signature identity")
        create_row.addWidget(self.new_button)
        create_row.addWidget(self.duplicate_button)
        library_layout.addLayout(create_row)

        manage_row = QHBoxLayout()
        manage_row.setSpacing(6)
        self.rename_button = QPushButton("Rename")
        self.rename_button.setObjectName("RenameSignatureTemplate")
        self.rename_button.setAccessibleName("Rename selected Signature identity")
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("DeleteSignatureTemplate")
        self.delete_button.setProperty("destructive", True)
        self.delete_button.setAccessibleName("Delete selected Signature identity")
        manage_row.addWidget(self.rename_button)
        manage_row.addWidget(self.delete_button)
        library_layout.addLayout(manage_row)

        self.default_button = QPushButton("Use as export default")
        self.default_button.setObjectName("SetDefaultSignatureTemplate")
        self.default_button.setProperty("primary", True)
        self.default_button.setAccessibleName(
            "Use selected Signature identity as export default"
        )
        self.default_button.setAccessibleDescription(
            "Changes the one global default used when Export Package opens."
        )
        library_layout.addWidget(self.default_button)
        splitter.addWidget(library_panel)

        editor_scroll = QScrollArea()
        editor_scroll.setObjectName("SignatureTemplateEditorScroll")
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setFrameShape(QFrame.Shape.NoFrame)
        editor_scroll.setAccessibleName("Signature identity editor")
        editor = QWidget()
        editor.setObjectName("SignatureTemplateEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(4, 0, 4, 4)
        editor_layout.setSpacing(12)

        editor_header = QFrame()
        editor_header.setObjectName("SignatureTemplateEditorHeader")
        editor_header.setProperty("managerPanel", True)
        editor_header_layout = QHBoxLayout(editor_header)
        editor_header_layout.setContentsMargins(14, 11, 14, 11)
        editor_copy = QVBoxLayout()
        editor_copy.setSpacing(1)
        self.editor_name_label = QLabel("No template selected")
        self.editor_name_label.setObjectName("SignatureTemplateEditorName")
        self.editor_name_label.setProperty("managerSection", True)
        self.revision_label = QLabel("Create a template to begin")
        self.revision_label.setProperty("managerMeta", True)
        editor_copy.addWidget(self.editor_name_label)
        editor_copy.addWidget(self.revision_label)
        editor_header_layout.addLayout(editor_copy, 1)
        self.save_button = QPushButton("Save changes")
        self.save_button.setObjectName("SaveSignatureTemplate")
        self.save_button.setProperty("primary", True)
        self.save_button.setAccessibleName("Save Signature identity changes")
        self.save_button.setEnabled(False)
        editor_header_layout.addWidget(self.save_button)
        editor_layout.addWidget(editor_header)

        identity_panel = QFrame()
        identity_panel.setObjectName("SignatureIdentityFieldsPanel")
        identity_panel.setProperty("managerPanel", True)
        identity_layout = QVBoxLayout(identity_panel)
        identity_layout.setContentsMargins(14, 13, 14, 14)
        identity_layout.setSpacing(10)
        identity_title = QLabel("Identity")
        identity_title.setProperty("managerSection", True)
        identity_layout.addWidget(identity_title)

        form = QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(9)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.display_text_edit = QLineEdit()
        self.display_text_edit.setObjectName("SignatureDisplayText")
        self.display_text_edit.setMaxLength(120)
        self.display_text_edit.setAccessibleName("Display name")
        self.display_text_edit.setAccessibleDescription(
            "The primary identity line shown in linked export treatments."
        )
        self.username_text_edit = QLineEdit()
        self.username_text_edit.setObjectName("SignatureUsernameText")
        self.username_text_edit.setMaxLength(80)
        self.username_text_edit.setPlaceholderText("Optional, for example @coach_view")
        self.username_text_edit.setAccessibleName("Username")
        self.wordmark_text_edit = QLineEdit()
        self.wordmark_text_edit.setObjectName("SignatureWordmarkText")
        self.wordmark_text_edit.setMaxLength(80)
        self.wordmark_text_edit.setPlaceholderText("Used when no wordmark image is set")
        self.wordmark_text_edit.setAccessibleName("Wordmark text")

        self.accent_color_edit = QLineEdit()
        self.accent_color_edit.setObjectName("SignatureAccentColor")
        self.accent_color_edit.setMaxLength(7)
        self.accent_color_edit.setValidator(QRegularExpressionValidator(
            QRegularExpression(r"#[0-9A-Fa-f]{0,6}"), self.accent_color_edit
        ))
        self.accent_color_edit.setAccessibleName("Accent color hexadecimal value")
        self.accent_swatch = QToolButton()
        self.accent_swatch.setObjectName("SignatureAccentSwatch")
        self.accent_swatch.setFixedSize(34, 34)
        self.accent_swatch.setAccessibleName("Choose accent color")
        accent_row = QHBoxLayout()
        accent_row.setContentsMargins(0, 0, 0, 0)
        accent_row.setSpacing(7)
        accent_row.addWidget(self.accent_color_edit, 1)
        accent_row.addWidget(self.accent_swatch)
        accent_widget = QWidget()
        accent_widget.setLayout(accent_row)

        self.show_result_check = QCheckBox("Show play result when one is supplied")
        self.show_result_check.setObjectName("SignatureShowResult")
        self.show_result_check.setAccessibleName("Show play result")
        self.show_result_check.setAccessibleDescription(
            "Controls whether a supplied result is visible in Signature and Vertical."
        )

        for text, widget in (
            ("Display name", self.display_text_edit),
            ("Username", self.username_text_edit),
            ("Wordmark text", self.wordmark_text_edit),
            ("Accent", self.accent_color_edit),
        ):
            label = QLabel(text)
            label.setBuddy(widget)
            label.setProperty("managerFieldLabel", True)
            form.addRow(label, accent_widget if text == "Accent" else widget)
        result_label = QLabel("Result")
        result_label.setBuddy(self.show_result_check)
        result_label.setProperty("managerFieldLabel", True)
        form.addRow(result_label, self.show_result_check)
        identity_layout.addLayout(form)
        editor_layout.addWidget(identity_panel)

        assets_panel = QFrame()
        assets_panel.setObjectName("SignatureIdentityAssetsPanel")
        assets_panel.setProperty("managerPanel", True)
        assets_layout = QVBoxLayout(assets_panel)
        assets_layout.setContentsMargins(14, 13, 14, 14)
        assets_layout.setSpacing(9)
        assets_title = QLabel("Embedded identity images")
        assets_title.setProperty("managerSection", True)
        assets_help = QLabel(
            "Images are copied into the template library; moving the original file will not break an export."
        )
        assets_help.setProperty("managerMuted", True)
        assets_help.setWordWrap(True)
        assets_layout.addWidget(assets_title)
        assets_layout.addWidget(assets_help)
        self.wordmark_asset_editor = _AssetEditor(
            "wordmark_logo",
            "Wordmark logo",
            "Optional image; wordmark text remains the fallback.",
        )
        self.profile_asset_editor = _AssetEditor(
            "profile_photo",
            "Profile photo",
            "Optional image, rendered with the compositor's circular mask.",
        )
        self._asset_editors_layout = QBoxLayout(
            QBoxLayout.Direction.LeftToRight)
        self._asset_editors_layout.setContentsMargins(0, 0, 0, 0)
        self._asset_editors_layout.setSpacing(10)
        self._asset_editors_layout.addWidget(self.wordmark_asset_editor, 1)
        self._asset_editors_layout.addWidget(self.profile_asset_editor, 1)
        assets_layout.addLayout(self._asset_editors_layout)

        self.border_policy_note = QLabel(
            "Border image editing is unavailable until the linked 16:9 and 9:16 fit behavior is defined."
        )
        self.border_policy_note.setObjectName("SignatureBorderPolicyNote")
        self.border_policy_note.setProperty("managerMeta", True)
        self.border_policy_note.setWordWrap(True)
        self.border_policy_note.setAccessibleName("Border image editing status")
        self.border_policy_note.setAccessibleDescription(
            "There is no active border control because linked aspect-ratio behavior has not been approved."
        )
        assets_layout.addWidget(self.border_policy_note)

        self.border_notice = QFrame()
        self.border_notice.setObjectName("SignatureStoredBorderNotice")
        self.border_notice.setProperty("managerNotice", True)
        self.border_notice.setAccessibleName("Stored border asset notice")
        border_layout = QVBoxLayout(self.border_notice)
        border_layout.setContentsMargins(10, 9, 10, 9)
        border_layout.setSpacing(2)
        border_title = QLabel("Stored border preserved")
        border_title.setProperty("managerSection", True)
        border_copy = QLabel(
            "This identity already contains a border image. It remains embedded and is preserved by save, rename, and duplicate. Editing and preview stay unavailable until linked 16:9/9:16 border behavior is defined."
        )
        border_copy.setWordWrap(True)
        border_copy.setProperty("managerMuted", True)
        border_layout.addWidget(border_title)
        border_layout.addWidget(border_copy)
        self.border_notice.setVisible(False)
        assets_layout.addWidget(self.border_notice)
        editor_layout.addWidget(assets_panel)

        previews_panel = QFrame()
        previews_panel.setObjectName("SignatureLinkedPreviewsPanel")
        previews_panel.setProperty("managerPanel", True)
        previews_layout = QVBoxLayout(previews_panel)
        previews_layout.setContentsMargins(14, 13, 14, 14)
        previews_layout.setSpacing(9)
        previews_header = QHBoxLayout()
        previews_title = QLabel("Linked output previews")
        previews_title.setProperty("managerSection", True)
        self.preview_status_label = QLabel("")
        self.preview_status_label.setObjectName("SignaturePreviewStatus")
        self.preview_status_label.setProperty("managerMeta", True)
        self.preview_status_label.setAccessibleName("Linked preview status")
        previews_header.addWidget(previews_title)
        previews_header.addStretch(1)
        previews_header.addWidget(self.preview_status_label)
        previews_layout.addLayout(previews_header)

        preview_row = QHBoxLayout()
        preview_row.setSpacing(10)
        signature_card, self.signature_preview = self._make_preview_card(
            "SIGNATURE · 16:9", "Signature 16 by 9 linked preview"
        )
        vertical_card, self.vertical_preview = self._make_preview_card(
            "VERTICAL · 9:16", "Vertical 9 by 16 linked preview"
        )
        signature_card.setMinimumHeight(250)
        vertical_card.setMinimumHeight(250)
        preview_row.addWidget(signature_card, 3)
        preview_row.addWidget(vertical_card, 1)
        previews_layout.addLayout(preview_row, 1)
        no_audio = QLabel(NO_AUDIO_PREVIEW_MESSAGE)
        no_audio.setObjectName("SignatureNoAudioPreviewSeam")
        no_audio.setProperty("managerMuted", True)
        no_audio.setWordWrap(True)
        no_audio.setAccessibleName("Identity preview audio limitation")
        no_audio.setAccessibleDescription(NO_AUDIO_PREVIEW_MESSAGE)
        previews_layout.addWidget(no_audio)
        editor_layout.addWidget(previews_panel, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("SignatureTemplateManagerStatus")
        self.status_label.setProperty("managerStatus", "neutral")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("Template manager status")
        editor_layout.addWidget(self.status_label)

        editor_scroll.setWidget(editor)
        splitter.addWidget(editor_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 1050])
        self._update_asset_editor_layout(self.width())

        self.new_button.clicked.connect(self._prompt_create)
        self.duplicate_button.clicked.connect(self._prompt_duplicate)
        self.rename_button.clicked.connect(self._prompt_rename)
        self.delete_button.clicked.connect(self._prompt_delete)
        self.default_button.clicked.connect(self._set_current_default)
        self.save_button.clicked.connect(self._save_from_ui)

        self.setTabOrder(self.library_list, self.new_button)
        self.setTabOrder(self.new_button, self.duplicate_button)
        self.setTabOrder(self.duplicate_button, self.rename_button)
        self.setTabOrder(self.rename_button, self.delete_button)
        self.setTabOrder(self.delete_button, self.default_button)
        self.setTabOrder(self.default_button, self.display_text_edit)
        self.setTabOrder(self.display_text_edit, self.username_text_edit)
        self.setTabOrder(self.username_text_edit, self.wordmark_text_edit)
        self.setTabOrder(self.wordmark_text_edit, self.accent_color_edit)
        self.setTabOrder(self.accent_color_edit, self.accent_swatch)
        self.setTabOrder(self.accent_swatch, self.show_result_check)
        self.setTabOrder(self.show_result_check, self.wordmark_asset_editor.choose_button)
        self.setTabOrder(
            self.wordmark_asset_editor.choose_button,
            self.wordmark_asset_editor.remove_button,
        )
        self.setTabOrder(
            self.wordmark_asset_editor.remove_button,
            self.profile_asset_editor.choose_button,
        )
        self.setTabOrder(
            self.profile_asset_editor.choose_button,
            self.profile_asset_editor.remove_button,
        )
        self.setTabOrder(self.profile_asset_editor.remove_button, self.save_button)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if hasattr(self, "_asset_editors_layout"):
            self._update_asset_editor_layout(event.size().width())

    def _update_asset_editor_layout(self, width: int) -> None:
        """Use the editor's available width before spending vertical space."""

        direction = (
            QBoxLayout.Direction.LeftToRight
            if int(width) >= 1240
            else QBoxLayout.Direction.TopToBottom
        )
        if self._asset_editors_layout.direction() is not direction:
            self._asset_editors_layout.setDirection(direction)
            self._asset_editors_layout.invalidate()

    def _make_preview_card(
        self,
        title: str,
        accessible_name: str,
    ) -> tuple[QFrame, _PreviewCanvas]:
        card = QFrame()
        card.setProperty("previewCard", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        label = QLabel(title)
        label.setProperty("managerEyebrow", True)
        canvas = _PreviewCanvas(accessible_name)
        canvas.setProperty("previewCanvas", True)
        layout.addWidget(label)
        layout.addWidget(canvas, 1)
        return card, canvas

    def _connect_editor(self) -> None:
        for edit in (
            self.display_text_edit,
            self.username_text_edit,
            self.wordmark_text_edit,
            self.accent_color_edit,
        ):
            edit.textChanged.connect(self._on_editor_changed)
        self.show_result_check.toggled.connect(self._on_editor_changed)
        self.accent_color_edit.textChanged.connect(self._update_accent_swatch)
        self.accent_swatch.clicked.connect(self._choose_accent)
        for asset_editor in (
            self.wordmark_asset_editor,
            self.profile_asset_editor,
        ):
            asset_editor.chooseRequested.connect(self._choose_asset)
            asset_editor.removeRequested.connect(self.remove_asset)

    def refresh_library(self, preferred_id: str | None = None) -> None:
        records = self._repository.list_records()
        self._records = {record.template_id: record for record in records}
        current_id = (
            preferred_id
            or (self._current_record.template_id if self._current_record else None)
        )
        if current_id not in self._records:
            selected = next((record for record in records if record.is_selected), None)
            current_id = selected.template_id if selected else (
                records[0].template_id if records else None
            )

        blocker = QSignalBlocker(self.library_list)
        try:
            self.library_list.clear()
            current_item: QListWidgetItem | None = None
            for record in records:
                label = f"{record.name}\n{'DEFAULT' if record.is_selected else 'Identity'}"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, record.template_id)
                accessible = record.name + (
                    ", export default" if record.is_selected else ", saved identity"
                )
                item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible)
                item.setToolTip(accessible)
                self.library_list.addItem(item)
                if record.template_id == current_id:
                    current_item = item
            if current_item is not None:
                self.library_list.setCurrentItem(current_item)
        finally:
            del blocker

        self._load_record(self._records.get(current_id) if current_id else None)
        has_record = self._current_record is not None
        for button in (
            self.duplicate_button,
            self.rename_button,
            self.delete_button,
        ):
            button.setEnabled(has_record)
        self.default_button.setEnabled(
            has_record and not self._current_record.is_selected
        )
        self.libraryChanged.emit()

    def select_template(
        self,
        template_id: str,
        *,
        discard_unsaved: bool = False,
    ) -> SignatureTemplateRecord:
        if self._dirty and not discard_unsaved:
            raise UnsavedTemplateChangesError(
                "save or discard the current identity edits before changing selection"
            )
        record = self._records.get(template_id)
        if record is None:
            refreshed = self._repository.get_record(template_id)
            if refreshed is None:
                raise SignatureTemplateRepositoryError(
                    f"template {template_id!r} does not exist"
                )
            self.refresh_library(template_id)
            record = self._records[template_id]
        else:
            self._set_list_current(template_id)
            self._load_record(record)
        return record

    def create_template(self, name: str) -> SignatureTemplateRecord:
        template = SignatureTemplate(
            template_id=self._new_template_id(name),
            name=name,
            identity=SignatureIdentity(
                display_text=name,
                wordmark_text="TapeSift",
                accent_color="#39E07A",
                show_result=True,
            ),
        )
        record = self._repository.create(template)
        self.refresh_library(record.template_id)
        self._set_status(f"Created {record.name}.", error=False)
        return self._records[record.template_id]

    def duplicate_template(self, name: str) -> SignatureTemplateRecord:
        current = self._require_current()
        duplicate = SignatureTemplate(
            template_id=self._new_template_id(name),
            name=name,
            # Image snapshots, including an existing border, are immutable and
            # safe to share. The SQLite store writes its own BLOB rows.
            identity=current.template.identity,
        )
        record = self._repository.create(duplicate)
        self.refresh_library(record.template_id)
        self._set_status(f"Duplicated {current.name} as {record.name}.", error=False)
        return self._records[record.template_id]

    def rename_template(self, name: str) -> SignatureTemplateRecord:
        current = self._require_current()
        renamed = replace(current.template, name=name)
        record = self._repository.update(
            renamed,
            expected_revision=current.revision,
        )
        self.refresh_library(record.template_id)
        self._set_status(f"Renamed identity to {record.name}.", error=False)
        return self._records[record.template_id]

    def delete_template(self) -> bool:
        current = self._require_current()
        deleted = self._repository.delete(
            current.template_id,
            expected_revision=current.revision,
        )
        self._current_record = None
        self._dirty = False
        self.refresh_library()
        if deleted:
            self._set_status(f"Deleted {current.name}.", error=False)
        return deleted

    def set_default_template(self) -> SignatureTemplateRecord:
        current = self._require_current()
        record = self._repository.set_default(current.template_id)
        self.refresh_library(current.template_id)
        self.defaultTemplateChanged.emit(record.template_id)
        self._set_status(f"{record.name} is now the export default.", error=False)
        return self._records[record.template_id]

    def save_current_template(self) -> SignatureTemplateRecord:
        current = self._require_current()
        draft = self._draft_template()
        record = self._repository.update(
            draft,
            expected_revision=current.revision,
        )
        self.refresh_library(record.template_id)
        saved = self._records[record.template_id]
        self.templateSaved.emit(saved.template_id, saved.revision)
        self._set_status(
            f"Saved {saved.name} · revision {saved.revision}.", error=False
        )
        return saved

    def discard_current_changes(self) -> SignatureTemplateRecord | None:
        """Reload the selected record without mutating the stored revision."""

        current = self._current_record
        if current is None:
            self._dirty = False
            return None
        refreshed = self._repository.get_record(current.template_id)
        if refreshed is None:
            self.refresh_library()
            return None
        self.refresh_library(refreshed.template_id)
        self._set_status("Draft changes discarded.", error=False)
        return self._current_record

    def set_asset_from_path(self, role: str, path: str | Path) -> ImageAssetSnapshot:
        if role not in {"wordmark_logo", "profile_photo"}:
            raise SignatureTemplateValidationError(
                "only wordmark logo and profile photo can be edited here"
            )
        self._require_current()
        asset = ImageAssetSnapshot.capture_file(path)
        if role == "wordmark_logo":
            self._wordmark_asset = asset
            self.wordmark_asset_editor.set_asset(asset)
        else:
            self._profile_asset = asset
            self.profile_asset_editor.set_asset(asset)
        self._mark_dirty()
        return asset

    def remove_asset(self, role: str) -> None:
        self._require_current()
        if role == "wordmark_logo":
            self._wordmark_asset = None
            self.wordmark_asset_editor.set_asset(None)
        elif role == "profile_photo":
            self._profile_asset = None
            self.profile_asset_editor.set_asset(None)
        else:
            raise SignatureTemplateValidationError(
                "only wordmark logo and profile photo can be edited here"
            )
        self._mark_dirty()

    def refresh_previews(self) -> None:
        current = self._current_record
        if current is None:
            message = "Create a template to preview it."
            self.signature_preview.set_message(message)
            self.vertical_preview.set_message(message)
            self.preview_status_label.setText("")
            return
        try:
            draft = self._draft_template()
            images = self._preview_builder.render(draft, current.revision)
        except (SignatureTemplateValidationError, PreviewCompositorError, ValueError) as exc:
            message = f"Preview unavailable: {exc}"
            self.signature_preview.set_message(message)
            self.vertical_preview.set_message(message)
            self.preview_status_label.setText("Needs attention")
            self.preview_status_label.setAccessibleDescription(message)
            return
        self.signature_preview.set_image(images[ExportStyle.SIGNATURE])
        self.vertical_preview.set_image(images[ExportStyle.VERTICAL])
        self.preview_status_label.setText("Silent identity preview")
        self.preview_status_label.setAccessibleDescription(NO_AUDIO_PREVIEW_MESSAGE)
        self._preview_render_count += 1

    def _load_record(self, record: SignatureTemplateRecord | None) -> None:
        self._preview_timer.stop()
        self._loading = True
        try:
            self._current_record = record
            if record is None:
                self.editor_name_label.setText("No template selected")
                self.revision_label.setText("Create a template to begin")
                self.display_text_edit.clear()
                self.username_text_edit.clear()
                self.wordmark_text_edit.clear()
                self.accent_color_edit.setText("#39E07A")
                self.show_result_check.setChecked(True)
                self._wordmark_asset = None
                self._profile_asset = None
                self.wordmark_asset_editor.set_asset(None)
                self.profile_asset_editor.set_asset(None)
                self.border_notice.setVisible(False)
            else:
                identity = record.template.identity
                self.editor_name_label.setText(record.name)
                default_copy = " · export default" if record.is_selected else ""
                self.revision_label.setText(
                    f"Revision {record.revision}{default_copy}"
                )
                self.display_text_edit.setText(identity.display_text)
                self.username_text_edit.setText(identity.username_text)
                self.wordmark_text_edit.setText(identity.wordmark_text)
                self.accent_color_edit.setText(identity.accent_color)
                self.show_result_check.setChecked(identity.show_result)
                self._wordmark_asset = identity.wordmark_logo
                self._profile_asset = identity.profile_photo
                self.wordmark_asset_editor.set_asset(self._wordmark_asset)
                self.profile_asset_editor.set_asset(self._profile_asset)
                self.border_notice.setVisible(identity.border is not None)
                self.border_notice.setAccessibleDescription(
                    "An embedded border image is preserved but cannot be edited "
                    "or previewed until linked aspect-ratio behavior is defined."
                    if identity.border is not None
                    else ""
                )
        finally:
            self._loading = False

        enabled = record is not None
        for widget in (
            self.display_text_edit,
            self.username_text_edit,
            self.wordmark_text_edit,
            self.accent_color_edit,
            self.accent_swatch,
            self.show_result_check,
            self.wordmark_asset_editor,
            self.profile_asset_editor,
        ):
            widget.setEnabled(enabled)
        self.default_button.setEnabled(enabled and not record.is_selected if record else False)
        self._dirty = False
        self.save_button.setEnabled(False)
        self._update_accent_swatch(self.accent_color_edit.text())
        self.refresh_previews()

    def _draft_template(self) -> SignatureTemplate:
        current = self._require_current()
        # Border is copied from the loaded revision verbatim. There is no UI
        # path that can replace or clear it while its linked-output semantics
        # remain undefined.
        border = current.template.identity.border
        return SignatureTemplate(
            template_id=current.template_id,
            name=current.name,
            identity=SignatureIdentity(
                display_text=self.display_text_edit.text(),
                username_text=self.username_text_edit.text(),
                wordmark_text=self.wordmark_text_edit.text(),
                wordmark_logo=self._wordmark_asset,
                profile_photo=self._profile_asset,
                border=border,
                accent_color=self.accent_color_edit.text(),
                show_result=self.show_result_check.isChecked(),
            ),
        )

    def _require_current(self) -> SignatureTemplateRecord:
        if self._current_record is None:
            raise SignatureTemplateRepositoryError("no Signature identity is selected")
        return self._current_record

    def _new_template_id(self, name: str) -> str:
        if not isinstance(name, str):
            raise SignatureTemplateValidationError("template name must be text")
        normalized = _ID_COMPONENT.sub("-", name.strip().casefold()).strip("-._")
        base = normalized[:44] or "identity"
        while True:
            candidate = f"{base}-{uuid.uuid4().hex[:10]}"
            if self._repository.get(candidate) is None:
                return candidate

    def _mark_dirty(self) -> None:
        if self._loading or self._current_record is None:
            return
        self._dirty = True
        self.save_button.setEnabled(True)
        self._preview_timer.start()

    def _on_editor_changed(self, *_args: object) -> None:
        self._mark_dirty()

    def _update_accent_swatch(self, value: str) -> None:
        color = QColor(value)
        if not color.isValid():
            color = QColor(COLORS["surface"])
        self.accent_swatch.setStyleSheet(
            "QToolButton {"
            f"background-color: {color.name()};"
            f"border: 1px solid {COLORS['line_strong']};"
            f"border-radius: {RADIUS_MD}px;"
            "}"
        )

    def _set_list_current(self, template_id: str | None) -> None:
        blocker = QSignalBlocker(self.library_list)
        try:
            for index in range(self.library_list.count()):
                item = self.library_list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == template_id:
                    self.library_list.setCurrentItem(item)
                    return
            self.library_list.setCurrentItem(None)
        finally:
            del blocker

    def _on_library_item_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        template_id = (
            current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        )
        if template_id is None or (
            self._current_record is not None
            and template_id == self._current_record.template_id
        ):
            return
        if self._dirty and not self._resolve_unsaved_edits():
            self._set_list_current(
                self._current_record.template_id if self._current_record else None
            )
            return
        try:
            self.select_template(template_id, discard_unsaved=True)
        except SignatureTemplateRepositoryError as exc:
            self._set_status(str(exc), error=True)

    def _resolve_unsaved_edits(self) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Unsaved identity changes")
        box.setText("Save changes before editing another identity?")
        box.setInformativeText(
            "The export default will not change unless you choose Use as export default."
        )
        box.setIcon(QMessageBox.Icon.Question)
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        box.setDefaultButton(QMessageBox.StandardButton.Save)
        decision = box.exec()
        if decision == QMessageBox.StandardButton.Cancel:
            return False
        if decision == QMessageBox.StandardButton.Discard:
            self._dirty = False
            return True
        return self._run_ui_operation(self.save_current_template)

    def _prompt_create(self) -> None:
        name, accepted = QInputDialog.getText(
            self,
            "New identity",
            "Template name",
        )
        if accepted:
            self._run_ui_operation(lambda: self.create_template(name))

    def _prompt_duplicate(self) -> None:
        current = self._current_record
        if current is None:
            return
        if self._dirty and not self._resolve_unsaved_edits():
            return
        current = self._current_record
        if current is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Duplicate identity",
            "New template name",
            text=f"{current.name} Copy",
        )
        if accepted:
            self._run_ui_operation(lambda: self.duplicate_template(name))

    def _prompt_rename(self) -> None:
        current = self._current_record
        if current is None:
            return
        if self._dirty and not self._resolve_unsaved_edits():
            return
        current = self._current_record
        if current is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "Rename identity",
            "Template name",
            text=current.name,
        )
        if accepted and name != current.name:
            self._run_ui_operation(lambda: self.rename_template(name))

    def _prompt_delete(self) -> None:
        current = self._current_record
        if current is None:
            return
        if self._dirty and not self._resolve_unsaved_edits():
            return
        current = self._current_record
        if current is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Delete identity")
        box.setText(f"Delete {current.name}?")
        box.setInformativeText(
            "This removes the template and its embedded image copies."
        )
        box.setIcon(QMessageBox.Icon.Warning)
        delete_action = box.addButton(
            "Delete identity", QMessageBox.ButtonRole.DestructiveRole
        )
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is delete_action:
            self._run_ui_operation(self.delete_template)

    def _set_current_default(self) -> None:
        if self._dirty and not self._resolve_unsaved_edits():
            return
        self._run_ui_operation(self.set_default_template)

    def _save_from_ui(self) -> None:
        self._run_ui_operation(self.save_current_template)

    def _choose_asset(self, role: str) -> None:
        title = "Choose wordmark logo" if role == "wordmark_logo" else "Choose profile photo"
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            title,
            "",
            _IMAGE_FILTER,
        )
        if not path:
            return
        self._run_ui_operation(lambda: self.set_asset_from_path(role, path))

    def _choose_accent(self) -> None:
        current = QColor(self.accent_color_edit.text())
        if not current.isValid():
            current = QColor("#39E07A")
        chosen = QColorDialog.getColor(
            current,
            self,
            "Choose identity accent",
            QColorDialog.ColorDialogOption.DontUseNativeDialog,
        )
        if chosen.isValid():
            self.accent_color_edit.setText(chosen.name().upper())

    def _run_ui_operation(self, operation: Callable[[], object]) -> bool:
        try:
            operation()
        except (SignatureTemplateRepositoryError, SignatureTemplateValidationError) as exc:
            self._set_status(str(exc), error=True)
            return False
        return True

    def _set_status(self, message: str, *, error: bool) -> None:
        self.status_label.setText(message)
        self.status_label.setProperty("managerStatus", "error" if error else "ok")
        self.status_label.setAccessibleDescription(message)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _prepare_to_close(self) -> bool:
        if self._dirty and not self._resolve_unsaved_edits():
            return False
        self._preview_timer.stop()
        if not self._closed_repository:
            self._repository.close()
            self._closed_repository = True
        return True

    def reject(self) -> None:
        """Make Escape follow the same dirty-draft and repository-close path."""

        if self._prepare_to_close():
            super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt API
        if not self._prepare_to_close():
            event.ignore()
            return
        event.accept()


def _manager_stylesheet() -> str:
    return f"""
    QDialog#SignatureTemplateManager {{
        background: {COLORS['canvas']};
        color: {COLORS['text']};
    }}
    #SignatureTemplateManager QLabel[managerTitle="true"] {{
        color: {COLORS['text']};
        font-size: 25px;
        font-weight: 700;
    }}
    #SignatureTemplateManager QLabel[managerEyebrow="true"] {{
        color: {COLORS['green']};
        font-family: Consolas, "Cascadia Mono", monospace;
        font-size: 10px;
        font-weight: 700;
    }}
    #SignatureTemplateManager QLabel[managerSection="true"] {{
        color: {COLORS['text']};
        font-size: 13px;
        font-weight: 700;
    }}
    #SignatureTemplateManager QLabel[managerMuted="true"] {{
        color: {COLORS['muted']};
    }}
    #SignatureTemplateManager QLabel[managerMeta="true"] {{
        color: {COLORS['muted_bright']};
        font-family: Consolas, "Cascadia Mono", monospace;
        font-size: 10px;
    }}
    #SignatureTemplateManager QLabel[managerFieldLabel="true"] {{
        color: {COLORS['muted_bright']};
        font-weight: 600;
    }}
    #SignatureTemplateManager QFrame[managerPanel="true"] {{
        background: {COLORS['window']};
        border: 1px solid {COLORS['line']};
        border-radius: {RADIUS_LG}px;
    }}
    #SignatureTemplateLibraryPanel {{
        background: {COLORS['window']};
        border-color: {COLORS['line']};
    }}
    #SignatureTemplateEditorHeader {{
        background: {COLORS['raised']};
        border-color: {COLORS['line_strong']};
    }}
    #SignatureIdentityFieldsPanel {{
        background: {COLORS['surface']};
        border: none;
    }}
    #SignatureIdentityAssetsPanel {{
        background: {COLORS['window']};
        border-color: {COLORS['line']};
    }}
    #SignatureLinkedPreviewsPanel {{
        background: {COLORS['raised']};
        border-color: {COLORS['line_strong']};
    }}
    #SignatureTemplateManager QFrame[assetEditor="true"],
    #SignatureTemplateManager QFrame[previewCard="true"] {{
        background: {COLORS['surface']};
        border: none;
        border-radius: {RADIUS_MD}px;
    }}
    #SignatureTemplateManager QFrame[managerNotice="true"] {{
        background: #24251f;
        border: 1px solid {COLORS['warning']};
        border-radius: {RADIUS_MD}px;
    }}
    #SignatureTemplateManager QLabel[assetThumb="true"],
    #SignatureTemplateManager QLabel[previewCanvas="true"] {{
        background: #0e100e;
        border: 1px solid {COLORS['line']};
        border-radius: {RADIUS_MD}px;
        color: {COLORS['muted']};
        padding: 4px;
    }}
    #SignatureTemplateManager QListWidget {{
        background: {COLORS['surface']};
        border: 1px solid {COLORS['line']};
        border-radius: {RADIUS_MD}px;
        padding: 5px;
    }}
    #SignatureTemplateManager QListWidget::item {{
        color: {COLORS['muted_bright']};
        min-height: 42px;
        padding: 5px 8px;
        margin: 1px;
        border-radius: {RADIUS_MD}px;
    }}
    #SignatureTemplateManager QListWidget::item:hover {{
        background: {COLORS['hover']};
        color: {COLORS['text']};
    }}
    #SignatureTemplateManager QListWidget::item:selected {{
        background: {COLORS['green_dim']};
        color: {COLORS['text']};
    }}
    #SignatureTemplateManager QLineEdit {{
        min-height: 32px;
        color: {COLORS['text']};
        background: {COLORS['surface']};
        border: 1px solid {COLORS['line']};
        border-radius: {RADIUS_MD}px;
        padding: 0 9px;
        selection-background-color: {COLORS['green_dim']};
    }}
    #SignatureTemplateManager QLineEdit:focus {{
        border-color: {COLORS['green']};
    }}
    #SignatureTemplateManager QCheckBox {{
        color: {COLORS['muted_bright']};
        spacing: 8px;
    }}
    #SignatureTemplateManager QPushButton,
    #SignatureTemplateManager QToolButton {{
        min-height: 30px;
        color: {COLORS['muted_bright']};
        background: {COLORS['surface']};
        border: 1px solid {COLORS['line']};
        border-radius: {RADIUS_MD}px;
        padding: 0 10px;
        font-weight: 600;
    }}
    #SignatureTemplateManager QPushButton:hover,
    #SignatureTemplateManager QToolButton:hover {{
        color: {COLORS['text']};
        border-color: {COLORS['line_strong']};
        background: {COLORS['hover']};
    }}
    #SignatureTemplateManager QPushButton[primary="true"] {{
        color: {COLORS['green_dark']};
        background: {COLORS['green']};
        border-color: {COLORS['green']};
    }}
    #SignatureTemplateManager QPushButton[primary="true"]:hover {{
        background: {COLORS['green_hover']};
    }}
    #SignatureTemplateManager QPushButton[destructive="true"] {{
        color: {COLORS['error']};
    }}
    #SignatureTemplateManager QPushButton:disabled,
    #SignatureTemplateManager QToolButton:disabled,
    #SignatureTemplateManager QLineEdit:disabled {{
        color: #65685f;
        background: {COLORS['window']};
        border-color: #30312b;
    }}
    #SignatureTemplateManager QLabel[managerStatus="error"] {{
        color: {COLORS['error']};
    }}
    #SignatureTemplateManager QLabel[managerStatus="ok"] {{
        color: {COLORS['green']};
    }}
    #SignatureBorderPolicyNote,
    #SignaturePreviewStatus,
    #SignatureNoAudioPreviewSeam {{
        color: {COLORS['muted_bright']};
        font-size: 11px;
    }}
    #SignatureTemplateManager QPushButton:focus,
    #SignatureTemplateManager QToolButton:focus,
    #SignatureTemplateManager QLineEdit:focus {{
        border-color: {COLORS['green']};
    }}
    #SignatureTemplateManager QSplitter::handle {{
        background: transparent;
        width: 10px;
    }}
    #SignatureTemplateManager QScrollArea,
    #SignatureTemplateManager QScrollArea > QWidget > QWidget {{
        background: transparent;
        border: none;
    }}
    #SignatureTemplateManager QScrollBar:vertical {{
        width: 10px;
        margin: 2px 0;
        background: transparent;
    }}
    #SignatureTemplateManager QScrollBar::handle:vertical {{
        min-height: 34px;
        background: {COLORS['line_strong']};
        border-radius: 4px;
    }}
    #SignatureTemplateManager QScrollBar::handle:vertical:hover {{
        background: {COLORS['muted']};
    }}
    #SignatureTemplateManager QScrollBar::add-line:vertical,
    #SignatureTemplateManager QScrollBar::sub-line:vertical,
    #SignatureTemplateManager QScrollBar::add-page:vertical,
    #SignatureTemplateManager QScrollBar::sub-page:vertical {{
        height: 0;
        background: transparent;
    }}
    """
