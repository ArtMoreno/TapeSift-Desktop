"""Shell V3 presentation for the settings and recovery dialog family."""

from __future__ import annotations

from datetime import datetime
from copy import deepcopy
from dataclasses import fields
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QDialog, QCheckBox,
    QFormLayout, QLineEdit, QTreeWidget, QTreeWidgetItem, QMessageBox, QStyledItemDelegate,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from tapesift.ui.dialog_components import DialogHeader
from tapesift.ui.settings_dialog import SettingsDialog
from tapesift.ui.project_settings_dialog import ProjectSettingsDialog
from tapesift.ui.tag_color_manager_dialog import TagColorManagerDialog
from tapesift.ui.result_manager_dialog import ResultManagerDialog, STANDARD_RESULT_CHOICES
from tapesift.ui.how_it_works_dialog import HowItWorksDialog, SECTIONS
from tapesift.ui.shortcuts_dialog import ShortcutsDialog
from tapesift.ui.dialog_components import DialogSection
from tapesift.services.tag_style_service import DEFAULT_STYLE, primary_timeline_tag
from tapesift.ui_v3.icons import tinted_icon, icon_path
from tapesift.ui_core.flow_layout import FlowLayout
from tapesift.ui_core.timeline import build_timeline_legend
from tapesift.ui_v2.icon_utils import tinted_standard_icon


class RecoveryAction(Enum):
    """Explicit result of the non-destructive recovery decision."""

    NONE = "none"
    DISCARD = "discard"
    READ_ONLY = "read_only"
    RESTORE = "restore"


def _support_size(dialog: QDialog) -> None:
    dialog.setProperty("supportDialog", "true")
    dialog.setMinimumSize(760, 520)
    screen = dialog.screen().availableGeometry()
    dialog.resize(min(1100, screen.width() - 40), min(730, screen.height() - 60))


def _support_label(text: str, role: str) -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setProperty("supportRole", role)
    label.setWordWrap(role not in ("chip", "stepTitle"))
    if role == "chip":
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return label


def _support_rule(title: str) -> QWidget:
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 10, 0, 6)
    row.addWidget(_support_label(title, "section"))
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setObjectName("V3SupportRule")
    row.addWidget(line, 1)
    return widget


def _support_nav_icon(name: str) -> QIcon:
    # An explicit SVG render avoids the native menu icon's small raster fallback.
    canvas = QPixmap(64, 64)
    canvas.setDevicePixelRatio(2)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    QSvgRenderer(str(icon_path(name))).render(painter, QRectF(0, 0, 32, 32))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(QRectF(0, 0, 32, 32), QColor("#b6c7bd"))
    painter.end()
    return QIcon(canvas)


def _support_owner(dialog):
    parent = dialog.parentWidget()
    while parent is not None:
        if parent.objectName() == "TapeSiftV3":
            return parent
        parent = parent.parentWidget()
    return None


def _support_shell(dialog, title: str, subtitle: str, active: str, *, pinned_preview=None) -> None:
    """Move the real dialog contents and footer into the standard side rail."""
    _support_size(dialog)
    outer = dialog.layout()
    footer = outer.takeAt(outer.count() - 1)
    content = QWidget()
    content.setObjectName("V3SupportContent")
    body = QVBoxLayout(content)
    body.setContentsMargins(24, 20, 24, 16)
    body.setSpacing(12)
    body.addWidget(_support_label(title, "title"))
    if subtitle:
        body.addWidget(_support_label(subtitle, "description"))
    while outer.count():
        item = outer.takeAt(0)
        if isinstance(item.widget(), DialogHeader):
            item.widget().hide()
        elif item.widget() is not None:
            body.addWidget(item.widget())
        elif item.layout() is not None:
            layout = item.layout()
            layout.setParent(None)
            body.addLayout(layout, 1 if active == "Manage results" else 0)
    nav = QListWidget()
    nav.setObjectName("V3SupportNav")
    nav.setAccessibleName("Settings and help navigation")
    nav.setFixedWidth(242)
    nav.setIconSize(QSize(32, 32))
    nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    owner = _support_owner(dialog)
    routes = [("General", "settings-24.svg", "_open_settings"),
              ("Project settings", "folder-open-24.svg", "_open_project_settings"),
              ("Timeline tag colors", "color-24.svg", "_open_v3_tag_colors"),
              ("Manage results", "text-number-list-ltr-24.svg", None),
              ("How TapeSift works", "question-circle-24.svg", "_show_how_it_works")]
    settings_sections = ("General", "Playback", "First Read", "Clip Defaults", "Export", "FFmpeg")
    nav.setProperty("compact", "true")
    routes[1:1] = [(name, icon, "_open_settings") for name, icon in zip(
        settings_sections[1:], ("play-24.svg", "target-24.svg", "cut-24.svg", "share-20.svg", "window-console-20.svg"))]
    for name, icon, command in routes:
        if name in ("General", "Project settings", "How TapeSift works"):
            nav.addItem({"General": "SETTINGS", "Project settings": "PROJECT", "How TapeSift works": "HELP"}[name])
            heading = nav.item(nav.count() - 1)
            heading.setFlags(Qt.ItemFlag.NoItemFlags)
            heading.setForeground(QColor("#7b9b89"))
        nav.addItem(name if name == active else "Open " + name + "…")
        item = nav.item(nav.count() - 1)
        item.setData(Qt.ItemDataRole.UserRole, name)
        item.setIcon(_support_nav_icon(icon))
        item.setToolTip("Opens a separate dialog. Save or cancel changes in each dialog separately.")
        if name == active:
            nav.setCurrentItem(item)
        elif owner is None or (name in ("Project settings", "Timeline tag colors") and owner.session is None):
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            item.setToolTip("Open a project to use this section.")
    def navigate(item):
        name = item.data(Qt.ItemDataRole.UserRole) or item.text()
        if name == active or owner is None:
            return
        if name == "Timeline tag colors" and isinstance(dialog, ProjectSettingsDialog):
            dialog._edit_tag_styles()
        elif name == "Manage results":
            owner.clip_editor._manage_results()
        else:
            command = next(command for value, _icon, command in routes if value == name)
            if command == "_open_settings":
                owner._open_settings(settings_sections.index(name))
            else:
                getattr(owner, command)()
        nav.setCurrentItem(next(nav.item(i) for i in range(nav.count()) if nav.item(i).data(Qt.ItemDataRole.UserRole) == active))
    nav.itemClicked.connect(navigate)
    nav.itemActivated.connect(navigate)
    scroll = QScrollArea()
    scroll.setObjectName("V3SupportBodyScroll")
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    row = QHBoxLayout()
    row.setSpacing(0)
    row.addWidget(nav)
    if pinned_preview is None:
        row.addWidget(scroll, 1)
    else:
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        right.addWidget(scroll, 1)
        right.addWidget(pinned_preview)
        row.addLayout(right, 1)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)
    outer.addLayout(row, 1)
    footer_widget = QWidget()
    footer_widget.setObjectName("V3SupportFooter")
    footer_layout = QHBoxLayout(footer_widget)
    footer_layout.setContentsMargins(20, 14, 20, 14)
    if footer.widget() is not None:
        footer_layout.addStretch(1)
        footer_layout.addWidget(footer.widget())
    elif footer.layout() is not None:
        footer.layout().setParent(None)
        footer_layout.addLayout(footer.layout())
    outer.addWidget(footer_widget)
    for buttons in dialog.findChildren(QDialogButtonBox):
        buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        buttons.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        for standard in (QDialogButtonBox.StandardButton.Ok, QDialogButtonBox.StandardButton.Save):
            save = buttons.button(standard)
            if save is not None:
                save.setProperty("primary", "true")
                save.style().unpolish(save)
                save.style().polish(save)
    dialog.support_nav, dialog.support_scroll = nav, scroll
    dialog.support_preview, dialog.support_footer = pinned_preview, footer_widget


class SettingsDialogV3(SettingsDialog):
    """The six existing pages, with a draft shared by their child dialogs."""

    def __init__(self, settings, parent=None) -> None:
        self._settings_target = settings
        draft = deepcopy(settings)
        self._settings_baseline = {field.name: deepcopy(getattr(draft, field.name)) for field in fields(draft)}
        # Child editors may call save; only the outer Save commits this draft.
        draft.save = lambda: None
        super().__init__(draft, parent)
        self.setObjectName("V3SettingsDialog")
        self.setProperty("shellV3Dialog", "true")
        _support_size(self)
        header = self.findChild(DialogHeader)
        if header is not None:
            header.hide()
        outer = self.layout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        tabs = self.findChild(QTabWidget, "SettingsTabs")
        body = QFrame()
        body.setObjectName("V3SettingsBody")
        row = QHBoxLayout(body)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        nav = QListWidget()
        nav.setObjectName("V3SettingsNav")
        nav.setAccessibleName("Settings sections")
        nav.setFixedWidth(286)
        nav.setIconSize(QSize(32, 32))
        nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        row.addWidget(nav)
        stack = QStackedWidget()
        stack.setObjectName("V3SettingsStack")
        row.addWidget(stack, 1)
        descriptions = {
            "General": "Workspace and saving preferences.",
            "Playback": "Playback defaults and precision review preferences.",
            "First Read": "Optional assistance from your configured vision provider.",
            "Clip Defaults": "Defaults for new clips and the Clip Details panel.",
            "Export": "Formats, filenames and output organization.",
            "FFmpeg": "The local media tools used by TapeSift."}
        icons = ("table-20.svg", "filmstrip-play-20.svg", "document-pdf-20.svg", "pencil-24.svg", "share-20.svg", "code-20.svg")
        while tabs.count():
            title, page = tabs.tabText(0), tabs.widget(0)
            tabs.removeTab(0)
            layout = page.layout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(16)
            if isinstance(layout, QFormLayout):
                layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                layout.insertRow(0, _support_rule("Workspace" if title == "General" else title))
                if title == "General":
                    layout.insertRow(3, _support_rule("Saving"))
                    explanations = {
                        "Default project folder:": "Where new projects are saved by default.",
                        "Default output folder:": "Where exports are saved by default.",
                        "Autosave interval:": "How often projects are autosaved.",
                        "Recent projects kept:": "Number of recent projects kept in the list.",
                        "Confirm before delete:": "Ask before deleting clips."}
                    for form_row in range(layout.rowCount()):
                        item = layout.itemAt(form_row, QFormLayout.ItemRole.LabelRole)
                        if item is None or not isinstance(item.widget(), QLabel):
                            continue
                        label = item.widget()
                        text = label.text()
                        if text not in explanations:
                            continue
                        label.hide()
                        layout.removeWidget(label)
                        help_column = QWidget()
                        help_column.setMinimumWidth(282)
                        copy = QVBoxLayout(help_column)
                        copy.setContentsMargins(0, 6, 10, 6)
                        copy.setSpacing(5)
                        copy.addWidget(_support_label(text.rstrip(":"), "fieldLabel"))
                        copy.addWidget(_support_label(explanations[text], "description"))
                        layout.setWidget(form_row, QFormLayout.ItemRole.LabelRole, help_column)
                    layout.setVerticalSpacing(22)
                    self.confirm_delete_check.setText("Ask for confirmation before deleting.")
            wrap = QWidget()
            wrapper = QVBoxLayout(wrap)
            wrapper.setContentsMargins(26, 22, 26, 24)
            wrapper.setSpacing(18)
            wrapper.addWidget(_support_label(title, "title"))
            wrapper.addWidget(_support_label(descriptions[title], "description"))
            wrapper.addWidget(page)
            page.show()
            wrapper.addStretch(1)
            page.setMinimumSize(0, 0)
            scroll = QScrollArea()
            scroll.setObjectName("V3SettingsPageScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setWidget(wrap)
            stack.addWidget(scroll)
            nav.addItem(title)
            nav.item(nav.count() - 1).setIcon(_support_nav_icon(icons[nav.count() - 1]))
        index = outer.indexOf(tabs)
        outer.removeWidget(tabs)
        tabs.setParent(None)
        tabs.deleteLater()
        outer.insertWidget(max(0, index), body, 1)
        nav.currentRowChanged.connect(stack.setCurrentIndex)
        nav.setCurrentRow(0)
        self.section_nav, self.section_stack = nav, stack
        buttons = self.findChild(QDialogButtonBox)
        outer.removeWidget(buttons)
        footer = QWidget()
        footer.setObjectName("V3SupportFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(20, 14, 20, 14)
        footer_layout.addStretch(1)
        buttons.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        footer_layout.addWidget(buttons)
        outer.addWidget(footer)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        save = buttons.button(QDialogButtonBox.StandardButton.Ok)
        save.setObjectName("V3SettingsSave")
        save.setAutoDefault(True)
        save.setDefault(True)

    def _playback_tab(self):
        from tapesift.ui_v2.attribute_grid import REVIEW_ROWS
        page = super()._playback_tab()
        page.layout().addRow(_support_rule("Tag Map rows"))
        hint = QLabel("Choose rows to show. Hiding rows gives space to video and keeps saved tags.")
        hint.setWordWrap(True)
        page.layout().addRow(hint)
        rows = QWidget()
        layout = QGridLayout(rows)
        layout.setContentsMargins(0, 0, 0, 0)
        hidden = self.settings.hidden_tag_map_rows
        hidden = hidden if isinstance(hidden, list) else []
        self.tag_map_row_checks = {}
        for index, row in enumerate(REVIEW_ROWS):
            check = QCheckBox(row.label.replace("&", "&&"))
            check.setChecked(row.key not in hidden)
            check.setObjectName("TagMapRow_" + row.key)
            self.tag_map_row_checks[row.key] = check
            layout.addWidget(check, index // 2, index % 2)
        page.layout().addRow(rows)
        return page

    def accept(self) -> None:
        self.settings.hidden_tag_map_rows = [key for key, check in self.tag_map_row_checks.items()
                                            if not check.isChecked()]
        changed = {name: deepcopy(getattr(self.settings, name))
                   for name, value in self._settings_baseline.items()
                   if getattr(self.settings, name) != value}
        candidate = deepcopy(self._settings_target)
        for name, value in changed.items():
            setattr(candidate, name, value)
        try:
            candidate.save()
        except Exception as exc:
            QMessageBox.critical(self, "Settings were not saved", str(exc))
            return
        for name, value in changed.items():
            setattr(self._settings_target, name, deepcopy(value))
        QDialog.accept(self)


class RecoveryDialogV3(QDialog):
    """Show the three standard recovery choices without consuming the marker."""

    def __init__(self, db_path: str | Path, project_name: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("V3RecoveryDialog")
        self.setProperty("tapesiftDialog", True)
        self.setProperty("shellV3Dialog", "true")
        self.setWindowTitle("Recover Project")
        self.setMinimumSize(662, 406)
        self.resize(662, 406)
        self.selected_action = RecoveryAction.NONE

        path = Path(db_path)
        name = project_name or path.stem
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            modified_text = modified.strftime("%b %d, %Y at %I:%M %p")
        except OSError:
            modified_text = "Unknown"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(18)
        outer.addWidget(DialogHeader(
            "Recovered Project Found",
            "TapeSift found a project from a session that did not close "
            "cleanly. Inspect it safely, discard the recovery notice, or "
            "restore it to continue working.",
            eyebrow="TAPESIFT  /  RECOVERY",
            badge="UNSAVED SESSION",
            parent=self,
        ))

        summary = QFrame()
        summary.setObjectName("V3RecoverySummary")
        summary_row = QHBoxLayout(summary)
        summary_row.setContentsMargins(20, 18, 20, 18)
        summary_row.setSpacing(18)
        icon = QLabel()
        icon.setObjectName("V3RecoveryInfoIcon")
        icon.setPixmap(tinted_standard_icon(
            self,
            QStyle.StandardPixmap.SP_MessageBoxInformation,
            colour="#39e07a",
            size=QSize(38, 38),
        ).pixmap(38, 38))
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        summary_row.addWidget(icon)

        facts = QGridLayout()
        facts.setHorizontalSpacing(18)
        facts.setVerticalSpacing(11)
        for row, (label, value) in enumerate((
            ("Project", name),
            ("Last modified", modified_text),
            ("Location", str(path)),
        )):
            key = QLabel(label)
            key.setProperty("role", "recoveryKey")
            facts.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop)
            detail = QLabel(value)
            detail.setProperty("role", "recoveryValue")
            detail.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse)
            detail.setWordWrap(True)
            detail.setMinimumWidth(0)
            detail.setSizePolicy(
                QSizePolicy.Policy.Ignored,
                QSizePolicy.Policy.Preferred,
            )
            facts.addWidget(detail, row, 1)
        facts.setColumnStretch(1, 1)
        summary_row.addLayout(facts, 1)
        outer.addWidget(summary, 1)

        actions = QHBoxLayout()
        actions.setSpacing(14)
        discard = QPushButton("Discard Recovery")
        discard.setObjectName("V3RecoveryDiscard")
        discard.setProperty("destructive", "true")
        discard.setAutoDefault(False)
        discard.clicked.connect(
            lambda: self._choose(RecoveryAction.DISCARD))
        read_only = QPushButton("Open Read-Only")
        read_only.setObjectName("V3RecoveryReadOnly")
        read_only.setAutoDefault(False)
        read_only.clicked.connect(
            lambda: self._choose(RecoveryAction.READ_ONLY))
        restore = QPushButton("Restore Project")
        restore.setObjectName("V3RecoveryRestore")
        restore.setProperty("primary", "true")
        restore.setAutoDefault(True)
        restore.setDefault(True)
        restore.clicked.connect(
            lambda: self._choose(RecoveryAction.RESTORE))
        actions.addWidget(discard, 1)
        actions.addWidget(read_only, 1)
        actions.addWidget(restore, 1)
        outer.addLayout(actions)

        self.discard_button = discard
        self.read_only_button = read_only
        self.restore_button = restore

    def _choose(self, action: RecoveryAction) -> None:
        self.selected_action = action
        self.accept()


def _support_action_icon(name: str) -> QIcon:
    assets = {
        "grip": "re-order-dots-vertical-24.svg", "trash": "delete-24.svg",
        "left": "arrow-left-24.svg", "right": "arrow-right-24.svg",
        "up": "arrow-up-24.svg", "down": "arrow-down-24.svg",
    }
    return _support_nav_icon(assets[name])


def _support_search_icon(edit: QLineEdit) -> None:
    edit.addAction(_support_nav_icon("search-16.svg"), QLineEdit.ActionPosition.LeadingPosition)
    # Embedded line-edit actions cannot inherit the full-size button padding.
    for button in edit.findChildren(QToolButton):
        button.setStyleSheet("QToolButton { padding: 0; margin: 0; border: none; "
                            "min-height: 0; min-width: 0; background: transparent; }")


def _support_preview_panel() -> tuple[QWidget, QVBoxLayout]:
    panel = QWidget()
    panel.setObjectName("V3PinnedSupportPreview")
    panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    panel.setStyleSheet("QWidget#V3PinnedSupportPreview { background: #0a0f10; border: none; border-top: 1px solid #29342f; }")
    layout = QVBoxLayout(panel)
    layout.setContentsMargins(24, 10, 24, 16)
    layout.setSpacing(8)
    return panel, layout


class _TagOrderHeader(QHeaderView):
    def __init__(self, parent) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self.setSectionsMovable(True)
        self.setSectionsClickable(True)
        self.setFixedWidth(26)
        self.setDefaultSectionSize(32)
        self.setMinimumSectionSize(32)
        self.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Drag to reorder this list. Clip tags and primary color priority stay unchanged.")
        self.setAccessibleName("Tag style display order; drag rows to reorder")
        self._grip = _support_action_icon("grip")

    def paintSection(self, painter, rect, logical_index) -> None:
        painter.fillRect(rect, QColor("#0b100d"))
        painter.setPen(QColor("#243a2d"))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        self._grip.paint(painter, rect.adjusted(3, 5, -3, -5))


class TagColorManagerDialogV3(TagColorManagerDialog):
    """The existing staged style controls with their actual timeline preview."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setObjectName("V3TagColorsDialog")
        self.table.setObjectName("V3TagStylesTable")
        self.table.setStyleSheet(
            "QTableWidget#V3TagStylesTable::item:selected { color: #edf3ef; "
            "background: #111917; border: none; border-top: 1px solid #344b3e; "
            "border-bottom: 1px solid #344b3e; }")
        self.table.setMinimumHeight(min(340, 32 * (self.table.rowCount() + 1)))
        self.table.setVerticalHeader(_TagOrderHeader(self.table))
        self.table.verticalHeader().show()
        self._remember_order = bool(self._original_styles)
        self._set_display_order(list(self._original_styles))
        for row in range(self.table.rowCount()):
            self.table.setRowHeight(row, 32)
        self.preview = QWidget()
        self.preview.setObjectName("V3TagStylePreview")
        self.preview_flow = FlowLayout(self.preview)
        self.preview_labels = {}
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setObjectName("V3TagPreviewScroll")
        self.preview_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.preview_scroll.setStyleSheet("QScrollArea#V3TagPreviewScroll { background: transparent; border: none; }")
        self.preview_scroll.setWidget(self.preview)
        self.preview_scroll.viewport().installEventFilter(self)
        _support_search_icon(self.filter_edit)
        restore = next(button for button in self.findChildren(QPushButton) if button.text() == "Restore Defaults")
        restore.setIcon(_support_nav_icon("arrow-reset-24.svg"))
        restore.setIconSize(QSize(18, 18))
        preview_panel, preview_layout = _support_preview_panel()
        reassurance = QWidget()
        reassurance_row = QHBoxLayout(reassurance)
        reassurance_row.setContentsMargins(0, 0, 0, 0)
        reassurance_row.setSpacing(8)
        info = QLabel()
        info.setPixmap(_support_nav_icon("info-16.svg").pixmap(QSize(16, 16)))
        reassurance_row.addWidget(info)
        self.style_reassurance = _support_label(
            "These styling settings do not remove tags from clips, search, or exports.", "description")
        reassurance_row.addWidget(self.style_reassurance, 1)
        preview_layout.addWidget(reassurance)
        preview_layout.addWidget(_support_label("Timeline preview", "section"))
        preview_layout.addWidget(self.preview_scroll)
        outer = self.layout()
        ordering = QHBoxLayout()
        ordering.addWidget(_support_label("Drag to reorder this list · Alt+↑ / Alt+↓", "description"), 1)
        self.move_style_up = QPushButton()
        self.move_style_down = QPushButton()
        for button, delta, icon, label in (
                (self.move_style_up, -1, "up", "Move selected tag up"),
                (self.move_style_down, 1, "down", "Move selected tag down")):
            button.setIcon(_support_action_icon(icon))
            button.setIconSize(QSize(18, 18))
            button.setFixedSize(30, 28)
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.clicked.connect(lambda _checked=False, step=delta: self._move_style(step))
            ordering.addWidget(button)
        outer.insertLayout(outer.indexOf(self.table), ordering)
        for key, row in self._rows.items():
            row["category"].setPlaceholderText("Category")
            label = _support_label(str(row["tag"]), "chip")
            label.setMaximumWidth(190)
            self.preview_flow.addWidget(label)
            self.preview_labels[key] = label
            row["category"].textChanged.connect(self._update_style_preview)
            row["primary"].toggled.connect(self._update_style_preview)
            row["timeline"].toggled.connect(self._update_style_preview)
        self.table.verticalHeader().sectionMoved.connect(self._style_moved)
        self.table.itemSelectionChanged.connect(self._update_order_controls)
        self.filter_edit.textChanged.connect(self._update_order_controls)
        from PySide6.QtGui import QShortcut, QKeySequence
        for key, delta in (("Alt+Up", -1), ("Alt+Down", 1)):
            shortcut = QShortcut(QKeySequence(key), self.table)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda step=delta: self._move_style(step))
        _support_shell(self, "Timeline tag colors",
                       "Control which tags lead the Primary Tag view and how they read on the timeline.",
                       "Timeline tag colors", pinned_preview=preview_panel)
        self._reorder_preview()
        self._update_order_controls()
        self._update_style_preview()

    def eventFilter(self, watched, event) -> bool:
        if (hasattr(self, "preview_scroll") and watched is self.preview_scroll.viewport()
                and event.type() == QEvent.Type.Resize):
            self._fit_style_preview()
        return super().eventFilter(watched, event)

    def _fit_style_preview(self) -> None:
        # A large project must not let its chip preview displace the editors.
        height = self.preview_flow.heightForWidth(self.preview_scroll.viewport().width())
        self.preview.setMinimumHeight(height)
        self.preview_scroll.setFixedHeight(min(144, height))

    def _display_keys(self) -> list[str]:
        header = self.table.verticalHeader()
        return [str(self.table.item(header.logicalIndex(index), 0).data(Qt.ItemDataRole.UserRole))
                for index in range(header.count())]

    def _set_display_order(self, ordered_keys: list[str]) -> None:
        header = self.table.verticalHeader()
        keys = [key for key in ordered_keys if key in self._rows]
        keys.extend(key for key in sorted(self._rows, key=lambda key: str(self._rows[key]["tag"]).casefold())
                    if key not in keys)
        blocked = header.blockSignals(True)
        for visual, key in enumerate(keys):
            header.moveSection(header.visualIndex(self._rows[key]["row"]), visual)
        header.blockSignals(blocked)

    def _visible_style_rows(self) -> list[int]:
        header = self.table.verticalHeader()
        return [header.logicalIndex(index) for index in range(header.count())
                if not self.table.isRowHidden(header.logicalIndex(index))]

    def _move_style(self, delta: int) -> None:
        rows = self._visible_style_rows()
        selected = self.table.currentRow()
        if selected not in rows:
            return
        index = rows.index(selected)
        if 0 <= index + delta < len(rows):
            header = self.table.verticalHeader()
            header.moveSection(header.visualIndex(selected), header.visualIndex(rows[index + delta]))
            self.table.scrollToItem(self.table.item(selected, 0))

    def _style_moved(self, *_args) -> None:
        self._remember_order = True
        self._reorder_preview()
        self._update_order_controls()

    def _reorder_preview(self) -> None:
        while self.preview_flow.count():
            self.preview_flow.takeAt(0)
        for key in self._display_keys():
            self.preview_flow.addWidget(self.preview_labels[key])
        self._fit_style_preview()
        self.preview_flow.invalidate()

    def _update_order_controls(self, *_args) -> None:
        rows = self._visible_style_rows()
        current = self.table.currentRow()
        index = rows.index(current) if current in rows else -1
        self.move_style_up.setEnabled(index > 0)
        self.move_style_down.setEnabled(0 <= index < len(rows) - 1)

    def tag_styles(self) -> dict[str, dict[str, object]]:
        styles = super().tag_styles()
        if not self._remember_order:
            return styles
        # JSON and style normalization retain mapping order. Explicit defaults
        # keep unmodified rows in that order through the existing project Save.
        ordered = {key: styles.get(key, dict(DEFAULT_STYLE)) for key in self._display_keys()}
        ordered.update((key, value) for key, value in styles.items() if key not in ordered)
        return ordered

    @staticmethod
    def _set_color_button(button, color: str) -> None:
        TagColorManagerDialog._set_color_button(button, color)
        button.setObjectName("V3TagSwatch")
        button.setFixedSize(64, 32)
        button.setText("")
        button.setToolTip(color.upper() if color else "Automatic timeline color")

    def _choose_color(self, key: str) -> None:
        super()._choose_color(key)
        self._update_style_preview()

    def _restore_defaults(self) -> None:
        super()._restore_defaults()
        self._remember_order = False
        self._set_display_order([])
        self._reorder_preview()
        self._update_order_controls()
        self._update_style_preview()

    def _update_style_preview(self, *_args) -> None:
        styles = self.tag_styles()
        values = {key: primary_timeline_tag([str(row["tag"])], styles) for key, row in self._rows.items()}
        categories = {key: title for key, title, _color in values.values() if key}
        overrides = {key: color for key, _title, color in values.values() if key and color}
        palette = {key: color.name() for key, _title, color in build_timeline_legend("primary_tag", categories, overrides)}
        for key, label in self.preview_labels.items():
            timeline_key, title, _color = values[key]
            label.setText(str(self._rows[key]["tag"]))
            color = palette.get(timeline_key, "#59645e")
            label.setStyleSheet(f"border-left: 3px solid {color}; color: {'#dce7df' if timeline_key else '#839188'};")
            label.setToolTip(f"Primary timeline color · Category: {title}" if timeline_key else
                             "Secondary or hidden: retained on clips, excluded from Primary Tag coloring")
            label.setProperty("primaryTimeline", bool(timeline_key))
            button = self._rows[key]["color"]
            if not button.property("tagColor"):
                button.setStyleSheet(f"background-color: {color}; border: 1px solid {color};")
                button.setToolTip(f"Automatic timeline color: {color}. Click to choose an override.")
        self._fit_style_preview()


class ProjectSettingsDialogV3(ProjectSettingsDialog):
    tag_color_dialog_class = TagColorManagerDialogV3

    def __init__(self, project, known_tags=None, parent=None) -> None:
        super().__init__(project, known_tags, parent)
        self.setObjectName("V3ProjectSettingsDialog")
        periods = self.q2_edit.parentWidget()
        old = periods.findChild(QFormLayout)
        if old is not None:
            index = periods.layout().indexOf(old)
            while old.count():
                item = old.takeAt(0)
                if item.widget() is not None:
                    item.widget().hide()
            periods.layout().removeItem(old)
            old.deleteLater()
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(10)
            self.q1_fixed = QLineEdit("00:00 (fixed)")
            self.q1_fixed.setReadOnly(True)
            self.q1_fixed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            for title, edit, row, col in (("Q1 starts", self.q1_fixed, 0, 0), ("Q2 starts", self.q2_edit, 1, 0),
                                          ("Q3 starts", self.q3_edit, 0, 2), ("Q4 starts", self.q4_edit, 1, 2)):
                label = QLabel(title)
                label.setBuddy(edit)
                grid.addWidget(label, row, col)
                grid.addWidget(edit, row, col + 1)
                edit.show()
            grid.addWidget(QLabel("Overtime starts"), 2, 0)
            grid.addWidget(self.overtime_edit, 2, 1, 1, 3)
            self.overtime_edit.show()
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(3, 1)
            periods.layout().insertLayout(index, grid)
        for number, section in enumerate(self.findChildren(DialogSection), 1):
            label = section.findChild(QLabel)
            if label is not None:
                label.setText(f"{number}. {label.text()}")
        self.manage_tag_styles_btn.setText("Manage tag colors…")
        self.manage_tag_styles_btn.setIcon(tinted_icon("pencil-24.svg", "#dce7df", 18))
        _support_shell(self, "Project settings", f"Settings for this film · {project.name}", "Project settings")


class HowItWorksDialogV3(HowItWorksDialog):
    def __init__(self, parent=None, section: int = 0) -> None:
        super().__init__(parent, section)
        self.setObjectName("V3HowItWorksDialog")
        _support_size(self)
        self.contents.setObjectName("V3HelpNav")
        self.contents.setIconSize(QSize(32, 32))
        self.contents.setFixedWidth(280)
        self.contents.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.contents.setAccessibleName("Help topics")
        icons = ("document-pdf-20.svg", "filmstrip-play-20.svg", "pencil-24.svg", "scan-object-20.svg", "table-20.svg", "book-open-24.svg", "share-20.svg", "folder-open-24.svg")
        for index, icon in enumerate(icons):
            self.contents.item(index).setIcon(_support_nav_icon(icon))
        body = self.layout().itemAt(0).layout()
        body.setSpacing(0)
        body.removeWidget(self.text)
        self.help_pages = QStackedWidget()
        body.addWidget(self.help_pages, 1)
        overview = QWidget()
        overview.setObjectName("V3HelpOverview")
        overview_layout = QVBoxLayout(overview)
        overview_layout.setContentsMargins(26, 22, 26, 22)
        overview_layout.setSpacing(14)
        overview_layout.addWidget(_support_label("From game film to useful clips", "title"))
        overview_layout.addWidget(_support_label("TapeSift turns a long game film into named, searchable clips.", "description"))
        steps = (
            ("Open a film", "Start a project and point it at one film."),
            ("Detect or mark clips", "Detect Plays (Beta) suggests plays, or mark them by hand with I and O."),
            ("Log the play", "Log each play — who, what down, what happened."),
            ("Find it in Library", "Search across every game you have broken down."),
            ("Export clips or a reel", "Export single clips or a combined reel."))
        for number, (title, description) in enumerate(steps, 1):
            step = QFrame()
            step.setObjectName("V3HelpStep")
            row = QHBoxLayout(step)
            row.setContentsMargins(0, 6, 0, 11)
            row.setSpacing(16)
            badge = _support_label(str(number), "stepNumber")
            badge.setFixedSize(28, 28)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            copy = QVBoxLayout()
            copy.setSpacing(6)
            copy.addWidget(_support_label(title, "stepTitle"))
            copy.addWidget(_support_label(description, "description"))
            row.addLayout(copy, 1)
            overview_layout.addWidget(step)
        overview_layout.addWidget(_support_label(
            "Editing, playback and detection run locally. Optional First Read sends selected frame contact sheets to your configured provider when you run it.", "description"))
        self.shortcuts_button = QPushButton("Keyboard shortcuts")
        self.shortcuts_button.clicked.connect(lambda: ShortcutsDialog(self).exec())
        quick = QHBoxLayout()
        quick.addWidget(_support_label("Keyboard quick reference", "stepTitle"))
        quick.addStretch(1)
        quick.addWidget(self.shortcuts_button)
        overview_layout.addLayout(quick)
        key_row = QWidget()
        key_flow = FlowLayout(key_row)
        for key, action in (("J", "Reverse"), ("K", "Stop"), ("L", "Forward"), ("I", "Mark in"), ("O", "Mark out"), ("A", "Name / log clip")):
            entry = QWidget()
            row = QHBoxLayout(entry)
            row.setContentsMargins(0, 3, 10, 3)
            cap = _support_label(key, "keycap")
            cap.setFixedSize(34, 36)
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(cap)
            row.addWidget(_support_label(action, "description"))
            key_flow.addWidget(entry)
        overview_layout.addWidget(key_row)
        overview_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(overview)
        self.help_pages.addWidget(scroll)
        self.help_pages.addWidget(self.text)
        self.text.setObjectName("V3HelpText")
        self.text.document().setDefaultStyleSheet("body { color:#dce7df; font-family:'Segoe UI'; font-size:12pt; } h2,h3 { color:#eef5f0; } li { margin-bottom:18px; } p { margin-bottom:16px; } b { color:#edf5ef; }")
        self.layout().setContentsMargins(0, 0, 0, 0)
        buttons = self.findChild(QDialogButtonBox)
        self.layout().removeWidget(buttons)
        footer = QWidget()
        footer.setObjectName("V3SupportFooter")
        row = QHBoxLayout(footer)
        row.setContentsMargins(20, 14, 20, 14)
        row.addStretch(1)
        row.addWidget(buttons)
        self.layout().addWidget(footer)
        self._show_section(self.contents.currentRow())

    def _show_section(self, row: int) -> None:
        if not 0 <= row < len(SECTIONS):
            return
        content = SECTIONS[row][1]
        if hasattr(self, "help_pages"):
            self.help_pages.setCurrentIndex(0 if row == 0 else 1)
        if row == 0:
            content = content.replace("<h3>The short version</h3>", "<h2>From game film to useful clips</h2>")
            content += "<h3>Keyboard quick reference</h3><p><b>J</b> Reverse &nbsp; <b>K</b> Stop &nbsp; <b>L</b> Forward &nbsp; <b>I</b> Mark in &nbsp; <b>O</b> Mark out &nbsp; <b>A</b> Name / log clip</p>"
        elif row == 1:
            content += "<p><b>Timeline:</b> Shift+Up zooms in; Shift+Down zooms out; Shift+F fits the selected play. Ctrl+0 shows the full timeline.</p>"
            content += "<p><b>Tag Map:</b> select a clip, then Shift+E to enter the map. J/K move down/up through visible rows; Enter edits the cell. Esc closes a picker, then returns to playback. W/B save valid edits before moving to the next/previous clip outside text fields.</p>"
        elif row == 2:
            content += "<p><b>M</b> merges selected clips. <b>Ctrl+M</b> copies previous clip details.</p>"
        elif row == 4:
            content += "<p><b>Keyboard logging:</b> W/B save valid edits and move to the next/previous clip outside text fields. E focuses Clip Details; Shift+E enters the Tag Map. Ctrl+Shift+Enter saves and advances while typing. Esc returns to playback.</p>"
            content += "<p><b>Scoreboard photo.</b> Capture the actual board frame for the selected clip, then enter the situation beside it. The mini field reflects your entries. Capturing or replacing the photo does not read or change those values; automatic scoreboard reading is deferred.</p>"
        elif row == 6:
            content += "<p><b>Export &gt; Current clip</b> opens a preview with Video only, Video + summary (a two-second card before the film), and Field summary PNG. Selected clips opens package setup; Player / group cutups opens the cutup builder. Review these options before starting an export.</p>"
        self.text.setHtml(content)
        self.text.verticalScrollBar().setValue(0)


class _FavoriteRowDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        # The row widget paints the label; keep DisplayRole for commands and accessibility.
        option.text = ""


class ResultManagerDialogV3(ResultManagerDialog):
    """Grouped presentation of the existing vocabulary and ordered favorites."""

    _GROUPS = (
        ("Scoring", ("TD", "Touchdown", "Safety", "Field Goal Good", "Extra Point Good", "2-Point Good")),
        ("Possession", ("INT", "Interception", "Fumble", "Fumble Lost", "Fumble Recovered", "Turnover on Downs")),
        ("Down and yardage", ("No Gain", "First Down", "Gain", "Loss", "TFL", "Explosive", "3rd Down Conversion", "4th Down Conversion")),
        ("Passing", ("Reception", "Completion", "Incomplete", "Incompletion", "Drop", "Throwaway", "Batted Pass")),
        ("Pressure", ("Sack",)), ("Penalties", ("Penalty",)),
        ("Special teams", ("Field Goal Missed", "Field Goal Blocked", "Extra Point Missed", "Extra Point Blocked", "2-Point Failed", "Punt", "Kickoff")),
        ("Other", ("Kneel", "Spike", "Out of Bounds", "No Play")),
    )

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.setObjectName("V3ResultManagerDialog")
        self._refresh_pending = False
        outer = self.layout()
        intro = outer.takeAt(0).widget()
        intro.hide()
        columns = outer.itemAt(0).layout()
        favorites = columns.itemAt(0).layout()
        library = columns.itemAt(1).layout()
        self.favorite_count = favorites.itemAt(0).widget()
        buttons = {button.text(): button for button in self.findChildren(QPushButton)}
        self.result_action_buttons = buttons
        center = QVBoxLayout()
        center.addStretch(1)
        for text, caption, glyph in (
                ("Add to fast results", "Add to fast results", "left"),
                ("Remove from fast results", "Remove from fast", "right"),
                ("Rename custom result", "Rename custom", "pencil"),
                ("Remove custom result", "Delete custom", "trash")):
            button = buttons[text]
            button.setText(caption)
            button.setIcon(_support_nav_icon("pencil-24.svg")
                           if glyph == "pencil" else _support_action_icon(glyph))
            button.setIconSize(QSize(18, 18))
            button.setAccessibleName(text)
            button.setToolTip(text)
            button.setAutoDefault(False)
            center.addWidget(button)
        center.addStretch(1)
        columns.insertLayout(1, center)
        columns.setStretch(0, 4)
        columns.setStretch(1, 2)
        columns.setStretch(2, 4)
        self.favorite_list.setItemDelegate(_FavoriteRowDelegate(self.favorite_list))
        self.favorite_list.setMinimumHeight(230)
        favorites.addWidget(_support_label("Drag to reorder · Alt+↑ / Alt+↓", "description"))
        buttons["Restore standard results"].setIcon(_support_nav_icon("arrow-reset-24.svg"))
        buttons["Restore standard results"].setIconSize(QSize(18, 18))
        favorites.addWidget(buttons["Restore standard results"])
        custom = outer.takeAt(1).layout()
        custom.setParent(None)
        library.addLayout(custom)
        reset = outer.takeAt(1).layout()
        reset.deleteLater()
        self.result_list.hide()
        self.result_filter = QLineEdit()
        self.result_filter.setPlaceholderText("Search results")
        self.result_filter.setClearButtonEnabled(True)
        self.result_filter.setAccessibleName("Search the result vocabulary")
        _support_search_icon(self.result_filter)
        self.result_tree = QTreeWidget()
        self.result_tree.setObjectName("V3ResultLibrary")
        self.result_tree.setColumnCount(2)
        self.result_tree.setHeaderHidden(True)
        self.result_tree.header().setStretchLastSection(False)
        self.result_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.result_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.result_tree.setColumnWidth(1, 30)
        self.result_tree.setRootIsDecorated(True)
        self.result_tree.setIndentation(12)
        self.result_tree.setMinimumHeight(240)
        library.insertWidget(1, self.result_filter)
        library.insertWidget(2, self.result_tree, 1)
        self.result_tree.currentItemChanged.connect(self._select_result)
        self.favorite_list.currentRowChanged.connect(self._update_result_actions)
        self.result_tree.itemDoubleClicked.connect(lambda item, _column: self._add_selected_favorite() if item.data(0, Qt.ItemDataRole.UserRole) else None)
        self.result_filter.textChanged.connect(self._filter_results)
        self.preview_caption = _support_label("PREVIEW", "section")
        self.preview = QWidget()
        self.preview_flow = FlowLayout(self.preview)
        preview_panel, preview_layout = _support_preview_panel()
        preview_layout.addWidget(self.preview_caption)
        preview_layout.addWidget(self.preview)
        for model in (self.result_list.model(), self.favorite_list.model()):
            for signal in (model.rowsInserted, model.rowsRemoved, model.rowsMoved, model.dataChanged):
                signal.connect(self._queue_refresh)
        from PySide6.QtGui import QShortcut, QKeySequence
        for key, delta in (("Alt+Up", -1), ("Alt+Down", 1)):
            shortcut = QShortcut(QKeySequence(key), self.favorite_list)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda step=delta: self._move_favorite(step))
        _support_shell(self, "Manage results", "Keep up to six results within easy reach. Editing choices does not change results already logged on clips.",
                       "Manage results", pinned_preview=preview_panel)
        self.support_nav.setFixedWidth(186)
        self.support_nav.setWordWrap(True)
        self.support_nav.setTextElideMode(Qt.TextElideMode.ElideNone)
        save = self.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save)
        save.setProperty("primary", "true")
        save.style().unpolish(save)
        save.style().polish(save)
        self._refresh_results()

    def _queue_refresh(self, *_args) -> None:
        if not self._refresh_pending:
            self._refresh_pending = True
            QTimer.singleShot(0, self, self._refresh_results)

    def _select_result(self, item, _previous=None) -> None:
        value = item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        matches = self.result_list.findItems(value, Qt.MatchFlag.MatchExactly) if value else []
        self.result_list.setCurrentItem(matches[0] if matches else None)
        self._update_result_actions()

    def _add_library_result(self, value: str) -> None:
        for group_index in range(self.result_tree.topLevelItemCount()):
            group = self.result_tree.topLevelItem(group_index)
            for row in range(group.childCount()):
                item = group.child(row)
                if item.data(0, Qt.ItemDataRole.UserRole) == value and not item.isHidden():
                    self.result_tree.setCurrentItem(item)
                    self._add_selected_favorite()
                    return

    def _update_result_actions(self, *_args) -> None:
        selected = self.result_list.currentItem()
        value = selected.text().casefold() if selected is not None else ""
        favorites = {item.casefold() for item in self.favorites()}
        custom = bool(value) and value not in {item.casefold() for item in STANDARD_RESULT_CHOICES}
        self.result_action_buttons["Add to fast results"].setEnabled(
            bool(value) and value not in favorites and len(favorites) < 6)
        self.result_action_buttons["Remove from fast results"].setEnabled(self.favorite_list.currentRow() >= 0)
        self.result_action_buttons["Rename custom result"].setEnabled(custom)
        self.result_action_buttons["Remove custom result"].setEnabled(custom and value not in favorites)

    def _move_favorite(self, delta: int) -> None:
        row = self.favorite_list.currentRow()
        if row >= 0 and 0 <= row + delta < self.favorite_list.count():
            item = self.favorite_list.takeItem(row)
            self.favorite_list.insertItem(row + delta, item)
            self.favorite_list.setCurrentRow(row + delta)

    def _remove_preview_favorite(self, value: str) -> None:
        matches = self.favorite_list.findItems(value, Qt.MatchFlag.MatchExactly)
        if matches:
            self.favorite_list.setCurrentItem(matches[0])
            self._remove_favorite()

    def _refresh_results(self) -> None:
        from tapesift.services.football_vocab import lookup as vocab_lookup

        self._refresh_pending = False
        expanded = {self.result_tree.topLevelItem(i).text(0): self.result_tree.topLevelItem(i).isExpanded() for i in range(self.result_tree.topLevelItemCount())}
        selected = self.result_list.currentItem().text() if self.result_list.currentItem() else ""
        lookup = {value.casefold(): title for title, choices in self._GROUPS for value in choices}
        values = self.results()
        # The vocabulary grows independently of this dialog's old example list.
        # Known outcomes belong in their football group, never under Custom.
        category_groups = {"Penalty": "Penalties", "Score": "Scoring",
                           "Turnover": "Possession", "Negative": "Down and yardage",
                           "First Down": "Down and yardage", "Complete": "Passing",
                           "Stop": "Passing", "Special": "Special teams"}
        for value in values:
            item = vocab_lookup("result", value)
            if item is not None:
                lookup.setdefault(value.casefold(), category_groups.get(item.category, "Other"))
        groups = [title for title, _choices in self._GROUPS] + ["Custom"]
        favorites = {value.casefold() for value in self.favorites()}
        self.result_tree.clear()
        for title in groups:
            members = [value for value in values if lookup.get(value.casefold(), "Custom") == title]
            if not members:
                continue
            heading = QTreeWidgetItem(self.result_tree, [title.upper()])
            heading.setFlags(Qt.ItemFlag.ItemIsEnabled)
            heading.setForeground(0, QColor("#64d99b"))
            heading.setExpanded(expanded.get(title.upper(), True))
            for value in members:
                item = QTreeWidgetItem(heading, [value])
                item.setData(0, Qt.ItemDataRole.UserRole, value)
                item.setToolTip(0, value)
                item.setSizeHint(0, QSize(0, 32))
                add = QPushButton()
                add.setObjectName("V3ResultAdd")
                add.setStyleSheet(
                    "QPushButton#V3ResultAdd { min-width: 24px; max-width: 24px; "
                    "min-height: 24px; max-height: 24px; padding: 0; border: 1px solid transparent; "
                    "background: transparent; } "
                    "QPushButton#V3ResultAdd:hover { background: #1a2821; border-color: #617a6a; } "
                    "QPushButton#V3ResultAdd:focus { border-color: #67d096; }")
                add.setIcon(_support_nav_icon("add-24.svg"))
                add.setIconSize(QSize(18, 18))
                add.setFixedSize(28, 28)
                add.setAutoDefault(False)
                add.setAccessibleName(f"Add {value} to fast results")
                add.setToolTip("Already in fast results" if value.casefold() in favorites else
                               "Remove a fast result first; six maximum" if len(favorites) >= 6 else
                               f"Add {value} to fast results")
                add.setEnabled(value.casefold() not in favorites and len(favorites) < 6)
                add.clicked.connect(lambda _checked=False, current=value: self._add_library_result(current))
                self.result_tree.setItemWidget(item, 1, add)
                if value == selected:
                    self.result_tree.setCurrentItem(item)
        self._filter_results()
        values = self.favorites()
        self.favorite_count.setText(f"FAST RESULTS  {len(values)} / 6")
        for index in range(self.favorite_list.count()):
            item = self.favorite_list.item(index)
            row = self.favorite_list.itemWidget(item)
            if row is None:
                row = QWidget()
                layout = QHBoxLayout(row)
                layout.setContentsMargins(9, 4, 6, 4)
                grip = QLabel()
                grip.setPixmap(_support_action_icon("grip").pixmap(QSize(18, 18)))
                grip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                layout.addWidget(grip)
                label = QLabel()
                label.setObjectName("V3ResultRowLabel")
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                layout.addWidget(label, 1)
                remove = QPushButton()
                remove.setIcon(_support_action_icon("trash"))
                remove.setIconSize(QSize(18, 18))
                remove.setObjectName("V3ResultRemove")
                remove.setFixedSize(24, 24)
                remove.setAutoDefault(False)
                remove.clicked.connect(lambda _checked=False, current=item: self.favorite_list.takeItem(self.favorite_list.row(current)))
                layout.addWidget(remove)
                self.favorite_list.setItemWidget(item, row)
            row.findChild(QLabel, "V3ResultRowLabel").setText(item.text())
            remove = row.findChild(QPushButton, "V3ResultRemove")
            remove.setAccessibleName(f"Remove {item.text()} from fast results")
            remove.setToolTip(f"Remove {item.text()} from fast results; keep it in the library")
            row.setToolTip(item.text())
            if item.sizeHint() != QSize(0, 38):
                item.setSizeHint(QSize(0, 38))
        self.preview_flow.clear()
        self.preview_remove_buttons = {}
        configured = bool(values)
        if not values:
            from tapesift.ui_core.clip_editor import DEFAULT_RESULT_CHOICES
            values = list(DEFAULT_RESULT_CHOICES)
            self.preview_caption.setText("PREVIEW · Default chips when no favorites are configured")
        else:
            self.preview_caption.setText("PREVIEW · Fast result chips")
        for value in values:
            if not configured:
                label = _support_label(value, "chip")
                label.setMaximumWidth(190)
                self.preview_flow.addWidget(label)
                continue
            chip = QFrame()
            chip.setObjectName("V3ResultPreviewChip")
            chip.setStyleSheet("QFrame#V3ResultPreviewChip { background: #101714; border: 1px solid #2b3931; border-radius: 2px; }")
            row = QHBoxLayout(chip)
            row.setContentsMargins(10, 3, 4, 3)
            row.setSpacing(8)
            label = QLabel(value)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setMaximumWidth(190)
            label.setToolTip(value)
            row.addWidget(label)
            remove = QPushButton()
            remove.setObjectName("V3ResultRemove")
            remove.setIcon(_support_nav_icon("dismiss-16.svg"))
            remove.setIconSize(QSize(16, 16))
            remove.setFixedSize(24, 24)
            remove.setAutoDefault(False)
            remove.setAccessibleName(f"Remove {value} from fast results")
            remove.setToolTip(f"Remove {value} from fast results; keep it in the library")
            remove.clicked.connect(lambda _checked=False, current=value: self._remove_preview_favorite(current))
            row.addWidget(remove)
            self.preview_remove_buttons[value] = remove
            self.preview_flow.addWidget(chip)
        self._update_result_actions()

    def _filter_results(self, *_args) -> None:
        query = self.result_filter.text().strip().casefold()
        for index in range(self.result_tree.topLevelItemCount()):
            group = self.result_tree.topLevelItem(index)
            any_visible = False
            for row in range(group.childCount()):
                child = group.child(row)
                visible = not query or query in child.text(0).casefold() or query in group.text(0).casefold()
                child.setHidden(not visible)
                any_visible |= visible
            group.setHidden(not any_visible)
            if query and any_visible:
                group.setExpanded(True)

        current = self.result_tree.currentItem()
        if current is not None and (current.isHidden() or (current.parent() is not None and current.parent().isHidden())):
            self.result_tree.setCurrentItem(None)
            self.result_list.setCurrentItem(None)
        self._update_result_actions()
